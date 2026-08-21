"""Pure stage-two projection: immutable facts in, canonical meaning out."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of

from dotmac_document_rendering.contracts import (
    Block,
    ColumnSpec,
    DocumentKind,
    DocumentProjectionV1,
    DocumentState,
    ExactAmount,
    FactIncomplete,
    FactShapeInvalid,
    FactVersionUnsupported,
    InvoiceDocumentFactV1,
    LabelledValue,
    PartySnapshotV1,
    RenderedValue,
    Section,
    SelectedTemplateV1,
    SourceAuthority,
    StaticText,
    Table,
    TemplateDecision,
)
from dotmac_document_rendering.templates import label_map

PROJECTION_CONTRACT_VERSION = 1


class _Positioned(Protocol):
    @property
    def position(self) -> int: ...


def _canonical(value: object) -> object:
    if isinstance(value, TenantScope):
        return {"kind": "tenant", "tenant_id": str(value.tenant_id)}
    if isinstance(value, PlatformScope):
        return {"kind": "platform"}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _canonical(getattr(value, item.name)) for item in fields(value)
        }
    if isinstance(value, tuple | list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    return value


def canonical_fact_payload(fact: InvoiceDocumentFactV1) -> dict[str, object]:
    canonical = _canonical(fact)
    if not isinstance(canonical, dict):
        raise TypeError("fact canonicalization must produce an object")
    return canonical


def fact_fingerprint(fact: InvoiceDocumentFactV1) -> str:
    return fingerprint_of(canonical_fact_payload(fact))


def canonical_projection_payload(
    projection: DocumentProjectionV1,
) -> dict[str, object]:
    canonical = _canonical(projection)
    if not isinstance(canonical, dict):
        raise TypeError("projection canonicalization must produce an object")
    canonical.pop("digest", None)
    return canonical


def projection_digest(projection: DocumentProjectionV1) -> str:
    return fingerprint_of(canonical_projection_payload(projection))


def _required(value: str, field_name: str) -> None:
    if not value.strip():
        raise FactIncomplete(f"fact field {field_name!r} is required")


def _aware_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FactShapeInvalid(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise FactShapeInvalid(f"{field_name} must be UTC")


def _exact_decimal_text(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise FactShapeInvalid(f"{field_name} must be an exact decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise FactShapeInvalid(f"{field_name} must be an exact decimal string") from exc
    if not parsed.is_finite():
        raise FactShapeInvalid(f"{field_name} must be finite")


def _positions(items: Sequence[_Positioned], field_name: str) -> None:
    positions = tuple(item.position for item in items)
    if positions != tuple(sorted(positions)) or len(set(positions)) != len(positions):
        raise FactShapeInvalid(
            f"{field_name} positions must be unique and already ordered"
        )
    if any(position <= 0 for position in positions):
        raise FactShapeInvalid(f"{field_name} positions must be positive")


def _amounts(fact: InvoiceDocumentFactV1) -> tuple[ExactAmount, ...]:
    values = [fact.subtotal, fact.tax_total, fact.total]
    for line in fact.lines:
        values.extend((line.unit_amount, line.line_total))
    for discount in fact.discounts:
        values.append(discount.amount)
    for tax in fact.tax_lines:
        values.extend((tax.taxable_basis, tax.amount))
    return tuple(values)


def validate_fact(fact: InvoiceDocumentFactV1) -> None:
    if fact.contract_version != 1:
        raise FactVersionUnsupported(
            f"fact contract version {fact.contract_version} is not accepted"
        )
    if fact.fact_version <= 0 or fact.document_profile_version <= 0:
        raise FactShapeInvalid("fact and document-profile versions must be positive")
    if not isinstance(fact.scope, TenantScope | PlatformScope):
        raise FactShapeInvalid("scope must be TenantScope or PlatformScope")
    if not isinstance(fact.document_kind, DocumentKind):
        raise FactShapeInvalid("document_kind is not a declared DocumentKind")
    if not isinstance(fact.document_state, DocumentState):
        raise FactShapeInvalid("document_state is not a declared DocumentState")
    if not isinstance(fact.source_authority, SourceAuthority):
        raise FactShapeInvalid("source_authority is not a declared SourceAuthority")
    for field_name in (
        "document_number",
        "document_series_code",
        "document_series_version",
        "currency",
        "payment_terms",
        "locale",
        "timezone",
        "document_profile_code",
        "source_system",
        "source_record_id",
        "source_record_version",
        "correlation_id",
    ):
        _required(str(getattr(fact, field_name)), field_name)
    _required(fact.seller.legal_name, "seller.legal_name")
    _required(fact.customer.legal_name, "customer.legal_name")
    for field_name in ("emitted_at", "issued_at", "frozen_at"):
        _aware_utc(getattr(fact, field_name), field_name)
    try:
        ZoneInfo(fact.timezone)
    except ZoneInfoNotFoundError as exc:
        raise FactShapeInvalid(f"unknown IANA timezone {fact.timezone!r}") from exc
    if (
        len(fact.currency) != 3
        or fact.currency != fact.currency.upper()
        or not fact.currency.isalpha()
    ):
        raise FactShapeInvalid("currency must be a three-letter uppercase ISO code")
    if not 0 <= fact.minor_units <= 9:
        raise FactShapeInvalid("minor_units must be between zero and nine")
    _positions(fact.lines, "lines")
    _positions(fact.discounts, "discounts")
    _positions(fact.tax_lines, "tax_lines")
    if fact.document_kind is not DocumentKind.RECEIPT and not fact.lines:
        raise FactIncomplete("invoice and credit-note facts require at least one line")
    for index, line in enumerate(fact.lines):
        for field_name in (
            "description",
            "unit",
            "applied_price_code",
            "applied_price_version",
            "source_code",
            "source_version",
        ):
            _required(str(getattr(line, field_name)), f"lines[{index}].{field_name}")
        _exact_decimal_text(line.quantity, f"lines[{index}].quantity")
    for index, discount in enumerate(fact.discounts):
        for field_name in ("description", "source_code", "source_version"):
            _required(
                str(getattr(discount, field_name)),
                f"discounts[{index}].{field_name}",
            )
    for index, tax in enumerate(fact.tax_lines):
        for field_name in (
            "label",
            "treatment",
            "jurisdiction",
            "policy_code",
            "policy_version",
        ):
            _required(str(getattr(tax, field_name)), f"tax_lines[{index}].{field_name}")
        for component_index, component in enumerate(tax.rate_components):
            _required(
                component.code,
                f"tax_lines[{index}].rate_components[{component_index}].code",
            )
            _exact_decimal_text(
                component.rate,
                f"tax_lines[{index}].rate_components[{component_index}].rate",
            )
    if fact.payment_instructions:
        for field_name in ("bank_name", "account_name", "account_number"):
            _required(
                str(getattr(fact.payment_instructions, field_name)),
                f"payment_instructions.{field_name}",
            )
    if fact.fx_observation:
        observation = fact.fx_observation
        for field_name in (
            "observation_id",
            "observation_version",
            "source_currency",
            "target_currency",
            "provenance",
        ):
            _required(
                str(getattr(observation, field_name)), f"fx_observation.{field_name}"
            )
        _exact_decimal_text(observation.rate, "fx_observation.rate")
        _aware_utc(observation.observed_at, "fx_observation.observed_at")
        _aware_utc(observation.effective_at, "fx_observation.effective_at")
    for amount in _amounts(fact):
        if amount.currency != fact.currency or amount.minor_units != fact.minor_units:
            raise FactShapeInvalid(
                "every amount must carry the fact currency and minor-unit scale"
            )
    if fact.document_kind is DocumentKind.CREDIT_NOTE:
        if fact.tax_lines:
            raise FactShapeInvalid("credit notes must not carry tax lines")
        if Decimal(fact.tax_total.amount) != Decimal("0"):
            raise FactShapeInvalid("credit-note tax_total must be zero")
        if Decimal(fact.subtotal.amount) != Decimal(fact.total.amount):
            raise FactShapeInvalid("credit-note subtotal must equal total")


def _money(amount: ExactAmount, source_field: str) -> RenderedValue:
    value = Decimal(amount.amount)
    decimal_text = format(value, f".{amount.minor_units}f")
    text = f"{amount.currency} {decimal_text}"
    return RenderedValue(
        kind="money",
        source_field=source_field,
        raw=text,
        text=text,
        currency=amount.currency,
        minor_units=amount.minor_units,
    )


def _quantity(value: str, source_field: str) -> RenderedValue:
    _exact_decimal_text(value, source_field)
    parsed = Decimal(value)
    rendered = format(parsed, "f")
    return RenderedValue(
        kind="quantity", source_field=source_field, raw=rendered, text=rendered
    )


def _value(kind: str, value: object, source_field: str) -> RenderedValue:
    rendered = str(value)
    return RenderedValue(
        kind=kind, source_field=source_field, raw=rendered, text=rendered
    )


def _date_value(value: date, source_field: str, format_code: str) -> RenderedValue:
    return RenderedValue(
        kind="date",
        source_field=source_field,
        raw=value.isoformat(),
        text=value.strftime(format_code),
        format_code=format_code,
    )


def _datetime_value(
    value: datetime,
    source_field: str,
    *,
    timezone: str,
    format_code: str,
) -> RenderedValue:
    localized = value.astimezone(ZoneInfo(timezone))
    return RenderedValue(
        kind="datetime",
        source_field=source_field,
        raw=localized.isoformat(),
        text=localized.strftime(format_code),
        timezone=timezone,
        format_code=format_code,
    )


def _labelled(
    labels: dict[str, str], label_key: str, value: RenderedValue
) -> LabelledValue:
    return LabelledValue(label_key=label_key, label_text=labels[label_key], value=value)


def _party_section(
    *,
    key: str,
    position: int,
    source_prefix: str,
    party: PartySnapshotV1,
    labels: dict[str, str],
) -> Section:
    legal_name = party.legal_name
    address_lines = party.address_lines
    registered_identifier = party.registered_identifier
    tax_identifier = party.tax_identifier
    contact = party.contact
    blocks: list[Block] = [
        StaticText(key="section_title", text=labels[key]),
        _labelled(
            labels,
            "legal_name",
            _value("text", legal_name, f"{source_prefix}.legal_name"),
        ),
    ]
    for index, address_line in enumerate(address_lines):
        blocks.append(
            _labelled(
                labels,
                "address",
                _value(
                    "text",
                    address_line,
                    f"{source_prefix}.address_lines[{index}]",
                ),
            )
        )
    for label_key, value, field_name in (
        ("registered_identifier", registered_identifier, "registered_identifier"),
        ("tax_identifier", tax_identifier, "tax_identifier"),
        ("contact", contact, "contact"),
    ):
        if value:
            blocks.append(
                _labelled(
                    labels,
                    label_key,
                    _value("text", value, f"{source_prefix}.{field_name}"),
                )
            )
    return Section(key=key, position=position, blocks=tuple(blocks))


def project_invoice(
    fact: InvoiceDocumentFactV1,
    selected: SelectedTemplateV1,
    *,
    renderer_code: str,
    renderer_version: str,
    media_type: str,
) -> DocumentProjectionV1:
    """Build the complete semantic document without a clock, lookup, or session."""

    validate_fact(fact)
    if selected.binding.media_type != media_type:
        raise FactShapeInvalid(
            "selected template media type does not match the request"
        )
    labels = dict(label_map(selected.template))
    template = selected.template

    header = Section(
        key="header",
        position=1,
        blocks=(
            _labelled(
                labels,
                "document",
                _value(
                    "text",
                    labels[fact.document_kind.value],
                    "document_kind",
                ),
            ),
            _labelled(
                labels,
                "document_number",
                _value("identifier", fact.document_number, "document_number"),
            ),
            _labelled(
                labels,
                "state",
                _value("text", fact.document_state.value, "document_state"),
            ),
            _labelled(
                labels,
                "issue_date",
                _datetime_value(
                    fact.issued_at,
                    "issued_at",
                    timezone=fact.timezone,
                    format_code=template.datetime_format_code,
                ),
            ),
            _labelled(
                labels,
                "due_date",
                _date_value(fact.due_date, "due_date", template.date_format_code),
            ),
        ),
    )
    seller = _party_section(
        key="seller",
        position=2,
        source_prefix="seller",
        party=fact.seller,
        labels=labels,
    )
    customer = _party_section(
        key="customer",
        position=3,
        source_prefix="customer",
        party=fact.customer,
        labels=labels,
    )

    line_rows: list[tuple[RenderedValue, ...]] = []
    for index, line in enumerate(fact.lines):
        prefix = f"lines[{index}]"
        line_rows.append(
            (
                _value("identifier", line.position, f"{prefix}.position"),
                _value("text", line.description, f"{prefix}.description"),
                _quantity(line.quantity, f"{prefix}.quantity"),
                _value("text", line.unit, f"{prefix}.unit"),
                _money(line.unit_amount, f"{prefix}.unit_amount"),
                _money(line.line_total, f"{prefix}.line_total"),
            )
        )
    line_items = Section(
        key="line_items",
        position=4,
        blocks=(
            StaticText(key="section_title", text=labels["line_items"]),
            Table(
                columns=tuple(
                    ColumnSpec(key=key, label_text=labels[key])
                    for key in (
                        "position",
                        "description",
                        "quantity",
                        "unit",
                        "unit_amount",
                        "line_total",
                    )
                ),
                rows=tuple(line_rows),
            ),
        ),
    )

    sections: list[Section] = [header, seller, customer, line_items]
    decisions = [
        TemplateDecision(
            key="brand_asset",
            outcome="referenced" if fact.brand_asset_id else "omitted",
        ),
        TemplateDecision(
            key="discount_rows", outcome="shown" if fact.discounts else "omitted"
        ),
        TemplateDecision(
            key="payment_instructions",
            outcome="shown" if fact.payment_instructions else "omitted",
        ),
        TemplateDecision(
            key="tax_rows", outcome="shown" if fact.tax_lines else "omitted"
        ),
    ]

    next_position = 5
    if fact.discounts:
        discount_rows = tuple(
            (
                _value(
                    "text",
                    discount.description,
                    f"discounts[{index}].description",
                ),
                _money(discount.amount, f"discounts[{index}].amount"),
            )
            for index, discount in enumerate(fact.discounts)
        )
        sections.append(
            Section(
                key="discounts",
                position=next_position,
                blocks=(
                    StaticText(key="section_title", text=labels["discounts"]),
                    Table(
                        columns=(
                            ColumnSpec("discount", labels["discount"]),
                            ColumnSpec("line_total", labels["line_total"]),
                        ),
                        rows=discount_rows,
                    ),
                ),
            )
        )
        next_position += 1

    if fact.tax_lines:
        tax_rows = tuple(
            (
                _value("text", tax.label, f"tax_lines[{index}].label"),
                _value(
                    "text",
                    tax.treatment,
                    f"tax_lines[{index}].treatment",
                ),
                _value(
                    "text",
                    tax.jurisdiction,
                    f"tax_lines[{index}].jurisdiction",
                ),
                _value(
                    "text",
                    "; ".join(
                        f"{component.code} {component.rate}"
                        for component in tax.rate_components
                    ),
                    f"tax_lines[{index}].rate_components",
                ),
                _money(
                    tax.taxable_basis,
                    f"tax_lines[{index}].taxable_basis",
                ),
                _money(tax.amount, f"tax_lines[{index}].amount"),
            )
            for index, tax in enumerate(fact.tax_lines)
        )
        sections.append(
            Section(
                key="taxes",
                position=next_position,
                blocks=(
                    StaticText(key="section_title", text=labels["taxes"]),
                    Table(
                        columns=(
                            ColumnSpec("tax", labels["tax"]),
                            ColumnSpec("tax_treatment", labels["tax_treatment"]),
                            ColumnSpec("tax_jurisdiction", labels["tax_jurisdiction"]),
                            ColumnSpec("tax_rates", labels["tax_rates"]),
                            ColumnSpec("taxable_basis", labels["taxable_basis"]),
                            ColumnSpec("tax_amount", labels["tax_amount"]),
                        ),
                        rows=tax_rows,
                    ),
                ),
            )
        )
        next_position += 1

    sections.append(
        Section(
            key="totals",
            position=next_position,
            blocks=(
                StaticText(key="section_title", text=labels["totals"]),
                _labelled(labels, "subtotal", _money(fact.subtotal, "subtotal")),
                _labelled(labels, "tax_total", _money(fact.tax_total, "tax_total")),
                _labelled(labels, "total", _money(fact.total, "total")),
            ),
        )
    )
    next_position += 1
    sections.append(
        Section(
            key="payment_terms",
            position=next_position,
            blocks=(
                _labelled(
                    labels,
                    "payment_terms",
                    _value("text", fact.payment_terms, "payment_terms"),
                ),
            ),
        )
    )
    next_position += 1

    if fact.payment_instructions:
        instructions = fact.payment_instructions
        blocks: list[Block] = [
            StaticText(key="section_title", text=labels["payment_instructions"]),
            _labelled(
                labels,
                "bank_name",
                _value(
                    "text",
                    instructions.bank_name,
                    "payment_instructions.bank_name",
                ),
            ),
            _labelled(
                labels,
                "account_name",
                _value(
                    "text",
                    instructions.account_name,
                    "payment_instructions.account_name",
                ),
            ),
            _labelled(
                labels,
                "account_number",
                _value(
                    "identifier",
                    instructions.account_number,
                    "payment_instructions.account_number",
                ),
            ),
        ]
        if instructions.sort_code:
            blocks.append(
                _labelled(
                    labels,
                    "sort_code",
                    _value(
                        "identifier",
                        instructions.sort_code,
                        "payment_instructions.sort_code",
                    ),
                )
            )
        sections.append(
            Section(
                key="payment_instructions",
                position=next_position,
                blocks=tuple(blocks),
            )
        )

    projection = DocumentProjectionV1(
        projection_contract_version=PROJECTION_CONTRACT_VERSION,
        invoice_id=fact.invoice_id,
        fact_version=fact.fact_version,
        document_profile_code=fact.document_profile_code,
        document_profile_version=fact.document_profile_version,
        template_code=selected.binding.template_code,
        template_version=selected.binding.template_version,
        renderer_code=renderer_code,
        renderer_version=renderer_version,
        media_type=media_type,
        template_decisions=tuple(sorted(decisions, key=lambda item: item.key)),
        sections=tuple(sections),
        digest="",
    )
    return replace(projection, digest=projection_digest(projection))


def _resolve_source(fact: object, source_field: str) -> object:
    current = fact
    for part in source_field.split("."):
        if "[" in part:
            name, raw_index = part[:-1].split("[", 1)
            current = getattr(current, name)
            current = current[int(raw_index)]
        else:
            current = getattr(current, part)
    return current


def validate_projection_source_fields(
    fact: InvoiceDocumentFactV1,
    projection: DocumentProjectionV1,
) -> tuple[str, ...]:
    """Structural I8 guard: numeric/date values must name their exact fact field."""

    problems: list[str] = []
    guarded_kinds = {"money", "quantity", "date", "datetime"}
    for section in projection.sections:
        for block in section.blocks:
            for value in block.rendered_values():
                if value.kind not in guarded_kinds:
                    continue
                if not value.source_field:
                    problems.append(
                        f"{section.key}: {value.kind} value has no source_field"
                    )
                    continue
                try:
                    source = _resolve_source(fact, value.source_field)
                except (AttributeError, IndexError, ValueError) as exc:
                    problems.append(
                        f"{section.key}: source_field {value.source_field!r} does not "
                        f"resolve ({exc})"
                    )
                    continue
                if value.kind == "money":
                    if not isinstance(source, ExactAmount):
                        problems.append(f"{value.source_field!r} is not an ExactAmount")
                    elif value.raw != _money(source, value.source_field).raw:
                        problems.append(
                            f"{value.source_field!r} changed its exact amount"
                        )
                elif value.kind == "quantity":
                    if value.raw != _quantity(str(source), value.source_field).raw:
                        problems.append(f"{value.source_field!r} changed its quantity")
                elif value.kind == "date":
                    if not isinstance(source, date) or isinstance(source, datetime):
                        problems.append(f"{value.source_field!r} is not a date")
                    elif value.raw != source.isoformat():
                        problems.append(f"{value.source_field!r} changed its date")
                elif value.kind == "datetime":
                    if not isinstance(source, datetime) or not value.timezone:
                        problems.append(f"{value.source_field!r} is not a datetime")
                    elif (
                        value.raw
                        != source.astimezone(ZoneInfo(value.timezone)).isoformat()
                    ):
                        problems.append(f"{value.source_field!r} changed its datetime")
    return tuple(problems)


__all__ = [
    "PROJECTION_CONTRACT_VERSION",
    "canonical_fact_payload",
    "canonical_projection_payload",
    "fact_fingerprint",
    "project_invoice",
    "projection_digest",
    "validate_fact",
    "validate_projection_source_fields",
]
