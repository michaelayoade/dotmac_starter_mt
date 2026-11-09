"""Reusable data-only refusal conformance kit for V3 renderers and PDF engines.

``renderer_refusal_violations_v3`` and ``engine_refusal_violations_v3`` are the
kit any renderer or PDF engine must pass before an assembly composes it. The
kit performs no I/O itself: the caller supplies a ``FetchSpy`` that observes
file, network and name-resolution attempts in the process under test. An empty
result is the pass. Passing here is necessary, not sufficient: a real engine
composed later must pass this kit inside its own sandbox (process, network and
filesystem confinement), observed where it could actually fetch; its declared
``data-only`` attribute proves nothing by itself. A spy only sees the current
process; an engine that
delegates to a subprocess or service must be observed at that boundary too.
"""

from __future__ import annotations

import hashlib
import struct
from contextlib import AbstractContextManager, suppress
from dataclasses import replace
from html import escape
from typing import Protocol

from dotmac_document_rendering.contracts import (
    DocumentRenderingError,
    RenderErrorCode,
    RenderOutcome,
)
from dotmac_document_rendering.contracts_v3 import (
    DATA_ONLY_RESOURCE_POLICY,
    LOGO_MAX_BYTES,
    LOGO_MAX_DIMENSION,
    DocumentRendererV3,
    EmbeddedLogoAssetV3,
    PdfEngineV3,
    RenderedDocumentV3,
    RenderRequestV3,
)
from dotmac_document_rendering.output_validation_v3 import (
    html_data_only_violations_v3,
)
from dotmac_document_rendering.projection_v3 import project_invoice_v3
from dotmac_document_rendering.service_v3 import render_document_v3
from dotmac_document_rendering.templates_v2 import TemplateCatalogV2

HOSTILE_LEGAL_NAME = "</style><img src=http://x>"


class FetchSpy(Protocol):
    """Observes resource fetches (file, socket, name resolution) while active."""

    def watching(self) -> AbstractContextManager[None]: ...

    def fetches(self) -> tuple[str, ...]: ...


def _asset_for(
    base: EmbeddedLogoAssetV3, media_type: str, content: bytes
) -> EmbeddedLogoAssetV3:
    return EmbeddedLogoAssetV3(
        base.file_id, media_type, hashlib.sha256(content).hexdigest(), len(content)
    )


def engine_refusal_violations_v3(
    engine: PdfEngineV3, *, spy: FetchSpy, canary_file_url: str
) -> tuple[str, ...]:
    """A PDF engine must declare the policy and fetch nothing from hostile HTML.

    ``canary_file_url`` is a ``file://`` URL to a throwaway file the caller
    created, so a read is observable. The engine may refuse the HTML or render
    it; it must not fetch any referenced resource.
    """
    problems: list[str] = []
    if getattr(engine, "resource_policy", None) != DATA_ONLY_RESOURCE_POLICY:
        problems.append("engine does not declare the data-only resource policy")
    hostile = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="http://resource.invalid/a.css">'
        "<style>body{background-color:#fff}</style></head><body>"
        f'<img alt="" src="{escape(canary_file_url, quote=True)}">'
        '<img alt="" src="http://resource.invalid/pixel.png">'
        '<iframe src="http://resource.invalid/frame"></iframe>'
        "<p>engine probe</p></body></html>"
    )
    with spy.watching():
        # Refusing hostile input is a pass; only fetching is a failure.
        with suppress(Exception):
            engine.render_pdf(hostile, deadline_seconds=5.0)
    if spy.fetches():
        problems.append("engine fetched a resource while rendering hostile HTML")
    return tuple(problems)


