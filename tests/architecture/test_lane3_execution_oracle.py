"""The amended publication oracle: one pinned organization workflow, or nothing.

ADR-0070's 2026-10-05 amendment moves Lane 3 to an organization-owned
execution repository. `scripts/lane3_execution.py` holds what the oracle reads
from it, and every refusal below is planted, because a gate nobody has watched
refuse is a gate nobody should trust (ADR-0018).

The first test is the one that states today's state: the checked-in topology
records no admitted surface, so the oracle refuses before any network call.
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
from typing import Any

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from lane3_execution import (  # noqa: E402
    GRAMMAR_GUARD_PROVEN,
    ExecutionRunRefused,
    TopologyRefused,
    _slug,
    load_topology,
    parse_run_name,
    parse_topology,
    require_run_context,
    select_runs,
)
from require_rehearsal import RehearsalMissing, decide  # noqa: E402

from tests.unit.lane3_evidence_support import (  # noqa: E402
    EVIDENCE_PATH,
    REFERENCE,
    REQUIREMENTS,
    digest,
    rows,
    table,
    write_evidence,
)

SHA = "a" * 40
OTHER = "b" * 40
LAUNCHER = "c" * 40
REPO_ID = 1397614141
GROUP_ID = 4
ENV_ID = 23103080999

PROVISIONED: dict[str, Any] = {
    "schema": "Lane3ExecutionTopology.v1",
    "starter_repository": "michaelayoade/dotmac_starter_mt",
    "execution_repository": "dotmac-tech/lane3-exposure-execution",
    "execution_repository_id": REPO_ID,
    "execution_owner_id": 335992433,
    "workflow_path": ".github/workflows/lane3-exposure-rehearsal.yml",
    "environment": {
        "name": "lane3-rehearsal-protected",
        "id": ENV_ID,
        "reviewer": "michaelayoade",
    },
    "runner_group": {"name": "lane3-exposure-protected", "id": GROUP_ID},
    "admitted_launcher_revisions": [LAUNCHER],
    "receipt_artifact": "lane3-rehearsal-receipt",
    "admission_evidence": REFERENCE,
}


@pytest.fixture()
def evidence_root(tmp_path: pathlib.Path) -> pathlib.Path:
    write_evidence(tmp_path)
    return tmp_path


_TMP = tempfile.TemporaryDirectory(prefix="lane3-evidence-")  # removed at exit
_SHARED_ROOT = pathlib.Path(_TMP.name)
write_evidence(_SHARED_ROOT)


def _topology(root: pathlib.Path | None = None, **over: Any):  # type: ignore[no-untyped-def]
    document = copy.deepcopy(PROVISIONED)
    document.update(over)
    return parse_topology(document, repo_root=root or _SHARED_ROOT)


def _run(**over: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": 10,
        "run_attempt": 1,
        "repository": {"id": REPO_ID},
        "path": PROVISIONED["workflow_path"],
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": LAUNCHER,
        "display_title": f"lane3 starter={SHA} candidate=0.4.0a2",
        "status": "completed",
        "conclusion": "success",
        "run_started_at": "2026-10-05T10:00:00Z",
        "html_url": "https://example.invalid/run/10",
    }
    run.update(over)
    return run


# ── today: the checked-in topology refuses ─────────────────────────────────


def test_the_checked_in_topology_is_not_an_admitted_surface() -> None:
    """The designed state until § 7's admission evidence exists."""
    with pytest.raises(TopologyRefused):
        load_topology(REPO / ".github" / "lane3-execution.json")


def test_a_fully_provisioned_topology_parses(evidence_root: pathlib.Path) -> None:
    topology = _topology(evidence_root)
    assert topology.execution_repository_id == REPO_ID
    assert topology.admitted_launcher_revisions == frozenset({LAUNCHER})


