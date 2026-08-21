"""Structural canaries for the reusable controlled-documents owner."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from dotmac_documents import models, service
from dotmac_documents.manifest import module
from dotmac_kernel.namespaces import (
    DOCUMENTS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    module_schema,
)

MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/do_0001_documents.py"
MIGRATION_TEXT = MIGRATION.read_text(encoding="utf-8")
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_documents_manifest_matches_the_permanent_allocation() -> None:
    assert DOCUMENTS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == DOCUMENTS_MIGRATION_OWNER.owner == "documents"
    assert module.short_code == "documents"
    assert module.migration_prefix == DOCUMENTS_MIGRATION_OWNER.prefix == "do"
    assert module.migration_branch == "documents"
    assert module.db_schema == module_schema("documents") == "mod_documents"
    assert set(module.requires) == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
    }


def test_documents_declares_the_complete_tenant_only_catalog() -> None:
    assert module.platform_tables == ()
    assert set(module.tables) == {
        "document_libraries",
        "document_type_versions",
        "documents",
        "document_versions",
        "document_renditions",
        "document_classifications",
        "document_relations",
        "document_checkouts",
        "document_annotations",
        "document_access_grants",
        "document_acknowledgements",
        "document_events",
    }
    assert set(module.tables) == set(models.TABLES)
    for model in models.ALL_MODELS:
        assert model.__table__.schema == "mod_documents"
        tenant_id = model.__table__.columns.get("tenant_id")
        assert tenant_id is not None
        assert not tenant_id.nullable


def test_documents_module_foreign_keys_carry_tenant_scope() -> None:
    owned = set(module.tables)
    for model in models.ALL_MODELS:
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.column.table.name for element in constraint.elements}
            if targets & owned:
                assert "tenant_id" in {column.name for column in constraint.columns}


def test_document_identity_does_not_duplicate_file_or_domain_ownership() -> None:
    forbidden = {
        "storage_key",
        "provider_code",
        "invoice_id",
        "employee_id",
        "ticket_id",
        "work_order_id",
        "contract_id",
        "subscriber_id",
    }
    for model in models.ALL_MODELS:
        assert not (set(model.__table__.columns.keys()) & forbidden)
    version_columns = set(models.DocumentVersion.__table__.columns.keys())
    assert {
        "file_id",
        "checksum_sha256",
        "media_type",
        "byte_length",
    } <= version_columns
    pointer = next(
        constraint
        for constraint in models.Document.__table__.foreign_key_constraints
        if constraint.name == "fk_documents_current_version"
    )
    assert {column.name for column in pointer.columns} == {
        "tenant_id",
        "id",
        "current_version_id",
    }


def test_migration_creates_rls_and_immutable_evidence_in_one_revision() -> None:
    statements = re.sub(r"\s+", " ", MIGRATION_TEXT)
    for table in module.tables:
        qualified = f"mod_documents.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in statements
        assert f"{table}_tenant_isolation" in MIGRATION_TEXT
    assert "protect_document_version" in MIGRATION_TEXT
    assert "document_versions_immutable" in MIGRATION_TEXT
    assert "document_acknowledgements_immutable" in MIGRATION_TEXT
    assert "protect_document_event" in MIGRATION_TEXT
    assert "document_events_append_only" in MIGRATION_TEXT


def test_documents_has_no_sibling_owner_or_provider_dependency() -> None:
    forbidden_roots = {
        "app",
        "boto3",
        "httpx",
        "requests",
        "dotmac_files",
        "dotmac_approvals",
        "dotmac_durable_timers",
        "dotmac_records",
        "dotmac_document_rendering",
    }
    forbidden_calls = {"SessionLocal", "sessionmaker", "create_engine"}
    for path in MODULE_ROOT.rglob("*.py"):
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


def test_documents_services_take_time_and_verdicts_as_inputs() -> None:
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "datetime.now(" not in source
    assert "date.today(" not in source
    assert "scan_due" not in source
    assert "approval_requests" not in source


def test_documents_lineage_passes_the_composed_gate() -> None:
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
