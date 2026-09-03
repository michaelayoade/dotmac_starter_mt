#!/usr/bin/env python3
"""A tree declaring a BUILT candidate's version must ship that candidate's source.

`dotmac-deployment-foundation 0.3.0a5` is why this exists, and it is the second
time the fleet has paid for the same shape. The candidate was built once as
artifact 9903418260 from ``27bee8fc``; PR #600 then added 405 lines across three
files of the facility's importable source while ``version.py`` and
``pyproject.toml`` still declared ``0.3.0a5``. One version name, two contracts,
live on ``main`` — the exact state `AGENTS.md` rule 34 exists to prevent.

## Why two real guards were both silent

This repository already runs two guards over version identity, and NEITHER can
see this population. That is the finding worth carrying, because it is not an
absence of care:

* `scripts/version_binding_guard.py` answers *"may `<facility> <version>` be
  BUILT from `<source>` right now?"*. It runs in the candidate and release
  lanes. It would refuse a second ``0.3.0a5`` build, correctly — but it never
  runs on an ordinary merge, so a tree drifting away from a candidate it already
  built is not a question anybody asks it.
* `tests/architecture/test_declared_version_matches_published_tree.py` compares
  a distribution's ``src/`` against the git TAG of the version it declares. A
  candidate is untagged by construction (``tagged: false`` in its receipt), so
  that guard's oracle does not exist for this population and it finds nothing.

A repository that BUILDS artifacts before publishing them has **two**
populations — published distributions, and built-but-unpublished candidates —
and both existing guards were written against the first. `AGENTS.md` rule 25's
*extent* shape, precisely: **derive the extent, never declare it.** The region
read as covered because the guards flanking it are real ones.

## What this guard compares, and why it is a tree object

The subject is the git TREE OBJECT HASH of the facility's ``src/`` at two
revisions: the ``source_sha`` its candidate receipt records, and this tree. A
tree hash covers every path, every byte, every mode, every addition and every
deletion in one comparison that no file walk can be fooled into skipping — the
same reason `published_source_drift.py` uses one. It is a property, not a name:
nothing here greps for a version string or trusts a filename.

``src/`` rather than the package directory, for the reason that sibling already
established: comparing whole directories reports ``CHANGELOG.md``,
``EXTRACTION.toml`` and the ``pyproject.toml`` version line as drift, which is
governance bookkeeping rather than a lie about what a consumer installs. The
property that bit is the IMPORTABLE code.

## Both populations are DERIVED

Facilities come from ``.github/release-facilities.json`` — the closed set this
repository may publish — and candidate receipts are discovered **by schema**
(``CandidateArtifact.v1``), never by filename, so a receipt renamed or moved
still binds its version. `version_binding_guard.candidate_bindings` already made
that choice and it is inherited here rather than re-argued.

## Refusing to answer is not passing

Two conditions make this guard exit 2 rather than 0:

* the recorded ``source_sha`` is not an object in this checkout — a shallow
  clone cannot compare against bytes it does not have, and reporting "no drift"
  there would be a claim about a directory rather than about an artifact;
* the working tree is dirty within the facility's ``src/`` — a local run would
  otherwise answer about ``HEAD`` while the operator is looking at their edits.

`version_binding_guard`'s tag oracle draws the same line for the same reason,
and `test_declared_publication.py` records what it cost to learn: a sweep that
skipped on an unavailable oracle reported green while checking nothing.

## The ratchet, in both directions

A drifted facility fails. A REPAIRED one fails too, until the baseline is
regenerated (``--write``), because a debt list whose count can fall silently
stops describing reality — which is how a known-debt file becomes a place
defects go to be forgotten (ADR-0018).

## What this guard does NOT decide

Which version to allocate next. It refuses on a binding it can point at and
stops; a guard that picked the successor would be choosing a release identity as
a side effect of a check. The repair is always the same and is stated in the
refusal: **allocate a new version**, then append the superseded candidate's
`CandidateDisposition.v1`.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 -- fixed argv list, shell=False; git only
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
FACILITIES: Final = REPO_ROOT / ".github" / "release-facilities.json"
INVENTORIES: Final = REPO_ROOT / "docs" / "inventories"
BASELINE_PATH: Final = (
    REPO_ROOT / "tests" / "architecture" / "candidate_source_binding_baseline.json"
)

CANDIDATE_SCHEMA: Final = "CandidateArtifact.v1"

EXIT_OK: Final = 0
EXIT_REFUSED: Final = 1
EXIT_CANNOT_ANSWER: Final = 2


class CannotAnswer(RuntimeError):
    """An oracle this guard depends on is unavailable in this checkout."""


@dataclass(frozen=True, slots=True)
class Drift:
    """One facility whose declared version names bytes this tree is not."""

    facility: str
    version: str
    source_sha: str
    recorded_tree: str
    declared_tree: str
    receipt: str

    @property
    def key(self) -> str:
        return f"{self.facility}@{self.version}"

    def as_row(self) -> dict[str, str]:
        return {
            "facility": self.facility,
            "version": self.version,
            "source_sha": self.source_sha,
            "recorded_src_tree": self.recorded_tree,
            "declared_src_tree": self.declared_tree,
            "receipt": self.receipt,
        }

    def __str__(self) -> str:
        return (
            f"{self.facility} declares {self.version}, whose candidate receipt "
            f"({self.receipt}) records it BUILT from {self.source_sha} with "
            f"src tree {self.recorded_tree}. This tree's src is "
            f"{self.declared_tree}. One version name now covers two different "
            f"sets of importable bytes, so every pin against {self.version} is "
            "unidentifiable. Allocate a NEW version and append a "
            "CandidateDisposition.v1 for the superseded candidate; do not edit "
            "the receipt, which is a frozen fact about bytes that were built"
        )


def git(*args: str) -> str:
    result = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CannotAnswer(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def facilities() -> dict[str, dict[str, Any]]:
    """The closed set of publishable facilities. Derived, never hand-listed."""
    try:
        document = json.loads(FACILITIES.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CannotAnswer(f"cannot read {FACILITIES}: {exc}") from exc
    entries = document.get("facilities")
    if not isinstance(entries, dict) or not entries:
        raise CannotAnswer(
            f"{FACILITIES} carries no `facilities` object. Refusing to answer "
            "rather than reporting 'no facilities', which is indistinguishable "
            "from a clean tree"
        )
    return {name: entry for name, entry in entries.items() if isinstance(entry, dict)}


def declared_version(package_dir: Path) -> str:
    """The version this TREE declares, read from the distribution's own metadata."""
    path = package_dir / "pyproject.toml"
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CannotAnswer(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CannotAnswer(f"{path} is not valid TOML: {exc}") from exc
    for table in (
        document.get("project", {}),
        document.get("tool", {}).get("poetry", {}),
    ):
        version = table.get("version") if isinstance(table, dict) else None
        if isinstance(version, str) and version.strip():
            return version.strip()
    raise CannotAnswer(f"{path} declares no version")


def candidate_receipts() -> list[tuple[Path, dict[str, Any]]]:
    """Every `CandidateArtifact.v1`, discovered BY SCHEMA rather than by name."""
    found: list[tuple[Path, dict[str, Any]]] = []
    if not INVENTORIES.is_dir():
        raise CannotAnswer(
            f"{INVENTORIES} does not exist, so no candidate receipt can be "
            "read. An absent inventory is an unavailable oracle, not an "
            "absence of candidates"
        )
    for path in sorted(INVENTORIES.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(document, dict) and document.get("schema") == CANDIDATE_SCHEMA:
            found.append((path, document))
    return found


def _tree(revision: str, path: str) -> str:
    return git("rev-parse", f"{revision}:{path}")


def _require_clean(src_path: str) -> None:
    status = git("status", "--porcelain", "--", src_path)
    if status:
        raise CannotAnswer(
            f"the working tree is dirty under {src_path}:\n{status}\n"
            "This guard compares committed tree objects, so it would answer "
            "about HEAD while you are looking at your edits. Commit or stash "
            "first. Reporting a stale answer is worse than refusing one"
        )


def drifted(
    *,
    facility_entries: dict[str, dict[str, Any]] | None = None,
    receipts: list[tuple[Path, dict[str, Any]]] | None = None,
) -> list[Drift]:
    """Every facility whose declared version was already built to other bytes.

    The two populations are parameters so a test can drive this against a
    SYNTHETIC receipt and prove the detector still fires after the live defect
    is repaired. A guard whose only demonstration is the defect it was written
    for stops being demonstrable the moment it works — and `AGENTS.md` rule 25
    requires the sensitivity proof to outlive the debt, not to depend on it.
    Both default to the real, derived populations, so production behaviour is
    the injection-free path.
    """
    receipts = candidate_receipts() if receipts is None else receipts
    entries = facilities() if facility_entries is None else facility_entries
    findings: list[Drift] = []
    for name, entry in sorted(entries.items()):
        package_dir = entry.get("package_dir")
        if not isinstance(package_dir, str) or not package_dir.strip():
            raise CannotAnswer(
                f"facility {name!r} declares no package_dir in {FACILITIES}, so "
                "this guard cannot locate the source its versions bind to"
            )
        src_path = f"{package_dir.rstrip('/')}/src"
        if not (REPO_ROOT / src_path).is_dir():
            raise CannotAnswer(
                f"facility {name!r} names package_dir {package_dir!r} but "
                f"{src_path} is not a directory. Refusing to answer rather "
                "than skipping a facility, which would silently shrink the "
                "extent this guard claims to cover"
            )
        _require_clean(src_path)
        version = declared_version(REPO_ROOT / package_dir)
        declared_tree = _tree("HEAD", src_path)
        for path, document in receipts:
            if document.get("facility") != name:
                continue
            if str(document.get("version")) != version:
                continue
            source_sha = str(document.get("source_sha") or "")
            if not source_sha:
                raise CannotAnswer(
                    f"{path.relative_to(REPO_ROOT)} records a candidate for "
                    f"{name} {version} with no source_sha. A receipt that "
                    "cannot say which source it was built from cannot be "
                    "compared with anything"
                )
            try:
                recorded_tree = _tree(source_sha, src_path)
            except CannotAnswer as exc:
                raise CannotAnswer(
                    f"the source {source_sha} recorded by "
                    f"{path.relative_to(REPO_ROOT)} is not reachable in this "
                    f"checkout ({exc}). A shallow clone cannot compare against "
                    "bytes it does not have, and answering 'no drift' here "
                    "would be a claim about a directory rather than about an "
                    "artifact. Fetch with fetch-depth: 0"
                ) from exc
            if recorded_tree != declared_tree:
                findings.append(
                    Drift(
                        facility=name,
                        version=version,
                        source_sha=source_sha,
                        recorded_tree=recorded_tree,
                        declared_tree=declared_tree,
                        receipt=str(path.relative_to(REPO_ROOT)),
                    )
                )
    return findings


def baseline_document(rows: list[Drift]) -> dict[str, Any]:
    return {
        "$comment": [
            "Facilities whose DECLARED version was already built as a",
            "candidate to DIFFERENT importable bytes. Each row is one version",
            "name covering two contracts.",
            "",
            "Owner and checker: scripts/candidate_source_binding.py.",
            "Gate: tests/architecture/test_candidate_source_binding.py.",
            "",
            "This is FROZEN DEBT, ratcheted in BOTH directions: a newly",
            "drifted facility fails, and a REPAIRED one fails too until this",
            "file is regenerated with --write. A count that can fall silently",
            "stops describing reality, which is how a known-debt list becomes",
            "a place defects go to be forgotten (ADR-0018).",
            "",
            "Repair a row by allocating a NEW version and appending a",
            "CandidateDisposition.v1 for the superseded candidate. NEVER by",
            "editing the receipt: it is a frozen fact about bytes that were",
            "built once, and a restore proof or a cutover may already bind to",
            "it.",
            "",
            "An EMPTY rows array is the healthy state and is a claim, not an",
            "absence: it says every facility's declared version either has no",
            "candidate yet or still ships exactly the source that candidate",
            "was built from.",
        ],
        "rows": [row.as_row() for row in sorted(rows, key=lambda item: item.key)],
        "total": len(rows),
    }


def render_baseline(rows: list[Drift]) -> str:
    return json.dumps(baseline_document(rows), indent=2, sort_keys=True) + "\n"


def load_baseline() -> dict[str, Any]:
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CannotAnswer(f"cannot read {BASELINE_PATH}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the drift set differs from the committed baseline",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate the baseline from this tree",
    )
    args = parser.parse_args(argv)

    try:
        rows = drifted()
    except CannotAnswer as exc:
        print(f"CANNOT ANSWER: {exc}", file=sys.stderr)
        return EXIT_CANNOT_ANSWER

    if args.write:
        BASELINE_PATH.write_text(render_baseline(rows), encoding="utf-8")
        print(f"wrote {BASELINE_PATH.relative_to(REPO_ROOT)} ({len(rows)} row(s))")
        return EXIT_OK

    for row in rows:
        print(row)

    if not args.check:
        print(f"\n{len(rows)} drifted facility/facilities")
        return EXIT_OK

    try:
        expected = load_baseline()
    except CannotAnswer as exc:
        print(f"CANNOT ANSWER: {exc}", file=sys.stderr)
        return EXIT_CANNOT_ANSWER

    if render_baseline(rows) != json.dumps(expected, indent=2, sort_keys=True) + "\n":
        print(
            "\nREFUSED: the drift set does not match "
            f"{BASELINE_PATH.relative_to(REPO_ROOT)}.\n"
            "A NEW drifted facility is a version naming two contracts — "
            "allocate a new version.\n"
            "A REPAIRED one is also a failure until you re-run this with "
            "--write, so the count cannot fall silently.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
