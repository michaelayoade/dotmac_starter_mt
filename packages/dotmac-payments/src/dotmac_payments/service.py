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

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging.outbox import enqueue_event
from dotmac_kernel.money import Currency, Money
from sqlalchemy import select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from dotmac_payments.contracts import (
    AcceptMerchantContextV1,
    AuthorizedMerchantReviewV1,
    ConfirmationNotFound,
    ConfirmationSource,
    ConfirmedCashObservationV1,
    Conflict,
    GetConfirmedCashV1,
    GetOriginalMerchantContextV1,
    GetPaymentIntentV1,
    MerchantConfigurationV1,
    MerchantContextEndV1,
    MerchantContextV1,
    MerchantReviewAuthority,
    MerchantReviewEvidenceV1,
    MerchantReviewOperation,
    MerchantTransportContextV1,
    OpenPaymentIntent,
    PaymentIntentObservationV1,
    PaymentIntentStatus,
    PaymentPurpose,
    RecordConfirmation,
    ResolveMerchantContextV1,
    ReviewTransferProof,
    RevokeMerchantContextV1,
    SubmitTransferProof,
    TransferProofState,
    UnresolvedMoneyPrecision,
)
from dotmac_payments.models import (
    MerchantContext,
    MerchantContextEnd,
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
    "accept_merchant_context",
    "revoke_merchant_context",
    "resolve_current_merchant_context",
    "get_original_merchant_context",
    "merchant_review_request_fingerprint",
    "get_confirmed_cash_v1",
    "get_payment_intent_v1",
    "cancel_payment_intent",
    "expire_payment_intent",
    "open_payment_intent",
    "record_confirmation",
    "review_transfer_proof",
    "submit_transfer_proof",
]


def _merchant_text(value: object, maximum: int, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 for char in value)
    ):
        raise Conflict(f"{field} must be exact nonblank text within its storage bound")
    return value


def _merchant_digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise Conflict("merchant review digest must be lowercase SHA-256")
    return value


