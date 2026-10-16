"""Payment intent commands, vocabularies and outcomes."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money


class PaymentError(Exception):
    """Base refusal."""


class Conflict(PaymentError):
    """The intent, proof or confirmation state is inadmissible."""


class ConfirmationNotFound(PaymentError):
    """The requested confirmation is absent from the tenant scope."""


class UnresolvedMoneyPrecision(Conflict):
    """Legacy evidence has no authoritative currency scale."""


@dataclass(frozen=True, slots=True)
class GetPaymentIntentV1:
    reference: str


@dataclass(frozen=True, slots=True)
class PaymentIntentObservationV1:
    """Persisted intent binding and lifecycle, not immutable cash evidence."""

    scope: TenantScope
    intent_id: UUID
    reference: str
    payer_reference: str
    target_reference: str | None
    purpose: PaymentPurpose
    requested: Money
    confirmed: Money | None
    provider_type: str
    channel: str
    status: PaymentIntentStatus
    opened_at: datetime
    expires_at: datetime | None
    settled_at: datetime | None


@dataclass(frozen=True, slots=True)
class GetConfirmedCashV1:
    confirmation_id: UUID


@dataclass(frozen=True, slots=True)
class ConfirmedCashObservationV1:
    """Persisted cash evidence; payer/account identity belongs to assembly."""

    scope: TenantScope
    confirmation_id: UUID
    intent_id: UUID
    confirmed: Money
    source: ConfirmationSource
    provider_type: str
    external_reference: str
    observed_at: datetime


class PaymentPurpose(enum.StrEnum):
    INVOICE_SETTLEMENT = "INVOICE_SETTLEMENT"
    ACCOUNT_CREDIT_DEPOSIT = "ACCOUNT_CREDIT_DEPOSIT"
    SERVICE_FEE = "SERVICE_FEE"
    PURCHASE_FUNDING = "PURCHASE_FUNDING"


class PaymentIntentStatus(enum.StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ConfirmationSource(enum.StrEnum):
    """How a confirmation reached us. It is evidence provenance, not authority.

    `PROVIDER_CALLBACK` is corroboration from a transport, never the thing that
    picks which intent is settled — the intent is addressed by its own
    reference, established before any provider I/O.
    """

    PROVIDER_CALLBACK = "PROVIDER_CALLBACK"
    PROVIDER_RECONCILIATION = "PROVIDER_RECONCILIATION"
    TRANSFER_PROOF = "TRANSFER_PROOF"
    MANUAL = "MANUAL"


class TransferProofState(enum.StrEnum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class OpenPaymentIntent:
    """Open an intent.

    `opened_at` is an authoritative business fact, not a clock seam: it is WHEN
    the payer was asked to pay, and a history backfill or a provider
    reconciliation import carries the real one. Omitting it means "now"; the
    module never overrides a supplied value with its own wall clock, because a
    migrated intent whose `opened_at` is its import timestamp cannot be
    compared against its source and fails the dossier's settlement-time drift
    check.

    `PURCHASE_FUNDING` binds an opaque purchase or obligation before an invoice
    exists. It requires a nonblank `target_reference`, preserved exactly on
    replay. The purpose grants no spendable credit, cash qualification or
    ledger-posting authority.

    `expires_at` is validated against `opened_at`, never against the moment the
    import happens to run. Both may be in the past for a historical intent, so
    long as they are correctly ordered.
    """

    payer_reference: str
    purpose: PaymentPurpose
    requested: Money
    reference: str
    provider_type: str
    channel: str
    target_reference: str | None = None
    expires_at: datetime | None = None
    opened_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SubmitTransferProof:
    intent_id: UUID
    declared: Money
    document_reference: str
    submitted_reference: str
    declared_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReviewTransferProof:
    proof_id: UUID
    accept: bool
    reviewer: str
    rejection_reason: str | None = None
    reviewed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecordConfirmation:
    """Correlate immutable confirmed-payment evidence to its named intent.

    A repeated provider/external-reference identity must retain its intent,
    amount, currency and source. A supplied observation instant must match the
    original; omitting it on replay retains the stored instant. New evidence
    alone defaults an omitted instant to the current time.
    """

    intent_id: UUID
    source: ConfirmationSource
    external_reference: str
    confirmed: Money
    observed_at: datetime | None = None


__all__ = [
    "Conflict",
    "ConfirmationNotFound",
    "ConfirmedCashObservationV1",
    "GetConfirmedCashV1",
    "GetPaymentIntentV1",
    "PaymentIntentObservationV1",
    "UnresolvedMoneyPrecision",
    "ConfirmationSource",
    "OpenPaymentIntent",
    "PaymentError",
    "PaymentIntentStatus",
    "PaymentPurpose",
    "RecordConfirmation",
    "ReviewTransferProof",
    "SubmitTransferProof",
    "TransferProofState",
]
