"""Closed AST proof for the reviewed Executor.run witness-lifetime wrapper.

This resolves one exact transparent wrapper, never arbitrary helper chains. Both
public returns must forward the same plan and proven lock to the same real
method. Any extra statement, alternate return, branch or effect refuses.
"""

from __future__ import annotations

import ast

_WRAPPER = """
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
"""
_GUARD_PREFIX = """
self._lock_path = lock.require_held(product=self._spec.product)
self._verify_host_source()
"""


def _body(method: ast.FunctionDef) -> list[ast.stmt]:
    statements = method.body
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]
    return statements


def _shape(statements: list[ast.stmt]) -> str:
    return ast.dump(ast.Module(body=statements, type_ignores=[]))


def _require_lock_signature(method: ast.FunctionDef) -> None:
    args = method.args
    assert not method.decorator_list, "execution method must be the actual method"
    assert [arg.arg for arg in args.posonlyargs + args.args] == ["self", "plan"]
    assert [arg.arg for arg in args.kwonlyargs] == ["lock"]
    assert args.kw_defaults == [None] and not args.defaults
    assert args.vararg is None and args.kwarg is None


def _direct_guard(method: ast.FunctionDef) -> bool:
    return _shape(_body(method)[:2]) == _shape(ast.parse(_GUARD_PREFIX).body)


def resolve_execution_body(executor: ast.ClassDef, entry_name: str) -> ast.FunctionDef:
    """Return a directly guarded body, or prove the ONE reviewed run wrapper."""
    definitions = [
        node
        for node in executor.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    methods = {method.name: method for method in definitions}
    assert len(methods) == len(
        definitions
    ), "duplicate method definitions obscure entry"
    assert entry_name in methods, "public entry is missing"
    protected = {entry_name, "_run_consuming"}
    for statement in executor.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        assert not any(
            (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Store)
                and node.id in protected
            )
            or (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in protected
            )
            for node in ast.walk(statement)
        ), "class-scope rebinding cannot replace the execution method"
    entry = methods[entry_name]
    assert isinstance(entry, ast.FunctionDef), "execution must be a synchronous method"
    _require_lock_signature(entry)
    if _direct_guard(entry):
        return entry
    assert entry_name == "run", "only the reviewed run wrapper may delegate"
    expected = ast.parse(_WRAPPER).body[0]
    assert isinstance(expected, ast.FunctionDef)
    assert _shape(_body(entry)) == _shape(expected.body), (
        "run must forward unchanged plan+lock on both returns to one guarded body; "
        "only the reviewed witness assignments/refusal/finally are transparent"
    )
    assert "_run_consuming" in methods, "delegated body must be an actual method"
    body = methods["_run_consuming"]
    assert isinstance(
        body, ast.FunctionDef
    ), "delegated body must execute synchronously"
    _require_lock_signature(body)
    assert _direct_guard(body), "delegated body must prove lock then source directly"
    return body
