"""A declared version must ship the source its CANDIDATE was built from.

## The population two real guards could not see

`test_declared_version_matches_published_tree.py` compares a distribution's
``src/`` against the git TAG of the version it declares.
`scripts/version_binding_guard.py` refuses to BUILD a version that is already
bound. Both are real, both work, and neither could see
`dotmac-deployment-foundation 0.3.0a5`: the candidate is untagged by
construction (``tagged: false``), so the first guard's oracle does not exist for
it, and the second only runs in the candidate and release lanes, so an ordinary
merge never asks it anything.

A repository that BUILDS artifacts before publishing them has TWO populations.
`AGENTS.md` rule 25's *extent* shape says derive the extent rather than declare
it, and the region here read as covered precisely because the guards flanking it
were serious ones.

## Why this lives in the `unit` job and not in `make check`

The comparison needs the candidate's ``source_sha`` to be an object in the
checkout, so it needs history. `make check` and the CI `quality` matrix run on a
default shallow checkout and must stay runnable offline; this job declares
``fetch-depth: 0`` for exactly the reason `test_declared_publication.py` needed
it. Putting the guard in the shallow matrix would make it exit 2 on every run —
an oracle-unavailable refusal reported as a build failure, or worse, quietly
tolerated until somebody made it "pass" by weakening it.

## The sensitivity proof outlives the debt

The demonstration cannot be *"it caught 0.3.0a5"*, because after that repair it
catches nothing and stops being demonstrable. So the detector's two populations
are parameters, and the proof drives a SYNTHETIC receipt against two real
revisions of this repository: one whose facility source differs from this tree
(must fire, naming the facility) and one that is this tree (must stay silent).
Both halves survive every future repair.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts/candidate_source_binding.py"


def _load_script():
    """Load the guard, REGISTERING it in `sys.modules` BEFORE executing it.

    `@dataclass(slots=True)` re-reads `sys.modules[cls.__module__]` while it
    processes the class, so a module executed without being registered raises
    `AttributeError: 'NoneType' object has no attribute '__dict__'` — which
    reads as a bug in the guard and is a bug in this loader.

    This is the THIRD time this repository has paid for it: the publication
    sweep first, then `test_version_binding_guard.py::_module`, now here. Each
    fix was correct and local, and the defect recurred because the correction
    lives in a docstring rather than in a shared loader. Noted rather than
    silently repaired again — a fourth occurrence should produce the helper, not
    a fourth copy of this paragraph.
    """
    spec = importlib.util.spec_from_file_location("_candidate_source_binding", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GUARD = _load_script()


#: The guard's OWN git helper, not a second one. A test that shelled out
#: separately would be comparing the detector against a different instrument,
#: and the two could disagree about the thing under test.
_git = GUARD.git


def _one_facility() -> tuple[str, dict[str, Any], str]:
    """A real facility, its registry entry, and its ``src`` path."""
    entries = GUARD.facilities()
    assert entries, (
        "no facility is registered in .github/release-facilities.json. A guard "
        "over an empty set passes for the wrong reason"
    )
    name = sorted(entries)[0]
    entry = entries[name]
    return name, entry, f"{str(entry['package_dir']).rstrip('/')}/src"


def _receipt(version: str, source_sha: str, facility: str) -> list:
    """A synthetic `CandidateArtifact.v1`, injected as the receipt population."""
    return [
        (
            REPO_ROOT / "docs/inventories/synthetic-candidate.json",
            {
                "schema": "CandidateArtifact.v1",
                "facility": facility,
                "version": version,
                "source_sha": source_sha,
                "artifact_id": "0",
            },
        )
    ]


# ── the ratchet ─────────────────────────────────────────────────────────────


def test_the_drift_set_matches_the_committed_baseline() -> None:
    """Two-directional. A new drifted facility fails; a REPAIRED one fails too,
    until the baseline is regenerated, so the count cannot fall silently."""
    rows = GUARD.drifted()
    rendered = GUARD.render_baseline(rows)
    committed = GUARD.BASELINE_PATH.read_text(encoding="utf-8")
    assert rendered == committed, (
        "the drift set no longer matches "
        f"{GUARD.BASELINE_PATH.relative_to(REPO_ROOT)}. A NEW row is a version "
        "naming two contracts — allocate a new version. A REMOVED row is a "
        "repair that must be recorded: re-run "
        "`python scripts/candidate_source_binding.py --write`"
    )


def test_the_recorded_total_still_describes_the_rows() -> None:
    document = json.loads(GUARD.BASELINE_PATH.read_text(encoding="utf-8"))
    assert document["total"] == len(document["rows"])


# ── sensitivity: it fires on a planted divergence ───────────────────────────


def test_the_detector_fires_on_a_planted_divergence() -> None:
    """THE SENSITIVITY PROOF. A receipt claiming this tree's declared version was
    built from a revision whose facility source differs must produce exactly one
    finding, naming the facility."""
    name, entry, src_path = _one_facility()
    version = GUARD.declared_version(REPO_ROOT / entry["package_dir"])
    here = _git("rev-parse", f"HEAD:{src_path}")

    other = ""
    for candidate in _git("log", "--format=%H", "--", src_path).splitlines():
        if _git("rev-parse", f"{candidate}:{src_path}") != here:
            other = candidate
            break
    assert other, (
        f"no commit in this repository's history has a {src_path} tree "
        "differing from HEAD, so the planted defect cannot be constructed. "
        "This is an oracle failure, not a pass"
    )

    found = GUARD.drifted(
        facility_entries={name: entry},
        receipts=_receipt(version, other, name),
    )
    assert len(found) == 1, f"the planted divergence was not detected: {found}"
    assert found[0].facility == name
    assert found[0].version == version
    assert found[0].source_sha == other
    assert found[0].recorded_tree != found[0].declared_tree
    assert name in str(found[0])


def test_the_detector_is_silent_on_the_conforming_form() -> None:
    """THE POSITIVE CONTROL. Same instrument, same scope, a receipt naming the
    revision this tree IS — no finding. An instrument that reports drift for
    every input and a tree that has drifted are the same colour."""
    name, entry, _src_path = _one_facility()
    version = GUARD.declared_version(REPO_ROOT / entry["package_dir"])
    head = _git("rev-parse", "HEAD")
    assert (
        GUARD.drifted(
            facility_entries={name: entry},
            receipts=_receipt(version, head, name),
        )
        == []
    )


def test_a_version_with_no_candidate_is_clean_rather_than_unknown() -> None:
    """The state a repair produces: the declared version has never been built,
    so nothing binds it. This is what must go GREEN after a version bump, and
    asserting it stops the repair from being mistaken for a silenced detector."""
    name, entry, _ = _one_facility()
    assert (
        GUARD.drifted(
            facility_entries={name: entry},
            receipts=_receipt("0.0.0-never-built", _git("rev-parse", "HEAD"), name),
        )
        == []
    )


# ── the extent is derived, and it is not empty ──────────────────────────────


def test_the_comparison_actually_reaches_a_real_facility() -> None:
    """Non-vacuity. Every registered facility resolves to a real src directory,
    and at least one real candidate receipt is discovered for one of them —
    otherwise this whole module passes over an empty set."""
    entries = GUARD.facilities()
    assert entries
    for name, entry in entries.items():
        src = REPO_ROOT / f"{str(entry['package_dir']).rstrip('/')}/src"
        assert src.is_dir(), f"{name} names {src}, which is not a directory"

    receipts = GUARD.candidate_receipts()
    facilities_with_receipts = {
        document.get("facility") for _, document in receipts
    } & set(entries)
    assert facilities_with_receipts, (
        "no CandidateArtifact.v1 receipt was discovered for any registered "
        "facility, so the detector compared nothing"
    )


def test_receipts_are_discovered_by_schema_not_by_filename() -> None:
    """A receipt renamed or moved still binds its version. Keying discovery to
    `foundation-candidate-*.json` would have silently stopped seeing one."""
    name, entry, src_path = _one_facility()
    version = GUARD.declared_version(REPO_ROOT / entry["package_dir"])
    here = _git("rev-parse", f"HEAD:{src_path}")
    other = next(
        commit
        for commit in _git("log", "--format=%H", "--", src_path).splitlines()
        if _git("rev-parse", f"{commit}:{src_path}") != here
    )
    renamed = [
        (
            REPO_ROOT / "docs/inventories/a-name-nobody-globbed-for.json",
            {
                "schema": "CandidateArtifact.v1",
                "facility": name,
                "version": version,
                "source_sha": other,
                "artifact_id": "0",
            },
        )
    ]
    assert len(GUARD.drifted(facility_entries={name: entry}, receipts=renamed)) == 1


# ── refusing to answer is not passing ───────────────────────────────────────


def test_an_unreachable_source_refuses_rather_than_reporting_no_drift() -> None:
    """A shallow clone cannot compare against bytes it does not have. Answering
    'no drift' there is a claim about a directory, not about an artifact."""
    name, entry, _ = _one_facility()
    version = GUARD.declared_version(REPO_ROOT / entry["package_dir"])
    with pytest.raises(GUARD.CannotAnswer) as caught:
        GUARD.drifted(
            facility_entries={name: entry},
            receipts=_receipt(version, "0" * 40, name),
        )
    assert "not reachable in this checkout" in str(caught.value)


def test_a_receipt_without_a_source_sha_refuses() -> None:
    name, entry, _ = _one_facility()
    version = GUARD.declared_version(REPO_ROOT / entry["package_dir"])
    receipts = [
        (
            REPO_ROOT / "docs/inventories/no-source.json",
            {
                "schema": "CandidateArtifact.v1",
                "facility": name,
                "version": version,
                "artifact_id": "0",
            },
        )
    ]
    with pytest.raises(GUARD.CannotAnswer, match="no source_sha"):
        GUARD.drifted(facility_entries={name: entry}, receipts=receipts)


def test_a_facility_whose_source_is_missing_refuses_rather_than_skipping() -> None:
    """Skipping would silently shrink the extent this guard claims to cover."""
    with pytest.raises(GUARD.CannotAnswer, match="is not a directory"):
        GUARD.drifted(
            facility_entries={"ghost": {"package_dir": "packages/does-not-exist"}},
            receipts=[],
        )


def test_a_facility_without_a_package_dir_refuses() -> None:
    with pytest.raises(GUARD.CannotAnswer, match="no package_dir"):
        GUARD.drifted(facility_entries={"ghost": {}}, receipts=[])
