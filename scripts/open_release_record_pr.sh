#!/usr/bin/env bash
# Open the post-release record as a pull request, straight after tagging.
#
# A release workflow writes the TAG. It does not write the RECORD, and the tag
# invalidates the publication ledger the instant it lands — five gates fail
# from that moment until a human remembers. Between 2026-08-21 and 2026-08-22
# that happened FOUR times, twice for the same distribution one version apart,
# and the fourth left four open pull requests red at once. Each presented as
# *that branch* being broken rather than as `main` being broken, which is what
# made it expensive to diagnose rather than expensive to fix.
#
# So the record stops being remembered. `scripts/write_release_record.py` makes
# the edits; this opens them and enables squash auto-merge. `main` is protected,
# so the recorder cannot push to it directly: required CI remains the merge
# authority, while the redundant human bookkeeping gesture is removed.
#
# Idempotent by construction: the writer reports "nothing to do" when the
# record is already complete, and this exits 0 without opening an empty PR.
# Re-running a release, or racing a hand repair, converges.
#
# FAILS LOUDLY when it cannot open the record. An earlier version of this
# script exited 0 and printed a `::warning::`, on the reasoning that the
# artifact is already published so the run should not report a successful
# publication as failed. That reasoning was wrong, and it recreated the exact
# failure class the script exists to close: correctness went back to depending
# on somebody READING a warning in a green run. A green run with no record is
# indistinguishable, at a glance, from a green run with one.
#
# So the run goes RED. The failure message states plainly that the artifact IS
# published and tagged — nobody should re-run the publish — names the command
# that closes the gap, and links the ready-made pull-request page for the branch
# it has already pushed. "Tag exists, record missing" is now visible in the
# place people already look.
#
# PROVEN, not hypothetical. The first real end-to-end run was the kernel 0.1.0a92
# release (run 32617583628, 2026-08-23). This script ran, removed the ledger row
# correctly, pushed `chore/record-dotmac-kernel-0.1.0a92` — and could not open the
# pull request:
#
#   pull request create failed: GraphQL: GitHub Actions is not permitted to
#   create or approve pull requests (createPullRequest)
#
# It then exited 0. The step reported success, the job was green, the run was
# green, and the record reached `main` only because a human noticed and wrote it
# by hand. That is the whole failure class, reproduced by the automation built to
# end it.
#
# The repaired failure path is proven by kernel 0.1.0a93 (run 32622991682): the
# script pushed the correct record branch, failed RED when the workflow token
# could not open the pull request, and Michael opened #372 from that branch.
# The broad repository switch that lets an appropriately scoped GitHub Actions
# token create or approve pull requests is enabled for recorder automation.
# More importantly, the tagging job's ordinary workflow token has
# contents:write ONLY, so that repository setting cannot make the publisher the
# PR writer. The target automatic
# path is a dedicated recorder GitHub App with contents:write and
# pull_requests:write, and no Actions, deployment, environment or administration
# authority. GitHub does not split PR creation from its review API, so the hard
# separation is that the App authors and last-pushes the PR while protected main
# requires the complete strict CI set with no bypass. The App enables auto-merge
# but cannot waive a check. The release workflows prefer that App's short-lived
# token for BOTH the branch push and PR, and fall back to `GITHUB_TOKEN` only to
# push the correct branch before PR creation fails loudly. Until the App identity
# is installed, that red run plus the one-click URL is the fail-closed bridge.
#
# A CONNECTOR release has a THIRD ledger: a published connector's manifest
# digest is what an installation adopts by, and
# `docs/inventories/released-manifest-digests.json` holds it immutable. That row
# is written here too, for the same reason as the other two: a tag with no row
# fails `make manifest-digest-check` from the instant it lands, and "somebody
# will remember" has already failed four times.
#
# `--manifest-python` names the interpreter that can import the connector's
# dependencies — the connector lane passes the clean venv it already built to
# install and conform the PUBLISHED wheel, so the digest is derived by the same
# interpreter that proved the artifact. Its absence means "not the connector
# lane", which is why it is a path rather than a boolean: the kernel, module and
# adapter lanes publish nothing that carries an adopted manifest digest.
set -uo pipefail

DISTRIBUTION=""
VERSION=""
TAG=""
PACKAGE_DIR=""
IMPORT_NAME=""
MANIFEST_PYTHON=""
RELEASE_RUN=""

while [ $# -gt 0 ]; do
  case "$1" in
    --distribution) DISTRIBUTION="$2"; shift 2 ;;
    --version)      VERSION="$2";      shift 2 ;;
    --tag)          TAG="$2";          shift 2 ;;
    --package-dir)  PACKAGE_DIR="$2";  shift 2 ;;
    --import-name)  IMPORT_NAME="$2";  shift 2 ;;
    --manifest-python) MANIFEST_PYTHON="$2"; shift 2 ;;
    --release-run)  RELEASE_RUN="$2";  shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

for required in DISTRIBUTION VERSION TAG; do
  if [ -z "${!required}" ]; then
    echo "--${required,,} is required" >&2
    exit 2
  fi
done

MANUAL="python scripts/write_release_record.py --distribution ${DISTRIBUTION} --version ${VERSION} --tag ${TAG}"
if [ -n "${PACKAGE_DIR}" ]; then
  MANUAL="${MANUAL} --package-dir ${PACKAGE_DIR}"
  if [ -n "${IMPORT_NAME}" ]; then
    MANUAL="${MANUAL} --import-name ${IMPORT_NAME}"
  fi
