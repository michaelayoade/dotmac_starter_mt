from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[1]
SOURCE = PACKAGE / "src" / "dotmac_connector_nextcloud"
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "alembic",
        "asyncio.subprocess",
        "sqlalchemy",
        "subprocess",
    }
)
FORBIDDEN_TEXT = (
    "SessionLocal(",
    "sessionmaker(",
    "db.execute(",
    "db.query(",
    "shell=True",
    "os.system(",
)


def _violations(source: str) -> tuple[str, ...]:
    findings: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots = {node.module or ""}
        else:
            continue
        findings.extend(sorted(roots & FORBIDDEN_IMPORT_ROOTS))
    findings.extend(token for token in FORBIDDEN_TEXT if token in source)
    return tuple(findings)


def test_connector_has_no_persistence_process_or_shell_surface() -> None:
    for path in SOURCE.glob("*.py"):
        assert _violations(path.read_text()) == (), path


def test_boundary_guard_sensitivity_detects_a_planted_violation() -> None:
    assert _violations("import subprocess\nsubprocess.run(['provider'])") == (
        "subprocess",
    )


def test_only_closed_management_routes_are_expressible() -> None:
    transport = (SOURCE / "transport.py").read_text()
    assert "def request(self, path:" not in transport
    assert "arbitrary" not in transport
    assert "argv" not in transport
    assert "command" not in transport
