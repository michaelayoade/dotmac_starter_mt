"""0.1.0a6 on PostgreSQL: RLS, append-only evidence, locks and the document guard.

Needs ``TEST_MIGRATION_DATABASE_URL`` (``make test-db-up``); skipped otherwise by the
shared ``migrated_finance`` fixture.
"""

# ruff: noqa: F811

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_payables import models as pm
from dotmac_payables.contracts import (
    ApplyCredit,
    Conflict,
    CreateSupplierInvoice,
    CreateSupplierPayment,
    DeclareTaxDeferral,
    HistoricalSupplierCreditCommand,
    HistoricalSupplierCreditEvidence,
    HistoricalTaxDeferralCommand,
    HistoricalTaxDeferralEvidence,
    HistoricalTaxDeferralRelease,
    ObligationSchedule,
    PayableLineInput,
    SupplierPaymentAllocationInput,
)
from dotmac_payables.service import (
    admit_historical_supplier_credit,
    admit_historical_tax_deferral,
    apply_credit_note,
    approve_invoice,
    approve_supplier_payment,
    create_invoice,
    create_supplier_payment,
    declare_tax_deferral,
    record_supplier_payment,
    submit_invoice,
)
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_accounting_payables_isolation import (  # noqa: F401
    _run_pair,
    _seed_tenants,
    migrated_finance,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
DEFERRAL = "account:1455"
RELEASE = "account:1440"
NEW_TABLES = (
    "tax_deferral_policies",
    "supplier_payment_tax_releases",
    "historical_tax_deferrals",
    "historical_credit_receipts",
    "historical_credit_settlements",
    "credit_note_tax_splits",
    "credit_application_tax_releases",
)
_n = {"n": 0}


def _next() -> int:
    _n["n"] += 1
    return _n["n"]


def _declare(admin_url: str, tenant: uuid.UUID) -> uuid.UUID:
    engine = create_engine(admin_url)
    with Session(engine) as db:
        policy = declare_tax_deferral(
            db,
            scope=TenantScope(tenant),
            command=DeclareTaxDeferral(DEFERRAL, RELEASE, 2, "u:fin", "decision"),
            idempotency_key=f"pg:declare:{_next()}",
            idempotency_expires_at=None,
            declared_at=NOW,
        )
        policy_id = policy.id
        db.commit()
    engine.dispose()
    return policy_id


def _invoice(
    admin_url: str,
    tenant: uuid.UUID,
    *,
    schedule: tuple[str, ...] = ("1075",),
    net: str = "1000",
    tax: str = "75",
    imported: bool = False,
    receipt: bool = True,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A committed invoice with deferral tax; approved by the owner or imported."""
    n = _next()
    engine = create_engine(admin_url)
    with Session(engine) as db:
        scope = TenantScope(tenant)
        invoice = create_invoice(
            db,
            scope=scope,
            command=CreateSupplierInvoice(
                number=f"PINV-PG-{n}",
                supplier_ref="party:s",
                supplier_name_snapshot="S",
                supplier_document_number=f"PG-{n}",
                invoice_date=date(2026, 8, 1),
                received_date=date(2026, 8, 2),
                currency_code="NGN",
                exchange_rate=Decimal("1"),
                liability_account_ref="account:2000",
                lines=(
                    PayableLineInput(
                        description="x",
                        quantity=Decimal("1"),
                        unit_price=Decimal(net),
                        posting_account_ref="account:5100",
                        tax_amount=Decimal(tax),
                        tax_account_ref=DEFERRAL,
                    ),
                ),
                schedule=tuple(
                    ObligationSchedule(
                        date(2026, 8, 31) + timedelta(days=30 * i), Decimal(a)
                    )
                    for i, a in enumerate(schedule)
                ),
            ),
            idempotency_key=f"pg:inv:{n}",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )
        submit_invoice(
            db,
            scope=scope,
            invoice_id=invoice.id,
            submitted_by="u:ap",
            submitted_at=NOW,
        )
        invoice_id = invoice.id
        if not imported:
            approve_invoice(
                db,
                scope=scope,
                invoice_id=invoice_id,
                approval_reference="a",
                approved_by="u:c",
                idempotency_key=f"pg:appr:{n}",
                idempotency_expires_at=None,
                approved_at=NOW,
            )
            if receipt:
                db.add(
                    pm.AccountingReceipt(
                        tenant_id=tenant,
                        document_kind="supplier_invoice",
                        document_id=invoice_id,
                        consequence_fingerprint="e" * 64,
                        accounting_reference="j",
                        accounting_evidence_fingerprint="f" * 64,
                        recorded_at=NOW,
                    )
                )
        db.commit()
    if imported:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE mod_payables.supplier_invoices SET status='APPROVED',"
                    "approved_by='legacy:a',approved_at=now(),"
                    "approval_reference='legacy' WHERE id=:id"
                ),
                {"id": invoice_id},
            )
    with Session(engine) as db:
        obligations = [
            row.id
            for row in db.scalars(
                select(pm.PaymentObligation)
                .where(pm.PaymentObligation.invoice_id == invoice_id)
                .order_by(pm.PaymentObligation.sequence)
            )
        ]
    engine.dispose()
    return invoice_id, obligations


def _approved_payment(
    admin_url: str, tenant: uuid.UUID, allocations: list[tuple[uuid.UUID, str]]
) -> uuid.UUID:
    n = _next()
    engine = create_engine(admin_url)
    with Session(engine) as db:
        payment = create_supplier_payment(
            db,
            scope=TenantScope(tenant),
            command=CreateSupplierPayment(
                number=f"SPAY-PG-{n}",
                supplier_ref="party:s",
                supplier_name_snapshot="S",
                currency_code="NGN",
                exchange_rate=Decimal("1"),
                value_date=date(2026, 9, 2),
                cash_account_ref="account:1010",
                payer_instrument_ref="instrument:1",
                allocations=tuple(
                    SupplierPaymentAllocationInput(ob, Decimal(amount))
                    for ob, amount in allocations
                ),
                prepared_by="u:prep",
            ),
            idempotency_key=f"pg:pc:{n}",
            idempotency_expires_at=None,
            prepared_at=NOW,
            separation_of_duties=True,
        )
        payment_id = payment.id
        approve_supplier_payment(
            db,
            scope=TenantScope(tenant),
            payment_id=payment_id,
            approval_reference="ap",
            approved_by="u:appr",
            idempotency_key=f"pg:pa:{n}",
            idempotency_expires_at=None,
            approved_at=NOW,
        )
        db.commit()
    engine.dispose()
    return payment_id


def _recorder(tenant: uuid.UUID, payment_id: uuid.UUID, tag: str):
    return lambda db: record_supplier_payment(
        db,
        scope=TenantScope(tenant),
        payment_id=payment_id,
        bank_reference=f"BR-{tag}",
        recorded_by=f"u:rec-{tag}",
        idempotency_key=f"pg:rec:{tag}",
        idempotency_expires_at=None,
        recorded_at=NOW + timedelta(hours=2),
    ).id


def _amounts(admin_url: str, invoice_id: uuid.UUID) -> list[Decimal]:
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT amount FROM mod_payables.supplier_payment_tax_releases "
                "WHERE invoice_id=:id ORDER BY created_at, line_number"
            ),
            {"id": invoice_id},
        ).all()
    engine.dispose()
    return sorted(Decimal(r[0]) for r in rows)


# --- catalog -------------------------------------------------------------------


def test_new_tables_have_forced_rls_insert_select_grants_and_append_only_triggers(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        for table in NEW_TABLES:
            qualified = f"mod_payables.{table}"
            enabled, forced = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid=to_regclass(:t)"
                ),
                {"t": qualified},
            ).one()
            assert enabled and forced, table
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_policies "
                        "WHERE schemaname='mod_payables' "
                        "AND tablename=:t"
                    ),
                    {"t": table},
                )
                == 1
            ), table
            privileges = {
                privilege: conn.scalar(
                    text("SELECT has_table_privilege('app_user', :t, :p)"),
                    {"t": qualified, "p": privilege},
                )
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            }
            assert privileges == {
                "SELECT": True,
                "INSERT": True,
                "UPDATE": False,
                "DELETE": False,
                "TRUNCATE": False,
            }, table
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE tgrelid=to_regclass(:t) "
                        "AND NOT tgisinternal"
                    ),
                    {"t": qualified},
                )
                >= 1
            ), table
    engine.dispose()


# --- release rows: invariants, append-only, tenant isolation -------------------


def test_release_rows_and_policy_are_append_only_checked_and_tenant_isolated(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_finance
    tenant, other = _seed_tenants(admin_url)
    _declare(admin_url, tenant)
    invoice_id, (ob,) = _invoice(admin_url, tenant)
    payment_id = _approved_payment(admin_url, tenant, [(ob, "1075")])
    engine = create_engine(admin_url)
    with Session(engine) as db:
        _recorder(tenant, payment_id, "a")(db)
        db.commit()
    assert _amounts(admin_url, invoice_id) == [Decimal("75")]
    for sql in (
        "UPDATE mod_payables.supplier_payment_tax_releases SET amount=1",
        "DELETE FROM mod_payables.supplier_payment_tax_releases",
        "UPDATE mod_payables.tax_deferral_policies SET minor_units=3",
        "DELETE FROM mod_payables.tax_deferral_policies",
    ):
        with engine.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text(sql))
    insert = (
        "INSERT INTO mod_payables.supplier_payment_tax_releases "
        "(id,tenant_id,payment_id,line_number,invoice_id,policy_id,"
        "deferral_account_ref,release_account_ref,amount,created_at) "
        "SELECT gen_random_uuid(),tenant_id,payment_id,:line,invoice_id,policy_id,"
        ":deferral,:release,:amount,now() "
        "FROM mod_payables.supplier_payment_tax_releases"
    )
    for params in (
        {"line": 9, "deferral": DEFERRAL, "release": RELEASE, "amount": 0},
        {"line": 9, "deferral": DEFERRAL, "release": DEFERRAL, "amount": 1},
        # the unique key (tenant, payment, invoice, deferral account)
        {"line": 9, "deferral": DEFERRAL, "release": RELEASE, "amount": 1},
    ):
        with engine.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text(insert), params)
    policy_insert = (
        "INSERT INTO mod_payables.tax_deferral_policies "
        "(id,tenant_id,deferral_account_ref,release_account_ref,minor_units,"
        "declared_by,evidence_reference,declared_at) "
        "VALUES (gen_random_uuid(),:t,:d,:r,:m,'u','e',now())"
    )
    for params in (
        {"t": tenant, "d": "account:7", "r": "account:7", "m": 2},
        {"t": tenant, "d": "account:7", "r": "account:8", "m": 7},
        {"t": tenant, "d": DEFERRAL, "r": "account:8", "m": 2},  # unique deferral
    ):
        with engine.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text(policy_insert), params)
    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :t, false)"),
            {"t": str(other)},
        )
        for table in ("supplier_payment_tax_releases", "tax_deferral_policies"):
            assert conn.scalar(text(f"SELECT count(*) FROM mod_payables.{table}")) == 0  # noqa: S608
        with pytest.raises(DBAPIError):
            conn.execute(
                text(policy_insert),
                {"t": tenant, "d": "account:9", "r": "account:8", "m": 2},
            )
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :t, false)"),
            {"t": str(tenant)},
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM mod_payables.supplier_payment_tax_releases")
            )
            == 1
        )
        for sql in (
            "UPDATE mod_payables.tax_deferral_policies SET minor_units=3",
            "DELETE FROM mod_payables.supplier_payment_tax_releases",
        ):
            with pytest.raises(DBAPIError):
                conn.execute(text(sql))
                conn.commit()
    app.dispose()
    engine.dispose()


# --- concurrency: the invoice row lock serialises releases ---------------------


def test_two_payments_on_one_obligation_release_the_deferred_tax_exactly_once(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    _declare(admin_url, tenant)
    invoice_id, (ob,) = _invoice(admin_url, tenant)
    first = _approved_payment(admin_url, tenant, [(ob, "500")])
    second = _approved_payment(admin_url, tenant, [(ob, "575")])
    one, two = _run_pair(
        admin_url, _recorder(tenant, first, "c1"), _recorder(tenant, second, "c2")
    )
    assert one[0] == "ok" and two[0] == "ok", (one, two)
    amounts = _amounts(admin_url, invoice_id)
    assert amounts == [Decimal("34.88"), Decimal("40.12")]
    assert sum(amounts) == Decimal("75")


def test_two_payments_on_two_obligations_of_one_invoice_serialise_on_the_invoice(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    _declare(admin_url, tenant)
    invoice_id, (ob1, ob2) = _invoice(admin_url, tenant, schedule=("600", "475"))
    first = _approved_payment(admin_url, tenant, [(ob1, "600")])
    second = _approved_payment(admin_url, tenant, [(ob2, "475")])
    one, two = _run_pair(
        admin_url, _recorder(tenant, first, "d1"), _recorder(tenant, second, "d2")
    )
    assert one[0] == "ok" and two[0] == "ok", (one, two)
    # Without the invoice lock both payments would see the whole 1075 outstanding.
    assert _amounts(admin_url, invoice_id) == [Decimal("33.14"), Decimal("41.86")]


# --- settled history: deferral position and historical credit -------------------


class _Fixed:
    def __init__(self, evidence):
        self.evidence = evidence

    def verify(self, *, scope, command, destination):
        return self.evidence


def _settled_admission(
    db, tenant: uuid.UUID, invoice_id: uuid.UUID, settlement_id: str
):
    from dotmac_payables.contracts import (
        HistoricalSettledInvoiceCommand,
        HistoricalSettledInvoiceEvidence,
        HistoricalSettlement,
    )
    from dotmac_payables.service import admit_historical_settled_invoice

    invoice = db.get(pm.SupplierInvoice, invoice_id)
    names = (
        "number supplier_ref supplier_name_snapshot supplier_document_number "
        "invoice_date received_date currency_code exchange_rate subtotal "
        "tax_amount total_amount liability_account_ref procurement_ref "
        "receipt_evidence_fingerprint submitted_by submitted_at approved_by "
        "approved_at approval_reference"
    ).split()
    evidence = HistoricalSettledInvoiceEvidence(
        tenant_id=tenant,
        source_owner="legacy",
        source_invoice_id=f"src:{invoice_id}",
        source_snapshot_sha256="a" * 64,
        invoice_id=invoice_id,
        source_status="POSTED",
        invoice_type="STANDARD",
        settlements=(
            HistoricalSettlement(
                settlement_id,
                "PAY-1",
                date(2026, 8, 20),
                Decimal("500.00"),
                Decimal("0.00"),
                "NGN",
                Decimal("1"),
                "cv1:" + "1" * 64,
                "legacy:p1",
                "v2:p1",
            ),
        ),
        settled_amount=Decimal("500.00"),
        residual_amount=Decimal("575.00"),
        withholding_tax_amount=Decimal("0.00"),
        prepayment_amount=Decimal(0),
        due_date=date(2026, 8, 31),
        original_posted_at=datetime(2026, 8, 1, tzinfo=UTC),
        source_journal_ref="s",
        destination_journal_ref="d",
        accounting_evidence_fingerprint="cv1:" + "b" * 64,
        facts_fingerprint="cv1:" + "c" * 64,
        **{name: getattr(invoice, name) for name in names},
    )
    return admit_historical_settled_invoice(
        db,
        scope=TenantScope(tenant),
        command=HistoricalSettledInvoiceCommand(
            "legacy",
            evidence.source_invoice_id,
            invoice_id,
            "a" * 64,
            evidence.fingerprint(),
        ),
        verifier=_Fixed(evidence),
        idempotency_key=f"pg:settled:{invoice_id}",
        idempotency_expires_at=None,
        admitted_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def _admit_settled_with_deferral(
    admin_url: str, tenant: uuid.UUID, invoice_id: uuid.UUID
) -> HistoricalTaxDeferralCommand:
    engine = create_engine(admin_url)
    with Session(engine) as db:
        _settled_admission(db, tenant, invoice_id, f"alloc:{invoice_id}")
        db.commit()
    engine.dispose()
    return HistoricalTaxDeferralCommand(
        "legacy", f"src:{invoice_id}", invoice_id, DEFERRAL, "a" * 64, ""
    )


def test_historical_deferral_is_postgres_append_only_and_bounds_the_release(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    _declare(admin_url, tenant)
    invoice_id, _ = _invoice(admin_url, tenant, imported=True)
    base = _admit_settled_with_deferral(admin_url, tenant, invoice_id)
    evidence = HistoricalTaxDeferralEvidence(
        tenant_id=tenant,
        source_owner="legacy",
        source_invoice_id=base.source_invoice_id,
        source_snapshot_sha256="a" * 64,
        invoice_id=invoice_id,
        deferral_account_ref=DEFERRAL,
        deferred_tax_total=Decimal("75.00"),
        releases=(
            HistoricalTaxDeferralRelease(
                "schedule:7", Decimal("25.00"), "cv1:" + "3" * 64
            ),
        ),
        released_before_admission=Decimal("25.00"),
        remaining=Decimal("50.00"),
        accounting_evidence_fingerprint="cv1:" + "4" * 64,
        facts_fingerprint="cv1:" + "5" * 64,
    )
    engine = create_engine(admin_url)
    with Session(engine) as db:
        record = admit_historical_tax_deferral(
            db,
            scope=TenantScope(tenant),
            command=HistoricalTaxDeferralCommand(
                "legacy",
                base.source_invoice_id,
                invoice_id,
                DEFERRAL,
                "a" * 64,
                evidence.fingerprint(),
            ),
            verifier=_Fixed(evidence),
            idempotency_key="pg:deferral",
            idempotency_expires_at=None,
            admitted_at=datetime(2026, 10, 2, tzinfo=UTC),
        )
        assert record.remaining == Decimal("50")
        db.commit()
    for sql in (
        "UPDATE mod_payables.historical_tax_deferrals SET remaining=0",
        "DELETE FROM mod_payables.historical_tax_deferrals",
    ):
        with engine.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text(sql))
    with engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(  # the CHECK: remaining = total - released_before_admission
            text(
                "INSERT INTO mod_payables.historical_tax_deferrals "
                "(id,tenant_id,invoice_id,deferral_account_ref,source_owner,"
                "source_invoice_id,source_snapshot_sha256,evidence_fingerprint,"
                "facts_fingerprint,accounting_evidence_fingerprint,"
                "deferred_tax_total,released_before_admission,remaining,evidence,"
                "admitted_at) SELECT gen_random_uuid(),tenant_id,invoice_id,"
                "'account:other',source_owner,'x','a','f','f','f',75,25,49,"
                "'{}'::json,now() FROM mod_payables.historical_tax_deferrals"
            )
        )
    (obligation,) = (
        row
        for (row,) in engine.connect().execute(
            text("SELECT id FROM mod_payables.payment_obligations WHERE invoice_id=:i"),
            {"i": invoice_id},
        )
    )
    payment = _approved_payment(admin_url, tenant, [(obligation, "575")])
    with Session(engine) as db:
        _recorder(tenant, payment, "h1")(db)
        db.commit()
    assert _amounts(admin_url, invoice_id) == [Decimal("50")]
    engine.dispose()


def _credit_fixture(admin_url: str, tenant: uuid.UUID):
    """An APPROVED shell invoice (imported) and the evidence of a 100.00 overpayment."""
    from dotmac_payables.contracts import HistoricalSettlement

    n = _next()
    engine = create_engine(admin_url)
    with Session(engine) as db:
        scope = TenantScope(tenant)
        invoice = create_invoice(
            db,
            scope=scope,
            command=CreateSupplierInvoice(
                number=f"PINV-HC-{n}",
                supplier_ref="party:s",
                supplier_name_snapshot="S",
                supplier_document_number=f"HC-{n}",
                invoice_date=date(2026, 8, 1),
                received_date=date(2026, 8, 2),
                currency_code="NGN",
                exchange_rate=Decimal("1"),
                liability_account_ref="account:2000",
                lines=(
                    PayableLineInput(
                        "x", Decimal("1"), Decimal("1000"), "account:5100"
                    ),
                ),
                schedule=(ObligationSchedule(date(2026, 8, 31), Decimal("1000")),),
            ),
            idempotency_key=f"pg:hc:{n}",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )
        submit_invoice(
            db, scope=scope, invoice_id=invoice.id, submitted_by="l:u", submitted_at=NOW
        )
        invoice_id = invoice.id
        db.commit()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mod_payables.supplier_invoices SET status='APPROVED',"
                "approved_by='legacy:a',approved_at=now(),approval_reference='legacy' "
                "WHERE id=:id"
            ),
            {"id": invoice_id},
        )
    settlements = (
        HistoricalSettlement(
            f"hc-alloc:{n}:1",
            "PAY-1",
            date(2026, 8, 20),
            Decimal("1100.00"),
            Decimal("0.00"),
            "NGN",
            Decimal("1"),
            "cv1:" + "1" * 64,
            "legacy:p1",
            "v2:p1",
        ),
    )
    evidence = HistoricalSupplierCreditEvidence(
        tenant_id=tenant,
        source_owner="legacy",
        source_invoice_id=f"src:hc:{n}",
        source_snapshot_sha256="a" * 64,
        invoice_id=invoice_id,
        source_status="POSTED",
        invoice_type="STANDARD",
        number=f"PINV-HC-{n}",
        supplier_ref="party:s",
        supplier_name_snapshot="S",
        currency_code="NGN",
        exchange_rate=Decimal("1"),
        total_amount=Decimal("1000.00"),
        liability_account_ref="account:2000",
        settlements=settlements,
        settled_amount=Decimal("1100.00"),
        withholding_tax_amount=Decimal("0.00"),
        credit_amount=Decimal("100.00"),
        carried_control_amount=Decimal("-100.00"),
        carried_evidence_ref="opening:row-1",
        original_posted_at=datetime(2026, 8, 1, tzinfo=UTC),
        source_journal_ref="s",
        destination_journal_ref="d",
        accounting_evidence_fingerprint="cv1:" + "b" * 64,
        facts_fingerprint="cv1:" + "c" * 64,
    )
    engine.dispose()
    return invoice_id, evidence


def _admit_credit(db, tenant, evidence, *, key="pg:credit", number="HCN-PG-1"):
    return admit_historical_supplier_credit(
        db,
        scope=TenantScope(tenant),
        command=HistoricalSupplierCreditCommand(
            evidence.source_owner,
            evidence.source_invoice_id,
            evidence.invoice_id,
            number,
            evidence.source_snapshot_sha256,
            evidence.fingerprint(),
            "finance:r5b#19",
            "u:fin",
        ),
        verifier=_Fixed(evidence),
        idempotency_key=key,
        idempotency_expires_at=None,
        admitted_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_historical_credit_passes_the_document_guard_and_is_append_only(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_finance
    tenant, other = _seed_tenants(admin_url)
    invoice_id, evidence = _credit_fixture(admin_url, tenant)
    engine = create_engine(admin_url)
    with Session(engine) as db:
        # DRAFT -> SUBMITTED -> APPROVED: legal under protect_payables_document.
        credit = _admit_credit(db, tenant, evidence)
        credit_id = credit.id
        assert credit.status.value == "APPROVED"
        assert credit.available_amount == Decimal("100")
        db.commit()
    with Session(engine) as db:  # replay is a no-op
        assert _admit_credit(db, tenant, evidence, key="pg:credit:2").id == credit_id
        db.commit()
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM mod_payables.credit_notes")) == 1
        assert (
            conn.scalar(
                text("SELECT count(*) FROM mod_payables.historical_credit_settlements")
            )
            == 1
        )
    # The approved credit is itself immutable under the existing document guard.
    for sql in (
        "UPDATE mod_payables.credit_notes SET total_amount=1",
        "DELETE FROM mod_payables.credit_notes",
        "UPDATE mod_payables.historical_credit_receipts SET credit_amount=1",
        "DELETE FROM mod_payables.historical_credit_receipts",
        "UPDATE mod_payables.historical_credit_settlements SET payment_ref='x'",
        "DELETE FROM mod_payables.historical_credit_settlements",
    ):
        with engine.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text(sql))
    # A raw second receipt for the same invoice, or a non-positive credit, is refused.
    with engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO mod_payables.historical_credit_receipts "
                "SELECT * FROM mod_payables.historical_credit_receipts"
            )
        )
    # The credit applies to a new invoice through the unchanged apply path.
    new_invoice, (ob,) = _invoice(admin_url, tenant, tax="0", schedule=("1000",))
    with Session(engine) as db:
        apply_credit_note(
            db,
            scope=TenantScope(tenant),
            command=ApplyCredit(credit_id, ob, Decimal("100"), "u:ap"),
            idempotency_key="pg:apply",
            idempotency_expires_at=None,
            applied_at=NOW,
        )
        db.commit()
    assert new_invoice != invoice_id
    # Tenant isolation for the application role.
    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :t, false)"),
            {"t": str(other)},
        )
        for table in ("historical_credit_receipts", "historical_credit_settlements"):
            assert conn.scalar(text(f"SELECT count(*) FROM mod_payables.{table}")) == 0  # noqa: S608
    app.dispose()
    engine.dispose()


def test_a_credit_settlement_cannot_reuse_a_settled_admission_id_in_the_database(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    _declare(admin_url, tenant)
    settled_invoice, _ = _invoice(admin_url, tenant, imported=True)
    _admit_settled_with_deferral(admin_url, tenant, settled_invoice)
    invoice_id, evidence = _credit_fixture(admin_url, tenant)
    engine = create_engine(admin_url)
    with Session(engine) as db:
        _admit_credit(db, tenant, evidence)
        db.commit()
    # Past the service, the database itself refuses the reuse.
    with engine.connect() as conn, pytest.raises(DBAPIError, match="already admitted"):
        conn.execute(
            text(
                "INSERT INTO mod_payables.historical_credit_settlements "
                "(id,tenant_id,receipt_id,line_number,source_settlement_id,payment_ref,"
                "settled_on,applied_amount,withholding_amount,currency_code,"
                "exchange_rate,source_row_fingerprint,source_journal_ref,"
                "destination_journal_ref,recorded_at) "
                "SELECT gen_random_uuid(),c.tenant_id,c.id,2,h.source_settlement_id,"
                "'p',DATE '2026-08-20',1,0,'NGN',1,'f','s','d',now() "
                "FROM mod_payables.historical_credit_receipts c, "
                "mod_payables.historical_settlements h LIMIT 1"
            )
        )
    # The service refuses it first, with a typed conflict.
    other_invoice, other_evidence = _credit_fixture(admin_url, tenant)
    taken = _taken_settlement_id(engine)
    from dataclasses import replace

    clash = replace(other_evidence.settlements[0], source_settlement_id=taken)
    with Session(engine) as db, pytest.raises(Conflict):
        _admit_credit(
            db,
            tenant,
            replace(other_evidence, settlements=(clash,)),
            key="pg:credit:clash",
            number="HCN-PG-2",
        )
    assert invoice_id != other_invoice
    engine.dispose()


def _taken_settlement_id(engine) -> str:
    with engine.connect() as conn:
        return str(
            conn.scalar(
                text(
                    "SELECT source_settlement_id "
                    "FROM mod_payables.historical_settlements"
                )
            )
        )


# --- the migration ---------------------------------------------------------------


def test_pa_0006_downgrade_is_allowed_empty_and_refused_once_populated(
    migrated_finance: tuple[str, str],
) -> None:
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    migration = import_module(
        "dotmac_payables.migrations.versions.pa_0006_tax_release_hist_credit"
    )
    assert migration.down_revision == "pa_0005_reversal_settled_history"
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        transaction = conn.begin()
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()  # empty: allowed
        for table in NEW_TABLES:
            assert (
                conn.scalar(
                    text("SELECT to_regclass(:t)"), {"t": f"mod_payables.{table}"}
                )
                is None
            )
        assert (
            conn.scalar(
                text(
                    "SELECT to_regprocedure("
                    "'mod_payables.protect_historical_credit_settlement()')"
                )
            )
            is None
        )
        transaction.rollback()
    _declare(admin_url, tenant)  # one permanent row is enough
    with engine.connect() as conn:
        transaction = conn.begin()
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="forward correction"):
                migration.downgrade()
        transaction.rollback()
    engine.dispose()


def test_concurrent_admissions_of_one_settlement_id_admit_exactly_one(
    migrated_finance: tuple[str, str],
) -> None:
    """The advisory lock serialises the two admission paths on a settlement id."""
    from dataclasses import replace

    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    settled_invoice, _ = _invoice(admin_url, tenant, imported=True)
    _, credit_evidence = _credit_fixture(admin_url, tenant)
    shared = "alloc:shared-settlement"
    clash = replace(credit_evidence.settlements[0], source_settlement_id=shared)
    credit_evidence = replace(credit_evidence, settlements=(clash,))

    def credit(db):
        return _admit_credit(db, tenant, credit_evidence, key="pg:race:credit").id

    def settled(db):
        return _settled_admission(db, tenant, settled_invoice, shared).id

    one, two = _run_pair(admin_url, credit, settled)
    outcomes = sorted([one[0], two[0]])
    assert outcomes == ["err", "ok"], (one, two)
    failed = one if one[0] == "err" else two
    assert isinstance(failed[1], Conflict), failed
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        taken = conn.scalar(
            text(
                "SELECT (SELECT count(*) FROM mod_payables.historical_settlements"
                " WHERE source_settlement_id=:s)"
                " + (SELECT count(*) FROM mod_payables.historical_credit_settlements"
                " WHERE source_settlement_id=:s)"
            ),
            {"s": shared},
        )
    engine.dispose()
    assert taken == 1


def test_a_credit_application_and_a_payment_on_one_invoice_serialise(
    migrated_finance: tuple[str, str],
) -> None:
    admin_url, _ = migrated_finance
    tenant, _ = _seed_tenants(admin_url)
    _declare(admin_url, tenant)
    invoice_id, (ob1, ob2) = _invoice(admin_url, tenant, schedule=("600", "475"))
    shell_invoice, evidence = _credit_fixture(admin_url, tenant)
    engine = create_engine(admin_url)
    with Session(engine) as db:
        credit_id = _admit_credit(db, tenant, evidence).id
        db.commit()
    payment = _approved_payment(admin_url, tenant, [(ob1, "600")])

    def apply(db):
        return apply_credit_note(
            db,
            scope=TenantScope(tenant),
            command=ApplyCredit(credit_id, ob2, Decimal("100"), "u:ap"),
            idempotency_key="pg:apply:race",
            idempotency_expires_at=None,
            applied_at=NOW,
        ).id

    one, two = _run_pair(admin_url, _recorder(tenant, payment, "e1"), apply)
    assert one[0] == "ok" and two[0] == "ok", (one, two)
    # The payment ran first (41.86); the application then measured 100 against the
    # 475 still outstanding on the invoice: round(33.14 * 100 / 475) = 6.98.
    assert _amounts(admin_url, invoice_id) == [Decimal("41.86")]
    with engine.connect() as conn:
        applied = [
            Decimal(r[0])
            for r in conn.execute(
                text("SELECT amount FROM mod_payables.credit_application_tax_releases")
            )
        ]
        released = Decimal(
            conn.scalar(
                text(
                    "SELECT coalesce(sum(amount),0) FROM "
                    "mod_payables.supplier_payment_tax_releases"
                )
            )
        )
    engine.dispose()
    assert applied == [Decimal("6.98")]
    assert released + sum(applied) <= Decimal("75")
    assert shell_invoice != invoice_id
