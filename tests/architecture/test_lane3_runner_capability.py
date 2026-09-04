"""`Lane3RunnerCapability.v1` must be unable to claim a rehearsal ran.

The record gates a candidate build. `foundation-candidate.yml` deliberately does
NOT gate on a passing rehearsal — that is what breaks the bootstrap loop — so a
capability record that could be satisfied by a passing run would reintroduce the
loop from the other side.

Two families of check, and they fail for different reasons:

**Structural.** The module cannot be handed a rehearsal outcome, its vocabulary
cannot spell one, and it cannot reach the receipt's vocabulary. Enforced by an
import allowlist ratcheted in both directions, by the `Verdict` member set, and
by a refusal of any schema field named for an outcome.

**Behavioural.** Every reason is planted separately: repairing exactly one flips
exactly one, and a source with none of them reaches `capable`. The second
direction is the one that would otherwise be skipped — without it, the gate has
only ever been observed at the value it was written to return.
"""

from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import lane3_runner_capability as capability  # noqa: E402

MODULE = ROOT / "scripts" / "lane3_runner_capability.py"

#: Everything the module may import. Every entry is incapable of receiving a
#: rehearsal outcome: no network, no subprocess, no filesystem beyond reading
#: declared paths, and — the load-bearing one — nothing from
#: `dotmac_deployment_foundation`, whose `rehearsal` module holds the six-member
#: status vocabulary this record must not be able to say. RATCHETED: an addition
#: widens what the record can reach, and a removal that is still imported fails
#: just as loudly.
IMPORT_ALLOWLIST = frozenset(
    {
        "__future__",
        "argparse",
        "ast",
        "json",
        "re",
        "sys",
        "dataclasses",
        "enum",
        "pathlib",
    }
)

#: Words a capability verdict must not be able to say. `executed_passed`,
#: `executed_failed` and `not_executed` are the receipt's, and the whole point
#: is that this record cannot reach them.
OUTCOME_WORDS = (
    "pass",
    "fail",
    "ran",
    "run",
    "execut",
    "green",
    "receipt",
    "conclusion",
)


# ── structural: it cannot be handed a result ────────────────────────────────


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add("." * node.level + (node.module or ""))
    return found


def test_the_record_cannot_reach_anything_that_could_hand_it_a_result() -> None:
    """The allowlist, both directions."""
    imported = _imports(ast.parse(MODULE.read_text(encoding="utf-8")))
    assert imported - IMPORT_ALLOWLIST == set(), (
        "the capability record imported something outside the allowlist; it may "
        "read source files and nothing else"
    )
    assert IMPORT_ALLOWLIST - imported == set(), (
        "an allowlisted import is no longer used — lower the allowlist "
        "deliberately rather than leaving it describing reach the module does "
        "not have"
    )


def test_the_receipt_vocabulary_is_not_importable_from_here() -> None:
    """The standing instruction, made mechanical.

    The tempting future edit is to unify this enum with the receipt's — one
    status vocabulary for the lane, obviously tidier. That hands this record the
    words `executed_passed` and `executed_failed`, and from that moment a
    capability verdict and a rehearsal outcome are the same type. The
    duplication IS the guard.
    """
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # IMPORTS, not source text. The module docstring names
    # `dotmac_deployment_foundation.rehearsal.RequirementStatus` on purpose —
    # it is where the standing instruction lives — and a text scan would refuse
    # the paragraph explaining why the import is absent. A guard that refuses
    # its own rationale is one somebody deletes rather than obeys.
    for module in _imports(tree):
        assert "dotmac_deployment_foundation" not in module, module
        assert "rehearsal" not in module, module

    # And the rationale must actually be present, or the ratchet above is
    # protecting a decision nobody recorded.
    docstring = ast.get_docstring(tree) or ""
    assert "RequirementStatus" in docstring
    assert "duplication" in docstring.lower()


def test_the_vocabulary_cannot_spell_an_outcome() -> None:
    """Two verdicts, and neither is a claim about a run."""
    assert {v.value for v in capability.Verdict} == {"capable", "not_capable"}
    for verdict in capability.Verdict:
        assert not any(
            word in verdict.value for word in ("pass", "fail", "ran")
        ), verdict


def test_no_field_of_the_record_is_named_for_an_outcome() -> None:
    """A record with a `run_id` or a `conclusion` field is one an author will
    eventually populate."""
    record = capability.assess(ROOT)
    keys = set(record) | {
        key
        for entry in record["reasons"]
        for key in entry  # type: ignore[union-attr]
    }
    for key in keys:
        assert not any(word in key.lower() for word in OUTCOME_WORDS), key


def test_the_command_line_offers_no_way_to_supply_a_result() -> None:
    """Point 1 of the design, checked on the real parser rather than the prose.

    A flag added tomorrow in a module nobody thought to re-read is exactly the
    one that would let a caller pass `--rehearsal-run` and have the verdict
    depend on it.
    """
    with pytest.raises(SystemExit):
        capability.main(["--help"])
    parser_source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(parser_source)
    options = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert options == {"--root", "--out", "--summary"}, options


