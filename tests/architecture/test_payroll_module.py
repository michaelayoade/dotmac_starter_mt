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
    assert module.version == "0.1.0a1"
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