fi
if [ -n "${MANIFEST_PYTHON}" ]; then
  MANUAL="${MANUAL}
  then: make manifest-digest-record TAG=${TAG} RELEASE_RUN=${RELEASE_RUN}"
fi

give_up() {
  echo "::error::the ${TAG} release record was NOT opened: $1"
  echo "::error::"
  echo "::error::The branch may already carry the correct edits. Check, and if"
  echo "::error::so open it directly:"
  echo "::error::  ${COMPARE_URL}"
  echo "::error::"
  echo "::error::DO NOT RE-RUN THE PUBLISH. ${DISTRIBUTION} ${VERSION} is already"
  echo "::error::published and tagged; the artifact is fine and this failure is"
  echo "::error::bookkeeping only. main is RED until the record lands."
  echo "::error::"
  echo "::error::Close it by hand, on a branch off main:"
  echo "::error::  ${MANUAL}"
  echo "::error::then open a pull request titled:"
  echo "::error::  chore(release): record the ${DISTRIBUTION} ${VERSION} publication"
  exit 1
}

BRANCH="chore/record-${DISTRIBUTION}-${VERSION}"
COMPARE_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-michaelayoade/dotmac_starter_mt}/pull/new/${BRANCH}"

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git fetch origin main --quiet || give_up "could not fetch main"
git checkout -B "${BRANCH}" origin/main --quiet || give_up "could not branch from main"

ARGS=(--distribution "${DISTRIBUTION}" --version "${VERSION}" --tag "${TAG}")
if [ -n "${PACKAGE_DIR}" ]; then
  ARGS+=(--package-dir "${PACKAGE_DIR}")
  if [ -n "${IMPORT_NAME}" ]; then
    ARGS+=(--import-name "${IMPORT_NAME}")
  fi
fi

if ! OUTPUT="$(python scripts/write_release_record.py "${ARGS[@]}" 2>&1)"; then
  echo "${OUTPUT}"
  give_up "the record writer refused (see above)"
fi
echo "${OUTPUT}"

# The connector lane's third ledger. Refuses rather than warns, for the same
# reason `give_up` is loud: a green run with no record is indistinguishable, at
# a glance, from a green run with one. The writer is idempotent — a row that
# already matches is a no-op, and a row that DISAGREES refuses rather than
# overwriting, because a published contract is written once.
if [ -n "${MANIFEST_PYTHON}" ]; then
  if ! DIGEST_OUTPUT="$("${MANIFEST_PYTHON}" scripts/released_manifest_sweep.py \
      --record --tag "${TAG}" --release-run "${RELEASE_RUN}" 2>&1)"; then
    echo "${DIGEST_OUTPUT}"
    give_up "the released-manifest digest writer refused (see above)"
  fi
  echo "${DIGEST_OUTPUT}"
  OUTPUT="${OUTPUT}
${DIGEST_OUTPUT}"
fi

if git diff --quiet; then
  # A real success, and the ONLY one besides opening the pull request: the
  # writer found both ledgers already correct, so there is nothing to record.
  echo "the ${TAG} record is already complete — no pull request needed"
  exit 0
fi

git add -A
git commit --quiet -m "chore(release): record the ${DISTRIBUTION} ${VERSION} publication

Written by scripts/write_release_record.py from the release workflow, straight
after tagging ${TAG}.

The tag makes this distribution's publication-ledger row false the moment it
lands, and any released migrations become bytes that must not change. Both
halves are recorded here rather than remembered, because remembering has
failed four times.

Digests, where present, are read from the tag itself rather than the working
tree: a digest taken from the tree would agree with an edit made after
publication, which is exactly what the immutability gate exists to refuse." \
  || give_up "nothing to commit after a non-empty diff (unexpected)"

git push --force-with-lease origin "${BRANCH}" --quiet \
  || give_up "could not push ${BRANCH}"

if ! gh pr view "${BRANCH}" --json number >/dev/null 2>&1; then
  gh pr create \
    --base main \
    --head "${BRANCH}" \
    --title "chore(release): record the ${DISTRIBUTION} ${VERSION} publication" \
    --body "Opened automatically by the release workflow immediately after tagging \`${TAG}\`.

\`${DISTRIBUTION} ${VERSION}\` **is published and tagged.** Until this merges, \`main\` is red on the gates that hold a publication and its record together, and every open pull request inherits those failures while looking like its own branch is broken.

$(echo "${OUTPUT}" | sed 's/^/- /')

Both edits are mechanical. The publication-ledger row is removed as *text*, so the file's prose is untouched; any migration digests are read from the tag rather than the working tree, because a digest taken from the tree would agree with an edit made after publication.

This bookkeeping pull request is configured to squash-merge automatically as
soon as every protected-main check is green." \
    || give_up "could not open the pull request (branch ${BRANCH} is pushed)"
else
  echo "a pull request for ${BRANCH} already exists — updated it"
fi

gh pr merge "${BRANCH}" --auto --squash \
  || give_up "could not enable auto-merge for ${BRANCH}"

echo "post-release record opened with auto-merge enabled for ${TAG}"
