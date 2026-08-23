"""Static boundary and lineage canaries for `dotmac-sales`."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages" / "dotmac-sales"
SOURCE = PACKAGE / "src" / "dotmac_sales"
MIGRATION = SOURCE / "migrations" / "versions" / "sa_0001_sales.py"


def test_manifest_owns_only_the_tenant_sales_lineage() -> None:
    from dotmac_sales.manifest import module
    from dotmac_sales.models import SCHEMA, TENANT_TABLES

    assert module.code == "sales"
    assert module.short_code == "sales"
    assert module.migration_prefix == "sa"
    assert module.migration_branch == "sales"
    assert module.tables == TENANT_TABLES
    assert module.platform_tables == ()
    assert SCHEMA == "mod_sales"
    assert set(module.requires) == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
        "idempotency_ledger.v1",
        "outbox_relay.v1",
    }


def test_public_surface_exposes_the_installed_lineage() -> None:
    import dotmac_sales

    assert "versions_dir" in dotmac_sales.__all__
    assert dotmac_sales.versions_dir() == MIGRATION.parent
    assert MIGRATION.is_file()


def test_every_sales_model_is_tenant_scoped_in_its_own_schema() -> None:
    from dotmac_sales.models import (
        TENANT_TABLES,
        Lead,
        LeadOrigin,
        Pipeline,
        PipelineStage,
        Quote,
        QuoteDiscountRevision,
        QuoteLine,
    )

    models = (
        Pipeline,
        PipelineStage,
        Lead,
        LeadOrigin,
        Quote,
        QuoteLine,
        QuoteDiscountRevision,
    )
    assert {model.__tablename__ for model in models} == set(TENANT_TABLES)
    for model in models:
        assert model.__table__.schema == "mod_sales"
        tenant = model.__table__.c.tenant_id
        assert not tenant.nullable
        assert any(
            set(constraint.columns.keys()) == {"tenant_id", "id"}
            for constraint in model.__table__.constraints
        )


def test_handoff_cannot_carry_downstream_owner_ids() -> None:
    from dotmac_sales import AcceptedQuoteHandoffV1, AcceptedQuoteLineV1

    names = {field.name for field in fields(AcceptedQuoteHandoffV1)}
    line_names = {field.name for field in fields(AcceptedQuoteLineV1)}
    forbidden = {
        "sales_order_id",
        "subscriber_id",
        "subscription_id",
        "project_id",
        "work_order_id",
        "invoice_id",
    }
    assert names.isdisjoint(forbidden)
    assert {
        "currency_minor_units",
        "fulfillment_eligibility_requirement_refs",
    } <= names
    assert {
        "price_version_ref",
        "terms_ref",
        "terms_snapshot",
        "specification_ref",
        "taxes",
    } <= line_names


def test_module_never_imports_orders_or_product_assemblies() -> None:
    forbidden_roots = {"app", "dotmac_orders", "dotmac_sub", "dotmac_crm"}
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden_roots), path


def test_services_leave_transaction_authority_to_the_caller() -> None:
    source = (SOURCE / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "SessionLocal" not in source


def test_migration_contains_force_rls_grants_and_immutability_guards() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "app_current_tenant_id()" in source
    assert "REVOKE ALL ON SCHEMA mod_sales FROM platform_api" in source
    assert "sales_quotes_accepted_immutable" in source
    assert "sales_quote_lines_accepted_immutable" in source
    assert "sales_discount_revisions_append_only" in source
    assert "CREATE SCHEMA IF NOT EXISTS mod_sales" in source


def test_extraction_dossier_names_sub_as_first_cutover_and_campaigns_out() -> None:
    dossier = (PACKAGE / "EXTRACTION.toml").read_text(encoding="utf-8")
    assert 'source_mode = "product-first"' in dossier
    assert "dotmac_sub is cutover 1" in dossier
    assert "Keep campaigns unverified and untouched" in dossier
    assert "SalesOrder/order-line ownership outside" in dossier
