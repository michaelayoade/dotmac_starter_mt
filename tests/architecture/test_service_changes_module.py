"""Structural canaries for durable service-change requests."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    SERVICE_CHANGES_MIGRATION_OWNER,
)
from dotmac_service_changes import models, service
from dotmac_service_changes.contracts import EXECUTION_ORDER, ExecutionState
from dotmac_service_changes.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/sch_0001_service_changes.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_tenant_plane_are_exact() -> None:
    assert SERVICE_CHANGES_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("service_changes", "servicechanges", "sch", "mod_servicechanges")
    assert tuple(module.tables) == (
        "service_change_requests",
        "service_change_checkpoints",
    )
    assert module.platform_tables == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_servicechanges"
        assert table.c.tenant_id.nullable is False
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in unique_columns


def test_crossed_owners_are_checkpoint_rows_not_request_columns() -> None:
    """The shape change from the source, pinned.

    Sub carried each crossed owner as a nullable FK column on the request. That
    cannot say WHEN a domain was reached, cannot hold two observations for one
    domain, and grows a column per new collaborator. A column reappearing here
    means the row-per-checkpoint design has been quietly undone.
    """
    request_columns = set(models.metadata_table("service_change_requests").c.keys())
    forbidden = {
        "service_qualification_id",
        "field_fee_invoice_id",
        "field_fee_payment_id",
        "service_order_id",
        "work_order_id",
        "provisioning_readiness_decision_id",
        "remote_radius_profile_id",
        "remote_radius_user_id",
        "account_adjustment_id",
        "credit_note_id",
        "ledger_entry_id",
    }
    assert not forbidden & request_columns
    checkpoint = models.metadata_table("service_change_checkpoints")
    assert {"domain", "evidence_reference", "facts", "observed_at"} <= set(
        checkpoint.c.keys()
    )


def test_the_execution_chain_is_declared_once_and_excludes_failure() -> None:
    """`FAILED` is reachable from anywhere, so it must not sit in the ordered
    chain — if it did, `advance_execution` would offer it as "the next step"
    from `DELIVERY_VERIFIED` and refuse it everywhere else."""
    assert ExecutionState.FAILED not in EXECUTION_ORDER
    assert EXECUTION_ORDER[0] is ExecutionState.AWAITING_PAYMENT
    assert EXECUTION_ORDER[-1] is ExecutionState.COMPLETED
    assert len(set(EXECUTION_ORDER)) == len(EXECUTION_ORDER)
    assert set(EXECUTION_ORDER) | {ExecutionState.FAILED} == set(ExecutionState)


def test_checkpoints_are_append_only_at_the_orm() -> None:
    source = (ROOT / "models.py").read_text(encoding="utf-8")
    assert 'event.listens_for(ServiceChangeCheckpoint, "before_update")' in source
    assert 'event.listens_for(ServiceChangeCheckpoint, "before_delete")' in source
    assert issubclass(models.ServiceChangeCheckpointImmutableError, RuntimeError)


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
                        ("dotmac_service_changes", "dotmac_kernel")
                    )
                )


def test_root_migration_is_a_forced_rls_lineage_and_passes_the_gate() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in module.tables:
        assert f'op.create_table(\n        "{table}"' in source
    assert "for table in _TENANT_TABLES:" in source
    assert "ALTER TABLE mod_servicechanges.{table} ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE mod_servicechanges.{table} FORCE ROW LEVEL SECURITY" in source
    assert (
        "CREATE POLICY {table}_tenant_isolation ON mod_servicechanges.{table}" in source
    )
    assert "mod_servicechanges.{table} TO app_user" in source

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
