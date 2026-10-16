"""Payment intent commands, vocabularies and outcomes."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Money
from sqlalchemy.orm import Session


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
    "MerchantReviewOperation",
    "MerchantTransportContextV1",
    "MerchantConfigurationV1",
    "MerchantReviewEvidenceV1",
    "AcceptMerchantContextV1",
    "RevokeMerchantContextV1",
    "AuthorizedMerchantReviewV1",
    "MerchantReviewAuthority",
    "MerchantContextV1",
    "MerchantContextEndV1",
    "ResolveMerchantContextV1",
    "GetOriginalMerchantContextV1",
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
    "OriginalProviderCashEvidenceV1",
    "QualifyProviderCashV1",
    "AuthorizedProviderCashV1",
    "ProviderCashEvidenceAuthority",
    "ProviderCashQualificationV1",
]


class MerchantReviewOperation(enum.StrEnum):
    ACCEPT = "ACCEPT"
    REVOKE = "REVOKE"


@dataclass(frozen=True, slots=True)
class MerchantTransportContextV1:
    """Opaque original integration provenance; never physical merchant proof."""

    provider_type: str
    environment: str
    installation_reference: str
    capability_reference: str
    config_revision_reference: str
    config_digest: str


@dataclass(frozen=True, slots=True)
class MerchantConfigurationV1:
    transport: MerchantTransportContextV1
    physical_merchant_id: str


@dataclass(frozen=True, slots=True)
class MerchantReviewEvidenceV1:
    artifact_reference: str
    sha256: str


@dataclass(frozen=True, slots=True)
class AcceptMerchantContextV1:
    command_id: UUID
    configuration: MerchantConfigurationV1
    evidence: MerchantReviewEvidenceV1
    effective_from: datetime
    effective_until: datetime | None = None
    supersedes_context_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RevokeMerchantContextV1:
    command_id: UUID
    context_id: UUID
    evidence: MerchantReviewEvidenceV1


@dataclass(frozen=True, slots=True)
class AuthorizedMerchantReviewV1:
    """Authority-produced attestation of human configuration review ONLY.

    Never deserialize this from a request. The installed product authority must
    freshly authenticate and authorize the human reviewer for this exact tenant,
    operation and full request fingerprint. This is not artifact-byte checking
    or independent provider verification, and confers no cash/ledger authority.
    """

    scope: TenantScope
    command_id: UUID
    operation: MerchantReviewOperation
    request_fingerprint: str
    reviewer_reference: str
    reviewed_at: datetime


class MerchantReviewAuthority(Protocol):
    def authorize(
        self,
        db: Session,
        *,
        scope: TenantScope,
        command: AcceptMerchantContextV1 | RevokeMerchantContextV1,
        request_fingerprint: str,
    ) -> AuthorizedMerchantReviewV1:
        """Revalidate the exact human review; raise on denial/indeterminate.

        An implementation belongs to the product's authenticated server owner,
        never to provider payloads. It reads current authority in the caller
        transaction without mutating or completing it. There is deliberately
        no default authority.
        """
        ...


@dataclass(frozen=True, slots=True)
class MerchantContextV1:
    scope: TenantScope
    context_id: UUID
    command_id: UUID
    request_fingerprint: str
    configuration: MerchantConfigurationV1
    evidence: MerchantReviewEvidenceV1
    reviewer_reference: str
    reviewed_at: datetime
    effective_from: datetime
    effective_until: datetime | None


@dataclass(frozen=True, slots=True)
class MerchantContextEndV1:
    scope: TenantScope
    end_id: UUID
    command_id: UUID
    context_id: UUID
    operation: MerchantReviewOperation
    request_fingerprint: str
    replacement_context_id: UUID | None
    evidence: MerchantReviewEvidenceV1
    reviewer_reference: str
    reviewed_at: datetime
    effective_at: datetime


@dataclass(frozen=True, slots=True)
class ResolveMerchantContextV1:
    transport: MerchantTransportContextV1
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class GetOriginalMerchantContextV1:
    """Evidence-only query for an already bound fact; no admission authority."""

    context_id: UUID
    transport: MerchantTransportContextV1
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class OriginalProviderCashEvidenceV1:
    """Immutable owner locator, not an authentication claim or a cash verdict."""

    owner: str
    reference: str
    schema_version: str
    sha256: str


@dataclass(frozen=True, slots=True)
class QualifyProviderCashV1:
    command_id: UUID
    original_evidence: OriginalProviderCashEvidenceV1


@dataclass(frozen=True, slots=True)
class AuthorizedProviderCashV1:
    """Installed authority output only; never deserialize from caller payloads.

    The authority resolves actual retained authenticated original operation
    evidence, including its account-bound verification and accepted context.
    Provider decoding, currency precision and successful-capture interpretation
    belong to that authority; unsupported or missing paid_at must be refused.
    """

    scope: TenantScope
    command_id: UUID
    request_fingerprint: str
    original_evidence: OriginalProviderCashEvidenceV1
    transport: MerchantTransportContextV1
    context_id: UUID
    verified_at: datetime
    transaction_kind: str
    transaction_id: str
    checkout_reference: str
    status: str
    money: Money
    paid_at: datetime
    observed_at: datetime


class ProviderCashEvidenceAuthority(Protocol):
    def verify_original(
        self,
        db: Session,
        *,
        scope: TenantScope,
        request: QualifyProviderCashV1,
        request_fingerprint: str,
    ) -> AuthorizedProviderCashV1:
        """Freshly authenticate actual original evidence or raise on uncertainty.

        No default implementation exists. The product installs its trusted
        server evidence owner; constructing this DTO is not proof. Read in the
        caller transaction without mutating/completing it. Revalidate even on
        replay and admission; never replace original transport with current
        configuration or infer paid_at from creation/receipt/retrieval time.
        """
        ...


@dataclass(frozen=True, slots=True)
class ProviderCashQualificationV1:
    """Historical immutable cash fact; this is never an admission token."""

    scope: TenantScope
    qualification_id: UUID
    command_id: UUID
    request_fingerprint: str
    original: AuthorizedProviderCashV1
    physical_merchant_id: str
    source_namespace: str
    source_key: str
    source_version: str
    provenance_fingerprint: str
