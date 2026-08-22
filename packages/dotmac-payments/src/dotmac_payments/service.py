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
from dotmac_kernel.money import Money, currency
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_payments.contracts import (
    ConfirmationSource,
    Conflict,
    OpenPaymentIntent,
    PaymentIntentStatus,
    RecordConfirmation,
    ReviewTransferProof,
    SubmitTransferProof,
    TransferProofState,
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
    if money.amount <= Decimal(0):
        raise Conflict(f"{field} must be greater than zero")
    return money


def _intent(db: Session, tenant_id: UUID, intent_id: UUID) -> PaymentIntent:
    row = db.scalar(
        select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id, PaymentIntent.id == intent_id
        )
    )
    if row is None:
        raise Conflict("payment intent was not found in the tenant")
    return row


def _same_currency(intent: PaymentIntent, money: Money, field: str) -> None:
    if money.currency.code != intent.currency_code:
        raise Conflict(
            f"{field} currency {money.currency.code} does not match the intent "
            f"currency {intent.currency_code}"
        )


def open_payment_intent(
    db: Session, *, scope: TenantScope, command: OpenPaymentIntent
) -> PaymentIntent:
    """Open an intent, idempotently on its own `reference`."""
    tenant_id = _tenant(scope)
    reference = _required(command.reference, "reference")
    requested = _positive(command.requested, "requested amount")
    existing = db.scalar(
        select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id, PaymentIntent.reference == reference
        )
    )
    if existing is not None:
        if (
            existing.requested_amount != requested.amount
            or existing.currency_code != requested.currency.code
        ):
            raise Conflict("reference was reused for a different amount")
        if existing.payer_reference != command.payer_reference.strip():
            raise Conflict("reference was reused for a different payer")
        return existing
    now = datetime.now(UTC)
    if command.expires_at is not None and command.expires_at <= now:
        raise Conflict("expiry must be in the future")
    row = PaymentIntent(
        tenant_id=tenant_id,
        reference=reference,
        payer_reference=_required(command.payer_reference, "payer reference"),
        target_reference=command.target_reference,
        purpose=command.purpose,
        provider_type=_required(command.provider_type, "provider type").upper(),
        channel=_required(command.channel, "channel").upper(),
        currency_code=requested.currency.code,
        requested_amount=requested.amount,
        status=_OPEN,
        opened_at=now,
        expires_at=command.expires_at,
    )
    db.add(row)
    db.flush()
    return row


def cancel_payment_intent(
    db: Session, *, scope: TenantScope, intent_id: UUID
) -> PaymentIntent:
    intent = _intent(db, _tenant(scope), intent_id)
    if intent.status is not _OPEN:
        raise Conflict("only a pending payment intent can be cancelled")
    intent.status = PaymentIntentStatus.CANCELLED
    intent.settled_at = datetime.now(UTC)
    db.flush()
    return intent


def expire_payment_intent(
    db: Session, *, scope: TenantScope, intent_id: UUID, now: datetime | None = None
) -> PaymentIntent:
    intent = _intent(db, _tenant(scope), intent_id)
    moment = now or datetime.now(UTC)
    if intent.status is not _OPEN:
        raise Conflict("only a pending payment intent can expire")
    if intent.expires_at is None or intent.expires_at > moment:
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
    proof = db.scalar(
        select(PaymentTransferProof).where(
            PaymentTransferProof.tenant_id == tenant_id,
            PaymentTransferProof.id == command.proof_id,
        )
    )
    if proof is None:
        raise Conflict("transfer proof was not found in the tenant")
    if proof.state is not TransferProofState.SUBMITTED:
        raise Conflict("transfer proof has already been reviewed")
    reviewed_at = command.reviewed_at or datetime.now(UTC)
    proof.reviewer = _required(command.reviewer, "reviewer")
    proof.reviewed_at = reviewed_at
    if not command.accept:
        proof.state = TransferProofState.REJECTED
        proof.rejection_reason = _required(
            command.rejection_reason or "", "rejection reason"
        )
        db.flush()
        return proof
    proof.state = TransferProofState.ACCEPTED
    record_confirmation(
        db,
        scope=scope,
        command=RecordConfirmation(
            intent_id=proof.intent_id,
            source=ConfirmationSource.TRANSFER_PROOF,
            external_reference=proof.submitted_reference,
            confirmed=Money.of(
                Decimal(proof.declared_amount), currency(proof.currency_code)
            ),
            observed_at=reviewed_at,
        ),
    )
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
        return replay

    if intent.status is not _OPEN:
        raise Conflict("only a pending payment intent can be confirmed")
    observed_at = command.observed_at or datetime.now(UTC)
    if intent.expires_at is not None and observed_at > intent.expires_at:
        raise Conflict("payment intent had already expired when this was observed")

    row = PaymentConfirmation(
        tenant_id=tenant_id,
        intent_id=intent.id,
        source=command.source,
        provider_type=intent.provider_type,
        external_reference=external_reference,
        currency_code=confirmed.currency.code,
        confirmed_amount=confirmed.amount,
        observed_at=observed_at,
    )
    db.add(row)
    intent.status = PaymentIntentStatus.CONFIRMED
    intent.confirmed_amount = confirmed.amount
    intent.settled_at = observed_at
    db.flush()
    return row


__all__ = [
    "cancel_payment_intent",
    "expire_payment_intent",
    "open_payment_intent",
    "record_confirmation",
    "review_transfer_proof",
    "submit_transfer_proof",
]
