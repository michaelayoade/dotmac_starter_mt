"""Hosted-only V2 semantic, provenance, rendering and fail-closed canaries."""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta, timezone
from decimal import (
    ROUND_DOWN,
    ROUND_UP,
    Clamped,
    Decimal,
    Inexact,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    getcontext,
    localcontext,
)
from html import escape
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from dotmac_document_rendering import (
    DEFAULT_ENGLISH_LABELS_V2,
    DeterministicHtmlRendererV2,
    DocumentProfileBindingV2,
    DocumentTemplateV1,
    EngineUnavailable,
    FactShapeInvalid,
    FrozenPaymentLinkV2,
    InMemoryDocumentRendererV2,
    LabelText,
    PdfDocumentRendererV2,
    RenderErrorClass,
    RenderErrorCode,
    RenderOutcome,
    RenderRequestV2,
    TemplateCatalogV2,
    canonical_projection_payload_v2,
    fact_fingerprint_v2,
    project_invoice_v2,
    projection_digest_v2,
    render_document_v2,
    renderer_contract_violations_v2,
)
from dotmac_kernel.semantic_encoding import digest_of

from tests.document_rendering_fact_adapter import map_stored_fact
from tests.document_rendering_pdf_fixture import display_text, text_pdf
from tests.unit.test_billing_document_rendering_contract import (
    NOW,
    money,
    projected_values,
    stored_fact,
)

GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "fixtures/document_rendering/invoice_projection_v2.json"
)


def fact():
    return map_stored_fact(stored_fact())


def catalog(value=None, *, media_type="text/html"):
    value = value or fact()
    return TemplateCatalogV2(
        templates=(
            DocumentTemplateV1(
                contract_version=1,
                template_code="invoice-en",
                template_version="2",
                language_tag="en",
                labels=tuple(
                    LabelText(key, text) for key, text in DEFAULT_ENGLISH_LABELS_V2
                ),
                date_format_code="%Y-%m-%d",
                datetime_format_code="%Y-%m-%d %H:%M %Z",
            ),
        ),
        bindings=(
            DocumentProfileBindingV2(
                value.document_profile_code,
                value.document_profile_version,
                media_type,
                "invoice-en",
                "2",
            ),
        ),
        assembly_file="tests/document_rendering_fact_adapter.py",
    )


def request(value=None, **overrides):
    values = {
        "fact": value or fact(),
        "media_type": "text/html",
        "rendered_at": NOW,
        "max_bytes": 1_000_000,
        "deadline_seconds": 5.0,
        "idempotency_key": "stored-fact:7:html",
        "request_fingerprint": "test-request",
        "correlation_id": "render-fact:7",
    }
    values.update(overrides)
    return RenderRequestV2(**values)


def projection(value=None, renderer=None):
    value = value or fact()
    renderer = renderer or DeterministicHtmlRendererV2()
    selected = catalog(value).select(
        document_profile_code=value.document_profile_code,
        document_profile_version=value.document_profile_version,
        media_type="text/html",
    )
    return project_invoice_v2(
        value,
        selected,
        renderer_code=renderer.code,
        renderer_version=renderer.version,
        media_type="text/html",
    )


