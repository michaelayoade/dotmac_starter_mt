"""Structural ownership guards for ``dotmac-forms`` (ADR-0040)."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from dotmac_forms import models
from dotmac_forms.manifest import module
from dotmac_kernel.namespaces import FORMS_MIGRATION_OWNER, MIGRATION_OWNER_LEDGER

MODULE_ROOT = Path(inspect.getfile(models)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/fm_0001_forms.py"


def test_forms_keeps_erp_seven_table_contract_in_one_tenant_lineage() -> None:
    assert FORMS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == FORMS_MIGRATION_OWNER.owner == "forms"
    assert module.short_code == "forms"
    assert module.migration_prefix == "fm"
    assert module.migration_branch == "forms"
    assert module.db_schema == "mod_forms"
    assert module.platform_tables == ()
    assert set(module.tables) == set(models.TENANT_TABLES)
    assert len(module.tables) == 7
    for model in models.TENANT_MODELS:
        assert model.__table__.schema == "mod_forms"
        assert not model.__table__.columns.tenant_id.nullable
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.column.table.schema for element in constraint.elements}
            if "mod_forms" in targets:
                assert "tenant_id" in constraint.columns


def test_forms_migration_enables_and_forces_rls_for_every_declared_table() -> None:
    text = re.sub(r"\s+", " ", MIGRATION.read_text(encoding="utf-8"))
    assert "ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY" in text
    assert "ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY" in text
    assert "CREATE POLICY {table}_tenant_isolation" in text
    assert "UNIQUE (tenant_id, id)" in text


def test_forms_has_no_product_sibling_transport_or_transaction_authority() -> None:
    forbidden = {
        "app",
        "dotmac_workflow_runtime",
        "dotmac_files",
        "httpx",
        "requests",
    }
    forbidden_calls = {"SessionLocal", "sessionmaker", "create_engine"}
    for path in MODULE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".", 1)[0] for a in node.names} & forbidden
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                assert name not in forbidden_calls
    service = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in service
    assert ".rollback(" not in service

