"""Immutable public contracts for issued-document rendering."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable
from uuid import UUID

from dotmac_kernel.cache import Scope


class DocumentKind(enum.StrEnum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    RECEIPT = "receipt"


class DocumentState(enum.StrEnum):
    ISSUED = "issued"
    CORRECTED = "corrected"
    CANCELLED = "cancelled"


class SourceAuthority(enum.StrEnum):
    INTERNAL = "internal"
    PROVIDER_OWNED = "provider_owned"
    EXTERNAL_FINANCE = "external_finance"


class RenderOutcome(enum.StrEnum):
    RENDERED = "rendered"
    REFUSED = "refused"
    FAILED = "failed"


class RenderErrorClass(enum.StrEnum):
    PERMANENT = "permanent"
    RETRYABLE = "retryable"


class RenderErrorCode(enum.StrEnum):
    FACT_INCOMPLETE = "fact_incomplete"
    FACT_VERSION_UNSUPPORTED = "fact_version_unsupported"
    FACT_SHAPE_INVALID = "fact_shape_invalid"
    TEMPLATE_NOT_FOUND = "template_not_found"
    TEMPLATE_INVALID = "template_invalid"
    MEDIA_TYPE_UNSUPPORTED = "media_type_unsupported"
    OUTPUT_TOO_LARGE = "output_too_large"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    ENGINE_TIMEOUT = "engine_timeout"


class DocumentRenderingError(Exception):
    """A stable rendering refusal/failure, safe for assembly classification."""

    error_code: RenderErrorCode
    error_class: RenderErrorClass

    def __init__(self, message: str) -> None:
        super().__init__(message)


class FactIncomplete(DocumentRenderingError):
    error_code = RenderErrorCode.FACT_INCOMPLETE
    error_class = RenderErrorClass.PERMANENT


class FactVersionUnsupported(DocumentRenderingError):
    error_code = RenderErrorCode.FACT_VERSION_UNSUPPORTED
    error_class = RenderErrorClass.PERMANENT


class FactShapeInvalid(DocumentRenderingError):
    error_code = RenderErrorCode.FACT_SHAPE_INVALID
    error_class = RenderErrorClass.PERMANENT


class TemplateNotFound(DocumentRenderingError):
    error_code = RenderErrorCode.TEMPLATE_NOT_FOUND
    error_class = RenderErrorClass.PERMANENT


class TemplateInvalid(DocumentRenderingError):
    error_code = RenderErrorCode.TEMPLATE_INVALID
    error_class = RenderErrorClass.PERMANENT


class MediaTypeUnsupported(DocumentRenderingError):
    error_code = RenderErrorCode.MEDIA_TYPE_UNSUPPORTED
    error_class = RenderErrorClass.PERMANENT


class OutputTooLarge(DocumentRenderingError):
    error_code = RenderErrorCode.OUTPUT_TOO_LARGE
    error_class = RenderErrorClass.PERMANENT


class EngineUnavailable(DocumentRenderingError):
    error_code = RenderErrorCode.ENGINE_UNAVAILABLE
    error_class = RenderErrorClass.RETRYABLE


class EngineTimeout(DocumentRenderingError):
    error_code = RenderErrorCode.ENGINE_TIMEOUT
    error_class = RenderErrorClass.RETRYABLE


@dataclass(frozen=True, slots=True)
class ExactAmount:
    """Wire-safe exact money: an ISO code, minor-unit scale, and decimal string."""

    currency: str
    minor_units: int
    amount: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, str):
            raise TypeError("amount must be a decimal string; JSON numbers are refused")
        if (
            len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isalpha()
            or self.currency != self.currency.upper()
        ):
            raise ValueError("currency must be a three-letter uppercase ISO code")
        if not 0 <= self.minor_units <= 9:
            raise ValueError("minor_units must be between zero and nine")
        try:
            value = Decimal(self.amount)
        except InvalidOperation as exc:
            raise ValueError("amount must be an exact decimal string") from exc
        if not value.is_finite():
            raise ValueError("amount must be finite")
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int):
            raise ValueError("amount must be finite")
        decimal_places = max(0, -exponent)
        if decimal_places > self.minor_units:
            raise ValueError("amount carries more precision than its minor units")


@dataclass(frozen=True, slots=True)
class PartySnapshotV1:
    legal_name: str
    address_lines: tuple[str, ...]
    registered_identifier: str | None = None
    tax_identifier: str | None = None
    contact: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "address_lines", tuple(self.address_lines))


@dataclass(frozen=True, slots=True)
class PaymentInstructionsV1:
    bank_name: str
    account_name: str
    account_number: str
    sort_code: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentLineV1:
    position: int
    description: str
    quantity: str
    unit: str
    unit_amount: ExactAmount
    line_total: ExactAmount
    applied_price_code: str
    applied_price_version: str
    source_code: str
    source_version: str


@dataclass(frozen=True, slots=True)
class DiscountLineV1:
    position: int
    description: str
    amount: ExactAmount
    source_code: str
    source_version: str


@dataclass(frozen=True, slots=True)
class TaxRateComponentV1:
    code: str
    rate: str


@dataclass(frozen=True, slots=True)
class TaxLineV1:
    position: int
    label: str
    treatment: str
    jurisdiction: str
    rate_components: tuple[TaxRateComponentV1, ...]
    taxable_basis: ExactAmount
    amount: ExactAmount
    policy_code: str
    policy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate_components", tuple(self.rate_components))


@dataclass(frozen=True, slots=True)
class FxObservationSnapshotV1:
    observation_id: str
    observation_version: str
    source_currency: str
    target_currency: str
    rate: str
    observed_at: datetime
    effective_at: datetime
    provenance: str


@dataclass(frozen=True, slots=True)
class InvoiceDocumentFactV1:
    """The complete by-value input; renderers never look an invoice up."""

    contract_version: int
    scope: Scope
    invoice_id: UUID
    fact_version: int
    emitted_at: datetime
    issued_at: datetime
    frozen_at: datetime
    document_number: str
    document_series_code: str
    document_series_version: str
    document_state: DocumentState
    document_kind: DocumentKind
    seller: PartySnapshotV1
    customer: PartySnapshotV1
    lines: tuple[DocumentLineV1, ...]
    discounts: tuple[DiscountLineV1, ...]
    tax_lines: tuple[TaxLineV1, ...]
    subtotal: ExactAmount
    tax_total: ExactAmount
    total: ExactAmount
    currency: str
    minor_units: int
    payment_terms: str
    due_date: date
    payment_instructions: PaymentInstructionsV1 | None
    brand_asset_id: UUID | None
    locale: str
    timezone: str
    document_profile_code: str
    document_profile_version: int
    source_authority: SourceAuthority
    source_system: str
    source_record_id: str
    source_record_version: str
    correlation_id: str
    fx_observation: FxObservationSnapshotV1 | None = None
    supersedes_fact_id: UUID | None = None
    superseded_by_fact_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lines", tuple(self.lines))
        object.__setattr__(self, "discounts", tuple(self.discounts))
        object.__setattr__(self, "tax_lines", tuple(self.tax_lines))


@dataclass(frozen=True, slots=True)
class LabelText:
    key: str
    text: str


@dataclass(frozen=True, slots=True)
class DocumentTemplateV1:
    """An immutable semantic template artifact, never editable in place."""

    contract_version: int
    template_code: str
    template_version: str
    language_tag: str
    labels: tuple[LabelText, ...]
    date_format_code: str
    datetime_format_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", tuple(self.labels))


@dataclass(frozen=True, slots=True)
class DocumentProfileBinding:
    document_profile_code: str
    document_profile_version: int
    media_type: str
    template_code: str
    template_version: str


@dataclass(frozen=True, slots=True)
class SelectedTemplateV1:
    binding: DocumentProfileBinding
    template: DocumentTemplateV1


@dataclass(frozen=True, slots=True)
class TemplateDecision:
    key: str
    outcome: str


@dataclass(frozen=True, slots=True)
class RenderedValue:
    kind: str
    source_field: str | None
    raw: str
    text: str
    currency: str | None = None
    minor_units: int | None = None
    timezone: str | None = None
    format_code: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    key: str
    label_text: str


@dataclass(frozen=True, slots=True)
class LabelledValue:
    label_key: str
    label_text: str
    value: RenderedValue

    def rendered_values(self) -> tuple[RenderedValue, ...]:
        return (self.value,)


@dataclass(frozen=True, slots=True)
class Table:
    columns: tuple[ColumnSpec, ...]
    rows: tuple[tuple[RenderedValue, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "rows", tuple(tuple(row) for row in self.rows))

    def rendered_values(self) -> tuple[RenderedValue, ...]:
        return tuple(value for row in self.rows for value in row)


@dataclass(frozen=True, slots=True)
class StaticText:
    key: str
    text: str

    def rendered_values(self) -> tuple[RenderedValue, ...]:
        return ()


Block = LabelledValue | Table | StaticText


@dataclass(frozen=True, slots=True)
class Section:
    key: str
    position: int
    blocks: tuple[Block, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", tuple(self.blocks))


@dataclass(frozen=True, slots=True)
class DocumentProjectionV1:
    projection_contract_version: int
    invoice_id: UUID
    fact_version: int
    document_profile_code: str
    document_profile_version: int
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
class RenderRequestV1:
    fact: InvoiceDocumentFactV1
    media_type: str
    rendered_at: datetime
    max_bytes: int
    deadline_seconds: float
    idempotency_key: str
    request_fingerprint: str
    correlation_id: str

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if self.rendered_at.tzinfo is None or self.rendered_at.utcoffset() is None:
            raise ValueError("rendered_at must be timezone-aware")
        for field_name in (
            "media_type",
            "idempotency_key",
            "request_fingerprint",
            "correlation_id",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must be non-empty")


@dataclass(frozen=True, slots=True)
class RenderedDocumentV1:
    contract_version: int
    invoice_id: UUID
    source_fact_version: int
    source_fact_fingerprint: str
    projection: DocumentProjectionV1 | None
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
class DocumentRenderer(Protocol):
    code: str
    version: str

    def media_types(self) -> frozenset[str]: ...

    def accepts_contract_versions(self) -> frozenset[int]: ...

    def projection_contract_version(self) -> int: ...

    def render(
        self,
        projection: DocumentProjectionV1,
        request: RenderRequestV1,
    ) -> RenderedDocumentV1: ...


@runtime_checkable
class PdfEngine(Protocol):
    """Local resource driver. Provider I/O and product payloads do not belong here."""

    code: str
    version: str

    def render_pdf(self, html: str, *, deadline_seconds: float) -> bytes: ...


__all__ = [
    "Block",
    "ColumnSpec",
    "DiscountLineV1",
    "DocumentKind",
    "DocumentLineV1",
    "DocumentProfileBinding",
    "DocumentProjectionV1",
    "DocumentRenderer",
    "DocumentRenderingError",
    "DocumentState",
    "DocumentTemplateV1",
    "EngineTimeout",
    "EngineUnavailable",
    "ExactAmount",
    "FactIncomplete",
    "FactShapeInvalid",
    "FactVersionUnsupported",
    "FxObservationSnapshotV1",
    "InvoiceDocumentFactV1",
    "LabelText",
    "LabelledValue",
    "MediaTypeUnsupported",
    "OutputTooLarge",
    "PartySnapshotV1",
    "PaymentInstructionsV1",
    "PdfEngine",
    "RenderErrorClass",
    "RenderErrorCode",
    "RenderOutcome",
    "RenderRequestV1",
    "RenderedDocumentV1",
    "RenderedValue",
    "Section",
    "SelectedTemplateV1",
    "SourceAuthority",
    "StaticText",
    "Table",
    "TaxLineV1",
    "TaxRateComponentV1",
    "TemplateDecision",
    "TemplateInvalid",
    "TemplateNotFound",
]
