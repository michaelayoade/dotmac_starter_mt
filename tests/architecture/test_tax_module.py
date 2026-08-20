"""Static contract for the tenant-only tax owner."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import dotmac_tax
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)
from dotmac_tax import models
from dotmac_tax.manifest import module

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-tax"
SOURCE = PACKAGE / "src/dotmac_tax"
MIGRATION = SOURCE / "migrations/versions/tx_0001_tax.py"


def test_manifest_allocates_one_tenant_only_tax_lineage() -> None:
    assert module.code == "tax"
    assert module.version == "0.1.0a1"
    assert module.short_code == "tax"
    assert module.migration_prefix == "tx"
    assert module.migration_branch == "tax"
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == ()
    assert set(module.requires) == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
    }
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]["version"]
    assert declared == dotmac_tax.__version__ == module.version


def test_every_tax_table_has_direct_tenant_identity() -> None:
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


def test_tax_members_rates_calendars_and_boxes_are_data_not_code() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in SOURCE.rglob("*.py")
    )
    for forbidden in (
        "class TaxType",
        "VAT =",
        "WITHHOLDING =",
        "PAYE =",
        "seed_nigeria",
        "7.5%",
        "0.075",
        "21st day",
        "FIRS",
        "NRS",
        "LIRS",
    ):
        assert forbidden not in source
    assert "tax_kind_code" in models.TaxCode.__table__.c
    assert "due_on" in models.TaxFilingObligation.__table__.c
    assert "box_code" in models.StatutoryReportBox.__table__.c


def test_tax_imports_no_product_or_sibling_domain_and_no_gl_foreign_keys() -> None:
    forbidden_roots = {
        "app",
        "dotmac_accounting",
        "dotmac_banking",
        "dotmac_billing",
        "dotmac_payroll",
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
    for model in models.TENANT_MODELS:
        assert "journal_entry_id" not in model.__table__.c
        assert "fiscal_period_id" not in model.__table__.c


def test_tax_services_are_flush_only() -> None:
    source = (SOURCE / "service.py").read_text(encoding="utf-8")
    for forbidden in (".commit(", ".rollback(", "SessionLocal(", "sessionmaker("):
        assert forbidden not in source


def test_tax_migration_forces_rls_and_protects_filing_evidence() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    compact = re.sub(r"\s+", " ", source)
    assert "protect_tax_evidence" in source
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
