"""Structural canaries for the tenant-only Digital Media owner."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from dotmac_digital_media import models
from dotmac_digital_media.manifest import module
from dotmac_kernel.namespaces import (
    DIGITAL_MEDIA_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    module_schema,
)

MODULE_ROOT = Path(inspect.getfile(models)).parent
REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = MODULE_ROOT / "migrations" / "versions" / "dm_0001_digital_media.py"
MIGRATION_TEXT = MIGRATION.read_text(encoding="utf-8")


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert DIGITAL_MEDIA_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == DIGITAL_MEDIA_MIGRATION_OWNER.owner == "digital_media"
    assert module.short_code == "digitalmedia"
    assert module.migration_prefix == DIGITAL_MEDIA_MIGRATION_OWNER.prefix == "dm"
    assert module.migration_branch == "digital_media"
    assert module.db_schema == module_schema("digitalmedia") == "mod_digitalmedia"


def test_v1_is_tenant_only_and_declares_every_model_table() -> None:
    assert module.platform_tables == ()
    assert set(module.tables) == set(models.TABLES)
    assert len(models.TABLES) == 15
    for model in models.ALL_MODELS:
        assert model.__table__.schema == "mod_digitalmedia"
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
            }, f"{model.__name__} has an unscoped module foreign key"


def test_sibling_and_provider_meaning_does_not_leak_into_the_schema() -> None:
    forbidden_columns = {
        "file_path",
        "object_key",
        "bucket",
        "document_id",
        "content_id",
        "publication_id",
        "record_id",
        "campaign_id",
        "provider_id",
        "connector_id",
    }
    for model in models.ALL_MODELS:
        assert not (set(model.__table__.columns.keys()) & forbidden_columns)


def test_migration_forces_rls_and_composite_identity_on_every_table() -> None:
    statements = re.sub(r"\s+", " ", MIGRATION_TEXT)
    for table in module.tables:
        qualified = f"mod_digitalmedia.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in statements
        assert f"{table}_tenant_isolation" in MIGRATION_TEXT
    assert 'sa.UniqueConstraint("tenant_id", "id"' in MIGRATION_TEXT


def test_immutable_evidence_is_protected_by_database_triggers() -> None:
    for table in (
        "media_revisions",
        "media_metadata_observations",
        "media_rights_versions",
        "media_usage_observations",
        "media_events",
    ):
        assert f"{table}_immutable" in MIGRATION_TEXT
    assert "raise_immutable_digital_media_evidence" in MIGRATION_TEXT


def test_package_contains_no_sibling_provider_or_scheduler_implementation() -> None:
    forbidden_roots = {
        "boto3",
        "celery",
        "httpx",
        "requests",
        "dotmac_approvals",
        "dotmac_content",
        "dotmac_documents",
        "dotmac_durable_timers",
        "dotmac_files",
        "dotmac_integration",
        "dotmac_media_observations",
        "dotmac_publishing",
        "dotmac_records",
    }
    forbidden_calls = {"SessionLocal", "sessionmaker", "create_engine"}
    for path in sorted(MODULE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert not roots & forbidden_roots, path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_roots, path
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                assert name not in forbidden_calls, path

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in MODULE_ROOT.rglob("*.py")
    )
    assert "scan_due" not in combined
    assert "skip_locked" not in combined
    assert "provider_client" not in combined


def test_services_do_not_own_transactions() -> None:
    service = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in service
    assert ".rollback(" not in service
    assert "SessionLocal(" not in service


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


def test_lineage_refuses_an_assembly_without_prerequisite_bindings() -> None:
    from dotmac_kernel.migrations.gate import run_gate

    report = run_gate(
        [module],
        [
            REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            REPO_ROOT / "alembic/versions",
            MIGRATION.parent,
        ],
        bindings=(),
    )
    assert not report.ok
    assert any("binds no provider" in violation for violation in report.violations)


def test_product_first_dossier_names_sources_retirement_and_no_consumers() -> None:
    dossier = (MODULE_ROOT.parents[1] / "EXTRACTION.toml").read_text(encoding="utf-8")
    assert 'source_mode = "product-first"' in dossier
    assert '"dotmac_mkt"' in dossier
    assert "local_copy_retirement" in dossier
    assert "shadow_and_drift" in dossier
    assert "contract_consumers = []" in dossier
