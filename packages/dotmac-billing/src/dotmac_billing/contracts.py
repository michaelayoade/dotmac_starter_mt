"""Frozen Billing V1 commands and published facts.

Allocation and coverage deliberately do not live here. They are internal
derivations of immutable Billing effects, not cross-application contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

from dotmac_billing.errors import BillingRuleViolation

OBLIGATION_ACCEPT_CONTRACT = "billing.obligation.accept.v1"
SETTLEMENT_CONTRACT = "billing.settlement.accept.v1"
RECEIVABLE_POSITION_CONTRACT = "billing.receivable.position.v1"
RECEIVABLE_EXPOSURE_CONTRACT = "billing.receivable.exposure.v1"
DOCUMENT_FACT_CONTRACT = "billing.invoice.document.fact.v1"
ACCOUNTING_FACT_CONTRACT = "billing.accounting.fact.v1"
ARTIFACT_COMMAND_CONTRACT = "billing.document.artifact.record.v1"
ARTIFACT_REPAIR_CONTRACT = "billing.document.artifact.repair.v1"


FinancialState = Literal["open", "partially_resolved", "resolved", "cancelled"]
PositionCompleteness = Literal["complete", "partial", "unknown"]
PositionProjectionMode = Literal["authoritative", "shadow"]
ReceivableSourceAuthority = Literal["internal", "provider_owned", "external_finance"]
CollectionTiming = Literal["advance", "arrears"]


class ServicePeriodStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    VERIFIED = "verified"
    UNKNOWN_UNVERIFIED = "unknown_unverified"


@dataclass(frozen=True, slots=True)
class ServicePeriodEvidenceV1:
    status: ServicePeriodStatus
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status is ServicePeriodStatus.VERIFIED:
            if self.starts_at is None or self.ends_at is None:
                raise ValueError("verified service period requires both instants")
            if self.starts_at >= self.ends_at:
                raise ValueError("service period start must precede end")
        elif self.starts_at is not None or self.ends_at is not None:
            raise ValueError("non-verified service period must not carry instants")


class DueDateBasisStatus(str, Enum):
    VERIFIED = "verified"
    UNKNOWN_UNVERIFIED = "unknown_unverified"


@dataclass(frozen=True, slots=True)
class DueDateBasisV1:
    status: DueDateBasisStatus
    source_authority: str
    evidence_ref: str
    payment_terms_code: str | None = None
    payment_terms_version: str | None = None
    issued_at: datetime | None = None
    effective_at: datetime | None = None
    timezone: str | None = None
    derivation_policy: str | None = None
    derivation_version: str | None = None
    override_actor: str | None = None
    override_reason: str | None = None
    override_evidence_ref: str | None = None
    supersedes_basis_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.source_authority or not self.evidence_ref:
            raise ValueError("due-date basis requires source authority and evidence")
        verified = (
            self.payment_terms_code,
            self.payment_terms_version,
            self.issued_at,
            self.effective_at,
            self.timezone,
            self.derivation_policy,
            self.derivation_version,
        )
        if self.status is DueDateBasisStatus.VERIFIED and any(
            value is None for value in verified
        ):
            raise ValueError("verified due-date basis requires complete provenance")

    @classmethod
    def unknown_unverified(
        cls, *, source_authority: str, evidence_ref: str
    ) -> DueDateBasisV1:
        return cls(
            status=DueDateBasisStatus.UNKNOWN_UNVERIFIED,
            source_authority=source_authority,
            evidence_ref=evidence_ref,
        )

    @property
    def automated_collection_allowed(self) -> bool:
        return self.status is DueDateBasisStatus.VERIFIED

    def require_collectible(self, *, native: bool) -> None:
        if native and not self.automated_collection_allowed:
            raise BillingRuleViolation(
                "due_date_unverified",
                "native collectible issuance requires a verified due-date basis",
            )


@dataclass(frozen=True, slots=True)
class AppliedTaxSnapshotV1:
    treatment_code: str
    jurisdiction_code: str
    policy_id: str
    policy_version: str
    rate: Decimal
    taxable_basis: Money
    tax_amount: Money


@dataclass(frozen=True, slots=True)
class AppliedFxSnapshotV1:
    observation_id: str
    observation_version: str
    base_currency: str
    quote_currency: str
    rate: Decimal
    rate_purpose: str
    observed_at: datetime
    effective_at: datetime
    rounding_policy: str
    provenance: str


@dataclass(frozen=True, slots=True)
class PartyTaxIdentitySnapshotV1:
    party_role: Literal["seller", "customer"]
    identity_type: str
    identity_value: str
    country_code: str
    source_authority: str
    source_version: str


@dataclass(frozen=True, slots=True)
class PostalAddressSnapshotV1:
    line_one: str
    country_code: str
    line_two: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None


@dataclass(frozen=True, slots=True)
class RegisteredIdentifierSnapshotV1:
    identifier_type: str
    identifier_value: str
    issuing_country_code: str | None = None


@dataclass(frozen=True, slots=True)
class PartyDocumentSnapshotV1:
    legal_name: str
    address: PostalAddressSnapshotV1
    registered_identifiers: tuple[RegisteredIdentifierSnapshotV1, ...] = ()
    trading_name: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentInstructionsSnapshotV1:
    method_code: str
    bank_name: str | None = None
    account_name: str | None = None
    account_reference: str | None = None
    routing_code: str | None = None
    narrative: str | None = None


@dataclass(frozen=True, slots=True)
class PresentationAssetReferenceV1:
    status: Literal["file", "none"]
    file_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.status == "file") != (self.file_id is not None):
            raise ValueError("presentation asset file status and file id disagree")

    @classmethod
    def none(cls) -> PresentationAssetReferenceV1:
        return cls(status="none")


@dataclass(frozen=True, slots=True)
class DiscountSnapshotV1:
    discount_code: str
    description: str
    amount: Money
    source_version: str


@dataclass(frozen=True, slots=True)
class InvoiceLineFactV1:
    line_number: int
    description: str
    quantity: Decimal
    unit_code: str
    unit_amount: Money
    pre_tax_amount: Money
    discount_total: Money
    tax_amount: Money
    total_amount: Money
    price_source_version: str
    discounts: tuple[DiscountSnapshotV1, ...] = ()


@dataclass(frozen=True, slots=True)
class AcceptRatedObligationV1:
    scope: Scope
    billing_account_id: UUID
    contract_line_ref: str
    contract_version: str
    charge_component: str
    source_system: str
    source_kind: str
    source_fact_id: str
    source_fact_version: str
    subject_ref: str
    service_ref: str | None
    service_period: ServicePeriodEvidenceV1
    collection_timing: CollectionTiming
    pre_tax_amount: Money
    tax_amount: Money
    total_amount: Money
    rated_at: datetime
    price_version_id: str
    tax_snapshots: tuple[AppliedTaxSnapshotV1, ...] = ()
    fx_snapshot: AppliedFxSnapshotV1 | None = None
    supersedes_obligation_id: UUID | None = None


class SettlementFundingLane(str, Enum):
    AVAILABLE_CREDIT = "available_credit"
    PREPAID_FUNDING = "prepaid_funding"


@dataclass(frozen=True, slots=True)
class AcceptSettlementV1:
    scope: Scope
    billing_account_id: UUID
    source_system: str
    source_settlement_key: str
    source_version: str
    amount: Money
    occurred_at: datetime
    observed_at: datetime
    confirmation_evidence: str
    funding_lane: SettlementFundingLane


@dataclass(frozen=True, slots=True)
class InvoiceDocumentFactV1:
    scope: Scope
    invoice_id: UUID
    fact_version: int
    series_code: str
    document_number: str
    document_kind: Literal["invoice", "credit_note", "receipt"]
    document_state: Literal["issued", "corrected", "cancelled"]
    document_profile_code: str
    document_profile_version: str
    currency: str
    minor_units: int
    subtotal: Money
    tax_total: Money
    grand_total: Money
    due_at: datetime | None
    due_date_basis: DueDateBasisV1
    seller_snapshot: PartyDocumentSnapshotV1
    customer_snapshot: PartyDocumentSnapshotV1
    payment_instructions: PaymentInstructionsSnapshotV1
    brand_asset: PresentationAssetReferenceV1
    locale: str
    timezone: str
    lines: tuple[InvoiceLineFactV1, ...]
    tax_snapshots: tuple[AppliedTaxSnapshotV1, ...]
    fx_snapshot: AppliedFxSnapshotV1 | None
    party_tax_identities: tuple[PartyTaxIdentitySnapshotV1, ...]
    issued_at: datetime
    frozen_at: datetime
    source_authority: Literal["internal", "provider_owned", "external_finance"]
    correlation_id: str
    presentation_model_digest: str


@dataclass(frozen=True, slots=True)
class RecordDocumentArtifactV1:
    scope: Scope
    fact_id: UUID
    invoice_id: UUID
    fact_version: int
    media_type: str
    file_id: UUID
    checksum_sha256: str
    byte_length: int
    renderer_code: str
    renderer_version: str
    template_version: str
    presentation_model_digest: str
    rendered_at: datetime
    correlation_id: str
    issued_by: Literal["reconciler"] = "reconciler"

    def __post_init__(self) -> None:
        if self.fact_version < 1:
            raise ValueError("artifact fact version must be positive")
        if not self.media_type or not self.renderer_code or not self.renderer_version:
            raise ValueError("artifact requires media type and renderer provenance")
        if not self.template_version or not self.correlation_id:
            raise ValueError("artifact requires template and correlation provenance")
        if self.byte_length < 0:
            raise ValueError("artifact byte length cannot be negative")
        try:
            digest_is_hex = (
                len(self.checksum_sha256) == 64
                and len(bytes.fromhex(self.checksum_sha256)) == 32
            )
        except ValueError:
            digest_is_hex = False
        if not digest_is_hex or self.checksum_sha256 != self.checksum_sha256.lower():
            raise ValueError("artifact checksum must be a lowercase SHA-256 digest")
        if len(self.presentation_model_digest) != 64:
            raise ValueError("artifact requires a SHA-256 presentation-model digest")
        if self.issued_by != "reconciler":
            raise ValueError("only the declared reconciler may record an artifact")


@dataclass(frozen=True, slots=True)
class RepairDocumentArtifactV1:
    """Append a physical repair while preserving the official semantic fact."""

    scope: Scope
    current_artifact_id: UUID
    replacement_file_id: UUID
    checksum_sha256: str
    byte_length: int
    presentation_model_digest: str
    rendered_at: datetime
    correlation_id: str
    supersession_reason: str
    issued_by: Literal["reconciler"] = "reconciler"

    def __post_init__(self) -> None:
        if self.byte_length < 0:
            raise ValueError("artifact byte length cannot be negative")
        try:
            digest_is_hex = (
                len(self.checksum_sha256) == 64
                and len(bytes.fromhex(self.checksum_sha256)) == 32
            )
        except ValueError:
            digest_is_hex = False
        if not digest_is_hex or self.checksum_sha256 != self.checksum_sha256.lower():
            raise ValueError("artifact checksum must be a lowercase SHA-256 digest")
        if len(self.presentation_model_digest) != 64:
            raise ValueError("artifact requires a SHA-256 presentation-model digest")
        if not self.correlation_id or not self.supersession_reason:
            raise ValueError("artifact repair requires correlation and reason evidence")
        if self.rendered_at.tzinfo is None:
            raise ValueError("artifact repair rendered_at must be timezone-aware")
        if self.issued_by != "reconciler":
            raise ValueError("only the declared reconciler may repair an artifact")


@dataclass(frozen=True, slots=True)
class ReceivablePositionV1:
    scope: Scope
    source_owner: str
    billing_account_id: UUID
    source_version: int
    posting_group_watermark: UUID | None
    source_authority: ReceivableSourceAuthority
    projection_mode: PositionProjectionMode
    derived_from: Literal["posting_groups", "projection"]
    completeness: PositionCompleteness
    completeness_reason_code: str | None
    state_fingerprint: str
    observed_at: datetime
    financial_state: FinancialState
    collectible_receivable: Money
    available_credit: Money
    prepaid_funding: Money

    def __post_init__(self) -> None:
        required_text = (
            self.source_owner,
            self.state_fingerprint,
        )
        if any(not value or not value.strip() for value in required_text):
            raise ValueError(
                "receivable position owner and fingerprint must be non-empty"
            )
        if self.source_version < 1:
            raise ValueError("source_version must be positive")
        if self.source_authority not in (
            "internal",
            "provider_owned",
            "external_finance",
        ):
            raise ValueError("source_authority is unsupported")
        if self.projection_mode not in ("authoritative", "shadow"):
            raise ValueError("projection_mode is unsupported")
        if self.derived_from not in ("posting_groups", "projection"):
            raise ValueError("derived_from is unsupported")
        if self.completeness not in ("complete", "partial", "unknown"):
            raise ValueError("completeness is unsupported")
        if self.financial_state not in (
            "open",
            "partially_resolved",
            "resolved",
            "cancelled",
        ):
            raise ValueError("financial_state is unsupported")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        values = (
            self.collectible_receivable,
            self.available_credit,
            self.prepaid_funding,
        )
        if any(not isinstance(value, Money) for value in values):
            raise TypeError("receivable position amounts must be Money")
        if any(value.is_negative for value in values):
            raise ValueError("receivable position amounts must be non-negative")
        if len({value.currency for value in values}) != 1:
            raise ValueError("receivable position amounts must use one currency")
        if self.completeness == "complete":
            if self.completeness_reason_code is not None:
                raise ValueError(
                    "a complete position cannot carry an incomplete reason"
                )
        elif not self.completeness_reason_code:
            raise ValueError("an incomplete position requires a reason code")


@dataclass(frozen=True, slots=True)
class ReceivableExposureV1:
    """One collectible exposure with its own service and due-date evidence."""

    scope: Scope
    source_owner: str
    exposure_ref: str
    billing_account_id: UUID
    subject_ref: str
    service_ref: str | None
    collection_timing: CollectionTiming
    source_version: int
    posting_group_watermark: UUID | None
    source_authority: ReceivableSourceAuthority
    projection_mode: PositionProjectionMode
    derived_from: Literal["posting_groups", "projection"]
    completeness: PositionCompleteness
    completeness_reason_code: str | None
    state_fingerprint: str
    observed_at: datetime
    service_period: ServicePeriodEvidenceV1
    due_at: datetime | None
    due_date_basis: DueDateBasisV1
    financial_state: FinancialState
    collectible_receivable: Money

    def __post_init__(self) -> None:
        required_text = (
            self.source_owner,
            self.exposure_ref,
            self.subject_ref,
            self.state_fingerprint,
        )
        if any(not value or not value.strip() for value in required_text):
            raise ValueError("receivable exposure identity must be non-empty")
        if self.service_ref is not None and not self.service_ref.strip():
            raise ValueError("service_ref must be non-empty when supplied")
        if self.collection_timing not in ("advance", "arrears"):
            raise ValueError("collection_timing is unsupported")
        if self.source_version < 1:
            raise ValueError("source_version must be positive")
        if self.source_authority not in (
            "internal",
            "provider_owned",
            "external_finance",
        ):
            raise ValueError("source_authority is unsupported")
        if self.projection_mode not in ("authoritative", "shadow"):
            raise ValueError("projection_mode is unsupported")
        if self.derived_from not in ("posting_groups", "projection"):
            raise ValueError("derived_from is unsupported")
        if self.completeness not in ("complete", "partial", "unknown"):
            raise ValueError("completeness is unsupported")
        if self.financial_state not in (
            "open",
            "partially_resolved",
            "resolved",
            "cancelled",
        ):
            raise ValueError("financial_state is unsupported")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.due_at is not None and self.due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        if not isinstance(self.collectible_receivable, Money):
            raise TypeError("collectible receivable must be Money")
        if self.collectible_receivable.is_negative:
            raise ValueError("collectible receivable must be non-negative")
        if self.completeness == "complete":
            if self.completeness_reason_code is not None:
                raise ValueError(
                    "a complete exposure cannot carry an incomplete reason"
                )
        elif not self.completeness_reason_code:
            raise ValueError("an incomplete exposure requires a reason code")
        if self.due_date_basis.status is DueDateBasisStatus.UNKNOWN_UNVERIFIED:
            if self.due_at is not None:
                raise ValueError("an unverified due-date basis cannot carry due_at")


@dataclass(frozen=True, slots=True)
class AccountingEffectV1:
    lane: Literal["receivable", "available_credit", "prepaid_funding"]
    amount_delta: Money


@dataclass(frozen=True, slots=True)
class AccountingAllocationEffectV1:
    settlement_id: UUID
    document_id: UUID | None
    effect_kind: Literal[
        "allocation", "deallocation", "reallocation", "refund", "reversal"
    ]
    amount_delta: Money
    offsets_allocation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AccountingFactV1:
    scope: Scope
    source_system: str
    fact_id: UUID
    fact_version: int
    billing_account_id: UUID
    posting_group_id: UUID
    source_ref: str
    source_authority: Literal["internal", "provider_owned", "external_finance"]
    effect_kind: Literal[
        "invoice_issued",
        "credit_note_issued",
        "settlement_accepted",
        "allocation",
        "deallocation",
        "reallocation",
        "refund",
        "reversal",
    ]
    occurred_at: datetime
    committed_at: datetime
    effects: tuple[AccountingEffectV1, ...]
    allocations: tuple[AccountingAllocationEffectV1, ...] = ()
    reverses_fact_id: UUID | None = None
    tax_snapshots: tuple[AppliedTaxSnapshotV1, ...] = field(default_factory=tuple)
    fx_snapshot: AppliedFxSnapshotV1 | None = None


__all__ = [
    "ACCOUNTING_FACT_CONTRACT",
    "ARTIFACT_COMMAND_CONTRACT",
    "ARTIFACT_REPAIR_CONTRACT",
    "DOCUMENT_FACT_CONTRACT",
    "OBLIGATION_ACCEPT_CONTRACT",
    "RECEIVABLE_EXPOSURE_CONTRACT",
    "RECEIVABLE_POSITION_CONTRACT",
    "SETTLEMENT_CONTRACT",
    "AcceptRatedObligationV1",
    "AcceptSettlementV1",
    "AccountingAllocationEffectV1",
    "AccountingEffectV1",
    "AccountingFactV1",
    "AppliedFxSnapshotV1",
    "AppliedTaxSnapshotV1",
    "DueDateBasisStatus",
    "DueDateBasisV1",
    "DiscountSnapshotV1",
    "InvoiceLineFactV1",
    "InvoiceDocumentFactV1",
    "FinancialState",
    "PositionCompleteness",
    "PositionProjectionMode",
    "ReceivableSourceAuthority",
    "PartyDocumentSnapshotV1",
    "PartyTaxIdentitySnapshotV1",
    "PaymentInstructionsSnapshotV1",
    "PostalAddressSnapshotV1",
    "PresentationAssetReferenceV1",
    "ReceivableExposureV1",
    "ReceivablePositionV1",
    "RepairDocumentArtifactV1",
    "RegisteredIdentifierSnapshotV1",
    "RecordDocumentArtifactV1",
    "ServicePeriodEvidenceV1",
    "ServicePeriodStatus",
    "SettlementFundingLane",
]
