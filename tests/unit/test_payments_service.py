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
    cancel_payment_intent,
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
# A FIXED instant, and the tests below drive the module from it. This is only
# sound because `opened_at` is a supplied business fact: `open_payment_intent`
# validates `expires_at` against the opening time it is given, never against
# the moment the suite happens to run. An earlier revision read the wall clock
# there, which made a fixed NOW a delayed-action failure — green until
# wall-clock time overtook NOW + 1h, red on every run after.
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
    kwargs.setdefault("opened_at", NOW)
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


def test_a_historical_import_opens_and_expires_wholly_in_the_past(
    db: Session,
) -> None:
    """The adoption case: a backfilled intent whose real timeline has passed.

    Sub's history is full of intents opened months ago and expired shortly
    after. Both timestamps are in the past on the day the backfill runs, and
    the module must accept them on the strength of their ORDERING alone — an
    expiry checked against import wall time would refuse every migrated row,
    and stamping `opened_at` with the import moment would make the shadow's
    settlement-time comparison report drift on all of them.
    """
    scope = TenantScope(TENANT_A)
    opened = datetime(2025, 3, 4, 9, 30, tzinfo=UTC)
    expired = opened + timedelta(days=1)

    intent = _intent(db, scope, opened_at=opened, expires_at=expired)

    assert intent.opened_at == opened
    assert intent.expires_at == expired
    expire_payment_intent(db, scope=scope, intent_id=intent.id, now=expired)
    assert intent.status is PaymentIntentStatus.EXPIRED
    assert intent.settled_at == expired


def test_an_expiry_at_or_before_the_opening_time_is_refused(db: Session) -> None:
    """Ordering is the invariant, not futureness."""
    scope = TenantScope(TENANT_A)
    opened = datetime(2025, 3, 4, 9, 30, tzinfo=UTC)
    for expires_at in (opened, opened - timedelta(seconds=1)):
        with pytest.raises(Conflict):
            _intent(
                db,
                scope,
                reference="ref-bad",
                opened_at=opened,
                expires_at=expires_at,
            )


def test_a_replay_keeps_the_stored_opening_time(db: Session) -> None:
    """Omitting `opened_at` on a retry must not re-derive it.

    A retry that defaulted to "now" would silently move an authoritative
    business fact every time the caller retried — the drift would be invisible
    because the call succeeds and returns the same intent.
    """
    scope = TenantScope(TENANT_A)
    opened = datetime(2025, 3, 4, 9, 30, tzinfo=UTC)
    first = _intent(db, scope, opened_at=opened)

    replay = open_payment_intent(
        db,
        scope=scope,
        command=OpenPaymentIntent(
            payer_reference="customer:1",
            purpose=PaymentPurpose.INVOICE_SETTLEMENT,
            requested=Money.of(Decimal("15000.00"), NGN),
            reference="ref-1",
            provider_type="paystack",
            channel="web",
        ),
    )
    assert replay.id == first.id
    assert replay.opened_at == opened


def test_a_replay_naming_a_different_opening_time_is_refused(db: Session) -> None:
    """Two callers disagreeing about when the payer was asked to pay is the
    same class of defect as disagreeing about the amount, and is refused the
    same way rather than resolved by last-writer-wins."""
    scope = TenantScope(TENANT_A)
    opened = datetime(2025, 3, 4, 9, 30, tzinfo=UTC)
    _intent(db, scope, opened_at=opened)
    with pytest.raises(Conflict):
        _intent(db, scope, opened_at=opened + timedelta(hours=1))


def test_a_cancellation_settles_at_the_moment_the_caller_names(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    opened = datetime(2025, 3, 4, 9, 30, tzinfo=UTC)
    cancelled = opened + timedelta(hours=6)
    intent = _intent(db, scope, opened_at=opened)

    cancel_payment_intent(db, scope=scope, intent_id=intent.id, cancelled_at=cancelled)

    assert intent.status is PaymentIntentStatus.CANCELLED
    assert intent.settled_at == cancelled


def test_a_cancellation_before_the_opening_time_is_refused(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    opened = datetime(2025, 3, 4, 9, 30, tzinfo=UTC)
    intent = _intent(db, scope, opened_at=opened)
    with pytest.raises(Conflict):
        cancel_payment_intent(
            db,
            scope=scope,
            intent_id=intent.id,
            cancelled_at=opened - timedelta(seconds=1),
        )


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
