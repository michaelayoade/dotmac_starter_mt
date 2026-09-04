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


def test_this_tree_is_not_capable_and_says_exactly_which_reasons_remain() -> None:
    """The live verdict. It gets shorter as repairs land, and that is the point.

    `compose_identity_unused` was repaired in this change and is deliberately
    absent — its detector is proved below by planting the defect BACK, because a
    guard whose subject has been fixed is about the NEXT occurrence, not the
    last one.

    The four that remain all need a host or the collector rework, and no host
    may be contacted until Michael answers on the disposable target.
    """
    record = capability.assess(ROOT)
    assert record["verdict"] == "not_capable"
    assert {entry["reason"] for entry in record["reasons"]} == LIVE_REASONS  # type: ignore[index]
    # Every reason carries a location a reader can check it against. A reason
    # without one is a status, and a status a reader cannot check is a number.
    for entry in record["reasons"]:  # type: ignore[union-attr]
        assert entry["detail"].strip()
        assert entry["evidence"]


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


#: One repair per LIVE reason — the smallest edit that removes it, so a flipped
#: code cannot be an accident of a broad rewrite. These are shapes, not the real
#: fixes: the real ones need a host, and none has been contacted.
REPAIRS: dict[capability.Reason, dict[Path, list[tuple[str, str]]]] = {
    capability.Reason.FAR_END_SENTINEL: {
        capability.COLLECTOR: [
            ("__TARGET_OBSERVED_V4__", "${TARGET_SAW_V4}"),
            ("__TARGET_OBSERVED_V6__", "${TARGET_SAW_V6}"),
        ]
    },
    capability.Reason.NO_INSIDE_VANTAGE: {
        capability.COLLECTOR: [
            (
                '\\"private_inside\\": {\\"reachable\\": false',
                '\\"private_inside\\": {\\"reachable\\": %s',
            ),
            (
                '\\"privileged_vantage_refused\\": null',
                '\\"privileged_vantage_refused\\": %s',
            ),
        ]
    },
    capability.Reason.SERVICE_STATE_ASSERTED: {
        capability.COLLECTOR: [
            ('\\"service_running\\": true', '\\"service_running\\": %s')
        ],
        capability.RUNNER: [
            ('probe.get("service_running", True)', 'probe["service_running"]')
        ],
    },
    capability.Reason.NO_INDUCED_FAILURE: {
        capability.RUNNER: [
            (
                "    restored = effects.observe()",
                "    provoke_apply_failure(effects)\n    restored = effects.observe()",
            )
        ]
    },
}

LIVE_REASONS = frozenset(REPAIRS)


@pytest.mark.parametrize("repaired", list(REPAIRS))
def test_repairing_exactly_one_reason_clears_exactly_that_reason(
    repaired: capability.Reason, tmp_path: Path
) -> None:
    """PER-CODE ISOLATION. Independent detectors, independent subjects.

    A detector firing on some shared condition would clear several reasons at
    once here, which is the shape an aggregate wears when dressed as a set of
    codes.
    """
    record = capability.assess(_tree_with(tmp_path, REPAIRS[repaired]))
    reasons = {entry["reason"] for entry in record["reasons"]}  # type: ignore[index]
    assert repaired not in reasons, f"{repaired} survived its own repair"
    assert reasons == LIVE_REASONS - {repaired}, reasons
    assert record["verdict"] == "not_capable"


def test_a_source_with_none_of_them_is_capable(tmp_path: Path) -> None:
    """THE OTHER DIRECTION, and the one that would be skipped.

    Without it, every assertion above is consistent with a function that returns
    `not_capable` unconditionally — the gate would have been observed only at the
    value it was written to return.
    """
    every: dict[Path, list[tuple[str, str]]] = {}
    for repairs in REPAIRS.values():
        for relative, edits in repairs.items():
            every.setdefault(relative, []).extend(edits)
    record = capability.assess(_tree_with(tmp_path, every))
    assert record["reasons"] == [], record["reasons"]
    assert record["verdict"] == "capable"


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
