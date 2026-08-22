"""Structural canaries for staffed inbox operations."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_inbox_operations import models, service
from dotmac_inbox_operations.manifest import module
from dotmac_kernel.namespaces import (
    INBOX_OPERATIONS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
)

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/io_0001_inbox_operations.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_secure_plane_are_exact() -> None:
    assert INBOX_OPERATIONS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("inbox_operations", "inbox_ops", "io", "mod_inbox_ops")
    assert tuple(module.tables) == (
        "inbox_queues",
        "inbox_routing_rules",
        "inbox_agent_presence",
        "conversation_assignments",
        "inbox_workflow_events",
    )
    assert tuple(module.platform_tables) == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_inbox_ops"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques


def test_inbox_content_transport_and_workforce_stay_out() -> None:
    forbidden = {
        "message_body",
        "message_content",
        "read_cursor",
        "provider_message_id",
        "webhook_payload",
        "shift_id",
        "work_order_id",
        "payroll_id",
    }
    columns = {
        column.name
        for name in module.tables
        for column in models.metadata_table(name).columns
    }
    assert not columns & forbidden


def test_service_is_flush_only_and_package_independent() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback"}
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name == "app" or name.startswith("app."):
                    offenders.append(name)
                if name.startswith("dotmac_") and not name.startswith(
                    ("dotmac_inbox_operations", "dotmac_kernel")
                ):
                    offenders.append(name)
    assert not offenders


def test_root_migration_declares_the_whole_forced_rls_plane() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "io_0001_inbox_operations"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("inbox_operations",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    for table in module.tables:
        qualified = f"mod_inbox_ops.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
        assert f"ON {qualified} TO app_user" in source


def test_lineage_passes_the_composed_migration_gate() -> None:
    from dotmac_kernel.migrations.gate import run_gate

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    report = run_gate(
        [module],
        [
            REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            REPO_ROOT / "alembic/versions",
            MIGRATION.parent,
        ],
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert report.ok, report.violations
