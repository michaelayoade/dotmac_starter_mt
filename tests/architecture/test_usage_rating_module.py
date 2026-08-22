"""Structural canaries for pre-tax usage rating."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    USAGE_RATING_MIGRATION_OWNER,
)
from dotmac_usage_rating import models, service
from dotmac_usage_rating.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/ur_0001_usage_rating.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_secure_plane_are_exact() -> None:
    assert USAGE_RATING_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("usage_rating", "usage_rate", "ur", "mod_usage_rate")
    assert tuple(module.tables) == ("rating_rules", "rated_usage_obligations")
    assert tuple(module.platform_tables) == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_usage_rate"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques


def test_rating_stops_before_billing_tax_and_accounting() -> None:
    forbidden = {
        "invoice_id",
        "invoice_status",
        "tax_amount",
        "tax_code",
        "journal_id",
        "account_id",
        "debit",
        "credit",
        "payment_id",
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
                    ("dotmac_usage_rating", "dotmac_kernel")
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
    assert ast.literal_eval(assigned["revision"]) == "ur_0001_usage_rating"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("usage_rating",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    for table in module.tables:
        qualified = f"mod_usage_rate.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
        assert f"ON {qualified} TO app_user" in source


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
