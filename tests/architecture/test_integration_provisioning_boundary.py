"""Architecture canaries for the provisioning connector boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from dotmac_integration.manifest import module
from dotmac_integration.provisioning_models import PROVISIONING_PLATFORM_TABLES

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "packages/dotmac-integration/src/dotmac_integration/provisioning.py"
MIGRATION = (
    ROOT / "packages/dotmac-integration/src/dotmac_integration/migrations/versions/"
    "ig_0008_provisioning.py"
)


def _session_crossings(source: str) -> list[str]:
    tree = ast.parse(source)
    crossings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("invoke_"):
            continue
        for argument in (*node.args.args, *node.args.kwonlyargs):
            annotation = ast.unparse(argument.annotation) if argument.annotation else ""
            if argument.arg in {"db", "session"} or "Session" in annotation:
                crossings.append(f"{node.name}:{argument.arg}")
    return crossings


def _process_execution_calls(source: str) -> list[str]:
    tree = ast.parse(source)
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(name.split(".")[0] in {"subprocess", "shlex"} for name in names):
                findings.extend(names)
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in {"os.system", "subprocess.call", "subprocess.run", "Popen"}:
                findings.append(name)
    return findings


def _sql_literals(source: str) -> set[str]:
    tree = ast.parse(source)
    statements: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "op.execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            statements.add(node.args[0].value)
    return statements


def test_plugin_io_signature_cannot_receive_a_database_session() -> None:
    assert _session_crossings(SOURCE.read_text()) == []


def test_session_boundary_guard_is_sensitive() -> None:
    assert _session_crossings(
        "from sqlalchemy.orm import Session\n"
        "def invoke_connector(request: object, *, db: Session) -> None: ...\n"
    ) == ["invoke_connector:db"]


def test_provisioning_engine_cannot_execute_arbitrary_processes() -> None:
    assert _process_execution_calls(SOURCE.read_text()) == []


def test_process_execution_guard_is_sensitive() -> None:
    assert _process_execution_calls(
        "import subprocess\ndef invoke_connector(): subprocess.run(['tool'])\n"
    )


def test_provisioning_tables_are_declared_on_platform_plane_only() -> None:
    assert set(PROVISIONING_PLATFORM_TABLES) <= set(module.platform_tables)
    assert set(PROVISIONING_PLATFORM_TABLES).isdisjoint(module.tables)


def test_ig_0008_enforces_platform_boundary_and_receipt_immutability() -> None:
    source = MIGRATION.read_text()
    statements = _sql_literals(source)
    assert 'revision = "ig_0008_provisioning"' in source
    assert 'down_revision = "ig_0007_idempotency_ledger"' in source
    assert "ROW LEVEL SECURITY" not in source
    for table in PROVISIONING_PLATFORM_TABLES:
        assert f'"{table}"' in source
        assert (
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.{table} "
            "TO platform_api;"
        ) in statements
        assert (
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.{table} " "TO app_admin;"
        ) in statements
        assert f"REVOKE ALL ON mod_intg.{table} FROM app_user;" in statements
    assert any(
        "BEFORE UPDATE OR DELETE ON mod_intg.provisioning_receipts" in statement
        for statement in statements
    )
    assert any(
        "BEFORE UPDATE OR DELETE ON mod_intg.provisioning_commands" in statement
        for statement in statements
    )
    assert any(
        "BEFORE UPDATE OR DELETE ON mod_intg.provisioning_command_receipts" in statement
        for statement in statements
    )
