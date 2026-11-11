"""Structural contract for the tenant payables owner."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, PAYABLES_MIGRATION_OWNER
from dotmac_payables import models, service
from dotmac_payables.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/pa_0001_payables.py"
SECOND_MIGRATION = ROOT / "migrations/versions/pa_0002_optional_supplier_doc.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_matches_same_change_namespace_allocation() -> None:
    assert PAYABLES_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.code == PAYABLES_MIGRATION_OWNER.owner == "payables"
    assert module.short_code == "payables"
    assert module.migration_prefix == "pa"
    assert module.migration_branch == "payables"
    assert module.db_schema == "mod_payables"
    assert tuple(module.platform_tables) == ()


def test_every_table_is_tenant_scoped_and_internal_fks_are_composite() -> None:
    assert set(module.tables) == set(models.TABLES)
    for model in models.ALL_MODELS:
        table = model.__table__
        assert table.schema == "mod_payables"
        assert table.c.tenant_id.nullable is False
        uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in uniques, table.name
        for constraint in table.foreign_key_constraints:
            targets = tuple(e.target_fullname for e in constraint.elements)
            if not any(target.startswith("mod_payables.") for target in targets):
                continue
            assert next(column.name for column in constraint.columns) == "tenant_id"
            assert targets[0].endswith(".tenant_id")


def test_payables_contains_no_payment_execution_or_cross_domain_foreign_keys() -> None:
    forbidden_columns = {
        "supplier_id",
        "purchase_order_id",
        "goods_receipt_id",
        "journal_entry_id",
        "bank_account_id",
        "payment_batch_id",
        "provider_id",
        "credential_id",
    }
    for model in models.ALL_MODELS:
        assert not (forbidden_columns & set(model.__table__.c.keys()))

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in ROOT.rglob("*.py")
    )
    assert "dotmac_accounting" not in combined
    assert "httpx" not in combined
    assert "requests" not in combined
    assert "bank_account" not in combined


def test_service_owns_no_session_or_transaction() -> None:
    tree = ast.parse(Path(inspect.getfile(service)).read_text(encoding="utf-8"))
    calls = {
        getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not ({"commit", "rollback", "SessionLocal", "sessionmaker"} & calls)


def test_migration_proves_rls_and_immutable_observations() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for table in set(module.tables) - {"historical_invoice_receipts"}:
        qualified = f"mod_payables.{table}"
        assert f"{qualified} ENABLE ROW LEVEL SECURITY" in source
        assert f"{qualified} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY {table}_tenant_isolation" in source
    assert "protect_immutable_payables_evidence" in source
    for table in (
        "liability_events",
        "credit_applications",
        "settlement_observations",
        "accounting_receipts",
    ):
        assert f"{table}_immutable" in source


def test_supplier_document_number_is_optional_in_model_and_migration() -> None:
    assert models.SupplierInvoice.__table__.c.supplier_document_number.nullable is True
    assert models.CreditNote.__table__.c.supplier_document_number.nullable is True

    source = SECOND_MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "pa_0001_payables"' in source
    for table in ("supplier_invoices", "credit_notes"):
        assert f'"{table}"' in source
    assert '_TABLES = ("supplier_invoices", "credit_notes")' in source
    assert "for table in _TABLES" in source
    assert "for table in reversed(_TABLES)" in source
    assert source.count('"supplier_document_number"') >= 2
    assert source.count("nullable=True") == 1
    assert source.count("nullable=False") == 1


def test_payables_lineage_loads_as_one_alembic_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["pa_0003_historical_invoice"]
    assert scripts.get_bases() == ["pa_0001_payables"]


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


def test_historical_receipt_is_forward_only_and_immutable() -> None:
    source = (MIGRATION.parent / "pa_0003_historical_invoice.py").read_text()
    assert 'down_revision = "pa_0002_optional_supplier_doc"' in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "historical_invoice_receipts_immutable" in source
    assert "GRANT SELECT, INSERT" in source
    table = models.HistoricalInvoiceReceipt.__table__
    assert len(table.foreign_key_constraints) == 4
