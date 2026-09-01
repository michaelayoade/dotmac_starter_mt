#!/usr/bin/env python3
"""A version name binds to ONE source and ONE artifact, or the build is refused.

`dotmac-deployment-foundation 0.3.0a2` is why this exists. It was built once as
candidate artifact 9740182233 from ``e930f878…``; commit ``0f390a9a…`` (#551)
then changed the facility source under the same declared version, and for ten
days the repository offered one version name over two different contracts. That
is the state `AGENTS.md` rule 34 exists to prevent — an installation adopts by
digest, so a version naming two contracts makes every pin against either of them
unidentifiable — and nothing was watching for it.

Every part of the repair already existed as a RECORD. A published version has a
tag. A built candidate has a `CandidateArtifact.v1` receipt naming its source and
its bytes. An invalidated candidate now has a `CandidateDisposition.v1`. What was
missing was anything that READ them before allocating the next build, so the
records described the collision instead of preventing it.

## The question this answers

*May `<facility> <version>` be built from `<source>` right now?*

Not "does this version look new" — that is the judgement that failed. The guard
enumerates the real bindings and refuses on any of them:

* **a git tag** — the version was published. A tag is written only after
  `release_module.py verify-registry` installed the exact version from the index,
  so it is this repository's own assertion that the version EXISTS somewhere a
  consumer can pin.
* **a candidate receipt** — the version was already built once, to specific
  bytes. A second build under the same name produces different bytes with the
  same identity, which is the collision itself. Rebuilding to *matching* bytes is
  not an escape: a rebuild that happens to match is a claim, not a proof
  (`scripts/foundation_candidate.py`).
* **a disposition** — the version was consumed. An `invalidated` candidate is
  the sharpest case, because its receipt already exists AND it has been ruled
  permanently unpublishable.

## Why the tag oracle refuses rather than skips

Without tags this guard can see candidates and dispositions but not
publications, so it would admit a version that is already on the index. That is
the failure mode it exists to stop, so an unfetched checkout is a REFUSAL to
answer (exit 2), never a pass. `test_declared_publication.py` records the same
lesson learned expensively: its sweep used to `pytest.skip` on an unavailable
oracle, so every check in the module skipped silently while CI reported green.

The cost is one workflow line — `fetch-depth: 0` — which every lane calling this
already declares.

## Two purposes, because a release lane's input is a candidate

`--purpose candidate` refuses all three bindings: a version about to be BUILT
must be untouched.

`--purpose release` deliberately does not refuse on a candidate receipt, because
publishing the exact bytes a candidate lane already built is the correct release
path — `foundation-candidate.yml` exists precisely so that publication reuses
those bytes rather than rebuilding them. Refusing there would block every
release the bootstrap sequence was designed around. Tags and unpublishable
dispositions still refuse, which is what stops a republication and what stops an
invalidated candidate from reaching the index.

The distinction is a flag rather than two scripts because it is one policy with
one exception, and two scripts would let the exception drift into the rule.

## What this guard does NOT decide

Whether a version SHOULD be built. That is the release owner's, and this refuses
only on collisions it can point at. It also allocates nothing: a refusal names
the binding and stops, because a guard that picked the next free version would be
choosing a release identity as a side effect of a check.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 -- argv list, shell=False; git only
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
INVENTORIES: Final = Path("docs") / "inventories"
FACILITIES: Final = Path(".github") / "release-facilities.json"

CANDIDATE_SCHEMA: Final = "CandidateArtifact.v1"
DISPOSITION_SCHEMA: Final = "CandidateDisposition.v1"

#: Exit code for a REFUSAL — the version is bound and must not be built.
EXIT_REFUSED: Final = 1
#: Exit code for "this guard cannot answer". Separate from a refusal on purpose:
#: a caller must be able to tell "the oracle is missing, fix the checkout" from
#: "the version is taken, allocate another", and collapsing them into one code
#: is how a broken oracle gets treated as a policy decision.
EXIT_CANNOT_ANSWER: Final = 2


class CannotAnswer(RuntimeError):
    """An oracle this guard depends on is unavailable."""


@dataclass(frozen=True, slots=True)
class Binding:
    """One reason a version name is already taken."""

    kind: str
    version: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CannotAnswer(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def facilities(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    document = json.loads((repo_root / FACILITIES).read_text(encoding="utf-8"))
    entries = document.get("facilities")
    if not isinstance(entries, dict):
        raise CannotAnswer(f"{FACILITIES} carries no `facilities` object")
    return entries


def tag_prefix(facility: str, repo_root: Path = REPO_ROOT) -> str:
    entry = facilities(repo_root).get(facility)
    if not isinstance(entry, dict) or not entry.get("tag_prefix"):
        raise CannotAnswer(
            f"{facility!r} is not an allowlisted facility in {FACILITIES}, so "
            "this guard does not know which tags would bind its versions. "
            "Refusing to answer rather than reporting 'no tag found', which "
            "would be indistinguishable from a clean version"
        )
    return str(entry["tag_prefix"])


def _inventory_documents(repo_root: Path) -> list[tuple[Path, Any]]:
    found: list[tuple[Path, Any]] = []
    directory = repo_root / INVENTORIES
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.json")):
        try:
            found.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except ValueError:
            continue
    return found


def candidate_bindings(facility: str, *, repo_root: Path = REPO_ROOT) -> list[Binding]:
    """Every version this facility has already built a candidate of.

    Discovered by SCHEMA rather than by filename. A receipt renamed, moved or
    added under a new convention still binds its version, and a guard keyed to
    `foundation-candidate-*.json` would have silently stopped seeing it.
    """
    bindings: list[Binding] = []
    for path, document in _inventory_documents(repo_root):
        if not isinstance(document, dict):
            continue
        if document.get("schema") != CANDIDATE_SCHEMA:
            continue
        if document.get("facility") != facility:
            continue
        bindings.append(
            Binding(
                kind="candidate artifact",
                version=str(document.get("version")),
                detail=(
                    f"{path.relative_to(repo_root)} records artifact "
                    f"{document.get('artifact_id')} built from "
                    f"{document.get('source_sha')} (sha256 "
                    f"{document.get('sha256')}). A version is built ONCE; a "
                    "second build under the same name produces different bytes "
                    "with the same identity, and a rebuild that happens to "
                    "match is a claim rather than a proof"
                ),
            )
        )
    return bindings


def disposition_bindings(
    facility: str, *, repo_root: Path = REPO_ROOT
) -> list[Binding]:
    """Every version this facility has already CONSUMED."""
    bindings: list[Binding] = []
    for path, document in _inventory_documents(repo_root):
        if not isinstance(document, dict):
            continue
        for entry in document.get("entries", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("schema") != DISPOSITION_SCHEMA:
                continue
            if entry.get("facility") != facility:
                continue
            bindings.append(
                Binding(
                    kind=f"disposition ({entry.get('disposition')})",
                    version=str(entry.get("version")),
                    detail=(
                        f"{path.relative_to(repo_root)} records this version "
                        f"{entry.get('disposition')}"
                        + (
                            f" by commit {entry.get('invalidating_commit')}"
                            if entry.get("invalidating_commit")
                            else ""
                        )
                        + f", publishable={entry.get('publishable')}. A consumed "
                        "version is never reissued: the record naming it would "
                        "then describe bytes nobody can identify"
                    ),
                )
            )
    return bindings


def tag_bindings(facility: str, *, repo_root: Path = REPO_ROOT) -> list[Binding]:
    """Every version of this facility that has been published.

    Raises :class:`CannotAnswer` when git has no tags — see the module docstring.
    A checkout with no tags at all is indistinguishable from a facility that has
    never been released, and the two must not produce the same answer.
    """
    prefix = tag_prefix(facility, repo_root)
    if not _git(repo_root, "tag", "-l").strip():
        raise CannotAnswer(
            "this checkout has no tags, so published versions are invisible and "
            "an already-released version would be admitted. Fetch tags "
            "(`fetch-depth: 0`) — an unavailable oracle is not a pass"
        )
    bindings: list[Binding] = []
    for tag in _git(repo_root, "tag", "-l", f"{prefix}*").split():
        peeled = _git(repo_root, "rev-list", "-n", "1", tag).strip()
        bindings.append(
            Binding(
                kind="published tag",
                version=tag[len(prefix) :],
                detail=(
                    f"tag {tag} peels to {peeled}. The tag is written only after "
                    "the exact version was installed from the index, so this "
                    "version exists somewhere a consumer can already pin"
                ),
            )
        )
    return bindings


PURPOSES: Final[tuple[str, ...]] = ("candidate", "release")


def all_bindings(facility: str, *, repo_root: Path = REPO_ROOT) -> list[Binding]:
    return (
        tag_bindings(facility, repo_root=repo_root)
        + candidate_bindings(facility, repo_root=repo_root)
        + disposition_bindings(facility, repo_root=repo_root)
    )


def bindings_for(
    facility: str,
    version: str,
    *,
    repo_root: Path = REPO_ROOT,
    purpose: str = "candidate",
) -> list[Binding]:
    """Every reason ``version`` is refused for ``purpose``. Empty means free."""
    if purpose not in PURPOSES:
        raise CannotAnswer(f"unknown purpose {purpose!r}; expected {list(PURPOSES)}")
    found = [
        binding
        for binding in all_bindings(facility, repo_root=repo_root)
        if binding.version == version
    ]
    if purpose == "release":
        # A candidate receipt for THIS version is the release's input, not a
        # collision: `foundation-candidate.yml` builds once so that publication
        # reuses those exact bytes. Tags and unpublishable dispositions still
        # bite, which is what stops a republication and an invalidated release.
        found = [
            binding
            for binding in found
            if binding.kind != "candidate artifact"
            and not binding.kind.startswith("disposition (published)")
        ]
    return found


def cmd_check(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    try:
        found = bindings_for(
            args.facility, args.version, repo_root=repo_root, purpose=args.purpose
        )
    except CannotAnswer as refusal:
        print(
            f"CANNOT ANSWER for {args.facility} {args.version}: {refusal}",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ANSWER
    if found:
        print(
            f"REFUSED for {args.purpose}: {args.facility} {args.version} is "
            f"already bound {len(found)} way(s):",
            file=sys.stderr,
        )
        for binding in found:
            print(f"  - {binding}", file=sys.stderr)
        print(
            "\nAllocate the next unbound version. Do NOT rebuild, republish, "
            "tag or re-declare a bound one: one version name over two contracts "
            "makes every pin against either of them unidentifiable "
            "(AGENTS.md rule 34).",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    print(
        f"ADMIT for {args.purpose}: {args.facility} {args.version} is bound by "
        "nothing on record that forbids it"
    )
    return 0


def cmd_bindings(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    try:
        every = all_bindings(args.facility, repo_root=repo_root)
    except CannotAnswer as refusal:
        print(f"CANNOT ANSWER: {refusal}", file=sys.stderr)
        return EXIT_CANNOT_ANSWER
    for binding in sorted(every, key=lambda item: (item.version, item.kind)):
        print(f"{binding.version:>12}  {binding.kind}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="refuse a version that is already bound")
    check.set_defaults(handler=cmd_check)
    check.add_argument("facility")
    check.add_argument("--version", required=True)
    check.add_argument(
        "--purpose",
        default="candidate",
        choices=list(PURPOSES),
        help=(
            "`candidate` refuses every binding. `release` permits this "
            "version's own candidate receipt, which is the bytes it publishes"
        ),
    )

    listing = sub.add_parser("bindings", help="print every bound version")
    listing.set_defaults(handler=cmd_bindings)
    listing.add_argument("facility")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
