"""Deterministic issued-document rendering from immutable by-value facts.

The module owns selection, semantic projection, formatting, and renderer
provenance. Billing owns what an invoice means and which stored artifact is
official; ``dotmac-files`` owns opaque bytes at rest; the assembly composes all
three. This package imports neither sibling and opens no transaction.
"""

from __future__ import annotations

from typing import Final

from dotmac_kernel.cache import PlatformScope, TenantScope

from dotmac_document_rendering.conformance import renderer_contract_violations
from dotmac_document_rendering.contracts import (
    Block,
    ColumnSpec,
    DiscountLineV1,
    DocumentKind,
    DocumentLineV1,
    DocumentProfileBinding,
    DocumentProjectionV1,
    DocumentRenderer,
    DocumentRenderingError,
    DocumentState,
    DocumentTemplateV1,
    EngineTimeout,
    EngineUnavailable,
    ExactAmount,
    FactIncomplete,
    FactShapeInvalid,
    FactVersionUnsupported,
    FxObservationSnapshotV1,
    InvoiceDocumentFactV1,
    LabelledValue,
    LabelText,
    MediaTypeUnsupported,
    OutputTooLarge,
    PartySnapshotV1,
    PaymentInstructionsV1,
    PdfEngine,
    RenderedDocumentV1,
    RenderedValue,
    RenderErrorClass,
    RenderErrorCode,
    RenderOutcome,
    RenderRequestV1,
    Section,
    SelectedTemplateV1,
    SourceAuthority,
    StaticText,
    Table,
    TaxLineV1,
    TaxRateComponentV1,
    TemplateDecision,
    TemplateInvalid,
    TemplateNotFound,
)
from dotmac_document_rendering.manifest import module
from dotmac_document_rendering.projection import (
    PROJECTION_CONTRACT_VERSION,
    canonical_fact_payload,
    canonical_projection_payload,
    fact_fingerprint,
    project_invoice,
    projection_digest,
    validate_fact,
    validate_projection_source_fields,
)
from dotmac_document_rendering.renderers import (
    DeterministicHtmlRenderer,
    InMemoryDocumentRenderer,
    PdfDocumentRenderer,
    html_from_projection,
)
from dotmac_document_rendering.service import render_document
from dotmac_document_rendering.templates import (
    DEFAULT_ENGLISH_LABELS,
    TemplateCatalog,
    label_map,
)

__version__: Final[str] = "0.1.0a1"

SUPPORTED_MODULES: Final[frozenset[str]] = frozenset(
    {
        "dotmac_document_rendering.conformance",
        "dotmac_document_rendering.contracts",
        "dotmac_document_rendering.manifest",
        "dotmac_document_rendering.projection",
        "dotmac_document_rendering.renderers",
        "dotmac_document_rendering.service",
        "dotmac_document_rendering.templates",
    }
)

INTERNAL_MODULES: Final[frozenset[str]] = frozenset()

__all__ = [
    "DEFAULT_ENGLISH_LABELS",
    "INTERNAL_MODULES",
    "PROJECTION_CONTRACT_VERSION",
    "SUPPORTED_MODULES",
    "Block",
    "ColumnSpec",
    "DeterministicHtmlRenderer",
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
    "InMemoryDocumentRenderer",
    "InvoiceDocumentFactV1",
    "LabelText",
    "LabelledValue",
    "MediaTypeUnsupported",
    "OutputTooLarge",
    "PartySnapshotV1",
    "PaymentInstructionsV1",
    "PdfDocumentRenderer",
    "PdfEngine",
    "PlatformScope",
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
    "TemplateCatalog",
    "TemplateDecision",
    "TemplateInvalid",
    "TemplateNotFound",
    "TenantScope",
    "__version__",
    "canonical_fact_payload",
    "canonical_projection_payload",
    "fact_fingerprint",
    "html_from_projection",
    "label_map",
    "module",
    "project_invoice",
    "projection_digest",
    "render_document",
    "renderer_contract_violations",
    "validate_fact",
    "validate_projection_source_fields",
]
