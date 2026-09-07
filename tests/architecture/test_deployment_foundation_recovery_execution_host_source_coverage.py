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
