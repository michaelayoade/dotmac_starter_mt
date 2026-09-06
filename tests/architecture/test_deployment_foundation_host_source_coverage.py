"""Every mutating `Executor` entry point calls `_verify_host_source`.

## What this guards against

`run.py` has exactly one class that mutates a host: `Executor`. Its two
mutating entry points, `run` and `rollback`, are marked the same way this
codebase already marks "this requires a proven lock" —
they accept `lock: DeploymentLockHeld` as a keyword parameter, the same
structural marker `test_deployment_foundation_lock_capability.py` relies on
("a caller holding no lock has nothing to call it with"). A future mutating
entry point that copies `run`'s shape (accepts `lock=`) but forgets to call
`_verify_host_source` is exactly the gap Boundary 4's ruling closes — and
exactly the gap `test_deployment_foundation_single_executor.py` was written
against for the authorization seam. This is the same guard, one seam over.

## Why the plants are synthetic

Per `test_deployment_foundation_single_executor.py`'s own stated reason: a
plant committed to disk becomes part of the tree the guard approves the moment
it lands. Both plants here are built from STRINGS this test parses with `ast`,
never written to disk as real code.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN_PY = (
    REPO
    / "packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation"
    / "engine/run.py"
)

#: The structural marker for "this mutates a host and must hold the caller's
#: lock" — see `run`/`rollback`'s own signatures and
#: `test_deployment_foundation_lock_capability.py`'s docstring.
_LOCK_MARKER = "lock"

#: What every mutating entry point must call, directly, in its own body.
_PREREQUISITE = "_verify_host_source"


def _executor_class(tree: ast.Module) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Executor":
            return node
    raise AssertionError("no class Executor found; this proves nothing")


def _all_arg_names(node: ast.FunctionDef) -> set[str]:
    args = node.args
    names = {a.arg for a in args.args}
    names |= {a.arg for a in args.kwonlyargs}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def _mutating_entry_points(class_node: ast.ClassDef) -> list[ast.FunctionDef]:
    """Public methods that accept `lock=` — the marker a mutating entry point
    already carries in this file, independent of this guard's own addition."""
    points = []
    for node in class_node.body:
        if (
            isinstance(node, ast.FunctionDef)
            and not node.name.startswith("_")
            and _LOCK_MARKER in _all_arg_names(node)
        ):
            points.append(node)
    return points


def _calls_prerequisite(
    func_node: ast.FunctionDef, *, name: str = _PREREQUISITE
) -> bool:
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == name
        for call in ast.walk(func_node)
    )


# ── the named proof ──────────────────────────────────────────────────────


def test_every_mutating_entry_point_calls_the_host_source_prerequisite() -> None:
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"), filename=str(RUN_PY))
    executor = _executor_class(tree)
    points = _mutating_entry_points(executor)

    # A TWO-DIRECTIONAL assertion on the population itself: if a new mutating
    # entry point appears (or one of the two known ones vanishes) this fails
    # even before checking the call, so the set cannot silently drift under
    # the guard the way `UNAUTHORIZED_EXECUTE_BACKLOG` warns against.
    names = {p.name for p in points}
    assert names == {"run", "rollback"}, (
        f"the set of lock-taking (mutating) Executor entry points moved: "
        f"{sorted(names)}. A NEW one needs the same `_verify_host_source` "
        "call this test enforces on the other two; a REMOVED one is worth "
        "recording here rather than silently shrinking the population"
    )

    missing = [p.name for p in points if not _calls_prerequisite(p)]
    assert missing == [], (
        f"{missing} accepts a proven lock (so it mutates a host) but never "
        f"calls {_PREREQUISITE}. That is exactly the hole Boundary 4's "
        "ruling closes: a mutating path that forgot the mandatory host "
        "source verification"
    )


# ── sensitivity: the plant is NAMED ─────────────────────────────────────────


def test_the_coverage_guard_names_a_planted_omission() -> None:
    """PLANTED, in memory. A mutating-shaped method with NO call to the
    prerequisite must be caught, and caught BY NAME."""
    source = (
        "class Executor:\n"
        "    def run(self, plan, *, lock):\n"
        "        self._lock_path = lock.require_held(product=self._spec.product)\n"
        "        self._grant.require(operation='deploy', descriptor_digest='x')\n"
        "        return None\n"
        "\n"
        "    def rollback(self, plan, *, lock):\n"
        "        self._lock_path = lock.require_held(product=self._spec.product)\n"
        "        self._verify_host_source()\n"
        "        self._grant.require(operation='rollback', descriptor_digest='x')\n"
        "        return None\n"
    )
    tree = ast.parse(source, filename="<plant: omitted prerequisite>")
    executor = _executor_class(tree)
    points = _mutating_entry_points(executor)
    assert {p.name for p in points} == {
        "run",
        "rollback",
    }, "the plant does not exhibit the shape being detected"

    missing = [p.name for p in points if not _calls_prerequisite(p)]
    assert missing == ["run"], (
        f"expected exactly ['run'] to be named as missing the prerequisite, "
        f"got {missing}. The guard must name the OFFENDING method, not merely "
        "report that something, somewhere, is wrong"
    )


# ── near-miss: a non-mutating helper needs no gate, and must stay silent ──


def test_the_coverage_guard_stays_silent_on_a_read_only_near_miss() -> None:
    """NEAR-MISS, MUST BE SILENT, and it must be actually EXERCISED — the
    assertion on `points` below fails loudly if the near-miss method were
    (wrongly) treated as mutating, so this cannot pass merely because the
    file changed shape."""
    source = (
        "class Executor:\n"
        "    def observe(self, plan):\n"
        "        return self._effects.observe_roles()\n"
        "\n"
        "    def run(self, plan, *, lock):\n"
        "        self._lock_path = lock.require_held(product=self._spec.product)\n"
        "        self._verify_host_source()\n"
        "        self._grant.require(operation='deploy', descriptor_digest='x')\n"
        "        return None\n"
    )
    tree = ast.parse(source, filename="<near-miss: read-only helper>")
    executor = _executor_class(tree)
    points = _mutating_entry_points(executor)

    # It was actually EXERCISED: `observe` is present in the class but absent
    # from the population the guard evaluates, which is the only way this
    # test can tell "correctly excluded" from "never looked at".
    names = {p.name for p in points}
    assert "observe" not in names, (
        "observe() takes no `lock=` and must not be treated as a mutating "
        "entry point; if it appears here the marker stopped discriminating"
    )
    assert names == {"run"}

    missing = [p.name for p in points if not _calls_prerequisite(p)]
    assert missing == [], (
        "the near-miss plant's only real entry point DOES call the "
        "prerequisite; the guard must stay silent here"
    )


def test_the_real_executor_class_is_actually_found() -> None:
    """A guard that silently found zero classes and reported an empty
    coverage set would look identical to a fully-covered one. This is the
    "found something real" control for the named proof above."""
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"), filename=str(RUN_PY))
    executor = _executor_class(tree)
    assert executor.name == "Executor"
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "_verify_host_source"
        for node in executor.body
    ), "Executor no longer defines _verify_host_source at all"
