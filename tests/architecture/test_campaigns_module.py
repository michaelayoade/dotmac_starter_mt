"""Structural contract for the tenant-only campaigns module."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from dotmac_campaigns import models
from dotmac_campaigns.manifest import module
from dotmac_kernel.namespaces import (
    CAMPAIGNS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    module_schema,
)

MODULE_ROOT = Path(inspect.getfile(models)).parent
MIGRATION = MODULE_ROOT / "migrations" / "versions" / "ca_0001_campaigns.py"
MIGRATION_TEXT = MIGRATION.read_text(encoding="utf-8")


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert CAMPAIGNS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == CAMPAIGNS_MIGRATION_OWNER.owner == "campaigns"
    assert module.short_code == "campaigns"
    assert module.migration_prefix == CAMPAIGNS_MIGRATION_OWNER.prefix == "ca"
    assert module.migration_branch == "campaigns"
    assert module.db_schema == module_schema("campaigns") == "mod_campaigns"


def test_v1_is_tenant_only_and_declares_every_model_table() -> None:
    assert module.platform_tables == ()
    assert set(module.tables) == set(models.TABLES)
    for model in models.ALL_MODELS:
        assert model.__table__.schema == "mod_campaigns"
        tenant_id = model.__table__.columns.get("tenant_id")
        assert tenant_id is not None
        assert not tenant_id.nullable


def test_every_module_foreign_key_is_tenant_composite() -> None:
    table_names = set(models.TABLES)
    for model in models.ALL_MODELS:
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.column.table.name for element in constraint.elements}
            if not targets & table_names:
                continue
            assert {column.name for column in constraint.columns} >= {
                "tenant_id"
            }, f"{model.__name__} has a cross-table FK without tenant_id"


def test_no_product_or_provider_foreign_key_or_column_leaks_into_the_owner() -> None:
    forbidden = {
        "subscriber_id",
        "customer_id",
        "person_id",
        "lead_id",
        "opportunity_id",
        "invoice_id",
        "order_id",
        "subscription_id",
        "conversation_id",
        "message_id",
        "connector_id",
        "smtp_config_id",
        "provider_campaign_id",
    }
    for model in models.ALL_MODELS:
        assert not (set(model.__table__.columns.keys()) & forbidden)


def test_migration_creates_forced_rls_and_composite_identity_for_every_table() -> None:
    statements = re.sub(r"\s+", " ", MIGRATION_TEXT)
    for table in module.tables:
        qualified = f"mod_campaigns.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in statements
        assert f"{table}_tenant_isolation" in MIGRATION_TEXT
    assert "UNIQUE (tenant_id, id)" in statements


def test_migration_protects_snapshots_after_sending_starts() -> None:
    assert "protect_campaign_snapshot" in MIGRATION_TEXT
    assert "campaign_revisions_snapshot_immutable" in MIGRATION_TEXT
    assert "campaign_steps_snapshot_immutable" in MIGRATION_TEXT
    assert "campaign_audiences_snapshot_immutable" in MIGRATION_TEXT
    assert "campaign_recipients_snapshot_immutable" in MIGRATION_TEXT


def test_module_contains_no_scheduler_or_provider_client() -> None:
    forbidden_import_roots = {
        "celery",
        "httpx",
        "requests",
        "boto3",
        "dotmac_template_studio",
        "dotmac_durable_timers",
        "dotmac_ticketing",
        "dotmac_integration",
    }
    forbidden_calls = {"SessionLocal", "sessionmaker", "create_engine"}
    for path in sorted(MODULE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert not roots & forbidden_import_roots, path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_import_roots, path
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                assert name not in forbidden_calls, path

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in MODULE_ROOT.rglob("*.py")
    ).lower()
    assert "scan_due" not in combined
    assert "skip_locked" not in combined
    assert "smtp" not in combined
    assert "create_lead" not in combined


def test_services_never_commit_rollback_or_own_a_session() -> None:
    service = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in service
    assert ".rollback(" not in service
    assert "SessionLocal(" not in service