@pytest.mark.parametrize(
    "path",
    [
        "execution_repository_id",
        "execution_owner_id",
        "environment.id",
        "runner_group.id",
    ],
)
@pytest.mark.parametrize("value", [None, 0, -1, True, "1397614141"])
def test_every_immutable_id_must_be_recorded(
    path: str, value: object, evidence_root: pathlib.Path
) -> None:
    document = copy.deepcopy(PROVISIONED)
    node = document
    *parents, leaf = path.split(".")
    for part in parents:
        node = node[part]
    node[leaf] = value
    with pytest.raises(TopologyRefused, match=path):
        parse_topology(document, repo_root=evidence_root)


@pytest.mark.parametrize("revisions", [[], ["C" * 40], ["c" * 39], [None], "c" * 40])
def test_no_launcher_is_admitted_without_a_full_commit(revisions: object) -> None:
    with pytest.raises(TopologyRefused, match="admitted_launcher_revisions"):
        _topology(admitted_launcher_revisions=revisions)


@pytest.mark.parametrize("evidence", [None, "", "   "])
def test_no_surface_is_admitted_without_admission_evidence(evidence: object) -> None:
    with pytest.raises(TopologyRefused, match="admission_evidence"):
        _topology(admission_evidence=evidence)


# ── admission_evidence is a reference to a complete § 7 table ─────────────


def _refuses(root: pathlib.Path, match: str, reference: str = REFERENCE) -> None:
    """The guard itself refused: a TopologyRefused naming the evidence defect."""
    with pytest.raises(TopologyRefused, match=match) as caught:
        _topology(root, admission_evidence=reference)
    assert "admission_evidence" in str(caught.value)


def test_a_complete_evidence_table_admits(evidence_root: pathlib.Path) -> None:
    assert _topology(evidence_root).admission_evidence == REFERENCE


@pytest.mark.parametrize(
    "reference",
    [
        "TBD",
        "docs/LANE3_EXECUTION_TOPOLOGY.md#7 run 1",
        "docs/LANE3_EXECUTION_TOPOLOGY.md#7-admission-evidence",
        f"{EVIDENCE_PATH}",
        f"{EVIDENCE_PATH}#",
        f"{EVIDENCE_PATH}#Fixture Run 1",
        f"../{EVIDENCE_PATH}#fixture-run-1",
        "docs/other.md#fixture-run-1",
    ],
)
def test_an_arbitrary_or_misdirected_reference_is_refused(
    evidence_root: pathlib.Path, reference: str
) -> None:
    _refuses(evidence_root, "not a reference of the form", reference)


def test_a_reference_to_a_missing_file_is_refused(tmp_path: pathlib.Path) -> None:
    _refuses(tmp_path, "cannot be read")


def test_a_missing_anchor_is_refused(evidence_root: pathlib.Path) -> None:
    _refuses(evidence_root, "matches 0 headings", f"{EVIDENCE_PATH}#no-such-run")


def test_a_duplicate_anchor_is_refused(tmp_path: pathlib.Path) -> None:
    write_evidence(tmp_path, table(rows()) + table(rows()))
    _refuses(tmp_path, "matches 2 headings")


@pytest.mark.parametrize("missing", REQUIREMENTS)
def test_a_missing_requirement_row_is_refused(
    tmp_path: pathlib.Path, missing: str
) -> None:
    kept = {k: v for k, v in rows().items() if k != missing}
    write_evidence(tmp_path, table(kept))
    _refuses(tmp_path, f"no row for: {missing}")


def test_an_unknown_or_repeated_requirement_row_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    extra = {**rows(), "Something else": ("run:9000100", digest("x"))}
    write_evidence(tmp_path, table(extra))
    _refuses(tmp_path, "not a § 7 requirement")
    text = table(rows()) + f"| PR exclusion | `run:9000200` | `{digest('y')}` |\n"
    write_evidence(tmp_path, text)
    _refuses(tmp_path, "repeats")


