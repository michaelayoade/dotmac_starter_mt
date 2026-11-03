"""Refund instruction and post-confirmation cash outcomes.

Two narrow primitives and nothing else. `instruct_refund` records and BOUNDS an
instruction to return confirmed cash; `observe_cash_outcome` records what later
happened to that cash. Neither performs provider I/O (the connector owns
transport), posts to a ledger, or decides what a refund means for a receivable
(Billing owns that). There is deliberately no fee amount and no saved-payment
mandate here.

Safety rests on one lock. Every instruction and every outcome that resolves to a
confirmation first takes the owning intent row `FOR UPDATE`, so concurrent
refunds of the same cash serialize and each sees the other's committed total.
The PostgreSQL insert trigger repeats the same bound for writers that bypass
this module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money
from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from dotmac_payments.contracts import (
    REFUND_OUTCOME_KINDS,
    AttentionReason,
    CashOutcomeKind,
    CashOutcomeState,
    ConfirmationNotFound,
    Conflict,
    GetRefundableCashV1,
    InstructRefund,
    ObserveCashOutcome,
    PaymentIntentStatus,
    RefundableCashObservationV1,
    RefundExceedsConfirmedCash,
)
from dotmac_payments.models import (
    PaymentCashOutcome,
    PaymentConfirmation,
    RefundInstruction,
)
from dotmac_payments.service import (
    _intent,
    _persisted_money,
    _positive,
    _required,
    _tenant,
    _utc_moment,
)

_REFERENCE_DOMAIN = "payment-refund-reference-v1"
_REFERENCE_PREFIX = "rf1-"
_OUTCOME_DOMAIN = "payment-cash-outcome-v1"


def derive_refund_provider_reference(scope: TenantScope, request_id: UUID) -> str:
    """The provider reference for a refund request, a pure function of its id.

    The PostgreSQL insert trigger recomputes the same value, so a writer cannot
    attach a different reference to a request. Opaque to every provider.
    """
    tenant_id = _tenant(scope)
    if not isinstance(request_id, UUID):
        raise TypeError("refund request id must be a UUID")
    digest = hashlib.sha256(
        f"{_REFERENCE_DOMAIN}:{tenant_id}:{request_id}".encode()
    ).hexdigest()
    return _REFERENCE_PREFIX + digest[:32]


def _require_read_committed(db: Session) -> bool:
    """True on PostgreSQL after proving fresh-snapshot-after-lock semantics.

    A REPEATABLE READ or SERIALIZABLE transaction would keep reading its first
    snapshot after waiting on a lock, and so miss the refund that made it wait.
    SQLite is the non-authoritative unit lane only and takes no locks.
    """
    dialect = db.get_bind().dialect.name
    if dialect != "postgresql":
        return False
    settings = db.execute(
        text(
            "SELECT current_setting('transaction_isolation'), "
            "current_setting('transaction_read_only')"
        )
    ).one()
    if tuple(settings) != ("read committed", "off"):
        raise Conflict("refund and outcome writes require READ WRITE READ COMMITTED")
    return True


def _confirmation(
    db: Session, tenant_id: UUID, confirmation_id: UUID
) -> PaymentConfirmation | None:
    return db.scalar(
        select(PaymentConfirmation).where(
            PaymentConfirmation.tenant_id == tenant_id,
            PaymentConfirmation.id == confirmation_id,
        )
    )


def _confirmation_money(row: PaymentConfirmation) -> Money:
    return _persisted_money(row.confirmed_amount, row.currency_code, row.minor_units)


def _committed(db: Session, tenant_id: UUID, confirmation_id: UUID) -> Decimal:
    """Total held by instructions that have not been observed to fail."""
    failed = exists().where(
        PaymentCashOutcome.tenant_id == RefundInstruction.tenant_id,
        PaymentCashOutcome.instruction_id == RefundInstruction.id,
        PaymentCashOutcome.state == CashOutcomeState.OBSERVED,
        PaymentCashOutcome.kind == CashOutcomeKind.REFUND_FAILED,
    )
    amounts = db.scalars(
        select(RefundInstruction.amount).where(
            RefundInstruction.tenant_id == tenant_id,
            RefundInstruction.confirmation_id == confirmation_id,
            ~failed,
        )
    ).all()
    return sum(amounts, Decimal(0))


def _same_money(
    row_amount: Decimal, code: str, scale: int | None, money: Money
) -> bool:
    return (
        row_amount == money.amount
        and code == money.currency.code
        and scale == money.currency.minor_units
    )


def instruct_refund(
    db: Session, *, scope: TenantScope, command: InstructRefund
) -> RefundInstruction:
    """Record one refund instruction, or replay it by its request id.

    Refused when the amount exceeds the confirmation's cash less every earlier
    instruction that has not failed (in-flight ones included), when the currency
    differs, or when the confirmation is unknown. A replay returns the stored
    instruction and refuses a changed confirmation or amount.
    """
    tenant_id = _tenant(scope)
    if not isinstance(command.request_id, UUID):
        raise TypeError("refund request id must be a UUID")
    amount = _positive(command.amount, "refund amount")
    _require_read_committed(db)
    confirmation = _confirmation(db, tenant_id, command.confirmation_id)
    if confirmation is None:
        raise ConfirmationNotFound("payment confirmation was not found in the tenant")
    # Serialize with every other refund of this cash BEFORE reading totals.
    intent = _intent(db, tenant_id, confirmation.intent_id)

    replay = db.scalar(
        select(RefundInstruction)
        .where(
            RefundInstruction.tenant_id == tenant_id,
            RefundInstruction.request_id == command.request_id,
        )
        .execution_options(populate_existing=True)
    )
    if replay is not None:
        if replay.confirmation_id != confirmation.id:
            raise Conflict("refund request was reused for a different confirmation")
        if not _same_money(
            replay.amount, replay.currency_code, replay.minor_units, amount
        ):
            raise Conflict("refund request was reused for a different amount")
        if command.instructed_at is not None and _utc_moment(
            command.instructed_at
        ) != _utc_moment(replay.instructed_at):
            raise Conflict("refund request was reused for a different instant")
        return replay

    if intent.status is not PaymentIntentStatus.CONFIRMED:
        raise Conflict("only a confirmed payment can be refunded")
    confirmed = _confirmation_money(confirmation)
    if (
        amount.currency.code != confirmed.currency.code
        or amount.currency.minor_units != confirmed.currency.minor_units
    ):
        raise Conflict("refund currency does not match the confirmed cash")
    committed = _committed(db, tenant_id, confirmation.id)
    if amount.amount > confirmed.amount - committed:
        raise RefundExceedsConfirmedCash(
            "refund exceeds confirmed cash less prior and in-flight refunds"
        )
    row = RefundInstruction(
        tenant_id=tenant_id,
        request_id=command.request_id,
        confirmation_id=confirmation.id,
        intent_id=intent.id,
        provider_type=confirmation.provider_type,
        provider_reference=derive_refund_provider_reference(scope, command.request_id),
        currency_code=amount.currency.code,
        minor_units=amount.currency.minor_units,
        amount=amount.amount,
        instructed_at=command.instructed_at or datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def get_refundable_cash_v1(
    db: Session, *, scope: TenantScope, query: GetRefundableCashV1
) -> RefundableCashObservationV1:
    """Cash still refundable on one confirmation, without flushing caller state."""
    tenant_id = _tenant(scope)
    with db.no_autoflush:
        confirmation = _confirmation(db, tenant_id, query.confirmation_id)
        if confirmation is None:
            raise ConfirmationNotFound(
                "payment confirmation was not found in the tenant"
            )
        confirmed = _confirmation_money(confirmation)
        committed = _committed(db, tenant_id, confirmation.id)
    currency = confirmed.currency
    return RefundableCashObservationV1(
        scope=scope,
        confirmation_id=confirmation.id,
        confirmed=confirmed,
        committed=Money.of(committed, currency),
        refundable=Money.of(confirmed.amount - committed, currency),
    )


def _digest(values: Sequence[object]) -> str:
    frames = [str(item).encode("utf-8") for item in (_OUTCOME_DOMAIN, *values)]
    return hashlib.sha256(
        b"".join(str(len(item)).encode("ascii") + b":" + item for item in frames)
    ).hexdigest()


class _Shape:
    """A validated, normalized outcome command."""

    __slots__ = (
        "amount",
        "correction_id",
        "fingerprint",
        "kind",
        "outcome_reference",
        "provider_type",
        "refund_reference",
    )

    def __init__(self, command: ObserveCashOutcome) -> None:
        kind = CashOutcomeKind(command.kind)
        self.kind = kind
        self.amount = _positive(command.amount, "outcome amount")
        refund = kind in REFUND_OUTCOME_KINDS
        if kind is CashOutcomeKind.ADMIN_CORRECTION:
            if not isinstance(command.correction_id, UUID):
                raise ValueError("an admin correction requires a correction id")
            if (
                command.provider_type is not None
                or command.outcome_reference is not None
                or command.refund_reference is not None
            ):
                raise ValueError(
                    "an admin correction is identified by its correction id alone"
                )
            self.correction_id: UUID | None = command.correction_id
            self.provider_type: str | None = None
            self.outcome_reference: str | None = None
            self.refund_reference: str | None = None
        else:
            if command.correction_id is not None:
                raise ValueError("only an admin correction carries a correction id")
            if command.provider_type is None or command.outcome_reference is None:
                raise ValueError("an outcome requires a provider type and reference")
            if refund != (command.refund_reference is not None):
                raise ValueError(
                    "a refund outcome, and only a refund outcome, names the "
                    "refund reference it answers"
                )
            self.correction_id = None
            self.provider_type = _required(
                command.provider_type, "provider type"
            ).upper()
            self.outcome_reference = _required(
                command.outcome_reference, "outcome reference"
            )
            self.refund_reference = (
                _required(command.refund_reference, "refund reference")
                if command.refund_reference is not None
                else None
            )
        self.fingerprint = _digest(
            [
                kind.value,
                command.confirmation_id,
                self.amount.amount,
                self.amount.currency.code,
                self.amount.currency.minor_units,
                self.provider_type or "",
                self.outcome_reference or "",
                self.correction_id or "",
                self.refund_reference or "",
            ]
        )


def _existing_outcome(
    db: Session, tenant_id: UUID, shape: _Shape
) -> PaymentCashOutcome | None:
    if shape.correction_id is not None:
        clause = PaymentCashOutcome.correction_id == shape.correction_id
    else:
        clause = (PaymentCashOutcome.provider_type == shape.provider_type) & (
            PaymentCashOutcome.outcome_reference == shape.outcome_reference
        )
    return db.scalar(
        select(PaymentCashOutcome)
        .where(PaymentCashOutcome.tenant_id == tenant_id, clause)
        .execution_options(populate_existing=True)
    )


def _replay(
    row: PaymentCashOutcome, shape: _Shape, observed_at: datetime | None
) -> PaymentCashOutcome:
    if row.request_fingerprint != shape.fingerprint:
        raise Conflict("outcome identity was reused for different evidence")
    if observed_at is not None and _utc_moment(observed_at) != _utc_moment(
        row.observed_at
    ):
        raise Conflict("outcome identity was reused for a different observation time")
    return row


def _attention(
    db: Session,
    tenant_id: UUID,
    shape: _Shape,
    confirmation: PaymentConfirmation | None,
) -> tuple[AttentionReason | None, RefundInstruction | None]:
    """Decide whether the outcome can be applied. Order is part of the contract."""
    if confirmation is None:
        return AttentionReason.UNKNOWN_CONFIRMATION, None
    if (
        shape.provider_type is not None
        and shape.provider_type != confirmation.provider_type
    ):
        return AttentionReason.PROVIDER_MISMATCH, None
    confirmed = _confirmation_money(confirmation)
    if (
        shape.amount.currency.code != confirmed.currency.code
        or shape.amount.currency.minor_units != confirmed.currency.minor_units
    ):
        return AttentionReason.CURRENCY_MISMATCH, None
    if shape.amount.amount > confirmed.amount:
        return AttentionReason.EXCEEDS_CONFIRMED_CASH, None
    if shape.kind not in REFUND_OUTCOME_KINDS:
        return None, None
    instruction = db.scalar(
        select(RefundInstruction)
        .where(
            RefundInstruction.tenant_id == tenant_id,
            RefundInstruction.provider_type == shape.provider_type,
            RefundInstruction.provider_reference == shape.refund_reference,
        )
        .execution_options(populate_existing=True)
    )
    if instruction is None:
        return AttentionReason.UNKNOWN_REFUND_INSTRUCTION, None
    if instruction.confirmation_id != confirmation.id:
        return AttentionReason.CONFIRMATION_MISMATCH, None
    if instruction.amount != shape.amount.amount:
        return AttentionReason.AMOUNT_MISMATCH, instruction
    terminal = db.scalar(
        select(PaymentCashOutcome.id).where(
            PaymentCashOutcome.tenant_id == tenant_id,
            PaymentCashOutcome.instruction_id == instruction.id,
            PaymentCashOutcome.state == CashOutcomeState.OBSERVED,
            PaymentCashOutcome.kind.in_(REFUND_OUTCOME_KINDS),
        )
    )
    if terminal is not None:
        return AttentionReason.TERMINAL_CONFLICT, instruction
    return None, instruction


def observe_cash_outcome(
    db: Session, *, scope: TenantScope, command: ObserveCashOutcome
) -> PaymentCashOutcome:
    """Record an outcome observed after confirmation; never drop it.

    An outcome that names no confirmation of this tenant, or that disagrees with
    the confirmation or refund instruction it names, is stored as
    `ATTENTION_REQUIRED` with a reason for the owning flow to resolve. Replay of
    the same identity returns the stored row, including a parked one; changed
    evidence conflicts. Applied refund outcomes release (failed) or keep
    (completed) the cash an instruction holds.
    """
    tenant_id = _tenant(scope)
    if not isinstance(command.confirmation_id, UUID):
        raise TypeError("confirmation id must be a UUID")
    shape = _Shape(command)
    if _require_read_committed(db):
        # Identity fence for the case with no intent row to lock (an unmatched
        # outcome). Always taken BEFORE the intent lock and never by
        # `instruct_refund`, so lock order cannot cycle.
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {
                "key": f"{_OUTCOME_DOMAIN}:{tenant_id}:"
                f"{shape.correction_id or shape.provider_type}:"
                f"{shape.outcome_reference or ''}"
            },
        )
    confirmation = _confirmation(db, tenant_id, command.confirmation_id)
    if confirmation is not None:
        # Same serialization point as instruct_refund.
        _intent(db, tenant_id, confirmation.intent_id)

    existing = _existing_outcome(db, tenant_id, shape)
    if existing is not None:
        return _replay(existing, shape, command.observed_at)

    reason, instruction = _attention(db, tenant_id, shape, confirmation)
    now = datetime.now(UTC)
    row = PaymentCashOutcome(
        tenant_id=tenant_id,
        kind=shape.kind,
        state=(
            CashOutcomeState.ATTENTION_REQUIRED
            if reason is not None
            else CashOutcomeState.OBSERVED
        ),
        attention_reason=reason,
        claimed_confirmation_id=command.confirmation_id,
        confirmation_id=confirmation.id if confirmation is not None else None,
        instruction_id=instruction.id if instruction is not None else None,
        provider_type=shape.provider_type,
        outcome_reference=shape.outcome_reference,
        correction_id=shape.correction_id,
        refund_reference=shape.refund_reference,
        currency_code=shape.amount.currency.code,
        minor_units=shape.amount.currency.minor_units,
        amount=shape.amount.amount,
        request_fingerprint=shape.fingerprint,
        observed_at=command.observed_at or now,
        recorded_at=now,
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "derive_refund_provider_reference",
    "get_refundable_cash_v1",
    "instruct_refund",
    "observe_cash_outcome",
]
