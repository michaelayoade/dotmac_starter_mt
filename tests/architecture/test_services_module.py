"""Structural canaries for the service-instance lifecycle owner."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, SERVICES_MIGRATION_OWNER
from dotmac_services import models, service
from dotmac_services.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/se_0001_service_lifecycle.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_secure_plane_are_exact() -> None:
    assert SERVICES_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("services", "services", "se", "mod_services")
    assert tuple(module.tables) == ("service_instances", "service_lifecycle_events")
    assert tuple(module.platform_tables) == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_services"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques


def test_service_owner_has_no_sibling_or_foreign_application_dependency() -> None:
    forbidden_columns = {
        "price",
        "amount",
        "currency",
        "invoice_id",
        "subscription_id",
        "network_device_id",
        "access_policy_id",
        "fulfillment_status",
    }
    columns = {
        column.name
        for name in module.tables
        for column in models.metadata_table(name).columns
    }
    assert not columns & forbidden_columns
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
                    ("dotmac_services", "dotmac_kernel")
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
    assert ast.literal_eval(assigned["revision"]) == "se_0001_service_lifecycle"
    assert ast.literal_eval(assigned["down_revision"]) is None
    assert ast.literal_eval(assigned["branch_labels"]) == ("services",)
    assert ast.literal_eval(assigned["REQUIRES"]) == tuple(module.requires)
    for table in module.tables:
        qualified = f"mod_services.{table}"
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
