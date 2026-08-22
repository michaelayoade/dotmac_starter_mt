"""Reusable contract checks for any installed ``DocumentRenderer``."""

from __future__ import annotations

import hashlib
from html import escape

from dotmac_document_rendering.contracts import (
    DocumentProjectionV1,
    DocumentRenderer,
    RenderedDocumentV1,
    RenderOutcome,
    RenderRequestV1,
    StaticText,
    Table,
)
from dotmac_document_rendering.projection import (
    canonical_projection_payload,
    fact_fingerprint,
    projection_digest,
)


def _html_correspondence(
    projection: DocumentProjectionV1, payload: bytes
) -> tuple[str, ...]:
    try:
        html = payload.decode("utf-8")
    except UnicodeDecodeError:
        return ("text/html payload is not UTF-8",)
    problems: list[str] = []
    for section in projection.sections:
        for block_index, block in enumerate(section.blocks):
            if isinstance(block, StaticText) and escape(block.text) not in html:
                problems.append(f"static text {block.key!r} is absent from HTML")
            for value in block.rendered_values():
                if escape(value.text) not in html:
                    problems.append(
                        "projected value from "
                        f"{value.source_field!r} is absent from HTML"
                    )
            if isinstance(block, Table):
                marker = (
                    f'<table data-table="{escape(f"{section.key}:{block_index}")}">'
                )
                start = html.find(marker)
                end = html.find("</table>", start + len(marker))
                table_html = html[start:end] if start >= 0 and end >= 0 else ""
                expected_rows = len(block.rows)
                if table_html.count('<tr data-row="') != expected_rows:
                    problems.append(
                        "HTML row count differs from projection table "
                        f"({expected_rows})"
                    )
    return tuple(problems)


def renderer_contract_violations(
    renderer: DocumentRenderer,
    *,
    projection: DocumentProjectionV1,
    request: RenderRequestV1,
) -> tuple[str, ...]:
    """Return every renderer-contract violation; empty is a pass.

    Consumers call this from their parametrized test suite.  It deliberately
    checks a layout-only output as byte integrity, not semantic drift: payload
    bytes may change, while the returned projection must remain exact.
    """

    problems: list[str] = []
    results: list[RenderedDocumentV1] = []
    for run in range(2):
        try:
            results.append(renderer.render(projection, request))
        except Exception as exc:  # a conformance report must name the adapter failure
            problems.append(f"render run {run + 1} raised {type(exc).__name__}: {exc}")
    if len(results) != 2:
        return tuple(problems)

    for result in results:
        if result.outcome is not RenderOutcome.RENDERED or result.payload is None:
            problems.append("renderer did not return a complete rendered outcome")
            continue
        if result.projection is None:
            problems.append("rendered outcome carries no semantic projection")
            continue
        if canonical_projection_payload(
            result.projection
        ) != canonical_projection_payload(projection):
            problems.append("renderer changed the semantic projection")
        if result.projection.digest != projection.digest:
            problems.append("renderer changed the semantic projection digest")
        if result.projection.digest != projection_digest(result.projection):
            problems.append("renderer returned a projection with a stale digest")
        expected_checksum = f"sha256:{hashlib.sha256(result.payload).hexdigest()}"
        if result.byte_length != len(result.payload):
            problems.append("renderer byte_length does not match its payload")
        if result.checksum_sha256 != expected_checksum:
            problems.append("renderer checksum does not match its payload")
        if result.media_type != request.media_type:
            problems.append("renderer media type does not match the request")
        if (
            result.renderer_code != renderer.code
            or result.renderer_version != renderer.version
        ):
            problems.append("renderer provenance does not match the bound renderer")
        if (
            result.invoice_id != request.fact.invoice_id
            or result.source_fact_version != request.fact.fact_version
            or result.source_fact_fingerprint != fact_fingerprint(request.fact)
        ):
            problems.append("renderer result does not match the source fact")
        if (
            result.template_code != projection.template_code
            or result.template_version != projection.template_version
            or result.projection_contract_version
            != projection.projection_contract_version
            or result.projection_digest != projection.digest
        ):
            problems.append("renderer result does not match projection provenance")
        if (
            result.rendered_at != request.rendered_at
            or result.scope != request.fact.scope
            or result.idempotency_key != request.idempotency_key
            or result.request_fingerprint != request.request_fingerprint
            or result.correlation_id != request.correlation_id
        ):
            problems.append("renderer result does not echo request provenance")
        if request.media_type == "text/html":
            problems.extend(_html_correspondence(result.projection, result.payload))

    if results[0].projection_digest != results[1].projection_digest:
        problems.append("two runs produced different semantic projection digests")
    if request.media_type not in renderer.media_types():
        problems.append("renderer does not declare the media type it produced")
    if request.fact.contract_version not in renderer.accepts_contract_versions():
        problems.append("renderer does not declare the fact version it rendered")
    if renderer.projection_contract_version() != projection.projection_contract_version:
        problems.append("renderer declares a different projection contract version")
    return tuple(dict.fromkeys(problems))


__all__ = ["renderer_contract_violations"]
