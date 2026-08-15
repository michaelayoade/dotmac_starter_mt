#!/usr/bin/env bash
# Prove a MODULE's declared kernel floor is the earliest kernel that can load it.
#
# `scripts/kernel_floor_check.sh` proves the kernel's own third-party floors are
# real. This is the other half: a module declares `dotmac-kernel = ">=X"`, and
# nothing until now proved that X is (a) sufficient and (b) necessary.
#
# Both halves matter, and they fail differently:
#
#   sufficient — the module must IMPORT against a kernel at exactly X. A floor
#     set too high costs adopters nothing but compatibility they could have had;
#     a floor that does not actually work is a broken release.
#   necessary  — the module must FAIL against the previous PUBLISHED kernel. A
#     floor set too low is the dangerous direction: it resolves cleanly and then
#     raises `TypeError` at manifest import, in the adopter's deployment.
#
# The second is why this script exists. `dotmac-release-catalog` and
# `dotmac-entitlement-allocation` each shipped three releases declaring
# `>=0.1.0a44` / `>=0.1.0a45` while their manifests consumed a kernel field that
# did not exist until a53 — the floors were seventeen releases too low and every
# gate passed, because the floor was only ever compared against itself.
#
# ## Why the kernel is BUILT FROM A TAG rather than downloaded
#
# The published wheels live on a private index this job has no credentials for.
# Building from the release tag needs none, and is stronger anyway: it proves
# the claim against the exact source that was tagged.
#
# This is NOT air-gapped and must not be described as such. The script fetches
# the two release tags when the checkout lacks them — CI checkouts are shallow
# and carry none — and pip resolves each module's runtime dependencies. It runs
# offline ONLY where both the tags and those wheels are already cached. An
# operator who read "air-gapped" would discover the difference at the least
# convenient moment.
#
# ## Why a50 is the negative control, not a53
#
# `ModuleManifest.platform_tables` was introduced in kernel SOURCE at a53. a53,
# a54 and a55 were never published — the tags jump a50 to a56 — so a53 is not a
# floor anyone can install, and a56 is the earliest installable kernel carrying
# the capability. The previous PUBLISHED release is therefore a50, and that is
# what a correct floor must exclude.
set -euo pipefail

FLOOR_TAG="${FLOOR_TAG:-dotmac-kernel-v0.1.0a56}"
BELOW_TAG="${BELOW_TAG:-dotmac-kernel-v0.1.0a50}"
FLOOR_PYTHON="${FLOOR_PYTHON:-python3}"

# The modules whose floor this capability sets, as "package_dir:import_name".
MODULES="${MODULES:-packages/dotmac-release-catalog:dotmac_release_catalog packages/dotmac-entitlement-allocation:dotmac_entitlement_allocation}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir="$(mktemp -d)"
cleanup() {
  git -C "${repo_root}" worktree remove --force "${workdir}/floor" 2>/dev/null || true
  git -C "${repo_root}" worktree remove --force "${workdir}/below" 2>/dev/null || true
  rm -rf "${workdir}"
}
trap cleanup EXIT

# A shallow CI checkout has no tags. Fetch exactly the two needed rather than
# `--tags`, which drags the whole tag namespace for no benefit.
for tag in "${FLOOR_TAG}" "${BELOW_TAG}"; do
  if ! git -C "${repo_root}" rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
    echo "==> Fetching ${tag}"
    git -C "${repo_root}" fetch --quiet --depth=1 origin "refs/tags/${tag}:refs/tags/${tag}"
  fi
done

build_kernel_wheel() {
  local tag="$1" name="$2"
  git -C "${repo_root}" worktree add --quiet --detach "${workdir}/${name}" "${tag}"
  (cd "${workdir}/${name}/packages/dotmac-kernel" \
    && poetry build -f wheel -o "${workdir}/dist-${name}" >/dev/null)
  ls "${workdir}/dist-${name}"/*.whl
}

echo "==> Building the kernel wheel at ${FLOOR_TAG} and ${BELOW_TAG}"
floor_wheel="$(build_kernel_wheel "${FLOOR_TAG}" floor)"
below_wheel="$(build_kernel_wheel "${BELOW_TAG}" below)"
echo "    floor: ${floor_wheel##*/}"
echo "    below: ${below_wheel##*/}"

# Import the manifest and report OK / the exception type. Deliberately narrow:
# only `TypeError` counts as "this kernel cannot load the manifest", because any
# other failure means the probe itself is broken and must not read as a pass.
probe() {
  local wheel="$1" package_dir="$2" import_name="$3" venv="$4"
  "${FLOOR_PYTHON}" -m venv "${venv}"
  "${venv}/bin/pip" install --quiet --upgrade pip
  "${venv}/bin/pip" install --quiet "${wheel}"
  "${venv}/bin/pip" install --quiet --no-deps "${repo_root}/${package_dir}"
  "${venv}/bin/pip" install --quiet "sqlalchemy>=2.0,<3"
  "${venv}/bin/python" - "$import_name" <<'PY'
import importlib
import sys

name = sys.argv[1]
try:
    module = importlib.import_module(f"{name}.manifest")
except TypeError as exc:
    print(f"TYPEERROR {exc}")
except Exception as exc:  # noqa: BLE001 - reported, never swallowed
    print(f"UNEXPECTED {type(exc).__name__}: {exc}")
else:
    import dotmac_kernel

    print(f"OK kernel {dotmac_kernel.__version__} manifest {module.module.version}")
PY
}

status=0
for entry in ${MODULES}; do
  package_dir="${entry%%:*}"
  import_name="${entry##*:}"
  declared="$(
    "${FLOOR_PYTHON}" - "${repo_root}/${package_dir}/pyproject.toml" <<'PY'
import sys, tomllib
data = tomllib.loads(open(sys.argv[1], "rb").read().decode())
print(data["tool"]["poetry"]["dependencies"]["dotmac-kernel"])
PY
  )"
  expected=">=${FLOOR_TAG#dotmac-kernel-v}"
  echo
  echo "==> ${package_dir} declares dotmac-kernel ${declared}"
  if [[ "${declared}" != "${expected}" ]]; then
    echo "!! expected ${expected} — this script proves THAT floor, so a" >&2
    echo "   different one is unproven rather than merely different." >&2
    status=1
    continue
  fi

  echo "    at the floor (${FLOOR_TAG}) — must IMPORT"
  result="$(probe "${floor_wheel}" "${package_dir}" "${import_name}" "${workdir}/venv-floor-${import_name}")"
  echo "    ${result}"
  if [[ "${result}" != OK* ]]; then
    echo "!! the declared floor cannot load the manifest — the floor is wrong" >&2
    status=1
  fi

  echo "    below the floor (${BELOW_TAG}) — must FAIL"
  result="$(probe "${below_wheel}" "${package_dir}" "${import_name}" "${workdir}/venv-below-${import_name}")"
  echo "    ${result}"
  if [[ "${result}" != TYPEERROR* ]]; then
    echo "!! the previous published kernel loaded this manifest, so the floor" >&2
    echo "   is higher than it needs to be — or this probe proves nothing." >&2
    status=1
  fi
done

echo
if [[ "${status}" -eq 0 ]]; then
  echo "==> Module floor check passed (sufficient AND necessary)"
else
  echo "==> Module floor check FAILED" >&2
fi
exit "${status}"