def test_reviewed_v2_semantic_golden_covers_complete_producer_shape():
    value = fact()
    projected = projection(value)
    golden = json.loads(GOLDEN.read_text())
    values = projected_values(projected)
    for path, raw in golden["raw_fields"].items():
        assert values[path].raw == raw, path
    decisions = {item.key: item.outcome for item in projected.template_decisions}
    for path in golden["absent_fields"]:
        assert decisions[path] == "absent", path
        assert path not in values
    for path in golden["omitted_fields"]:
        assert decisions[path] == "omitted", path
        assert path not in values
    assert [values[f"lines[{i}].line_number"].raw for i in range(9)] == [
        str(i) for i in range(1, 10)
    ]
    assert values["issued_at"].text == "2026-10-13"
    assert values["fx_snapshot.observed_at"].text == "2026-10-13 17:00 WAT"
    assert values["issued_at"].timezone == "Africa/Lagos"
    assert projected.digest.startswith("cv1:")
    assert projected.source_fact_fingerprint.startswith("cv1:")
    assert projected.digest != projected.source_fact_fingerprint
    assert value.original_presentation_model_digest == "a" * 64
    assert canonical_projection_payload_v2(
        projected
    ) == canonical_projection_payload_v2(projection(value))
    assert projected.digest == projection_digest_v2(projected)
    assert projected.digest != digest_of(
        "dotmac-document-rendering.fact.v2", canonical_projection_payload_v2(projected)
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: replace(f, grand_total=money("991.00")),
        lambda f: replace(f, subtotal=money("901.00")),
        lambda f: replace(f, tax_total=money("136.00")),
        lambda f: replace(f, recorded_at=f.recorded_at + timedelta(seconds=1)),
        lambda f: replace(f, original_presentation_model_digest="b" * 64),
        lambda f: replace(f, document_number="INV-CHANGED"),
        lambda f: replace(f, document_profile_version="opaque:v8"),
        lambda f: replace(f, series_version="series:2"),
        lambda f: replace(
            f, seller_snapshot=replace(f.seller_snapshot, trading_name="Changed")
        ),
        lambda f: replace(
            f,
            payment_instructions=replace(
                f.payment_instructions, bank_name="Changed Bank"
            ),
        ),
        lambda f: replace(
            f,
            lines=(replace(f.lines[0], price_source_version="price:new"),)
            + f.lines[1:],
        ),
        lambda f: replace(
            f,
            lines=(replace(f.lines[0], pre_tax_amount=money("101.00")),) + f.lines[1:],
        ),
        lambda f: replace(
            f, lines=(replace(f.lines[0], discount_total=money("6.00")),) + f.lines[1:]
        ),
        lambda f: replace(
            f, lines=(replace(f.lines[0], tax_amount=money("16.00")),) + f.lines[1:]
        ),
        lambda f: replace(
            f, lines=(replace(f.lines[0], total_amount=money("111.00")),) + f.lines[1:]
        ),
    ],
)
def test_financial_provenance_and_presentation_mutations_change_both_v2_hashes(mutate):
    original = fact()
    changed = mutate(original)
    assert fact_fingerprint_v2(changed) != fact_fingerprint_v2(original)
    assert projection(changed).digest != projection(original).digest
    assert original.original_presentation_model_digest == "a" * 64


def test_typed_absence_differs_from_empty_text_and_order_is_semantic():
    absent = fact()
    empty = replace(
        absent, payment_instructions=replace(absent.payment_instructions, narrative="")
    )
    none = replace(
        absent,
        payment_instructions=replace(absent.payment_instructions, narrative=None),
    )
    assert fact_fingerprint_v2(empty) != fact_fingerprint_v2(none)
    assert projection(empty).digest != projection(none).digest
    reordered = replace(
        absent, party_tax_identities=tuple(reversed(absent.party_tax_identities))
    )
    assert fact_fingerprint_v2(absent) != fact_fingerprint_v2(reordered)
    assert projection(absent).digest != projection(reordered).digest


@pytest.mark.parametrize(
    "bad",
    [
        lambda f: replace(
            f,
            lines=(
                replace(
                    f.lines[0],
                    recorded_line=replace(
                        f.lines[0].recorded_line, fact_id=UUID(int=9)
                    ),
                ),
            )
            + f.lines[1:],
        ),
        lambda f: replace(
            f,
            lines=(
                replace(
                    f.lines[0],
                    recorded_line=replace(f.lines[0].recorded_line, fact_version=8),
                ),
            )
            + f.lines[1:],
        ),
        lambda f: replace(
            f,
            lines=(
                replace(
                    f.lines[0],
                    recorded_line=replace(f.lines[0].recorded_line, line_number=2),
                ),
            )
            + f.lines[1:],
        ),
        lambda f: replace(f, lines=tuple(reversed(f.lines))),
        lambda f: replace(f, document_profile_version=7),
    ],
)
def test_false_parent_line_qualification_and_nonopaque_profile_fail_closed(bad):
    result = render_document_v2(
        request(bad(fact())), catalog=catalog(), renderer=DeterministicHtmlRendererV2()
    )
    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_class is RenderErrorClass.PERMANENT
    assert result.payload is None