@pytest.mark.parametrize(
    "coordinate",
    [
        "",
        "TBD",
        "run:0",
        "run:123",
        "run:111111",
        "123456",
        "run:<id>",
        "audit:x",
        "audit:aaaaaaaa",
        "ci:9000001",
        "run:9000001 extra",
    ],
)
def test_a_placeholder_or_untyped_coordinate_is_refused(
    tmp_path: pathlib.Path, coordinate: str
) -> None:
    broken = {**rows(), "Credential failure": (coordinate, digest("c"))}
    write_evidence(tmp_path, table(broken))
    _refuses(tmp_path, "Credential failure")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "0" * 64,
        "a" * 64,
        digest("z")[:63],
        digest("z") + "0",
        digest("z").upper(),
        "sha256:" + digest("z"),
    ],
)
def test_a_bad_digest_is_refused(tmp_path: pathlib.Path, bad: str) -> None:
    broken = {**rows(), "No-grant refusal": ("audit:openbao-audit-77", bad)}
    write_evidence(tmp_path, table(broken))
    _refuses(tmp_path, "No-grant refusal.*sha256")


@pytest.mark.parametrize("word", ["pending", "TBD", "UNKNOWN", "N/A", "n/a"])
def test_a_row_marked_pending_is_refused_even_with_valid_looking_cells(
    tmp_path: pathlib.Path, word: str
) -> None:
    broken = {**rows(), "PR exclusion": (f"run:9000555 {word}", digest("p"))}
    write_evidence(tmp_path, table(broken))
    _refuses(tmp_path, "PR exclusion.*pending, unknown or a placeholder")


def test_the_same_digest_or_coordinate_on_two_rows_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    same_digest = {
        **rows(),
        "PR exclusion": (rows()["PR exclusion"][0], digest("Public controls")),
    }
    write_evidence(tmp_path, table(same_digest))
    _refuses(tmp_path, "reuses one digest")
    same_coordinate = {
        **rows(),
        "Oracle reachability": (rows()["PR exclusion"][0], digest("o")),
    }
    write_evidence(tmp_path, table(same_coordinate))
    _refuses(tmp_path, "reuses one coordinate")


@pytest.mark.parametrize(
    ("row", "wrong"),
    [
        ("No-grant refusal", "run:9000999"),
        ("No-grant refusal", "scan:fixture-record-9"),
        ("Credential failure", "run:9000999"),
        ("Credential failure", "readback:fixture-record-9"),
        ("Isolation and disclosure", "audit:fixture-record-9"),
        ("Isolation and disclosure", "run:9000999"),
        ("Authorized scheduling", "audit:fixture-record-9"),
        ("Authorized scheduling", "readback:fixture-record-9"),
        ("Public controls", "run:9000999"),
        ("Public controls", "audit:fixture-record-9"),
    ],
)
def test_a_coordinate_kind_the_row_does_not_allow_is_refused(
    tmp_path: pathlib.Path, row: str, wrong: str
) -> None:
    write_evidence(tmp_path, table({**rows(), row: (wrong, digest("w"))}))
    _refuses(tmp_path, f"row {row!r} must use a")


def test_a_hidden_table_is_refused(tmp_path: pathlib.Path) -> None:
    body = table(rows())
    write_evidence(
        tmp_path, body.replace("| Requirement", "<!--\n| Requirement", 1) + "-->\n"
    )
    _refuses(tmp_path, "comment or code fence")
    for fence in ("```", "~~~"):
        write_evidence(
            tmp_path, body.replace("| Requirement", f"{fence}\n| Requirement", 1)
        )
        _refuses(tmp_path, "comment or code fence")


@pytest.mark.parametrize(
    "separator", ["| | | |", "|-|", "| -- | -- | -- |", "| --- | --- |"]
)
def test_a_weak_separator_row_is_refused(
    tmp_path: pathlib.Path, separator: str
) -> None:
    write_evidence(tmp_path, table(rows()).replace("| --- | --- | --- |", separator))
    _refuses(tmp_path, "no separator row")


def test_a_symlinked_evidence_path_or_topology_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    real = tmp_path / "real"
    write_evidence(real)
    linked = tmp_path / "linked"
    (linked / "docs").mkdir(parents=True)
    (linked / "docs" / "inventories").symlink_to(real / "docs" / "inventories")
    _refuses(linked, "symbolic link")
    from tests.unit.lane3_evidence_support import write_topology

    good = pathlib.Path(write_topology(real, PROVISIONED))
    other = tmp_path / "other" / ".github"
    other.mkdir(parents=True)
    (other / "lane3-execution.json").symlink_to(good)
    with pytest.raises(TopologyRefused, match="symbolic link"):
        load_topology(other / "lane3-execution.json")


