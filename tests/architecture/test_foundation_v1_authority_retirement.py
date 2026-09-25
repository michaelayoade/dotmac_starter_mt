"""Collected tests and active entry points may not resurrect V1 execution authority."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "tests" / "unit"
SOURCE = (
    ROOT
    / "packages"
    / "dotmac-deployment-foundation"
    / "src"
    / "dotmac_deployment_foundation"
)


def _calls(path: Path, name: str) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []

    class Visitor(ast.NodeVisitor):
        current = "<module>"

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            before = self.current
            self.current = node.name
            self.generic_visit(node)
            self.current = before

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == name:
                found.append((self.current, node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def test_no_collected_positive_call_to_historical_authorize() -> None:
    found = {
        path.name: calls
        for path in UNIT.glob("test_deployment_foundation_*.py")
        if (calls := _calls(path, "authorize"))
    }
    assert {name: [owner for owner, _ in calls] for name, calls in found.items()} == {
        "test_deployment_foundation_execution_seam.py": [
            "test_historical_authorize_always_refuses"
        ]
    }


def test_active_entry_points_do_not_call_v1_authorize() -> None:
    for path in (
        SOURCE / "cli.py",
        SOURCE / "engine" / "run.py",
        ROOT / "scripts" / "lane3_authorization.py",
        ROOT / "scripts" / "exposure_rehearsal_runner.py",
    ):
        assert _calls(path, "authorize") == [], path


def test_only_v3_issuer_constructs_a_valid_grant() -> None:
    references = {
        path.relative_to(SOURCE).as_posix(): path.read_text(encoding="utf-8")
        for path in SOURCE.rglob("*.py")
        if "_ISSUED" in path.read_text(encoding="utf-8")
    }
    assert set(references) == {"authorization.py", "authorization_v3.py"}
    assert "ExecutionGrant(\n        _ISSUED," in references["authorization_v3.py"]


def test_effects_cannot_supply_a_second_host_identity_claim() -> None:
    """Identity comes from trusted CP/F2 composition, not Effects kwargs."""
    tree = ast.parse((SOURCE / "engine" / "run.py").read_text(encoding="utf-8"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    effects_methods = {
        node.name
        for node in classes["Effects"].body
        if isinstance(node, ast.FunctionDef)
    }
    assert not effects_methods & {
        "host_identity",
        "host_id",
        "target_identity",
        "target_id",
    }
    constructor = next(
        node
        for node in classes["Executor"].body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    arguments = {arg.arg for arg in constructor.args.args + constructor.args.kwonlyargs}
    assert not arguments & {
        "host_identity",
        "host_id",
        "target_identity",
        "target_id",
        "host_incarnation",
        "host_enrolment_ref",
    }
    check = next(
        node
        for node in classes["Executor"].body
        if isinstance(node, ast.FunctionDef) and node.name == "_require_execution_plan"
    )
    accessed = {
        node.attr for node in ast.walk(check) if isinstance(node, ast.Attribute)
    }
    assert {
        "host_identity",
        "installed_signer_fingerprint",
        "installed_trust_root_version",
    } <= accessed


def test_pre_effect_consumption_is_owned_by_the_fixed_v3_provider() -> None:
    authority = ast.parse((SOURCE / "authorization_v3.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in authority.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "require_committed_consumption_v3"
    )
    consumer_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "consume_dispatch"
    ]
    assert len(consumer_calls) == 1
    assert isinstance(consumer_calls[0].func.value, ast.Attribute)
    assert consumer_calls[0].func.value.attr == "v3_provider"
    assert ast.unparse(function.body[-2]).endswith(
        "grant.v3_provider.consume_dispatch(request=request)"
    )
    assert ast.unparse(function.body[-1]) == "return recovery_ref"
    provider = next(
        node
        for node in authority.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ExecutionAuthorityV3Provider"
    )
    consume = next(
        node
        for node in provider.body
        if isinstance(node, ast.FunctionDef) and node.name == "consume_dispatch"
    )
    assert ast.unparse(consume.returns) == "None"

    run = ast.parse((SOURCE / "engine" / "run.py").read_text(encoding="utf-8"))
    executor = next(
        node
        for node in run.body
        if isinstance(node, ast.ClassDef) and node.name == "Executor"
    )
    methods = {
        node.name: node for node in executor.body if isinstance(node, ast.FunctionDef)
    }
    for name in ("run", "rollback"):
        calls = {
            node.func.attr: node.lineno
            for node in ast.walk(methods[name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"_verify_host_source", "_require_execution_plan"}
        }
        assert calls["_verify_host_source"] < calls["_require_execution_plan"]
        statements = methods[name].body
        consumption_at = next(
            index
            for index, statement in enumerate(statements)
            if isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr == "_require_execution_plan"
        )
        effect_at = next(
            index
            for index in range(consumption_at + 1, len(statements))
            if isinstance(statements[index], ast.Expr)
            and isinstance(statements[index].value, ast.Call)
            and isinstance(statements[index].value.func, ast.Attribute)
            and statements[index].value.func.attr == "_annotate"
        )
        bridge = statements[consumption_at + 1 : effect_at]
        assert bridge
        assert all(
            isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Name | ast.Constant)
            for statement in bridge
        ), "only non-failing assignments may follow consumption before effects"
    pre_effect = methods["_require_execution_plan"]
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "require_committed_consumption_v3"
        for node in ast.walk(pre_effect)
    )
