"""Pure HTML renderer and PDF engine adapter; neither owns storage or retry."""

from __future__ import annotations

import hashlib
from html import escape

from dotmac_document_rendering.contracts import (
    DocumentProjectionV1,
    EngineTimeout,
    EngineUnavailable,
    FactShapeInvalid,
    FactVersionUnsupported,
    LabelledValue,
    MediaTypeUnsupported,
    PdfEngine,
    RenderedDocumentV1,
    RenderOutcome,
    RenderRequestV1,
    StaticText,
    Table,
)
from dotmac_document_rendering.projection import (
    PROJECTION_CONTRACT_VERSION,
    fact_fingerprint,
)

_DEFAULT_STYLESHEET = """
body { color: #111827; font-family: sans-serif; line-height: 1.4; }
section { margin-block: 1rem; }
h2 { font-size: 1rem; margin: 0 0 .5rem; }
dl { display: grid; grid-template-columns: 12rem 1fr; gap: .25rem .75rem; }
dt { font-weight: 600; }
dd { margin: 0; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #d1d5db; padding: .35rem; text-align: left; }
""".strip()


def _validate_stylesheet(stylesheet: str) -> None:
    lowered = stylesheet.lower()
    forbidden = ("</style", "<script", "@import", "@font-face", "url(")
    if any(token in lowered for token in forbidden):
        raise FactShapeInvalid(
            "renderer stylesheet must be self-contained and contain no active content"
        )


def html_from_projection(
    projection: DocumentProjectionV1,
    *,
    stylesheet: str = _DEFAULT_STYLESHEET,
) -> str:
    """Serialize the semantic projection without making presentation decisions."""

    _validate_stylesheet(stylesheet)
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        f"<style>{stylesheet}</style>",
        "</head><body>",
    ]
    for section in projection.sections:
        parts.append(f'<section data-section="{escape(section.key, quote=True)}">')
        labelled: list[LabelledValue] = []
        for block_index, block in enumerate(section.blocks):
            if isinstance(block, StaticText):
                level = "h2" if block.key == "section_title" else "p"
                parts.append(f"<{level}>{escape(block.text)}</{level}>")
            elif isinstance(block, LabelledValue):
                labelled.append(block)
            elif isinstance(block, Table):
                table_key = escape(
                    f"{section.key}:{block_index}",
                    quote=True,
                )
                parts.append(f'<table data-table="{table_key}"><thead><tr>')
                parts.extend(
                    f"<th>{escape(column.label_text)}</th>" for column in block.columns
                )
                parts.append("</tr></thead><tbody>")
                for row_index, row in enumerate(block.rows):
                    parts.append(f'<tr data-row="{row_index}">')
                    parts.extend(f"<td>{escape(value.text)}</td>" for value in row)
                    parts.append("</tr>")
                parts.append("</tbody></table>")
        if labelled:
            parts.append("<dl>")
            for block in labelled:
                parts.append(
                    f"<dt>{escape(block.label_text)}</dt>"
                    f"<dd>{escape(block.value.text)}</dd>"
                )
            parts.append("</dl>")
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def _success(
    *,
    projection: DocumentProjectionV1,
    request: RenderRequestV1,
    renderer_code: str,
    renderer_version: str,
    payload: bytes,
) -> RenderedDocumentV1:
    return RenderedDocumentV1(
        contract_version=1,
        invoice_id=request.fact.invoice_id,
        source_fact_version=request.fact.fact_version,
        source_fact_fingerprint=fact_fingerprint(request.fact),
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
    projection: DocumentProjectionV1,
    request: RenderRequestV1,
    code: str,
    version: str,
    media_types: frozenset[str],
) -> None:
    if request.fact.contract_version != 1:
        raise FactVersionUnsupported(
            f"fact contract version {request.fact.contract_version} is not accepted"
        )
    if request.media_type not in media_types:
        raise MediaTypeUnsupported(
            f"media type {request.media_type!r} is not supported"
        )
    if projection.projection_contract_version != PROJECTION_CONTRACT_VERSION:
        raise FactShapeInvalid(
            "projection contract version does not match the renderer"
        )
    if projection.media_type != request.media_type:
        raise FactShapeInvalid("projection media type does not match the request")
    if projection.renderer_code != code or projection.renderer_version != version:
        raise FactShapeInvalid(
            "projection renderer provenance does not match the renderer"
        )
    if (
        projection.invoice_id != request.fact.invoice_id
        or projection.fact_version != request.fact.fact_version
    ):
        raise FactShapeInvalid("projection source identity does not match the fact")


class DeterministicHtmlRenderer:
    """Provider-free renderer used by products, CI, and HTML output paths."""

    code = "dotmac-html"
    version = "1"

    def __init__(self, *, stylesheet: str = _DEFAULT_STYLESHEET) -> None:
        _validate_stylesheet(stylesheet)
        self._stylesheet = stylesheet

    def media_types(self) -> frozenset[str]:
        return frozenset({"text/html"})

    def accepts_contract_versions(self) -> frozenset[int]:
        return frozenset({1})

    def projection_contract_version(self) -> int:
        return PROJECTION_CONTRACT_VERSION

    def render(
        self,
        projection: DocumentProjectionV1,
        request: RenderRequestV1,
    ) -> RenderedDocumentV1:
        _validate_call(
            projection=projection,
            request=request,
            code=self.code,
            version=self.version,
            media_types=self.media_types(),
        )
        payload = html_from_projection(projection, stylesheet=self._stylesheet).encode(
            "utf-8"
        )
        return _success(
            projection=projection,
            request=request,
            renderer_code=self.code,
            renderer_version=self.version,
            payload=payload,
        )


class InMemoryDocumentRenderer(DeterministicHtmlRenderer):
    """Credential-free renderer fake for product development and contract tests."""

    code = "dotmac-memory-html"
    version = "1"


class PdfDocumentRenderer:
    """Small adapter around one explicitly installed local PDF engine."""

    def __init__(
        self,
        engine: PdfEngine,
        *,
        stylesheet: str = _DEFAULT_STYLESHEET,
    ) -> None:
        _validate_stylesheet(stylesheet)
        self._engine = engine
        self._stylesheet = stylesheet
        self.code = f"pdf:{engine.code}"
        self.version = engine.version

    def media_types(self) -> frozenset[str]:
        return frozenset({"application/pdf"})

    def accepts_contract_versions(self) -> frozenset[int]:
        return frozenset({1})

    def projection_contract_version(self) -> int:
        return PROJECTION_CONTRACT_VERSION

    def render(
        self,
        projection: DocumentProjectionV1,
        request: RenderRequestV1,
    ) -> RenderedDocumentV1:
        _validate_call(
            projection=projection,
            request=request,
            code=self.code,
            version=self.version,
            media_types=self.media_types(),
        )
        html = html_from_projection(projection, stylesheet=self._stylesheet)
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
        if not isinstance(payload, bytes) or not payload.startswith(b"%PDF-"):
            raise EngineUnavailable(
                f"PDF engine {self._engine.code!r} returned invalid PDF bytes"
            )
        return _success(
            projection=projection,
            request=request,
            renderer_code=self.code,
            renderer_version=self.version,
            payload=payload,
        )


__all__ = [
    "DeterministicHtmlRenderer",
    "InMemoryDocumentRenderer",
    "PdfDocumentRenderer",
    "html_from_projection",
]