def test_projection_refuses_selected_profile_binding_mismatch():
    value = fact()
    selected = catalog().select(
        document_profile_code=value.document_profile_code,
        document_profile_version=value.document_profile_version,
        media_type="text/html",
    )
    with pytest.raises(FactShapeInvalid):
        project_invoice_v2(
            replace(value, document_profile_version="different"),
            selected,
            renderer_code="dotmac-html",
            renderer_version="2",
            media_type="text/html",
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://pay.example.test/",
        "javascript:alert(1)",
        "https:///pay",
        "https://",
        "https://user:password@pay.example.test/",
        "https://pay.example.test:/",
        "https://pay.example.test:99999/",
        "https://[broken/",
        "https://pay.example.test\\evil/",
        "https://pay.example.test/\nnext",
        "https://pay.example.test/\x00",
        "https://bad%20host/",
        "https://pay.example.test/%0A",
        "https://pay.example.test/%00",
        "https://pay.example.test/%5Cevil",
    ],
)
def test_unqualified_unsafe_or_malformed_payment_urls_are_typed_refusals(url):
    value = fact()
    value = replace(
        value,
        payment_instructions=replace(
            value.payment_instructions,
            payment_link=FrozenPaymentLinkV2(
                url, "future-payment-owner", "snapshot:1", "1"
            ),
        ),
    )
    result = render_document_v2(
        request(value), catalog=catalog(), renderer=DeterministicHtmlRendererV2()
    )
    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_code is RenderErrorCode.FACT_SHAPE_INVALID
    assert result.payload is None


@pytest.mark.parametrize(
    "owner,snapshot,version", [("", "1", "1"), ("owner", "", "1"), ("owner", "1", "")]
)
def test_link_requires_explicit_frozen_owner_identity_and_version(
    owner, snapshot, version
):
    value = fact()
    value = replace(
        value,
        payment_instructions=replace(
            value.payment_instructions,
            payment_link=FrozenPaymentLinkV2(
                "https://pay.example.test/", owner, snapshot, version
            ),
        ),
    )
    assert (
        render_document_v2(
            request(value), catalog=catalog(), renderer=DeterministicHtmlRendererV2()
        ).outcome
        is RenderOutcome.REFUSED
    )


def test_frozen_bank_narrative_no_link_and_future_qualified_link_are_deterministic():
    for linked in (False, True):
        value = fact()
        if linked:
            value = replace(
                value,
                payment_instructions=replace(
                    value.payment_instructions,
                    payment_link=FrozenPaymentLinkV2(
                        'https://pay.example.test/?a=1&b="quoted"',
                        "future-payment-owner",
                        "snapshot:1",
                        "v2",
                    ),
                ),
            )
        first = render_document_v2(
            request(value), catalog=catalog(), renderer=DeterministicHtmlRendererV2()
        )
        second = render_document_v2(
            request(value), catalog=catalog(), renderer=DeterministicHtmlRendererV2()
        )
        assert first == second
        assert first.outcome is RenderOutcome.RENDERED
        assert first.persisted_fact_id == value.persisted_fact_id
        assert (
            first.original_presentation_model_digest
            == value.original_presentation_model_digest
        )
        html = first.payload.decode()
        assert "Frozen Bank" in html
        assert "Use invoice reference &lt;unchanged&gt;" in html
        assert "Seller &lt;Limited&gt;" in html
        assert "<script" not in html
        if linked:
            assert escape(value.payment_instructions.payment_link.url) in html
        else:
            assert "https://pay.example.test" not in html
        assert (
            first.checksum_sha256
            == "sha256:" + hashlib.sha256(first.payload).hexdigest()
        )
        assert first.byte_length == len(first.payload)


