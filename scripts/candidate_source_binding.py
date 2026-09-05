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

## The window this guard could not see, and the `window` mode that closes it

Everything above answers the question *"does this tree still ship the source the
RECORDED candidate was built from?"*. It is a good question and it has now been
silent through a fourth recurrence, because it presupposes the record.

`dotmac-deployment-foundation 0.4.0a1` is the case. Run ``33920058598`` built the
candidate from ``753a004e`` on 2026-09-04, successfully, and the receipt was left
in the run's artifacts. It never reached ``docs/inventories/``. From that moment
the repository held no fact at all about the build:

    $ python3 scripts/candidate_source_binding.py --check
    0 drifted facility/facilities
    $ python3 scripts/version_binding_guard.py check \
        dotmac-deployment-foundation --version 0.4.0a1 --purpose candidate
    ADMIT for candidate: ... bound by nothing on record that forbids it

Both guards were right about the records and wrong about the world. ``#628`` and
``#631`` then moved the facility's importable source under the unchanged
``0.4.0a1`` name, and `tests/architecture/test_version_binding_guard.py` went on
asserting that ``0.4.0a1`` is *not recorded as built* — an assertion that was
true of the ledger and false of the artifact.

**A LIVE CANDIDATE is therefore not "a receipt in the tree".** It is a build that
happened and has not been consumed. Precisely, for facility ``F`` declaring
version ``V``: a successful run of the candidate lane, at a commit whose declared
version for ``F`` is ``V``, which produced the ``<F>-candidate`` artifact, and for
which no publication tag or `CandidateDisposition.v1` has retired ``V``. The
state lives where it always lived — the run, and the `CandidateArtifact.v1`
receipt that is supposed to be committed from it — and this mode reads BOTH
rather than introducing a third store.

## Why the build oracle is a network call, and why that is the doctrine

*"No candidate has been built for the declared version"* is a claim about the
BUILD SYSTEM, not about this repository, and `AGENTS.md` rule 30 says a claim of
that kind needs an authoritative external oracle carrying immutable coordinates.
The coordinates here are the run id and the artifact id — the same two facts
`CandidateArtifact.v1` records. No repository-local formulation can see this
population: at the moment ``#628`` was opened, the tree's every byte was
consistent with ``0.4.0a1`` never having been built.

So the oracle is the Actions API, read through ``gh`` exactly as
`scripts/foundation_candidate.py` already reads it, and an unreachable oracle
exits 2. It is not a pass.

## What `window` compares, and what it refuses on

Still tree objects, never version strings — a version string is what failed to
notice this. For every live candidate build it reports:

* ``unrecorded`` — the build produced bytes and this tree carries no
  `CandidateArtifact.v1` for them. Every downstream guard reads that receipt, so
  its absence is not bookkeeping: it is the reason the other four checks were
  silent.
* ``drifted`` — the facility's ``src/`` tree object at this revision differs from
  the one that build compiled. One version name, two sets of importable bytes.

## Where it runs, which is the half that was missing

In `ci.yml`'s own ``candidate-window`` job, on every pull request and every push
to ``main`` — not in the release lane and not only when a receipt is staged. The
release lane's checks all fire at dispatch, weeks after the drift has landed, and
`release_facility.require_candidate_ancestry` deliberately permits the tree to
have moved (equality there would be unsatisfiable and would get waived). Neither
of those is the moment this defect is cheap. The moment is the pull request that
moves the source.

## What this guard does NOT decide

Which version to allocate next. It refuses on a binding it can point at and
stops; a guard that picked the successor would be choosing a release identity as
a side effect of a check. That property is not incidental to `window` either — a
gate that answered "rebuild as 0.4.0a2" would be issuing a release identity from
a pull-request check, and the identity would then exist because a script emitted
it rather than because anybody decided it. The repair is always the same and is
stated in the refusal: **allocate a new version**, then append the superseded
candidate's `CandidateDisposition.v1`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 -- fixed argv list, shell=False; git and gh only
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
#: The `window` mode's own frozen debt. Separate FILE, same owner and same
#: writer: the two modes ask different questions of different populations, and
#: one baseline holding both would make the offline receipt check depend on a
#: network oracle it has no reason to need.
WINDOW_BASELINE_PATH: Final = (
    REPO_ROOT / "tests" / "architecture" / "candidate_window_baseline.json"
)

CANDIDATE_SCHEMA: Final = "CandidateArtifact.v1"

