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
ADMISSION = ROOT / "migrations/versions/io_0002_queue_admission.py"
SAFETY = ROOT / "migrations/versions/io_0003_operational_safety.py"
# The lineage root created five tables; a2 added admission state; a3 adds
# durable routing evidence while changing lifecycle uniqueness to active-only.
ROOT_TABLES = (
    "inbox_queues",
    "inbox_routing_rules",
    "inbox_agent_presence",
    "conversation_assignments",
    "inbox_workflow_events",
)
ADMISSION_TABLES = ("inbox_queue_entries", "inbox_round_robin_cursors")
SAFETY_TABLES = ("inbox_routing_decisions",)
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
        "inbox_queue_entries",
        "inbox_round_robin_cursors",
        "inbox_routing_decisions",
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
    for table in ROOT_TABLES:
        qualified = f"mod_inbox_ops.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
        assert f"ON {qualified} TO app_user" in source


def test_the_admission_revision_extends_the_same_forced_rls_plane() -> None:
    """A later revision is where a tenant table most often arrives WITHOUT its
    policy: the root migration gets reviewed as a plane, an increment gets
    reviewed as a feature. Both admission tables are checked here for the same
    four properties the root is."""
    source = ADMISSION.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "io_0002_queue_admission"
    assert ast.literal_eval(assigned["down_revision"]) == "io_0001_inbox_operations"
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    assert ast.literal_eval(assigned["_TENANT_TABLES"]) == ADMISSION_TABLES
    assert set(ROOT_TABLES) | set(ADMISSION_TABLES) == set(module.tables)
    assert "for table in _TENANT_TABLES:" in source
    assert "ALTER TABLE mod_inbox_ops.{table} ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE mod_inbox_ops.{table} FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY {table}_tenant_isolation ON mod_inbox_ops.{table}" in source
    assert "mod_inbox_ops.{table} TO app_user" in source


def test_a_stored_queue_position_is_unique_within_its_queue() -> None:
    """The FIFO guarantee only holds if two conversations cannot occupy one
    place; without this the position is decoration."""
    from dotmac_inbox_operations import models

    table = models.metadata_table("inbox_queue_entries")
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("tenant_id", "queue_id", "queue_position") in unique_columns


def test_assignment_and_queue_uniqueness_only_cover_active_work() -> None:
    assignments = models.metadata_table("conversation_assignments")
    entries = models.metadata_table("inbox_queue_entries")
    assignment_index = next(
        index
        for index in assignments.indexes
        if index.name == "uq_conversation_assignments_active_conversation"
    )
    queue_index = next(
        index
        for index in entries.indexes
        if index.name == "uq_inbox_queue_entries_active_conversation"
    )
    assert assignment_index.unique
    assert queue_index.unique
    assert str(assignment_index.dialect_options["postgresql"]["where"]) == (
        "status = 'ASSIGNED'"
    )
    assert str(queue_index.dialect_options["postgresql"]["where"]) == (
        "status = 'QUEUED'"
    )


def test_operational_safety_revision_extends_rls_and_replaces_lifecycle_keys() -> None:
    source = SAFETY.read_text(encoding="utf-8")
    assigned = {
        target.id: node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert ast.literal_eval(assigned["revision"]) == "io_0003_operational_safety"
    assert ast.literal_eval(assigned["down_revision"]) == "io_0002_queue_admission"
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    assert ast.literal_eval(assigned["_TENANT_TABLES"]) == SAFETY_TABLES
    assert set(ROOT_TABLES) | set(ADMISSION_TABLES) | set(SAFETY_TABLES) == set(
        module.tables
    )
    assert "uq_conversation_assignments_tenant_conversation" in source
    assert "uq_conversation_assignments_active_conversation" in source
    assert "status = 'ASSIGNED'" in source
    assert "uq_inbox_queue_entries_tenant_conversation" in source
    assert "uq_inbox_queue_entries_active_conversation" in source
    assert "status = 'QUEUED'" in source
    assert "CREATE FUNCTION mod_inbox_ops.refuse_routing_decision_mutation()" in source
    assert "CREATE TRIGGER inbox_routing_decisions_append_only" in source
    assert "BEFORE UPDATE OR DELETE ON mod_inbox_ops.inbox_routing_decisions" in source
    assert "for table in _TENANT_TABLES:" in source
    assert "ALTER TABLE mod_inbox_ops.{table} ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE mod_inbox_ops.{table} FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY {table}_tenant_isolation ON mod_inbox_ops.{table}" in source


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
