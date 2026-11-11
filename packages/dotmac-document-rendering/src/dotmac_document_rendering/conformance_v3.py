"""V3 adapter evidence: provenance, integrity, completeness and data-only HTML."""

from __future__ import annotations

import hashlib
from typing import ClassVar

from dotmac_document_rendering.conformance_v2 import _Text
from dotmac_document_rendering.contracts import (
    RenderOutcome,
)
from dotmac_document_rendering.contracts_v3 import (
    DATA_ONLY_RESOURCE_POLICY,
    DocumentProjectionV3,
    DocumentRendererV3,
    RenderedDocumentV3,
    RenderRequestV3,
)
from dotmac_document_rendering.output_validation_v2 import (
    pdf_contract_violations_v2,
    projected_text_v2,
)
from dotmac_document_rendering.output_validation_v3 import (
    html_data_only_violations_v3,
)
from dotmac_document_rendering.projection_v3 import (
    fact_fingerprint_v3,
    projection_digest_v3,
)
from dotmac_document_rendering.renderers_v3 import _v2_view, html_from_projection_v3


class _TextV3(_Text):
    """The closed V2 markup grammar plus exactly one data: logo image."""

    _parents: ClassVar[dict[str, set[str | None]]] = {
        **_Text._parents,
        "img": {"body"},
    }

    def __init__(self) -> None:
        super().__init__()
        self.images: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            super().handle_starttag(tag, attrs)
            return
        values = dict(attrs)
        parent = self.stack[-1] if self.stack else None
        if (
            parent != "body"
            or len(values) != len(attrs)
            or set(values) != {"alt", "src"}
            or values["alt"] != ""
        ):
            self.active = True
        self.images.append(values.get("src"))
        self.signature.append(
            ("start", tag, tuple(sorted(attrs, key=lambda item: item[0])))
        )


def result_contract_violations_v3(
    renderer: DocumentRendererV3,
    *,
    projection: DocumentProjectionV3,
    request: RenderRequestV3,
    result: RenderedDocumentV3,
) -> tuple[str, ...]:
    problems: list[str] = []
    if result.outcome is not RenderOutcome.RENDERED or not isinstance(
        result.payload, bytes
    ):
        return ("renderer did not return complete bytes",)
    if (
        result.projection != projection
        or result.projection_digest != projection.digest
        or projection.digest != projection_digest_v3(projection)
    ):
        problems.append("renderer changed projection or returned a stale digest")
    expected = {
        "contract_version": 3,
        "invoice_id": request.fact.invoice_id,
        "source_fact_version": request.fact.fact_version,
        "persisted_fact_id": request.fact.persisted_fact_id,
        "original_presentation_model_digest": (
            request.fact.original_presentation_model_digest
        ),
        "source_fact_fingerprint": fact_fingerprint_v3(request.fact),
        "projection_contract_version": 3,
        "template_code": projection.template_code,
        "template_version": projection.template_version,
        "renderer_code": renderer.code,
        "renderer_version": renderer.version,
        "media_type": request.media_type,
        "rendered_at": request.rendered_at,
        "scope": request.fact.scope,
        "idempotency_key": request.idempotency_key,
        "request_fingerprint": request.request_fingerprint,
        "correlation_id": request.correlation_id,
        "byte_length": len(result.payload),
        "checksum_sha256": f"sha256:{hashlib.sha256(result.payload).hexdigest()}",
        "error_code": None,
        "error_class": None,
        "error_message": None,
    }
    for name, value in expected.items():
        if getattr(result, name) != value:
            problems.append(f"renderer returned inconsistent {name}")
    if len(result.payload) > request.max_bytes:
        problems.append("renderer exceeded output limit")
    if request.media_type == "text/html":
        parser = _TextV3()
        try:
            text = result.payload.decode("utf-8")
            parser.feed(text)
        except (UnicodeDecodeError, ValueError):
            problems.append("renderer returned invalid HTML text")
        else:
            view = _v2_view(projection)
            if parser.values != list(projected_text_v2(view)):
                problems.append("HTML omitted, changed or reordered projected content")
            expected_parser = _TextV3()
            expected_parser.feed(
                html_from_projection_v3(projection, logo_content=request.logo_content)
            )
            if parser.signature != expected_parser.signature or parser.stack:
                problems.append("HTML changed the bounded invoice markup structure")
            if parser.active:
                problems.append(
                    "HTML contains content outside the passive markup grammar"
                )
            problems.extend(
                html_data_only_violations_v3(
                    text, max_images=0 if projection.brand_logo is None else 1
                )
            )
    elif request.media_type == "application/pdf":
        problems.extend(
            pdf_contract_violations_v2(result.payload, _v2_view(projection))
        )
    return tuple(dict.fromkeys(problems))


def renderer_contract_violations_v3(
    renderer: DocumentRendererV3,
    *,
    projection: DocumentProjectionV3,
    request: RenderRequestV3,
) -> tuple[str, ...]:
    problems: list[str] = []
    if (
        request.media_type not in renderer.media_types()
        or 3 not in renderer.accepts_contract_versions()
        or renderer.projection_contract_version() != 3
        or getattr(renderer, "resource_policy", None) != DATA_ONLY_RESOURCE_POLICY
    ):
        problems.append("renderer does not declare requested V3 contracts and policy")
    results: list[RenderedDocumentV3] = []
    for run in range(2):
        try:
            result = renderer.render(projection, request)
            results.append(result)
            problems.extend(
                result_contract_violations_v3(
                    renderer, projection=projection, request=request, result=result
                )
            )
        except Exception as exc:
            problems.append(f"render run {run + 1} raised {type(exc).__name__}")
    if (
        len(results) == 2
        and results[0].projection_digest != results[1].projection_digest
    ):
        problems.append("two runs produced different projection digests")
    return tuple(dict.fromkeys(problems))


__all__ = [
    "renderer_contract_violations_v3",
    "result_contract_violations_v3",
]
