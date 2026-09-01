"""The released-migrations gate must know what it is not checking.

`test_released_migrations.py` freezes the bytes of every migration that shipped
in a published tag, and it is a strong guard — over a HAND-MAINTAINED list of
what to check. That makes the list itself the blind spot: a distribution absent
from `DISTRIBUTIONS` is not reported as unprotected, it is never looked at, and
absence of a finding reads exactly like absence of a problem.

`dotmac-numbering` 0.1.0a2 is the proof. A released migration was edited in
place and every gate stayed green, because the gate was never pointed at the
file. The edit was caught by a byte comparison against release tags, not by the
guard whose whole subject is released migration bytes.

A gate over a hand-maintained list is a gate whose hole grows with the system:
every new stateful module is uncovered by default, silently, and nothing says
so. This test makes the hole a number that can only be reduced deliberately.

## Two directions, because either alone rots

* A newly RELEASED lineage that nobody enrolled fails. That is the growth case:
  a module ships, its migrations enter a wheel, and the gate does not know.
* A newly COVERED lineage also fails, until the baseline is regenerated. That is
  the bookkeeping case: a silently shrinking debt list stops describing reality,
  and a list nobody maintains is where defects go to be forgotten.

The baseline is a record of what is NOT protected. Rows leave it by enrolling
the distribution in `test_released_migrations.py` — never by deleting the row.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).parent / "released_lineage_coverage_baseline.json"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_released_lineage_coverage", REPO_ROOT / "scripts/released_lineage_coverage.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_coverage = _load()


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text())


def _require_tags() -> None:
    if not _coverage.tags():
        pytest.fail(
            "this checkout has no git tags, so no lineage can be classified as "
            "released. Fetch tags rather than letting this check pass vacuously"
        )


def test_no_newly_released_lineage_escapes_the_gate():
    """The growth case: a module ships and nobody enrolled it."""

    _require_tags()
    known = set(_baseline()["uncovered"])
    current = set(_coverage.uncovered())
    new = sorted(current - known)
    assert not new, (
        f"these distributions now have RELEASED migration lineages that "
        f"test_released_migrations.py does not check: {new}. Their bytes are in "
        "a published wheel and have run in databases this repository cannot "
        "inspect, with nothing freezing them. Enrol each in DISTRIBUTIONS, "
        "LINEAGE_GLOBS, TAG_PREFIXES, RELEASED_TAGS and UNRELEASED."
    )


def test_newly_covered_lineages_are_removed_from_the_baseline():
    """The bookkeeping case: closing the hole must be a visible edit."""

    _require_tags()
    known = set(_baseline()["uncovered"])
    current = set(_coverage.uncovered())
    closed = sorted(known - current)
    assert not closed, (
        f"{closed} are now covered by the released-migrations gate. Regenerate "
        "the baseline in this same change: make released-lineage-coverage-baseline"
    )


def test_the_recorded_totals_still_describe_this_tree():
    _require_tags()
    baseline = _baseline()
    assert baseline["uncovered_total"] == len(baseline["uncovered"])
    assert len(_coverage.released_lineages()) == baseline["released_lineages_total"], (
        "the number of released lineages changed; regenerate the baseline so "
        "its counts still describe this tree"
    )


def test_numbering_is_covered_now():
    """The distribution whose released migration was edited must be enrolled.

    Named explicitly rather than left to the counts: this is the defect the
    whole file exists because of, and a regression that silently dropped it back
    out would otherwise only move a number.
    """

    _require_tags()
    assert "dotmac-numbering" in _coverage.covered(), (
        "dotmac-numbering must stay enrolled in test_released_migrations.py: a "
        "released migration of its was edited in place, and dotmac_erp pins a2 "
        "AND composes this lineage"
    )
    assert "dotmac-numbering" not in _coverage.uncovered()


def test_the_coverage_detector_would_actually_fire(monkeypatch):
    """Sensitivity: the matcher must be able to report a distribution uncovered.

    Every assertion above passes trivially if `uncovered()` can only ever
    return an empty set.

    This used to assert `uncovered()` was non-empty. That worked while the
    baseline held 43 rows and stops proving anything the moment the debt is
    paid off — a sensitivity check that only holds while the defect exists
    reports "detector healthy" and "no debt remaining" with the same green
    tick, and cannot tell you which one it meant. Worse, it inverts: the change
    that finally closes the last hole is the change that turns this test red,
    so the reward for finishing the work is a failing suite and the obvious fix
    is to delete the proof.

    It is now proved by making a genuinely covered distribution look
    unenrolled and requiring the REAL `uncovered()` to name it. That is the
    property the baseline depends on — not "the debt list is non-empty" but
    "an unenrolled released lineage is reported" — and it holds at 43 rows, at
    two, and at zero, because the existing debt is subtracted rather than
    assumed present.

    `dotmac-numbering` is the probe on purpose. It is the distribution whose
    released migration was edited in place, `test_numbering_is_covered_now`
    already refuses to let it leave the gate, and a probe that stopped existing
    would turn this proof vacuous — so the two tests fail together rather than
    one silently covering for the other.
    """

    _require_tags()
    released = set(_coverage.released_lineages())
    covered = _coverage.covered()
    assert released, "no lineage classified as released; the matcher is broken"
    assert covered, "the gate's DISTRIBUTIONS map parsed as empty"

    probe = "dotmac-numbering"
    assert probe in released and probe in covered, (
        f"{probe} is no longer a covered released lineage, so it cannot serve "
        "as the probe; see test_numbering_is_covered_now"
    )

    known_debt = set(_coverage.uncovered())
    assert (
        probe not in known_debt
    ), f"{probe} is already reported uncovered, so unenrolling it would prove nothing"

    monkeypatch.setattr(_coverage, "covered", lambda: covered - {probe})
    assert set(_coverage.uncovered()) == known_debt | {probe}, (
        "with a released lineage removed from the gate's DISTRIBUTIONS map, "
        "`uncovered()` does not report it — the detector can no longer see an "
        "unenrolled lineage, whatever the baseline currently says"
    )
