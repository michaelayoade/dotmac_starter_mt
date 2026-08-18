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
# the kernel release tags when the checkout lacks them — CI checkouts are
# shallow and carry none — and pip resolves each module's runtime dependencies.
# It runs offline ONLY where both the tags and those wheels are already cached.
# An operator who read "air-gapped" would discover the difference at the least
# convenient moment.
#
# ## Why the pair is DERIVED, not written down
#
# Each module is proven against ITS OWN declared floor, and against the kernel
# release published immediately below that floor. Both come from the module and
# the tag set at run time.
#
# The pair used to be two constants shared by every listed module, which worked
# exactly as long as every listed module had the same floor. The moment
# `dotmac-entitlement-allocation` moved to a66 for `idempotency_ledger.v1` while
# `dotmac-release-catalog` stayed at a56, the constants described neither
# faithfully — and the failure was the good kind only by luck: the script
# compares the declaration to the constant and refused. Had it defaulted to the
# constant instead, it would have kept proving a real claim about the wrong
# version, silently, which is the failure this file exists to prevent.
#
# ## Why the negative control is the previous PUBLISHED release
#
# `ModuleManifest.platform_tables` was introduced in kernel SOURCE at a53. a53,
# a54 and a55 were never published — the tags jump a50 to a56 — so a53 is not a
# floor anyone can install, and a56 is the earliest installable kernel carrying
# the capability. The previous PUBLISHED release is therefore a50, and that is
# what a correct floor must exclude.
#
# Deriving from the tag set reproduces that answer rather than trusting it:
# unpublished numbers have no tag, so they cannot be selected. a56 still yields
# a50; a66 yields a65.
set -euo pipefail

FLOOR_PYTHON="${FLOOR_PYTHON:-python3}"

# The modules under proof, as "package_dir:import_name". Each is checked
# against ITS OWN declared floor — see "Why the pair is derived" above.
MODULES="${MODULES:-packages/dotmac-release-catalog:dotmac_release_catalog packages/dotmac-entitlement-allocation:dotmac_entitlement_allocation packages/dotmac-files:dotmac_files}"

# Overrides for a one-off investigation. Left unset in CI: a hand-set pair is
# a claim about a module that the module itself can contradict, which is the
# drift this script stopped hardcoding.
FLOOR_TAG="${FLOOR_TAG:-}"
BELOW_TAG="${BELOW_TAG:-}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir="$(mktemp -d)"
cleanup() {
  git -C "${repo_root}" worktree remove --force "${workdir}/floor" 2>/dev/null || true
  git -C "${repo_root}" worktree remove --force "${workdir}/below" 2>/dev/null || true
  rm -rf "${workdir}"
}
trap cleanup EXIT

# A shallow CI checkout has no tags at all, and the derivation below reads the
# published set — so the tag namespace IS the input here, unlike the two-tag
# fetch this replaced.
if [[ -z "$(git -C "${repo_root}" tag -l 'dotmac-kernel-v*')" ]]; then
  echo "==> Fetching dotmac-kernel release tags"
  git -C "${repo_root}" fetch --quiet --depth=1 origin \
    'refs/tags/dotmac-kernel-v*:refs/tags/dotmac-kernel-v*'
fi

# The previous PUBLISHED kernel release below a given version — the negative
# control. Derived from the tag set rather than written down, because "the one
# before" moves every time the kernel releases, and a written-down pair goes
# stale silently: it keeps proving a real claim about the wrong versions.
#
# Unpublished numbers are absent from the tag set by construction, so a53-a55
# (never released) cannot be selected here. That is the same reasoning the a56
# note above records, arrived at by measurement instead of by hand.
previous_published() {
  local floor_serial="${1#0.1.0a}"
  git -C "${repo_root}" tag -l 'dotmac-kernel-v0.1.0a*' \
    | sed 's/^dotmac-kernel-v0\.1\.0a//' \
    | grep -E '^[0-9]+$' \
    | sort -n \
    | awk -v f="${floor_serial}" '$1 < f' \
    | tail -1
}

declare -A WHEEL_CACHE=()

