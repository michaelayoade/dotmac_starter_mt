"""ERP parity canaries for supplier liabilities and obligations."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.models import Tenant
from dotmac_payables import models
from dotmac_payables.contracts import (
    AccountingReceiptInput,
    ApplyCredit,
    CreateCreditNote,
    CreateSupplierInvoice,
    InvalidAmount,
    InvalidTransition,
    ObligationSchedule,
    PayableLineInput,
    SettlementObservationInput,
)
from dotmac_payables.service import (
    apply_credit_note,
    approve_credit_note,
    approve_invoice,
    build_credit_accounting_consequence,
    build_invoice_accounting_consequence,
    create_credit_note,
    create_invoice,
    record_accounting_receipt,
    record_settlement,
    submit_credit_note,
    submit_invoice,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
NOW = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_payables": None}},
    )
    Tenant.__table__.create(engine)
    IdempotencyRecord.__table__.create(engine)
    for table_name in models.TABLES:
        models.metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT, slug="alpha", name="Alpha"))
        session.flush()
        yield session
    engine.dispose()


def _invoice_command() -> CreateSupplierInvoice:
    return CreateSupplierInvoice(
        number="PINV-2026-0001",
        supplier_ref="party:supplier-1",
        supplier_name_snapshot="Fibre Vendor Ltd",
        supplier_document_number="FV-9081",
        invoice_date=date(2026, 8, 1),
        received_date=date(2026, 8, 2),
        currency_code="NGN",
        exchange_rate=Decimal("1"),
        liability_account_ref="account:2100",
        lines=(
            PayableLineInput(
                description="Fibre cable",
                quantity=Decimal("2"),
                unit_price=Decimal("500"),
                posting_account_ref="account:5100",
                tax_amount=Decimal("75"),
                tax_account_ref="account:2200",
                dimension_refs=(("PROJECT", "ABUJA-FTTH"),),
            ),
        ),
        schedule=(ObligationSchedule(date(2026, 8, 31), Decimal("1075")),),
        procurement_ref="procurement:po-22",
        receipt_evidence_fingerprint="a" * 64,
    )


def _approved_invoice(db: Session):
    scope = TenantScope(TENANT)
    invoice = create_invoice(
        db,
        scope=scope,
        command=_invoice_command(),
        idempotency_key="invoice:create:1",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    submit_invoice(
        db,
        scope=scope,
        invoice_id=invoice.id,
        submitted_by="user:ap",
        submitted_at=NOW,
    )
    approve_invoice(
        db,
        scope=scope,
        invoice_id=invoice.id,
        approval_reference="approval:invoice:1",
        approved_by="user:controller",
        idempotency_key="invoice:approve:1",
        idempotency_expires_at=None,
        approved_at=NOW,
    )
    return scope, invoice


def test_invoice_approval_recognizes_liability_and_due_obligation(db: Session) -> None:
    _, invoice = _approved_invoice(db)
    obligation = db.scalar(select(models.PaymentObligation))
    event = db.scalar(select(models.LiabilityEvent))
    assert invoice.status.value == "APPROVED"
    assert obligation is not None
    assert obligation.original_amount == Decimal("1075.000000")
    assert obligation.outstanding_amount == Decimal("1075.000000")
    assert event is not None and event.amount == Decimal("1075.000000")


def test_standard_invoice_refuses_negative_or_mismatched_schedule(db: Session) -> None:
    scope = TenantScope(TENANT)
    command = _invoice_command()
    bad = replace(
        command,
        schedule=(ObligationSchedule(date(2026, 8, 31), Decimal("1")),),
    )
    with pytest.raises(InvalidAmount, match="schedule"):
        create_invoice(
            db,
            scope=scope,
            command=bad,
            idempotency_key="invoice:bad",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )


def test_invoice_refuses_a_schedule_that_drifts_after_money_rounding(
    db: Session,
) -> None:
    scope = TenantScope(TENANT)
    command = replace(
        _invoice_command(),
        lines=(
            PayableLineInput(
                description="Precision-sensitive item",
                quantity=Decimal("1"),
                unit_price=Decimal("0.000001"),
                posting_account_ref="account:5100",
            ),
        ),
        schedule=(
            ObligationSchedule(date(2026, 8, 31), Decimal("0.0000005")),
            ObligationSchedule(date(2026, 9, 30), Decimal("0.0000005")),
        ),
    )
    with pytest.raises(InvalidAmount, match="rounded payment schedule"):
        create_invoice(
            db,
            scope=scope,
            command=command,
            idempotency_key="invoice:rounding-drift",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )


def test_accounting_consequence_is_balanced_but_payables_does_not_post_it(
    db: Session,
) -> None:
    _, invoice = _approved_invoice(db)
    consequence = build_invoice_accounting_consequence(
        db, tenant_id=TENANT, invoice_id=invoice.id
    )
    assert sum(entry.debit for entry in consequence.entries) == sum(
        entry.credit for entry in consequence.entries
    )
    assert consequence.source.document_id == str(invoice.id)
    assert not hasattr(invoice, "journal_entry_id")


def test_credit_note_reduces_obligation_without_double_recognizing_credit(
    db: Session,
) -> None:
    scope, invoice = _approved_invoice(db)
    obligation = db.scalar(select(models.PaymentObligation))
    assert obligation is not None
    credit = create_credit_note(
        db,
        scope=scope,
        command=CreateCreditNote(
            number="PCN-2026-0001",
            supplier_ref=invoice.supplier_ref,
            supplier_name_snapshot=invoice.supplier_name_snapshot,
            supplier_document_number="FV-CN-1",
            credit_date=date(2026, 8, 5),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref="account:2100",
            original_invoice_id=invoice.id,
            lines=(
                PayableLineInput(
                    description="Returned cable",
                    quantity=Decimal("1"),
                    unit_price=Decimal("200"),
                    posting_account_ref="account:5100",
                ),
            ),
        ),
        idempotency_key="credit:create:1",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    submit_credit_note(
        db,
        scope=scope,
        credit_note_id=credit.id,
        submitted_by="user:ap",
        submitted_at=NOW,
    )
    approve_credit_note(
        db,
        scope=scope,
        credit_note_id=credit.id,
        approval_reference="approval:credit:1",
        approved_by="user:controller",
        idempotency_key="credit:approve:1",
        idempotency_expires_at=None,
        approved_at=NOW,
    )
    apply_credit_note(
        db,
        scope=scope,
        command=ApplyCredit(credit.id, obligation.id, Decimal("200"), "user:ap"),
        idempotency_key="credit:apply:1",
        idempotency_expires_at=None,
        applied_at=NOW,
    )
    assert credit.available_amount == Decimal("0")
    assert obligation.outstanding_amount == Decimal("875")
    assert db.scalar(select(func.count()).select_from(models.LiabilityEvent)) == 2
    consequence = build_credit_accounting_consequence(
        db, tenant_id=TENANT, credit_note_id=credit.id
    )
    assert sum(item.debit for item in consequence.entries) == sum(
        item.credit for item in consequence.entries
    )


def test_settlement_is_an_external_observation_not_a_payment(db: Session) -> None:
    scope, _ = _approved_invoice(db)
    obligation = db.scalar(select(models.PaymentObligation))
    assert obligation is not None
    record_settlement(
        db,
        scope=scope,
        command=SettlementObservationInput(
            obligation_id=obligation.id,
            source_owner="treasury",
            source_reference="payment:445",
            source_version="1",
            source_fingerprint="b" * 64,
            amount=Decimal("500"),
            occurred_at=NOW,
            currency_code="NGN",
        ),
        idempotency_key="settlement:445",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    assert obligation.outstanding_amount == Decimal("575")
    assert obligation.status.value == "PARTIALLY_SETTLED"
    assert (
        db.scalar(select(func.count()).select_from(models.SettlementObservation)) == 1
    )
    with pytest.raises(InvalidAmount, match="outstanding"):
        record_settlement(
            db,
            scope=scope,
            command=SettlementObservationInput(
                obligation_id=obligation.id,
                source_owner="treasury",
                source_reference="payment:446",
                source_version="1",
                source_fingerprint="c" * 64,
                amount=Decimal("1000"),
                occurred_at=NOW,
                currency_code="NGN",
            ),
            idempotency_key="settlement:446",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )


def test_accounting_receipt_is_separate_from_invoice_lifecycle(db: Session) -> None:
    scope, invoice = _approved_invoice(db)
    consequence = build_invoice_accounting_consequence(
        db, tenant_id=TENANT, invoice_id=invoice.id
    )
    receipt = record_accounting_receipt(
        db,
        scope=scope,
        command=AccountingReceiptInput(
            document_kind="supplier_invoice",
            document_id=invoice.id,
            consequence_fingerprint=consequence.fingerprint,
            accounting_reference="journal:JNL-44",
            accounting_evidence_fingerprint="e" * 64,
        ),
        idempotency_key="accounting-receipt:1",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    assert receipt.accounting_reference == "journal:JNL-44"
    assert invoice.status.value == "APPROVED"
    with pytest.raises(InvalidTransition, match="already"):
        record_accounting_receipt(
            db,
            scope=scope,
            command=AccountingReceiptInput(
                document_kind="supplier_invoice",
                document_id=invoice.id,
                consequence_fingerprint=consequence.fingerprint,
                accounting_reference="journal:JNL-OTHER",
                accounting_evidence_fingerprint="0" * 64,
            ),
            idempotency_key="accounting-receipt:2",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )
