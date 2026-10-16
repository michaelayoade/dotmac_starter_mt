"""Payment intent and confirmation correlation.

Ported from `dotmac_sub`'s `topup_intents`/`payments` path. What this module
owns is narrow on purpose: an intent to pay, the evidence that it was paid, and
the correlation between the two. It owns no receivable — Billing decides what a
confirmed payment settles and in what order — no bank account, and no provider
transport.

Two rules carry over from the source and one defect does not:

* An external settlement fact is correlated to an intent addressed by ITS OWN
  reference, never chosen from provider metadata. Provider payload is
  corroboration; the destination was bound before any provider I/O.
* Amounts are exact and currency-checked; a confirmation in another currency is
  refused rather than coerced.
* Sub's uniqueness on `external_id` was partial on `provider_id IS NOT NULL`,
  so CRM-origin payments fell outside it and needed a second index to stop a
  concurrent push double-recording cash. Here the uniqueness is unconditional
  per (tenant, provider_type, external_reference), so there is no gap class.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_payments.contracts import (
    ConfirmationNotFound,
    ConfirmationSource,
    ConfirmedCashObservationV1,
    Conflict,
    GetConfirmedCashV1,
    GetPaymentIntentV1,
    OpenPaymentIntent,
    PaymentIntentObservationV1,
    PaymentIntentStatus,
    PaymentPurpose,
    RecordConfirmation,
    ReviewTransferProof,
    SubmitTransferProof,
    TransferProofState,
    UnresolvedMoneyPrecision,
)
from dotmac_payments.models import (
    PaymentConfirmation,
    PaymentIntent,
    PaymentTransferProof,
)

_OPEN = PaymentIntentStatus.PENDING


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-payments requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _positive(money: Money, field: str) -> Money:
    if (
        type(money.currency.minor_units) is not int
        or not 0 <= money.currency.minor_units <= 6
    ):
        raise Conflict(f"{field} currency scale must be an integer from 0 to 6")
    if not money.amount.is_finite():
        raise Conflict(f"{field} must be finite")
    if money.amount >= Decimal("100000000000000"):
        raise Conflict(f"{field} does not fit Numeric(20, 6)")
    if money.amount <= Decimal(0):
        raise Conflict(f"{field} must be greater than zero")
    return money


def _persisted_money(amount: Decimal, code: str, scale: int | None) -> Money:
    if scale is None:
        raise UnresolvedMoneyPrecision("stored currency precision is unresolved")
    if type(scale) is not int or not 0 <= scale <= 6:
        raise UnresolvedMoneyPrecision("stored currency precision is unsupported")
    # Inspect before Money construction, whose public constructor quantizes.
    if not amount.is_finite() or not Decimal(0) < amount < Decimal("100000000000000"):
        raise Conflict("stored amount does not fit positive finite Numeric(20, 6)")
    stored_currency = Currency(code, scale)
    if stored_currency.code != code:
        raise Conflict("stored currency code is not canonical")
    money = Money.of(amount, stored_currency)
    if money.amount != amount:
        raise UnresolvedMoneyPrecision("stored amount conflicts with its precision")
    return money


def _intent(db: Session, tenant_id: UUID, intent_id: UUID) -> PaymentIntent:
    row = db.scalar(
        select(PaymentIntent)
        .where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == intent_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise Conflict("payment intent was not found in the tenant")
    return row


def _utc_moment(value: datetime) -> datetime:
    """Compare persisted instants consistently across timezone-aware drivers.

    SQLite's test adapter returns naive UTC values for timezone-bearing columns;
    production PostgreSQL returns aware instants. This changes no stored fact.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _same_currency(intent: PaymentIntent, money: Money, field: str) -> None:
    if intent.minor_units is None:
        raise UnresolvedMoneyPrecision(
            "payment intent currency precision is unresolved"
        )
    if (
        money.currency.code != intent.currency_code
        or money.currency.minor_units != intent.minor_units
    ):
        raise Conflict(
            f"{field} currency {money.currency.code}/{money.currency.minor_units} "
            f"does not match the intent currency "
            f"{intent.currency_code}/{intent.minor_units}"
        )


def open_payment_intent(
    db: Session, *, scope: TenantScope, command: OpenPaymentIntent
) -> PaymentIntent:
    """Open or replay one reference bound to the same normalized request.

    Destination and expiry are exact optional bindings. Only an omitted
    opening time retains its stored value without comparison on replay.
    """
    tenant_id = _tenant(scope)
    reference = _required(command.reference, "reference")
    payer_reference = _required(command.payer_reference, "payer reference")
    provider_type = _required(command.provider_type, "provider type").upper()
    channel = _required(command.channel, "channel").upper()
    requested = _positive(command.requested, "requested amount")
    if command.purpose == PaymentPurpose.PURCHASE_FUNDING and (
        command.target_reference is None or not command.target_reference.strip()
    ):
        raise Conflict("purchase funding requires a nonblank target reference")
    existing = db.scalar(
        select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id, PaymentIntent.reference == reference
        )
    )
    if existing is not None:
        if (
            existing.requested_amount != requested.amount
            or existing.currency_code != requested.currency.code
            or existing.minor_units != requested.currency.minor_units
        ):
            raise Conflict("reference was reused for a different amount")
        if existing.payer_reference != payer_reference:
            raise Conflict("reference was reused for a different payer")
        if existing.target_reference != command.target_reference:
            raise Conflict("reference was reused for a different target")
        if existing.purpose != command.purpose:
            raise Conflict("reference was reused for a different purpose")
        if existing.provider_type != provider_type:
            raise Conflict("reference was reused for a different provider")
        if existing.channel != channel:
            raise Conflict("reference was reused for a different channel")
        if (existing.expires_at is None) != (command.expires_at is None) or (
            existing.expires_at is not None
            and command.expires_at is not None
            and _utc_moment(existing.expires_at) != _utc_moment(command.expires_at)
        ):
            raise Conflict("reference was reused for a different expiry")
        # A replay keeps the STORED opening time. Omitting `opened_at` means
        # "now" only when there is nothing to replay — re-deriving it here
        # would let a retry silently move an authoritative business fact.
        # An explicitly different one is a conflict, not a correction: two
        # callers disagreeing about when the payer was asked to pay is the
        # same class of defect as disagreeing about the amount.
        if command.opened_at is not None and _utc_moment(
            command.opened_at
        ) != _utc_moment(existing.opened_at):
            raise Conflict("reference was reused for a different opening time")
        return existing
    opened_at = command.opened_at or datetime.now(UTC)
    if command.expires_at is not None and command.expires_at <= opened_at:
        raise Conflict("expiry must be after the opening time")
    row = PaymentIntent(
        tenant_id=tenant_id,
        reference=reference,
        payer_reference=payer_reference,
        target_reference=command.target_reference,
        purpose=command.purpose,
        provider_type=provider_type,
        channel=channel,
        currency_code=requested.currency.code,
        minor_units=requested.currency.minor_units,
        requested_amount=requested.amount,
        status=_OPEN,
        opened_at=opened_at,
        expires_at=command.expires_at,
    )
    db.add(row)
    db.flush()
    return row


def cancel_payment_intent(
    db: Session,
    *,
    scope: TenantScope,
    intent_id: UUID,
    cancelled_at: datetime | None = None,
) -> PaymentIntent:
    """Cancel a pending intent, settling it at `cancelled_at`.

    Takes the caller's moment for the same reason `expire_payment_intent`
    does: `settled_at` is compared against the source system during the
    backfill shadow, and a cancelled history stamped with import wall time
    reports drift on every row.
    """
    intent = _intent(db, _tenant(scope), intent_id)
    moment = cancelled_at or datetime.now(UTC)
    if intent.status is not _OPEN:
        raise Conflict("only a pending payment intent can be cancelled")
    if _utc_moment(moment) < _utc_moment(intent.opened_at):
        raise Conflict("cancellation cannot precede the opening time")
    intent.status = PaymentIntentStatus.CANCELLED
    intent.settled_at = moment
    db.flush()
    return intent


def expire_payment_intent(
    db: Session, *, scope: TenantScope, intent_id: UUID, now: datetime | None = None
) -> PaymentIntent:
    intent = _intent(db, _tenant(scope), intent_id)
    moment = now or datetime.now(UTC)
    if intent.status is not _OPEN:
        raise Conflict("only a pending payment intent can expire")
    if intent.expires_at is None or _utc_moment(intent.expires_at) > _utc_moment(
        moment
    ):
        raise Conflict("payment intent has not reached its expiry")
    intent.status = PaymentIntentStatus.EXPIRED
    intent.settled_at = moment
    db.flush()
    return intent