class CorruptRenderer(DeterministicHtmlRendererV2):
    def __init__(self, mutation):
        super().__init__()
        self.mutation = mutation

    def render(self, projection, request):
        result = super().render(projection, request)
        if self.mutation == "stale":
            return replace(result, projection_digest="cv1:" + "0" * 64)
        if self.mutation == "provenance":
            return replace(result, source_fact_version=999)
        if self.mutation == "persisted_fact_id":
            return replace(result, persisted_fact_id=UUID(int=999))
        if self.mutation == "original_digest":
            return replace(result, original_presentation_model_digest="b" * 64)
        if self.mutation == "checksum":
            return replace(result, checksum_sha256="sha256:" + "0" * 64)
        injections = {
            "meta_refresh": b'<meta http-equiv="refresh" content="0;url=https://external.example.test/">',
            "video": b'<video src="https://external.example.test/video"></video>',
            "svg": b'<svg><a href="https://external.example.test/">x</a></svg>',
            "css_escape": rb"<style>body{background:\75\72\6c(https://external.example.test/)}</style>",
            "css_import": b"<style>@import/**/url(https://external.example.test/);</style>",
            "css_comment_escape": rb"<style>body{background:/*comment*/\75rl(https://external.example.test/)}</style>",
            "event_handler": b'<div onclick="alert(1)"></div>',
            "unknown_attributes": b'<div data-unknown="external-value"></div>',
            "html_comment": b"<!-- unqualified hidden content -->",
        }
        if self.mutation in injections:
            payload = result.payload.replace(
                b"</body>", injections[self.mutation] + b"</body>"
            )
        elif self.mutation == "active":
            payload = result.payload.replace(
                b"</body>", b"<script>alert(1)</script></body>"
            )
        elif self.mutation == "extra_link":
            payload = result.payload.replace(
                b"</body>", b'<a href="https://unfrozen.example.test/">pay</a></body>'
            )
        elif self.mutation == "shuffle":
            payload = result.payload.replace(b"Service line 1", b"TEMP-LINE")
            payload = payload.replace(b"Service line 9", b"Service line 1")
            payload = payload.replace(b"TEMP-LINE", b"Service line 9")
        else:
            payload = result.payload.replace(b"Service line 9", b"")
        return replace(
            result,
            payload=payload,
            byte_length=len(payload),
            checksum_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "stale",
        "provenance",
        "persisted_fact_id",
        "original_digest",
        "checksum",
        "truncate",
        "shuffle",
        "active",
        "extra_link",
        "meta_refresh",
        "video",
        "svg",
        "css_escape",
        "css_import",
        "css_comment_escape",
        "event_handler",
        "unknown_attributes",
        "html_comment",
    ],
)
def test_malicious_adapter_cannot_return_a_successful_document(mutation):
    renderer = CorruptRenderer(mutation)
    assert renderer_contract_violations_v2(
        renderer, projection=projection(renderer=renderer), request=request()
    )
    result = render_document_v2(request(), catalog=catalog(), renderer=renderer)
    assert result.outcome is RenderOutcome.FAILED
    assert result.error_class is RenderErrorClass.RETRYABLE
    assert result.payload is None


def test_memory_fake_passes_reusable_v2_contract_and_size_limit_refuses_all_bytes():
    renderer = InMemoryDocumentRendererV2()
    assert (
        renderer_contract_violations_v2(
            renderer, projection=projection(renderer=renderer), request=request()
        )
        == ()
    )
    result = render_document_v2(
        request(max_bytes=32), catalog=catalog(), renderer=renderer
    )
    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_code is RenderErrorCode.OUTPUT_TOO_LARGE
    assert result.payload is result.byte_length is result.checksum_sha256 is None


class BrokenPdf:
    code = "broken-pdf"
    version = "1"

    def __init__(self, failure):
        self.failure = failure

    def render_pdf(self, html, *, deadline_seconds):
        assert "Service line 9" in html
        if self.failure == "timeout":
            raise TimeoutError
        if self.failure == "unavailable":
            raise EngineUnavailable("absent PDF engine")
        if self.failure == "incomplete":
            return b"%PDF-1.7\ntruncated"
        return b"not a PDF"


@pytest.mark.parametrize(
    "failure,code",
    [
        ("timeout", RenderErrorCode.ENGINE_TIMEOUT),
        ("unavailable", RenderErrorCode.ENGINE_UNAVAILABLE),
        ("invalid", RenderErrorCode.ENGINE_UNAVAILABLE),
        ("incomplete", RenderErrorCode.ENGINE_UNAVAILABLE),
    ],
)
def test_pdf_failures_are_typed_retryable_and_have_no_fallback(failure, code):
    result = render_document_v2(
        request(media_type="application/pdf"),
        catalog=catalog(media_type="application/pdf"),
        renderer=PdfDocumentRendererV2(BrokenPdf(failure)),
    )
    assert result.outcome is RenderOutcome.FAILED
    assert result.error_class is RenderErrorClass.RETRYABLE
    assert result.error_code is code
    assert result.payload is None


def test_internal_evidence_is_fingerprint_bound_without_customer_visible_leakage():
    value = fact()
    value = replace(
        value,
        due_date_basis=replace(
            value.due_date_basis,
            override_actor="internal-override-actor",
            override_reason="internal-case-reason",
            override_evidence_ref="internal-protected-evidence",
        ),
    )
    result = render_document_v2(
        request(value), catalog=catalog(), renderer=DeterministicHtmlRendererV2()
    )
    assert result.outcome is RenderOutcome.RENDERED
    html = result.payload.decode()
    for internal in (
        str(value.persisted_fact_id),
        str(value.invoice_id),
        str(value.scope.tenant_id),
        value.original_presentation_model_digest,
        value.correlation_id,
        value.lines[0].price_source_version,
        "internal-override-actor",
        "internal-case-reason",
        "internal-protected-evidence",
        value.tax_snapshots[0].policy_id,
        value.fx_snapshot.provenance,
    ):
        assert internal not in html
    assert result.projection_digest != projection(fact()).digest
    assert "Pre-tax amount" in html
    assert "lines[0].pre_tax_amount" not in html


def test_rendering_a_qualified_link_does_not_fetch_any_network_resource(monkeypatch):
    import socket
    import urllib.request

    def forbidden(*args, **kwargs):
        raise AssertionError("renderer tried external I/O")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    value = fact()
    value = replace(
        value,
        payment_instructions=replace(
            value.payment_instructions,
            payment_link=FrozenPaymentLinkV2(
                "https://pay.example.test/frozen",
                "future-payment-owner",
                "snapshot:1",
                "1",
            ),
        ),
    )
    result = render_document_v2(
        request(value), catalog=catalog(), renderer=DeterministicHtmlRendererV2()
    )
    assert result.outcome is RenderOutcome.RENDERED
    assert b'href="https://pay.example.test/frozen"' in result.payload


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: replace(f, grand_total="990.00"),
        lambda f: replace(f, due_at="2026-10-14"),
        lambda f: replace(f, due_at=NOW.replace(tzinfo=None)),
        lambda f: replace(
            f, lines=(replace(f.lines[0], quantity="2.500"),) + f.lines[1:]
        ),
        lambda f: replace(
            f, lines=(replace(f.lines[0], pre_tax_amount="100.00"),) + f.lines[1:]
        ),
        lambda f: replace(
            f, due_date_basis=replace(f.due_date_basis, status="verified")
        ),
    ],
)
def test_noncanonical_financial_and_due_date_shapes_are_permanent_refusals(mutate):
    result = render_document_v2(
        request(mutate(fact())),
        catalog=catalog(),
        renderer=DeterministicHtmlRendererV2(),
    )
    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_code is RenderErrorCode.FACT_SHAPE_INVALID
    assert result.error_class is RenderErrorClass.PERMANENT
    assert result.payload is None