def test_a_table_without_the_columns_is_refused(tmp_path: pathlib.Path) -> None:
    write_evidence(tmp_path, "## Fixture run 1\n\nno table here\n")
    _refuses(tmp_path, "no table with the columns")


def test_load_topology_resolves_evidence_beside_the_topology_file(
    tmp_path: pathlib.Path,
) -> None:
    from tests.unit.lane3_evidence_support import write_topology

    good = write_topology(tmp_path, PROVISIONED)
    assert load_topology(pathlib.Path(good)).admission_evidence == REFERENCE
    (tmp_path / EVIDENCE_PATH).write_text(table({}), encoding="utf-8")
    with pytest.raises(TopologyRefused, match="admission_evidence.*no row for"):
        load_topology(pathlib.Path(good))
    stray = tmp_path / "elsewhere.json"
    stray.write_text("{}", encoding="utf-8")
    with pytest.raises(TopologyRefused, match="not directly under a .github"):
        load_topology(stray)


def test_the_shipped_evidence_file_admits_nothing() -> None:
    """Every anchor in the shipped file, template included, is refused by the guard."""
    text = (REPO / EVIDENCE_PATH).read_text(encoding="utf-8")
    anchors = [
        _slug(line.lstrip("#")) for line in text.splitlines() if line.startswith("#")
    ]
    assert any("template" in a for a in anchors)
    for anchor in anchors:
        with pytest.raises(TopologyRefused, match="admission_evidence #"):
            parse_topology(
                {**PROVISIONED, "admission_evidence": f"{EVIDENCE_PATH}#{anchor}"},
                repo_root=REPO,
            )


def test_the_checked_in_topology_keeps_admission_evidence_null() -> None:
    document = json.loads((REPO / ".github" / "lane3-execution.json").read_text())
    assert document["admission_evidence"] is None


# ── the dispatch grammar ───────────────────────────────────────────────────


def test_the_grammar_parses_exactly_the_launcher_title() -> None:
    assert parse_run_name(f"lane3 starter={SHA} candidate=0.4.0a2") == (SHA, "0.4.0a2")


@pytest.mark.parametrize(
    "title",
    [
        f"lane3 starter={SHA.upper()} candidate=0.4.0a2",
        f"lane3 starter={SHA[:39]} candidate=0.4.0a2",
        f"lane3 starter={SHA} candidate=0.4.0a2 extra",
        f"Lane3 starter={SHA} candidate=0.4.0a2",
        f"lane3 starter={SHA} candidate=latest",
        None,
        42,
    ],
)
def test_the_grammar_refuses_anything_else(title: object) -> None:
    assert parse_run_name(title) is None


# ── selection: the population must be the pinned workflow ─────────────────


@pytest.mark.parametrize(
    "over",
    [
        {"repository": {"id": 1}},
        {"repository": None},
        {"path": ".github/workflows/other.yml"},
        {"event": "push"},
        {"event": "pull_request_target"},
        {"head_branch": "feature"},
    ],
)
def test_a_run_outside_the_pinned_workflow_refuses_the_population(
    over: dict[str, Any],
) -> None:
    with pytest.raises(ExecutionRunRefused, match="not the pinned workflow"):
        select_runs([_run(), _run(id=11, **over)], sha=SHA, topology=_topology())


def test_an_unparseable_title_refuses_until_the_grammar_guard_is_proven() -> None:
    assert GRAMMAR_GUARD_PROVEN is False
    with pytest.raises(ExecutionRunRefused, match="does not parse"):
        select_runs(
            [_run(), _run(id=11, display_title="lane3 by hand")],
            sha=SHA,
            topology=_topology(),
        )


