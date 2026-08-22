"""Contract tests for the stateless issued-document rendering owner.

These tests describe the public contract before its implementation.  The
fixture deliberately has nine lines: Sub's production fallback truncated at
seven, so a seven-line fixture would let the real defect pass unnoticed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from dotmac_document_rendering import (
    DEFAULT_ENGLISH_LABELS,
    ColumnSpec,
    DeterministicHtmlRenderer,
    DiscountLineV1,
    DocumentKind,
    DocumentLineV1,
    DocumentProfileBinding,
    DocumentRenderingError,
    DocumentState,
    DocumentTemplateV1,
    EngineUnavailable,
    ExactAmount,
    InMemoryDocumentRenderer,
    InvoiceDocumentFactV1,
    LabelledValue,
    LabelText,
    PartySnapshotV1,
    PaymentInstructionsV1,
    PdfDocumentRenderer,
    PlatformScope,
    RenderedValue,
    RenderErrorClass,
    RenderErrorCode,
    RenderOutcome,
    RenderRequestV1,
    Section,
    SourceAuthority,
    Table,
    TemplateCatalog,
    TemplateDecision,
    TemplateInvalid,
    TemplateNotFound,
    TenantScope,
    canonical_projection_payload,
    project_invoice,
    projection_digest,
    render_document,
    renderer_contract_violations,
    validate_projection_source_fields,
)

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
INVOICE_ID = UUID("20000000-0000-0000-0000-000000000002")
GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "document_rendering"
    / "invoice_projection_v1.json"
)


def _money(value: str) -> ExactAmount:
    return ExactAmount(currency="USD", minor_units=2, amount=value)


def _line(position: int) -> DocumentLineV1:
    return DocumentLineV1(
        position=position,
        description=f"Service line {position}",
        quantity="1",
        unit="month",
        unit_amount=_money("10.00"),
        line_total=_money("10.00"),
        applied_price_code="standard",
        applied_price_version="2026-08",
        source_code="subscription",
        source_version="1",
    )


def _fact(*, scope=None) -> InvoiceDocumentFactV1:
    return InvoiceDocumentFactV1(
        contract_version=1,
        scope=scope or TenantScope(TENANT_ID),
        invoice_id=INVOICE_ID,
        fact_version=1,
        emitted_at=datetime(2026, 8, 19, 7, 1, tzinfo=UTC),
        issued_at=datetime(2026, 8, 19, 7, 0, tzinfo=UTC),
        frozen_at=datetime(2026, 8, 19, 7, 0, tzinfo=UTC),
        document_number="INV-2026-0001",
        document_series_code="invoice",
        document_series_version="1",
        document_state=DocumentState.ISSUED,
        document_kind=DocumentKind.INVOICE,
        seller=PartySnapshotV1(
            legal_name="Dotmac Technologies Ltd",
            address_lines=("Abuja", "Nigeria"),
            registered_identifier="RC-1",
            tax_identifier="TIN-1",
            contact="billing@example.test",
        ),
        customer=PartySnapshotV1(
            legal_name="Example Customer <Ltd>",
            address_lines=("Lagos", "Nigeria"),
            tax_identifier="TIN-CUSTOMER",
        ),
        lines=tuple(_line(position) for position in range(1, 10)),
        discounts=(
            DiscountLineV1(
                position=1,
                description="Loyalty credit",
                amount=_money("5.00"),
                source_code="promotion",
                source_version="3",
            ),
        ),
        tax_lines=(),
        subtotal=_money("90.00"),
        tax_total=_money("0.00"),
        total=_money("85.00"),
        currency="USD",
        minor_units=2,
        payment_terms="Due on receipt",
        due_date=date(2026, 8, 19),
        payment_instructions=PaymentInstructionsV1(
            bank_name="Example Bank",
            account_name="Dotmac Technologies Ltd",
            account_number="0000000000",
            sort_code="000001",
        ),
        brand_asset_id=None,
        locale="en",
        timezone="Africa/Lagos",
        document_profile_code="tax-invoice",
        document_profile_version=1,
        source_authority=SourceAuthority.INTERNAL,
        source_system="dotmac-billing",
        source_record_id="billing-invoice-1",
        source_record_version="1",
        correlation_id="corr-1",
    )


def _template(*, code: str = "invoice-en", version: str = "1") -> DocumentTemplateV1:
    return DocumentTemplateV1(
        contract_version=1,
        template_code=code,
        template_version=version,
        language_tag="en",
        labels=tuple(
            LabelText(key=key, text=text) for key, text in DEFAULT_ENGLISH_LABELS
        ),
        date_format_code="%Y-%m-%d",
        datetime_format_code="%Y-%m-%d %H:%M %Z",
    )


def _catalog() -> TemplateCatalog:
    return TemplateCatalog(
        templates=(_template(),),
        bindings=(
            DocumentProfileBinding(
                document_profile_code="tax-invoice",
                document_profile_version=1,
                media_type="text/html",
                template_code="invoice-en",
                template_version="1",
            ),
        ),
        assembly_file="app/document_rendering.py",
    )


def _request(fact: InvoiceDocumentFactV1 | None = None, **overrides) -> RenderRequestV1:
    values = {
        "fact": fact or _fact(),
        "media_type": "text/html",
        "rendered_at": datetime(2026, 8, 19, 7, 2, tzinfo=UTC),
        "max_bytes": 1_000_000,
        "deadline_seconds": 5.0,
        "idempotency_key": "invoice-1-v1-html",
        "request_fingerprint": "request-fingerprint",
        "correlation_id": "render-corr-1",
    }
    values.update(overrides)
    return RenderRequestV1(**values)


def _projection(fact: InvoiceDocumentFactV1 | None = None):
    renderer = DeterministicHtmlRenderer()
    selected = _catalog().select(
        document_profile_code="tax-invoice",
        document_profile_version=1,
        media_type="text/html",
    )
    return project_invoice(
        fact or _fact(),
        selected,
        renderer_code=renderer.code,
        renderer_version=renderer.version,
        media_type="text/html",
    )


def test_same_fact_template_and_renderer_produce_the_same_semantic_projection() -> None:
    first = _projection()
    second = _projection()

    assert first == second
    assert first.digest == projection_digest(first)
    assert first.digest == projection_digest(second)
    assert canonical_projection_payload(first) == canonical_projection_payload(second)


def test_canonical_projection_matches_the_reviewed_v1_golden() -> None:
    projection = _projection()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert projection.digest == golden["digest"]
    assert canonical_projection_payload(projection) == golden["projection"]


def test_in_memory_fake_passes_the_reusable_renderer_contract() -> None:
    renderer = InMemoryDocumentRenderer()
    selected = _catalog().select(
        document_profile_code="tax-invoice",
        document_profile_version=1,
        media_type="text/html",
    )
    projection = project_invoice(
        _fact(),
        selected,
        renderer_code=renderer.code,
        renderer_version=renderer.version,
        media_type="text/html",
    )

    assert (
        renderer_contract_violations(
            renderer,
            projection=projection,
            request=_request(),
        )
        == ()
    )


def test_projection_is_scope_invariant_because_scope_changes_storage_not_meaning() -> (
    None
):
    tenant = _projection(_fact(scope=TenantScope(TENANT_ID)))
    platform = _projection(_fact(scope=PlatformScope()))

    assert tenant.digest == platform.digest
    assert canonical_projection_payload(tenant) == canonical_projection_payload(
        platform
    )


def test_every_line_and_money_source_is_projected() -> None:
    projection = _projection()
    line_table = next(
        block
        for section in projection.sections
        if section.key == "line_items"
        for block in section.blocks
        if isinstance(block, Table)
    )

    assert len(line_table.rows) == 9
    assert [row[0].text for row in line_table.rows] == [str(i) for i in range(1, 10)]
    assert validate_projection_source_fields(_fact(), projection) == ()
    money_values = [
        value
        for section in projection.sections
        for block in section.blocks
        for value in block.rendered_values()
        if value.kind == "money"
    ]
    assert money_values
    assert all(value.source_field for value in money_values)
    assert all(value.text.startswith("USD ") for value in money_values)


def test_exact_amount_formats_without_rounding_or_a_currency_symbol() -> None:
    projection = _projection()
    total = next(
        block.value
        for section in projection.sections
        if section.key == "totals"
        for block in section.blocks
        if isinstance(block, LabelledValue) and block.label_key == "total"
    )

    assert total.raw == "USD 85.00"
    assert total.text == "USD 85.00"
    assert total.currency == "USD"
    assert total.minor_units == 2

    with pytest.raises(ValueError, match="minor units"):
        ExactAmount(currency="USD", minor_units=2, amount="1.001")
    with pytest.raises(TypeError, match="string"):
        ExactAmount(currency="USD", minor_units=2, amount=1.0)  # type: ignore[arg-type]


def test_credit_note_shape_is_validated_never_recomputed() -> None:
    invalid = replace(
        _fact(),
        document_kind=DocumentKind.CREDIT_NOTE,
        document_profile_code="credit-note",
        tax_total=_money("1.00"),
    )
    catalog = TemplateCatalog(
        templates=(_template(code="credit-note-en"),),
        bindings=(
            DocumentProfileBinding(
                document_profile_code="credit-note",
                document_profile_version=1,
                media_type="text/html",
                template_code="credit-note-en",
                template_version="1",
            ),
        ),
        assembly_file="app/document_rendering.py",
    )

    result = render_document(
        _request(invalid), catalog=catalog, renderer=DeterministicHtmlRenderer()
    )

    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_code is RenderErrorCode.FACT_SHAPE_INVALID
    assert result.error_class is RenderErrorClass.PERMANENT
    assert result.payload is None


def test_missing_number_and_unsupported_fact_version_fail_closed() -> None:
    renderer = DeterministicHtmlRenderer()

    missing = render_document(
        _request(replace(_fact(), document_number="")),
        catalog=_catalog(),
        renderer=renderer,
    )
    unsupported = render_document(
        _request(replace(_fact(), contract_version=2)),
        catalog=_catalog(),
        renderer=renderer,
    )

    assert (missing.outcome, missing.error_code, missing.error_class) == (
        RenderOutcome.REFUSED,
        RenderErrorCode.FACT_INCOMPLETE,
        RenderErrorClass.PERMANENT,
    )
    assert unsupported.error_code is RenderErrorCode.FACT_VERSION_UNSUPPORTED


def test_unbound_profile_names_the_assembly_file_as_the_fix() -> None:
    fact = replace(_fact(), document_profile_code="unbound-profile")

    result = render_document(
        _request(fact), catalog=_catalog(), renderer=DeterministicHtmlRenderer()
    )

    assert result.error_code is RenderErrorCode.TEMPLATE_NOT_FOUND
    assert "app/document_rendering.py" in (result.error_message or "")
    with pytest.raises(TemplateNotFound, match="app/document_rendering.py"):
        _catalog().select(
            document_profile_code="unbound-profile",
            document_profile_version=1,
            media_type="text/html",
        )


def test_template_versions_are_immutable_and_catalog_duplicates_fail_at_boot() -> None:
    with pytest.raises(TemplateInvalid, match="duplicate template"):
        TemplateCatalog(
            templates=(_template(), _template()),
            bindings=(),
            assembly_file="app/document_rendering.py",
        )
    with pytest.raises(TemplateInvalid, match="duplicate binding"):
        TemplateCatalog(
            templates=(_template(),),
            bindings=(_catalog().bindings[0], _catalog().bindings[0]),
            assembly_file="app/document_rendering.py",
        )


def test_html_renderer_escapes_fact_text_and_corresponds_to_the_projection() -> None:
    request = _request()
    result = render_document(
        request, catalog=_catalog(), renderer=DeterministicHtmlRenderer()
    )

    assert result.outcome is RenderOutcome.RENDERED
    assert result.payload is not None
    html = result.payload.decode("utf-8")
    assert "Example Customer &lt;Ltd&gt;" in html
    assert "Example Customer <Ltd>" not in html
    assert result.byte_length == len(result.payload)
    assert result.checksum_sha256.startswith("sha256:")
    assert result.rendered_at == request.rendered_at
    assert result.projection is not None
    for section in result.projection.sections:
        for block in section.blocks:
            for value in block.rendered_values():
                assert value.text.replace("<", "&lt;").replace(">", "&gt;") in html


def test_output_limit_refuses_the_whole_document_instead_of_truncating() -> None:
    result = render_document(
        _request(max_bytes=32),
        catalog=_catalog(),
        renderer=DeterministicHtmlRenderer(),
    )

    assert result.outcome is RenderOutcome.REFUSED
    assert result.error_code is RenderErrorCode.OUTPUT_TOO_LARGE
    assert result.payload is None
    assert result.byte_length is None
    assert result.checksum_sha256 is None
    assert "fallback" not in {outcome.value for outcome in RenderOutcome}


class _UnavailablePdfEngine:
    code = "unavailable-pdf"
    version = "1"

    def render_pdf(self, html: str, *, deadline_seconds: float) -> bytes:
        raise EngineUnavailable("native PDF engine is absent")


class _TimeoutPdfEngine:
    code = "timeout-pdf"
    version = "1"

    def render_pdf(self, html: str, *, deadline_seconds: float) -> bytes:
        raise TimeoutError


def _pdf_catalog() -> TemplateCatalog:
    return TemplateCatalog(
        templates=(_template(),),
        bindings=(
            DocumentProfileBinding(
                document_profile_code="tax-invoice",
                document_profile_version=1,
                media_type="application/pdf",
                template_code="invoice-en",
                template_version="1",
            ),
        ),
        assembly_file="app/document_rendering.py",
    )


@pytest.mark.parametrize(
    ("engine", "error_code"),
    [
        (_UnavailablePdfEngine(), RenderErrorCode.ENGINE_UNAVAILABLE),
        (_TimeoutPdfEngine(), RenderErrorCode.ENGINE_TIMEOUT),
    ],
)
def test_pdf_engine_failures_are_retryable_and_never_fall_back(
    engine, error_code
) -> None:
    result = render_document(
        _request(media_type="application/pdf"),
        catalog=_pdf_catalog(),
        renderer=PdfDocumentRenderer(engine),
    )

    assert result.outcome is RenderOutcome.FAILED
    assert result.error_code is error_code
    assert result.error_class is RenderErrorClass.RETRYABLE
    assert result.payload is None


class _PdfEngine:
    code = "test-pdf"
    version = "1"

    def render_pdf(self, html: str, *, deadline_seconds: float) -> bytes:
        assert "INV-2026-0001" in html
        return b"%PDF-1.7\ncomplete-document"


def test_pdf_adapter_binds_exact_payload_length_and_checksum() -> None:
    result = render_document(
        _request(media_type="application/pdf"),
        catalog=_pdf_catalog(),
        renderer=PdfDocumentRenderer(_PdfEngine()),
    )

    assert result.outcome is RenderOutcome.RENDERED
    assert result.payload == b"%PDF-1.7\ncomplete-document"
    assert result.byte_length == len(result.payload)
    assert result.checksum_sha256 is not None


class _ProjectionMutatingRenderer(DeterministicHtmlRenderer):
    mutation: str

    def __init__(self, mutation: str) -> None:
        super().__init__()
        self.mutation = mutation

    def render(self, projection, request):
        if self.mutation == "layout":
            original = super().render(projection, request)
            assert original.payload is not None
            payload = original.payload.replace(
                b"<head>", b"<head><style>.layout-only{display:block}</style>"
            )
            return replace(
                original,
                payload=payload,
                byte_length=len(payload),
                checksum_sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
            )

        sections = list(projection.sections)
        if self.mutation == "clock":
            sections.append(
                Section(
                    key="volatile",
                    position=999,
                    blocks=(
                        LabelledValue(
                            label_key="clock",
                            label_text="Clock",
                            value=RenderedValue(
                                kind="datetime",
                                source_field=None,
                                raw=datetime.now(tz=UTC).isoformat(),
                                text=datetime.now(tz=UTC).isoformat(),
                                timezone="UTC",
                            ),
                        ),
                    ),
                )
            )
        else:
            index = next(
                i for i, section in enumerate(sections) if section.key == "line_items"
            )
            section = sections[index]
            table = next(block for block in section.blocks if isinstance(block, Table))
            rows = (
                tuple(reversed(table.rows))
                if self.mutation == "shuffle"
                else table.rows[:7]
            )
            sections[index] = replace(section, blocks=(replace(table, rows=rows),))

        mutated = replace(projection, sections=tuple(sections), digest="")
        mutated = replace(mutated, digest=projection_digest(mutated))
        return super().render(mutated, request)


@pytest.mark.parametrize("mutation", ["clock", "shuffle", "truncate"])
def test_determinism_conformance_guard_bites_on_real_failure_classes(
    mutation: str,
) -> None:
    violations = renderer_contract_violations(
        _ProjectionMutatingRenderer(mutation),
        projection=_projection(),
        request=_request(),
    )
    assert violations, mutation


def test_determinism_guard_accepts_a_layout_only_change() -> None:
    assert (
        renderer_contract_violations(
            _ProjectionMutatingRenderer("layout"),
            projection=_projection(),
            request=_request(),
        )
        == ()
    )


def test_projection_shapes_are_frozen_and_ordered() -> None:
    decision = TemplateDecision(key="tax_rows", outcome="omitted")
    column = ColumnSpec(key="description", label_text="Description")
    assert decision.key == "tax_rows"
    assert column.key == "description"
    with pytest.raises(DocumentRenderingError):
        project_invoice(
            replace(_fact(), lines=tuple(reversed(_fact().lines))),
            _catalog().select(
                document_profile_code="tax-invoice",
                document_profile_version=1,
                media_type="text/html",
            ),
            renderer_code="fake-html",
            renderer_version="1",
            media_type="text/html",
        )