class CompletePdf:
    code = "complete-test-pdf"
    version = "1"

    def __init__(self, texts, mutation=None):
        self.texts = texts
        self.mutation = mutation
        self.payload = None

    def render_pdf(self, html, *, deadline_seconds):
        assert "Service line 9" in html
        assert "Frozen Bank" in html
        texts = list(self.texts)
        if self.mutation == "missing":
            texts.remove("Service line 9")
        elif self.mutation == "reordered":
            left, right = texts.index("Service line 1"), texts.index("Service line 9")
            texts[left], texts[right] = texts[right], texts[left]
        elif self.mutation == "blank":
            texts = []
        payload = text_pdf(
            texts,
            action=self.mutation,
            compressed=self.mutation in {"compressed", "decoded_oversize"},
            padding_bytes=(
                32 * 1024 * 1024 + 1 if self.mutation == "decoded_oversize" else 0
            ),
        )
        if self.mutation == "marker":
            payload = b"%PDF-1.7\nmarker-only\n%%EOF\n"
        elif self.mutation == "truncated":
            payload = payload[: len(payload) // 2] + b"\n%%EOF\n"
        elif self.mutation == "encrypted":
            from pypdf import PdfReader, PdfWriter

            writer = PdfWriter(clone_from=PdfReader(BytesIO(payload), strict=True))
            # Disposable encrypted fixture only; no real material or password custody.
            writer.encrypt("test-only-fixture", algorithm="RC4-128")
            output = BytesIO()
            writer.write(output)
            payload = output.getvalue()
        self.payload = payload
        return payload


def pdf_case(value=None, mutation=None):
    value = value or fact()
    pdf_catalog = catalog(value, media_type="application/pdf")
    selected = pdf_catalog.select(
        document_profile_code=value.document_profile_code,
        document_profile_version=value.document_profile_version,
        media_type="application/pdf",
    )
    projected = project_invoice_v2(
        value,
        selected,
        renderer_code="pdf-v2:complete-test-pdf",
        renderer_version="1",
        media_type="application/pdf",
    )
    engine = CompletePdf(display_text(projected), mutation)
    renderer = PdfDocumentRendererV2(engine)
    return engine, renderer, pdf_catalog


@pytest.mark.parametrize("compression", [None, "compressed"])
def test_v2_pdf_output_binds_complete_engine_bytes_and_provenance(compression):
    from pypdf import PdfReader

    engine, renderer, pdf_catalog = pdf_case(mutation=compression)
    result = render_document_v2(
        request(media_type="application/pdf"), catalog=pdf_catalog, renderer=renderer
    )
    assert result.outcome is RenderOutcome.RENDERED
    assert result.payload == engine.payload
    assert result.byte_length == len(result.payload)
    assert (
        result.checksum_sha256 == "sha256:" + hashlib.sha256(result.payload).hexdigest()
    )
    assert result.renderer_code == "pdf-v2:complete-test-pdf"
    assert result.renderer_version == "1"
    assert result.persisted_fact_id == fact().persisted_fact_id
    assert (
        result.original_presentation_model_digest
        == fact().original_presentation_model_digest
    )
    parsed = PdfReader(BytesIO(result.payload), strict=True)
    assert len(parsed.pages) > 1
    text = " ".join(" ".join(page.extract_text().split()) for page in parsed.pages)
    golden = json.loads(GOLDEN.read_text())
    assert all(f"Service line {number}" in text for number in range(1, 10))
    assert (
        golden["raw_fields"][
            "seller_snapshot.registered_identifiers[1].identifier_value"
        ]
        in text
    )
    assert "NGN " + golden["raw_fields"]["grand_total"] in text


@pytest.mark.parametrize(
    "mutation",
    [
        "marker",
        "truncated",
        "missing",
        "reordered",
        "blank",
        "encrypted",
        "uri",
        "javascript",
        "decoded_oversize",
    ],
)
def test_pdf_structural_or_content_failures_never_return_rendered(mutation):
    engine, renderer, pdf_catalog = pdf_case(mutation=mutation)
    result = render_document_v2(
        request(media_type="application/pdf"), catalog=pdf_catalog, renderer=renderer
    )
    assert engine.payload is not None
    assert result.outcome is RenderOutcome.FAILED
    assert result.error_code is RenderErrorCode.ENGINE_UNAVAILABLE
    assert result.error_class is RenderErrorClass.RETRYABLE
    assert result.payload is result.byte_length is result.checksum_sha256 is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: replace(
            f,
            customer_snapshot=replace(
                f.customer_snapshot,
                address=replace(f.customer_snapshot.address, city={"untyped": "city"}),
            ),
        ),
        lambda f: replace(
            f,
            payment_instructions=replace(
                f.payment_instructions, narrative={"untyped": "narrative"}
            ),
        ),
        lambda f: replace(
            f, seller_snapshot=replace(f.seller_snapshot, legal_name="\ud800")
        ),
        lambda f: replace(
            f,
            customer_snapshot=replace(
                f.customer_snapshot,
                address=replace(f.customer_snapshot.address, city="\ud800"),
            ),
        ),
        lambda f: replace(
            f, payment_instructions=replace(f.payment_instructions, narrative="\ud800")
        ),
    ],
)
def test_malformed_optional_nested_values_refuse_with_stable_typed_errors(mutate):
    result = render_document_v2(
        request(mutate(fact())),
        catalog=catalog(),
        renderer=DeterministicHtmlRendererV2(),
    )
    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_code is RenderErrorCode.FACT_SHAPE_INVALID
    assert result.error_class is RenderErrorClass.PERMANENT
    assert result.payload is None


def test_wrong_top_level_fact_fails_with_a_typed_shape_error():
    with pytest.raises(FactShapeInvalid):
        render_document_v2(
            replace(request(), fact={}),
            catalog=catalog(),
            renderer=DeterministicHtmlRendererV2(),
        )


