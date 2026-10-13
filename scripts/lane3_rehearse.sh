#!/usr/bin/env bash
# Lane 3's rehearsal steps, owned by Starter (D-S2c C1).
#
# Every step `exposure-rehearsal.yml` used to carry inline lives here, so the
# Lane 3 properties are asserted on THIS file and survive the workflow's
# retirement (docs/LANE3_EXECUTION_TOPOLOGY.md § 13, C5). The org launcher calls
# this script at the dispatched Starter revision (C3). Neither caller adds a
# step of its own between these: a property a caller could reorder is a
# property of the caller, and the point of moving them is that it is not.
#
# Two phases, because they run on two different machines:
#
#   preflight  GitHub-HOSTED. Can this SOURCE produce a Lane 3 rehearsal
#              receipt at all? Stdlib only, before any runner is queried.
#   rehearse   the execution runner. Resolve, fetch and verify the recorded
#              candidate, install it isolated, ask the authorization gate,
#              qualify the external vantage, then drive the runner.
#
# Inputs arrive as environment variables, never as text interpolated into a
# command. A dispatch value pasted into a `run:` body is shell, and the shell
# runs it; read from the environment it is only ever a value.
#
# Exit codes are the repository's three: 0 done, 1 refused, 2 the question
# cannot be answered here. A step that fails passes its own status through.
set -euo pipefail

refuse() {
  echo "REFUSED: $*" >&2
  exit 1
}

# Each `step` line opens one named unit. The architecture guards split this
# file on these lines and assert order and content unit by unit, the way they
# used to assert workflow steps, so a unit must not be split or merged quietly.
step() {
  echo "== ${1}" >&2
}

require_env() {
  local name
  for name in "$@"; do
    [ -n "${!name:-}" ] || refuse "${name} is not set. Every Lane 3 input is" \
      "required and none has a default: a rehearsal missing one is not a" \
      "partial rehearsal, it is a different activity"
  done
}

preflight() {
  step "Refuse a source whose runner cannot produce a rehearsal receipt"
  python scripts/lane3_runner_capability.py \
    --root . \
    --out lane3-runner-capability.json \
    --summary "${GITHUB_STEP_SUMMARY}"
}

# The recorded candidate's coordinates, read from `resolve-candidate`'s output
# into named variables. A closed key set: an unknown or repeated key refuses,
# so a changed emitter cannot slip a value past this reader under a new name.
read_candidate() {
  local line key value seen=" "
  while IFS= read -r line; do
    key="${line%%=*}"
    value="${line#*=}"
    case "${key}" in
      candidate_repository | candidate_run_id | candidate_artifact_id | \
        candidate_filename | candidate_sha256 | candidate_source_sha | \
        candidate_receipt) ;;
      *) refuse "resolve-candidate emitted an unknown key '${key}'" ;;
    esac
    case "${seen}" in *" ${key} "*) refuse "resolve-candidate repeated ${key}" ;; esac
    seen="${seen}${key} "
    printf -v "${key}" '%s' "${value}"
  done < "$1"
  for key in candidate_repository candidate_artifact_id candidate_sha256 \
    candidate_source_sha; do
    case "${seen}" in *" ${key} "*) ;; *) refuse "resolve-candidate omitted ${key}" ;; esac
  done
}

# The private vantage topology, from the ONE seam (`lane3_topology_source.py`),
# bound by the dispatched Fleet host_id. Values land in shell variables only:
# never echoed, never written to a file, never exported. An unknown, repeated
# or missing key refuses, as `read_candidate` does for the candidate.
read_topology() {
  local line key value seen=" "
  while IFS= read -r line; do
    key="${line%%=*}"
    value="${line#*=}"
    case "${key}" in
      TOPOLOGY_VERSION) topology_version="${value}" ;;
      TOPOLOGY_TARGET) topology_target="${value}" ;;
      TOPOLOGY_PROBE_HOST) topology_probe_host="${value}" ;;
      TOPOLOGY_OBSERVER_USER) topology_observer_user="${value}" ;;
      *) refuse "the topology seam emitted an unknown key '${key}'" ;;
    esac
    case "${seen}" in *" ${key} "*) refuse "the topology seam repeated ${key}" ;; esac
    seen="${seen}${key} "
  done <<< "$1"
  for key in TOPOLOGY_VERSION TOPOLOGY_TARGET TOPOLOGY_PROBE_HOST TOPOLOGY_OBSERVER_USER; do
    case "${seen}" in *" ${key} "*) ;; *) refuse "the topology seam omitted ${key}" ;; esac
  done
}

