"""The three-stage rendering orchestration; still pure and persistence-free."""

from __future__ import annotations

import hashlib

from dotmac_document_rendering.contracts import (
    DocumentProjectionV1,
    DocumentRenderer,
    DocumentRenderingError,
    EngineUnavailable,
    MediaTypeUnsupported,
    OutputTooLarge,
    RenderedDocumentV1,
    RenderErrorClass,
    RenderOutcome,
    RenderRequestV1,
)
from dotmac_document_rendering.projection import (
    fact_fingerprint,
    project_invoice,
    validate_fact,
)
from dotmac_document_rendering.templates import TemplateCatalog


def _error_result(
    request: RenderRequestV1,
    renderer: DocumentRenderer,
    error: DocumentRenderingError,
    *,
    projection: DocumentProjectionV1 | None,
) -> RenderedDocumentV1:
    return RenderedDocumentV1(
        contract_version=1,
        invoice_id=request.fact.invoice_id,
        source_fact_version=request.fact.fact_version,
        source_fact_fingerprint=fact_fingerprint(request.fact),
        projection=projection,
        projection_contract_version=(
            projection.projection_contract_version if projection else None
        ),
        projection_digest=projection.digest if projection else None,
        renderer_code=renderer.code,
        renderer_version=renderer.version,
        template_code=projection.template_code if projection else None,
        template_version=projection.template_version if projection else None,
        media_type=request.media_type,
        byte_length=None,
        checksum_sha256=None,
        payload=None,
        outcome=(
            RenderOutcome.REFUSED
            if error.error_class is RenderErrorClass.PERMANENT
            else RenderOutcome.FAILED
        ),
        error_code=error.error_code,
        error_class=error.error_class,
        error_message=str(error),
        rendered_at=request.rendered_at,
        scope=request.fact.scope,
        idempotency_key=request.idempotency_key,
        request_fingerprint=request.request_fingerprint,
        correlation_id=request.correlation_id,
    )


def render_document(
    request: RenderRequestV1,
    *,
    catalog: TemplateCatalog,
    renderer: DocumentRenderer,
) -> RenderedDocumentV1:
    """Select, project, and render with stable typed refusal/failure outcomes."""

    projection: DocumentProjectionV1 | None = None
    try:
        validate_fact(request.fact)
        if request.media_type not in renderer.media_types():
            raise MediaTypeUnsupported(
                f"renderer {renderer.code!r} does not support {request.media_type!r}"
            )
        selected = catalog.select(
            document_profile_code=request.fact.document_profile_code,
            document_profile_version=request.fact.document_profile_version,
            media_type=request.media_type,
        )
        projection = project_invoice(
            request.fact,
            selected,
            renderer_code=renderer.code,
            renderer_version=renderer.version,
            media_type=request.media_type,
        )
        result = renderer.render(projection, request)
        if result.outcome is not RenderOutcome.RENDERED or result.payload is None:
            raise EngineUnavailable(
                f"renderer {renderer.code!r} returned no complete document"
            )
        if (
            result.invoice_id != request.fact.invoice_id
            or result.source_fact_version != request.fact.fact_version
            or result.source_fact_fingerprint != fact_fingerprint(request.fact)
            or result.projection != projection
            or result.projection_contract_version
            != projection.projection_contract_version
            or result.projection_digest != projection.digest
            or result.template_code != projection.template_code
            or result.template_version != projection.template_version
            or result.renderer_code != renderer.code
            or result.renderer_version != renderer.version
            or result.media_type != request.media_type
            or result.rendered_at != request.rendered_at
            or result.scope != request.fact.scope
            or result.idempotency_key != request.idempotency_key
            or result.request_fingerprint != request.request_fingerprint
            or result.correlation_id != request.correlation_id
        ):
            raise EngineUnavailable(
                f"renderer {renderer.code!r} returned inconsistent provenance"
            )
        expected_checksum = f"sha256:{hashlib.sha256(result.payload).hexdigest()}"
        if (
            result.byte_length != len(result.payload)
            or result.checksum_sha256 != expected_checksum
        ):
            raise EngineUnavailable(
                f"renderer {renderer.code!r} returned inconsistent byte integrity"
            )
        if len(result.payload) > request.max_bytes:
            raise OutputTooLarge(
                f"rendered output exceeds the {request.max_bytes}-byte limit"
            )
        return result
    except DocumentRenderingError as error:
        return _error_result(
            request,
            renderer,
            error,
            projection=projection,
        )


__all__ = ["render_document"]
