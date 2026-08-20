"""Structural canaries for the tenant-only procurement decision owner.

Written before the implementation.  The assertions pin the boundary that the
source audit drew: Procurement owns purchasing decisions and their evidence,
not Party/supplier identity, product work, Inventory or Accounts Payable.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    PROCUREMENT_MIGRATION_OWNER,
    module_schema,
)
from dotmac_procurement import models
from dotmac_procurement.manifest import module
from sqlalchemy import UniqueConstraint

MODULE_ROOT = Path(inspect.getfile(models)).parent
MIGRATION = MODULE_ROOT / "migrations" / "versions" / "pc_0001_procurement.py"
MIGRATION_TEXT = MIGRATION.read_text(encoding="utf-8")
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert PROCUREMENT_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == PROCUREMENT_MIGRATION_OWNER.owner == "procurement"
    assert module.short_code == "procurement"
    assert module.migration_prefix == PROCUREMENT_MIGRATION_OWNER.prefix == "pc"
    assert module.migration_branch == "procurement"
    assert module.db_schema == module_schema("procurement") == "mod_procurement"


def test_v1_is_tenant_only_and_declares_every_model_table() -> None:
    assert module.platform_tables == ()
    assert set(module.tables) == set(models.TABLES)
    for model in models.ALL_MODELS:
        assert model.__table__.schema == "mod_procurement"
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


def test_each_purchase_commitment_consumes_its_source_once() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in models.PurchaseOrder.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("tenant_id", "source_requisition_id") in unique_columns
    assert ("tenant_id", "source_evaluation_id") in unique_columns
    assert "uq_purchase_orders_tenant_requisition_source" in MIGRATION_TEXT
    assert "uq_purchase_orders_tenant_evaluation_source" in MIGRATION_TEXT
    assert "ck_purchase_orders_source_required" in MIGRATION_TEXT


def test_product_finance_inventory_and_provider_identity_do_not_leak_in() -> None:
    forbidden_columns = {
        "organization_id",
        "subscriber_id",
        "customer_id",
        "project_id",
        "work_order_id",
        "employee_id",
        "supplier_id",
        "vendor_id",
        "inventory_item_id",
        "warehouse_id",
        "goods_receipt_id",
        "supplier_invoice_id",
        "journal_entry_id",
        "connector_id",
        "provider_id",
    }
    for model in models.ALL_MODELS:
        assert not (set(model.__table__.columns.keys()) & forbidden_columns)


def test_migration_creates_forced_rls_for_every_declared_table() -> None:
    statements = re.sub(r"\s+", " ", MIGRATION_TEXT)
    for table in module.tables:
        qualified = f"mod_procurement.{table}"
        assert f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY" in statements
        assert f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY" in statements
        assert f"{table}_tenant_isolation" in MIGRATION_TEXT
        assert f"uq_{table}_tenant_id_id" in MIGRATION_TEXT


def test_database_protects_submitted_offers_and_decision_evidence() -> None:
    assert "protect_requisition_snapshot" in MIGRATION_TEXT
    assert "purchase_requisition_lines_snapshot_immutable" in MIGRATION_TEXT
    assert "sourcing_invitations_snapshot_immutable" in MIGRATION_TEXT
    assert "protect_submitted_bid" in MIGRATION_TEXT
    assert "bid_submissions_immutable_after_submit" in MIGRATION_TEXT
    assert "bid_lines_immutable_after_submit" in MIGRATION_TEXT
    assert "bid_evaluations_content_immutable" in MIGRATION_TEXT
    assert "receipt_observations_append_only" in MIGRATION_TEXT
    assert "protect_procurement_evidence" in MIGRATION_TEXT
    assert "procurement_evidence_append_only" in MIGRATION_TEXT


def test_module_has_no_product_sibling_provider_or_session_owner() -> None:
    forbidden_import_roots = {
        "app",
        "dotmac_approvals",
        "dotmac_inventory",
        "dotmac_people",
        "dotmac_projects",
        "dotmac_integration",
        "httpx",
        "requests",
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


def test_services_never_commit_or_roll_back() -> None:
    service = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert ".commit(" not in service
    assert ".rollback(" not in service


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


def test_lineage_gate_refuses_an_assembly_without_prerequisite_bindings() -> None:
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