rehearse() {
  require_env LANE3_FACILITY LANE3_CANDIDATE_VERSION LANE3_AUTHORIZATION_RUN \
    LANE3_CONTROLLER_IDENTITY LANE3_HOST_ID GITHUB_SHA GH_TOKEN

  # The rehearsal executes the exact candidate that may later be published.
  # Coordinates and digest come only from the committed CandidateArtifact.v1
  # receipt; no dispatch field may substitute one.
  step "Resolve the allowlisted facility"
  python scripts/release_facility.py resolve \
    "${LANE3_FACILITY}" --version "${LANE3_CANDIDATE_VERSION}"

  step "Resolve the recorded candidate"
  python scripts/release_facility.py resolve-candidate \
    "${LANE3_FACILITY}" --version "${LANE3_CANDIDATE_VERSION}" > candidate.env
  read_candidate candidate.env

  step "Fetch the recorded candidate artifact"
  mkdir -p candidate-dist
  gh api "/repos/${candidate_repository}/actions/artifacts/${candidate_artifact_id}/zip" \
    > candidate.zip
  python -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
    candidate.zip candidate-dist

  step "Refuse anything that is not the recorded candidate"
  python scripts/release_facility.py verify-candidate \
    "${LANE3_FACILITY}" --version "${LANE3_CANDIDATE_VERSION}" \
    --dist candidate-dist

  # ISOLATED means the installed wheel is the ONLY importable copy, and the
  # venv alone does not achieve that: `PYTHONPATH` is honoured by every
  # interpreter, so a caller that exported it would put the checkout's
  # Foundation `src` ahead of site-packages and the rehearsal would exercise
  # the SOURCE. Every candidate interpreter below is therefore launched `-E -P`
  # (ignore PYTHON* variables; do not prepend the script's own directory).
  step "Install the verified candidate in an isolated environment"
  python -m venv .lane3-foundation
  .lane3-foundation/bin/python -m pip install --no-deps candidate-dist/*.whl

  # BEFORE the probe host and the target are touched, and in the SAME
  # interpreter the runner uses: whether a verifier is installed is a property
  # of the candidate venv. 0 attested / 1 refused / 2 unanswerable; today 2.
  step "Refuse unless Lane 3's authorization can be verified here"
  .lane3-foundation/bin/python -E -P scripts/lane3_authorization.py \
    --descriptor scripts/exposure-rehearsal/product.toml \
    --target "${LANE3_HOST_ID}"

  # The target address, the probe vantage and the observer principal exist
  # ONLY in the OpenBao vantage-topology record (docs/LANE3_EXECUTION_TOPOLOGY.md
  # section 4). Exit 2 means no reader is provisioned (B7), exit 1 a record or
  # binding refusal; either stops here, before the probe host or target is
  # touched. The runner reads the record itself and refuses a different version.
  step "Resolve the private vantage topology for this host"
  local topology_lines topology_version topology_target topology_probe_host \
    topology_observer_user
  topology_lines="$(python -I scripts/lane3_topology_source.py resolve \
    --host-id "${LANE3_HOST_ID}")" ||
    refuse "the vantage topology could not be resolved for this host"
  read_topology "${topology_lines}"
  topology_lines=""

  # Collected BEFORE the controller runs, so the vantage is qualified against
  # its own interfaces and routes rather than trusted, and a probe measured
  # after teardown can never be reused as a negative. The observation identity
  # is SEPARATE from the controller identity; an unset one refuses.
  step "Collect and qualify the external probe evidence"
  [ -n "${topology_probe_host}" ] || refuse "no external probe vantage configured"
  [ -n "${topology_observer_user}" ] && [ -n "${LANE3_OBSERVER_KEY:-}" ] ||
    refuse "no target-side observation identity configured. The far-end" \
      "source address is the one check measured from the far end, and a" \
      "vantage cannot certify where it egresses from"
  ./scripts/exposure-rehearsal/collect_probe_evidence.sh \
    qualify "${topology_probe_host}" "${topology_target}" > probe-evidence.json

  # Three revisions, three sources: the runner's own commit is `GITHUB_SHA`;
  # the candidate's source commit and digest come ONLY from the resolved
  # receipt. The v2 inputs (API run, encrypted evidence bundle, vantage record
  # reference) are passed through as given; the runner refuses when one is
  # absent rather than inventing it.
  step "Execute Lane 3 through the controller"
  [ -n "${LANE3_JUMP_KEY:-}" ] || refuse "no inside-vantage jump key configured." \
    "Items 12 and 16 need a probe that genuinely originates inside the" \
    "accepted source set; this host is outside it by construction"
  .lane3-foundation/bin/python -E -P scripts/exposure_rehearsal_runner.py \
    --foundation-revision "${GITHUB_SHA}" \
    --candidate-source-revision "${candidate_source_sha}" \
    --foundation-artifact "${candidate_sha256}" \
    --authorization-run "${LANE3_AUTHORIZATION_RUN}" \
    --controller-identity "${LANE3_CONTROLLER_IDENTITY}" \
    --controller-key "${HOME}/.dotmac/controller-${LANE3_AUTHORIZATION_RUN}" \
    --host-id "${LANE3_HOST_ID}" \
    --topology-version "${topology_version}" \
    --candidate-version "${LANE3_CANDIDATE_VERSION}" \
    --inside-jump-key "${LANE3_JUMP_KEY}" \
    --observer-key "${LANE3_OBSERVER_KEY}" \
    --probe-evidence probe-evidence.json \
    --api-run "${LANE3_API_RUN:-}" \
    --evidence-bundle "${LANE3_EVIDENCE_BUNDLE:-}" \
    --descriptor scripts/exposure-rehearsal/product.toml \
    --receipt-out receipt.json \
    --status-out docs/inventories/deployment-exposure-rehearsal-status.md \
    --release-out lane3-lease-release.json \
    --terminal-evidence-out lane3-terminal-evidence.json
}

case "${1:-}" in
  preflight) preflight ;;
  rehearse) rehearse ;;
  *)
    echo "usage: lane3_rehearse.sh preflight|rehearse" >&2
    exit 2
    ;;
esac
