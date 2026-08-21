"""Structural ownership guards for ``dotmac-workflow-runtime`` (ADR-0040)."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    WORKFLOW_RUNTIME_MIGRATION_OWNER,
)
from dotmac_workflow_runtime import models
from dotmac_workflow_runtime.manifest import module

MODULE_ROOT = Path(inspect.getfile(models)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/wr_0001_runtime.py"


def test_runtime_is_one_tenant_lineage_with_ordered_checkpoint_evidence() -> None:
    assert WORKFLOW_RUNTIME_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == WORKFLOW_RUNTIME_MIGRATION_OWNER.owner == "workflow_runtime"
    assert module.short_code == "workflow"
    assert module.migration_prefix == "wr"
    assert module.migration_branch == "workflow_runtime"
    assert module.db_schema == "mod_workflow"
    assert module.platform_tables == ()
    assert set(module.tables) == set(models.TENANT_TABLES)
    assert set(module.tables) == {
        "workflow_executions",
        "workflow_checkpoints",
        "workflow_repairs",
    }


def test_runtime_migration_forces_rls_and_tenant_composite_foreign_keys() -> None:
    text = re.sub(r"\s+", " ", MIGRATION.read_text(encoding="utf-8"))
    assert "ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY" in text
    assert "ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY" in text
    assert "CREATE POLICY {table}_tenant_isolation" in text
    assert "UNIQUE (tenant_id, id)" in text
    for model in models.TENANT_MODELS:
        assert not model.__table__.columns.tenant_id.nullable
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.column.table.schema for element in constraint.elements}
            if "mod_workflow" in targets:
                assert "tenant_id" in constraint.columns


def test_runtime_cannot_execute_product_effects_or_own_transactions() -> None:
    forbidden = {
        "app",
        "dotmac_forms",
        "dotmac_fulfillment",
        "dotmac_durable_timers",
        "httpx",
        "requests",
        "subprocess",
    }
    for path in MODULE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".", 1)[0] for a in node.names} & forbidden
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden
    service = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    for forbidden_call in (".commit(", ".rollback(", "SessionLocal(", "sessionmaker("):
        assert forbidden_call not in service