def test_selection_projects_the_starter_revision_and_keeps_the_launcher() -> None:
    kept = select_runs(
        [_run(), _run(id=11, display_title=f"lane3 starter={OTHER} candidate=0.4.0a2")],
        sha=SHA,
        topology=_topology(),
    )
    assert [run["id"] for run in kept] == [10]
    assert kept[0]["head_sha"] == SHA
    assert kept[0]["launcher_sha"] == LAUNCHER


def test_an_older_success_does_not_mask_a_newer_failure_across_the_projection() -> None:
    """`decide`'s newest-then-check semantics survive the projection unchanged."""
    runs = [
        _run(id=10, run_started_at="2026-10-05T10:00:00Z"),
        _run(id=11, run_started_at="2026-10-05T11:00:00Z", conclusion="failure"),
    ]
    with pytest.raises(RehearsalMissing, match="not completed/success"):
        decide(select_runs(runs, sha=SHA, topology=_topology()), SHA)


def test_a_rehearsal_of_another_revision_is_not_a_rehearsal_of_this_one() -> None:
    runs = [_run(display_title=f"lane3 starter={OTHER} candidate=0.4.0a2")]
    with pytest.raises(RehearsalMissing, match="no rehearsal run exists"):
        decide(select_runs(runs, sha=SHA, topology=_topology()), SHA)


# ── the selected run's context ─────────────────────────────────────────────


def _selected() -> dict[str, Any]:
    return select_runs([_run()], sha=SHA, topology=_topology())[0]


GOOD_JOBS = [{"id": 1, "runner_group_id": GROUP_ID}]
GOOD_APPROVALS = [
    {
        "state": "approved",
        "user": {"login": "michaelayoade"},
        "environments": [{"id": ENV_ID}],
    }
]


def test_an_admitted_run_on_the_group_with_its_approval_is_accepted() -> None:
    require_run_context(
        _selected(), jobs=GOOD_JOBS, approvals=GOOD_APPROVALS, topology=_topology()
    )


def test_a_launcher_revision_that_was_not_admitted_is_refused() -> None:
    run = {**_selected(), "launcher_sha": "d" * 40}
    with pytest.raises(ExecutionRunRefused, match="not an admitted launcher"):
        require_run_context(
            run, jobs=GOOD_JOBS, approvals=GOOD_APPROVALS, topology=_topology()
        )


@pytest.mark.parametrize(
    "jobs",
    [
        [],
        [{"id": 1, "runner_group_id": 1}],
        [{"id": 1, "runner_group_id": GROUP_ID}, {"id": 2, "runner_group_id": None}],
    ],
)
def test_a_job_off_the_pinned_runner_group_is_refused(
    jobs: list[dict[str, Any]],
) -> None:
    with pytest.raises(ExecutionRunRefused, match="runner group"):
        require_run_context(
            _selected(), jobs=jobs, approvals=GOOD_APPROVALS, topology=_topology()
        )


@pytest.mark.parametrize(
    "approvals",
    [
        [],
        [{**GOOD_APPROVALS[0], "state": "rejected"}],
        [{**GOOD_APPROVALS[0], "user": {"login": "someone-else"}}],
        [{**GOOD_APPROVALS[0], "environments": [{"id": 1}]}],
        [{**GOOD_APPROVALS[0], "user": None}],
    ],
)
def test_an_unapproved_run_is_refused(approvals: list[dict[str, Any]]) -> None:
    with pytest.raises(ExecutionRunRefused, match="no approval"):
        require_run_context(
            _selected(), jobs=GOOD_JOBS, approvals=approvals, topology=_topology()
        )


# ── the release step no longer lists or downloads runs itself ─────────────


def test_the_release_step_does_not_select_or_download_a_run_separately() -> None:
    """A separate `gh run download` could read a different run from the one the
    oracle selected; the oracle reads the receipt from its own selection."""
    text = (REPO / ".github" / "workflows" / "release-facility.yml").read_text()
    assert "gh run download" not in text
    assert "gh run list --workflow exposure-rehearsal.yml" not in text
    assert "--receipt-out .rehearsal/receipt.json" in text