#: The lane that BUILDS candidates. Named rather than inferred, and checked:
#: `tests/architecture/test_candidate_window_binding.py` fails if this file
#: stops existing or stops being the workflow that uploads `<facility>-candidate`.
#: Inferring it from the workflow directory would make a renamed lane read as
#: "no candidate has ever been built", which is the exact silence being closed.
CANDIDATE_WORKFLOW: Final = "foundation-candidate.yml"

#: A run counts as a BUILD only with this conclusion. `foundation-candidate.yml`
#: uploads with `if-no-files-found: error`, so a successful run has necessarily
#: produced its artifacts — which is why success plus the artifact NAME is a
#: sound test for "this run built a candidate of this facility" without
#: downloading anything.
BUILD_CONCLUSION: Final = "success"

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


def _version_from_pyproject(source: str, label: str) -> str:
    try:
        document = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise CannotAnswer(f"{label} is not valid TOML: {exc}") from exc
    for table in (
        document.get("project", {}),
        document.get("tool", {}).get("poetry", {}),
    ):
        version = table.get("version") if isinstance(table, dict) else None
        if isinstance(version, str) and version.strip():
            return version.strip()
    raise CannotAnswer(f"{label} declares no version")


def declared_version(package_dir: Path) -> str:
    """The version this TREE declares, read from the distribution's own metadata."""
    path = package_dir / "pyproject.toml"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CannotAnswer(f"cannot read {path}: {exc}") from exc
    return _version_from_pyproject(source, str(path))


def declared_version_at(revision: str, package_dir: str) -> str:
    """The version a distribution declared at ``revision``.

    The `window` mode needs this in both directions: to read what THIS revision
    declares, and to decide which past candidate runs were building the version
    it declares. The second use is what makes the run oracle precise without
    trusting a workflow input — a run's `head_sha` is a commit, and the version
    it was building is whatever that commit's own `pyproject.toml` said.
    """
    path = f"{package_dir.rstrip('/')}/pyproject.toml"
    return _version_from_pyproject(
        git("show", f"{revision}:{path}"), f"{revision}:{path}"
    )


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


# ── the window: a build that happened, and a tree that moved under it ───────


@dataclass(frozen=True, slots=True)
class CandidateBuild:
    """One candidate artifact the BUILD SYSTEM says exists.

    Immutable coordinates, per `AGENTS.md` rule 30: a run id and an artifact id,
    the same two facts `CandidateArtifact.v1` records — so a finding can be
    checked by hand against the API, and so the repair (committing the receipt)
    is a transcription rather than an investigation.
    """

    facility: str
    version: str
    source_sha: str
    run_id: str
    artifact_id: str
    run_url: str


@dataclass(frozen=True, slots=True)
class WindowFinding:
    """One live candidate whose window is open in a way that must not stand."""

    build: CandidateBuild
    built_tree: str
    declared_tree: str
    reasons: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.build.facility}@{self.build.version}#{self.build.run_id}"

    def as_row(self) -> dict[str, Any]:
        # Deliberately WITHOUT the current src tree. The debt this row freezes is
        # "a live candidate's window is open", which is one bit and does not
        # change as further commits land; recording the moving tree would demand
        # a baseline rewrite per commit and teach everyone to regenerate the file
        # without reading it. The row clears when the version is re-allocated,
        # which is the repair.
        return {
            "artifact_id": self.build.artifact_id,
            "facility": self.build.facility,
            "reasons": list(self.reasons),
            "run_id": self.build.run_id,
            "run_url": self.build.run_url,
            "source_sha": self.build.source_sha,
            "built_src_tree": self.built_tree,
            "version": self.build.version,
        }

    def __str__(self) -> str:
        parts = [
            f"{self.build.facility} declares {self.build.version}, which run "
            f"{self.build.run_id} ({self.build.run_url}) already BUILT from "
            f"{self.build.source_sha} as artifact {self.build.artifact_id}."
        ]
        if "unrecorded" in self.reasons:
            parts.append(
                "No CandidateArtifact.v1 in docs/inventories/ records those "
                "bytes, so version_binding_guard, candidate_source_binding, "
                "Lane 3 and the release lane are all reading a repository that "
                "does not know the build happened. Commit the receipt the run "
                "uploaded."
            )
        if "drifted" in self.reasons:
            parts.append(
                f"The facility's src tree at that build is {self.built_tree} "
                f"and this revision's is {self.declared_tree}: one version name "
                "now covers two different sets of importable bytes, so every "
                "pin against it is unidentifiable. Allocate a NEW version and "
                "append a CandidateDisposition.v1 for the superseded candidate. "
                "This guard does not choose which version that is — a check "
                "that issued a release identity would be deciding one."
            )
        return " ".join(parts)


