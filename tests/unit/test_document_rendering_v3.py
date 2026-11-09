"""Hosted-only V3 tests: embedded data-only logo, refusals and the engine kit."""

import hashlib
import urllib.request
from dataclasses import fields, replace
from html.parser import HTMLParser

import pytest
from dotmac_document_rendering import (
    DATA_ONLY_RESOURCE_POLICY,
    HOSTILE_LEGAL_NAME,
    LOGO_MAX_BYTES,
    DeterministicHtmlRendererV3,
    DocumentProfileBindingV2,
    EmbeddedLogoAssetV3,
    EngineUnavailable,
    FactShapeInvalid,
    FrozenPaymentLinkV2,
    InMemoryDocumentRendererV3,
    InvoiceDocumentFactV3,
    PdfDocumentRendererV3,
    RenderErrorCode,
    RenderOutcome,
    RenderRequestV3,
    data_uri_for_logo_v3,
    engine_refusal_violations_v3,
    fact_fingerprint_v3,
    html_data_only_violations_v3,
    project_invoice_v2,
    project_invoice_v3,
    projection_digest_v3,
    render_document_v3,
    renderer_contract_violations_v3,
    renderer_refusal_violations_v3,
    validate_fact_v3,
    verify_logo_content_v3,
)

from tests.document_rendering_fetch_spy import ProcessFetchSpy
from tests.document_rendering_pdf_fixture import text_pdf
from tests.unit.test_billing_document_rendering_contract import NOW
from tests.unit.test_document_rendering_v2 import catalog, fact

PNG = b"\x89PNG\r\n\x1a\n"
LOGO = PNG + b"synthetic-logo-bytes" * 4


def asset(content: bytes = LOGO, media_type: str = "image/png") -> EmbeddedLogoAssetV3:
    return EmbeddedLogoAssetV3(
        file_id=fact().persisted_fact_id,
        media_type=media_type,
        sha256=hashlib.sha256(content).hexdigest(),
        byte_length=len(content),
    )


def fact_v3(*, logo: bool = True, **overrides) -> InvoiceDocumentFactV3:
    base = fact()
    values = {
        item.name: getattr(base, item.name)
        for item in fields(base)
        if item.name != "brand_asset"
    }
    values["contract_version"] = 3
    values["brand_logo"] = asset() if logo else None
    values.update(overrides)
    return InvoiceDocumentFactV3(**values)


def request_v3(value=None, *, media_type="text/html", logo_content=LOGO, **overrides):
    value = value or fact_v3()
    values = {
        "fact": value,
        "media_type": media_type,
        "rendered_at": NOW,
        "max_bytes": 5_000_000,
        "deadline_seconds": 5.0,
        "idempotency_key": "stored-fact:7:v3",
        "request_fingerprint": "test-request-v3",
        "correlation_id": "render-fact:7",
        "logo_content": logo_content if value.brand_logo is not None else None,
    }
    values.update(overrides)
    return RenderRequestV3(**values)


class _TextCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag in {"h2", "p", "dt", "dd", "th", "td"}:
            self._current = []

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag):
        if tag in {"h2", "p", "dt", "dd", "th", "td"} and self._current is not None:
            self.values.append("".join(self._current))
            self._current = None


class TextPdfEngine:
    """A data-only fake engine: visible text only, no resource of any kind."""

    code = "text-pdf"
    version = "1"
    resource_policy = DATA_ONLY_RESOURCE_POLICY

    def render_pdf(self, html, *, deadline_seconds):
        collector = _TextCollector()
        collector.feed(html)
        return text_pdf(collector.values)


def pdf_catalog(value=None):
    return catalog(value or fact_v3(), media_type="application/pdf")


def test_renders_one_verified_data_image_and_no_other_url():
    value = fact_v3()
    result = render_document_v3(
        request_v3(value),
        catalog=catalog(value),
        renderer=DeterministicHtmlRendererV3(),
    )
    assert result.outcome is RenderOutcome.RENDERED
    html = result.payload.decode()
    assert html.count("<img") == 1
    assert data_uri_for_logo_v3(value.brand_logo, LOGO) in html
    assert html_data_only_violations_v3(html) == ()
    assert result.contract_version == 3
    assert result.projection.brand_logo == value.brand_logo


def test_fact_without_logo_renders_no_image():
    value = fact_v3(logo=False)
    result = render_document_v3(
        request_v3(value),
        catalog=catalog(value),
        renderer=DeterministicHtmlRendererV3(),
    )
    assert result.outcome is RenderOutcome.RENDERED
    assert "<img" not in result.payload.decode()
    decisions = {d.key: d.outcome for d in result.projection.template_decisions}
    assert decisions["brand_logo"] == "omitted"


def test_v3_reuses_the_v2_projection_of_every_section_unchanged():
    value = fact_v3()
    selected = catalog(value).select(
        document_profile_code=value.document_profile_code,
        document_profile_version=value.document_profile_version,
        media_type="text/html",
    )
    v3 = project_invoice_v3(
        value, selected, renderer_code="r", renderer_version="3", media_type="text/html"
    )
    v2_fact = fact()
    v2 = project_invoice_v2(
        v2_fact,
        selected,
        renderer_code="r",
        renderer_version="2",
        media_type="text/html",
    )
    assert v3.sections == v2.sections
    assert v3.digest == projection_digest_v3(v3)
    assert v3.digest.startswith("cv1:")
    assert v3.digest != v2.digest
    assert v3.source_fact_fingerprint == fact_fingerprint_v3(value)


def test_logo_identity_is_part_of_the_frozen_fingerprint():
    value = fact_v3()
    other = replace(value, brand_logo=asset(LOGO + b"x"))
    assert fact_fingerprint_v3(value) != fact_fingerprint_v3(other)


def test_frozen_payment_link_is_visible_text_never_an_anchor():
    link = FrozenPaymentLinkV2("https://pay.example.test/i/1", "billing", "s1", "1")
    value = fact_v3()
    value = replace(
        value,
        payment_instructions=replace(value.payment_instructions, payment_link=link),
    )
    result = render_document_v3(
        request_v3(value),
        catalog=catalog(value),
        renderer=DeterministicHtmlRendererV3(),
    )
    html = result.payload.decode()
    assert result.outcome is RenderOutcome.RENDERED
    assert "https://pay.example.test/i/1" in html
    assert "<a " not in html and "href" not in html
    assert html_data_only_violations_v3(html) == ()


@pytest.mark.parametrize(
    "bad",
    [
        replace(asset(), media_type="image/gif"),
        replace(asset(), media_type="image/svg+xml"),
        replace(asset(), media_type="IMAGE/PNG"),
        replace(asset(), sha256="A" * 64),
        replace(asset(), sha256="a" * 63),
        replace(asset(), sha256="sha256:" + "a" * 64),
        replace(asset(), byte_length=0),
        replace(asset(), byte_length=LOGO_MAX_BYTES + 1),
        replace(asset(), byte_length=True),
    ],
)
def test_malformed_logo_identity_is_refused(bad):
    with pytest.raises(FactShapeInvalid):
        validate_fact_v3(fact_v3(brand_logo=bad))


@pytest.mark.parametrize(
    "content,declared",
    [
        (PNG + b"x" * 8, replace(asset(PNG + b"x" * 8), media_type="image/jpeg")),
        (b"\xff\xd8\xff" + b"x" * 8, asset(b"\xff\xd8\xff" + b"x" * 8, "image/png")),
        (LOGO[:-1] + b"Z", asset()),
        (LOGO + b"\x00", asset()),
        (PNG + b"\x00" * LOGO_MAX_BYTES, asset(PNG + b"\x00" * LOGO_MAX_BYTES)),
        (bytearray(LOGO), asset()),
    ],
)
def test_logo_bytes_are_verified_against_the_frozen_identity(content, declared):
    with pytest.raises(FactShapeInvalid):
        verify_logo_content_v3(declared, content)


def test_boundary_sized_jpeg_is_accepted():
    content = b"\xff\xd8\xff" + b"\x00" * (LOGO_MAX_BYTES - 3)
    verify_logo_content_v3(asset(content, "image/jpeg"), content)


@pytest.mark.parametrize(
    "renderer_cls", [DeterministicHtmlRendererV3, InMemoryDocumentRendererV3]
)
def test_html_renderers_pass_the_refusal_kit_with_zero_fetches(renderer_cls):
    value = fact_v3()
    problems = renderer_refusal_violations_v3(
        renderer_cls(),
        catalog=catalog(value),
        request=request_v3(value),
        spy=ProcessFetchSpy(),
    )
    assert problems == ()


