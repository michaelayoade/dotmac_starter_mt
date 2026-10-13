"""Billing-shaped, immutable V2 inputs; no dependency on Billing runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Protocol, runtime_checkable
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

from dotmac_document_rendering.contracts import (
    DocumentTemplateV1,
    FactShapeInvalid,
    LabelledValue,
    RenderErrorClass,
    RenderErrorCode,
    RenderOutcome,
    Section,
    TemplateDecision,
)


def _require_utf8_text(value: str, path: str) -> None:
    """Require encodable text without altering any valid Unicode bytes."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FactShapeInvalid(f"{path} must be valid UTF-8 text") from exc


@dataclass(frozen=True, slots=True)
class PostalAddressV2:
    line_one: str
    country_code: str
    line_two: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None


@dataclass(frozen=True, slots=True)
class RegisteredIdentifierV2:
    identifier_type: str
    identifier_value: str
    issuing_country_code: str | None = None


@dataclass(frozen=True, slots=True)
class PartySnapshotV2:
    legal_name: str
    address: PostalAddressV2
    registered_identifiers: tuple[RegisteredIdentifierV2, ...] = ()
    trading_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "registered_identifiers", tuple(self.registered_identifiers)
        )


@dataclass(frozen=True, slots=True)
class PartyTaxIdentityV2:
    party_role: str
    identity_type: str
    identity_value: str
    country_code: str
    source_authority: str
    source_version: str


@dataclass(frozen=True, slots=True)
class DiscountV2:
    discount_code: str
    description: str
    amount: Money
    source_version: str


@dataclass(frozen=True, slots=True)
class RecordedLineReferenceV2:
    emitter: str
    fact_id: UUID
    fact_version: int
    line_number: int


@dataclass(frozen=True, slots=True)
class DocumentLineV2:
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
    recorded_line: RecordedLineReferenceV2
    discounts: tuple[DiscountV2, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "discounts", tuple(self.discounts))


@dataclass(frozen=True, slots=True)
class TaxSnapshotV2:
    treatment_code: str
    jurisdiction_code: str
    policy_id: str
    policy_version: str
    rate: Decimal
    taxable_basis: Money
    tax_amount: Money


@dataclass(frozen=True, slots=True)
class FxSnapshotV2:
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
class DueDateBasisV2:
    status: str
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


@dataclass(frozen=True, slots=True)
class PresentationAssetV2:
    status: str
    file_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class FrozenPaymentLinkV2:
    """Only a future owner-versioned frozen snapshot can supply this link."""

    url: str
    owner: str
    snapshot_id: str
    snapshot_version: str


@dataclass(frozen=True, slots=True)
class PaymentInstructionsV2:
    method_code: str
    bank_name: str | None = None
    account_name: str | None = None
    account_reference: str | None = None
    routing_code: str | None = None
    narrative: str | None = None
    payment_link: FrozenPaymentLinkV2 | None = None