def _merchant_instant(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise Conflict("merchant review instants must include a timezone")
    return value.astimezone(UTC)


def _merchant_uuid(value: object) -> UUID:
    if type(value) is not UUID:
        raise Conflict("merchant review identifiers must be UUID values")
    return value


def _transport_values(value: MerchantTransportContextV1) -> tuple[str, ...]:
    if type(value) is not MerchantTransportContextV1:
        raise Conflict("typed merchant transport context is required")
    return (
        _merchant_text(value.provider_type, 40, "provider"),
        _merchant_text(value.environment, 40, "environment"),
        _merchant_text(value.installation_reference, 160, "installation"),
        _merchant_text(value.capability_reference, 160, "capability"),
        _merchant_text(value.config_revision_reference, 160, "configuration revision"),
        _merchant_digest(value.config_digest),
    )


def _review_evidence(value: MerchantReviewEvidenceV1) -> tuple[str, str]:
    if type(value) is not MerchantReviewEvidenceV1:
        raise Conflict("typed merchant review evidence is required")
    return (
        _merchant_text(value.artifact_reference, 512, "review artifact"),
        _merchant_digest(value.sha256),
    )


def merchant_review_request_fingerprint(
    command: AcceptMerchantContextV1 | RevokeMerchantContextV1,
) -> str:
    """Bind the complete validated human-review request; not an economic key."""
    if type(command) is AcceptMerchantContextV1:
        if type(command.configuration) is not MerchantConfigurationV1:
            raise Conflict("typed merchant configuration is required")
        start = _merchant_instant(command.effective_from)
        until = (
            _merchant_instant(command.effective_until)
            if command.effective_until is not None
            else None
        )
        if until is not None and until <= start:
            raise Conflict("merchant context interval must be nonempty")
        values: tuple[object, ...] = (
            "merchant-review-request-v1",
            MerchantReviewOperation.ACCEPT.value,
            str(_merchant_uuid(command.command_id)),
            _transport_values(command.configuration.transport),
            _merchant_text(
                command.configuration.physical_merchant_id,
                160,
                "physical merchant identity",
            ),
            _review_evidence(command.evidence),
            start.isoformat(),
            until.isoformat() if until is not None else None,
            str(_merchant_uuid(command.supersedes_context_id))
            if command.supersedes_context_id is not None
            else None,
        )
    elif type(command) is RevokeMerchantContextV1:
        values = (
            "merchant-review-request-v1",
            MerchantReviewOperation.REVOKE.value,
            str(_merchant_uuid(command.command_id)),
            str(_merchant_uuid(command.context_id)),
            _review_evidence(command.evidence),
        )
    else:
        raise Conflict("typed merchant review command is required")
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _merchant_authorization(
    db: Session,
    *,
    scope: TenantScope,
    command: AcceptMerchantContextV1 | RevokeMerchantContextV1,
    authority: MerchantReviewAuthority,
) -> AuthorizedMerchantReviewV1:
    if type(scope) is not TenantScope:
        raise Conflict("merchant review requires an exact TenantScope")
    tenant_id = _merchant_uuid(_tenant(scope))
    if not callable(getattr(authority, "authorize", None)):
        raise Conflict(
            "an installed product-owned merchant review authority is required"
        )
    fingerprint = merchant_review_request_fingerprint(command)
    operation = (
        MerchantReviewOperation.ACCEPT
        if type(command) is AcceptMerchantContextV1
        else MerchantReviewOperation.REVOKE
    )
    with db.no_autoflush:
        result = authority.authorize(
            db, scope=scope, command=command, request_fingerprint=fingerprint
        )
    if merchant_review_request_fingerprint(command) != fingerprint:
        raise Conflict("merchant review request changed during authorization")
    if (
        type(result) is not AuthorizedMerchantReviewV1
        or type(result.scope) is not TenantScope
        or _merchant_uuid(result.scope.tenant_id) != tenant_id
        or _merchant_uuid(result.command_id) != command.command_id
        or type(result.operation) is not MerchantReviewOperation
        or result.operation != operation
        or _merchant_digest(result.request_fingerprint) != fingerprint
    ):
        raise Conflict(
            "merchant review authority attestation does not bind the exact request"
        )
    _merchant_text(result.reviewer_reference, 160, "authenticated reviewer")
    if _merchant_instant(result.reviewed_at) > datetime.now(UTC):
        raise Conflict("merchant review authority supplied a future review instant")
    return result


def _merchant_lock(
    db: Session,
    tenant_id: UUID,
    command_id: UUID,
    transport: MerchantTransportContextV1,
) -> None:
    # SQLite is the explicitly non-authoritative unit lane only. PostgreSQL
    # direct SQL is guarded identically by pm5, including isolation refusal.
    dialect = db.get_bind().dialect.name
    if dialect not in ("postgresql", "sqlite"):
        raise Conflict("merchant context persistence requires PostgreSQL")
    if dialect == "postgresql":
        settings = db.execute(
            text(
                "SELECT current_setting('transaction_isolation'), "
                "current_setting('transaction_read_only')"
            )
        ).one()
        if tuple(settings) != ("read committed", "off"):
            raise Conflict(
                "merchant context mutations require READ WRITE READ COMMITTED"
            )
        db.execute(
            text(
                "SELECT mod_payments.merchant_review_lock("
                "CAST(:tenant AS uuid),CAST(:command AS uuid),"
                "CAST(:installation AS text),CAST(:capability AS text))"
            ),
            {
                "tenant": str(tenant_id),
                "command": str(command_id),
                "installation": transport.installation_reference,
                "capability": transport.capability_reference,
            },
        )


def _merchant_end(row: MerchantContextEnd) -> MerchantContextEndV1:
    return MerchantContextEndV1(
        scope=TenantScope(row.tenant_id),
        end_id=row.id,
        command_id=row.command_id,
        context_id=row.context_id,
        operation=MerchantReviewOperation(row.operation),
        request_fingerprint=row.request_fingerprint,
        replacement_context_id=row.replacement_context_id,
        evidence=MerchantReviewEvidenceV1(row.evidence_reference, row.evidence_sha256),
        reviewer_reference=row.reviewer_reference,
        reviewed_at=_utc_moment(row.reviewed_at),
        effective_at=_utc_moment(row.effective_at),
    )


def _merchant_stored_instant(value: object) -> datetime:
    if type(value) is not datetime:
        raise Conflict("stored merchant review instant is invalid")
    return _utc_moment(value)


def _merchant_snapshot(values: RowMapping) -> MerchantContextV1:
    until = values["effective_until"]
    return MerchantContextV1(
        scope=TenantScope(_merchant_uuid(values["tenant_id"])),
        context_id=_merchant_uuid(values["id"]),
        command_id=_merchant_uuid(values["command_id"]),
        request_fingerprint=_merchant_digest(values["request_fingerprint"]),
        configuration=MerchantConfigurationV1(
            MerchantTransportContextV1(
                _merchant_text(values["provider_type"], 40, "stored provider"),
                _merchant_text(values["environment"], 40, "stored environment"),
                _merchant_text(
                    values["installation_reference"], 160, "stored installation"
                ),
                _merchant_text(
                    values["capability_reference"], 160, "stored capability"
                ),
                _merchant_text(
                    values["config_revision_reference"], 160, "stored config revision"
                ),
                _merchant_digest(values["config_digest"]),
            ),
            _merchant_text(values["physical_merchant_id"], 160, "stored merchant"),
        ),
        evidence=MerchantReviewEvidenceV1(
            _merchant_text(values["evidence_reference"], 512, "stored evidence"),
            _merchant_digest(values["evidence_sha256"]),
        ),
        reviewer_reference=_merchant_text(
            values["reviewer_reference"], 160, "stored reviewer"
        ),
        reviewed_at=_merchant_stored_instant(values["reviewed_at"]),
        effective_from=_merchant_stored_instant(values["effective_from"]),
        effective_until=_merchant_stored_instant(until) if until is not None else None,
    )


def _merchant_context_rows(
    db: Session, *conditions: ColumnElement[bool], lock: bool = False
) -> tuple[MerchantContextV1, ...]:
    statement = select(MerchantContext.__table__).where(*conditions)
    if lock:
        statement = statement.with_for_update()
    with db.no_autoflush:
        return tuple(
            _merchant_snapshot(values) for values in db.execute(statement).mappings()
        )


def _merchant_end_rows(
    db: Session, *conditions: ColumnElement[bool]
) -> tuple[MerchantContextEndV1, ...]:
    with db.no_autoflush:
        values = (
            db.execute(select(MerchantContextEnd.__table__).where(*conditions))
            .mappings()
            .all()
        )
    return tuple(
        MerchantContextEndV1(
            scope=TenantScope(_merchant_uuid(row["tenant_id"])),
            end_id=_merchant_uuid(row["id"]),
            command_id=_merchant_uuid(row["command_id"]),
            context_id=_merchant_uuid(row["context_id"]),
            operation=MerchantReviewOperation(row["operation"]),
            request_fingerprint=_merchant_digest(row["request_fingerprint"]),
            replacement_context_id=_merchant_uuid(row["replacement_context_id"])
            if row["replacement_context_id"] is not None
            else None,
            evidence=MerchantReviewEvidenceV1(
                _merchant_text(row["evidence_reference"], 512, "stored end evidence"),
                _merchant_digest(row["evidence_sha256"]),
            ),
            reviewer_reference=_merchant_text(
                row["reviewer_reference"], 160, "stored end reviewer"
            ),
            reviewed_at=_merchant_stored_instant(row["reviewed_at"]),
            effective_at=_merchant_stored_instant(row["effective_at"]),
        )
        for row in values
    )


def _require_merchant_acceptance_match(
    db: Session,
    *,
    row: MerchantContextV1,
    command: AcceptMerchantContextV1,
    review: AuthorizedMerchantReviewV1 | None = None,
) -> None:
    expected_until = (
        _merchant_instant(command.effective_until)
        if command.effective_until is not None
        else None
    )
    if (
        row.context_id != command.command_id
        or row.command_id != command.command_id
        or row.request_fingerprint != merchant_review_request_fingerprint(command)
        or row.configuration != command.configuration
        or row.evidence != command.evidence
        or row.effective_from != _merchant_instant(command.effective_from)
        or row.effective_until != expected_until
        or (
            review is not None
            and (
                row.scope != review.scope
                or row.reviewer_reference != review.reviewer_reference
                or row.reviewed_at != _merchant_instant(review.reviewed_at)
            )
        )
    ):
        raise Conflict("persisted merchant context differs from the authorized review")
    # The predecessor is an append-only separate fact; include its full review
    # tuple in acceptance/replay validation rather than trusting a digest alone.
    predecessors = _merchant_end_rows(
        db,
        MerchantContextEnd.tenant_id == row.scope.tenant_id,
        MerchantContextEnd.replacement_context_id == row.context_id,
    )
    if command.supersedes_context_id is None:
        if predecessors:
            raise Conflict("merchant acceptance has an unexpected predecessor")
    elif len(predecessors) != 1 or (
        predecessors[0].context_id != command.supersedes_context_id
        or predecessors[0].command_id != row.command_id
        or predecessors[0].operation != MerchantReviewOperation.ACCEPT
        or predecessors[0].request_fingerprint != row.request_fingerprint
        or predecessors[0].evidence != row.evidence
        or predecessors[0].reviewer_reference != row.reviewer_reference
        or predecessors[0].reviewed_at != row.reviewed_at
        or predecessors[0].effective_at != row.effective_from
    ):
        raise Conflict("merchant acceptance predecessor differs from authorized review")


def _require_merchant_revocation_match(
    row: MerchantContextEndV1,
    command: RevokeMerchantContextV1,
    *,
    review: AuthorizedMerchantReviewV1 | None = None,
) -> None:
    if (
        row.command_id != command.command_id
        or row.context_id != command.context_id
        or row.operation != MerchantReviewOperation.REVOKE
        or row.replacement_context_id is not None
        or row.request_fingerprint != merchant_review_request_fingerprint(command)
        or row.evidence != command.evidence
        or (
            review is not None
            and (
                row.scope != review.scope
                or row.reviewer_reference != review.reviewer_reference
                or row.reviewed_at != _merchant_instant(review.reviewed_at)
            )
        )
    ):
        raise Conflict(
            "persisted merchant termination differs from the authorized review"
        )


def _merchant_event(
    db: Session,
    *,
    scope: TenantScope,
    identifier: UUID,
    review: AuthorizedMerchantReviewV1,
    ended: bool,
) -> None:
    event_type = (
        "payments.merchant_context.ended.v1"
        if ended
        else "payments.merchant_context.accepted.v1"
    )
    payload = {
        "schema_version": 1,
        "context_id": str(identifier),
        "command_id": str(review.command_id),
        "request_fingerprint": review.request_fingerprint,
    }
    enqueue_event(
        db,
        tenant_id=scope.tenant_id,
        event_type=event_type,
        payload=payload,
        correlation_id=str(review.command_id),
    )


def accept_merchant_context(
    db: Session,
    *,
    scope: TenantScope,
    command: AcceptMerchantContextV1,
    authority: MerchantReviewAuthority,
) -> MerchantContextV1:
    """Record human-reviewed configuration authority in the caller transaction.

    The mandatory product-owned authority freshly revalidates the exact human
    review, including on replay. No artifact/provider verification, qualified
    cash, checkout availability or ledger consequence is claimed here.
    """
    if type(command) is not AcceptMerchantContextV1:
        raise Conflict("typed merchant acceptance command is required")
    review = _merchant_authorization(
        db, scope=scope, command=command, authority=authority
    )
    tenant_id = _tenant(scope)
    transport = command.configuration.transport
    _merchant_lock(db, tenant_id, command.command_id, transport)
    existing_rows = _merchant_context_rows(
        db,
        MerchantContext.tenant_id == tenant_id,
        MerchantContext.command_id == command.command_id,
    )
    if existing_rows:
        existing = existing_rows[0]
        if existing.request_fingerprint != review.request_fingerprint:
            raise Conflict("merchant review command already binds other material")
        _require_merchant_acceptance_match(db, row=existing, command=command)
        return existing
    if (
        db.scalar(
            select(MerchantContextEnd.id).where(
                MerchantContextEnd.tenant_id == tenant_id,
                MerchantContextEnd.command_id == command.command_id,
            )
        )
        is not None
    ):
        raise Conflict("merchant review command already names a termination")
    predecessor = None
    if command.supersedes_context_id is not None:
        predecessors = _merchant_context_rows(
            db,
            MerchantContext.tenant_id == tenant_id,
            MerchantContext.id == command.supersedes_context_id,
            lock=True,
        )
        predecessor = predecessors[0] if predecessors else None
        if (
            predecessor is None
            or predecessor.configuration.transport.installation_reference
            != transport.installation_reference
            or predecessor.configuration.transport.capability_reference
            != transport.capability_reference
            or predecessor.configuration.transport.provider_type
            != transport.provider_type
            or predecessor.configuration.transport.environment != transport.environment
            or _merchant_instant(command.effective_from)
            < _utc_moment(predecessor.effective_from)
            or db.scalar(
                select(MerchantContextEnd.id).where(
                    MerchantContextEnd.tenant_id == tenant_id,
                    MerchantContextEnd.context_id == predecessor.context_id,
                )
            )
            is not None
        ):
            raise Conflict("supersession requires the exact unended merchant context")
        db.add(
            MerchantContextEnd(
                tenant_id=tenant_id,
                command_id=command.command_id,
                context_id=predecessor.context_id,
                operation=MerchantReviewOperation.ACCEPT.value,
                request_fingerprint=review.request_fingerprint,
                replacement_context_id=command.command_id,
                evidence_reference=command.evidence.artifact_reference,
                evidence_sha256=command.evidence.sha256,
                reviewer_reference=review.reviewer_reference,
                reviewed_at=review.reviewed_at,
                effective_at=command.effective_from,
            )
        )
        db.flush()
    # Reject overlap across configuration revisions, not only exact replays.
    candidates = _merchant_context_rows(
        db,
        MerchantContext.tenant_id == tenant_id,
        MerchantContext.installation_reference == transport.installation_reference,
        MerchantContext.capability_reference == transport.capability_reference,
    )
    start = _merchant_instant(command.effective_from)
    until = (
        _merchant_instant(command.effective_until)
        if command.effective_until is not None
        else None
    )
    for candidate in candidates:
        ends = _merchant_end_rows(
            db,
            MerchantContextEnd.tenant_id == tenant_id,
            MerchantContextEnd.context_id == candidate.context_id,
        )
        end = ends[0] if ends else None
        bounds = [
            moment
            for moment in (
                candidate.effective_until,
                end.effective_at if end is not None else None,
            )
            if moment is not None
        ]
        upper = min(_utc_moment(moment) for moment in bounds) if bounds else None
        lower = _utc_moment(candidate.effective_from)
        if (
            (upper is None or lower < upper)
            and (upper is None or start < upper)
            and (until is None or lower < until)
        ):
            raise Conflict("competing accepted merchant context interval")
    row = MerchantContext(
        id=command.command_id,
        tenant_id=tenant_id,
        command_id=command.command_id,
        request_fingerprint=review.request_fingerprint,
        provider_type=transport.provider_type,
        environment=transport.environment,
        physical_merchant_id=command.configuration.physical_merchant_id,
        installation_reference=transport.installation_reference,
        capability_reference=transport.capability_reference,
        config_revision_reference=transport.config_revision_reference,
        config_digest=transport.config_digest,
        evidence_reference=command.evidence.artifact_reference,
        evidence_sha256=command.evidence.sha256,
        reviewer_reference=review.reviewer_reference,
        reviewed_at=review.reviewed_at,
        effective_from=command.effective_from,
        effective_until=command.effective_until,
    )
    db.add(row)
    db.flush()
    persisted = _merchant_context_rows(
        db,
        MerchantContext.tenant_id == tenant_id,
        MerchantContext.id == row.id,
    )
    if len(persisted) != 1:
        raise Conflict("persisted merchant context is absent after acceptance")
    _require_merchant_acceptance_match(
        db, row=persisted[0], command=command, review=review
    )
    if predecessor is not None:
        _merchant_event(
            db,
            scope=scope,
            identifier=predecessor.context_id,
            review=review,
            ended=True,
        )
    _merchant_event(db, scope=scope, identifier=row.id, review=review, ended=False)
    return persisted[0]


def revoke_merchant_context(
    db: Session,
    *,
    scope: TenantScope,
    command: RevokeMerchantContextV1,
    authority: MerchantReviewAuthority,
) -> MerchantContextEndV1:
    if type(command) is not RevokeMerchantContextV1:
        raise Conflict("typed merchant revocation command is required")
    review = _merchant_authorization(
        db, scope=scope, command=command, authority=authority
    )
    tenant_id = _tenant(scope)
    originals = _merchant_context_rows(
        db,
        MerchantContext.tenant_id == tenant_id,
        MerchantContext.id == command.context_id,
    )
    original = originals[0] if originals else None
    if original is None:
        raise Conflict("merchant context is absent in the tenant")
    _merchant_lock(db, tenant_id, command.command_id, original.configuration.transport)
    ends = _merchant_end_rows(
        db,
        MerchantContextEnd.tenant_id == tenant_id,
        MerchantContextEnd.context_id == original.context_id,
    )
    existing = ends[0] if ends else None
    if existing is not None:
        if (
            existing.command_id != command.command_id
            or existing.request_fingerprint != review.request_fingerprint
        ):
            raise Conflict(
                "merchant context already ended with different review material"
            )
        _require_merchant_revocation_match(existing, command)
        return existing
    if (
        db.scalar(
            select(MerchantContext.id).where(
                MerchantContext.tenant_id == tenant_id,
                MerchantContext.command_id == command.command_id,
            )
        )
        is not None
    ):
        raise Conflict("merchant review command already names an acceptance")
    row = MerchantContextEnd(
        tenant_id=tenant_id,
        command_id=command.command_id,
        context_id=original.context_id,
        operation=MerchantReviewOperation.REVOKE.value,
        request_fingerprint=review.request_fingerprint,
        evidence_reference=command.evidence.artifact_reference,
        evidence_sha256=command.evidence.sha256,
        reviewer_reference=review.reviewer_reference,
        reviewed_at=review.reviewed_at,
        effective_at=max(datetime.now(UTC), _utc_moment(original.effective_from)),
    )
    db.add(row)
    db.flush()
    persisted = _merchant_end_rows(
        db,
        MerchantContextEnd.tenant_id == tenant_id,
        MerchantContextEnd.id == row.id,
    )
    if len(persisted) != 1:
        raise Conflict("persisted merchant termination is absent after revocation")
    _require_merchant_revocation_match(persisted[0], command, review=review)
    if persisted[0].effective_at != _merchant_end(row).effective_at:
        raise Conflict("persisted merchant termination instant differs")
    _merchant_event(
        db, scope=scope, identifier=original.context_id, review=review, ended=True
    )
    return persisted[0]


def _matching_merchant_transport(
    row: MerchantContextV1, transport: MerchantTransportContextV1, captured: datetime
) -> bool:
    return (
        _transport_values(row.configuration.transport) == _transport_values(transport)
        and row.effective_from <= captured
        and (row.effective_until is None or captured < row.effective_until)
    )


def resolve_current_merchant_context(
    db: Session,
    *,
    scope: TenantScope,
    query: ResolveMerchantContextV1,
) -> MerchantContextV1:
    """Resolve unended configuration authority; this alone never qualifies cash."""
    if type(query) is not ResolveMerchantContextV1:
        raise Conflict("typed merchant context query is required")
    if type(scope) is not TenantScope:
        raise Conflict("merchant context query requires an exact TenantScope")
    _transport_values(query.transport)
    captured = _merchant_instant(query.captured_at)
    tenant_id = _merchant_uuid(_tenant(scope))
    rows = _merchant_context_rows(
        db,
        MerchantContext.tenant_id == tenant_id,
        MerchantContext.installation_reference
        == query.transport.installation_reference,
        MerchantContext.capability_reference == query.transport.capability_reference,
        ~select(MerchantContextEnd.id)
        .where(
            MerchantContextEnd.tenant_id == tenant_id,
            MerchantContextEnd.context_id == MerchantContext.id,
        )
        .exists(),
    )
    matches = [
        row
        for row in rows
        if _matching_merchant_transport(row, query.transport, captured)
    ]
    if len(matches) != 1:
        raise Conflict("one applicable unended merchant context is required")
    return matches[0]


def get_original_merchant_context(
    db: Session,
    *,
    scope: TenantScope,
    query: GetOriginalMerchantContextV1,
) -> MerchantContextV1:
    """Read original evidence irrespective of later ends, at any isolation.

    The caller must own an already bound immutable fact. This read cannot prove
    that fact exists and cannot authorize new cash or an opening import.
    """
    if type(query) is not GetOriginalMerchantContextV1:
        raise Conflict("typed original merchant context query is required")
    if type(scope) is not TenantScope:
        raise Conflict("merchant context query requires an exact TenantScope")
    _transport_values(query.transport)
    captured = _merchant_instant(query.captured_at)
    rows = _merchant_context_rows(
        db,
        MerchantContext.tenant_id == _merchant_uuid(_tenant(scope)),
        MerchantContext.id == _merchant_uuid(query.context_id),
    )
    row = rows[0] if rows else None
    if row is None or not _matching_merchant_transport(row, query.transport, captured):
        raise Conflict("original merchant context differs from the bound capture")
    return row
