"""Every mutating `RecoveryExecutor` entry point calls `_verify_host_source`.

## What this guards against

`recovery_execution.py` has exactly one class that mutates a host:
`RecoveryExecutor`. It has no `lock:` parameter the way `engine.run.Executor`
does — this class only creates and destroys a cluster IT created, and takes
no deployment lock at all — so the marker
`test_deployment_foundation_host_source_coverage.py` uses for `Executor`
does not apply here. The population is instead every PUBLIC method other than
`__init__`: today that is exactly `run`, but a future mutating entry point
added to this class (a resumed/retried recovery, say) would join the
population automatically, and the guard would refuse to pass silently if it
forgot the same call.

## Why the plants are synthetic

Same reason as the sibling guard: a plant committed to disk becomes part of
the tree the guard approves the moment it lands. Both plants here are built
from STRINGS this test parses with `ast`, never written to disk as real code.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.architecture.host_source_skip_inventory import (
    RECOVERY_BEHAVIOR_GAP_INVENTORY,
    RECOVERY_BEHAVIOR_GAP_RETIRE_WHEN,
    SKIP_INVENTORY_SCOPE,
)
from tests.architecture.test_deployment_foundation_host_source_constructor_seam import (
    _require_non_admission_call_shape,
)

REPO = Path(__file__).resolve().parents[2]
RECOVERY_EXECUTION_PY = (
    REPO
    / "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"
    / "recovery_execution.py"
)

_PREREQUISITE = "_verify_host_source"


def _recovery_executor_class(tree: ast.Module) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "RecoveryExecutor":
            return node
    raise AssertionError("no class RecoveryExecutor found; this proves nothing")


def _public_methods(class_node: ast.ClassDef) -> list[ast.FunctionDef]:
    """Every method other than `__init__` and a private (`_`-prefixed) one.

    `__init__` is excluded deliberately: it cannot call `_verify_host_source`
    itself (the receipt/metadata seam is only assembled there), and Boundary
    4's ruling is about the call happening before the first EFFECT, which for
    a constructor-time refusal has not been reached yet.
    """
    return [
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef)
        and not node.name.startswith("_")
        and node.name != "__init__"
    ]


def _calls_prerequisite(func_node: ast.FunctionDef) -> bool:
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == _PREREQUISITE
        for call in ast.walk(func_node)
    )


# ── the named proof ──────────────────────────────────────────────────────


def test_every_mutating_entry_point_calls_the_host_source_prerequisite() -> None:
    tree = ast.parse(
        RECOVERY_EXECUTION_PY.read_text(encoding="utf-8"),
        filename=str(RECOVERY_EXECUTION_PY),
    )
    executor = _recovery_executor_class(tree)
    points = _public_methods(executor)

    # A TWO-DIRECTIONAL assertion on the population itself, mirroring
    # `test_deployment_foundation_host_source_coverage.py`: if a new public
    # method appears (or `run` vanishes) this fails before checking the call.
    names = {p.name for p in points}
    assert names == {"run"}, (
        f"the set of public RecoveryExecutor entry points moved: "
        f"{sorted(names)}. A NEW one needs the same `_verify_host_source` "
        "call this test enforces on `run`; a REMOVED one is worth recording "
        "here rather than silently shrinking the population"
    )

    missing = [p.name for p in points if not _calls_prerequisite(p)]
    assert missing == [], (
        f"{missing} is a public RecoveryExecutor method (so it can mutate a "
        f"host) but never calls {_PREREQUISITE}"
    )


# ── sensitivity: the plant is NAMED ─────────────────────────────────────────


def test_the_coverage_guard_names_a_planted_omission() -> None:
    """PLANTED, in memory. A public method with NO call to the prerequisite
    must be caught, and caught BY NAME."""
    source = (
        "class RecoveryExecutor:\n"
        "    def __init__(self, spec, manifest, effects, *, source_evidence, "
        "product_image):\n"
        "        pass\n"
        "\n"
        "    def run(self, bundle):\n"
        "        return None\n"
    )
    tree = ast.parse(source, filename="<plant: omitted prerequisite>")
    executor = _recovery_executor_class(tree)
    points = _public_methods(executor)
    assert {p.name for p in points} == {
        "run"
    }, "the plant does not exhibit the shape being detected"

    missing = [p.name for p in points if not _calls_prerequisite(p)]
    assert missing == ["run"], (
        f"expected exactly ['run'] to be named as missing the prerequisite, "
        f"got {missing}"
    )


# ── near-miss: a private helper needs no gate, and must stay silent ────────


def test_the_coverage_guard_stays_silent_on_a_private_helper_near_miss() -> None:
    """NEAR-MISS, MUST BE SILENT, and EXERCISED: the assertion on `points`
    below fails loudly if the near-miss method were (wrongly) treated as part
    of the population. `RecoveryExecutor` has no `lock:`-style marker the way
    `engine.run.Executor` does to separate a read-only PUBLIC method from a
    mutating one — every public method here is treated as mutating, which is
    the conservative direction (over-, never under-, monitoring). What this
    guard DOES correctly exclude is a private dispatch helper — `_dispatch`
    and the ten `_do_*` step handlers are exactly this shape in the real
    class, called only from within `run`, which is where the one real call
    lives."""
    source = (
        "class RecoveryExecutor:\n"
        "    def _do_fresh_target(self, bundle, outcome):\n"
        "        return None\n"
        "\n"
        "    def run(self, bundle):\n"
        "        self._verify_host_source()\n"
        "        self._do_fresh_target(bundle, None)\n"
        "        return None\n"
    )
    tree = ast.parse(source, filename="<near-miss: private step handler>")
    executor = _recovery_executor_class(tree)
    points = _public_methods(executor)

    names = {p.name for p in points}
    assert "_do_fresh_target" not in names, (
        "_do_fresh_target is PRIVATE and must not be treated as part of the "
        "population the guard evaluates; if it appears here the marker "
        "stopped discriminating on name"
    )
    assert names == {"run"}

    missing = [p.name for p in points if not _calls_prerequisite(p)]
    assert missing == [], (
        "the near-miss plant's only real entry point DOES call the "
        "prerequisite; the guard must stay silent here"
    )


def test_the_real_class_is_actually_found() -> None:
    """A guard that silently found zero classes would look identical to a
    fully-covered one."""
    tree = ast.parse(
        RECOVERY_EXECUTION_PY.read_text(encoding="utf-8"),
        filename=str(RECOVERY_EXECUTION_PY),
    )
    executor = _recovery_executor_class(tree)
    assert executor.name == "RecoveryExecutor"
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "_verify_host_source"
        for node in executor.body
    ), "RecoveryExecutor no longer defines _verify_host_source at all"


def test_recovery_behavior_gap_is_named_and_separate_from_executor_skip_inventory() -> (
    None
):
    """The 73 skips do not claim coverage of RecoveryExecutor.run's sequence."""
    assert SKIP_INVENTORY_SCOPE == "Executor tests only"
    assert RECOVERY_BEHAVIOR_GAP_RETIRE_WHEN == (
        "trusted-provenance-admission-and-real-RecoveryExecutor.run-coverage"
    )
    tree = ast.parse(
        RECOVERY_EXECUTION_PY.read_text(encoding="utf-8"),
        filename=str(RECOVERY_EXECUTION_PY),
    )
    executor = _recovery_executor_class(tree)
    assert RECOVERY_BEHAVIOR_GAP_INVENTORY == (
        "RecoveryExecutor.run::real-ten-step-sequence",
    )
    run = _run_method(executor)
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_verify_host_source"
        for node in ast.walk(run)
    )