@dataclass(frozen=True, slots=True)
class InvoiceDocumentFactV2:
    contract_version: int
    scope: Scope
    invoice_id: UUID
    emitter: str
    persisted_fact_id: UUID
    persisted_contract_version: str
    fact_version: int
    recorded_at: datetime
    original_presentation_model_digest: str
    series_code: str
    series_version: str | None
    document_number: str
    document_kind: str
    document_state: str
    document_profile_code: str
    document_profile_version: str
    currency: str
    minor_units: int
    subtotal: Money
    tax_total: Money
    grand_total: Money
    due_at: datetime | None
    due_date_basis: DueDateBasisV2
    seller_snapshot: PartySnapshotV2
    customer_snapshot: PartySnapshotV2
    payment_instructions: PaymentInstructionsV2
    brand_asset: PresentationAssetV2
    locale: str
    timezone: str
    lines: tuple[DocumentLineV2, ...]
    tax_snapshots: tuple[TaxSnapshotV2, ...]
    fx_snapshot: FxSnapshotV2 | None
    party_tax_identities: tuple[PartyTaxIdentityV2, ...]
    issued_at: datetime
    frozen_at: datetime
    source_authority: str
    correlation_id: str

    def __post_init__(self) -> None:
        for name in ("lines", "tax_snapshots", "party_tax_identities"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class DocumentProfileBindingV2:
    document_profile_code: str
    document_profile_version: str
    media_type: str
    template_code: str
    template_version: str


@dataclass(frozen=True, slots=True)
class SelectedTemplateV2:
    binding: DocumentProfileBindingV2
    template: DocumentTemplateV1


@dataclass(frozen=True, slots=True)
class PaymentLinkValueV2(LabelledValue):
    """An inert frozen HTTPS link; rendering never follows it."""

    url: str


@dataclass(frozen=True, slots=True)
class DocumentProjectionV2:
    projection_contract_version: int
    invoice_id: UUID
    fact_version: int
    source_fact_fingerprint: str
    document_profile_code: str
    document_profile_version: str
    template_code: str
    template_version: str
    renderer_code: str
    renderer_version: str
    media_type: str
    template_decisions: tuple[TemplateDecision, ...]
    sections: tuple[Section, ...]
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_decisions", tuple(self.template_decisions))
        object.__setattr__(self, "sections", tuple(self.sections))


@dataclass(frozen=True, slots=True)
class RenderRequestV2:
    fact: InvoiceDocumentFactV2
    media_type: str
    rendered_at: datetime
    max_bytes: int
    deadline_seconds: float
    idempotency_key: str
    request_fingerprint: str
    correlation_id: str

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if (
            isinstance(self.deadline_seconds, bool)
            or not isinstance(self.deadline_seconds, int | float)
            or not isfinite(self.deadline_seconds)
            or self.deadline_seconds <= 0
        ):
            raise ValueError("deadline_seconds must be positive")
        if self.rendered_at.tzinfo is None or self.rendered_at.utcoffset() is None:
            raise ValueError("rendered_at must be timezone-aware")
        for field_name in (
            "media_type",
            "idempotency_key",
            "request_fingerprint",
            "correlation_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise FactShapeInvalid(f"{field_name} must be text")
            _require_utf8_text(value, field_name)
            if not value.strip():
                raise ValueError(f"{field_name} must be non-empty")


@dataclass(frozen=True, slots=True)
class RenderedDocumentV2:
    contract_version: int
    invoice_id: UUID
    source_fact_version: int
    source_fact_fingerprint: str
    persisted_fact_id: UUID
    original_presentation_model_digest: str
    projection: DocumentProjectionV2 | None
    projection_contract_version: int | None
    projection_digest: str | None
    renderer_code: str
    renderer_version: str
    template_code: str | None
    template_version: str | None
    media_type: str
    byte_length: int | None
    checksum_sha256: str | None
    payload: bytes | None
    outcome: RenderOutcome
    error_code: RenderErrorCode | None
    error_class: RenderErrorClass | None
    error_message: str | None
    rendered_at: datetime
    scope: Scope
    idempotency_key: str
    request_fingerprint: str
    correlation_id: str


@runtime_checkable
class DocumentRendererV2(Protocol):
    code: str
    version: str

    def media_types(self) -> frozenset[str]: ...

    def accepts_contract_versions(self) -> frozenset[int]: ...

    def projection_contract_version(self) -> int: ...

    def render(
        self,
        projection: DocumentProjectionV2,
        request: RenderRequestV2,
    ) -> RenderedDocumentV2: ...


__all__ = [
    "PostalAddressV2",
    "RegisteredIdentifierV2",
    "PartySnapshotV2",
    "PartyTaxIdentityV2",
    "DiscountV2",
    "RecordedLineReferenceV2",
    "DocumentLineV2",
    "TaxSnapshotV2",
    "FxSnapshotV2",
    "DueDateBasisV2",
    "PresentationAssetV2",
    "FrozenPaymentLinkV2",
    "PaymentInstructionsV2",
    "InvoiceDocumentFactV2",
    "DocumentProfileBindingV2",
    "SelectedTemplateV2",
    "PaymentLinkValueV2",
    "DocumentProjectionV2",
    "RenderRequestV2",
    "RenderedDocumentV2",
    "DocumentRendererV2",
]