def test_pdf_renderer_with_a_data_only_engine_passes_the_refusal_kit(tmp_path):
    value = fact_v3()
    renderer = PdfDocumentRendererV3(TextPdfEngine())
    problems = renderer_refusal_violations_v3(
        renderer,
        catalog=pdf_catalog(value),
        request=request_v3(value, media_type="application/pdf"),
        spy=ProcessFetchSpy(),
    )
    assert problems == ()
    canary = tmp_path / "canary.txt"
    canary.write_text("canary", encoding="utf-8")
    assert (
        engine_refusal_violations_v3(
            TextPdfEngine(),
            spy=ProcessFetchSpy(watched_paths=(canary,)),
            canary_file_url=canary.as_uri(),
        )
        == ()
    )


def test_hostile_legal_name_is_inert_text():
    value = fact_v3()
    value = replace(
        value,
        seller_snapshot=replace(value.seller_snapshot, legal_name=HOSTILE_LEGAL_NAME),
    )
    result = render_document_v3(
        request_v3(value),
        catalog=catalog(value),
        renderer=DeterministicHtmlRendererV3(),
    )
    html = result.payload.decode()
    assert result.outcome is RenderOutcome.RENDERED
    assert "&lt;/style&gt;&lt;img src=http://x&gt;" in html
    assert html.count("<img") == 1
    assert html_data_only_violations_v3(html) == ()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: replace(v, brand_logo=asset(LOGO + b"x" * LOGO_MAX_BYTES)),
        lambda v: replace(v, brand_logo=asset(b"\xff\xd8\xff" + b"x" * 8, "image/png")),
        lambda v: replace(v, brand_logo=replace(v.brand_logo, sha256="0" * 64)),
        lambda v: replace(
            v, brand_logo=replace(v.brand_logo, media_type="image/svg+xml")
        ),
    ],
)
def test_service_refuses_invalid_assets_with_no_payload(mutate):
    value = mutate(fact_v3())
    result = render_document_v3(
        request_v3(value, logo_content=LOGO),
        catalog=catalog(value),
        renderer=DeterministicHtmlRendererV3(),
    )
    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_code is RenderErrorCode.FACT_SHAPE_INVALID
    assert result.payload is None


def test_missing_or_unexpected_logo_bytes_are_refused():
    value = fact_v3()
    missing = render_document_v3(
        replace(request_v3(value), logo_content=None),
        catalog=catalog(value),
        renderer=DeterministicHtmlRendererV3(),
    )
    assert missing.outcome is RenderOutcome.REFUSED
    bare = fact_v3(logo=False)
    unexpected = render_document_v3(
        replace(request_v3(bare), logo_content=LOGO),
        catalog=catalog(bare),
        renderer=DeterministicHtmlRendererV3(),
    )
    assert unexpected.outcome is RenderOutcome.REFUSED


def test_two_renders_of_the_same_fact_are_byte_identical():
    value = fact_v3()
    first, second = (
        render_document_v3(
            request_v3(value),
            catalog=catalog(value),
            renderer=DeterministicHtmlRendererV3(),
        )
        for _ in range(2)
    )
    assert first.payload == second.payload
    assert first.checksum_sha256 == second.checksum_sha256


def test_renderer_contract_check_is_clean_for_the_reference_renderer():
    value = fact_v3()
    renderer = DeterministicHtmlRendererV3()
    selected = catalog(value).select(
        document_profile_code=value.document_profile_code,
        document_profile_version=value.document_profile_version,
        media_type="text/html",
    )
    projection = project_invoice_v3(
        value,
        selected,
        renderer_code=renderer.code,
        renderer_version=renderer.version,
        media_type="text/html",
    )
    assert (
        renderer_contract_violations_v3(
            renderer, projection=projection, request=request_v3(value)
        )
        == ()
    )


def test_pdf_adapter_refuses_an_engine_without_the_data_only_policy():
    class Undeclared:
        code = "undeclared"
        version = "1"

        def render_pdf(self, html, *, deadline_seconds):
            return b""

    class Fetching(Undeclared):
        resource_policy = "network-ok"

    for engine in (Undeclared(), Fetching()):
        with pytest.raises(EngineUnavailable):
            PdfDocumentRendererV3(engine)


