"""Architecture canaries for the cross-domain analytics projection owner."""

from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

import dotmac_analytics
from dotmac_analytics import models, service
from dotmac_analytics.manifest import module
from dotmac_kernel.namespaces import (
    ANALYTICS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    NamespaceRegistry,
    revision_id_pattern,
)
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATIONS = MODULE_ROOT / "migrations/versions"
MIGRATION = MIGRATIONS / "ay_0001_analytics.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-analytics"


def _package_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MODULE_ROOT.rglob("*.py"))
    )


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert ANALYTICS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.migration_owner() == ANALYTICS_MIGRATION_OWNER
    assert models.SCHEMA == ANALYTICS_MIGRATION_OWNER.db_schema == "mod_analytics"
    assert ANALYTICS_MIGRATION_OWNER.prefix == "ay"


def test_the_module_is_tenant_only_and_declares_every_model_table() -> None:
    registry = NamespaceRegistry.from_manifests([module])
    assert module.platform_tables == ()
    assert module.tables == models.TENANT_TABLES
    assert registry.declared_tables("mod_analytics") == frozenset(models.TENANT_TABLES)
    assert {model.__tablename__ for model in models.TENANT_MODELS} == set(
        models.TENANT_TABLES
    )


def test_every_table_has_not_null_tenant_and_composite_identity() -> None:
    for model in models.TENANT_MODELS:
        table = model.__table__
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert ("tenant_id", "id") in uniques, table.fullname


def test_no_foreign_key_reaches_a_product_or_sibling_module() -> None:
    permitted = {"tenants", *(f"mod_analytics.{name}" for name in models.TENANT_TABLES)}
    for model in models.TENANT_MODELS:
        for constraint in model.__table__.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            targets = {element.column.table.fullname for element in constraint.elements}
            assert targets <= permitted, (model.__name__, targets)


def test_observations_are_exact_aggregate_facts_not_a_free_form_data_lake() -> None:
    columns = models.MetricObservation.__table__.c
    assert columns.value_numeric.type.precision == 38
    assert columns.value_numeric.type.scale == 12
    assert columns.selector_digest.nullable is False
    for forbidden in (
        "payload",
        "value_json",
        "customer_id",
        "subscriber_id",
        "person_id",
        "email",
        "phone",
        "ip_address",
        "user_agent",
    ):
        assert forbidden not in columns


def test_source_receipts_and_observations_are_append_only_structurally() -> None:
    for model in models.APPEND_ONLY_MODELS:
        assert "updated_at" not in model.__table__.c
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE" in migration
    assert "refuse_mutation" in migration
    for model in models.APPEND_ONLY_MODELS:
        assert (
            f"GRANT SELECT, INSERT ON mod_analytics.{model.__tablename__}" in migration
        )


def test_at_most_once_is_delegated_to_the_kernel_owner() -> None:
    source = inspect.getsource(service.record_batch)
    assert "dotmac_kernel.idempotency" in source
    assert "execute_once(" in source
    assert "content_fingerprint" not in models.MetricIngestReceipt.__table__.c
    assert "idempotency_ledger.v1" in module.requires


def test_ingest_and_rebuild_share_one_tenant_write_lock() -> None:
    lock_source = inspect.getsource(service._serialize_tenant_writes)
    assert "pg_advisory_xact_lock" in lock_source
    for operation in (service.record_batch, service.rebuild_projection):
        assert "_serialize_tenant_writes(" in inspect.getsource(operation)


def test_the_service_never_reads_domain_tables_or_the_clock() -> None:
    source = _package_source()
    for forbidden in (
        "dotmac_billing",
        "dotmac_sales",
        "dotmac_subscriptions",
        "dotmac_ticketing",
        "dotmac_web_analytics",
        "datetime.now",
        "date.today",
        "os.environ",
        "getenv",
    ):
        assert forbidden not in source


def test_no_provider_or_product_switch_enters_the_package() -> None:
    source = _package_source().lower()
    for forbidden in (
        "google_analytics",
        "mixpanel",
        "metabase",
        "power_bi",
        "dotmac_erp",
        "dotmac_sub",
        "dotmac_crm",
    ):
        assert forbidden not in source


def test_the_module_never_owns_sessions_or_transactions() -> None:
    source = _package_source()
    for forbidden in (
        "SessionLocal(",
        "PlatformSessionLocal(",
        "sessionmaker(",
        "create_engine(",
        "db.commit()",
        "db.rollback()",
    ):
        assert forbidden not in source


def test_the_migration_forces_rls_on_every_table() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in models.TENANT_TABLES:
        assert f"ALTER TABLE mod_analytics.{table} ENABLE ROW LEVEL SECURITY;" in source
        assert f"ALTER TABLE mod_analytics.{table} FORCE ROW LEVEL SECURITY;" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source


def test_the_revision_is_a_lineage_root_with_declared_prerequisites() -> None:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision", "REQUIRES"}
    }
    assert revision_id_pattern("ay").match(assignments["revision"])
    assert assignments["down_revision"] is None
    assert tuple(assignments["REQUIRES"]) == module.requires


def test_the_lineage_passes_the_composed_migration_gate() -> None:
    """The reference assembly does not install this optional lineage."""
    from dotmac_kernel.migrations.gate import run_gate

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    report = run_gate(
        [module],
        [
            REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions",
            REPO_ROOT / "alembic/versions",
            MIGRATIONS,
        ],
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )
    assert report.ok, f"composed gate violations: {report.violations}"


def test_the_package_carries_the_product_first_dossier() -> None:
    dossier = (PACKAGE_ROOT / "EXTRACTION.toml").read_text(encoding="utf-8")
    assert 'source_mode = "product-first"' in dossier
    assert "dotmac_erp:app/models/analytics/org_metric_snapshot.py" in dossier
    assert "dotmac_erp:tests/services/test_metric_store.py" in dossier
    assert "dotmac-insights" in dossier


def test_unreleased_version_surfaces_and_adoption_evidence_agree() -> None:
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text("utf-8"))
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text("utf-8"))
    declared = pyproject["tool"]["poetry"]["version"]
    assert declared == dotmac_analytics.__version__ == module.version
    assert dossier["status"] == "audit-complete"
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"]
