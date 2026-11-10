"""V3 HTML renderer and PDF adapter with a data-only resource policy."""

from __future__ import annotations

import hashlib

from dotmac_document_rendering.contracts import (
    EngineTimeout,
    EngineUnavailable,
    FactIncomplete,
    FactShapeInvalid,
    FactVersionUnsupported,
    MediaTypeUnsupported,
    OutputTooLarge,
    RenderOutcome,
)
from dotmac_document_rendering.contracts_v2 import DocumentProjectionV2
from dotmac_document_rendering.contracts_v3 import (
    DATA_ONLY_RESOURCE_POLICY,
    DocumentProjectionV3,
    EmbeddedLogoAssetV3,
    PdfEngineV3,
    RenderedDocumentV3,
    RenderRequestV3,
)
from dotmac_document_rendering.output_validation_v2 import (
    pdf_contract_violations_v2,
    validate_stylesheet_v2,
)
from dotmac_document_rendering.output_validation_v3 import (
    data_uri_for_logo_v3,
    html_data_only_violations_v3,
)
from dotmac_document_rendering.projection_v3 import (
    PROJECTION_CONTRACT_VERSION_V3,
    fact_fingerprint_v3,
    projection_digest_v3,
    verify_logo_content_v3,
)
from dotmac_document_rendering.renderers_v2 import (
    _DEFAULT_STYLESHEET,
    html_from_projection_v2,
)

_BODY_OPEN = "</head><body>"


def _v2_view(projection: DocumentProjectionV3) -> DocumentProjectionV2:
    """The V2-shaped projection used only to reuse V2 markup and PDF checks."""
    return DocumentProjectionV2(
        2,
        projection.invoice_id,
        projection.fact_version,
        projection.source_fact_fingerprint,
        projection.document_profile_code,
        projection.document_profile_version,
        projection.template_code,
        projection.template_version,
        projection.renderer_code,
        projection.renderer_version,
        projection.media_type,
        projection.template_decisions,
        projection.sections,
        projection.digest,
    )


def _check_logo(projection: DocumentProjectionV3, logo_content: bytes | None) -> None:
    check_logo_v3(projection.brand_logo, logo_content)


def check_logo_v3(
    asset: EmbeddedLogoAssetV3 | None, logo_content: bytes | None
) -> None:
    """Refuse missing, unexpected or unverified logo bytes."""
    if asset is None:
        if logo_content is not None:
            raise FactShapeInvalid("logo bytes supplied for a fact without a logo")
        return
    if logo_content is None:
        raise FactIncomplete("the fact freezes a brand logo but no bytes were supplied")
    verify_logo_content_v3(asset, logo_content)


def embed_verified_logo_v3(
    html: str, asset: EmbeddedLogoAssetV3 | None, logo_content: bytes | None
) -> str:
    """Place the one verified data: image first in the body, or change nothing."""
    check_logo_v3(asset, logo_content)
    if asset is None or logo_content is None:
        return html
    head, separator, tail = html.partition(_BODY_OPEN)
    if not separator:
        raise FactShapeInvalid("document markup has no body to place the logo in")
    uri = data_uri_for_logo_v3(asset, logo_content)
    return f'{head}{separator}<img alt="" src="{uri}">{tail}'


def html_from_projection_v3(
    projection: DocumentProjectionV3,
    *,
    logo_content: bytes | None = None,
    stylesheet: str = _DEFAULT_STYLESHEET,
) -> str:
    """Serialize the projection; the only resource is the verified data: logo."""
    if not isinstance(projection, DocumentProjectionV3):
        raise FactShapeInvalid("projection must use the V3 immutable contract")
    projection_digest_v3(projection)  # validate every nested projection leaf
    check_logo_v3(projection.brand_logo, logo_content)
    html = html_from_projection_v2(_v2_view(projection), stylesheet=stylesheet)
    html = embed_verified_logo_v3(html, projection.brand_logo, logo_content)
    problems = html_data_only_violations_v3(
        html, max_images=0 if projection.brand_logo is None else 1
    )
    if problems:
        raise FactShapeInvalid("; ".join(problems))
    return html


def _success(
    *,
    projection: DocumentProjectionV3,
    request: RenderRequestV3,
    renderer_code: str,
    renderer_version: str,
    payload: bytes,
) -> RenderedDocumentV3:
    return RenderedDocumentV3(
        contract_version=3,
        invoice_id=request.fact.invoice_id,
        source_fact_version=request.fact.fact_version,
        source_fact_fingerprint=fact_fingerprint_v3(request.fact),
        persisted_fact_id=request.fact.persisted_fact_id,
        original_presentation_model_digest=request.fact.original_presentation_model_digest,
        projection=projection,
        projection_contract_version=projection.projection_contract_version,
        projection_digest=projection.digest,
        renderer_code=renderer_code,
        renderer_version=renderer_version,
        template_code=projection.template_code,
        template_version=projection.template_version,
        media_type=request.media_type,
        byte_length=len(payload),
        checksum_sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
        payload=payload,
        outcome=RenderOutcome.RENDERED,
        error_code=None,
        error_class=None,
        error_message=None,
        rendered_at=request.rendered_at,
        scope=request.fact.scope,
        idempotency_key=request.idempotency_key,
        request_fingerprint=request.request_fingerprint,
        correlation_id=request.correlation_id,
    )


