"""V3 orchestration: select, verify the logo, project, render, check, return."""

from __future__ import annotations

import hashlib
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, TenantScope

from dotmac_document_rendering.conformance_v3 import result_contract_violations_v3
from dotmac_document_rendering.contracts import (
    DocumentRenderingError,
    EngineUnavailable,
    FactShapeInvalid,
    MediaTypeUnsupported,
    OutputTooLarge,
    RenderErrorClass,
    RenderOutcome,
)
from dotmac_document_rendering.contracts_v3 import (
    DATA_ONLY_RESOURCE_POLICY,
    DocumentProjectionV3,
    DocumentRendererV3,
    InvoiceDocumentFactV3,
    RenderedDocumentV3,
    RenderRequestV3,
)
from dotmac_document_rendering.projection_v3 import (
    fact_fingerprint_v3,
    project_invoice_v3,
    validate_fact_v3,
    verify_logo_content_v3,
)
from dotmac_document_rendering.templates_v2 import TemplateCatalogV2


def _safe_fact_fingerprint(request: RenderRequestV3) -> str:
    try:
        return fact_fingerprint_v3(request.fact)
    except (FactShapeInvalid, ValueError, TypeError, AttributeError):
        return ""


def _error_result(
    request: RenderRequestV3,
    renderer: DocumentRendererV3,
    error: DocumentRenderingError,
    *,
    projection: DocumentProjectionV3 | None,
) -> RenderedDocumentV3:
    return RenderedDocumentV3(
        contract_version=3,
        invoice_id=request.fact.invoice_id,
        source_fact_version=request.fact.fact_version,
        source_fact_fingerprint=_safe_fact_fingerprint(request),
        persisted_fact_id=request.fact.persisted_fact_id,
        original_presentation_model_digest=request.fact.original_presentation_model_digest,
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


def render_document_v3(
    request: RenderRequestV3,
    *,
    catalog: TemplateCatalogV2,
    renderer: DocumentRendererV3,
) -> RenderedDocumentV3:
    """Refuse before rendering unless the logo bytes are exactly the frozen logo."""

    if not isinstance(request, RenderRequestV3):
        raise FactShapeInvalid("request must use the V3 immutable contract")
    if not isinstance(request.fact, InvoiceDocumentFactV3):
        raise FactShapeInvalid("fact must use the V3 immutable contract")
    if (
        not isinstance(request.fact.invoice_id, UUID)
        or not isinstance(request.fact.persisted_fact_id, UUID)
        or not isinstance(request.fact.scope, TenantScope | PlatformScope)
        or type(request.fact.fact_version) is not int
        or not isinstance(request.fact.original_presentation_model_digest, str)
        or (
            isinstance(request.fact.scope, TenantScope)
            and not isinstance(request.fact.scope.tenant_id, UUID)
        )
    ):
        raise FactShapeInvalid(
            "fact requires typed source identity to return a rendering outcome"
        )
    projection: DocumentProjectionV3 | None = None
    try:
        validate_fact_v3(request.fact)
        if request.fact.brand_logo is None:
            if request.logo_content is not None:
                raise FactShapeInvalid("logo bytes supplied for a fact without a logo")
        elif request.logo_content is None:
            raise FactShapeInvalid(
                "the fact freezes a brand logo but no bytes were supplied"
            )
        else:
            verify_logo_content_v3(request.fact.brand_logo, request.logo_content)
        if getattr(renderer, "resource_policy", None) != DATA_ONLY_RESOURCE_POLICY:
            raise EngineUnavailable(
                f"renderer {renderer.code!r} does not declare the "
                f"{DATA_ONLY_RESOURCE_POLICY!r} resource policy"
            )
        if request.media_type not in renderer.media_types():
            raise MediaTypeUnsupported(
                f"renderer {renderer.code!r} does not support {request.media_type!r}"
            )
        if (
            request.fact.contract_version not in renderer.accepts_contract_versions()
            or renderer.projection_contract_version() != 3
        ):
            raise MediaTypeUnsupported("renderer does not accept V3 contracts")
        selected = catalog.select(
            document_profile_code=request.fact.document_profile_code,
            document_profile_version=request.fact.document_profile_version,
            media_type=request.media_type,
        )
        projection = project_invoice_v3(
            request.fact,
            selected,
            renderer_code=renderer.code,
            renderer_version=renderer.version,
            media_type=request.media_type,
        )
        try:
            result = renderer.render(projection, request)
        except DocumentRenderingError:
            raise
        except Exception as exc:
            raise EngineUnavailable(
                "renderer adapter failed to produce a document"
            ) from exc
        if not isinstance(result, RenderedDocumentV3):
            raise EngineUnavailable("renderer returned an unsupported result contract")
        if result.outcome is not RenderOutcome.RENDERED or not isinstance(
            result.payload, bytes
        ):
            raise EngineUnavailable(
                f"renderer {renderer.code!r} returned no complete document"
            )
        if (
            result.invoice_id != request.fact.invoice_id
            or result.source_fact_version != request.fact.fact_version
            or result.source_fact_fingerprint != fact_fingerprint_v3(request.fact)
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
        problems = result_contract_violations_v3(
            renderer, projection=projection, request=request, result=result
        )
        if problems:
            raise EngineUnavailable("; ".join(problems))
        return result
    except DocumentRenderingError as error:
        return _error_result(request, renderer, error, projection=projection)


__all__ = ["render_document_v3"]
