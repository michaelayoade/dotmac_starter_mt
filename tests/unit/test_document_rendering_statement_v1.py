"""Hosted-only account statement fact, projection, selection and HTML tests."""

import hashlib
from dataclasses import fields, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from dotmac_document_rendering import (
    DEFAULT_ENGLISH_LABELS_STATEMENT_V1,
    HOSTILE_LEGAL_NAME,
    LOGO_MAX_BYTES,
    AccountStatementFactV1,
    DocumentTemplateV1,
    EmbeddedLogoAssetV3,
    FactIncomplete,
    FactShapeInvalid,
    FactVersionUnsupported,
    FrozenPaymentLinkV2,
    LabelText,
    StatementEntryV1,
    StatementProfileBindingV1,
    StatementTemplateCatalogV1,
    TemplateInvalid,
    TemplateNotFound,
    fact_fingerprint_statement_v1,
    html_data_only_violations_v3,
    html_from_statement_projection_v1,
    project_statement_v1,
    statement_html_v1,
    statement_projection_digest_v1,
    validate_statement_fact_v1,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money

from tests.unit.test_document_rendering_v2 import fact as invoice_fact
from tests.unit.test_document_rendering_v3 import LOGO, asset

NOW = datetime(2026, 10, 31, 16, 0, tzinfo=UTC)
STATEMENT_ID = UUID("40000000-0000-0000-0000-000000000004")
SOURCE_ID = UUID("50000000-0000-0000-0000-000000000005")


def naira(amount: str) -> Money:
    return Money.of(amount, Currency("NGN", 2))


def entry(number: int, kind: str, debit: str, credit: str, balance: str, day: int):
    return StatementEntryV1(
        entry_number=number,
        occurred_at=datetime(2026, 10, day, 10, 0, tzinfo=UTC),
        entry_kind=kind,
        reference=f"REF-{number}",
        description=f"Frozen entry {number}",
        debit=naira(debit),
        credit=naira(credit),
        balance_after=naira(balance),
        source_fact_id=SOURCE_ID,
        source_fact_version=3,
    )


def statement(**overrides) -> AccountStatementFactV1:
    parties = invoice_fact()
    values = {
        "contract_version": 1,
        "scope": TenantScope(UUID("10000000-0000-0000-0000-000000000001")),
        "statement_id": STATEMENT_ID,
        "emitter": "dotmac-billing",
        "fact_version": 2,
        "recorded_at": NOW,
        "original_presentation_model_digest": "c" * 64,
        "statement_number": "STM-2026-10-0001",
        "statement_state": "issued",
        "statement_profile_code": "statement.standard",
        "statement_profile_version": "opaque:s1",
        "currency": "NGN",
        "minor_units": 2,
        "period_start": datetime(2026, 10, 1, tzinfo=UTC),
        "period_end": datetime(2026, 10, 31, tzinfo=UTC),
        "opening_balance": naira("0.00"),
        "total_debits": naira("110.00"),
        "total_credits": naira("50.00"),
        "closing_balance": naira("60.00"),
        "entries": (
            entry(1, "invoice", "110.00", "0.00", "110.00", 5),
            entry(2, "payment", "0.00", "50.00", "60.00", 12),
        ),
        "customer_account_reference": "ACC-0042",
        "seller_snapshot": parties.seller_snapshot,
        "customer_snapshot": parties.customer_snapshot,
        "payment_instructions": parties.payment_instructions,
        "brand_logo": asset(),
        "locale": "en",
        "timezone": "Africa/Lagos",
        "issued_at": NOW,
        "frozen_at": NOW,
        "source_authority": "internal",
        "correlation_id": "statement:2",
    }
    values.update(overrides)
    return AccountStatementFactV1(**values)


def catalog(value=None, *, labels=DEFAULT_ENGLISH_LABELS_STATEMENT_V1, version=None):
    value = value or statement()
    return StatementTemplateCatalogV1(
        templates=(
            DocumentTemplateV1(
                contract_version=1,
                template_code="statement-en",
                template_version="1",
                language_tag="en",
                labels=tuple(LabelText(key, text) for key, text in labels),
                date_format_code="%Y-%m-%d",
                datetime_format_code="%Y-%m-%d %H:%M %Z",
            ),
        ),
        bindings=(
            StatementProfileBindingV1(
                value.statement_profile_code,
                version or value.statement_profile_version,
                "text/html",
                "statement-en",
                "1",
            ),
        ),
        assembly_file="tests/unit/test_document_rendering_statement_v1.py",
    )


def project(value=None):
    value = value or statement()
    selected = catalog(value).select(
        statement_profile_code=value.statement_profile_code,
        statement_profile_version=value.statement_profile_version,
        media_type="text/html",
    )
    return project_statement_v1(
        value, selected, renderer_code="r", renderer_version="1", media_type="text/html"
    )


def test_projection_formats_supplied_values_exactly_and_in_order():
    projection = project()
    assert [section.key for section in projection.sections] == [
        "document",
        "seller",
        "customer",
        "summary",
        "entries",
        "payment_instructions",
    ]
    table = projection.sections[4].blocks[1]
    assert len(table.rows) == 2
    assert [column.key for column in table.columns][:3] == [
        "position",
        "entry_date",
        "entry_kind",
    ]
    first = table.rows[0]
    assert first[1].text == "2026-10-05"
    assert first[5].raw == "110.00" and first[5].text == "NGN 110.00"
    assert table.rows[1][6].text == "NGN 50.00"
    assert table.rows[1][7].text == "NGN 60.00"
    summary = {b.label_key: b.value.text for b in projection.sections[3].blocks[1:]}
    assert summary == {
        "opening_balance": "NGN 0.00",
        "total_debits": "NGN 110.00",
        "total_credits": "NGN 50.00",
        "closing_balance": "NGN 60.00",
    }
    assert projection.digest.startswith("cv1:")
    assert projection.digest == statement_projection_digest_v1(projection)


def test_nothing_is_derived_or_reconciled_by_the_renderer():
    # The supplied closing balance disagrees with the entries: it is rendered
    # exactly as supplied. Reconciliation belongs to the fact's owner.
    projection = project(statement(closing_balance=naira("999.00")))
    summary = {b.label_key: b.value.raw for b in projection.sections[3].blocks[1:]}
    assert summary["closing_balance"] == "999.00"


def test_projection_is_deterministic_and_bound_to_every_frozen_input():
    value = statement()
    assert project(value) == project(value)
    base = fact_fingerprint_statement_v1(value)
    for changed in (
        replace(value, closing_balance=naira("61.00")),
        replace(value, period_end=datetime(2026, 10, 30, tzinfo=UTC)),
        replace(value, entries=(value.entries[0],)),
        replace(
            value,
            entries=(replace(value.entries[0], balance_after=naira("111.00")),)
            + value.entries[1:],
        ),
        replace(value, brand_logo=asset(LOGO + b"x")),
        replace(value, brand_logo=None),
        replace(value, original_presentation_model_digest="d" * 64),
    ):
        assert fact_fingerprint_statement_v1(changed) != base
    assert project(value).digest != project(replace(value, statement_number="X")).digest


def test_html_embeds_one_data_image_and_no_followable_url():
    link = FrozenPaymentLinkV2("https://pay.example.test/s/1", "billing", "s1", "1")
    value = statement()
    value = replace(
        value,
        payment_instructions=replace(value.payment_instructions, payment_link=link),
    )
    projection, html = statement_html_v1(
        value, catalog=catalog(value), logo_content=LOGO
    )
    assert html.count("<img") == 1 and "data:image/png;base64," in html
    assert "https://pay.example.test/s/1" in html
    assert "<a " not in html and "href" not in html
    assert html_data_only_violations_v3(html) == ()
    assert projection.brand_logo == value.brand_logo
    assert (
        html == statement_html_v1(value, catalog=catalog(value), logo_content=LOGO)[1]
    )


def test_statement_without_logo_or_payment_instructions_renders_without_either():
    value = statement(brand_logo=None, payment_instructions=None)
    projection, html = statement_html_v1(value, catalog=catalog(value))
    assert "<img" not in html
    assert "payment_instructions" not in [s.key for s in projection.sections]
    decisions = {d.key: d.outcome for d in projection.template_decisions}
    assert decisions["payment_instructions"] == "omitted"
    assert decisions["brand_logo"] == "omitted"


def test_hostile_text_is_inert():
    value = statement()
    value = replace(
        value,
        customer_snapshot=replace(
            value.customer_snapshot, legal_name=HOSTILE_LEGAL_NAME
        ),
    )
    _, html = statement_html_v1(value, catalog=catalog(value), logo_content=LOGO)
    assert "&lt;/style&gt;&lt;img src=http://x&gt;" in html
    assert html.count("<img") == 1
    assert html_data_only_violations_v3(html) == ()


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda v: replace(v, contract_version=2), FactVersionUnsupported),
        (lambda v: replace(v, statement_state="cancelled"), FactShapeInvalid),
        (lambda v: replace(v, currency="ngn"), FactShapeInvalid),
        (lambda v: replace(v, minor_units=10), FactShapeInvalid),
        (lambda v: replace(v, statement_number=" "), FactIncomplete),
        (
            lambda v: replace(v, original_presentation_model_digest="A" * 64),
            FactShapeInvalid,
        ),
        (lambda v: replace(v, timezone="Not/AZone"), FactShapeInvalid),
        (
            lambda v: replace(v, period_start=datetime(2026, 11, 2, tzinfo=UTC)),
            FactShapeInvalid,
        ),
        (lambda v: replace(v, issued_at=datetime(2026, 10, 31)), FactShapeInvalid),
        (
            lambda v: replace(v, opening_balance=Money.of("0.00", Currency("USD", 2))),
            FactShapeInvalid,
        ),
        (lambda v: replace(v, entries=(v.entries[1], v.entries[0])), FactShapeInvalid),
        (
            lambda v: replace(
                v, entries=(replace(v.entries[0], entry_number=0),) + v.entries[1:]
            ),
            FactShapeInvalid,
        ),
        (
            lambda v: replace(
                v, entries=(replace(v.entries[0], entry_kind="refund"),) + v.entries[1:]
            ),
            FactShapeInvalid,
        ),
        (
            lambda v: replace(
                v,
                entries=(replace(v.entries[0], debit=naira("-1.00")),) + v.entries[1:],
            ),
            FactShapeInvalid,
        ),
        (
            lambda v: replace(
                v, entries=(replace(v.entries[0], reference=""),) + v.entries[1:]
            ),
            FactIncomplete,
        ),
        (lambda v: replace(v, brand_logo=asset(LOGO, "image/gif")), FactShapeInvalid),
        (
            lambda v: replace(
                v, brand_logo=replace(asset(), byte_length=LOGO_MAX_BYTES + 1)
            ),
            FactShapeInvalid,
        ),
        (lambda v: replace(v, customer_account_reference=" "), FactShapeInvalid),
        (
            lambda v: replace(
                v, seller_snapshot=replace(v.seller_snapshot, legal_name="")
            ),
            FactIncomplete,
        ),
    ],
)
def test_invalid_statement_facts_are_refused(mutate, error):
    with pytest.raises(error):
        validate_statement_fact_v1(mutate(statement()))


