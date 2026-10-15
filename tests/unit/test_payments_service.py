"""Payment intent and correlation behavior canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
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
from sqlalchemy import create_engine, func, select
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


@pytest.mark.parametrize(
    "changed",
    [
        "payer",
        "target",
        "no_target",
        "purpose",
        "provider",
        "channel",
        "amount",
        "currency",
        "scale",
        "opening",
        "expiry",
        "no_expiry",
    ],
)
def test_intent_replay_rejects_each_changed_request_binding(db, changed):
    from dotmac_kernel.money import Currency

    scope = TenantScope(TENANT_A)
    command = OpenPaymentIntent(
        payer_reference="customer:1",
        target_reference="invoice:1",
        purpose=PaymentPurpose.INVOICE_SETTLEMENT,
        requested=Money.of("15000", NGN),
        reference="bound-request",
        provider_type="paystack",
        channel="web",
        opened_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    first = open_payment_intent(db, scope=scope, command=command)
    changes = {
        "payer": {"payer_reference": "customer:2"},
        "target": {"target_reference": "invoice:2"},
        "no_target": {"target_reference": None},
        "purpose": {"purpose": PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT},
        "provider": {"provider_type": "flutterwave"},
        "channel": {"channel": "transfer"},
        "amount": {"requested": Money.of("14999", NGN)},
        "currency": {"requested": Money.of("15000", USD)},
        "scale": {"requested": Money.of("15000", Currency("NGN", 3))},
        "opening": {"opened_at": NOW + timedelta(seconds=1)},
        "expiry": {"expires_at": NOW + timedelta(hours=2)},
        "no_expiry": {"expires_at": None},
    }
    with pytest.raises(Conflict):
        open_payment_intent(
            db, scope=scope, command=replace(command, **changes[changed])
        )
    db.expire_all()
    assert open_payment_intent(db, scope=scope, command=command).id == first.id
    assert db.scalar(select(func.count()).select_from(models.PaymentIntent)) == 1


@pytest.mark.parametrize("changed", ["target", "expiry"])
def test_intent_replay_cannot_add_an_absent_binding(db, changed):
    scope = TenantScope(TENANT_A)
    first = _intent(db, scope)
    with pytest.raises(Conflict):
        _intent(
            db,
            scope,
            **(
                {"target_reference": "invoice:1"}
                if changed == "target"
                else {"expires_at": NOW + timedelta(hours=1)}
            ),
        )
    assert first.target_reference is None
    assert first.expires_at is None


def test_intent_replay_normalizes_identity_and_preserves_terminal_facts(db):
    scope = TenantScope(TENANT_A)
    command = OpenPaymentIntent(
        payer_reference=" customer:1 ",
        target_reference="invoice:1",
        purpose=PaymentPurpose.INVOICE_SETTLEMENT,
        requested=Money.of("15000", NGN),
        reference=" bound-request ",
        provider_type=" paystack ",
        channel=" web ",
        opened_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    first = open_payment_intent(db, scope=scope, command=command)
    assert open_payment_intent(db, scope=scope, command=command).id == first.id
    expire_payment_intent(db, scope=scope, intent_id=first.id, now=command.expires_at)
    db.expire_all()
    offset = timezone(timedelta(hours=1))
    replay = open_payment_intent(
        db,
        scope=scope,
        command=replace(
            command,
            reference="bound-request",
            payer_reference="customer:1",
            provider_type="PAYSTACK",
            channel="WEB",
            opened_at=NOW.astimezone(offset),
            expires_at=command.expires_at.astimezone(offset),
        ),
    )
    assert replay.id == first.id
    assert replay.status is PaymentIntentStatus.EXPIRED
    assert replay.settled_at.replace(tzinfo=UTC) == command.expires_at
    assert (
        open_payment_intent(
            db, scope=scope, command=replace(command, opened_at=None)
        ).id
        == first.id
    )


def test_intent_reference_namespace_is_tenant_only(db):
    from dotmac_kernel.cache import PlatformScope

    first = _intent(db, TenantScope(TENANT_A), target_reference="invoice:a")
    second = _intent(db, TenantScope(TENANT_B), target_reference="invoice:b")
    assert first.id != second.id
    assert (
        _intent(db, TenantScope(TENANT_A), target_reference="invoice:a").id == first.id
    )
    with pytest.raises(TypeError, match="requires TenantScope"):
        _intent(db, PlatformScope())


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


@pytest.mark.parametrize("changed", ["amount", "currency", "source", "observed_at"])
def test_confirmation_replay_refuses_changed_immutable_evidence(db, changed):
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    command = RecordConfirmation(
        intent_id=intent.id,
        source=ConfirmationSource.PROVIDER_CALLBACK,
        external_reference="immutable-evidence",
        confirmed=Money.of("15000", NGN),
        observed_at=NOW,
    )
    first = record_confirmation(db, scope=scope, command=command)
    changes = {
        "amount": {"confirmed": Money.of("14999", NGN)},
        "currency": {"confirmed": Money.of("15000", USD)},
        "source": {"source": ConfirmationSource.MANUAL},
        "observed_at": {"observed_at": NOW + timedelta(seconds=1)},
    }
    with pytest.raises(Conflict):
        record_confirmation(
            db, scope=scope, command=replace(command, **changes[changed])
        )
    assert first.confirmed_amount == Decimal("15000")
    assert first.source is ConfirmationSource.PROVIDER_CALLBACK
    assert intent.confirmed_amount == Decimal("15000")


def test_confirmation_replay_omits_clock_and_survives_loaded_state_refresh(db):
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope, expires_at=NOW + timedelta(hours=1))
    command = RecordConfirmation(
        intent_id=intent.id,
        source=ConfirmationSource.PROVIDER_CALLBACK,
        external_reference="stored-clock",
        confirmed=Money.of("15000", NGN),
        observed_at=NOW,
    )
    first = record_confirmation(db, scope=scope, command=command)
    first_id = first.id
    db.expire_all()
    assert record_confirmation(db, scope=scope, command=command).id == first_id
    replay = record_confirmation(
        db, scope=scope, command=replace(command, observed_at=None)
    )
    assert replay.id == first_id
    assert replay.observed_at.replace(tzinfo=UTC) == NOW


def test_failed_proof_acceptance_does_not_write_review_evidence(db):
    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    proof = submit_transfer_proof(
        db,
        scope=scope,
        command=SubmitTransferProof(
            intent_id=intent.id,
            declared=Money.of("15000", NGN),
            document_reference="file:proof",
            submitted_reference="proof-before-provider",
            declared_at=NOW,
        ),
    )
    record_confirmation(
        db,
        scope=scope,
        command=RecordConfirmation(
            intent_id=intent.id,
            source=ConfirmationSource.PROVIDER_CALLBACK,
            external_reference="provider-won",
            confirmed=Money.of("15000", NGN),
            observed_at=NOW,
        ),
    )
    with pytest.raises(Conflict):
        review_transfer_proof(
            db,
            scope=scope,
            command=ReviewTransferProof(
                proof_id=proof.id,
                accept=True,
                reviewer="agent:reviewer",
                reviewed_at=NOW,
            ),
        )
    db.flush()
    db.refresh(proof)
    assert proof.state is TransferProofState.SUBMITTED
    assert proof.reviewer is None
    assert proof.reviewed_at is None


@pytest.mark.parametrize(
    "code,scale,value",
    [
        ("JPY", 0, "123"),
        ("NGN", 2, "123.45"),
        ("BHD", 3, "123.456"),
        ("XTS", 6, "123.456789"),
    ],
)
def test_exact_scale_survives_proof_and_scalar_query(db, code, scale, value):
    from dotmac_kernel.money import Currency
    from dotmac_payments import GetConfirmedCashV1, get_confirmed_cash_v1

    scope = TenantScope(TENANT_A)
    money = Money.of(value, Currency(code, scale))
    intent = open_payment_intent(
        db,
        scope=scope,
        command=OpenPaymentIntent(
            payer_reference="customer:precision",
            purpose=PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT,
            requested=money,
            reference="precision",
            provider_type="bank",
            channel="transfer",
            opened_at=NOW,
        ),
    )
    proof = submit_transfer_proof(
        db,
        scope=scope,
        command=SubmitTransferProof(
            intent_id=intent.id,
            declared=money,
            document_reference="file:precision",
            submitted_reference="precision-proof",
        ),
    )
    review_transfer_proof(
        db,
        scope=scope,
        command=ReviewTransferProof(
            proof_id=proof.id, accept=True, reviewer="reviewer", reviewed_at=NOW
        ),
    )
    confirmation = db.scalar(select(models.PaymentConfirmation))
    confirmation_id = confirmation.id
    # A dirty identity-map object is not authoritative query evidence, even
    # before the caller rolls its transaction back.
    confirmation.confirmed_amount = Decimal("9")
    observation = get_confirmed_cash_v1(
        db, scope=scope, query=GetConfirmedCashV1(confirmation_id)
    )
    assert observation.confirmed == money
    assert observation.scope == scope
    assert observation.intent_id == intent.id
    assert observation.observed_at == NOW
    db.expunge_all()
    assert (
        get_confirmed_cash_v1(
            db, scope=scope, query=GetConfirmedCashV1(confirmation_id)
        )
        == observation
    )
    from dotmac_payments import ConfirmationNotFound

    with pytest.raises(ConfirmationNotFound):
        get_confirmed_cash_v1(
            db, scope=TenantScope(TENANT_B), query=GetConfirmedCashV1(confirmation_id)
        )


@pytest.mark.parametrize(
    "scale,value", [(7, "1.0000001"), (2, "100000000000000.00"), (2, "NaN")]
)
def test_unsupported_money_refuses_before_any_write(db, scale, value):
    from dotmac_kernel.money import Currency

    money = Money.of(value, Currency("XTS", scale))
    with pytest.raises(Conflict):
        open_payment_intent(
            db,
            scope=TenantScope(TENANT_A),
            command=OpenPaymentIntent(
                payer_reference="payer",
                purpose=PaymentPurpose.ACCOUNT_CREDIT_DEPOSIT,
                requested=money,
                reference="invalid",
                provider_type="bank",
                channel="transfer",
            ),
        )
    assert db.scalar(select(func.count()).select_from(models.PaymentIntent)) == 0


def test_same_code_different_scale_is_not_replay_or_correlation(db):
    from dotmac_kernel.money import Currency

    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    changed = Money.of("15000", Currency("NGN", 3))
    with pytest.raises(Conflict):
        open_payment_intent(
            db,
            scope=scope,
            command=OpenPaymentIntent(
                payer_reference="customer:1",
                purpose=PaymentPurpose.INVOICE_SETTLEMENT,
                requested=changed,
                reference="ref-1",
                provider_type="paystack",
                channel="web",
            ),
        )
    with pytest.raises(Conflict):
        record_confirmation(
            db,
            scope=scope,
            command=RecordConfirmation(
                intent_id=intent.id,
                source=ConfirmationSource.MANUAL,
                external_reference="scale-conflict",
                confirmed=changed,
            ),
        )


def test_legacy_unknown_precision_refuses_acceptance_but_can_reject(db):
    from dotmac_payments import UnresolvedMoneyPrecision

    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    proof = submit_transfer_proof(
        db,
        scope=scope,
        command=SubmitTransferProof(
            intent_id=intent.id,
            declared=Money.of("15000", NGN),
            document_reference="legacy",
            submitted_reference="legacy",
        ),
    )
    proof.minor_units = None
    db.flush()
    with pytest.raises(UnresolvedMoneyPrecision):
        review_transfer_proof(
            db,
            scope=scope,
            command=ReviewTransferProof(
                proof_id=proof.id, accept=True, reviewer="operator"
            ),
        )
    assert proof.state is TransferProofState.SUBMITTED
    assert proof.reviewer is None
    review_transfer_proof(
        db,
        scope=scope,
        command=ReviewTransferProof(
            proof_id=proof.id,
            accept=False,
            reviewer="operator",
            rejection_reason="unresolved",
        ),
    )
    assert proof.state is TransferProofState.REJECTED


def test_proof_acceptance_cannot_round_inconsistent_stored_precision(db):
    from dotmac_payments import UnresolvedMoneyPrecision

    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    proof = submit_transfer_proof(
        db,
        scope=scope,
        command=SubmitTransferProof(
            intent_id=intent.id,
            declared=Money.of("15000", NGN),
            document_reference="inconsistent",
            submitted_reference="inconsistent",
        ),
    )
    proof.declared_amount = Decimal("15000.001")
    db.flush()
    with pytest.raises(UnresolvedMoneyPrecision):
        review_transfer_proof(
            db,
            scope=scope,
            command=ReviewTransferProof(
                proof_id=proof.id,
                accept=True,
                reviewer="operator",
            ),
        )
    assert proof.state is TransferProofState.SUBMITTED
    assert db.scalar(select(func.count()).select_from(models.PaymentConfirmation)) == 0


def test_intent_lookup_public_observation_is_frozen_and_requires_tenant(db):
    from dataclasses import FrozenInstanceError

    from dotmac_kernel.cache import PlatformScope
    from dotmac_payments import GetPaymentIntentV1, get_payment_intent_v1

    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    observation = get_payment_intent_v1(
        db, scope=scope, query=GetPaymentIntentV1(intent.reference)
    )
    assert observation.intent_id == intent.id
    assert observation.scope == scope
    with pytest.raises(FrozenInstanceError):
        observation.reference = "changed"
    with pytest.raises(ValueError, match="reference must not be empty"):
        get_payment_intent_v1(db, scope=scope, query=GetPaymentIntentV1(" \t "))
    with pytest.raises(TypeError, match="requires TenantScope"):
        get_payment_intent_v1(
            db, scope=PlatformScope(), query=GetPaymentIntentV1(intent.reference)
        )


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"minor_units": None}, "UnresolvedMoneyPrecision"),
        (
            {"requested_amount": Decimal("123.456"), "minor_units": 2},
            "UnresolvedMoneyPrecision",
        ),
        ({"currency_code": "ngn"}, "Conflict"),
    ],
)
def test_intent_lookup_refuses_corrupt_persisted_money(db, changes, error):
    from dotmac_payments import (
        GetPaymentIntentV1,
        UnresolvedMoneyPrecision,
        get_payment_intent_v1,
    )

    scope = TenantScope(TENANT_A)
    intent = _intent(db, scope)
    db.execute(
        models.PaymentIntent.__table__.update()
        .where(models.PaymentIntent.id == intent.id)
        .values(**changes)
    )
    expected_error = {
        "UnresolvedMoneyPrecision": UnresolvedMoneyPrecision,
        "Conflict": Conflict,
    }[error]
    with pytest.raises(expected_error):
        get_payment_intent_v1(
            db, scope=scope, query=GetPaymentIntentV1(intent.reference)
        )
