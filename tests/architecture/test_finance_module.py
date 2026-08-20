"""Static contract for the tenant-only fixed-asset accounting owner."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import dotmac_finance
from dotmac_finance import models
from dotmac_finance.manifest import module
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-finance"
SOURCE = PACKAGE / "src/dotmac_finance"
MIGRATION = SOURCE / "migrations/versions/fn_0001_asset_accounting.py"


def test_manifest_allocates_one_tenant_only_finance_lineage() -> None:
    assert module.code == "finance"
    assert module.version == "0.1.0a1"
    assert module.short_code == "finance"
    assert module.migration_prefix == "fn"
    assert module.migration_branch == "finance"
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == ()
    assert set(module.requires) == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
    }
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]["version"]
    assert declared == dotmac_finance.__version__ == module.version


def test_every_finance_table_has_direct_tenant_identity() -> None:
    for model in models.TENANT_MODELS:
        columns = model.__table__.columns
        assert "tenant_id" in columns, model.__name__
        assert columns["tenant_id"].nullable is False, model.__name__
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques, model.__name__


def test_asset_and_account_links_are_opaque_and_no_sibling_is_imported() -> None:
    asset_id = models.AssetBook.__table__.c.asset_id
    assert not asset_id.foreign_keys
    forbidden_columns = {
        "physical_state",
        "asset_status",
        "location_id",
        "custodian_id",
        "journal_entry_id",
        "fiscal_period_id",
    }
    for model in models.TENANT_MODELS:
        assert not (set(model.__table__.columns.keys()) & forbidden_columns)

    forbidden_roots = {
        "app",
        "dotmac_assets",
        "dotmac_approvals",
        "dotmac_billing",
        "dotmac_inventory",
    }
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert (
                    not {alias.name.split(".", 1)[0] for alias in node.names}
                    & forbidden_roots
                ), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_roots, path


def test_services_are_flush_only_and_use_no_session_factory() -> None:
    service = (SOURCE / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in service
    assert ".rollback(" not in service
    assert "SessionLocal(" not in service
    assert "sessionmaker(" not in service


def test_migration_forces_rls_and_preserves_accounting_evidence() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    statements = re.sub(r"\s+", " ", source)
    assert "ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in statements
    assert "ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in statements
    assert "CREATE POLICY {table}_tenant_isolation" in statements

    tree = ast.parse(source, filename=str(MIGRATION))
    policy_tables: set[str] = set()
    tenant_identity_constraints = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_tenant_policy"
            for statement in node.body
            for call in ast.walk(statement)
        ):
            assert isinstance(node.iter, ast.Tuple)
            policy_tables.update(
                element.value
                for element in node.iter.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "UniqueConstraint"
            and len(node.args) >= 2
            and all(isinstance(argument, ast.Constant) for argument in node.args[:2])
            and [argument.value for argument in node.args[:2]] == ["tenant_id", "id"]
        ):
            tenant_identity_constraints += 1

    assert policy_tables == set(module.tables)
    assert tenant_identity_constraints == len(module.tables)
    assert "protect_finance_evidence" in source
    assert "assert_balanced_consequence" in source


def test_accounting_vocabulary_refuses_silent_method_fallbacks() -> None:
    contracts = (SOURCE / "contracts.py").read_text(encoding="utf-8")
    assert "UNITS_OF_PRODUCTION" not in contracts
    assert "SUM_OF_YEARS" not in contracts
    calculation = (SOURCE / "calculation.py").read_text(encoding="utf-8")
    assert "default to straight" not in calculation.lower()
