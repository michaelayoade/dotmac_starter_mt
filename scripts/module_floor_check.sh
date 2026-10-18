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
# Building from the release tag needs none. It proves installed tagged-source
# compatibility, NOT byte identity with a registry wheel. Actual registry wheel
# verification and retained artifact comparison remain separate release evidence.
#
# This is NOT air-gapped and must not be described as such. The script fetches
# the complete kernel release tag namespace on every invocation — CI checkouts are
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
# `dotmac-release-catalog` then stayed at a56, the constants described neither
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
# Entries are "package_dir:import_name" or
# "package_dir:import_name:extra.module[,extra.module...]". The optional third
# field names modules BEYOND the manifest that consume the declared floor --
# see the fourth refusal family documented below. Omit it and the probe is
# manifest-only, exactly as before. A fourth field selects the closed mode
# manifest|registry|tenant-audit. Learning requires actual default registration;
# Fulfillment and Template Studio require tenant-audit API evidence only.
MODULES="${MODULES:-packages/dotmac-release-catalog:dotmac_release_catalog packages/dotmac-entitlement-allocation:dotmac_entitlement_allocation packages/dotmac-files:dotmac_files:dotmac_files.service packages/dotmac-billing:dotmac_billing:dotmac_billing.service packages/dotmac-learning:dotmac_learning::registry packages/dotmac-fulfillment:dotmac_fulfillment::tenant-audit packages/dotmac-template-studio:dotmac_template_studio::tenant-audit}"

# Overrides for a one-off investigation. Left unset in CI: a hand-set pair is
# a claim about a module that the module itself can contradict, which is the
# drift this script stopped hardcoding.
FLOOR_TAG="${FLOOR_TAG:-}"
BELOW_TAG="${BELOW_TAG:-}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir="$(mktemp -d)"
# Retained artifact evidence survives cleanup; callers may choose a CI artifact path.
evidence_dir="${MODULE_FLOOR_EVIDENCE_DIR:-$(mktemp -d)}"
mkdir -p "${evidence_dir}"
evidence_dir="$(cd "${evidence_dir}" && pwd)"
probe_source="${repo_root}/scripts/module_floor_probe.py"
cleanup() {
  for tree in "${workdir}"/kernel-*; do
    git -C "${repo_root}" worktree remove --force "${tree}" 2>/dev/null || true
  done
  rm -rf "${workdir}"
}
trap cleanup EXIT

# Always refresh the authoritative namespace. A nonempty partial local tag set
# must never manufacture the wrong immediately previous published release.
echo "==> Fetching complete dotmac-kernel release tag namespace"
git -C "${repo_root}" fetch --quiet --force --prune --prune-tags origin \
  'refs/tags/dotmac-kernel-v*:refs/tags/dotmac-kernel-v*'
git -C "${repo_root}" for-each-ref \
  --format='%(refname:short) %(objectname) %(*objectname)' \
  'refs/tags/dotmac-kernel-v*' > "${evidence_dir}/kernel-tags.txt"

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

