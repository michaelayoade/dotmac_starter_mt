"""Frozen Billing V1 commands and published facts.

Allocation effects remain Billing-owned derivations. A narrowly scoped current
observation proves one exact settlement allocation within the caller transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal, Protocol
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.money import Currency, Money

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


class ObligationSourceRegistry(Protocol):
    """Assembly-supplied registry of uniquely declared obligation sources."""

    def require_obligation_source(self, code: str) -> str:
        """Return the declaring owner, or refuse an undeclared source."""
        ...


@dataclass(frozen=True, slots=True)
class IssueRatedInvoiceV1:
    """Issue one quantity-one invoice from an immutable rated obligation.

    The assembly supplies already-frozen commercial, Tax and presentation
    evidence. This command does not rate, confirm funding or declare sources.
    Native collectible issuance always requires a verified due-date basis.
    Source fact version participates in the existing obligation natural key:
    a new version is a new identity, never correction of an issued invoice.
    The assembly owns source revision/deduplication and routes corrections to
    the existing Billing correction owner.
    """

    obligation: AcceptRatedObligationV1
    description: str
    unit_code: str
    seller_snapshot: PartyDocumentSnapshotV1
    customer_snapshot: PartyDocumentSnapshotV1
    payment_instructions: PaymentInstructionsSnapshotV1
    brand_asset: PresentationAssetReferenceV1
    locale: str
    timezone: str
    document_profile_code: str
    document_profile_version: str
    series_code: str
    reference_date: date
    due_at: datetime
    due_date_basis: DueDateBasisV1
    actor_ref: str
    correlation_id: str
    party_tax_identities: tuple[PartyTaxIdentitySnapshotV1, ...] = ()


@dataclass(frozen=True, slots=True)
class IssuedRatedInvoiceV1:
    """Original persisted issuance identities, stable across exact retries.

    This receipt is not a current financial position or an authorization proof.
    The issued fact version identifies the original immutable document fact.
    """

    scope: Scope
    billing_account_id: UUID
    obligation_id: UUID
    invoice_id: UUID
    line_id: UUID
    line_number: int
    issued_fact_version: int


class SettlementFundingLane(str, Enum):
    AVAILABLE_CREDIT = "available_credit"
    PREPAID_FUNDING = "prepaid_funding"


@dataclass(frozen=True, slots=True)
class AcceptSettlementV1:
    """Accept a confirmed source fact bound to one account and funding lane.

    Replay retains the original receipt observation (`observed_at`); account,
    lane, confirmation evidence and both exact source identifiers must match.
    """

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
class GetBillingAccountObservationV1:
    """Observe current persisted identity, without locking or authorizing a link."""

    scope: Scope
    account_id: UUID

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scope, TenantScope | PlatformScope)
            or not isinstance(self.account_id, UUID)
            or (
                isinstance(self.scope, TenantScope)
                and not isinstance(self.scope.tenant_id, UUID)
            )
        ):
            raise BillingRuleViolation(
                "account_observation_request_invalid",
                "account observation requires an explicit scope and UUID identity",
            )


@dataclass(frozen=True, slots=True)
class BillingAccountObservationV1:
    """Frozen copy of current account identity, not an immutable historical fact.

    The external reference can change and the account can disappear. Re-observe
    when needed; this value is neither a balance nor authorization evidence.
    """

    scope: Scope
    account_id: UUID
    external_account_ref: str
    currency: Currency


@dataclass(frozen=True, slots=True)
class GetCurrentSettlementAllocationV1:
    """Observe one exact allocation, including current inverse effects."""

    scope: Scope
    billing_account_id: UUID
    posting_group_id: UUID
    settlement_id: UUID
    document_id: UUID
    amount: Money

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scope, TenantScope | PlatformScope)
            or (
                isinstance(self.scope, TenantScope)
                and not isinstance(self.scope.tenant_id, UUID)
            )
            or any(
                not isinstance(value, UUID)
                for value in (
                    self.billing_account_id,
                    self.posting_group_id,
                    self.settlement_id,
                    self.document_id,
                )
            )
            or not isinstance(self.amount, Money)
            or not self.amount.amount.is_finite()
            or self.amount.amount <= 0
        ):
            raise BillingRuleViolation(
                "allocation_observation_request_invalid",
                "allocation observation requires UUID identities and positive Money",
            )


@dataclass(frozen=True, slots=True)
class CurrentSettlementAllocationV1:
    """Current transaction observation; re-observe before each product decision.

    The account lock remains held by the caller transaction. This value is not
    a historical fact or reusable admission token. Unlinked reductions refuse.
    """

    scope: Scope
    billing_account_id: UUID
    posting_group_id: UUID
    settlement_id: UUID
    document_id: UUID
    original_amount: Money
    remaining_amount: Money
    state: Literal["current", "reduced", "removed", "inactive_backing"]


@dataclass(frozen=True, slots=True)
class GetInvoiceDocumentFactV1:
    """Read one exact historical fact; no implicit latest-version selection."""

    scope: Scope
    invoice_id: UUID
    fact_version: int

    def __post_init__(self) -> None:
        if type(self.fact_version) is not int or self.fact_version < 1:
            raise ValueError("document fact version must be a positive integer")
        if not isinstance(self.invoice_id, UUID):
            raise TypeError("invoice_id must be a UUID")
        if not isinstance(self.scope, TenantScope | PlatformScope):
            raise TypeError("document fact query requires an explicit scope")
        if isinstance(self.scope, TenantScope) and not isinstance(
            self.scope.tenant_id, UUID
        ):
            raise TypeError("tenant scope requires a UUID")


@dataclass(frozen=True, slots=True)
class StoredInvoiceDocumentFactV1:
    """Persisted fact identity and recording instant, separate from issuance."""

    fact_id: UUID
    contract_version: Literal["billing.invoice.document.fact.v1"]
    recorded_at: datetime
    fact: InvoiceDocumentFactV1


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


ACCOUNTING_FACT_V2_CONTRACT = "billing.accounting.fact.v2"


def _withholding_money(value: Money, *, positive: bool = False) -> None:
    if not isinstance(value, Money):
        raise TypeError("withholding amounts must be Money")
    if (
        not value.amount.is_finite()
        or value.amount < 0
        or (positive and value.amount == 0)
    ):
        raise ValueError("withholding money must be finite and non-negative")
    if value.amount.adjusted() >= 14:
        raise ValueError("withholding money exceeds Billing numeric precision")
    if (
        not 0 <= value.currency.minor_units <= 6
        or value.amount
        != value.amount.quantize(Decimal(1).scaleb(-value.currency.minor_units))
    ):
        raise ValueError("withholding money must have exact supported precision")


@dataclass(frozen=True, slots=True)
class WithholdingTaxLineV1:
    sequence: int
    taxable_amount: Money
    rate: Decimal | None
    tax_amount: Money

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("withholding line sequence must be positive")
        _withholding_money(self.taxable_amount)
        _withholding_money(self.tax_amount)
        if self.taxable_amount.currency != self.tax_amount.currency:
            raise ValueError("withholding line currency must agree")
        if self.rate is not None and (
            not isinstance(self.rate, Decimal)
            or not self.rate.is_finite()
            or self.rate < 0
        ):
            raise ValueError("withholding rate must be an exact finite Decimal")


@dataclass(frozen=True, slots=True)
class WithholdingTaxEvidenceV1:
    """Assembly-mapped Tax result, never a Billing rate calculation.

    Source fingerprints bind the full Tax result; component and ordered lines
    retain its exact basis and amounts. References are audit evidence, not proof
    that the caller is authorized or that a certificate is valid.
    """

    source_owner: str
    determination_ref: str
    source_version: str
    source_fingerprint: str
    result_fingerprint: str
    evidence_ref: str
    source_ref: str
    component_ref: str
    rule_ref: str
    rule_version: str
    treatment_code: str
    calculation_base_code: str
    inclusive: bool
    taxable_basis: Money
    amount: Money
    lines: tuple[WithholdingTaxLineV1, ...]
    scope: Scope
    billing_account_id: UUID
    document_id: UUID
    recognition_basis_code: str
    fact_kind: str
    transaction_side: str
    determined_at: datetime

    def __post_init__(self) -> None:
        texts = (
            self.source_owner,
            self.determination_ref,
            self.evidence_ref,
            self.source_ref,
            self.component_ref,
            self.rule_ref,
            self.rule_version,
            self.treatment_code,
            self.calculation_base_code,
            self.source_version,
            self.source_fingerprint,
            self.recognition_basis_code,
            self.fact_kind,
            self.transaction_side,
        )
        if any(not isinstance(value, str) or not value.strip() for value in texts):
            raise ValueError("withholding Tax evidence requires complete provenance")
        if (
            not self.result_fingerprint.startswith("rv1:")
            or len(self.result_fingerprint) != 68
            or any(c not in "0123456789abcdef" for c in self.result_fingerprint[4:])
        ):
            raise ValueError(
                "withholding evidence requires the sealed Tax rv1 fingerprint"
            )
        if len(self.source_owner) > 120 or any(
            len(value) > 255 for value in (self.determination_ref, self.component_ref)
        ):
            raise ValueError("withholding Tax identities exceed persistence bounds")
        if not isinstance(self.scope, TenantScope | PlatformScope):
            raise TypeError("withholding Tax evidence requires an explicit scope")
        if isinstance(self.scope, TenantScope) and not isinstance(
            self.scope.tenant_id, UUID
        ):
            raise TypeError("withholding tenant scope requires a UUID")
        if not isinstance(self.billing_account_id, UUID) or not isinstance(
            self.document_id, UUID
        ):
            raise TypeError(
                "withholding Tax evidence requires UUID account and invoice"
            )
        if self.determined_at.tzinfo is None:
            raise ValueError("withholding determination requires an aware instant")
        if type(self.inclusive) is not bool:
            raise TypeError("withholding inclusive flag must be boolean")
        _withholding_money(self.taxable_basis)
        _withholding_money(self.amount, positive=True)
        if self.taxable_basis.currency != self.amount.currency:
            raise ValueError("withholding evidence currency must agree")
        if not isinstance(self.lines, tuple) or not self.lines:
            raise ValueError("withholding evidence requires ordered Tax lines")
        sequences = tuple(line.sequence for line in self.lines)
        if sequences != tuple(sorted(set(sequences))):
            raise ValueError(
                "withholding Tax lines must preserve increasing unique sequences"
            )
        if any(line.tax_amount.currency != self.amount.currency for line in self.lines):
            raise ValueError("withholding Tax line currency must agree")
        if (
            sum((line.tax_amount.amount for line in self.lines), Decimal(0))
            != self.amount.amount
        ):
            raise ValueError("withholding Tax lines must total the component amount")


@dataclass(frozen=True, slots=True)
class WithholdingCertificateObservationV1:
    source_owner: str
    observation_ref: str
    status: str
    evidence_ref: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if (
            any(
                not value.strip()
                for value in (
                    self.source_owner,
                    self.observation_ref,
                    self.status,
                    self.evidence_ref,
                )
            )
            or self.observed_at.tzinfo is None
        ):
            raise ValueError(
                "certificate observation requires provenance and an aware instant"
            )


@dataclass(frozen=True, slots=True)
class WithholdingDischargeEffectV1:
    discharge_id: UUID
    document_id: UUID
    claim_ref: str
    claimed_amount: Money
    accepted_delta: Money
    tax_evidence: WithholdingTaxEvidenceV1
    authorization_actor_ref: str
    authorization_evidence_ref: str
    certificate_observation: WithholdingCertificateObservationV1 | None = None
    settlement_id: UUID | None = None
    allocation_id: UUID | None = None
    reverses_discharge_id: UUID | None = None

    def __post_init__(self) -> None:
        _withholding_money(self.claimed_amount, positive=True)
        delta = self.accepted_delta
        if (
            not isinstance(delta, Money)
            or not delta.amount.is_finite()
            or delta.amount == 0
            or abs(delta.amount) > self.claimed_amount.amount
        ):
            raise ValueError(
                "withholding effect requires a bounded nonzero exact delta"
            )
        if (
            delta.currency != self.claimed_amount.currency
            or delta.currency != self.tax_evidence.amount.currency
            or self.claimed_amount.amount > self.tax_evidence.amount.amount
        ):
            raise ValueError("withholding effect must match its determined evidence")
        if (delta.amount < 0) != (self.reverses_discharge_id is not None):
            raise ValueError("withholding correction direction must match its linkage")
        if self.tax_evidence.document_id != self.document_id:
            raise ValueError("withholding effect must bind the Tax invoice")
        if any(
            not value.strip()
            for value in (
                self.claim_ref,
                self.authorization_actor_ref,
                self.authorization_evidence_ref,
            )
        ):
            raise ValueError("withholding effect requires claim and audit evidence")


@dataclass(frozen=True, slots=True)
class AccountingFactV2:
    """WHT-only successor; V1 fields, bytes and emissions remain unchanged."""

    scope: Scope
    source_system: str
    fact_id: UUID
    fact_version: int
    billing_account_id: UUID
    posting_group_id: UUID
    source_ref: str
    source_authority: Literal["internal", "provider_owned", "external_finance"]
    effect_kind: Literal["withholding_accepted", "withholding_reversed"]
    occurred_at: datetime
    committed_at: datetime
    effects: tuple[AccountingEffectV1, ...]
    withholding_discharges: tuple[WithholdingDischargeEffectV1, ...]
    reverses_fact_id: UUID | None = None


ACCOUNTING_FACT_V3_CONTRACT = "billing.accounting.fact.v3"
RECEIVABLE_POSITION_V2_CONTRACT = "billing.receivable.position.v2"
NonCashCreditEffectKind = Literal[
    "grant", "application", "application_reversal", "unused_reduction"
]


@dataclass(frozen=True, slots=True)
class NonCashCreditPolicyParameterV1:
    name: str
    value: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not isinstance(self.value, str):
            raise ValueError("policy parameters require a name and textual value")


@dataclass(frozen=True, slots=True)
class NonCashCreditClassificationV1:
    """Accepted policy snapshot; references are evidence, never permission proof."""

    category: str
    config_owner: str
    config_ref: str
    config_version: str
    config_digest: str
    accepted_snapshot: tuple[NonCashCreditPolicyParameterV1, ...]

    def __post_init__(self) -> None:
        import re

        for text, limit in (
            (self.category, 120),
            (self.config_owner, 120),
            (self.config_ref, 255),
            (self.config_version, 255),
        ):
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise ValueError(
                    "noncash classification requires bounded policy identity"
                )
        if re.fullmatch(r"[0-9a-f]{64}", self.config_digest) is None:
            raise ValueError("noncash classification requires a SHA256 digest")
        if (
            not isinstance(self.accepted_snapshot, tuple)
            or any(
                not isinstance(value, NonCashCreditPolicyParameterV1)
                for value in self.accepted_snapshot
            )
            or len({value.name for value in self.accepted_snapshot})
            != len(self.accepted_snapshot)
        ):
            raise ValueError(
                "accepted policy snapshot requires unique immutable typed parameters"
            )


@dataclass(frozen=True, slots=True)
class NonCashCreditGrantV1:
    scope: Scope
    grant_id: UUID
    billing_account_id: UUID
    original_amount: Money
    classification: NonCashCreditClassificationV1
    source_owner: str
    source_ref: str
    source_version: str
    authorization_actor_ref: str
    authorization_evidence_ref: str
    occurred_at: datetime
    restricted_document_id: UUID | None = None
    replaces_grant_id: UUID | None = None
    replaces_effect_id: UUID | None = None

    def __post_init__(self) -> None:
        _withholding_money(self.original_amount, positive=True)
        if (
            not isinstance(self.scope, TenantScope | PlatformScope)
            or self.occurred_at.tzinfo is None
        ):
            raise ValueError("grant requires explicit scope and aware instant")
        _non_cash_identity(
            self.source_owner,
            self.source_ref,
            self.source_version,
            self.authorization_actor_ref,
            self.authorization_evidence_ref,
        )
        if not isinstance(self.classification, NonCashCreditClassificationV1):
            raise TypeError("grant requires typed accepted classification")

    @property
    def subtotal(self) -> Money:
        return self.original_amount

    @property
    def total(self) -> Money:
        return self.original_amount

    @property
    def tax_total(self) -> Money:
        return Money.of(Decimal("0"), self.original_amount.currency)


@dataclass(frozen=True, slots=True)
class NonCashCreditEffectV1:
    scope: Scope
    effect_id: UUID
    grant_id: UUID
    billing_account_id: UUID
    effect_kind: NonCashCreditEffectKind
    availability_delta: Money
    source_owner: str
    source_ref: str
    source_version: str
    authorization_actor_ref: str
    authorization_evidence_ref: str
    occurred_at: datetime
    document_id: UUID | None = None
    original_application_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.availability_delta, Money):
            raise TypeError("noncash delta requires Money")
        _withholding_money(
            Money(abs(self.availability_delta.amount), self.availability_delta.currency)
        )
        if (
            not isinstance(self.scope, TenantScope | PlatformScope)
            or self.occurred_at.tzinfo is None
        ):
            raise ValueError("effect requires explicit scope and aware instant")
        _non_cash_identity(
            self.source_owner,
            self.source_ref,
            self.source_version,
            self.authorization_actor_ref,
            self.authorization_evidence_ref,
        )
        positive = self.effect_kind in ("grant", "application_reversal")
        if (
            self.effect_kind
            not in ("grant", "application", "application_reversal", "unused_reduction")
            or (self.availability_delta.amount > 0) != positive
            or self.availability_delta.amount == 0
        ):
            raise ValueError("noncash effect direction is invalid")
        if (self.document_id is not None) != (
            self.effect_kind in ("application", "application_reversal")
        ):
            raise ValueError("only application effects require an invoice")
        if (self.original_application_id is not None) != (
            self.effect_kind == "application_reversal"
        ):
            raise ValueError(
                "only an application reversal links its original application"
            )


def _non_cash_identity(owner: str, *values: str) -> None:
    if (
        not isinstance(owner, str)
        or not owner.strip()
        or len(owner) > 120
        or any(
            not isinstance(value, str) or not value.strip() or len(value) > 255
            for value in values
        )
    ):
        raise ValueError(
            "noncash facts require bounded source and authorization evidence"
        )


@dataclass(frozen=True, slots=True)
class ReceivablePositionV2(ReceivablePositionV1):
    """Four independent balances; V1 fingerprints retain their original meaning."""

    available_non_cash_credit: Money

    def __post_init__(self) -> None:
        ReceivablePositionV1.__post_init__(self)
        _withholding_money(self.available_non_cash_credit)
        if (
            self.available_non_cash_credit.amount < 0
            or self.available_non_cash_credit.currency != self.available_credit.currency
        ):
            raise ValueError(
                "available noncash credit must be nonnegative in the position currency"
            )


@dataclass(frozen=True, slots=True)
class AccountingFactV3:
    scope: Scope
    source_system: str
    fact_id: UUID
    fact_version: int
    billing_account_id: UUID
    posting_group_id: UUID
    source_ref: str
    source_authority: Literal["internal"]
    effect_kind: Literal[
        "non_cash_credit_granted",
        "non_cash_credit_applied",
        "non_cash_credit_application_reversed",
        "non_cash_credit_reduced",
    ]
    occurred_at: datetime
    committed_at: datetime
    effects: tuple[AccountingEffectV1, ...]
    non_cash_credit_effects: tuple[NonCashCreditEffectV1, ...]
    non_cash_credit_grant: NonCashCreditGrantV1 | None = None


__all__ = [
    "IssueRatedInvoiceV1",
    "IssuedRatedInvoiceV1",
    "ObligationSourceRegistry",
    "BillingAccountObservationV1",
    "GetBillingAccountObservationV1",
    "GetCurrentSettlementAllocationV1",
    "CurrentSettlementAllocationV1",
    "ACCOUNTING_FACT_V3_CONTRACT",
    "RECEIVABLE_POSITION_V2_CONTRACT",
    "NonCashCreditEffectKind",
    "NonCashCreditPolicyParameterV1",
    "NonCashCreditClassificationV1",
    "NonCashCreditGrantV1",
    "NonCashCreditEffectV1",
    "ReceivablePositionV2",
    "AccountingFactV3",
    "ACCOUNTING_FACT_CONTRACT",
    "ACCOUNTING_FACT_V2_CONTRACT",
    "AccountingFactV2",
    "WithholdingTaxLineV1",
    "WithholdingTaxEvidenceV1",
    "WithholdingCertificateObservationV1",
    "WithholdingDischargeEffectV1",
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
    "GetInvoiceDocumentFactV1",
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
    "StoredInvoiceDocumentFactV1",
]