def test_equivalent_decimal_scales_and_instant_offsets_keep_projection_and_output():
    first = fact()
    first = replace(
        first,
        lines=(replace(first.lines[0], quantity=Decimal("1.0")),) + first.lines[1:],
    )
    offset = timezone(timedelta(hours=1))
    second = replace(
        first,
        issued_at=first.issued_at.astimezone(offset),
        recorded_at=first.recorded_at.astimezone(offset),
        frozen_at=first.frozen_at.astimezone(offset),
        lines=(replace(first.lines[0], quantity=Decimal("1.00")),) + first.lines[1:],
    )
    assert (
        first.original_presentation_model_digest
        == second.original_presentation_model_digest
    )
    assert fact_fingerprint_v2(first) == fact_fingerprint_v2(second)
    assert projection(first) == projection(second)
    first_result = render_document_v2(
        request(first), catalog=catalog(), renderer=DeterministicHtmlRendererV2()
    )
    second_result = render_document_v2(
        request(second), catalog=catalog(), renderer=DeterministicHtmlRendererV2()
    )
    assert first_result.outcome is second_result.outcome is RenderOutcome.RENDERED
    assert first_result.payload == second_result.payload
    assert first_result.projection_digest == second_result.projection_digest


def test_qualified_frozen_payment_url_is_pdf_text_without_annotations():
    from pypdf import PdfReader

    value = fact()
    url = "https://pay.example.test/frozen?invoice=13&version=7"
    value = replace(
        value,
        payment_instructions=replace(
            value.payment_instructions,
            payment_link=FrozenPaymentLinkV2(
                url, "future-payment-owner", "snapshot:13", "7"
            ),
        ),
    )
    _, renderer, pdf_catalog = pdf_case(value)
    result = render_document_v2(
        request(value, media_type="application/pdf"),
        catalog=pdf_catalog,
        renderer=renderer,
    )
    assert result.outcome is RenderOutcome.RENDERED
    parsed = PdfReader(BytesIO(result.payload), strict=True)
    assert all("/Annots" not in page for page in parsed.pages)
    assert url in " ".join(page.extract_text() for page in parsed.pages)


def _decimal_context_state(context):
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        tuple(
            sorted((signal.__name__, value) for signal, value in context.traps.items())
        ),
        tuple(
            sorted((signal.__name__, value) for signal, value in context.flags.items())
        ),
    )


@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_UP])
def test_ambient_decimal_precision_rounding_traps_and_exponents_do_not_change_rendering(
    rounding,
):
    value = fact()
    value = replace(value, grand_total=money("123.45"))
    render_request = request(value)
    render_catalog = catalog(value)
    renderer = DeterministicHtmlRendererV2()
    expected_fingerprint = fact_fingerprint_v2(value)
    expected_projection = projection(value)
    expected_result = render_document_v2(
        render_request, catalog=render_catalog, renderer=renderer
    )
    assert expected_result.outcome is RenderOutcome.RENDERED
    outer = _decimal_context_state(getcontext())
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = rounding
        ambient.Emin = -2
        ambient.Emax = 2
        ambient.capitals = 0
        ambient.clamp = 1
        for signal in (Inexact, Rounded, Overflow, Underflow, Subnormal, Clamped):
            ambient.traps[signal] = True
        ambient.clear_flags()
        before = _decimal_context_state(ambient)
        fingerprint = fact_fingerprint_v2(value)
        projected = projection(value)
        result = render_document_v2(
            render_request, catalog=render_catalog, renderer=renderer
        )
        assert _decimal_context_state(ambient) == before
    assert _decimal_context_state(getcontext()) == outer
    assert fingerprint == expected_fingerprint
    assert projected == expected_projection
    assert result.outcome is RenderOutcome.RENDERED
    assert result.projection_digest == expected_result.projection_digest
    assert result.payload == expected_result.payload
    total = projected_values(projected)["grand_total"]
    assert (total.raw, total.text) == ("123.45", "NGN 123.45")
    assert value.original_presentation_model_digest == "a" * 64