def _gh(*args: str) -> Any:
    """Read-only Actions API call through `gh`, as `foundation_candidate.py` does.

    `gh` rather than a raw request so the job token is used the same way every
    other workflow in this repository uses it, and so this needs no HTTP
    dependency in a job that installs nothing.
    """
    result = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
        ["gh", "api", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CannotAnswer(
            f"`gh api {' '.join(args)}` failed ({result.returncode}): "
            f"{result.stderr.strip()}. The build oracle is unavailable, so this "
            "guard cannot tell a facility with no candidate from one whose "
            "candidate it simply could not see — and those two must never "
            "produce the same answer. In CI the job needs `actions: read` and "
            "GH_TOKEN; locally it needs `gh auth login`"
        )
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise CannotAnswer(
            f"`gh api {' '.join(args)}` returned non-JSON: {exc}"
        ) from exc


def repository_slug() -> str:
    """`owner/name` for the repository whose runs are the oracle."""
    from_env = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if from_env:
        return from_env
    remote = git("remote", "get-url", "origin")
    stem = remote.removesuffix(".git")
    if ":" in stem and "//" not in stem:
        stem = stem.split(":", 1)[1]
    parts = [piece for piece in stem.split("/") if piece]
    if len(parts) < 2:
        raise CannotAnswer(
            f"cannot derive owner/name from the origin remote {remote!r}. "
            "Set GITHUB_REPOSITORY rather than letting this guess"
        )
    return "/".join(parts[-2:])


def oracle_builds(
    facility_entries: dict[str, dict[str, Any]],
    *,
    revision: str = "HEAD",
    repository: str | None = None,
) -> list[CandidateBuild]:
    """Every LIVE candidate build, from the workflow-run oracle.

    Live means: a successful run of the candidate lane, at a commit whose
    declared version for that facility is the version ``revision`` declares now.
    A run for an older version is not live — its name has already been moved off,
    which is the repair — so it is not reported and does not need a baseline row.

    A run's facility is decided by the ARTIFACT it produced (`<facility>-
    candidate`), never by a workflow input, because inputs are not in the runs
    API and a lane that one day builds two facilities must not have its runs
    attributed by whichever one matched a version.
    """
    slug = repository_slug() if repository is None else repository
    wanted: dict[str, tuple[str, str]] = {}
    for name, entry in sorted(facility_entries.items()):
        package_dir = _package_dir(name, entry)
        wanted[name] = (package_dir, declared_version_at(revision, package_dir))

    runs: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _gh(
            f"repos/{slug}/actions/workflows/{CANDIDATE_WORKFLOW}/runs"
            f"?per_page=100&page={page}"
        )
        if not isinstance(payload, dict):
            raise CannotAnswer(
                f"the runs endpoint for {CANDIDATE_WORKFLOW} returned "
                f"{type(payload).__name__}, not an object"
            )
        batch = payload.get("workflow_runs")
        if not isinstance(batch, list):
            raise CannotAnswer(
                f"the runs endpoint for {CANDIDATE_WORKFLOW} carries no "
                "`workflow_runs` array. Reporting 'no candidate has ever been "
                "built' from an unreadable answer is the silence this mode exists "
                "to end"
            )
        runs.extend(item for item in batch if isinstance(item, dict))
        total = payload.get("total_count")
        if not isinstance(total, int) or len(runs) >= total or not batch:
            break
        page += 1

    found: list[CandidateBuild] = []
    for run in runs:
        if run.get("conclusion") != BUILD_CONCLUSION:
            continue
        head = str(run.get("head_sha") or "")
        run_id = str(run.get("id") or "")
        if not head or not run_id:
            raise CannotAnswer(
                f"a {CANDIDATE_WORKFLOW} run carries no id or head_sha: {run!r}"
            )
        for name, (package_dir, declared) in wanted.items():
            try:
                built_version = declared_version_at(head, package_dir)
            except CannotAnswer as exc:
                raise CannotAnswer(
                    f"run {run_id} of {CANDIDATE_WORKFLOW} was built at {head}, "
                    f"which this checkout cannot read ({exc}). A shallow clone "
                    "cannot say which version a past run was building, and "
                    "answering 'none' would report every unseen build as absent. "
                    "Fetch with fetch-depth: 0"
                ) from exc
            if built_version != declared:
                continue
            artifact = _candidate_artifact(slug, run_id, name)
            if artifact is None:
                continue
            found.append(
                CandidateBuild(
                    facility=name,
                    version=declared,
                    source_sha=head,
                    run_id=run_id,
                    artifact_id=artifact,
                    run_url=str(run.get("html_url") or f"{slug} run {run_id}"),
                )
            )
    return found


def _candidate_artifact(slug: str, run_id: str, facility: str) -> str | None:
    """The id of ``<facility>-candidate`` in a run, or None if it produced none."""
    payload = _gh(f"repos/{slug}/actions/runs/{run_id}/artifacts?per_page=100")
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        raise CannotAnswer(
            f"the artifacts endpoint for run {run_id} carries no `artifacts` "
            "array, so this guard cannot say which facility that run built"
        )
    for artifact in payload["artifacts"]:
        if (
            isinstance(artifact, dict)
            and artifact.get("name") == f"{facility}-candidate"
        ):
            return str(artifact.get("id"))
    return None


def _package_dir(name: str, entry: dict[str, Any]) -> str:
    package_dir = entry.get("package_dir")
    if not isinstance(package_dir, str) or not package_dir.strip():
        raise CannotAnswer(
            f"facility {name!r} declares no package_dir in {FACILITIES}, so "
            "this guard cannot locate the source its versions bind to"
        )
    return package_dir.rstrip("/")


def window_findings(
    *,
    builds: list[CandidateBuild],
    facility_entries: dict[str, dict[str, Any]] | None = None,
    receipts: list[tuple[Path, dict[str, Any]]] | None = None,
    revision: str = "HEAD",
) -> list[WindowFinding]:
    """Every live candidate whose window is open, at ``revision``.

    All three populations are parameters for the reason the receipt-mode
    detector's are: the sensitivity proof must outlive the defect. ``revision``
    is the third one and it is what lets the proof replay the real history —
    the tree at the commit that broke it, and the tree at the commit that
    landed beside it and correctly did not.
    """
    entries = facilities() if facility_entries is None else facility_entries
    receipts = candidate_receipts() if receipts is None else receipts
    findings: list[WindowFinding] = []
    for build in sorted(builds, key=lambda item: (item.facility, item.run_id)):
        entry = entries.get(build.facility)
        if entry is None:
            continue
        package_dir = _package_dir(build.facility, entry)
        # LIVENESS, decided here rather than only in the oracle. A candidate
        # built under a name this revision no longer declares is not live: its
        # identity has been moved off, which IS the repair, and the window
        # closes for the same reason it opened. Deciding it in the guard rather
        # than only in the query is what makes the repair provable offline.
        if build.version != declared_version_at(revision, package_dir):
            continue
        src_path = f"{package_dir}/src"
        if not (REPO_ROOT / src_path).is_dir():
            raise CannotAnswer(
                f"facility {build.facility!r} names {src_path}, which is not a "
                "directory. Refusing to answer rather than skipping a facility, "
                "which would silently shrink the extent this guard covers"
            )
        if revision == "HEAD":
            _require_clean(src_path)
        declared_tree = _tree(revision, src_path)
        try:
            built_tree = _tree(build.source_sha, src_path)
        except CannotAnswer as exc:
            raise CannotAnswer(
                f"run {build.run_id} built {build.facility} {build.version} from "
                f"{build.source_sha}, which is not reachable in this checkout "
                f"({exc}). Answering 'no open window' here would be a claim "
                "about a directory rather than about an artifact. Fetch with "
                "fetch-depth: 0"
            ) from exc
        recorded = any(
            document.get("schema") == CANDIDATE_SCHEMA
            and document.get("facility") == build.facility
            and str(document.get("version")) == build.version
            and str(document.get("source_sha") or "") == build.source_sha
            for _path, document in receipts
        )
        reasons = tuple(
            reason
            for reason, holds in (
                ("unrecorded", not recorded),
                ("drifted", built_tree != declared_tree),
            )
            if holds
        )
        if reasons:
            findings.append(
                WindowFinding(
                    build=build,
                    built_tree=built_tree,
                    declared_tree=declared_tree,
                    reasons=reasons,
                )
            )
    return findings


def window_baseline_document(rows: list[WindowFinding]) -> dict[str, Any]:
    return {
        "$comment": [
            "LIVE CANDIDATES whose window between BUILD and PUBLICATION is open",
            "in a way that must not stand: bytes were built for the version this",
            "tree declares, and either no CandidateArtifact.v1 records them or",
            "the facility's importable source has moved since.",
            "",
            "Owner and checker: scripts/candidate_source_binding.py --window.",
            "Gate: the `candidate-window` job in .github/workflows/ci.yml, which",
            "is where a pull request that moves the source can still be stopped.",
            "Sensitivity proof: tests/architecture/test_candidate_window_binding.py.",
            "",
            "A row does NOT carry the current src tree. The debt is 'this",
            "candidate's window is open', which is one fact and does not change",
            "as further commits land; recording a moving value would demand a",
            "rewrite per commit and teach everyone to regenerate this file",
            "without reading it.",
            "",
            "FROZEN DEBT, ratcheted in BOTH directions (ADR-0018): a newly opened",
            "window fails, and a REPAIRED one fails too until this file is",
            "regenerated with `--window --write`, so the count cannot fall",
            "silently.",
            "",
            "Repair `unrecorded` by committing the receipt the run uploaded.",
            "Repair `drifted` by allocating a NEW version and appending a",
            "CandidateDisposition.v1 for the superseded candidate. Which version",
            "that is, is a decision; this guard refuses and names the binding,",
            "and deliberately does not choose one.",
            "",
            "An EMPTY rows array is the healthy state and is a claim, not an",
            "absence: it says the build oracle was reached and every candidate it",
            "knows of is either recorded and unmoved, or built under a version",
            "this tree no longer declares.",
        ],
        "rows": [row.as_row() for row in sorted(rows, key=lambda item: item.key)],
        "total": len(rows),
    }


def render_window_baseline(rows: list[WindowFinding]) -> str:
    return json.dumps(window_baseline_document(rows), indent=2, sort_keys=True) + "\n"


def _run_window(args: argparse.Namespace) -> int:
    try:
        if args.builds_json:
            builds = [
                CandidateBuild(**record)
                for record in json.loads(
                    Path(args.builds_json).read_text(encoding="utf-8")
                )
            ]
        else:
            builds = oracle_builds(facilities())
        rows = window_findings(builds=builds)
    except CannotAnswer as exc:
        print(f"CANNOT ANSWER: {exc}", file=sys.stderr)
        return EXIT_CANNOT_ANSWER
    except (OSError, TypeError, ValueError) as exc:
        print(f"CANNOT ANSWER: cannot read {args.builds_json}: {exc}", file=sys.stderr)
        return EXIT_CANNOT_ANSWER

    if args.write:
        WINDOW_BASELINE_PATH.write_text(render_window_baseline(rows), encoding="utf-8")
        print(
            f"wrote {WINDOW_BASELINE_PATH.relative_to(REPO_ROOT)} "
            f"({len(rows)} row(s))"
        )
        return EXIT_OK

    for row in rows:
        print(row)

    if not args.check:
        print(f"\n{len(rows)} open candidate window(s)")
        return EXIT_OK

    try:
        expected = json.loads(WINDOW_BASELINE_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        print(
            f"CANNOT ANSWER: cannot read {WINDOW_BASELINE_PATH}: {exc}",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ANSWER

    if (
        render_window_baseline(rows)
        != json.dumps(expected, indent=2, sort_keys=True) + "\n"
    ):
        print(
            "\nREFUSED: the open-window set does not match "
            f"{WINDOW_BASELINE_PATH.relative_to(REPO_ROOT)}.\n"
            "A NEW row is a candidate whose bytes this tree has already left "
            "behind, or a build this tree does not record — stop and repair it; "
            "do not regenerate the baseline to make the message go away.\n"
            "A REMOVED row is a repair that must be recorded: re-run with "
            "`--window --write` so the count cannot fall silently.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    return EXIT_OK


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
    parser.add_argument(
        "--window",
        action="store_true",
        help=(
            "ask the OTHER question: is a candidate LIVE for the declared "
            "version, and has this tree left its bytes behind? Reads the "
            "workflow-run oracle, so it needs `gh` and network"
        ),
    )
    parser.add_argument(
        "--builds-json",
        default="",
        help=(
            "--window only: the oracle's answer supplied as a JSON array of "
            "CandidateBuild records instead of queried. For the sensitivity "
            "proof and for an offline replay of a past state; CI must NOT pass "
            "it, and a test asserts the job does not"
        ),
    )
    args = parser.parse_args(argv)

    if args.window:
        return _run_window(args)
    if args.builds_json:
        print(
            "--builds-json is meaningless without --window: the receipt-bound "
            "check reads committed receipts and has no build oracle to replace",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ANSWER

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
