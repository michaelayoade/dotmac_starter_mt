"""Reusable independent adapter evidence: provenance, integrity and completeness."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from typing import ClassVar

from dotmac_document_rendering.contracts import (
    FactShapeInvalid,
    RenderOutcome,
)
from dotmac_document_rendering.contracts_v2 import (
    DocumentProjectionV2,
    DocumentRendererV2,
    RenderedDocumentV2,
    RenderRequestV2,
)
from dotmac_document_rendering.output_validation_v2 import (
    pdf_contract_violations_v2,
    projected_text_v2,
    validate_stylesheet_v2,
)
from dotmac_document_rendering.projection_v2 import (
    fact_fingerprint_v2,
    projection_digest_v2,
)
from dotmac_document_rendering.renderers_v2 import html_from_projection_v2


class _Text(HTMLParser):
    """Parse only the closed invoice markup grammar; retain full structure."""

    _parents: ClassVar[dict[str, set[str | None]]] = {
        "html": {None},
        "head": {"html"},
        "meta": {"head"},
        "style": {"head"},
        "body": {"html"},
        "section": {"body"},
        "h2": {"section"},
        "p": {"section"},
        "dl": {"section"},
        "dt": {"dl"},
        "dd": {"dl"},
        "a": {"dd"},
        "table": {"section"},
        "thead": {"table"},
        "tbody": {"table"},
        "tr": {"thead", "tbody"},
        "th": {"tr"},
        "td": {"tr"},
    }
    _attributes: ClassVar[dict[str, set[str]]] = {
        "meta": {"charset"},
        "section": {"data-section"},
        "table": {"data-table"},
        "tr": {"data-row"},
        "a": {"href"},
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []
        self.active = False
        self.links: list[str | None] = []
        self.stack: list[str] = []
        self.signature: list[tuple[str, str, tuple[tuple[str, str | None], ...]]] = []
        self.current: list[str] | None = None
        self.css: list[str] = []

    def handle_decl(self, decl: str) -> None:
        if decl.lower() != "doctype html" or self.stack:
            self.active = True
        self.signature.append(("decl", decl.lower(), ()))

    def handle_comment(self, data: str) -> None:
        self.active = True

    def handle_pi(self, data: str) -> None:
        self.active = True

    def unknown_decl(self, data: str) -> None:
        self.active = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent = self.stack[-1] if self.stack else None
        if tag not in self._parents or parent not in self._parents.get(tag, set()):
            self.active = True
        allowed = self._attributes.get(tag, set())
        if len(dict(attrs)) != len(attrs) or any(
            name not in allowed or value is None for name, value in attrs
        ):
            self.active = True
        if tag == "meta" and dict(attrs) != {"charset": "utf-8"}:
            self.active = True
        if tag == "a":
            self.links.append(dict(attrs).get("href"))
        self.signature.append(
            ("start", tag, tuple(sorted(attrs, key=lambda item: item[0])))
        )
        if tag != "meta":
            self.stack.append(tag)
        if tag == "style":
            self.css = []
        if tag in {"h2", "p", "dt", "dd", "th", "td"}:
            self.current = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.active = True

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.active = True
            return
        self.stack.pop()
        self.signature.append(("end", tag, ()))
        if tag == "style":
            try:
                validate_stylesheet_v2("".join(self.css))
            except FactShapeInvalid:
                self.active = True
        if tag in {"h2", "p", "dt", "dd", "th", "td"} and self.current is not None:
            self.values.append("".join(self.current))
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] == "style":
            self.css.append(data)
        elif self.current is not None:
            self.current.append(data)
        elif data.strip():
            self.active = True


def result_contract_violations_v2(
    renderer: DocumentRendererV2,
    *,
    projection: DocumentProjectionV2,
    request: RenderRequestV2,
    result: RenderedDocumentV2,
) -> tuple[str, ...]:
    problems: list[str] = []
    if result.outcome is not RenderOutcome.RENDERED or not isinstance(
        result.payload, bytes
    ):
        return ("renderer did not return complete bytes",)
    if (
        result.projection != projection
        or result.projection_digest != projection.digest
        or projection.digest != projection_digest_v2(projection)
    ):
        problems.append("renderer changed projection or returned a stale digest")
    expected = {
        "contract_version": 2,
        "invoice_id": request.fact.invoice_id,
        "source_fact_version": request.fact.fact_version,
        "persisted_fact_id": request.fact.persisted_fact_id,
        "original_presentation_model_digest": (
            request.fact.original_presentation_model_digest
        ),
        "source_fact_fingerprint": fact_fingerprint_v2(request.fact),
        "projection_contract_version": 2,
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
        parser = _Text()
        try:
            parser.feed(result.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            problems.append("renderer returned invalid HTML text")
        else:
            if parser.values != list(projected_text_v2(projection)):
                problems.append("HTML omitted, changed or reordered projected content")
            expected_parser = _Text()
            expected_parser.feed(html_from_projection_v2(projection))
            if parser.signature != expected_parser.signature or parser.stack:
                problems.append("HTML changed the bounded invoice markup structure")
            if parser.active:
                problems.append(
                    "HTML contains content outside the passive markup grammar"
                )
    elif request.media_type == "application/pdf":
        problems.extend(pdf_contract_violations_v2(result.payload, projection))
    return tuple(problems)


def renderer_contract_violations_v2(
    renderer: DocumentRendererV2,
    *,
    projection: DocumentProjectionV2,
    request: RenderRequestV2,
) -> tuple[str, ...]:
    problems: list[str] = []
    if (
        request.media_type not in renderer.media_types()
        or 2 not in renderer.accepts_contract_versions()
        or renderer.projection_contract_version() != 2
    ):
        problems.append("renderer does not declare requested V2 contracts")
    results: list[RenderedDocumentV2] = []
    for run in range(2):
        try:
            result = renderer.render(projection, request)
            results.append(result)
            problems.extend(
                result_contract_violations_v2(
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


__all__ = ["renderer_contract_violations_v2", "result_contract_violations_v2"]
