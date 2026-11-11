"""Pure lossless statement projection. No amount is summed, derived or checked
against another; every value is formatted exactly as supplied."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields, replace
from datetime import datetime
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Money
from dotmac_kernel.semantic_encoding import canonical_instant, digest_of, encode_fields

from dotmac_document_rendering.contracts import (
    Block,
    ColumnSpec,
    DocumentTemplateV1,
    FactIncomplete,
    FactShapeInvalid,
    FactVersionUnsupported,
    LabelledValue,
    RenderedValue,
    Section,
    StaticText,
    Table,
    TemplateDecision,
)
from dotmac_document_rendering.contracts_statement_v1 import (
    AccountStatementFactV1,
    AccountStatementProjectionV1,
    StatementEntryV1,
)
from dotmac_document_rendering.contracts_v2 import PartySnapshotV2
from dotmac_document_rendering.projection_v2 import (
    _canonical_decimal_text_v2,
    _semantic_context_v2,
    _validate_typed_value,
    _walk,
    canonical_value_v2,
    validate_payment_link_v2,
)
from dotmac_document_rendering.projection_v3 import validate_logo_asset_v3
from dotmac_document_rendering.templates_statement_v1 import (
    SelectedStatementTemplateV1,
)

STATEMENT_PROJECTION_CONTRACT_VERSION_V1 = 1
_ENTRY_KINDS = frozenset({"invoice", "credit_note", "receipt", "payment", "adjustment"})
_STATEMENT_STATES = frozenset({"issued", "corrected"})
_DATE_PATHS = ("issued_at", "period_start", "period_end", ".occurred_at")


def validate_statement_fact_v1(fact: AccountStatementFactV1) -> None:
    if not isinstance(fact, AccountStatementFactV1):
        raise FactShapeInvalid("fact must use the statement V1 immutable contract")
    _validate_typed_value(fact, AccountStatementFactV1, "fact")
    if fact.contract_version != 1:
        raise FactVersionUnsupported("only statement fact contract V1 is accepted")
    if not isinstance(fact.scope, TenantScope | PlatformScope):
        raise FactShapeInvalid("an explicit tenant or platform scope is required")
    if type(fact.minor_units) is not int or not 0 <= fact.minor_units <= 9:
        raise FactShapeInvalid("statement money scale must be an integer 0 to 9")
    if (
        len(fact.currency) != 3
        or not fact.currency.isascii()
        or not fact.currency.isalpha()
        or fact.currency != fact.currency.upper()
    ):
        raise FactShapeInvalid("statement currency must be an uppercase ISO code")
    for path, value in _walk(fact):
        if isinstance(value, Money) and (
            value.currency.code != fact.currency
            or value.currency.minor_units != fact.minor_units
            or not value.amount.is_finite()
        ):
            raise FactShapeInvalid(
                f"{path} does not match exact statement currency and scale"
            )
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise FactShapeInvalid(f"{path} must be timezone aware")
    if not fact.emitter.strip() or fact.fact_version <= 0:
        raise FactShapeInvalid("statement emitter and positive fact version required")
    for name in (
        "statement_number",
        "statement_profile_code",
        "statement_profile_version",
        "locale",
        "timezone",
        "correlation_id",
    ):
        if not getattr(fact, name).strip():
            raise FactIncomplete(f"{name} must be a nonempty string")
    if fact.customer_account_reference is not None and (
        not fact.customer_account_reference.strip()
    ):
        raise FactShapeInvalid("customer account reference must be text or absent")
    if fact.statement_state not in _STATEMENT_STATES or fact.source_authority not in {
        "internal",
        "provider_owned",
        "external_finance",
    }:
        raise FactShapeInvalid("invalid frozen statement classification")
    digest = fact.original_presentation_model_digest
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise FactShapeInvalid(
            "original producer digest must remain a bare lowercase SHA-256 digest"
        )
    try:
        ZoneInfo(fact.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise FactShapeInvalid("unknown frozen timezone") from exc
    if fact.period_start > fact.period_end:
        raise FactShapeInvalid("statement period starts after it ends")
    if fact.brand_logo is not None:
        validate_logo_asset_v3(fact.brand_logo)
    numbers = tuple(entry.entry_number for entry in fact.entries)
    if any(number <= 0 for number in numbers) or numbers != tuple(sorted(set(numbers))):
        raise FactShapeInvalid("entry numbers must be positive, unique and ordered")
    amounts = [
        fact.opening_balance,
        fact.total_debits,
        fact.total_credits,
        fact.closing_balance,
    ]
    previous: datetime | None = None
    for entry in fact.entries:
        if not isinstance(entry, StatementEntryV1):
            raise FactShapeInvalid("entry requires the complete typed snapshot")
        if entry.entry_kind not in _ENTRY_KINDS:
            raise FactShapeInvalid("entry kind is outside the frozen set")
        if not entry.reference.strip() or not entry.description.strip():
            raise FactIncomplete("entry reference and description are required")
        if entry.source_fact_version <= 0:
            raise FactShapeInvalid("entry source fact version must be positive")
        if entry.debit.amount < 0 or entry.credit.amount < 0:
            raise FactShapeInvalid("entry debit and credit must not be negative")
        if previous is not None and entry.occurred_at < previous:
            raise FactShapeInvalid("entries must be in chronological order")
        previous = entry.occurred_at
        amounts.extend((entry.debit, entry.credit, entry.balance_after))
    _require_party(fact.seller_snapshot)
    _require_party(fact.customer_snapshot)
    if fact.payment_instructions is not None:
        link = fact.payment_instructions.payment_link
        if link is not None:
            validate_payment_link_v2(link)


def _require_party(party: PartySnapshotV2) -> None:
    if (
        not party.legal_name.strip()
        or not party.address.line_one.strip()
        or not party.address.country_code.strip()
    ):
        raise FactIncomplete("structured party name and address are required")


def fact_fingerprint_statement_v1(fact: AccountStatementFactV1) -> str:
    _validate_typed_value(fact, AccountStatementFactV1, "fact")
    return digest_of(
        "dotmac-document-rendering.statement.fact.v1", canonical_value_v2(fact)
    )


def canonical_statement_projection_payload_v1(
    projection: AccountStatementProjectionV1,
) -> bytes:
    _validate_typed_value(projection, AccountStatementProjectionV1, "projection")
    return encode_fields(
        tuple(
            (item.name, canonical_value_v2(getattr(projection, item.name)))
            for item in fields(projection)
            if item.name != "digest"
        )
    )


def statement_projection_digest_v1(projection: AccountStatementProjectionV1) -> str:
    return digest_of(
        "dotmac-document-rendering.statement.projection.v1",
        canonical_statement_projection_payload_v1(projection),
    )


def _rendered(
    path: str, value: object, fact: AccountStatementFactV1, template: DocumentTemplateV1
) -> RenderedValue:
    if isinstance(value, Money):
        _validate_typed_value(value, Money, path)
        with localcontext(_semantic_context_v2()):
            raw = format(
                Decimal(_canonical_decimal_text_v2(value.amount)),
                f".{value.currency.minor_units}f",
            )
        return RenderedValue(
            "money",
            path,
            raw,
            f"{value.currency.code} {raw}",
            value.currency.code,
            value.currency.minor_units,
        )
    if isinstance(value, datetime):
        format_code = (
            template.date_format_code
            if path.endswith(_DATE_PATHS)
            else template.datetime_format_code
        )
        return RenderedValue(
            "datetime",
            path,
            canonical_instant(value),
            value.astimezone(ZoneInfo(fact.timezone)).strftime(format_code),
            timezone=fact.timezone,
            format_code=format_code,
        )
    text = str(value)
    return RenderedValue("text", path, text, text)


def project_statement_v1(
    fact: AccountStatementFactV1,
    selected: SelectedStatementTemplateV1,
    *,
    renderer_code: str,
    renderer_version: str,
    media_type: str,
) -> AccountStatementProjectionV1:
    validate_statement_fact_v1(fact)
    _validate_typed_value(selected, SelectedStatementTemplateV1, "selected_template")
    binding = selected.binding
    if (binding.template_code, binding.template_version) != (
        selected.template.template_code,
        selected.template.template_version,
    ):
        raise FactShapeInvalid("selected template does not match its exact binding")
    if (
        binding.statement_profile_code,
        binding.statement_profile_version,
        binding.media_type,
    ) != (fact.statement_profile_code, fact.statement_profile_version, media_type):
        raise FactShapeInvalid(
            "selected template binding does not exactly match the statement"
        )
    template = selected.template
    labels = {label.key: label.text for label in template.labels}
    sections: list[Section] = []
    decisions = [
        TemplateDecision(path, "absent" if leaf is None else "present")
        for path, leaf in _walk(fact)
    ]

    def field(key: str, path: str, leaf: object) -> LabelledValue:
        return LabelledValue(key, labels[key], _rendered(path, leaf, fact, template))

    def section(key: str, blocks: list[Block]) -> None:
        sections.append(
            Section(
                key,
                len(sections) + 1,
                (StaticText("section_title", labels[key]), *blocks),
            )
        )

    header: list[Block] = [
        StaticText("document_kind", labels["statement"]),
        field("statement_number", "statement_number", fact.statement_number),
        field("state", "statement_state", fact.statement_state),
        field("issue_date", "issued_at", fact.issued_at),
        field("period_start", "period_start", fact.period_start),
        field("period_end", "period_end", fact.period_end),
    ]
    if fact.customer_account_reference is not None:
        header.append(
            field(
                "customer_account",
                "customer_account_reference",
                fact.customer_account_reference,
            )
        )
    section("document", header)

    for role, party in (
        ("seller", fact.seller_snapshot),
        ("customer", fact.customer_snapshot),
    ):
        section(role, _party_blocks(f"{role}_snapshot", party, field))

    section(
        "summary",
        [
            field("opening_balance", "opening_balance", fact.opening_balance),
            field("total_debits", "total_debits", fact.total_debits),
            field("total_credits", "total_credits", fact.total_credits),
            field("closing_balance", "closing_balance", fact.closing_balance),
        ],
    )

    columns = (
        ("entry_number", "position", "position"),
        ("occurred_at", "entry_date", "entry_date"),
        ("entry_kind", "entry_kind", "entry_kind"),
        ("reference", "reference", "reference"),
        ("description", "description", "description"),
        ("debit", "debit", "debit"),
        ("credit", "credit", "credit"),
        ("balance_after", "balance", "balance"),
    )
    rows = tuple(
        tuple(
            _rendered(f"entries[{index}].{name}", getattr(entry, name), fact, template)
            for name, _, _ in columns
        )
        for index, entry in enumerate(fact.entries)
    )
    section(
        "entries",
        [
            Table(
                tuple(ColumnSpec(key, labels[label]) for _, key, label in columns), rows
            )
        ],
    )
    decisions.append(TemplateDecision("entries.rows", str(len(fact.entries))))

    payment = fact.payment_instructions
    if payment is not None:
        blocks: list[Block] = [
            field(
                "method_code", "payment_instructions.method_code", payment.method_code
            )
        ]
        for name in (
            "bank_name",
            "account_name",
            "account_reference",
            "routing_code",
            "narrative",
        ):
            leaf = getattr(payment, name)
            if leaf is not None:
                blocks.append(field(name, f"payment_instructions.{name}", leaf))
        if payment.payment_link is not None:
            # Visible text only: a statement carries no followable URL.
            blocks.append(
                field(
                    "payment_link",
                    "payment_instructions.payment_link.url",
                    payment.payment_link.url,
                )
            )
        section("payment_instructions", blocks)
    decisions.append(
        TemplateDecision(
            "payment_instructions", "omitted" if payment is None else "shown"
        )
    )
    decisions.append(
        TemplateDecision(
            "brand_logo", "omitted" if fact.brand_logo is None else "embedded_data_uri"
        )
    )
    projection = AccountStatementProjectionV1(
        STATEMENT_PROJECTION_CONTRACT_VERSION_V1,
        fact.statement_id,
        fact.fact_version,
        fact_fingerprint_statement_v1(fact),
        fact.statement_profile_code,
        fact.statement_profile_version,
        template.template_code,
        template.template_version,
        renderer_code,
        renderer_version,
        media_type,
        tuple(decisions),
        tuple(sections),
        fact.brand_logo,
        "",
    )
    return replace(projection, digest=statement_projection_digest_v1(projection))


def _party_blocks(
    prefix: str,
    party: PartySnapshotV2,
    field: Callable[[str, str, object], LabelledValue],
) -> list[Block]:
    blocks: list[Block] = [
        field("legal_name", f"{prefix}.legal_name", party.legal_name)
    ]
    if party.trading_name is not None:
        blocks.append(
            field("trading_name", f"{prefix}.trading_name", party.trading_name)
        )
    for name in (
        "line_one",
        "line_two",
        "city",
        "region",
        "postal_code",
        "country_code",
    ):
        leaf = getattr(party.address, name)
        if leaf is not None:
            blocks.append(field(name, f"{prefix}.address.{name}", leaf))
    for index, registered in enumerate(party.registered_identifiers):
        for name in ("identifier_type", "identifier_value", "issuing_country_code"):
            leaf = getattr(registered, name)
            if leaf is not None:
                blocks.append(
                    field(
                        name, f"{prefix}.registered_identifiers[{index}].{name}", leaf
                    )
                )
    return blocks


__all__ = [
    "STATEMENT_PROJECTION_CONTRACT_VERSION_V1",
    "canonical_statement_projection_payload_v1",
    "fact_fingerprint_statement_v1",
    "project_statement_v1",
    "statement_projection_digest_v1",
    "validate_statement_fact_v1",
]