def test_recovery_gap_stays_non_admitting_until_real_run_coverage_exists() -> None:
    """A planted admission call cannot silently retire the named gap."""
    source = (
        "class RecoveryExecutor:\n"
        "    def _verify_host_source(self):\n"
        "        return self.attested_pair\n"
        "    def run(self, bundle):\n"
        "        self._verify_host_source()\n"
    )
    tree = ast.parse(source, filename="<plant: recovery admission>")
    with pytest.raises(AssertionError, match="non-admitting"):
        _require_non_admission_call_shape(
            tree, class_name="RecoveryExecutor", path=Path("<plant>")
        )
    assert RECOVERY_BEHAVIOR_GAP_RETIRE_WHEN.endswith(
        "real-RecoveryExecutor.run-coverage"
    )
    assert any(
        isinstance(node, ast.Attribute) and node.attr == "attested_pair"
        for node in ast.walk(tree)
    ), "the plant must represent an admission change"


# ── STRUCTURAL ordering, over the real `run()` method's own source ─────────
#
# `_drive_steps` (a test helper that re-implemented `run()`'s dispatch loop
# to exercise post-gate behaviour) was removed after review found it had
# already drifted from the real method it copied — see
# `tests/unit/test_deployment_foundation_recovery_execution.py`'s module
# docstring. The replacement for the ordering claim that helper used to make
# is NOT another re-driven copy: it is a direct read of `run()`'s own
# top-level statements, asserting that the statement calling
# `_verify_host_source` appears BEFORE the statement calling `restore_plan`
# (procedure validation) and BEFORE the statement containing the dispatch
# loop (`_dispatch`). If a future edit reorders `run()`'s body, THIS test —
# reading that body directly — is what catches it; nothing here executes
# `run()` or any substitute for it.


def _run_method(class_node: ast.ClassDef) -> ast.FunctionDef:
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            return node
    raise AssertionError("RecoveryExecutor.run not found; this proves nothing")


