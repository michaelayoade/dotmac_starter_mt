"""Structural canaries for service-delivery orders and readiness evidence."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    SERVICE_ORDERS_MIGRATION_OWNER,
)
from dotmac_service_orders import models, service
from dotmac_service_orders.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/so_0001_service_delivery_orders.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_tenant_plane_are_exact() -> None:
    assert SERVICE_ORDERS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("service_orders", "serviceorders", "so", "mod_serviceorders")
    assert tuple(module.tables) == (
        "service_orders",
        "service_order_readiness_decisions",
        "service_order_readiness_checks",
    )
    assert module.platform_tables == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_serviceorders"
        assert table.c.tenant_id.nullable is False
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in unique_columns


def test_the_module_holds_no_other_owners_facts() -> None:
    """The whole reason this owner is narrower than the journey it closes.

    Sub built its readiness checks by READING Projects, Project Tasks, Work
    Orders and IP Assignments. If any of those facts reappear as columns here,
    the module has quietly become a second copy of another owner's state
    instead of a decision made from observations.
    """
    columns = set()
    for name in module.tables:
        columns |= set(models.metadata_table(name).c.keys())
    forbidden = {
        "project_id",
        "project_task_id",
        "work_order_id",
        "ip_assignment_id",
        "subscription_id",
        "invoice_id",
        "radius_profile_id",
        "service_state",
        "appointment_id",
    }
    assert not forbidden & columns


def test_readiness_evidence_is_append_only_at_the_orm() -> None:
    """The service exposing no update path is not enough on its own — a caller
    holding a decision object could still mutate and flush it."""
    source = (ROOT / "models.py").read_text(encoding="utf-8")
    assert 'event.listens_for(ServiceOrderReadinessDecision, "before_update")' in source
    assert 'event.listens_for(ServiceOrderReadinessDecision, "before_delete")' in source
    assert 'event.listens_for(ServiceOrderReadinessCheck, "before_update")' in source
    assert 'event.listens_for(ServiceOrderReadinessCheck, "before_delete")' in source
    assert issubclass(models.ReadinessEvidenceImmutableError, RuntimeError)


def test_service_is_flush_only_and_sibling_independent() -> None:
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback"}
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for imported in modules:
                assert imported != "app" and not imported.startswith("app.")
                assert not (
                    imported.startswith("dotmac_")
                    and not imported.startswith(
                        ("dotmac_service_orders", "dotmac_kernel")
                    )
                )


def test_root_migration_is_a_forced_rls_lineage_and_passes_the_gate() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in module.tables:
        assert f'op.create_table(\n        "{table}"' in source
    assert "for table in _TENANT_TABLES:" in source
    assert "ALTER TABLE mod_serviceorders.{table} ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE mod_serviceorders.{table} FORCE ROW LEVEL SECURITY" in source
    assert (
        "CREATE POLICY {table}_tenant_isolation ON mod_serviceorders.{table}" in source
    )
    assert "mod_serviceorders.{table} TO app_user" in source

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
