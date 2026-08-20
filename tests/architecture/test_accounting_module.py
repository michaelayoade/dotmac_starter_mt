"""Structural contract for the tenant accounting owner."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_accounting import models, service
from dotmac_accounting.manifest import module
from dotmac_kernel.namespaces import (
    ACCOUNTING_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
)

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/ac_0001_accounting.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_matches_same_change_namespace_allocation() -> None:
    assert ACCOUNTING_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == ACCOUNTING_MIGRATION_OWNER.owner == "accounting"
    assert module.short_code == "accounting"
    assert module.migration_prefix == "ac"
    assert module.migration_branch == "accounting"
    assert module.db_schema == "mod_accounting"
    assert tuple(module.platform_tables) == ()
    assert set(module.requires) == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
        "idempotency_ledger.v1",
    }


def test_every_table_is_tenant_scoped_and_internal_fks_are_composite() -> None:
    assert set(module.tables) == set(models.TABLES)
    for model in models.ALL_MODELS:
        table = model.__table__
        assert table.schema == "mod_accounting"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques, table.name
        for constraint in table.foreign_key_constraints:
            targets = tuple(e.target_fullname for e in constraint.elements)
            if not any(target.startswith("mod_accounting.") for target in targets):
                continue
            assert next(column.name for column in constraint.columns) == "tenant_id"
            assert targets[0].endswith(".tenant_id")


def test_dimensions_are_open_rows_not_fixed_product_columns() -> None:
    journal_line = models.JournalLine.__table__
    forbidden = {"cost_center_id", "project_id", "segment_id", "business_unit_id"}
    assert not (forbidden & set(journal_line.c.keys()))
    assert models.JournalLineDimension.__table__.c.dimension_value_id.nullable is False
    assert models.PostedLedgerDimension.__table__.c.dimension_code.nullable is False


def test_service_owns_no_session_transaction_or_sibling_module() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                assert name not in {
                    "commit",
                    "rollback",
                    "SessionLocal",
                    "sessionmaker",
                }
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name == "app" or name.startswith(("app.", "fastapi")):
                    offenders.append(f"{path.name}: {name}")
                if name.startswith("dotmac_") and not name.startswith(
                    ("dotmac_accounting", "dotmac_kernel")
                ):
                    offenders.append(f"{path.name}: {name}")
    assert not offenders


def test_migration_proves_rls_and_database_immutability() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in module.tables:
        qualified = f"mod_accounting.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
    assert "protect_posted_journal" in source
    assert "protect_immutable_accounting_evidence" in source
    assert "posted_ledger_lines_immutable" in source
    assert "posted_ledger_dimensions_immutable" in source
    assert "period_events_immutable" in source


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