def _first_top_level_index(
    body: list[ast.stmt], *, attr_call: str | None = None, name_call: str | None = None
) -> int | None:
    """The index of the first TOP-LEVEL statement in `body` whose subtree
    contains a call matching `attr_call` (an attribute call, `self.x(...)`)
    or `name_call` (a bare-name call, `f(...)`). Top-level, not nested — this
    is what lets "before"/"after" mean something about the method's own
    control flow rather than about arbitrary nesting depth.
    """
    for index, stmt in enumerate(body):
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if (
                attr_call
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr_call
            ):
                return index
            if (
                name_call
                and isinstance(node.func, ast.Name)
                and node.func.id == name_call
            ):
                return index
    return None


def test_run_calls_verify_host_source_before_restore_plan_and_dispatch() -> None:
    """The named, structural proof: reading `RecoveryExecutor.run`'s own
    statement order, `_verify_host_source` precedes both `restore_plan`
    (procedure validation) and `_dispatch` (the step loop)."""
    tree = ast.parse(
        RECOVERY_EXECUTION_PY.read_text(encoding="utf-8"),
        filename=str(RECOVERY_EXECUTION_PY),
    )
    executor = _recovery_executor_class(tree)
    run = _run_method(executor)

    verify_index = _first_top_level_index(run.body, attr_call="_verify_host_source")
    restore_plan_index = _first_top_level_index(run.body, name_call="restore_plan")
    dispatch_index = _first_top_level_index(run.body, attr_call="_dispatch")

    assert verify_index is not None, "run() no longer calls _verify_host_source at all"
    assert restore_plan_index is not None, "run() no longer calls restore_plan at all"
    assert dispatch_index is not None, "run() no longer calls _dispatch at all"

    assert verify_index < restore_plan_index, (
        f"_verify_host_source (statement {verify_index}) does not precede "
        f"restore_plan (statement {restore_plan_index}) in run()'s own body"
    )
    assert restore_plan_index <= dispatch_index, (
        f"restore_plan (statement {restore_plan_index}) does not precede or "
        f"coincide with the dispatch loop (statement {dispatch_index}) in "
        "run()'s own body"
    )
    assert verify_index < dispatch_index, (
        f"_verify_host_source (statement {verify_index}) does not precede "
        f"the dispatch loop (statement {dispatch_index}) in run()'s own body"
    )


def test_the_ordering_guard_names_a_planted_reversal() -> None:
    """PLANTED, in memory. A synthetic `run()` that calls `restore_plan` and
    dispatches BEFORE `_verify_host_source` must be caught, by index
    comparison, on this exact plant."""
    source = (
        "class RecoveryExecutor:\n"
        "    def run(self, bundle):\n"
        "        procedure = restore_plan(self._spec, self._manifest)\n"
        "        for spec in procedure:\n"
        "            self._dispatch(spec.step, bundle, None, [])\n"
        "        self._verify_host_source()\n"
        "        return None\n"
    )
    tree = ast.parse(source, filename="<plant: verification reordered after dispatch>")
    executor = _recovery_executor_class(tree)
    run = _run_method(executor)

    verify_index = _first_top_level_index(run.body, attr_call="_verify_host_source")
    restore_plan_index = _first_top_level_index(run.body, name_call="restore_plan")
    dispatch_index = _first_top_level_index(run.body, attr_call="_dispatch")

    assert verify_index is not None
    assert restore_plan_index is not None
    assert dispatch_index is not None
    assert verify_index > restore_plan_index, "the plant does not exhibit the reversal"
    assert verify_index > dispatch_index, "the plant does not exhibit the reversal"


def test_the_ordering_guard_stays_silent_on_the_correctly_ordered_near_miss() -> None:
    """NEAR-MISS, MUST BE SILENT, and EXERCISED: the correctly-ordered shape
    (the real one) must not be flagged."""
    source = (
        "class RecoveryExecutor:\n"
        "    def run(self, bundle):\n"
        "        self._verify_host_source()\n"
        "        procedure = restore_plan(self._spec, self._manifest)\n"
        "        for spec in procedure:\n"
        "            self._dispatch(spec.step, bundle, None, [])\n"
        "        return None\n"
    )
    tree = ast.parse(source, filename="<near-miss: correctly ordered>")
    executor = _recovery_executor_class(tree)
    run = _run_method(executor)

    verify_index = _first_top_level_index(run.body, attr_call="_verify_host_source")
    restore_plan_index = _first_top_level_index(run.body, name_call="restore_plan")
    dispatch_index = _first_top_level_index(run.body, attr_call="_dispatch")

    assert verify_index is not None
    assert restore_plan_index is not None
    assert dispatch_index is not None
    assert verify_index < restore_plan_index < dispatch_index
