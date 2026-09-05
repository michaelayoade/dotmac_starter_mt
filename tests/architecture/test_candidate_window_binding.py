"""The window between a candidate BUILD and its PUBLICATION, and the gate in it.

## The defect this replays

`dotmac-deployment-foundation 0.4.0a1` is the FOURTH recurrence of one shape,
and the first where every existing guard was not merely silent but correct:

* run ``33920058598`` built the candidate from ``753a004e`` on 2026-09-04 and
  uploaded the receipt as a workflow artifact;
* the receipt never reached ``docs/inventories/``;
* ``#628`` (``c427b9df``) and ``#631`` (``e843bed8``) then moved the facility's
  importable source while ``pyproject.toml`` still declared ``0.4.0a1``;
* `candidate_source_binding.py --check` reported ``0 drifted`` because it
  compares against receipts, and there was none. `version_binding_guard` ADMITTED
  ``0.4.0a1`` for a fresh candidate build, for the same reason.

So the fix could not be another reading of the repository. "Was a candidate
built?" is a claim about the BUILD SYSTEM (`AGENTS.md` rule 30), and the
`window` mode answers it from the workflow-run oracle.

## What this module proves, and why it never touches the network

The gate's three populations — facilities, receipts and BUILDS — are parameters,
and so is the ``revision`` it compares. That is what lets the sensitivity proof
be the real history rather than a story about it:

* **planted defect**: a build at ``753a004e`` against the tree at ``c427b9df``,
  the exact commit that first moved the source. It must fire, and name both trees.
* **negative control**: the same build against the tree at ``4895f179`` — ``#629``,
  which really did land on top of that candidate and really did not touch the
  facility's importable source. It must NOT report drift. A detector that
  refuses everything and a tree that has drifted are the same colour (ADR-0018).

Both revisions are fixed commits, so neither half decays when the live debt is
repaired — which is the failure mode `test_candidate_source_binding.py` already
records for its own proof.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts/candidate_source_binding.py"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"

#: The candidate build that is still live on `main`, by its immutable
#: coordinates. Read from the Actions API on 2026-09-05 and pinned here so the
#: proof needs no network: run 33920058598, artifact 9954731961, source
#: 753a004e. `candidate_window_baseline.json` carries the same three values,
#: and `test_the_pinned_build_is_the_one_the_baseline_froze` compares them.
LIVE_RUN_ID = "33920058598"
LIVE_ARTIFACT_ID = "9954731961"
LIVE_SOURCE_SHA = "753a004e7f8dbab034d5d6ca565c680d931a5309"

#: `#628` — the first commit to move the facility's importable source under the
#: unchanged `0.4.0a1` name. The planted defect is the tree AT this commit.
FIRST_DRIFTING_COMMIT = "c427b9df"

#: `#629` — landed on top of the same candidate and changed nothing importable
#: in the facility. The negative control is the tree AT this commit.
INNOCENT_COMMIT = "4895f179"


def _load_script():
    """Load the guard, REGISTERING it in `sys.modules` BEFORE executing it.

    The fourth occurrence of this dataclass/`slots=True` loader hazard, which
    `test_candidate_source_binding.py` predicted would produce a helper rather
    than a fourth paragraph. It imports that module's loader instead of copying
    it, which is the smallest honest version of that helper.
    """
    spec = importlib.util.spec_from_file_location("_candidate_source_binding", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GUARD = _load_script()
_git = GUARD.git


def _facility() -> tuple[str, dict[str, Any], str]:
    entries = GUARD.facilities()
    assert entries, (
        "no facility is registered in .github/release-facilities.json. A gate "
        "over an empty set passes for the wrong reason"
    )
    name = sorted(entries)[0]
    entry = entries[name]
    return name, entry, f"{str(entry['package_dir']).rstrip('/')}/src"


def _build(facility: str, version: str, source_sha: str) -> Any:
    return GUARD.CandidateBuild(
        facility=facility,
        version=version,
        source_sha=source_sha,
        run_id=LIVE_RUN_ID,
        artifact_id=LIVE_ARTIFACT_ID,
        run_url=f"https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/{LIVE_RUN_ID}",
    )


def _receipt(facility: str, version: str, source_sha: str) -> list:
    return [
        (
            REPO_ROOT / "docs/inventories/synthetic-candidate.json",
            {
                "schema": "CandidateArtifact.v1",
                "facility": facility,
                "version": version,
                "source_sha": source_sha,
                "artifact_id": LIVE_ARTIFACT_ID,
            },
        )
    ]


# ── the planted defect ──────────────────────────────────────────────────────


def test_the_planted_historical_defect_is_named() -> None:
    """THE SENSITIVITY PROOF. The candidate at 753a004e, and the tree at the
    commit that first moved out from under it. The gate must fire and must say
    which two sets of bytes now share one name."""
    name, entry, src_path = _facility()
    version = GUARD.declared_version_at(FIRST_DRIFTING_COMMIT, entry["package_dir"])
    build = _build(name, version, LIVE_SOURCE_SHA)

    found = GUARD.window_findings(
        builds=[build],
        facility_entries={name: entry},
        receipts=_receipt(name, version, LIVE_SOURCE_SHA),
        revision=FIRST_DRIFTING_COMMIT,
    )

    assert len(found) == 1, f"the planted defect was not detected: {found}"
    finding = found[0]
    assert "drifted" in finding.reasons
    assert finding.built_tree == _git("rev-parse", f"{LIVE_SOURCE_SHA}:{src_path}")
    assert finding.declared_tree == _git(
        "rev-parse", f"{FIRST_DRIFTING_COMMIT}:{src_path}"
    )
    assert finding.built_tree != finding.declared_tree
    rendered = str(finding)
    assert name in rendered
    assert LIVE_RUN_ID in rendered
    assert LIVE_SOURCE_SHA in rendered


def test_an_unrecorded_build_is_a_finding_on_its_own() -> None:
    """The half no tree comparison can reach. At `753a004e` the source had not
    moved yet — the defect at that instant was that nothing in the repository
    said a build had happened, which is why the other four checks stayed green
    for the whole window."""
    name, entry, _ = _facility()
    version = GUARD.declared_version_at(LIVE_SOURCE_SHA, entry["package_dir"])

    found = GUARD.window_findings(
        builds=[_build(name, version, LIVE_SOURCE_SHA)],
        facility_entries={name: entry},
        receipts=[],
        revision=LIVE_SOURCE_SHA,
    )

    assert [finding.reasons for finding in found] == [("unrecorded",)]


# ── the negative control ────────────────────────────────────────────────────


def test_a_change_that_does_not_touch_importable_source_passes() -> None:
    """THE NEGATIVE CONTROL, and it is not synthetic: `#629` really landed on
    top of this candidate and really left the facility's importable source
    alone. A gate that refused it would refuse everything, and a gate that
    refuses everything is indistinguishable from a tree that has drifted."""
    name, entry, src_path = _facility()
    assert _git("rev-parse", f"{INNOCENT_COMMIT}:{src_path}") == _git(
        "rev-parse", f"{LIVE_SOURCE_SHA}:{src_path}"
    ), (
        f"{INNOCENT_COMMIT} was chosen because it does not change {src_path}. "
        "If that is no longer true the control is not a control — pick another "
        "commit rather than deleting the test"
    )
    version = GUARD.declared_version_at(INNOCENT_COMMIT, entry["package_dir"])

    found = GUARD.window_findings(
        builds=[_build(name, version, LIVE_SOURCE_SHA)],
        facility_entries={name: entry},
        receipts=_receipt(name, version, LIVE_SOURCE_SHA),
        revision=INNOCENT_COMMIT,
    )

    assert found == [], f"the negative control was refused: {found}"


def test_a_build_under_a_version_this_tree_no_longer_declares_is_not_live() -> None:
    """The state the repair produces, asserted so it is never mistaken for a
    silenced detector. Allocating a new version moves the declared name off the
    built one; the window closes for the same reason it opened, and this holds
    OFFLINE — liveness is decided in the guard, not only in the query."""
    name, entry, _ = _facility()
    assert (
        GUARD.window_findings(
            builds=[
                _build(name, "0.0.0-a-name-this-tree-does-not-declare", LIVE_SOURCE_SHA)
            ],
            facility_entries={name: entry},
            receipts=[],
        )
        == []
    )


# ── refusing to answer is not passing ───────────────────────────────────────


def test_an_unreachable_build_commit_refuses_rather_than_reporting_no_window() -> None:
    name, entry, _ = _facility()
    with pytest.raises(GUARD.CannotAnswer, match="not reachable in this checkout"):
        GUARD.window_findings(
            builds=[
                _build(
                    name,
                    GUARD.declared_version(REPO_ROOT / entry["package_dir"]),
                    "0" * 40,
                )
            ],
            facility_entries={name: entry},
            receipts=[],
        )


def test_a_facility_whose_source_is_missing_refuses_rather_than_skipping() -> None:
    with pytest.raises(GUARD.CannotAnswer, match="is not a directory"):
        GUARD.window_findings(
            builds=[_build("ghost", "1.0", LIVE_SOURCE_SHA)],
            facility_entries={"ghost": {"package_dir": "packages/does-not-exist"}},
            receipts=[],
        )


def test_exit_status_separates_refusal_from_indeterminate(tmp_path: Path) -> None:
    """0 clean / 1 violation / 2 indeterminate, following
    `scripts/check_allocation_serialized.py`'s discipline. A refusal must never
    read as indeterminate and an indeterminate must never read as a pass, so all
    three are driven end to end through the real CLI against the real baseline."""
    name, entry, _ = _facility()
    version = GUARD.declared_version(REPO_ROOT / entry["package_dir"])

    def run(builds: list[dict[str, str]]) -> int:
        payload = tmp_path / "builds.json"
        payload.write_text(json.dumps(builds), encoding="utf-8")
        return subprocess.run(  # noqa: S603 # nosec B603
            [
                sys.executable,
                str(_SCRIPT),
                "--window",
                "--check",
                "--builds-json",
                str(payload),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).returncode

    live = {
        "facility": name,
        "version": version,
        "source_sha": LIVE_SOURCE_SHA,
        "run_id": LIVE_RUN_ID,
        "artifact_id": LIVE_ARTIFACT_ID,
        "run_url": (
            "https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/"
            + LIVE_RUN_ID
        ),
    }

    # 0 — the frozen debt, exactly as the baseline records it.
    assert run([live]) == GUARD.EXIT_OK
    # 1 — a REFUSAL. The set no longer matches the baseline; here because a row
    # vanished, which the two-directional ratchet must fail on just as loudly.
    assert run([]) == GUARD.EXIT_REFUSED
    # 2 — INDETERMINATE. A build whose source this checkout cannot resolve.
    assert run([{**live, "source_sha": "0" * 40}]) == GUARD.EXIT_CANNOT_ANSWER


def test_builds_json_without_window_is_indeterminate_not_a_pass() -> None:
    result = subprocess.run(  # noqa: S603 # nosec B603
        [sys.executable, str(_SCRIPT), "--check", "--builds-json", "/dev/null"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == GUARD.EXIT_CANNOT_ANSWER


# ── the gate does not choose a release identity ─────────────────────────────


def test_the_refusal_names_the_binding_and_allocates_nothing() -> None:
    """`candidate_source_binding.py` already declines to pick a successor, on the
    grounds that a guard choosing one would be issuing a release identity as a
    side effect of a check. `window` runs on every pull request, so the property
    matters more here, not less: a version emitted by a PR check would exist
    because a script printed it."""
    name, entry, _ = _facility()
    version = GUARD.declared_version_at(FIRST_DRIFTING_COMMIT, entry["package_dir"])
    finding = GUARD.window_findings(
        builds=[_build(name, version, LIVE_SOURCE_SHA)],
        facility_entries={name: entry},
        receipts=_receipt(name, version, LIVE_SOURCE_SHA),
        revision=FIRST_DRIFTING_COMMIT,
    )[0]

    rendered = str(finding)
    assert "Allocate a NEW version" in rendered
    assert "does not choose" in rendered
    # No successor is named. The declared version appears (it is the binding);
    # nothing that looks like a NEXT one does.
    for successor in ("0.4.0a2", "0.4.0a3", "0.4.0", "0.5.0"):
        assert successor not in rendered, (
            f"the refusal proposes {successor}. A check that issues a release "
            "identity has decided one"
        )
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "would be choosing a release identity as" in source


# ── the baseline, and the extent it claims ──────────────────────────────────


def test_the_recorded_total_still_describes_the_rows() -> None:
    document = json.loads(GUARD.WINDOW_BASELINE_PATH.read_text(encoding="utf-8"))
    assert document["total"] == len(document["rows"])


def test_the_pinned_build_is_the_one_the_baseline_froze() -> None:
    """The proof's coordinates and the frozen debt's are the same three facts.
    Without this the module could drift into proving a gate against a build the
    repository no longer has an open window for."""
    document = json.loads(GUARD.WINDOW_BASELINE_PATH.read_text(encoding="utf-8"))
    rows = [row for row in document["rows"] if row["run_id"] == LIVE_RUN_ID]
    assert len(rows) == 1, (
        "the 0.4.0a1 window is no longer in the baseline. If it was REPAIRED, "
        "re-point LIVE_RUN_ID/LIVE_ARTIFACT_ID/LIVE_SOURCE_SHA at the commit "
        "history this proof replays — they are historical facts and stay valid "
        "— and drop this assertion, which is about the live debt rather than "
        "about the detector"
    )
    assert rows[0]["artifact_id"] == LIVE_ARTIFACT_ID
    assert rows[0]["source_sha"] == LIVE_SOURCE_SHA
    assert rows[0]["reasons"] == ["unrecorded", "drifted"]


def test_the_candidate_lane_is_the_workflow_the_oracle_reads() -> None:
    """The oracle names one workflow. If that lane is renamed and this constant
    is not, the gate reports 'no candidate has ever been built' — the exact
    silence it exists to end — so the name is checked against the file and
    against the artifact it uploads."""
    lane = REPO_ROOT / ".github/workflows" / GUARD.CANDIDATE_WORKFLOW
    assert lane.is_file(), f"{GUARD.CANDIDATE_WORKFLOW} does not exist"
    name, _entry, _ = _facility()
    assert f"{name}-candidate" in lane.read_text(encoding="utf-8"), (
        f"{GUARD.CANDIDATE_WORKFLOW} no longer uploads an artifact named "
        f"{name}-candidate, which is how a run is attributed to a facility"
    )


# ── where it runs ───────────────────────────────────────────────────────────


def test_ci_runs_the_window_gate_with_the_evidence_it_needs() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["candidate-window"]

    assert job["permissions"]["actions"] == "read", (
        "the runs and artifacts endpoints ARE the oracle; without `actions: "
        "read` the guard exits 2 on every run"
    )
    checkout = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["fetch-depth"] == 0, (
        "the comparison needs the src tree at a past build commit; a shallow "
        "checkout can only produce a refusal to answer"
    )
    gate = next(
        step
        for step in job["steps"]
        if "candidate_source_binding" in str(step.get("run", ""))
    )
    assert "--window" in gate["run"] and "--check" in gate["run"]
    assert "--builds-json" not in gate["run"], (
        "CI must read the build system, not a file in the repository. "
        "`--builds-json` exists for the offline sensitivity proof"
    )
    assert gate["env"]["GH_TOKEN"]


def test_the_window_gate_is_not_in_the_offline_quality_matrix() -> None:
    """`make check` must stay runnable offline and the matrix must equal its
    prerequisites exactly (`test_ci_runs_canonical_check.py`). This gate needs
    full history and the Actions API, so it is its own job — the same split
    `allocation-gate` already makes."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["quality"]["strategy"]["matrix"]["target"]
    assert "candidate-window-check" not in matrix
    check_line = next(
        line
        for line in (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
        if line.startswith("check:")
    )
    assert "candidate-window-check" not in check_line