def renderer_refusal_violations_v3(
    renderer: DocumentRendererV3,
    *,
    catalog: TemplateCatalogV2,
    request: RenderRequestV3,
    spy: FetchSpy,
) -> tuple[str, ...]:
    """Run the V3 refusal and determinism checks against one composed renderer.

    ``request`` must be a valid logo-bearing request for ``renderer``'s media
    type. Checks: zero fetches; hostile names inert; oversize, wrong-type and
    digest-mismatched logos refused with no payload; tampered bytes refused by
    the renderer itself; two renders of one fact are byte-identical.
    """
    fact = request.fact
    logo = fact.brand_logo
    content = request.logo_content
    if logo is None or content is None:
        return ("the kit needs a request carrying a frozen logo and its bytes",)
    problems: list[str] = []

    def run(label: str, candidate: RenderRequestV3) -> RenderedDocumentV3 | None:
        with spy.watching():
            try:
                result = render_document_v3(
                    candidate, catalog=catalog, renderer=renderer
                )
            except Exception as exc:
                problems.append(f"{label}: raised {type(exc).__name__}")
                return None
        if spy.fetches():
            problems.append(f"{label}: a resource was fetched")
        return result

    def expect_refused(label: str, candidate: RenderRequestV3) -> None:
        result = run(label, candidate)
        if result is None:
            return
        if (
            result.outcome is not RenderOutcome.REFUSED
            or result.payload is not None
            or result.error_code is not RenderErrorCode.FACT_SHAPE_INVALID
        ):
            problems.append(f"{label}: was not refused with no payload")

    # Hostile text stays text.
    hostile_fact = replace(
        fact,
        seller_snapshot=replace(fact.seller_snapshot, legal_name=HOSTILE_LEGAL_NAME),
        customer_snapshot=replace(
            fact.customer_snapshot, legal_name=HOSTILE_LEGAL_NAME
        ),
    )
    hostile = run("hostile legal_name", replace(request, fact=hostile_fact))
    if hostile is not None:
        if hostile.outcome is not RenderOutcome.RENDERED or hostile.payload is None:
            problems.append("hostile legal_name: was not rendered")
        elif request.media_type == "text/html":
            text = hostile.payload.decode("utf-8", errors="replace")
            if escape(HOSTILE_LEGAL_NAME) not in text:
                problems.append("hostile legal_name: not escaped as text")
            if text.count("<img") != 1:
                problems.append("hostile legal_name: injected an element")
            problems.extend(html_data_only_violations_v3(text))

    # Refused assets.
    png = b"\x89PNG\r\n\x1a\n"
    jpeg = b"\xff\xd8\xff"
    ihdr = b"\x00\x00\x00\rIHDR"
    big = LOGO_MAX_DIMENSION + 1
    huge_png = png + ihdr + struct.pack(">II", big, 16) + b"\x08\x02\x00\x00\x00"
    zero_png = png + ihdr + struct.pack(">II", 0, 16) + b"\x08\x02\x00\x00\x00"
    bare_png = png + b"\x00" * 16
    huge_jpeg = (
        jpeg + b"\xc0\x00\x0b\x08" + struct.pack(">HH", 16, big) + b"\x01\x01\x11\x00"
    )
    bare_jpeg = jpeg + b"\x00" * 16
    oversize = png + b"\x00" * (LOGO_MAX_BYTES + 1 - len(png))
    cases: tuple[tuple[str, EmbeddedLogoAssetV3, bytes], ...] = (
        ("oversize logo", _asset_for(logo, "image/png", oversize), oversize),
        (
            "content longer than its frozen length",
            logo,
            content + b"\x00",
        ),
        (
            "wrong type: png declared, jpeg bytes",
            _asset_for(logo, "image/png", jpeg + b"\x00" * 16),
            jpeg + b"\x00" * 16,
        ),
        (
            "wrong type: svg",
            _asset_for(logo, "image/svg+xml", b"<svg/>"),
            b"<svg/>",
        ),
        (
            "wrong type: gif",
            _asset_for(logo, "image/gif", b"GIF89a" + b"\x00" * 16),
            b"GIF89a" + b"\x00" * 16,
        ),
        (
            "oversize PNG dimensions",
            _asset_for(logo, "image/png", huge_png),
            huge_png,
        ),
        ("zero PNG dimension", _asset_for(logo, "image/png", zero_png), zero_png),
        ("malformed PNG header", _asset_for(logo, "image/png", bare_png), bare_png),
        (
            "oversize JPEG dimensions",
            _asset_for(logo, "image/jpeg", huge_jpeg),
            huge_jpeg,
        ),
        (
            "malformed JPEG header",
            _asset_for(logo, "image/jpeg", bare_jpeg),
            bare_jpeg,
        ),
        (
            "digest mismatch",
            logo,
            content[:-1] + bytes([content[-1] ^ 0xFF]),
        ),
    )
    for label, asset, data in cases:
        expect_refused(
            label,
            replace(request, fact=replace(fact, brand_logo=asset), logo_content=data),
        )
    expect_refused("missing logo bytes", replace(request, logo_content=None))

    # The renderer itself must also refuse tampered bytes.
    try:
        selected = catalog.select(
            document_profile_code=fact.document_profile_code,
            document_profile_version=fact.document_profile_version,
            media_type=request.media_type,
        )
        projection = project_invoice_v3(
            fact,
            selected,
            renderer_code=renderer.code,
            renderer_version=renderer.version,
            media_type=request.media_type,
        )
        tampered = replace(
            request, logo_content=content[:-1] + bytes([content[-1] ^ 0xFF])
        )
        accepted = False
        with spy.watching():
            try:
                renderer.render(projection, tampered)
                accepted = True
            except DocumentRenderingError:
                accepted = False
        if accepted:
            problems.append("renderer accepted digest-mismatched logo bytes")
        if spy.fetches():
            problems.append("renderer fetched a resource while refusing")
    except DocumentRenderingError as exc:
        problems.append(f"direct renderer setup refused: {exc}")

    # Determinism.
    first = run("determinism run 1", request)
    second = run("determinism run 2", request)
    if first is None or second is None:
        problems.append("determinism: a baseline render did not complete")
    elif first.payload is None or first.payload != second.payload:
        problems.append("determinism: two renders produced different bytes")
    elif first.checksum_sha256 != second.checksum_sha256:
        problems.append("determinism: two renders produced different checksums")
    return tuple(dict.fromkeys(problems))


__all__ = [
    "HOSTILE_LEGAL_NAME",
    "FetchSpy",
    "engine_refusal_violations_v3",
    "renderer_refusal_violations_v3",
]