def test_empty_period_with_no_entries_is_a_valid_statement():
    validate_statement_fact_v1(statement(entries=()))


def test_fact_shape_has_no_url_or_path_field_and_logo_is_a_snapshot():
    names = {item.name for item in fields(AccountStatementFactV1)}
    assert not {n for n in names if "url" in n or "path" in n}
    assert fields(EmbeddedLogoAssetV3)[0].name == "file_id"


def test_template_selection_is_exact_and_fail_closed():
    value = statement()
    selected = catalog(value).select(
        statement_profile_code=value.statement_profile_code,
        statement_profile_version=value.statement_profile_version,
        media_type="text/html",
    )
    assert selected.template.template_code == "statement-en"
    for kwargs in (
        {"statement_profile_code": "statement.other"},
        {"statement_profile_version": "opaque:s2"},
        {"media_type": "application/pdf"},
    ):
        arguments = {
            "statement_profile_code": value.statement_profile_code,
            "statement_profile_version": value.statement_profile_version,
            "media_type": "text/html",
            **kwargs,
        }
        with pytest.raises(TemplateNotFound):
            catalog(value).select(**arguments)


def test_template_without_statement_labels_is_refused():
    base_only = tuple(
        item
        for item in DEFAULT_ENGLISH_LABELS_STATEMENT_V1
        if item[0] != "closing_balance"
    )
    with pytest.raises(TemplateInvalid):
        catalog(labels=base_only)


