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

The V5 witness-lifetime wrapper is resolved only after an exact closed AST proof:
both returns forward the same plan and lock, and no unreviewed call, effect,
return or branch enters the wrapper. The real private method must itself prove
the lock then call the source guard directly. A helper name alone grants no
exemption, and arbitrary transitive helper chains are never followed.

## Why the plants are synthetic

Per `test_deployment_foundation_single_executor.py`'s own stated reason: a
plant committed to disk becomes part of the tree the guard approves the moment
it lands. Both plants here are built from STRINGS this test parses with `ast`,
never written to disk as real code.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.architecture.foundation_executor_ast import resolve_execution_body

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

#: Mandatory direct guard, after proving the lock, in the resolved execution body.
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


def _calls_prerequisite(executor: ast.ClassDef, func_node: ast.FunctionDef) -> bool:
    try:
        resolve_execution_body(executor, func_node.name)
    except AssertionError:
        return False
    return True


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

    missing = [p.name for p in points if not _calls_prerequisite(executor, p)]
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

    missing = [p.name for p in points if not _calls_prerequisite(executor, p)]
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

    missing = [p.name for p in points if not _calls_prerequisite(executor, p)]
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


# The wrapper population is closed by control flow, not by a private method name.
_WRAPPED_EXECUTOR = """
class Executor:
    def run(self, plan, *, lock):
        if self._spec.descriptor_schema != SCHEMA_V5:
            return self._run_consuming(plan, lock=lock)
        if self._startup_token is not None:
            raise LiveInstanceRefusal("unsupported")
        self._startup_token = object()
        self._startup_lock = lock
        try:
            return self._run_consuming(plan, lock=lock)
        finally:
            self._startup_continuation = None
            self._startup_token = None
            self._startup_lock = None

    def _run_consuming(self, plan, *, lock):
        self._lock_path = lock.require_held(product=self._spec.product)
        self._verify_host_source()
        return plan

    def rollback(self, plan, *, lock):
        self._lock_path = lock.require_held(product=self._spec.product)
        self._verify_host_source()
        raise PreconditionFailed()
"""


def test_reviewed_wrapper_resolves_one_actual_guarded_body() -> None:
    executor = _executor_class(ast.parse(_WRAPPED_EXECUTOR))
    assert {method.name for method in _mutating_entry_points(executor)} == {
        "run",
        "rollback",
    }
    assert resolve_execution_body(executor, "run").name == "_run_consuming"
    assert resolve_execution_body(executor, "rollback").name == "rollback"


@pytest.mark.parametrize(
    "mutant",
    [
        "inner_guard",
        "wrong_body",
        "wrong_lock",
        "wrong_plan",
        "early_effect",
        "unguarded_return",
        "conditional_bypass",
        "conditional_guard",
        "missing_body",
        "body_without_lock",
        "decorated_body",
        "shadowed_body",
    ],
)
def test_wrapper_proof_refuses_bypass_and_forwarding_mutants(mutant: str) -> None:
    executor = _executor_class(ast.parse(_WRAPPED_EXECUTOR))
    assert resolve_execution_body(executor, "run").name == "_run_consuming"
    methods = {
        node.name: node for node in executor.body if isinstance(node, ast.FunctionDef)
    }
    run, body = methods["run"], methods["_run_consuming"]
    if mutant == "inner_guard":
        body.body.pop(1)
    elif mutant == "wrong_body":
        run.body[0].body[0].value.func.attr = "_unguarded"
    elif mutant == "wrong_lock":
        run.body[-1].body[0].value.keywords[0].value = ast.Name(
            id="other_lock", ctx=ast.Load()
        )
    elif mutant == "wrong_plan":
        run.body[0].body[0].value.args[0] = ast.Name(id="other_plan", ctx=ast.Load())
    elif mutant == "early_effect":
        run.body.insert(0, ast.parse("self._effects.switch(plan)").body[0])
    elif mutant == "unguarded_return":
        run.body[0].body[0].value = ast.Constant(value=None)
    elif mutant == "conditional_bypass":
        run.body.insert(0, ast.parse("if bypass: return plan").body[0])
    elif mutant == "conditional_guard":
        body.body[1] = ast.parse("if allowed: self._verify_host_source()").body[0]
    elif mutant == "missing_body":
        executor.body.remove(body)
    elif mutant == "body_without_lock":
        body.args.kwonlyargs = []
        body.args.kw_defaults = []
    elif mutant == "decorated_body":
        body.decorator_list = [ast.Name(id="unguarded", ctx=ast.Load())]
    else:
        executor.body.append(ast.parse("_run_consuming = unguarded").body[0])
    with pytest.raises(AssertionError):
        resolve_execution_body(executor, "run")
    assert not _calls_prerequisite(executor, run)
