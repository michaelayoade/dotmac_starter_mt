"""PostgreSQL canaries for the a7 separation-of-duties stamp (pa_0007, D-9)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.test_accounting_payables_isolation import (
    _seed_payment,
    _seed_tenants,
)
from tests.test_accounting_payables_isolation import migrated_finance as _fixture

migrated_finance = _fixture

_APPROVE_SAME = (
    "UPDATE mod_payables.supplier_payments SET status='APPROVED',"
    "approved_by='user:p',approved_at=now(),approval_reference='r' WHERE id=:id"
)


def test_stamp_defaults_to_enforced_and_database_refuses_same_actor(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    payment, _ = _seed_payment(admin_url, tenant)
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        transaction = conn.begin()
        assert (
            conn.scalar(
                text(
                    "SELECT separation_enforced FROM mod_payables.supplier_payments"
                    " WHERE id=:id"
                ),
                {"id": payment},
            )
            is True
        )
        with pytest.raises(DBAPIError):
            conn.execute(text(_APPROVE_SAME), {"id": payment})
        transaction.rollback()
    engine.dispose()


def test_database_relaxes_the_check_only_for_a_row_stamped_off(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    payment, _ = _seed_payment(admin_url, tenant)
    engine = create_engine(admin_url)
    # The stamp cannot be flipped on an existing row (UPDATE trigger).
    with engine.connect() as conn:
        transaction = conn.begin()
        with pytest.raises(DBAPIError, match="cannot be changed"):
            conn.execute(
                text(
                    "UPDATE mod_payables.supplier_payments"
                    " SET separation_enforced=false WHERE id=:id"
                ),
                {"id": payment},
            )
        transaction.rollback()
    # A row prepared under the relaxed policy (inserted with the stamp off) takes
    # the same actor.
    off_payment = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mod_payables.supplier_payments "
                "(id,tenant_id,number,supplier_ref,supplier_name_snapshot,"
                "currency_code,exchange_rate,value_date,cash_account_ref,"
                "payer_instrument_ref,gross_amount,wht_amount,net_amount,"
                "request_fingerprint,separation_enforced,status,prepared_by,"
                "prepared_at) VALUES (:id,:tenant,'SPAY-OFF','party:s','S','NGN',1,"
                "DATE '2026-09-02','account:1010','instrument:1',100,0,100,:fp,"
                "false,'DRAFT','user:p',now())"
            ),
            {"id": off_payment, "tenant": tenant, "fp": "b" * 64},
        )
        conn.execute(text(_APPROVE_SAME), {"id": off_payment})
    # Turning the policy "on" afterwards is refused too.
    with engine.connect() as conn:
        transaction = conn.begin()
        with pytest.raises(DBAPIError, match="cannot be changed"):
            conn.execute(
                text(
                    "UPDATE mod_payables.supplier_payments"
                    " SET separation_enforced=true WHERE id=:id"
                ),
                {"id": off_payment},
            )
        transaction.rollback()
    engine.dispose()


def test_pa_0007_downgrade_refuses_while_any_row_is_relaxed(
    migrated_finance: tuple[str, str],
) -> None:
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    migration = import_module(
        "dotmac_payables.migrations.versions.pa_0007_separation_policy"
    )
    assert migration.down_revision == "pa_0006_tax_release_hist_credit"
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        transaction = conn.begin()
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()  # no relaxed row: allowed
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE"
                    " table_schema='mod_payables' AND table_name='supplier_payments'"
                    " AND column_name='separation_enforced'"
                )
            )
            == 0
        )
        transaction.rollback()
    _seed_payment(admin_url, tenant)
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE mod_payables.supplier_payments"
                " DISABLE TRIGGER supplier_payments_guard_policy"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE mod_payables.supplier_payments"
                " DISABLE TRIGGER supplier_payments_lifecycle"
            )
        )
        conn.execute(
            text("UPDATE mod_payables.supplier_payments SET separation_enforced=false")
        )
    with engine.connect() as conn:
        transaction = conn.begin()
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="forward correction"):
                migration.downgrade()
        transaction.rollback()
    engine.dispose()