def test_binding_to_a_missing_template_is_refused():
    value = statement()
    with pytest.raises(TemplateInvalid):
        StatementTemplateCatalogV1(
            templates=(),
            bindings=(
                StatementProfileBindingV1(
                    value.statement_profile_code, "opaque:s1", "text/html", "nope", "1"
                ),
            ),
            assembly_file="tests/unit/test_document_rendering_statement_v1.py",
        )


def test_projection_must_match_the_exact_binding_and_not_be_stale():
    value = statement()
    selected = catalog(value).select(
        statement_profile_code=value.statement_profile_code,
        statement_profile_version=value.statement_profile_version,
        media_type="text/html",
    )
    with pytest.raises(FactShapeInvalid):
        project_statement_v1(
            value,
            selected,
            renderer_code="r",
            renderer_version="1",
            media_type="application/pdf",
        )
    projection = project(value)
    with pytest.raises(FactShapeInvalid):
        html_from_statement_projection_v1(
            replace(projection, digest="cv1:" + "0" * 64), logo_content=LOGO
        )


@pytest.mark.parametrize(
    "content",
    [
        None,
        LOGO + b"\x00",
        LOGO[:-1] + b"Z",
        b"\xff\xd8\xff" + LOGO[3:],
        bytearray(LOGO),
    ],
)
def test_logo_bytes_that_are_not_the_frozen_logo_are_refused(content):
    value = statement()
    with pytest.raises((FactShapeInvalid, FactIncomplete)):
        statement_html_v1(value, catalog=catalog(value), logo_content=content)


def test_unexpected_logo_bytes_for_a_logo_less_statement_are_refused():
    value = statement(brand_logo=None)
    with pytest.raises(FactShapeInvalid):
        statement_html_v1(value, catalog=catalog(value), logo_content=LOGO)
    assert hashlib.sha256(LOGO).hexdigest() == asset().sha256