@pytest.mark.parametrize(
    "markup",
    [
        '<img alt="" src="http://resource.invalid/x.png">',
        '<img alt="" src="file:///etc/hostname">',
        '<img alt="" src="//resource.invalid/x.png">',
        '<img alt="" src="data:image/svg+xml;base64,AAAA">',
        '<img alt="" src="data:text/html;base64,AAAA">',
        '<img alt="" src="data:image/png;base64,AAAA" onerror="x()">',
        '<link rel="stylesheet" href="http://resource.invalid/a.css">',
        '<a href="https://resource.invalid/">x</a>',
        "<script src=http://resource.invalid/a.js></script>",
        '<iframe src="data:text/html,x"></iframe>',
        "<style>p{background-color:red}@import 'x';</style>",
        "<style>p{background-image:url(data:image/png;base64,AAAA)}</style>",
        '<p style="color:red">x</p>',
        "<!-- comment -->",
        '<img alt="" src="data:image/png;base64,AAAA">'
        '<img alt="" src="data:image/png;base64,AAAA">',
    ],
)
def test_data_only_check_flags_every_non_data_reference(markup):
    html = (
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        f"{markup}</body></html>"
    )
    assert html_data_only_violations_v3(html)


def test_data_only_check_accepts_one_data_image():
    html = (
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        '<img alt="" src="data:image/png;base64,AAAA"><p>https://visible.text</p>'
        "</body></html>"
    )
    assert html_data_only_violations_v3(html) == ()


# --- Sensitivity: the kit must go RED against leaking implementations. -------


class LeakyUrllibEngine(TextPdfEngine):
    def render_pdf(self, html, *, deadline_seconds):
        try:
            urllib.request.urlopen("http://resource.invalid/pixel.png")
        except OSError:
            pass
        return super().render_pdf(html, deadline_seconds=deadline_seconds)


class LeakyFileEngine(TextPdfEngine):
    canary = None

    def render_pdf(self, html, *, deadline_seconds):
        try:
            with open(self.canary, "rb") as handle:
                handle.read()
        except OSError:
            pass
        return super().render_pdf(html, deadline_seconds=deadline_seconds)


class LeakySocketEngine(TextPdfEngine):
    def render_pdf(self, html, *, deadline_seconds):
        import socket

        try:
            socket.create_connection(("resource.invalid", 80), timeout=0.1)
        except OSError:
            pass
        return super().render_pdf(html, deadline_seconds=deadline_seconds)


def test_engine_kit_detects_http_file_and_socket_leaks(tmp_path):
    canary = tmp_path / "canary.txt"
    canary.write_text("canary", encoding="utf-8")
    LeakyFileEngine.canary = canary
    for engine in (LeakyUrllibEngine(), LeakyFileEngine(), LeakySocketEngine()):
        problems = engine_refusal_violations_v3(
            engine,
            spy=ProcessFetchSpy(watched_paths=(canary,)),
            canary_file_url=canary.as_uri(),
        )
        assert problems, type(engine).__name__


def test_engine_kit_requires_the_declared_policy():
    class Silent:
        code = "silent"
        version = "1"

        def render_pdf(self, html, *, deadline_seconds):
            raise EngineUnavailable("no")

    assert engine_refusal_violations_v3(
        Silent(), spy=ProcessFetchSpy(), canary_file_url="file:///nonexistent"
    )


class LeakyHtmlRenderer(DeterministicHtmlRendererV3):
    """Emits an unescaped remote image: a V3 renderer the kit must reject."""

    def render(self, projection, request):
        result = super().render(projection, request)
        payload = result.payload.replace(
            b"<body>", b'<body><img alt="" src="http://resource.invalid/p.png">'
        )
        return replace(
            result,
            payload=payload,
            byte_length=len(payload),
            checksum_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        )


def test_renderer_kit_rejects_a_renderer_that_emits_a_remote_image():
    value = fact_v3()
    problems = renderer_refusal_violations_v3(
        LeakyHtmlRenderer(),
        catalog=catalog(value),
        request=request_v3(value),
        spy=ProcessFetchSpy(),
    )
    assert problems


class NonDeterministicRenderer(DeterministicHtmlRendererV3):
    calls = 0

    def render(self, projection, request):
        type(self).calls += 1
        result = super().render(projection, request)
        payload = result.payload + b" " * (type(self).calls % 2)
        return replace(
            result,
            payload=payload,
            byte_length=len(payload),
            checksum_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        )


def test_renderer_kit_detects_non_deterministic_bytes():
    value = fact_v3()
    problems = renderer_refusal_violations_v3(
        NonDeterministicRenderer(),
        catalog=catalog(value),
        request=request_v3(value),
        spy=ProcessFetchSpy(),
    )
    assert any("determinism" in problem or "render" in problem for problem in problems)
    assert DocumentProfileBindingV2  # public V2 binding still exported unchanged
