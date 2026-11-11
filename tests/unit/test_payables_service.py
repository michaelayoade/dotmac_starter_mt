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
from dotmac_kernel.semantic_encoding import CanonicalEncodingError
from dotmac_payables import models
from dotmac_payables.contracts import (
    AccountingReceiptInput,
    ApplyCredit,
    Conflict,
    CreateCreditNote,
    CreateSupplierInvoice,
    InvalidAmount,
    InvalidTransition,
    ObligationSchedule,
    PayableLineInput,
    PayablesError,
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


def test_historical_documents_without_supplier_numbers_persist_as_null(
    db: Session,
) -> None:
    def historical_invoice(number: str) -> models.SupplierInvoice:
        return models.SupplierInvoice(
            tenant_id=TENANT,
            number=number,
            supplier_ref="party:supplier-1",
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number=None,
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref="account:2100",
            subtotal=Decimal("100"),
            tax_amount=Decimal("0"),
            total_amount=Decimal("100"),
            payment_schedule=[{"due_date": "2026-08-31", "amount": "100"}],
            request_fingerprint="a" * 64,
            created_at=NOW,
            updated_at=NOW,
        )

    first = historical_invoice("PINV-2026-0002")
    second = historical_invoice("PINV-2026-0003")
    db.add_all([first, second])
    db.flush()
    credit = models.CreditNote(
        tenant_id=TENANT,
        original_invoice_id=first.id,
        number="PCN-2026-0002",
        supplier_ref="party:supplier-1",
        supplier_name_snapshot="Fibre Vendor Ltd",
        supplier_document_number=None,
        credit_date=date(2026, 8, 5),
        currency_code="NGN",
        exchange_rate=Decimal("1"),
        liability_account_ref="account:2100",
        subtotal=Decimal("25"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("25"),
        available_amount=Decimal("0"),
        request_fingerprint="b" * 64,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(credit)
    db.flush()

    assert first.supplier_document_number is None
    assert second.supplier_document_number is None
    assert credit.supplier_document_number is None
    assert db.scalar(select(func.count()).select_from(models.SupplierInvoice)) == 2
    assert db.scalar(select(func.count()).select_from(models.CreditNote)) == 1


def test_new_supplier_invoice_still_requires_supplier_document_number(
    db: Session,
) -> None:
    with pytest.raises(PayablesError, match="supplier document number is required"):
        create_invoice(
            db,
            scope=TenantScope(TENANT),
            command=replace(
                _invoice_command(),
                number="PINV-2026-0004",
                supplier_document_number="   ",
            ),
            idempotency_key="invoice:missing-doc:rejected",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )


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


def test_historical_admission_requires_trusted_verifier(db: Session) -> None:
    from dotmac_payables import (
        HistoricalUnpaidInvoiceCommand,
        admit_historical_unpaid_invoice,
    )

    command = HistoricalUnpaidInvoiceCommand(
        "legacy", "opaque-1", uuid.uuid4(), "a" * 64, "cv1:" + "b" * 64
    )
    with pytest.raises(InvalidTransition, match="verifier"):
        admit_historical_unpaid_invoice(
            db,
            scope=TenantScope(TENANT),
            command=command,
            verifier=None,
            idempotency_key="historical",
            idempotency_expires_at=None,
            admitted_at=NOW,
        )


def test_approved_without_owner_recognition_has_no_accounting_consequence(
    db: Session,
) -> None:
    scope, invoice = _approved_invoice(db)
    from sqlalchemy import delete

    db.execute(delete(models.LiabilityEvent))
    with pytest.raises(InvalidTransition, match="recognition"):
        build_invoice_accounting_consequence(
            db, tenant_id=scope.tenant_id, invoice_id=invoice.id
        )


def _historical_case(db: Session):
    from dotmac_payables import (
        HistoricalUnpaidInvoiceCommand,
        HistoricalUnpaidInvoiceEvidence,
    )
    from dotmac_payables.contracts import InvoiceStatus

    invoice = create_invoice(
        db,
        scope=TenantScope(TENANT),
        command=_invoice_command(),
        idempotency_key="historical-create",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    invoice.status = InvoiceStatus.APPROVED
    db.flush()
    names = (
        "number",
        "supplier_ref",
        "supplier_name_snapshot",
        "supplier_document_number",
        "invoice_date",
        "received_date",
        "currency_code",
        "exchange_rate",
        "subtotal",
        "tax_amount",
        "total_amount",
        "liability_account_ref",
        "procurement_ref",
        "receipt_evidence_fingerprint",
        "submitted_by",
        "submitted_at",
        "approved_by",
        "approved_at",
        "approval_reference",
    )
    evidence = HistoricalUnpaidInvoiceEvidence(
        tenant_id=TENANT,
        invoice_id=invoice.id,
        source_owner="legacy",
        source_invoice_id="source:1",
        source_snapshot_sha256="a" * 64,
        source_status="POSTED",
        invoice_type="STANDARD",
        paid_amount=Decimal(0),
        allocation_count=0,
        withholding_tax_amount=Decimal(0),
        prepayment_amount=Decimal(0),
        due_date=date(2026, 8, 31),
        original_posted_at=NOW,
        source_journal_ref="source:journal",
        destination_journal_ref="destination:journal",
        accounting_evidence_fingerprint="cv1:" + "b" * 64,
        facts_fingerprint="cv1:" + "c" * 64,
        **{name: getattr(invoice, name) for name in names},
    )
    command = HistoricalUnpaidInvoiceCommand(
        "legacy", "source:1", invoice.id, "a" * 64, evidence.fingerprint()
    )
    return invoice, evidence, command


class _HistoricalVerifier:
    def __init__(self, evidence):
        self.evidence = evidence

    def verify(self, *, scope, command, destination):
        return self.evidence


def _admit(db, evidence, command, key="historical-admit"):
    from dotmac_payables import admit_historical_unpaid_invoice

    return admit_historical_unpaid_invoice(
        db,
        scope=TenantScope(TENANT),
        command=command,
        verifier=_HistoricalVerifier(evidence),
        idempotency_key=key,
        idempotency_expires_at=None,
        admitted_at=NOW,
    )


def test_historical_replay_preserves_invoice_after_settlement_and_key_expiry(
    db: Session,
) -> None:
    from sqlalchemy import delete

    invoice, evidence, command = _historical_case(db)
    # Compare persisted representations on both sides. SQLite drops timezone
    # metadata on reload, including the ORM-generated updated_at default.
    db.refresh(invoice)
    before = {c.name: getattr(invoice, c.name) for c in invoice.__table__.columns}
    receipt = _admit(db, evidence, command)
    obligation = db.get(models.PaymentObligation, receipt.obligation_id)
    assert (
        obligation.original_amount
        == obligation.outstanding_amount
        == evidence.total_amount
    )
    event = db.get(models.LiabilityEvent, receipt.event_id)
    assert event.event_kind == "historical_invoice_recognized"
    # SQLite reloads DateTime(timezone=True) without its UTC offset. The
    # comparison is specific to this SQLite fixture; the JSON evidence below
    # proves that admission preserved the original aware instant.
    assert event.occurred_at.replace(tzinfo=UTC) == NOW
    assert receipt.original_posted_at.replace(tzinfo=UTC) == NOW
    assert receipt.evidence["original_posted_at"] == NOW.isoformat(
        timespec="microseconds"
    )
    assert receipt.evidence["approved_by"] is None
    db.refresh(invoice)
    assert {
        c.name: getattr(invoice, c.name) for c in invoice.__table__.columns
    } == before
    with pytest.raises(InvalidTransition, match="imported accounting"):
        build_invoice_accounting_consequence(
            db, tenant_id=TENANT, invoice_id=invoice.id
        )
    record_settlement(
        db,
        scope=TenantScope(TENANT),
        command=SettlementObservationInput(
            obligation_id=obligation.id,
            source_owner="payments",
            source_reference="settled:1",
            source_version="1",
            source_fingerprint="d" * 64,
            amount=evidence.total_amount,
            occurred_at=NOW,
            currency_code="NGN",
        ),
        idempotency_key="settle:historical",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    db.execute(delete(IdempotencyRecord))
    assert _admit(db, evidence, command, "fresh-key").id == receipt.id
    assert obligation.outstanding_amount == 0
    assert db.scalar(select(func.count()).select_from(models.PaymentObligation)) == 1
    changed = replace(evidence, facts_fingerprint="cv1:" + "e" * 64)
    with pytest.raises(PayablesError, match="permanent historical"):
        _admit(
            db,
            changed,
            replace(command, evidence_fingerprint=changed.fingerprint()),
            "changed-key",
        )


@pytest.mark.parametrize(
    ("change", "error", "match"),
    [
        ({"source_status": "APPROVED"}, InvalidTransition, "POSTED STANDARD"),
        ({"invoice_type": "CREDIT_NOTE"}, InvalidTransition, "POSTED STANDARD"),
        ({"paid_amount": Decimal("1")}, InvalidTransition, "wholly unpaid"),
        ({"allocation_count": 1}, InvalidTransition, "wholly unpaid"),
        ({"withholding_tax_amount": Decimal("1")}, InvalidTransition, "wholly unpaid"),
        ({"prepayment_amount": Decimal("1")}, InvalidTransition, "wholly unpaid"),
        ({"total_amount": Decimal("1075.001")}, InvalidAmount, "two-decimal money"),
        # NaN is refused by cv1 itself before the owner could persist it.
        ({"total_amount": Decimal("NaN")}, CanonicalEncodingError, "finite Decimal"),
        (
            {"supplier_document_number": None},
            Conflict,
            "differs on supplier_document_number",
        ),
        ({"due_date": date(2026, 9, 1)}, Conflict, "payment schedule differs"),
        ({"approved_by": "invented:actor"}, Conflict, "differs on approved_by"),
        ({"subtotal": Decimal("1001")}, InvalidAmount, "do not reconcile"),
    ],
)
def test_historical_admission_refuses_ineligible_or_changed_evidence(
    db: Session, change, error, match
) -> None:
    _, evidence, command = _historical_case(db)
    altered = replace(evidence, **change)
    with pytest.raises(error, match=match):
        _admit(
            db, altered, replace(command, evidence_fingerprint=altered.fingerprint())
        )
    assert (
        db.scalar(select(func.count()).select_from(models.HistoricalInvoiceReceipt))
        == 0
    )
    assert db.scalar(select(func.count()).select_from(models.PaymentObligation)) == 0
    assert db.scalar(select(func.count()).select_from(models.LiabilityEvent)) == 0


@pytest.mark.parametrize("write", ["add", "dirty", "delete", "flush"])
def test_historical_admission_refuses_when_verifier_writes_to_session(
    db: Session, write: str
) -> None:
    from dotmac_payables import admit_historical_unpaid_invoice

    invoice, evidence, command = _historical_case(db)
    db.flush()

    class WritingVerifier:
        def verify(self, *, scope, command, destination):
            if write in {"add", "flush"}:
                destination.add(
                    models.LiabilityEvent(
                        tenant_id=TENANT,
                        event_kind="forged",
                        document_kind="supplier_invoice",
                        document_id=invoice.id,
                        supplier_ref=evidence.supplier_ref,
                        currency_code="NGN",
                        amount=Decimal("1.00"),
                        source_reference="x",
                        source_fingerprint="f" * 64,
                        occurred_at=NOW,
                    )
                )
                if write == "flush":
                    destination.flush()
            elif write == "dirty":
                invoice.approval_reference = "mutated-by-verifier"
            else:
                destination.delete(invoice)
            return evidence

    with pytest.raises(InvalidTransition, match="verifier must not write"):
        admit_historical_unpaid_invoice(
            db,
            scope=TenantScope(TENANT),
            command=command,
            verifier=WritingVerifier(),
            idempotency_key="writing-verifier",
            idempotency_expires_at=None,
            admitted_at=NOW,
        )
    if write == "flush":
        # The savepoint discarded the flushed row; no expunge is needed.
        assert not any(isinstance(row, models.LiabilityEvent) for row in db)
    # Drop the verifier's unflushed writes; nothing was admitted or persisted.
    db.expunge_all()
    assert (
        db.scalar(select(func.count()).select_from(models.HistoricalInvoiceReceipt))
        == 0
    )
    assert db.scalar(select(func.count()).select_from(models.PaymentObligation)) == 0
    assert db.scalar(select(func.count()).select_from(models.LiabilityEvent)) == 0


@pytest.mark.parametrize("core_write", [False, True])
@pytest.mark.parametrize("swallow_refusal", [False, True])
def test_historical_verifier_cannot_commit_callers_transaction(
    db: Session, core_write: bool, swallow_refusal: bool
) -> None:
    from dotmac_payables import admit_historical_unpaid_invoice

    invoice, evidence, command = _historical_case(db)
    invoice_id = invoice.id
    db.flush()
    caller_transaction = db.get_transaction()
    assert caller_transaction is not None and caller_transaction.is_active
    commit_refusals: list[str] = []

    class CommittingVerifier:
        def verify(self, *, scope, command, destination):
            if core_write:
                destination.execute(
                    models.LiabilityEvent.__table__.insert().values(
                        tenant_id=TENANT,
                        event_kind="forged",
                        document_kind="supplier_invoice",
                        document_id=invoice_id,
                        supplier_ref=evidence.supplier_ref,
                        currency_code="NGN",
                        amount=Decimal("1.00"),
                        source_reference="core-write-before-commit",
                        source_fingerprint="f" * 64,
                        occurred_at=NOW,
                    )
                )
            try:
                destination.commit()
            except InvalidTransition as exc:
                commit_refusals.append(str(exc))
                if not swallow_refusal:
                    raise
            return evidence

    with pytest.raises(InvalidTransition, match="verifier must not write"):
        admit_historical_unpaid_invoice(
            db,
            scope=TenantScope(TENANT),
            command=command,
            verifier=CommittingVerifier(),
            idempotency_key="committing-verifier",
            idempotency_expires_at=None,
            admitted_at=NOW,
        )
    assert commit_refusals == ["historical verifier must not write"]
    assert db.get_transaction() is caller_transaction
    assert caller_transaction.is_active
    assert db.get(models.SupplierInvoice, invoice_id) is not None
    for model in (
        models.HistoricalInvoiceReceipt,
        models.PaymentObligation,
        models.LiabilityEvent,
    ):
        assert db.scalar(select(func.count()).select_from(model)) == 0

    # The caller still owns its original flushed, uncommitted invoice. Its
    # rollback must remove that row as well as all verifier/admission writes.
    db.rollback()
    with Session(db.get_bind()) as fresh:
        assert fresh.get(models.SupplierInvoice, invoice_id) is None
        for model in (
            models.HistoricalInvoiceReceipt,
            models.PaymentObligation,
            models.LiabilityEvent,
        ):
            assert fresh.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("end", ["commit", "rollback"])
def test_historical_admission_refuses_when_verifier_ends_its_savepoint(
    db: Session, end: str
) -> None:
    from dotmac_payables import admit_historical_unpaid_invoice

    _, evidence, command = _historical_case(db)
    db.flush()

    class EndingVerifier:
        def verify(self, *, scope, command, destination):
            getattr(destination, end)()
            return evidence

    with pytest.raises(InvalidTransition, match="verifier must not write"):
        admit_historical_unpaid_invoice(
            db,
            scope=TenantScope(TENANT),
            command=command,
            verifier=EndingVerifier(),
            idempotency_key=f"ending-verifier-{end}",
            idempotency_expires_at=None,
            admitted_at=NOW,
        )
    assert (
        db.scalar(select(func.count()).select_from(models.HistoricalInvoiceReceipt))
        == 0
    )
    assert db.scalar(select(func.count()).select_from(models.PaymentObligation)) == 0
    assert db.scalar(select(func.count()).select_from(models.LiabilityEvent)) == 0


def test_historical_receipt_insert_failure_rolls_back_recognition(
    db: Session, monkeypatch
) -> None:
    _, evidence, command = _historical_case(db)
    original = db.add

    def refuse_receipt(row, *args, **kwargs):
        if isinstance(row, models.HistoricalInvoiceReceipt):
            raise ValueError("receipt unavailable")
        return original(row, *args, **kwargs)

    monkeypatch.setattr(db, "add", refuse_receipt)
    with pytest.raises(ValueError, match="receipt unavailable"):
        _admit(db, evidence, command)
    assert db.scalar(select(func.count()).select_from(models.PaymentObligation)) == 0
    assert db.scalar(select(func.count()).select_from(models.LiabilityEvent)) == 0
