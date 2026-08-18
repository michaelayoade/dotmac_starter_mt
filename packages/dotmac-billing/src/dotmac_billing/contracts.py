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

from dotmac_billing.errors import BillingRuleViolation

OBLIGATION_OUTPUT_CONTRACT = "RatedObligationOutputV1"
OBLIGATION_ACCEPT_CONTRACT = "billing.obligation.accept.v1"
SETTLEMENT_CONTRACT = "billing.settlement.accept.v1"
RECEIVABLE_POSITION_CONTRACT = "billing.receivable.position.v1"
DOCUMENT_FACT_CONTRACT = "billing.invoice.document.fact.v1"
ACCOUNTING_FACT_CONTRACT = "billing.accounting.fact.v1"
ARTIFACT_COMMAND_CONTRACT = "billing.document.artifact.record.v1"


@dataclass(frozen=True, slots=True)
class MoneyV1:
    """Exact wire/persistence money with explicit fraction precision."""

    amount: Decimal
    currency: str
    minor_units: int

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):
            raise ValueError("money refuses float input")
        if not isinstance(self.amount, Decimal):
            raise ValueError("money amount must be Decimal")
        if not self.amount.is_finite():
            raise ValueError("money amount must be finite")
        if (
            len(self.currency) != 3
            or not self.currency.isalpha()
            or self.currency != self.currency.upper()
        ):
            raise ValueError("currency must be an uppercase ISO code")
        if not 0 <= self.minor_units <= 6:
            raise ValueError("minor-unit precision must be 0 through 6")
        normalized = self.amount.normalize()
        exponent = normalized.as_tuple().exponent
        # Decimal types the exponent as int | Literal["n", "N", "F"] for
        # non-finite values.  The finite guard above makes the string branch
        # unreachable at runtime; keeping it explicit also keeps this boundary
        # honest to the library type rather than silencing the checker.
        scale = 0 if isinstance(exponent, str) else max(0, -exponent)
        if scale > self.minor_units:
            raise ValueError("amount exceeds its declared minor-unit precision")
        if len(normalized.as_tuple().digits) > 20:
            raise ValueError("amount exceeds NUMERIC(20,6) precision")


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
    taxable_basis: MoneyV1
    tax_amount: MoneyV1


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
    amount: MoneyV1
    source_version: str


@dataclass(frozen=True, slots=True)
class InvoiceLineFactV1:
    line_number: int
    description: str
    quantity: Decimal
    unit_code: str
    unit_amount: MoneyV1
    pre_tax_amount: MoneyV1
    discount_total: MoneyV1
    tax_amount: MoneyV1
    total_amount: MoneyV1
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
    service_period: ServicePeriodEvidenceV1
    collection_timing: str
    pre_tax_amount: MoneyV1
    tax_amount: MoneyV1
    total_amount: MoneyV1
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
    amount: MoneyV1
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
    subtotal: MoneyV1
    tax_total: MoneyV1
    grand_total: MoneyV1
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
    supersedes_artifact_id: UUID | None = None
    supersession_reason: str | None = None

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
        if (self.supersedes_artifact_id is None) != (self.supersession_reason is None):
            raise ValueError("artifact supersession id and reason must travel together")
        if self.issued_by != "reconciler":
            raise ValueError("only the declared reconciler may record an artifact")


@dataclass(frozen=True, slots=True)
class ReceivablePositionV1:
    scope: Scope
    source_owner: str
    exposure_ref: str
    billing_account_id: UUID
    source_version: int
    posting_group_watermark: UUID | None
    source_authority: Literal["internal", "provider_owned", "external_finance"]
    derived_from: Literal["posting_groups", "projection"]
    completeness: Literal["complete", "partial", "unknown"]
    state_fingerprint: str
    observed_at: datetime
    service_period: ServicePeriodEvidenceV1
    due_at: datetime | None
    due_date_basis: DueDateBasisV1
    collectible_receivable: MoneyV1
    available_credit: MoneyV1
    prepaid_funding: MoneyV1


@dataclass(frozen=True, slots=True)
class AccountingEffectV1:
    lane: Literal["receivable", "available_credit", "prepaid_funding"]
    amount_delta: MoneyV1


@dataclass(frozen=True, slots=True)
class AccountingAllocationEffectV1:
    settlement_id: UUID
    document_id: UUID | None
    effect_kind: Literal[
        "allocation", "deallocation", "reallocation", "refund", "reversal"
    ]
    amount_delta: MoneyV1
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
    "DOCUMENT_FACT_CONTRACT",
    "OBLIGATION_ACCEPT_CONTRACT",
    "OBLIGATION_OUTPUT_CONTRACT",
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
    "MoneyV1",
    "PartyDocumentSnapshotV1",
    "PartyTaxIdentitySnapshotV1",
    "PaymentInstructionsSnapshotV1",
    "PostalAddressSnapshotV1",
    "PresentationAssetReferenceV1",
    "ReceivablePositionV1",
    "RegisteredIdentifierSnapshotV1",
    "RecordDocumentArtifactV1",
    "ServicePeriodEvidenceV1",
    "ServicePeriodStatus",
    "SettlementFundingLane",
]
