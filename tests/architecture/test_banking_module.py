"""Static contract for the tenant-only banking owner."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import dotmac_banking
from dotmac_banking import models
from dotmac_banking.manifest import module
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-banking"
SOURCE = PACKAGE / "src/dotmac_banking"
MIGRATION = SOURCE / "migrations/versions/bk_0001_banking.py"


def test_manifest_allocates_one_tenant_only_banking_lineage() -> None:
    assert module.code == "banking"
    assert module.version == "0.1.0a1"
    assert module.short_code == "banking"
    assert module.migration_prefix == "bk"
    assert module.migration_branch == "banking"
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == ()
    assert set(module.requires) == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
    }
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]["version"]
    assert declared == dotmac_banking.__version__ == module.version


def test_every_banking_table_has_direct_tenant_identity() -> None:
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


def test_match_policy_reference_is_tenant_composite() -> None:
    constraint = next(
        item
        for item in models.MatchDecision.__table__.foreign_key_constraints
        if tuple(item.column_keys) == ("tenant_id", "policy_id")
    )
    assert tuple(element.target_fullname for element in constraint.elements) == (
        "mod_banking.match_policies.tenant_id",
        "mod_banking.match_policies.id",
    )


def test_accounts_and_observations_are_configured_not_provider_hardcoded() -> None:
    account = models.BankAccount.__table__
    assert "cash_account_ref" in account.c
    assert not account.c.cash_account_ref.foreign_keys
    assert not account.c.institution_id.foreign_keys == set()

    source = "\n".join(
        path.read_text(encoding="utf-8") for path in SOURCE.rglob("*.py")
    ).lower()
    for provider in ("mono", "paystack", "zenith", "uba", "gtbank", "access bank"):
        assert provider not in source
    assert "bank_names.csv" not in source


def test_banking_imports_no_product_or_sibling_domain() -> None:
    forbidden_roots = {
        "app",
        "dotmac_accounting",
        "dotmac_billing",
        "dotmac_finance",
        "dotmac_payroll",
        "dotmac_tax",
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


def test_banking_services_are_flush_only() -> None:
    source = (SOURCE / "service.py").read_text(encoding="utf-8")
    for forbidden in (".commit(", ".rollback(", "SessionLocal(", "sessionmaker("):
        assert forbidden not in source


def test_banking_migration_forces_rls_on_every_declared_table() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    compact = re.sub(r"\s+", " ", source)
    for table in module.tables:
        assert (
            f"ALTER TABLE {module.db_schema}.{table} ENABLE ROW LEVEL SECURITY"
            in compact
        )
        assert (
            f"ALTER TABLE {module.db_schema}.{table} FORCE ROW LEVEL SECURITY"
            in compact
        )
        assert (
            f"CREATE POLICY {table}_tenant_isolation " f"ON {module.db_schema}.{table}"
        ) in compact
        assert f'"{table}"' in source