# ── behavioural: today's main, and every reason planted ─────────────────────


def test_this_tree_is_capable_and_that_is_the_gate_on_the_build() -> None:
    """The verdict `foundation-candidate.yml` refuses to build without.

    All five reasons are repaired, so no live reason remains for a detector to
    demonstrate itself against. That is the good outcome and also the dangerous
    one: from here EVERY detector's only proof is its regression plant, because
    a scanner that had quietly stopped looking would produce this same verdict.

    So the plants below are not belt-and-braces any more — they are the whole
    evidence that this `capable` means anything.
    """
    record = capability.assess(ROOT)
    assert record["verdict"] == "capable", record["reasons"]
    assert record["reasons"] == []


def _tree_with(tmp_path: Path, repairs: dict[Path, list[tuple[str, str]]]) -> Path:
    """A copy of the real sources with targeted edits applied."""
    root = tmp_path / "tree"
    for relative in (capability.RUNNER, capability.COLLECTOR):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    for relative, edits in repairs.items():
        path = root / relative
        text = path.read_text(encoding="utf-8")
        for old, new in edits:
            assert old in text, f"anchor missing in {relative}: {old[:60]!r}"
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
    return root


def _reasons(root: Path) -> set[str]:
    return {
        entry["reason"]
        for entry in capability.assess(root)["reasons"]  # type: ignore[index]
    }


def test_every_reason_is_planted_somewhere() -> None:
    """NON-VACUITY over the SET rather than one reason at a time.

    A reason added later with no plant would sit in the vocabulary looking like
    coverage while never having been observed to fire once.
    """
    assert set(capability.Reason) == {
        capability.Reason.FAR_END_SENTINEL,
        capability.Reason.SERVICE_STATE_ASSERTED,
        capability.Reason.NO_INSIDE_VANTAGE,
        capability.Reason.NO_INDUCED_FAILURE,
        capability.Reason.COMPOSE_IDENTITY_UNUSED,
        capability.Reason.SOURCE_UNREADABLE,
    }


def test_losing_the_inside_vantage_is_refused_again(tmp_path: Path) -> None:
    """REGRESSION PLANT for items 12 and 16, in BOTH halves.

    Removing the literals is not the same as taking the measurement, so the
    detector checks for the MECHANISM too. Both halves are planted: a collector
    that emits the old literal, and a runner that no longer observes the
    refusal. A detector satisfied by only one would go quiet the first time
    somebody repaired the other and left the items failing on absent keys — the
    defect relocated rather than repaired.
    """
    literal = _tree_with(
        tmp_path / "collector",
        {
            capability.COLLECTOR: [
                (
                    "printf '  },\\n'",
                    'printf \'    \\"private_inside\\": '
                    "{\\\"reachable\\\": false}\\n'\nprintf '  },\\n'",
                )
            ]
        },
    )
    assert capability.Reason.NO_INSIDE_VANTAGE in _reasons(literal)

    unobserved = _tree_with(
        tmp_path / "runner",
        {
            capability.RUNNER: [
                (
                    f"inside_vantage.{capability.INSIDE_REFUSAL_SEAM}(",
                    "inside_vantage._not_observed(",
                )
            ]
        },
    )
    assert capability.Reason.NO_INSIDE_VANTAGE in _reasons(unobserved)


def test_asserting_the_service_state_again_is_refused(tmp_path: Path) -> None:
    """REGRESSION PLANT for item 13's repair, in BOTH halves.

    The collector measured a literal and the runner read an ABSENT key as a
    running service. Either alone is enough to refuse, so either alone is
    planted — a detector satisfied by only one of them would go quiet the first
    time somebody repaired the other.
    """
    literal = _tree_with(
        tmp_path / "collector",
        {
            capability.COLLECTOR: [
                ('\\"service_running\\": %s', '\\"service_running\\": true')
            ]
        },
    )
    assert capability.Reason.SERVICE_STATE_ASSERTED in {
        entry["reason"]
        for entry in capability.assess(literal)["reasons"]  # type: ignore[index]
    }

    defaulted = _tree_with(
        tmp_path / "runner",
        {
            capability.RUNNER: [
                ('probe["service_running"]', 'probe.get("service_running", True)')
            ]
        },
    )
    assert capability.Reason.SERVICE_STATE_ASSERTED in {
        entry["reason"]
        for entry in capability.assess(defaulted)["reasons"]  # type: ignore[index]
    }


def test_reintroducing_a_far_end_sentinel_is_refused_again(tmp_path: Path) -> None:
    """REGRESSION PLANT for the sentinel repair.

    The collector now reads the target's own report of this vantage's source
    address. Putting a fabricated value back must bring the reason with it —
    and the fabricated value is the one that matters, because a sentinel is
    NON-EMPTY, so `qualify_vantage` refuses through the MISMATCH branch and the
    run dies before recording a single item.
    """
    root = _tree_with(tmp_path, {})
    collector = root / capability.COLLECTOR
    text = collector.read_text(encoding="utf-8")
    collector.write_text(
        text.replace(
            '\\"%s\\",\\n\' \\"\\$OBSERVED4\\"', '\\"__TARGET_OBSERVED_V4__\\",\\n\''
        ),
        encoding="utf-8",
    )
    assert collector.read_text(encoding="utf-8") != text, "the plant anchor moved"
    reasons = {
        entry["reason"]
        for entry in capability.assess(root)["reasons"]  # type: ignore[index]
    }
    assert capability.Reason.FAR_END_SENTINEL in reasons


