"""Structural canaries for the declared-records compliance owner."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    RECORDS_MIGRATION_OWNER,
    module_schema,
)
from dotmac_records import models, service
from dotmac_records.manifest import module

MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/re_0001_records.py"
MIGRATION_TEXT = MIGRATION.read_text(encoding="utf-8")
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_records_manifest_matches_the_permanent_allocation() -> None:
    assert RECORDS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == RECORDS_MIGRATION_OWNER.owner == "records"
    assert module.short_code == "records"
    assert module.migration_prefix == RECORDS_MIGRATION_OWNER.prefix == "re"
    assert module.migration_branch == "records"
    assert module.db_schema == module_schema("records") == "mod_records"
    assert set(module.requires) == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
    }


def test_records_declares_the_complete_tenant_only_catalog() -> None:
    assert module.platform_tables == ()
    assert set(module.tables) == {
        "record_series_versions",
        "retention_schedule_versions",
        "records",
        "record_trigger_observations",
        "legal_hold_cases",
        "legal_hold_targets",
        "disposition_batches",
        "disposition_items",
        "custody_transfers",
        "preservation_checks",
        "record_events",
    }
    assert set(module.tables) == set(models.TABLES)
    for model in models.ALL_MODELS:
        assert model.__table__.schema == "mod_records"
        tenant_id = model.__table__.columns.get("tenant_id")
        assert tenant_id is not None
        assert not tenant_id.nullable


def test_records_module_foreign_keys_carry_tenant_scope() -> None:
    owned = set(module.tables)
    for model in models.ALL_MODELS:
        for constraint in model.__table__.foreign_key_constraints:
            targets = {element.column.table.name for element in constraint.elements}
            if targets & owned:
                assert "tenant_id" in {column.name for column in constraint.columns}


def test_record_snapshot_uses_opaque_source_and_file_references() -> None:
    columns = set(models.Record.__table__.columns.keys())
    assert {
        "source_owner",
        "source_type",
        "source_id",
        "source_version",
        "file_id",
        "checksum_sha256",
        "series_code",
        "series_version",
        "schedule_code",
        "schedule_version",
    } <= columns
    forbidden = {
        "invoice_id",
        "employee_id",
        "ticket_id",
        "work_order_id",
        "contract_id",
        "storage_key",
        "provider_code",
    }
    for model in models.ALL_MODELS:
        assert not (set(model.__table__.columns.keys()) & forbidden)


def test_migration_creates_rls_and_immutable_evidence_in_one_revision() -> None:
    statements = re.sub(r"\s+", " ", MIGRATION_TEXT)
    for table in module.tables:
        qualified = f"mod_records.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in statements
        assert f"{table}_tenant_isolation" in MIGRATION_TEXT
    assert "protect_record_definition" in MIGRATION_TEXT
    assert "record_series_versions_immutable" in MIGRATION_TEXT
    assert "retention_schedule_versions_immutable" in MIGRATION_TEXT
    assert "records_snapshot_immutable BEFORE UPDATE OR DELETE" in MIGRATION_TEXT
    assert "protect_record_event" in MIGRATION_TEXT
    assert "record_events_append_only" in MIGRATION_TEXT
    assert "disposition_items_immutable_membership" in MIGRATION_TEXT


def test_records_has_no_source_domain_sibling_or_provider_dependency() -> None:
    forbidden_roots = {
        "app",
        "boto3",
        "httpx",
        "requests",
        "dotmac_documents",
        "dotmac_files",
        "dotmac_approvals",
        "dotmac_durable_timers",
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


def test_records_services_do_not_scan_time_or_delete_bytes() -> None:
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "datetime.now(" not in source
    assert "date.today(" not in source
    assert "scan_due" not in source
    assert "delete_object" not in source
    assert "request_deletion(" not in source


def test_records_lineage_passes_the_composed_gate() -> None:
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