def submit_transfer_proof(
    db: Session, *, scope: TenantScope, command: SubmitTransferProof
) -> PaymentTransferProof:
    tenant_id = _tenant(scope)
    intent = _intent(db, tenant_id, command.intent_id)
    if intent.status is not _OPEN:
        raise Conflict("a transfer proof needs a pending payment intent")
    declared = _positive(command.declared, "declared amount")
    _same_currency(intent, declared, "declared amount")
    row = PaymentTransferProof(
        tenant_id=tenant_id,
        intent_id=intent.id,
        submitted_reference=_required(
            command.submitted_reference, "submitted reference"
        ),
        document_reference=_required(command.document_reference, "document reference"),
        currency_code=declared.currency.code,
        minor_units=declared.currency.minor_units,
        declared_amount=declared.amount,
        state=TransferProofState.SUBMITTED,
        declared_at=command.declared_at or datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def review_transfer_proof(
    db: Session, *, scope: TenantScope, command: ReviewTransferProof
) -> PaymentTransferProof:
    """Accept or reject a proof; acceptance is what correlates it to the intent.

    An accepted proof writes a `TRANSFER_PROOF` confirmation through the same
    single correlation path a provider callback uses, so there is exactly one
    place an intent can become CONFIRMED.
    """
    tenant_id = _tenant(scope)
    # Discover the parent without taking a child lock. Every lifecycle writer
    # acquires the intent first, then its proof, so provider and review paths
    # cannot invert lock order or confirm from a stale PENDING ORM instance.
    intent_id = db.scalar(
        select(PaymentTransferProof.intent_id).where(
            PaymentTransferProof.tenant_id == tenant_id,
            PaymentTransferProof.id == command.proof_id,
        )
    )
    if intent_id is None:
        raise Conflict("transfer proof was not found in the tenant")
    _intent(db, tenant_id, intent_id)
    proof = db.scalar(
        select(PaymentTransferProof)
        .where(
            PaymentTransferProof.tenant_id == tenant_id,
            PaymentTransferProof.id == command.proof_id,
            PaymentTransferProof.intent_id == intent_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if proof is None:
        raise Conflict("transfer proof was not found in the tenant")
    if proof.state is not TransferProofState.SUBMITTED:
        raise Conflict("transfer proof has already been reviewed")
    reviewer = _required(command.reviewer, "reviewer")
    reviewed_at = command.reviewed_at or datetime.now(UTC)
    rejection_reason: str | None = None
    if not command.accept:
        rejection_reason = _required(command.rejection_reason or "", "rejection reason")
    else:
        # Validate/correlate before mutating proof evidence: an expected
        # confirmation conflict must not autoflush an ACCEPTED proof.
        record_confirmation(
            db,
            scope=scope,
            command=RecordConfirmation(
                intent_id=proof.intent_id,
                source=ConfirmationSource.TRANSFER_PROOF,
                external_reference=proof.submitted_reference,
                confirmed=_persisted_money(
                    Decimal(proof.declared_amount),
                    proof.currency_code,
                    proof.minor_units,
                ),
                observed_at=reviewed_at,
            ),
        )
    proof.reviewer = reviewer
    proof.reviewed_at = reviewed_at
    proof.state = (
        TransferProofState.ACCEPTED if command.accept else TransferProofState.REJECTED
    )
    proof.rejection_reason = rejection_reason
    db.flush()
    return proof


def record_confirmation(
    db: Session, *, scope: TenantScope, command: RecordConfirmation
) -> PaymentConfirmation:
    """Correlate one external settlement fact to the intent it belongs to."""
    tenant_id = _tenant(scope)
    intent = _intent(db, tenant_id, command.intent_id)
    external_reference = _required(command.external_reference, "external reference")
    confirmed = _positive(command.confirmed, "confirmed amount")
    _same_currency(intent, confirmed, "confirmed amount")

    replay = db.scalar(
        select(PaymentConfirmation).where(
            PaymentConfirmation.tenant_id == tenant_id,
            PaymentConfirmation.provider_type == intent.provider_type,
            PaymentConfirmation.external_reference == external_reference,
        )
    )
    if replay is not None:
        if replay.intent_id != intent.id:
            raise Conflict(
                "external reference is already correlated to a different intent"
            )
        if (
            replay.confirmed_amount != confirmed.amount
            or replay.currency_code != confirmed.currency.code
            or replay.minor_units != confirmed.currency.minor_units
            or replay.source != command.source
        ):
            raise Conflict(
                "external reference was reused for different confirmation evidence"
            )
        if command.observed_at is not None and _utc_moment(
            command.observed_at
        ) != _utc_moment(replay.observed_at):
            raise Conflict(
                "external reference was reused for a different observation time"
            )
        return replay

    if intent.status is not _OPEN:
        raise Conflict("only a pending payment intent can be confirmed")
    observed_at = command.observed_at or datetime.now(UTC)
    if intent.expires_at is not None and _utc_moment(observed_at) > _utc_moment(
        intent.expires_at
    ):
        raise Conflict("payment intent had already expired when this was observed")

    row = PaymentConfirmation(
        tenant_id=tenant_id,
        intent_id=intent.id,
        source=command.source,
        provider_type=intent.provider_type,
        external_reference=external_reference,
        currency_code=confirmed.currency.code,
        minor_units=confirmed.currency.minor_units,
        confirmed_amount=confirmed.amount,
        observed_at=observed_at,
    )
    db.add(row)
    intent.status = PaymentIntentStatus.CONFIRMED
    intent.confirmed_amount = confirmed.amount
    intent.settled_at = observed_at
    db.flush()
    return row


def get_payment_intent_v1(
    db: Session, *, scope: TenantScope, query: GetPaymentIntentV1
) -> PaymentIntentObservationV1 | None:
    """Observe the persisted reference binding without flushing caller changes.

    Unknown and foreign references are indistinguishable. This read owns no
    lifecycle transition, locks or transaction completion; the caller may roll
    back its own flushed, uncommitted intent after observing it.
    """
    tenant_id = _tenant(scope)
    reference = _required(query.reference, "reference")
    table = PaymentIntent.__table__
    with db.no_autoflush:
        row = (
            db.execute(
                select(table).where(
                    table.c.tenant_id == tenant_id, table.c.reference == reference
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return None
    requested = _persisted_money(
        row["requested_amount"], row["currency_code"], row["minor_units"]
    )
    confirmed = (
        _persisted_money(
            row["confirmed_amount"], row["currency_code"], row["minor_units"]
        )
        if row["confirmed_amount"] is not None
        else None
    )
    return PaymentIntentObservationV1(
        scope=scope,
        intent_id=row["id"],
        reference=row["reference"],
        payer_reference=row["payer_reference"],
        target_reference=row["target_reference"],
        purpose=PaymentPurpose(row["purpose"]),
        requested=requested,
        confirmed=confirmed,
        provider_type=row["provider_type"],
        channel=row["channel"],
        status=PaymentIntentStatus(row["status"]),
        opened_at=_utc_moment(row["opened_at"]),
        expires_at=_utc_moment(row["expires_at"])
        if row["expires_at"] is not None
        else None,
        settled_at=_utc_moment(row["settled_at"])
        if row["settled_at"] is not None
        else None,
    )


def get_confirmed_cash_v1(
    db: Session, *, scope: TenantScope, query: GetConfirmedCashV1
) -> ConfirmedCashObservationV1:
    """Read immutable persisted scalars without flushing caller-owned changes.

    This observation neither commits the caller's transaction nor resolves a
    payer to a Billing account. Pending ORM changes cannot rewrite evidence.
    """
    tenant_id = _tenant(scope)
    table = PaymentConfirmation.__table__
    with db.no_autoflush:
        row = (
            db.execute(
                select(table).where(
                    table.c.tenant_id == tenant_id, table.c.id == query.confirmation_id
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise ConfirmationNotFound("payment confirmation was not found in the tenant")
    confirmed = _persisted_money(
        row["confirmed_amount"], row["currency_code"], row["minor_units"]
    )
    return ConfirmedCashObservationV1(
        scope=scope,
        confirmation_id=row["id"],
        intent_id=row["intent_id"],
        confirmed=confirmed,
        source=ConfirmationSource(row["source"]),
        provider_type=row["provider_type"],
        external_reference=row["external_reference"],
        observed_at=_utc_moment(row["observed_at"]),
    )


__all__ = [
    "get_confirmed_cash_v1",
    "get_payment_intent_v1",
    "cancel_payment_intent",
    "expire_payment_intent",
    "open_payment_intent",
    "record_confirmation",
    "review_transfer_proof",
    "submit_transfer_proof",
]
