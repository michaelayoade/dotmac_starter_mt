"""Refund instruction (P3) and cash outcome observation (P4) canaries.

SQLite proves the service rules; the database-boundary and concurrency proofs
live in `tests/test_payments_refunds_pg.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_kernel.money import Money, currency
from dotmac_payments import models
from dotmac_payments.contracts import (
    AdminCorrectionDirection,
    AttentionReason,
    CashOutcomeKind,
    CashOutcomeState,
    ConfirmationNotFound,
    ConfirmationSource,
    Conflict,
    GetRefundableCashV1,
    InstructRefund,
    ObserveCashOutcome,
    OpenPaymentIntent,
    PaymentPurpose,
    RecordConfirmation,
    RefundExceedsConfirmedCash,
)
from dotmac_payments.models import (
    TENANT_TABLES,
    PaymentCashOutcome,
    RefundInstruction,
)
from dotmac_payments.refunds import (
    derive_refund_provider_reference,
    get_refundable_cash_v1,
    instruct_refund,
    observe_cash_outcome,
)
from dotmac_payments.service import open_payment_intent, record_confirmation
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 11, 3, tzinfo=UTC)
NGN = currency("NGN")
USD = currency("USD")
SCOPE = TenantScope(TENANT_A)


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


def _confirmed(
    db: Session,
    *,
    scope: TenantScope = SCOPE,
    reference: str = "ref-1",
    amount: str = "10000.00",
    provider: str = "gateway",
):
    intent = open_payment_intent(
        db,
        scope=scope,
        command=OpenPaymentIntent(
            payer_reference="customer:1",
            purpose=PaymentPurpose.INVOICE_SETTLEMENT,
            requested=Money.of(Decimal(amount), NGN),
            reference=reference,
            provider_type=provider,
            channel="web",
            opened_at=NOW,
        ),
    )
    return record_confirmation(
        db,
        scope=scope,
        command=RecordConfirmation(
            intent_id=intent.id,
            source=ConfirmationSource.PROVIDER_CALLBACK,
            external_reference=f"ext-{reference}",
            confirmed=Money.of(Decimal(amount), NGN),
            observed_at=NOW,
        ),
    )


def _refund(db, confirmation, amount: str, request_id=None, scope=SCOPE, **kw):
    return instruct_refund(
        db,
        scope=scope,
        command=InstructRefund(
            request_id=request_id or uuid.uuid4(),
            confirmation_id=confirmation.id,
            amount=Money.of(Decimal(amount), NGN),
            **kw,
        ),
    )


def _outcome(db, confirmation_id, kind, amount, scope=SCOPE, **kw):
    kw.setdefault("provider_type", "gateway")
    kw.setdefault("outcome_reference", f"out-{uuid.uuid4()}")
    return observe_cash_outcome(
        db,
        scope=scope,
        command=ObserveCashOutcome(
            confirmation_id=confirmation_id,
            kind=kind,
            amount=Money.of(Decimal(amount), NGN),
            **kw,
        ),
    )


def _refundable(db, confirmation) -> Decimal:
    return get_refundable_cash_v1(
        db, scope=SCOPE, query=GetRefundableCashV1(confirmation.id)
    ).refundable.amount


# ----------------------------------------------------------------- P3 reference


def test_provider_reference_is_a_pure_function_of_tenant_and_request() -> None:
    request = uuid.uuid4()
    reference = derive_refund_provider_reference(SCOPE, request)
    assert reference == derive_refund_provider_reference(SCOPE, request)
    assert reference != derive_refund_provider_reference(TenantScope(TENANT_B), request)
    assert reference != derive_refund_provider_reference(SCOPE, uuid.uuid4())
    assert len(reference) == 36 and reference.startswith("rf1-")
    assert str(request) not in reference


def test_instruction_stores_the_derived_reference_and_confirmation_provider(db):
    confirmation = _confirmed(db)
    request = uuid.uuid4()
    row = _refund(db, confirmation, "2500.00", request_id=request)
    assert row.provider_reference == derive_refund_provider_reference(SCOPE, request)
    assert row.provider_type == confirmation.provider_type
    assert row.intent_id == confirmation.intent_id
    assert row.amount == Decimal("2500.00")


# --------------------------------------------------------------- P3 idempotency


def test_instruction_replay_returns_the_same_row_and_adds_none(db):
    confirmation = _confirmed(db)
    request = uuid.uuid4()
    first = _refund(db, confirmation, "4000.00", request_id=request)
    again = _refund(db, confirmation, "4000.00", request_id=request)
    assert again.id == first.id
    assert db.scalar(select(func.count()).select_from(RefundInstruction)) == 1
    # Replay is not a second hold on the cash.
    assert _refundable(db, confirmation) == Decimal("6000.00")


def test_replay_still_answers_when_the_cash_is_now_fully_held(db):
    confirmation = _confirmed(db)
    request = uuid.uuid4()
    first = _refund(db, confirmation, "6000.00", request_id=request)
    _refund(db, confirmation, "4000.00")
    assert _refund(db, confirmation, "6000.00", request_id=request).id == first.id


@pytest.mark.parametrize("changed", ["amount", "confirmation", "instant"])
def test_replay_refuses_a_changed_request(db, changed):
    confirmation = _confirmed(db)
    other = _confirmed(db, reference="ref-2")
    request = uuid.uuid4()
    _refund(db, confirmation, "1000.00", request_id=request, instructed_at=NOW)
    with pytest.raises(Conflict):
        _refund(
            db,
            other if changed == "confirmation" else confirmation,
            "1000.01" if changed == "amount" else "1000.00",
            request_id=request,
            instructed_at=(
                datetime(2026, 11, 4, tzinfo=UTC) if changed == "instant" else NOW
            ),
        )


# ------------------------------------------------------------ P3 over-refund


def test_a_single_refund_above_confirmed_cash_is_refused(db):
    confirmation = _confirmed(db)
    with pytest.raises(RefundExceedsConfirmedCash):
        _refund(db, confirmation, "10000.01")
    assert db.scalar(select(func.count()).select_from(RefundInstruction)) == 0


def test_the_full_amount_is_refundable_exactly_once(db):
    confirmation = _confirmed(db)
    _refund(db, confirmation, "10000.00")
    assert _refundable(db, confirmation) == Decimal("0.00")
    with pytest.raises(RefundExceedsConfirmedCash):
        _refund(db, confirmation, "0.01")


def test_prior_and_in_flight_refunds_count_against_the_cash(db):
    confirmation = _confirmed(db)
    _refund(db, confirmation, "6000.00")  # in flight: no outcome yet
    with pytest.raises(RefundExceedsConfirmedCash):
        _refund(db, confirmation, "4000.01")
    _refund(db, confirmation, "4000.00")


def test_a_completed_refund_stays_held_and_a_failed_one_is_released(db):
    confirmation = _confirmed(db)
    done = _refund(db, confirmation, "6000.00")
    failed = _refund(db, confirmation, "4000.00")
    for instruction, kind in (
        (done, CashOutcomeKind.REFUND_COMPLETED),
        (failed, CashOutcomeKind.REFUND_FAILED),
    ):
        outcome = _outcome(
            db,
            confirmation.id,
            kind,
            str(instruction.amount),
            refund_reference=instruction.provider_reference,
        )
        assert outcome.state is CashOutcomeState.OBSERVED
        assert outcome.instruction_id == instruction.id
    assert _refundable(db, confirmation) == Decimal("4000.00")
    _refund(db, confirmation, "4000.00")  # the released amount is usable again


def test_a_parked_failure_does_not_release_cash(db):
    confirmation = _confirmed(db)
    instruction = _refund(db, confirmation, "6000.00")
    parked = _outcome(
        db,
        confirmation.id,
        CashOutcomeKind.REFUND_FAILED,
        "5999.00",  # amount disagrees with the instruction
        refund_reference=instruction.provider_reference,
    )
    assert parked.attention_reason is AttentionReason.AMOUNT_MISMATCH
    assert _refundable(db, confirmation) == Decimal("4000.00")


def test_refund_currency_must_match_the_confirmed_cash(db):
    confirmation = _confirmed(db)
    with pytest.raises(Conflict):
        instruct_refund(
            db,
            scope=SCOPE,
            command=InstructRefund(
                request_id=uuid.uuid4(),
                confirmation_id=confirmation.id,
                amount=Money.of(Decimal("1.00"), USD),
            ),
        )


@pytest.mark.parametrize("amount", ["0", "-1.00"])
def test_refund_must_be_positive(db, amount):
    confirmation = _confirmed(db)
    with pytest.raises(Conflict):
        _refund(db, confirmation, amount)


def test_unknown_and_foreign_confirmations_cannot_be_refunded(db):
    foreign = _confirmed(db, scope=TenantScope(TENANT_B))
    with pytest.raises(ConfirmationNotFound):
        _refund(db, foreign, "1.00")
    with pytest.raises(ConfirmationNotFound):
        instruct_refund(
            db,
            scope=SCOPE,
            command=InstructRefund(
                request_id=uuid.uuid4(),
                confirmation_id=uuid.uuid4(),
                amount=Money.of(Decimal("1.00"), NGN),
            ),
        )


def test_refund_holds_are_per_confirmation(db):
    first, second = _confirmed(db), _confirmed(db, reference="ref-2")
    _refund(db, first, "10000.00")
    _refund(db, second, "10000.00")


# --------------------------------------------------------------------- P4 kinds


@pytest.mark.parametrize("kind", [CashOutcomeKind.CHARGEBACK, CashOutcomeKind.BOUNCE])
def test_chargeback_and_bounce_are_observed_against_known_cash(db, kind):
    confirmation = _confirmed(db)
    row = _outcome(db, confirmation.id, kind, "10000.00", observed_at=NOW)
    assert row.state is CashOutcomeState.OBSERVED
    assert row.attention_reason is None
    assert row.confirmation_id == confirmation.id
    assert row.instruction_id is None


def test_admin_correction_is_identified_by_its_correction_id_alone(db):
    confirmation = _confirmed(db)
    correction = uuid.uuid4()
    command = ObserveCashOutcome(
        confirmation_id=confirmation.id,
        kind=CashOutcomeKind.ADMIN_CORRECTION,
        amount=Money.of(Decimal("50.00"), NGN),
        correction_id=correction,
        direction=AdminCorrectionDirection.CASH_RETURNED_TO_CUSTOMER,
        approval_ref="approval:1",
    )
    row = observe_cash_outcome(db, scope=SCOPE, command=command)
    assert row.state is CashOutcomeState.OBSERVED
    assert row.provider_type is None and row.outcome_reference is None
    assert observe_cash_outcome(db, scope=SCOPE, command=command).id == row.id
    assert db.scalar(select(func.count()).select_from(PaymentCashOutcome)) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": CashOutcomeKind.ADMIN_CORRECTION},  # no correction id
        {
            "kind": CashOutcomeKind.ADMIN_CORRECTION,
            "correction_id": uuid.uuid4(),
            "approval_ref": "approval:1",
        },  # no direction
        {
            "kind": CashOutcomeKind.ADMIN_CORRECTION,
            "correction_id": uuid.uuid4(),
            "direction": AdminCorrectionDirection.CASH_RESTORED,
        },  # no approval
        {
            "kind": CashOutcomeKind.ADMIN_CORRECTION,
            "correction_id": uuid.uuid4(),
            "direction": AdminCorrectionDirection.CASH_RESTORED,
            "approval_ref": "approval:1",
            "provider_type": "gateway",
        },
        {
            "kind": CashOutcomeKind.BOUNCE,
            "provider_type": "gateway",
            "outcome_reference": "x",
            "direction": AdminCorrectionDirection.CASH_RESTORED,
        },
        {
            "kind": CashOutcomeKind.CHARGEBACK,
            "correction_id": uuid.uuid4(),
            "provider_type": "gateway",
            "outcome_reference": "x",
        },
        {"kind": CashOutcomeKind.CHARGEBACK},  # no identity
        {
            "kind": CashOutcomeKind.CHARGEBACK,
            "provider_type": "gateway",
            "outcome_reference": "x",
            "refund_reference": "rf1-x",
        },
        {
            "kind": CashOutcomeKind.REFUND_COMPLETED,
            "provider_type": "gateway",
            "outcome_reference": "x",
        },  # a refund outcome must name its refund
    ],
)
def test_outcome_identity_shapes_are_exclusive(db, kwargs):
    confirmation = _confirmed(db)
    with pytest.raises(ValueError):
        observe_cash_outcome(
            db,
            scope=SCOPE,
            command=ObserveCashOutcome(
                confirmation_id=confirmation.id,
                amount=Money.of(Decimal("1.00"), NGN),
                **kwargs,
            ),
        )


# --------------------------------------------------- P4 attention, never dropped


def test_outcome_on_an_unknown_confirmation_is_parked_not_dropped(db):
    ghost = uuid.uuid4()
    row = _outcome(db, ghost, CashOutcomeKind.CHARGEBACK, "75.00")
    assert row.state is CashOutcomeState.ATTENTION_REQUIRED
    assert row.attention_reason is AttentionReason.UNKNOWN_CONFIRMATION
    assert row.confirmation_id is None
    assert row.claimed_confirmation_id == ghost
    assert row.amount == Decimal("75.00")
    assert db.scalar(select(func.count()).select_from(PaymentCashOutcome)) == 1


def test_another_tenants_confirmation_is_unknown_here(db):
    foreign = _confirmed(db, scope=TenantScope(TENANT_B))
    row = _outcome(db, foreign.id, CashOutcomeKind.BOUNCE, "10.00")
    assert row.attention_reason is AttentionReason.UNKNOWN_CONFIRMATION
    assert row.confirmation_id is None and row.tenant_id == TENANT_A


def test_refund_outcome_for_an_unknown_instruction_is_parked(db):
    confirmation = _confirmed(db)
    row = _outcome(
        db,
        confirmation.id,
        CashOutcomeKind.REFUND_COMPLETED,
        "10.00",
        refund_reference="rf1-" + "0" * 32,
    )
    assert row.attention_reason is AttentionReason.UNKNOWN_REFUND_INSTRUCTION
    assert row.instruction_id is None


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("provider", AttentionReason.PROVIDER_MISMATCH),
        ("currency", AttentionReason.CURRENCY_MISMATCH),
        ("exceeds", AttentionReason.EXCEEDS_CONFIRMED_CASH),
    ],
)
def test_inadmissible_known_cash_outcomes_are_parked_with_a_reason(
    db, scenario, reason
):
    confirmation = _confirmed(db)
    kwargs = {}
    amount, money_currency = "10.00", NGN
    if scenario == "provider":
        kwargs["provider_type"] = "other"
    if scenario == "currency":
        money_currency = USD
    if scenario == "exceeds":
        amount = "10000.01"
    row = observe_cash_outcome(
        db,
        scope=SCOPE,
        command=ObserveCashOutcome(
            confirmation_id=confirmation.id,
            kind=CashOutcomeKind.CHARGEBACK,
            amount=Money.of(Decimal(amount), money_currency),
            **{"provider_type": "gateway", "outcome_reference": "o1", **kwargs},
        ),
    )
    assert row.state is CashOutcomeState.ATTENTION_REQUIRED
    assert row.attention_reason is reason
    assert row.confirmation_id == confirmation.id


def test_refund_outcome_naming_another_confirmation_is_parked(db):
    first, second = _confirmed(db), _confirmed(db, reference="ref-2")
    instruction = _refund(db, first, "100.00")
    row = _outcome(
        db,
        second.id,
        CashOutcomeKind.REFUND_COMPLETED,
        "100.00",
        refund_reference=instruction.provider_reference,
    )
    assert row.attention_reason is AttentionReason.CONFIRMATION_MISMATCH


def test_a_second_terminal_refund_outcome_is_parked_not_applied(db):
    confirmation = _confirmed(db)
    instruction = _refund(db, confirmation, "6000.00")
    common = {"refund_reference": instruction.provider_reference}
    first = _outcome(
        db, confirmation.id, CashOutcomeKind.REFUND_COMPLETED, "6000.00", **common
    )
    late = _outcome(
        db, confirmation.id, CashOutcomeKind.REFUND_FAILED, "6000.00", **common
    )
    assert first.state is CashOutcomeState.OBSERVED
    assert late.attention_reason is AttentionReason.TERMINAL_CONFLICT
    # The late failure must not have released the completed refund's hold.
    assert _refundable(db, confirmation) == Decimal("4000.00")


# ----------------------------------------------------------------- P4 replay


def test_replay_is_idempotent_for_applied_and_parked_outcomes(db):
    confirmation = _confirmed(db)
    for confirmation_id in (confirmation.id, uuid.uuid4()):
        command = ObserveCashOutcome(
            confirmation_id=confirmation_id,
            kind=CashOutcomeKind.CHARGEBACK,
            amount=Money.of(Decimal("20.00"), NGN),
            provider_type="gateway",
            outcome_reference=f"dispute-{confirmation_id}",
        )
        first = observe_cash_outcome(db, scope=SCOPE, command=command)
        again = observe_cash_outcome(db, scope=SCOPE, command=command)
        assert again.id == first.id and again.state is first.state
    assert db.scalar(select(func.count()).select_from(PaymentCashOutcome)) == 2


def test_replay_of_a_parked_outcome_does_not_re_evaluate_it(db):
    ghost = uuid.uuid4()
    command = ObserveCashOutcome(
        confirmation_id=ghost,
        kind=CashOutcomeKind.BOUNCE,
        amount=Money.of(Decimal("5.00"), NGN),
        provider_type="gateway",
        outcome_reference="r1",
    )
    parked = observe_cash_outcome(db, scope=SCOPE, command=command)
    assert parked.state is CashOutcomeState.ATTENTION_REQUIRED
    assert observe_cash_outcome(db, scope=SCOPE, command=command).id == parked.id


@pytest.mark.parametrize("changed", ["amount", "kind", "confirmation", "instant"])
def test_replay_refuses_changed_evidence(db, changed):
    confirmation = _confirmed(db)
    base = {
        "confirmation_id": confirmation.id,
        "kind": CashOutcomeKind.CHARGEBACK,
        "amount": Money.of(Decimal("20.00"), NGN),
        "provider_type": "gateway",
        "outcome_reference": "dispute-1",
        "observed_at": NOW,
    }
    observe_cash_outcome(db, scope=SCOPE, command=ObserveCashOutcome(**base))
    changes = {
        "amount": {"amount": Money.of(Decimal("21.00"), NGN)},
        "kind": {"kind": CashOutcomeKind.BOUNCE},
        "confirmation": {"confirmation_id": uuid.uuid4()},
        "instant": {"observed_at": datetime(2026, 11, 5, tzinfo=UTC)},
    }
    with pytest.raises(Conflict):
        observe_cash_outcome(
            db, scope=SCOPE, command=ObserveCashOutcome(**{**base, **changes[changed]})
        )


def test_omitting_the_instant_on_replay_keeps_the_stored_one(db):
    confirmation = _confirmed(db)
    command = ObserveCashOutcome(
        confirmation_id=confirmation.id,
        kind=CashOutcomeKind.BOUNCE,
        amount=Money.of(Decimal("1.00"), NGN),
        provider_type="gateway",
        outcome_reference="b1",
        observed_at=NOW,
    )
    first = observe_cash_outcome(db, scope=SCOPE, command=command)
    from dataclasses import replace

    again = observe_cash_outcome(
        db, scope=SCOPE, command=replace(command, observed_at=None)
    )
    assert again.id == first.id


def test_identity_is_per_tenant(db):
    mine = _confirmed(db)
    theirs = _confirmed(db, scope=TenantScope(TENANT_B))
    for scope, confirmation in ((SCOPE, mine), (TenantScope(TENANT_B), theirs)):
        row = observe_cash_outcome(
            db,
            scope=scope,
            command=ObserveCashOutcome(
                confirmation_id=confirmation.id,
                kind=CashOutcomeKind.BOUNCE,
                amount=Money.of(Decimal("1.00"), NGN),
                provider_type="gateway",
                outcome_reference="shared-reference",
            ),
        )
        assert row.state is CashOutcomeState.OBSERVED


# ------------------------------------------------------- append-only and shape


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("table", ["instruction", "outcome"])
def test_instructions_and_outcomes_are_append_only_at_the_orm(db, table, operation):
    confirmation = _confirmed(db)
    row = (
        _refund(db, confirmation, "1.00")
        if table == "instruction"
        else _outcome(db, confirmation.id, CashOutcomeKind.BOUNCE, "1.00")
    )
    db.flush()
    if operation == "update":
        row.amount = Decimal("2.00")
    else:
        db.delete(row)
    with pytest.raises(Conflict):
        db.flush()


def test_one_applied_terminal_outcome_per_instruction_is_a_schema_rule(db):
    confirmation = _confirmed(db)
    instruction = _refund(db, confirmation, "100.00")
    first = _outcome(
        db,
        confirmation.id,
        CashOutcomeKind.REFUND_COMPLETED,
        "100.00",
        refund_reference=instruction.provider_reference,
    )
    duplicate = PaymentCashOutcome(
        tenant_id=TENANT_A,
        kind=CashOutcomeKind.REFUND_FAILED,
        state=CashOutcomeState.OBSERVED,
        claimed_confirmation_id=confirmation.id,
        confirmation_id=confirmation.id,
        instruction_id=instruction.id,
        provider_type="GATEWAY",
        outcome_reference="forced",
        refund_reference=instruction.provider_reference,
        currency_code="NGN",
        minor_units=2,
        amount=Decimal("100.00"),
        request_fingerprint="0" * 64,
        observed_at=NOW,
        recorded_at=NOW,
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.flush()
    assert first.state is CashOutcomeState.OBSERVED


def test_outcome_state_shape_check_refuses_an_unreasoned_parked_row(db):
    db.add(
        PaymentCashOutcome(
            tenant_id=TENANT_A,
            kind=CashOutcomeKind.BOUNCE,
            state=CashOutcomeState.ATTENTION_REQUIRED,
            claimed_confirmation_id=uuid.uuid4(),
            provider_type="GATEWAY",
            outcome_reference="x",
            currency_code="NGN",
            minor_units=2,
            amount=Decimal("1.00"),
            request_fingerprint="0" * 64,
            observed_at=NOW,
            recorded_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


# ------------------------------------- chargebacks and bounces consume the cash


@pytest.mark.parametrize("kind", [CashOutcomeKind.CHARGEBACK, CashOutcomeKind.BOUNCE])
def test_a_charged_back_payment_cannot_be_refunded_again(db, kind):
    confirmation = _confirmed(db)
    _outcome(db, confirmation.id, kind, "10000.00")
    assert _refundable(db, confirmation) == Decimal("0.00")
    with pytest.raises(RefundExceedsConfirmedCash):
        _refund(db, confirmation, "0.01")


def test_partial_chargeback_leaves_only_the_remainder_refundable(db):
    confirmation = _confirmed(db)
    _outcome(db, confirmation.id, CashOutcomeKind.CHARGEBACK, "4000.00")
    with pytest.raises(RefundExceedsConfirmedCash):
        _refund(db, confirmation, "6000.01")
    _refund(db, confirmation, "6000.00")


def test_reversals_are_bounded_cumulatively_against_cash_net_of_refunds(db):
    confirmation = _confirmed(db)
    _refund(db, confirmation, "6000.00")  # in flight counts
    first = _outcome(db, confirmation.id, CashOutcomeKind.CHARGEBACK, "4000.00")
    second = _outcome(db, confirmation.id, CashOutcomeKind.BOUNCE, "0.01")
    assert first.state is CashOutcomeState.OBSERVED
    assert second.state is CashOutcomeState.ATTENTION_REQUIRED
    assert second.attention_reason is AttentionReason.EXCEEDS_CONFIRMED_CASH
    assert second.confirmation_id == confirmation.id  # recorded, not dropped


def test_two_chargebacks_on_one_payment_park_the_second(db):
    confirmation = _confirmed(db)
    one = _outcome(db, confirmation.id, CashOutcomeKind.CHARGEBACK, "10000.00")
    two = _outcome(db, confirmation.id, CashOutcomeKind.CHARGEBACK, "10000.00")
    assert one.state is CashOutcomeState.OBSERVED
    assert two.attention_reason is AttentionReason.EXCEEDS_CONFIRMED_CASH


def test_a_parked_chargeback_does_not_consume_cash(db):
    confirmation = _confirmed(db)
    _outcome(db, confirmation.id, CashOutcomeKind.CHARGEBACK, "10000.01")
    assert _refundable(db, confirmation) == Decimal("10000.00")


def _correction(db, confirmation, direction, amount, **kw):
    return observe_cash_outcome(
        db,
        scope=SCOPE,
        command=ObserveCashOutcome(
            confirmation_id=confirmation.id,
            kind=CashOutcomeKind.ADMIN_CORRECTION,
            amount=Money.of(Decimal(amount), NGN),
            correction_id=uuid.uuid4(),
            direction=direction,
            approval_ref="approval:7",
            **kw,
        ),
    )


def test_admin_correction_enters_the_arithmetic_by_direction(db):
    confirmation = _confirmed(db)
    out = AdminCorrectionDirection.CASH_RETURNED_TO_CUSTOMER
    back = AdminCorrectionDirection.CASH_RESTORED
    assert _correction(db, confirmation, out, "3000.00").state is (
        CashOutcomeState.OBSERVED
    )
    assert _refundable(db, confirmation) == Decimal("7000.00")
    # Restoring more than was returned is parked, not applied.
    over = _correction(db, confirmation, back, "3000.01")
    assert over.attention_reason is AttentionReason.EXCEEDS_CONFIRMED_CASH
    assert _correction(db, confirmation, back, "1000.00").state is (
        CashOutcomeState.OBSERVED
    )
    assert _refundable(db, confirmation) == Decimal("8000.00")


def test_replay_refuses_a_changed_correction_direction_or_approval(db):
    confirmation = _confirmed(db)
    correction = uuid.uuid4()
    base = {
        "confirmation_id": confirmation.id,
        "kind": CashOutcomeKind.ADMIN_CORRECTION,
        "amount": Money.of(Decimal("5.00"), NGN),
        "correction_id": correction,
        "direction": AdminCorrectionDirection.CASH_RETURNED_TO_CUSTOMER,
        "approval_ref": "approval:7",
    }
    observe_cash_outcome(db, scope=SCOPE, command=ObserveCashOutcome(**base))
    for change in (
        {"approval_ref": "approval:8"},
        {"direction": AdminCorrectionDirection.CASH_RESTORED},
    ):
        with pytest.raises(Conflict):
            observe_cash_outcome(
                db, scope=SCOPE, command=ObserveCashOutcome(**{**base, **change})
            )