def _validate_call(
    *,
    projection: DocumentProjectionV3,
    request: RenderRequestV3,
    code: str,
    version: str,
    media_types: frozenset[str],
) -> None:
    if not isinstance(projection, DocumentProjectionV3):
        raise FactShapeInvalid("projection must use the V3 immutable contract")
    projection_digest_v3(projection)
    if request.fact.contract_version != 3:
        raise FactVersionUnsupported(
            f"fact contract version {request.fact.contract_version} is not accepted"
        )
    if request.media_type not in media_types:
        raise MediaTypeUnsupported(
            f"media type {request.media_type!r} is not supported"
        )
    if projection.projection_contract_version != PROJECTION_CONTRACT_VERSION_V3:
        raise FactShapeInvalid(
            "projection contract version does not match the renderer"
        )
    if projection.digest != projection_digest_v3(
        projection
    ) or projection.source_fact_fingerprint != fact_fingerprint_v3(request.fact):
        raise FactShapeInvalid("projection digest or source evidence is stale")
    if projection.media_type != request.media_type:
        raise FactShapeInvalid("projection media type does not match the request")
    if projection.renderer_code != code or projection.renderer_version != version:
        raise FactShapeInvalid(
            "projection renderer provenance does not match the renderer"
        )
    if (
        projection.invoice_id != request.fact.invoice_id
        or projection.fact_version != request.fact.fact_version
        or projection.brand_logo != request.fact.brand_logo
    ):
        raise FactShapeInvalid("projection source identity does not match the fact")
    _check_logo(projection, request.logo_content)


class DeterministicHtmlRendererV3:
    """Provider-free renderer; its only resource is the verified data: logo."""

    code = "dotmac-html"
    version = "3"
    resource_policy = DATA_ONLY_RESOURCE_POLICY

    def __init__(self, *, stylesheet: str = _DEFAULT_STYLESHEET) -> None:
        validate_stylesheet_v2(stylesheet)
        self._stylesheet = stylesheet

    def media_types(self) -> frozenset[str]:
        return frozenset({"text/html"})

    def accepts_contract_versions(self) -> frozenset[int]:
        return frozenset({3})

    def projection_contract_version(self) -> int:
        return PROJECTION_CONTRACT_VERSION_V3

    def render(
        self,
        projection: DocumentProjectionV3,
        request: RenderRequestV3,
    ) -> RenderedDocumentV3:
        _validate_call(
            projection=projection,
            request=request,
            code=self.code,
            version=self.version,
            media_types=self.media_types(),
        )
        payload = html_from_projection_v3(
            projection,
            logo_content=request.logo_content,
            stylesheet=self._stylesheet,
        ).encode("utf-8")
        return _success(
            projection=projection,
            request=request,
            renderer_code=self.code,
            renderer_version=self.version,
            payload=payload,
        )


class InMemoryDocumentRendererV3(DeterministicHtmlRendererV3):
    """Credential-free renderer fake for product development and contract tests."""

    code = "dotmac-memory-html"
    version = "3"


class PdfDocumentRendererV3:
    """Adapter around one local PDF engine that declares the data-only policy."""

    resource_policy = DATA_ONLY_RESOURCE_POLICY

    def __init__(
        self,
        engine: PdfEngineV3,
        *,
        stylesheet: str = _DEFAULT_STYLESHEET,
    ) -> None:
        if getattr(engine, "resource_policy", None) != DATA_ONLY_RESOURCE_POLICY:
            raise EngineUnavailable(
                f"PDF engine {getattr(engine, 'code', '?')!r} does not declare "
                f"the {DATA_ONLY_RESOURCE_POLICY!r} resource policy"
            )
        validate_stylesheet_v2(stylesheet)
        self._engine = engine
        self._stylesheet = stylesheet
        self.code = f"pdf-v3:{engine.code}"
        self.version = engine.version

    def media_types(self) -> frozenset[str]:
        return frozenset({"application/pdf"})

    def accepts_contract_versions(self) -> frozenset[int]:
        return frozenset({3})

    def projection_contract_version(self) -> int:
        return PROJECTION_CONTRACT_VERSION_V3

    def render(
        self,
        projection: DocumentProjectionV3,
        request: RenderRequestV3,
    ) -> RenderedDocumentV3:
        _validate_call(
            projection=projection,
            request=request,
            code=self.code,
            version=self.version,
            media_types=self.media_types(),
        )
        html = html_from_projection_v3(
            projection,
            logo_content=request.logo_content,
            stylesheet=self._stylesheet,
        )
        try:
            payload = self._engine.render_pdf(
                html, deadline_seconds=request.deadline_seconds
            )
        except EngineUnavailable:
            raise
        except TimeoutError as exc:
            raise EngineTimeout(
                f"PDF engine {self._engine.code!r} exceeded the declared deadline"
            ) from exc
        except Exception as exc:
            raise EngineUnavailable(
                f"PDF engine {self._engine.code!r} could not render the document"
            ) from exc
        if not isinstance(payload, bytes):
            raise EngineUnavailable("PDF engine did not return bytes")
        if len(payload) > request.max_bytes:
            raise OutputTooLarge("PDF engine exceeded the request byte limit")
        problems = pdf_contract_violations_v2(payload, _v2_view(projection))
        if problems:
            raise EngineUnavailable("; ".join(problems))
        return _success(
            projection=projection,
            request=request,
            renderer_code=self.code,
            renderer_version=self.version,
            payload=payload,
        )


__all__ = [
    "DeterministicHtmlRendererV3",
    "check_logo_v3",
    "embed_verified_logo_v3",
    "InMemoryDocumentRendererV3",
    "PdfDocumentRendererV3",
    "html_from_projection_v3",
]