# File existence is authoritative across command-substitution subshells.
build_kernel_wheel() {
  local version="$1" tag="dotmac-kernel-v$1" cached tree wheel commit
  cached="${evidence_dir}/kernel-${version}.wheel-path"
  if ! commit="$(git -C "${repo_root}" rev-parse --verify "refs/tags/${tag}^{commit}")"; then
    echo "!! no ${tag} — a floor naming an unpublished version is unresolvable" >&2
    return 1
  fi
  if [[ -f "${cached}" ]]; then
    [[ "$(cat "${evidence_dir}/kernel-${version}.commit")" == "${commit}" ]] || return 1
    wheel="$(cat "${cached}")"
    [[ -f "${wheel}" ]] || return 1
    echo "${wheel}"
    return
  fi
  tree="${workdir}/kernel-${version}"
  git -C "${repo_root}" worktree add --quiet --detach "${tree}" "${commit}" || return
  (cd "${tree}/packages/dotmac-kernel" && poetry build -f wheel \
    -o "${evidence_dir}/kernel-${version}" >/dev/null) || return
  local wheels=("${evidence_dir}/kernel-${version}"/*.whl)
  [[ "${#wheels[@]}" -eq 1 && -f "${wheels[0]}" ]] || return 1
  wheel="${wheels[0]}"
  printf '%s\n' "${wheel}" > "${cached}"
  printf '%s\n' "${commit}" > "${evidence_dir}/kernel-${version}.commit"
  git -C "${repo_root}" worktree remove --force "${tree}" || return
  echo "${wheel}"
}

# The installed helper imports the manifest and records OK / how it refused.
# Narrow on purpose: only
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
#
# A FOURTH family sets a floor without touching the manifest at all: a module
# whose own source imports a kernel MODULE that the release does not ship. The
# three above are all manifest-level, so a manifest-only probe cannot see this
# one -- it reports OK below the floor and then cannot tell "the floor is too
# high" from "this probe proves nothing". `dotmac-files` is exactly this shape:
# its service imports `dotmac_kernel.transactions.conflict_savepoint`, first
# published in a98, while its manifest loads happily under a97. Billing instead
# imports conflict_savepoint_requires_rollback, added to the existing public
# module in a105: a104 ships the module but lacks the required symbol.
#
# So an entry may DECLARE the modules that consume its floor, and the probe
# imports them after the manifest. Declared rather than derived from the wheel
# contents, because importing every shipped module would change what the other
# entries prove as a side effect of fixing this one.
#
# A declared module that does not EXIST in the package is a broken probe, not a
# refusal. `ModuleNotFoundError` is an `ImportError`, so a typo would otherwise
# read as a refusal under BOTH kernels and manufacture a floor nothing proved.
# Presence is therefore checked with `find_spec` before the import is attempted,
# and absence is reported as BROKEN.
# These existing manifest-mode controls live in module_floor_probe.py.
# Unexpected exceptions and missing declared consumers fail the probe; they
# cannot qualify as a refusal. Billing additionally requires the exact missing
# conflict_savepoint_requires_rollback export in validate_evidence.
# Registry and tenant-audit modes accept only the exact missing tenant-audit API.
probe() {
  local wheel="$1" module_wheel="$2" import_name="$3" venv="$4" extra="$5"
  local mode="$6" kernel_version="$7" module_version="$8" distribution="$9"
  local commit="${10}" output="${11}" expected="${12}"
  "${FLOOR_PYTHON}" -m venv "${venv}" || return
  "${venv}/bin/pip" install --quiet --upgrade pip || return
  "${venv}/bin/pip" install --quiet "${wheel}" || return
  # The SAME module wheel is installed twice. Only its kernel requirement is
  # bypassed for the negative control; non-kernel metadata is unchanged.
  # Preserve built-metadata extras and markers (including Billing Alembic).
  # A transitive dependency must not replace the exact tagged Kernel under test.
  "${venv}/bin/python" -I "${probe_source}" dependencies \
    "${module_wheel}" "${kernel_version}" "${venv}" || return
  "${venv}/bin/pip" install --quiet \
    --constraint "${venv}/kernel-constraint.txt" \
    --requirement "${venv}/module-runtime.txt" || return
  if [[ "${expected}" == OK ]]; then
    "${venv}/bin/pip" install --quiet --constraint "${venv}/kernel-constraint.txt" "${module_wheel}" || return
  else
    "${venv}/bin/pip" install --quiet --no-deps "${module_wheel}" || return
  fi
  "${venv}/bin/python" -I "${probe_source}" run \
    --name "${import_name}" --extra "${extra}" --mode "${mode}" \
    --kernel-version "${kernel_version}" --module-version "${module_version}" \
    --distribution "${distribution}" --module-wheel "${module_wheel}" \
    --kernel-wheel "${wheel}" --commit "${commit}" --checkout "${repo_root}" \
    > "${output}" || return
  cat "${output}"
}

status=0
entry_count=0
for entry in ${MODULES}; do
  entry_count=$((entry_count + 1))
  parsed="$("${FLOOR_PYTHON}" -I "${probe_source}" entry "${entry}")" || {
    status=1; continue;
  }
  IFS=$'\t' read -r package_dir import_name extra_consumers mode <<<"${parsed}"
  [[ "${extra_consumers}" != - ]] || extra_consumers=""
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

  actual_previous="$(previous_published "${floor_version}")"
  if [[ -z "${actual_previous}" || "${below_version}" != "0.1.0a${actual_previous}" ]]; then
    echo "!! below tag must be the immediately previous published Kernel" >&2
    status=1; continue
  fi
  # Exactly one module build per entry, retained for both isolated environments.
  module_dist="${evidence_dir}/module-${import_name}"
  mkdir -p "${module_dist}"
  (cd "${repo_root}/${package_dir}" && poetry build -f wheel -o "${module_dist}" >/dev/null) || {
    status=1; continue;
  }
  module_wheels=("${module_dist}"/*.whl)
  if [[ "${#module_wheels[@]}" -ne 1 || ! -f "${module_wheels[0]}" ]]; then
    status=1; continue
  fi
  module_wheel="${module_wheels[0]}"
  wheel_info="$("${FLOOR_PYTHON}" -I "${probe_source}" wheel-info "${module_wheel}")" || {
    status=1; continue;
  }
  read -r distribution module_version <<<"${wheel_info}"

  floor_wheel="$(build_kernel_wheel "${floor_version}")" || { status=1; continue; }
  below_wheel="$(build_kernel_wheel "${below_version}")" || { status=1; continue; }
  echo "    floor ${floor_version} (${floor_wheel##*/})"
  echo "    below ${below_version} (${below_wheel##*/}) — previous PUBLISHED release"

  floor_commit="$(cat "${evidence_dir}/kernel-${floor_version}.commit")"
  below_commit="$(cat "${evidence_dir}/kernel-${below_version}.commit")"
  for side in floor below; do
    if [[ "${side}" == floor ]]; then
      selected_wheel="${floor_wheel}"; selected_version="${floor_version}"
      selected_commit="${floor_commit}"; expected=OK
    else
      selected_wheel="${below_wheel}"; selected_version="${below_version}"
      selected_commit="${below_commit}"; expected=REFUSED
    fi
    output="${evidence_dir}/${import_name}-${side}.json"
    if ! probe "${selected_wheel}" "${module_wheel}" "${import_name}" \
      "${workdir}/venv-${side}-${import_name}" "${extra_consumers}" "${mode}" \
      "${selected_version}" "${module_version}" "${distribution}" "${selected_commit}" "${output}" "${expected}"; then
      echo "!! ${side} installed probe failed" >&2
      status=1; continue
    fi
    if ! "${FLOOR_PYTHON}" -I "${probe_source}" validate --file "${output}" \
      --expected "${expected}" --mode "${mode}" --name "${import_name}" \
      --kernel-version "${selected_version}" --module-version "${module_version}" \
      --module-wheel "${module_wheel}" --kernel-wheel "${selected_wheel}" \
      --commit "${selected_commit}"; then
      echo "!! ${side} evidence does not establish the declared floor" >&2
      status=1
    fi
  done

done

if [[ "${entry_count}" -eq 0 ]]; then
  echo "!! no module entries under proof" >&2
  status=1
fi
echo "==> Retained tagged-source artifacts/evidence: ${evidence_dir}"
echo
if [[ "${status}" -eq 0 ]]; then
  echo "==> Module floor check passed (sufficient AND necessary)"
else
  echo "==> Module floor check FAILED" >&2
fi
exit "${status}"
