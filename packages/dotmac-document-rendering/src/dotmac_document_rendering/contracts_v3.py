"""V3 inputs: a Billing-shaped fact with a frozen, embedded, data-only logo.

V3 is additive. The V2 contracts, their wire form and their digests are
untouched. V3 differs in exactly three ways:

* the fact names its brand logo as an immutable ``EmbeddedLogoAssetV3``
  (identity, media type, digest, length) instead of a bare file reference;
* the request carries the verified logo bytes by value, so a renderer never
  looks a file, a brand or a URL up while rendering;
* renderers and PDF engines declare a ``data-only`` resource policy: the only
  resource a document may reference is a ``data:`` URI that the renderer built.

Brand reaches this package only as frozen snapshots. It imports no brand,
files or storage owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.money import Money

from dotmac_document_rendering.contracts import (
    FactShapeInvalid,
    RenderErrorClass,
    RenderErrorCode,
    RenderOutcome,
    Section,
    TemplateDecision,
)
from dotmac_document_rendering.contracts_v2 import (
    DocumentLineV2,
    DueDateBasisV2,
    FxSnapshotV2,
    PartySnapshotV2,
    PartyTaxIdentityV2,
    PaymentInstructionsV2,
    TaxSnapshotV2,
    _require_utf8_text,
)

DATA_ONLY_RESOURCE_POLICY: Final = "data-only"
LOGO_MAX_BYTES: Final = 256 * 1024
LOGO_MEDIA_TYPES: Final = frozenset({"image/png", "image/jpeg"})


@dataclass(frozen=True, slots=True)
class EmbeddedLogoAssetV3:
    """A frozen logo identity. The bytes travel in the request, never here."""

    file_id: UUID
    media_type: str
    sha256: str
    byte_length: int


@dataclass(frozen=True, slots=True)
class InvoiceDocumentFactV3:
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
    brand_logo: EmbeddedLogoAssetV3 | None
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
class DocumentProjectionV3:
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
    brand_logo: EmbeddedLogoAssetV3 | None
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_decisions", tuple(self.template_decisions))
        object.__setattr__(self, "sections", tuple(self.sections))


@dataclass(frozen=True, slots=True)
class RenderRequestV3:
    fact: InvoiceDocumentFactV3
    media_type: str
    rendered_at: datetime
    max_bytes: int
    deadline_seconds: float
    idempotency_key: str
    request_fingerprint: str
    correlation_id: str
    # The logo bytes, read by the assembly through a scoped verified read. The
    # renderer re-verifies them against the frozen digest before use.
    logo_content: bytes | None = None

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
        if self.logo_content is not None and not isinstance(self.logo_content, bytes):
            raise FactShapeInvalid("logo_content must be immutable bytes")
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
class RenderedDocumentV3:
    contract_version: int
    invoice_id: UUID
    source_fact_version: int
    source_fact_fingerprint: str
    persisted_fact_id: UUID
    original_presentation_model_digest: str
    projection: DocumentProjectionV3 | None
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
class DocumentRendererV3(Protocol):
    code: str
    version: str
    resource_policy: str

    def media_types(self) -> frozenset[str]: ...

    def accepts_contract_versions(self) -> frozenset[int]: ...

    def projection_contract_version(self) -> int: ...

    def render(
        self,
        projection: DocumentProjectionV3,
        request: RenderRequestV3,
    ) -> RenderedDocumentV3: ...


@runtime_checkable
class PdfEngineV3(Protocol):
    """A local PDF driver that fetches nothing.

    ``resource_policy`` must equal ``DATA_ONLY_RESOURCE_POLICY``: the engine
    resolves no ``file:``, ``http:``, ``https:`` or relative reference, reads no
    local file and opens no socket. The input is one complete HTML string whose
    only resources are ``data:`` URIs. An engine that cannot promise that must
    not be composed with V3; ``PdfDocumentRendererV3`` refuses it.
    """

    code: str
    version: str
    resource_policy: str

    def render_pdf(self, html: str, *, deadline_seconds: float) -> bytes: ...


__all__ = [
    "DATA_ONLY_RESOURCE_POLICY",
    "LOGO_MAX_BYTES",
    "LOGO_MEDIA_TYPES",
    "DocumentProjectionV3",
    "DocumentRendererV3",
    "EmbeddedLogoAssetV3",
    "InvoiceDocumentFactV3",
    "PdfEngineV3",
    "RenderRequestV3",
    "RenderedDocumentV3",
]