def test_large_quantity_and_rate_keep_all_digits_and_hash_distinct_values():
    quantity = "12345678901234567890123456789.12345678901234567891"
    rate = "0.12345678901234567890123456789123456789"
    value = fact()
    value = replace(
        value,
        lines=(replace(value.lines[0], quantity=Decimal(quantity)),) + value.lines[1:],
        tax_snapshots=(replace(value.tax_snapshots[0], rate=Decimal(rate)),),
    )
    expected_fingerprint = fact_fingerprint_v2(value)
    expected_projection = projection(value)
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.traps[Inexact] = ambient.traps[Rounded] = True
        ambient.clear_flags()
        before = _decimal_context_state(ambient)
        assert fact_fingerprint_v2(value) == expected_fingerprint
        assert projection(value) == expected_projection
        assert _decimal_context_state(ambient) == before
    values = projected_values(expected_projection)
    assert values["lines[0].quantity"].raw == quantity
    assert values["lines[0].quantity"].text == quantity
    assert values["tax_snapshots[0].rate"].raw == rate
    assert values["tax_snapshots[0].rate"].text == rate
    for different in (
        replace(
            value,
            lines=(
                replace(
                    value.lines[0],
                    quantity=Decimal(
                        "12345678901234567890123456789.12345678901234567892"
                    ),
                ),
            )
            + value.lines[1:],
        ),
        replace(
            value,
            tax_snapshots=(
                replace(
                    value.tax_snapshots[0],
                    rate=Decimal("0.12345678901234567890123456789123456788"),
                ),
            ),
        ),
    ):
        assert fact_fingerprint_v2(different) != expected_fingerprint
        assert projection(different).digest != expected_projection.digest


@pytest.mark.parametrize("decimal_text", ["1" * 4097, "1e4097", "1e-4097"])
@pytest.mark.parametrize("field", ["quantity", "rate"])
def test_oversized_decimal_coefficients_and_exponents_return_typed_refusals(
    decimal_text, field
):
    value = fact()
    oversized = Decimal(decimal_text)
    if field == "quantity":
        value = replace(
            value,
            lines=(replace(value.lines[0], quantity=oversized),) + value.lines[1:],
        )
    else:
        value = replace(
            value, tax_snapshots=(replace(value.tax_snapshots[0], rate=oversized),)
        )
    render_request, render_catalog = request(value), catalog(value)
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.traps[Inexact] = ambient.traps[Rounded] = True
        ambient.clear_flags()
        before = _decimal_context_state(ambient)
        result = render_document_v2(
            render_request,
            catalog=render_catalog,
            renderer=DeterministicHtmlRendererV2(),
        )
        assert _decimal_context_state(ambient) == before
    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_code is RenderErrorCode.FACT_SHAPE_INVALID
    assert result.error_class is RenderErrorClass.PERMANENT
    assert result.payload is result.byte_length is result.checksum_sha256 is None


def test_equivalent_signed_zero_money_has_identical_hash_projection_and_bytes():
    positive = replace(fact(), grand_total=money("0.00"))
    negative = replace(positive, grand_total=money("-0.00"))
    assert fact_fingerprint_v2(positive) == fact_fingerprint_v2(negative)
    assert projection(positive) == projection(negative)
    first = render_document_v2(
        request(positive),
        catalog=catalog(positive),
        renderer=DeterministicHtmlRendererV2(),
    )
    second = render_document_v2(
        request(negative),
        catalog=catalog(negative),
        renderer=DeterministicHtmlRendererV2(),
    )
    assert first.outcome is second.outcome is RenderOutcome.RENDERED
    assert first.payload == second.payload
    assert projected_values(first.projection)["grand_total"].raw == "0.00"


def test_oversized_money_refusal_preserves_context_and_integer_limit():
    import sys

    value = fact()
    # Construct a genuine Money in a sufficient context before testing rendering;
    # its existing two-minor-unit currency produces a 4,097-digit scaled integer.
    with localcontext() as construction:
        construction.prec = 16384
        oversized_money = money("1" * 4095 + ".00")
    value = replace(value, grand_total=oversized_money)
    render_request, render_catalog = request(value), catalog(value)
    integer_limit = sys.get_int_max_str_digits()
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.traps[Inexact] = ambient.traps[Rounded] = True
        ambient.clear_flags()
        before = _decimal_context_state(ambient)
        result = render_document_v2(
            render_request,
            catalog=render_catalog,
            renderer=DeterministicHtmlRendererV2(),
        )
        assert _decimal_context_state(ambient) == before
    assert sys.get_int_max_str_digits() == integer_limit
    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_code is RenderErrorCode.FACT_SHAPE_INVALID
    assert result.error_class is RenderErrorClass.PERMANENT
    assert result.payload is None
