"""Structural canaries for versioned escalation policy and instances."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    OPERATIONAL_ESCALATIONS_MIGRATION_OWNER,
)
from dotmac_operational_escalations import models, service
from dotmac_operational_escalations.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/oe_0001_escalation_policy.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_tenant_plane_are_exact() -> None:
    assert OPERATIONAL_ESCALATIONS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("operational_escalations", "escalations", "oe", "mod_escalations")
    assert tuple(module.tables) == (
        "escalation_policies",
        "escalation_policy_versions",
        "escalation_instances",
    )
    assert module.platform_tables == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_escalations"
        assert table.c.tenant_id.nullable is False
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in unique_columns


def test_one_active_version_is_enforced_by_the_database_not_the_writer() -> None:
    """A writer-only rule loses to a concurrent activation, and "which terms
    apply now" would then be answered by ordering."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert "uq_escalation_policy_versions_one_active" in source
    assert "unique=True" in source
    assert "state = 'ACTIVE'" in source


def test_an_instance_binds_a_version_rather_than_a_policy() -> None:
    """The whole reason versions exist: an escalation must still be readable
    against the terms it was raised under after the policy moves on."""
    instance = models.metadata_table("escalation_instances")
    assert "policy_version_id" in instance.c
    assert "policy_id" not in instance.c
    assert "level" in instance.c


def test_the_module_owns_no_delivery_and_no_roster() -> None:
    columns = set()
    for name in module.tables:
        columns |= set(models.metadata_table(name).c.keys())
    forbidden = {
        "delivery_status",
        "dedup_sent_at",
        "room_link_id",
        "room_provider",
        "owner_person_id",
        "watcher_person_id",
        "message_body",
        "recipient_address",
    }
    assert not forbidden & columns
    assert "escalation_deliveries" not in module.tables


def test_published_version_terms_cannot_be_rewritten() -> None:
    source = (ROOT / "models.py").read_text(encoding="utf-8")
    assert 'event.listens_for(EscalationPolicyVersion, "before_update")' in source
    assert issubclass(models.PolicyVersionImmutableError, RuntimeError)
    # Only lifecycle may move. Widening this set silently makes an open
    # escalation's terms editable again, which is the source defect.
    assert models._MUTABLE_VERSION_FIELDS == frozenset(
        {"state", "activated_at", "retired_at"}
    )


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
                        ("dotmac_operational_escalations", "dotmac_kernel")
                    )
                )


def test_root_migration_is_a_forced_rls_lineage_and_passes_the_gate() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in module.tables:
        assert f'op.create_table(\n        "{table}"' in source
    assert "for table in _TENANT_TABLES:" in source
    assert "ALTER TABLE mod_escalations.{table} ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE mod_escalations.{table} FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY {table}_tenant_isolation ON mod_escalations.{table}" in source
    assert "mod_escalations.{table} TO app_user" in source

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
