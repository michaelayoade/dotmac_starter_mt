"""Structural canaries for payment intent and confirmation correlation."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    PAYMENTS_MIGRATION_OWNER,
)
from dotmac_payments import models, service
from dotmac_payments.manifest import module

ROOT = Path(inspect.getfile(service)).parent
MIGRATION = ROOT / "migrations/versions/pm_0001_payment_intents.py"
MERCHANT_MIGRATION = ROOT / "migrations/versions/pm_0005_merchant_context.py"
CASH_MIGRATION = ROOT / "migrations/versions/pm_0006_cash_qualification.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_and_tenant_plane_are_exact() -> None:
    assert PAYMENTS_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert (
        module.code,
        module.short_code,
        module.migration_prefix,
        module.db_schema,
    ) == ("payments", "payments", "pm", "mod_payments")
    assert tuple(module.tables) == (
        "payment_intents",
        "payment_transfer_proofs",
        "payment_confirmations",
        "merchant_contexts",
        "merchant_context_ends",
        "provider_cash_qualifications",
    )
    assert module.platform_tables == ()
    for name in module.tables:
        table = models.metadata_table(name)
        assert table.schema == "mod_payments"
        assert table.c.tenant_id.nullable is False
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "id") in unique_columns


def test_money_is_exact_and_never_travels_without_its_currency() -> None:
    for name, amounts in (
        ("payment_intents", ("requested_amount", "confirmed_amount")),
        ("payment_transfer_proofs", ("declared_amount",)),
        ("payment_confirmations", ("confirmed_amount",)),
    ):
        table = models.metadata_table(name)
        assert "currency_code" in table.c
        for column in amounts:
            assert type(table.c[column].type).__name__ == "Numeric"
    source = (ROOT / "models.py").read_text(encoding="utf-8")
    assert "Float" not in source


def test_external_reference_uniqueness_has_no_partial_predicate() -> None:
    """The defect this module exists not to repeat.

    Sub's active `external_id` uniqueness required `provider_id IS NOT NULL`,
    which put CRM-origin payments outside it and needed a SECOND partial index
    to stop a concurrent push double-recording cash. A gap class only closes if
    the rule is unconditional, so a predicate here would be the bug returning.
    """
    table = models.metadata_table("payment_confirmations")
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("tenant_id", "provider_type", "external_reference") in unique_columns
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "postgresql_where" not in migration
    assert "sqlite_where" not in migration


def test_the_module_holds_no_receivable_or_bank_state() -> None:
    columns = set()
    for name in module.tables:
        columns |= set(models.metadata_table(name).c.keys())
    forbidden = {
        "invoice_id",
        "credit_note_id",
        "ledger_entry_id",
        "allocation_id",
        "bank_account_id",
        "collection_account_id",
        "provider_fee",
        "refunded_amount",
        "settlement_id",
    }
    assert not forbidden & columns


def test_confirmations_are_append_only_at_the_orm() -> None:
    source = (ROOT / "models.py").read_text(encoding="utf-8")
    assert 'event.listens_for(PaymentConfirmation, "before_update")' in source
    assert 'event.listens_for(PaymentConfirmation, "before_delete")' in source
    assert issubclass(models.PaymentConfirmationImmutableError, RuntimeError)


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
                    and not imported.startswith(("dotmac_payments", "dotmac_kernel"))
                )


def test_root_migration_is_a_forced_rls_lineage_and_passes_the_gate() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    merchant_source = MERCHANT_MIGRATION.read_text(encoding="utf-8")
    cash_source = CASH_MIGRATION.read_text(encoding="utf-8")
    creation_sources = {
        "payment_intents": source,
        "payment_transfer_proofs": source,
        "payment_confirmations": source,
        "merchant_contexts": merchant_source,
        "merchant_context_ends": merchant_source,
        "provider_cash_qualifications": cash_source,
    }
    assert set(creation_sources) == set(module.tables)
    for table, creation_source in creation_sources.items():
        if table == "provider_cash_qualifications":
            assert f'_TABLE = "{table}"' in creation_source
            assert "op.create_table(\n        _TABLE," in creation_source
        else:
            assert f'op.create_table(\n        "{table}"' in creation_source
    assert "for table in _TENANT_TABLES:" in source
    assert "for table in _TABLES:" in merchant_source
    for migration_source in (source, merchant_source):
        assert (
            "ALTER TABLE mod_payments.{table} ENABLE ROW LEVEL SECURITY"
            in migration_source
        )
        assert (
            "ALTER TABLE mod_payments.{table} FORCE ROW LEVEL SECURITY"
            in migration_source
        )
        assert (
            "CREATE POLICY {table}_tenant_isolation ON mod_payments.{table}"
            in migration_source
        )
        assert "mod_payments.{table} TO app_user" in migration_source

    assert '_SCHEMA = "mod_payments"' in cash_source
    assert "ALTER TABLE {_SCHEMA}.{_TABLE} ENABLE ROW LEVEL SECURITY" in cash_source
    assert "ALTER TABLE {_SCHEMA}.{_TABLE} FORCE ROW LEVEL SECURITY" in cash_source
    assert (
        "CREATE POLICY {_TABLE}_tenant_isolation ON {_SCHEMA}.{_TABLE}" in cash_source
    )
    assert "GRANT SELECT, INSERT ON {_SCHEMA}.{_TABLE} TO app_user" in cash_source

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
