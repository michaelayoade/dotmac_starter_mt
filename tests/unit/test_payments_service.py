"""Payment intent and correlation behavior canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_kernel.money import Money, currency
from dotmac_payments import models
from dotmac_payments.contracts import (
    ConfirmationSource,
    Conflict,
    OpenPaymentIntent,
    PaymentIntentStatus,
    PaymentPurpose,
    RecordConfirmation,
    ReviewTransferProof,
    SubmitTransferProof,
    TransferProofState,
)
from dotmac_payments.models import TENANT_TABLES, PaymentConfirmationImmutableError
from dotmac_payments.service import (
    expire_payment_intent,
    open_payment_intent,
    record_confirmation,
    review_transfer_proof,
    submit_transfer_proof,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 8, 22, tzinfo=UTC)
NGN = currency("NGN")
USD = currency("USD")


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_payments": None}},
    )
    Tenant.__table__.create(engine)
    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="a", name="A"),
                Tenant(id=TENANT_B, slug="b", name="B"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def _intent(db: Session, scope: TenantScope, reference: str = "ref-1", **kwargs):
    return open_payment_intent(
        db,
        scope=scope,
        command=OpenPaymentIntent(
            payer_reference="customer:1",
            purpose=PaymentPurpose.INVOICE_SETTLEMENT,
            requested=Money.of(Decimal("15000.00"), NGN),
            reference=reference,
            provider_type="paystack",
            channel="web",
            **kwargs,
        ),
    )


def test_opening_is_idempotent_on_the_reference(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    first = _intent(db, scope)
    assert _intent(db, scope).id == first.id
    with pytest.raises(Conflict):
        open_payment_intent(
            db,
            scope=scope,
            command=OpenPaymentIntent(
                payer_reference="customer:1",
                purpose=PaymentPurpose.INVOICE_SETTLEMENT,
                requested=Money.of(Decimal("99.00"), NGN),
                reference="ref-1",
                provider_type="paystack",
                channel="web",
            ),
        )


def test_a_confirmation_settles_the_intent_it_names(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    confirmation = record_confirmation(
        db,
        scope=scope,
        command=RecordConfirmation(
            intent_id=intent.id,
            source=ConfirmationSource.PROVIDER_CALLBACK,
            external_reference="ps_12345",
            confirmed=Money.of(Decimal("15000.00"), NGN),
            observed_at=NOW,
        ),
    )
    assert confirmation.intent_id == intent.id
    assert intent.status is PaymentIntentStatus.CONFIRMED
    assert intent.confirmed_amount == Decimal("15000.000000")


def test_a_replayed_callback_is_idempotent_and_cannot_be_stolen(db: Session) -> None:
    """The provider-metadata invariant, in behavior.

    The same external reference must always resolve to the intent it was first
    correlated with. Letting a second intent claim it is how a provider payload
    ends up choosing the destination.
    """
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope, reference="ref-a")
    other = _intent(db, scope, reference="ref-b")
    command = RecordConfirmation(
        intent_id=intent.id,
        source=ConfirmationSource.PROVIDER_CALLBACK,
        external_reference="ps_12345",
        confirmed=Money.of(Decimal("15000.00"), NGN),
        observed_at=NOW,
    )
    first = record_confirmation(db, scope=scope, command=command)
    assert record_confirmation(db, scope=scope, command=command).id == first.id

    with pytest.raises(Conflict):
        record_confirmation(
            db,
            scope=scope,
            command=RecordConfirmation(
                intent_id=other.id,
                source=ConfirmationSource.PROVIDER_CALLBACK,
                external_reference="ps_12345",
                confirmed=Money.of(Decimal("15000.00"), NGN),
                observed_at=NOW,
            ),
        )


def test_a_confirmation_in_another_currency_is_refused(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    with pytest.raises(Conflict):
        record_confirmation(
            db,
            scope=scope,
            command=RecordConfirmation(
                intent_id=intent.id,
                source=ConfirmationSource.PROVIDER_CALLBACK,
                external_reference="ps_9",
                confirmed=Money.of(Decimal("10.00"), USD),
                observed_at=NOW,
            ),
        )


def test_a_confirmation_observed_after_expiry_is_refused(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope, expires_at=NOW + timedelta(hours=1))
    with pytest.raises(Conflict):
        record_confirmation(
            db,
            scope=scope,
            command=RecordConfirmation(
                intent_id=intent.id,
                source=ConfirmationSource.PROVIDER_CALLBACK,
                external_reference="ps_late",
                confirmed=Money.of(Decimal("15000.00"), NGN),
                observed_at=NOW + timedelta(hours=2),
            ),
        )
    expire_payment_intent(
        db, scope=scope, intent_id=intent.id, now=NOW + timedelta(hours=2)
    )
    assert intent.status is PaymentIntentStatus.EXPIRED


def test_an_accepted_transfer_proof_confirms_through_the_one_path(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    proof = submit_transfer_proof(
        db,
        scope=scope,
        command=SubmitTransferProof(
            intent_id=intent.id,
            declared=Money.of(Decimal("15000.00"), NGN),
            document_reference="file:receipt-1",
            submitted_reference="bank-ref-1",
            declared_at=NOW,
        ),
    )
    reviewed = review_transfer_proof(
        db,
        scope=scope,
        command=ReviewTransferProof(
            proof_id=proof.id, accept=True, reviewer="agent:1", reviewed_at=NOW
        ),
    )
    assert reviewed.state is TransferProofState.ACCEPTED
    assert intent.status is PaymentIntentStatus.CONFIRMED


def test_a_rejected_transfer_proof_leaves_the_intent_pending(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    proof = submit_transfer_proof(
        db,
        scope=scope,
        command=SubmitTransferProof(
            intent_id=intent.id,
            declared=Money.of(Decimal("15000.00"), NGN),
            document_reference="file:receipt-1",
            submitted_reference="bank-ref-1",
            declared_at=NOW,
        ),
    )
    review_transfer_proof(
        db,
        scope=scope,
        command=ReviewTransferProof(
            proof_id=proof.id,
            accept=False,
            reviewer="agent:1",
            rejection_reason="amount does not match the bank statement",
            reviewed_at=NOW,
        ),
    )
    assert proof.state is TransferProofState.REJECTED
    assert intent.status is PaymentIntentStatus.PENDING


def test_confirmations_cannot_be_rewritten(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    confirmation = record_confirmation(
        db,
        scope=scope,
        command=RecordConfirmation(
            intent_id=intent.id,
            source=ConfirmationSource.PROVIDER_CALLBACK,
            external_reference="ps_12345",
            confirmed=Money.of(Decimal("15000.00"), NGN),
            observed_at=NOW,
        ),
    )
    confirmation.confirmed_amount = Decimal("1.00")
    with pytest.raises(PaymentConfirmationImmutableError):
        db.flush()
    db.expunge_all()


def test_another_tenants_intent_is_not_visible(db: Session) -> None:
    intent = _intent(db, TenantScope(TENANT_A))
    with pytest.raises(Conflict):
        record_confirmation(
            db,
            scope=TenantScope(TENANT_B),
            command=RecordConfirmation(
                intent_id=intent.id,
                source=ConfirmationSource.MANUAL,
                external_reference="x",
                confirmed=Money.of(Decimal("1.00"), NGN),
                observed_at=NOW,
            ),
        )