build_kernel_wheel() {
  local version="$1" tag="dotmac-kernel-v$1" name
  name="$(echo "${version}" | tr '.' '_')"
  if [[ -n "${WHEEL_CACHE[${version}]:-}" ]]; then
    echo "${WHEEL_CACHE[${version}]}"
    return
  fi
  if ! git -C "${repo_root}" rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
    echo "!! no ${tag} — a floor naming an unpublished version is unresolvable" >&2
    return 1
  fi
  git -C "${repo_root}" worktree add --quiet --detach "${workdir}/${name}" "${tag}"
  (cd "${workdir}/${name}/packages/dotmac-kernel" \
    && poetry build -f wheel -o "${workdir}/dist-${name}" >/dev/null)
  WHEEL_CACHE[${version}]="$(ls "${workdir}/dist-${name}"/*.whl)"
  echo "${WHEEL_CACHE[${version}]}"
}

# Import the manifest and report OK / how it refused. Narrow on purpose: only
# the failures that MEAN "this kernel cannot load this manifest" count, and
# everything else reads as a broken probe rather than a pass.
#
# Three refusals qualify, one per way a floor is actually set:
#
#   TypeError                — an unknown `ModuleManifest` field. The
#                              constructor-capability floors (`platform_tables`
#                              a53, `supported_plane_sets` a61).
#   ImportError/AttributeError — the manifest imports a kernel symbol that
#                              release does not have. This is what a
#                              PREREQUISITE floor looks like: a manifest naming
#                              `IDEMPOTENCY_LEDGER_V1` cannot even be imported
#                              by a kernel that never defined it.
#   PrerequisiteError        — the symbol exists but the name is not registered,
#                              so `validate_prerequisites` refuses in
#                              `__post_init__`.
#
# The third is why this list is not just "ImportError too": a kernel could
# publish the constant and not the registration, and that must still refuse.
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
refusals: tuple[type[BaseException], ...] = (TypeError, ImportError, AttributeError)
try:
    from dotmac_kernel.prerequisites import PrerequisiteError
except ImportError:
    # Kernels older than the prerequisite contract (pre-a56) have no such
    # class, and on those the manifest import fails as ImportError anyway.
    pass
else:
    refusals += (PrerequisiteError,)

try:
    module = importlib.import_module(f"{name}.manifest")
except refusals as exc:
    print(f"REFUSED {type(exc).__name__}: {exc}")
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
  echo
  echo "==> ${package_dir} declares dotmac-kernel ${declared}"

  # The pair comes FROM the module, so the two can no longer disagree. The
  # override exists for investigation and is still checked against the
  # declaration, because a hand-set pair that contradicts the module proves
  # something about a version the module does not claim.
  if [[ -n "${FLOOR_TAG}" ]]; then
    floor_version="${FLOOR_TAG#dotmac-kernel-v}"
    if [[ "${declared}" != ">=${floor_version}" ]]; then
      echo "!! FLOOR_TAG says ${floor_version}, the module says ${declared} —" >&2
      echo "   proving the override would say nothing about this module." >&2
      status=1
      continue
    fi
  else
    floor_version="${declared#>=}"
  fi
  if [[ "${declared}" != ">=${floor_version}" ]]; then
    echo "!! cannot read a floor from ${declared} — this script proves a" >&2
    echo "   '>=X' floor and nothing else." >&2
    status=1
    continue
  fi

  if [[ -n "${BELOW_TAG}" ]]; then
    below_version="${BELOW_TAG#dotmac-kernel-v}"
  else
    below_serial="$(previous_published "${floor_version}")"
    if [[ -z "${below_serial}" ]]; then
      echo "!! no published kernel below ${floor_version}, so 'necessary' is" >&2
      echo "   unprovable — the negative control would have nothing to run." >&2
      status=1
      continue
    fi
    below_version="0.1.0a${below_serial}"
  fi

  floor_wheel="$(build_kernel_wheel "${floor_version}")" || { status=1; continue; }
  below_wheel="$(build_kernel_wheel "${below_version}")" || { status=1; continue; }
  echo "    floor ${floor_version} (${floor_wheel##*/})"
  echo "    below ${below_version} (${below_wheel##*/}) — previous PUBLISHED release"

  echo "    at the floor (${floor_version}) — must IMPORT"
  result="$(probe "${floor_wheel}" "${package_dir}" "${import_name}" "${workdir}/venv-floor-${import_name}")"
  echo "    ${result}"
  if [[ "${result}" != OK* ]]; then
    echo "!! the declared floor cannot load the manifest — the floor is wrong" >&2
    status=1
  fi

  echo "    below the floor (${below_version}) — must FAIL"
  result="$(probe "${below_wheel}" "${package_dir}" "${import_name}" "${workdir}/venv-below-${import_name}")"
  echo "    ${result}"
  if [[ "${result}" != REFUSED* ]]; then
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
