"""Static contract for the tenant-only payroll owner."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import dotmac_payroll
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)
from dotmac_payroll import models
from dotmac_payroll.manifest import module

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/dotmac-payroll"
SOURCE = PACKAGE / "src/dotmac_payroll"
MIGRATION = SOURCE / "migrations/versions/py_0001_payroll.py"


def test_manifest_allocates_one_tenant_only_payroll_lineage() -> None:
    assert module.code == "payroll"
    assert module.version == "0.1.0a6"
    assert module.short_code == "payroll"
    assert module.migration_prefix == "py"
    assert module.migration_branch == "payroll"
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == ()
    assert set(module.requires) == {
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
    }
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["poetry"]["version"]
    assert declared == dotmac_payroll.__version__ == module.version


def test_every_payroll_table_has_direct_tenant_identity() -> None:
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


def test_calculation_snapshot_references_stay_inside_the_tenant() -> None:
    expected = {
        ("payroll_calculations", ("tenant_id", "revision_id")): (
            "mod_payroll.pay_structure_revisions.tenant_id",
            "mod_payroll.pay_structure_revisions.id",
        ),
        ("payroll_calculation_lines", ("tenant_id", "component_id")): (
            "mod_payroll.pay_components.tenant_id",
            "mod_payroll.pay_components.id",
        ),
    }
    for model in (models.PayrollCalculation, models.PayrollCalculationLine):
        for columns, targets in (
            (columns, targets)
            for (table, columns), targets in expected.items()
            if table == model.__tablename__
        ):
            constraint = next(
                item
                for item in model.__table__.foreign_key_constraints
                if tuple(item.column_keys) == columns
            )
            assert (
                tuple(element.target_fullname for element in constraint.elements)
                == targets
            )


def test_pay_components_are_data_and_calculations_use_no_expression_eval() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in SOURCE.rglob("*.py")
    )
    for forbidden in (
        "BASIC =",
        "PAYE =",
        "PENSION =",
        "NHF =",
        "eval(",
        "exec(",
        "formula: str",
        "lirs",
        "fctirs",
    ):
        assert forbidden not in source
    assert "component_code" in models.PayComponent.__table__.c
    assert "calculation_method" in models.PayStructureRule.__table__.c


def test_employee_and_account_links_are_opaque_and_siblings_are_not_imported() -> None:
    assert not models.EmployeePayAssignment.__table__.c.employee_ref.foreign_keys
    assert not models.PayComponent.__table__.c.liability_account_ref.foreign_keys
    forbidden_roots = {
        "app",
        "dotmac_banking",
        "dotmac_finance",
        "dotmac_people",
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


def test_payroll_services_are_flush_only() -> None:
    source = (SOURCE / "service.py").read_text(encoding="utf-8")
    for forbidden in (".commit(", ".rollback(", "SessionLocal(", "sessionmaker("):
        assert forbidden not in source


def test_payroll_migration_forces_rls_and_protects_calculation_evidence() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    compact = re.sub(r"\s+", " ", source)
    assert "protect_payroll_evidence" in source
    # The a5 reversal and a6 adjustment tables are created by py_0003 / py_0004
    # (each checked separately below).
    _LATER_TABLES = {"payroll_settlement_reversals", "payroll_liability_adjustments"}
    for table in (x for x in module.tables if x not in _LATER_TABLES):
        assert (
            f"ALTER TABLE {module.db_schema}.{table} ENABLE ROW LEVEL SECURITY"
            in compact
        )
        assert (
            f"ALTER TABLE {module.db_schema}.{table} FORCE ROW LEVEL SECURITY"
            in compact
        )
        assert (
            f"CREATE POLICY {table}_tenant_isolation ON {module.db_schema}.{table}"
        ) in compact
        assert f'"{table}"' in source


def test_payroll_a4_migration_revision_fits_alembic_version_column() -> None:
    migration = SOURCE / "migrations/versions/py_0002_mode_and_run_cancel.py"
    source = migration.read_text(encoding="utf-8")
    assert 'revision = "py_0002_mode_and_run_cancel"' in source
    assert 'down_revision = "py_0001_payroll"' in source
    # Alembic declares alembic_version.version_num as String(32).
    assert len("py_0002_mode_and_run_cancel") <= 32
    for name in (
        "ck_payroll_runs_cancel_present",
        "ck_payroll_runs_cancel_absent",
        "ck_pay_structure_rules_settlement_mode",
        "ck_payroll_calculation_lines_settlement_mode",
        "ck_payroll_liabilities_settlement_mode",
    ):
        assert name in source
        assert len(name) <= 63


def test_payroll_a5_migration_chain_revision_and_append_only_reversal() -> None:
    migration = SOURCE / "migrations/versions/py_0003_settlement_reversal.py"
    source = migration.read_text(encoding="utf-8")
    assert 'revision = "py_0003_settlement_reversal"' in source
    assert 'down_revision = "py_0002_mode_and_run_cancel"' in source
    assert len("py_0003_settlement_reversal") <= 32
    assert "protect_payroll_evidence" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "GRANT SELECT, INSERT ON" in source
    assert "downgrade refused" in source
    assert models.PayrollSettlementReversal.__tablename__ in module.tables


def test_payroll_a6_migration_chain_revision_and_append_only_adjustments() -> None:
    migration = SOURCE / "migrations/versions/py_0004_liability_adjustments.py"
    source = migration.read_text(encoding="utf-8")
    assert 'revision = "py_0004_liability_adjustments"' in source
    assert 'down_revision = "py_0003_settlement_reversal"' in source
    assert len("py_0004_liability_adjustments") <= 32
    assert "protect_payroll_evidence" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "GRANT SELECT, INSERT ON" in source
    assert "downgrade refused" in source
    assert models.PayrollLiabilityAdjustment.__tablename__ in module.tables
