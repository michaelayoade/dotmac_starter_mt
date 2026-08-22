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
# the edits; this opens them for review. `main` is protected, so this cannot
# and should not push to it directly: what it removes is the MEMORY step, not
# the review step.
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
# published and tagged — nobody should re-run the publish — and names the
# command that closes the gap. "Tag exists, record missing" is now visible in
# the place people already look.
set -uo pipefail

DISTRIBUTION=""
VERSION=""
TAG=""
PACKAGE_DIR=""
IMPORT_NAME=""

while [ $# -gt 0 ]; do
  case "$1" in
    --distribution) DISTRIBUTION="$2"; shift 2 ;;
    --version)      VERSION="$2";      shift 2 ;;
    --tag)          TAG="$2";          shift 2 ;;
    --package-dir)  PACKAGE_DIR="$2";  shift 2 ;;
    --import-name)  IMPORT_NAME="$2";  shift 2 ;;
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
  MANUAL="${MANUAL} --package-dir ${PACKAGE_DIR} --import-name ${IMPORT_NAME}"
fi

give_up() {
  echo "::error::the ${TAG} release record was NOT opened: $1"
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

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git fetch origin main --quiet || give_up "could not fetch main"
git checkout -B "${BRANCH}" origin/main --quiet || give_up "could not branch from main"

ARGS=(--distribution "${DISTRIBUTION}" --version "${VERSION}" --tag "${TAG}")
if [ -n "${PACKAGE_DIR}" ]; then
  ARGS+=(--package-dir "${PACKAGE_DIR}" --import-name "${IMPORT_NAME}")
fi

if ! OUTPUT="$(python scripts/write_release_record.py "${ARGS[@]}" 2>&1)"; then
  echo "${OUTPUT}"
  give_up "the record writer refused (see above)"
fi
echo "${OUTPUT}"

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

Review it as bookkeeping, and merge as soon as it is green." \
    || give_up "could not open the pull request (branch ${BRANCH} is pushed)"
else
  echo "a pull request for ${BRANCH} already exists — updated it"
fi

echo "post-release record opened for ${TAG}"