def test_removing_the_provocation_seam_is_refused_again(tmp_path: Path) -> None:
    """REGRESSION PLANT for item 8's repair.

    The detector looks for a call to the DECLARED provocation seam rather than
    pattern-matching arbitrary code, so removing the call must bring the reason
    back. A detector that accepted any `raise` would be satisfied by every error
    path the runner already has, none of which the apply path meets.
    """
    root = _tree_with(tmp_path, {})
    runner = root / capability.RUNNER
    text = runner.read_text(encoding="utf-8")
    assert f"{capability.PROVOCATION_SEAM}(" in text
    runner.write_text(
        text.replace(
            f"{capability.PROVOCATION_SEAM}(effects", "_no_provocation(effects"
        ),
        encoding="utf-8",
    )
    reasons = {
        entry["reason"]
        for entry in capability.assess(root)["reasons"]  # type: ignore[index]
    }
    assert capability.Reason.NO_INDUCED_FAILURE in reasons


def test_re_deriving_an_unused_compose_project_is_refused_again(
    tmp_path: Path,
) -> None:
    """REGRESSION PLANT for the reason this change repaired.

    A guard over a defect that has just been fixed passes for the wrong reason
    unless the defect is put back. This restores the exact shape: a name derived
    from the lease's Compose prefix whose only consumers are an evidence string
    and a check that cannot fire.
    """
    root = _tree_with(tmp_path, {})
    runner = root / capability.RUNNER
    text = runner.read_text(encoding="utf-8")
    text = text.replace(
        "    # This block used to derive a Compose project",
        '    project = f"{lease.compose_project_prefix}{args.authorization_run}"\n'
        "    if not lease.owns_project(project):\n"
        "        raise DeploymentFoundationError('outside the prefix')\n"
        "    # This block used to derive a Compose project",
        1,
    )
    runner.write_text(text, encoding="utf-8")

    record = capability.assess(root)
    reasons = {entry["reason"] for entry in record["reasons"]}  # type: ignore[index]
    assert capability.Reason.COMPOSE_IDENTITY_UNUSED in reasons
    assert record["verdict"] == "not_capable"


def test_passing_the_derived_project_to_a_consumer_satisfies_it(
    tmp_path: Path,
) -> None:
    """The OTHER permitted repair, so the detector is not written for one.

    The criteria allow either using the derived name or not deriving it. This
    change took the second; a detector that only accepted the first would refuse
    a conforming future fix.
    """
    root = _tree_with(tmp_path, {})
    runner = root / capability.RUNNER
    text = runner.read_text(encoding="utf-8")
    text = text.replace(
        "    # This block used to derive a Compose project",
        '    project = f"{lease.compose_project_prefix}{args.authorization_run}"\n'
        "    # This block used to derive a Compose project",
        1,
    ).replace(
        "        deploy_dir=args.deploy_dir,",
        "        deploy_dir=args.deploy_dir,\n        compose_project=project,",
        1,
    )
    runner.write_text(text, encoding="utf-8")

    reasons = {
        entry["reason"]
        for entry in capability.assess(root)["reasons"]  # type: ignore[index]
    }
    assert capability.Reason.COMPOSE_IDENTITY_UNUSED not in reasons


def test_an_unreadable_source_refuses_rather_than_passing_as_capable(
    tmp_path: Path,
) -> None:
    """ABSENT must be distinguishable from UNEXAMINED.

    Every detector above returns `None` when it finds nothing wrong, so a tree
    whose files cannot be read would produce no findings and read as capable.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    record = capability.assess(empty)
    assert record["verdict"] == "not_capable"
    assert {entry["reason"] for entry in record["reasons"]} == {  # type: ignore[index]
        capability.Reason.SOURCE_UNREADABLE
    }


def test_an_unparseable_runner_refuses(tmp_path: Path) -> None:
    """The same point one level in: a runner that does not parse cannot be
    judged capable, and must not be judged capable by default."""
    root = _tree_with(tmp_path, {})
    (root / capability.RUNNER).write_text("def broken(:\n", encoding="utf-8")
    record = capability.assess(root)
    assert record["verdict"] == "not_capable"
    assert capability.Reason.SOURCE_UNREADABLE in {
        entry["reason"]
        for entry in record["reasons"]  # type: ignore[index]
    }


def test_the_record_serialises_to_the_declared_schema() -> None:
    """The gate reads JSON, so the record has to survive a round trip."""
    record = capability.assess(ROOT)
    assert record["schema"] == "Lane3RunnerCapability.v1"
    assert json.loads(json.dumps(record)) == record
