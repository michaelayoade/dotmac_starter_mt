"""Structural and ownership guards for ``dotmac-reseller-management``."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    RESELLER_MANAGEMENT_MIGRATION_OWNER,
    module_schema,
)
from dotmac_reseller_management import models
from dotmac_reseller_management.manifest import module

MODULE_ROOT = Path(inspect.getfile(models)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/rm_0001_reseller_management.py"
MIGRATION_TEXT = MIGRATION.read_text(encoding="utf-8")


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert RESELLER_MANAGEMENT_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == RESELLER_MANAGEMENT_MIGRATION_OWNER.owner == (
        "reseller_management"
    )
    assert module.short_code == "reseller"
    assert module.migration_prefix == RESELLER_MANAGEMENT_MIGRATION_OWNER.prefix == "rm"
    assert module.migration_branch == "reseller_management"
    assert module.db_schema == module_schema("reseller") == "mod_reseller"


def test_reseller_management_is_tenant_only_and_fks_carry_tenant() -> None:
    assert module.platform_tables == ()
    assert set(module.tables) == set(models.TABLES)
    for model in models.ALL_MODELS:
        assert model.__table__.schema == "mod_reseller"
        tenant_id = model.__table__.columns.get("tenant_id")
        assert tenant_id is not None and not tenant_id.nullable
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.column.table.schema for element in constraint.elements}
            if "mod_reseller" in targets:
                assert "tenant_id" in constraint.columns


def test_migration_creates_forced_rls_and_tenant_composite_identity() -> None:
    statements = re.sub(r"\s+", " ", MIGRATION_TEXT)
    tree = ast.parse(MIGRATION_TEXT)
    secured = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_TABLES"
            for target in node.targets
        )
    )
    assert tuple(secured) == tuple(module.tables)
    assert "ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY" in statements
    assert "ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY" in statements
    assert "CREATE POLICY {table}_tenant_isolation" in statements
    assert "UNIQUE (tenant_id, id)" in statements


def test_owner_has_no_product_collaborator_or_transaction_boundary() -> None:
    forbidden_imports = {
        "app",
        "httpx",
        "requests",
        "dotmac_commercial_agreements",
        "dotmac_customers",
        "dotmac_entitlement_allocation",
        "dotmac_parties",
    }
    forbidden_calls = {"SessionLocal", "sessionmaker", "create_engine"}
    for path in MODULE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert not roots & forbidden_imports
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_imports
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                assert name not in forbidden_calls
    service = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in service
    assert ".rollback(" not in service
    for forbidden_field in (
        "commission",
        "payout",
        "invoice",
        "customer_status",
        "password",
    ):
        assert forbidden_field not in (MODULE_ROOT / "models.py").read_text(
            encoding="utf-8"
        )
