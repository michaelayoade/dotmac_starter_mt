"""Pure lossless V2 projection and domain-separated shared canonical encoding."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields, is_dataclass, replace
from datetime import datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    FloatOperation,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Underflow,
    localcontext,
)
from enum import Enum
from ipaddress import ip_address
from types import UnionType
from typing import get_args, get_origin, get_type_hints
from urllib.parse import unquote, urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Money
from dotmac_kernel.semantic_encoding import (
    ABSENT,
    canonical_decimal,
    canonical_instant,
    digest_of,
    encode,
    encode_fields,
    encode_ordered,
)

from dotmac_document_rendering.contracts import (
    Block,
    ColumnSpec,
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
from dotmac_document_rendering.contracts_v2 import (
    DiscountV2,
    DocumentLineV2,
    DocumentProjectionV2,
    DueDateBasisV2,
    FrozenPaymentLinkV2,
    FxSnapshotV2,
    InvoiceDocumentFactV2,
    PartySnapshotV2,
    PartyTaxIdentityV2,
    PaymentInstructionsV2,
    PaymentLinkValueV2,
    PostalAddressV2,
    PresentationAssetV2,
    RecordedLineReferenceV2,
    RegisteredIdentifierV2,
    SelectedTemplateV2,
    TaxSnapshotV2,
    _require_utf8_text,
)

PROJECTION_CONTRACT_VERSION_V2 = 2


def _semantic_context_v2() -> Context:
    # Construct every option explicitly; never inherit ambient precision/traps.
    return Context(
        prec=16_384,
        rounding=ROUND_HALF_EVEN,
        Emin=-16_384,
        Emax=16_384,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[
            InvalidOperation,
            DivisionByZero,
            Overflow,
            Underflow,
            Inexact,
            Rounded,
            FloatOperation,
        ],
    )


def _validate_decimal_v2(value: Decimal, path: str) -> None:
    if not value.is_finite():
        raise FactShapeInvalid(f"{path} must be a finite exact decimal")
    parts = value.as_tuple()
    if (
        len(parts.digits) > 4_096
        or not isinstance(parts.exponent, int)
        or not -4_096 <= parts.exponent <= 4_096
    ):
        raise FactShapeInvalid(f"{path} exceeds the decimal coefficient/exponent bound")


def _validate_money_v2(value: Money, path: str) -> None:
    _validate_decimal_v2(value.amount, path)
    scale = value.currency.minor_units
    if type(scale) is not int or not 0 <= scale <= 4_096:
        raise FactShapeInvalid(f"{path} exceeds the currency scale bound")
    parts = value.amount.as_tuple()
    exponent = parts.exponent
    if not isinstance(exponent, int):
        raise FactShapeInvalid(f"{path} must be finite exact money")
    # Count representation digits, without deriving or rounding a monetary value.
    scaled_digits = max(1, len(parts.digits) + exponent + scale)
    if not value.amount.is_zero() and scaled_digits > 4_096:
        raise FactShapeInvalid(f"{path} exceeds the scaled integer digit bound")
    fractional_places = max(0, -exponent - scale)
    if fractional_places and any(parts.digits[-fractional_places:]):
        raise FactShapeInvalid(f"{path} carries precision beyond its currency scale")


def _canonical_decimal_text_v2(value: Decimal) -> str:
    _validate_decimal_v2(value, "decimal presentation")
    try:
        with localcontext(_semantic_context_v2()):
            return canonical_decimal(value)
    except DecimalException as exc:
        raise FactShapeInvalid("decimal cannot be represented exactly") from exc


def canonical_value_v2(value: object) -> bytes:
    """Compose public kernel frames under one explicit exact decimal context."""
    try:
        with localcontext(_semantic_context_v2()):
            return _canonical_value_v2(value)
    except (DecimalException, ValueError) as exc:
        # Public Kernel Money encoding uses integer-to-string conversion, whose
        # process limit can be lowered independently of the decimal context.
        raise FactShapeInvalid(
            "value cannot be encoded within semantic bounds"
        ) from exc


def _canonical_value_v2(value: object) -> bytes:
    """Keep the shared encoder as the sole canonical wire-format owner."""
    if value is None:
        return encode(ABSENT)
    if isinstance(value, Enum):
        return encode(str(value.value))
    if isinstance(value, UUID):
        return encode_fields((("uuid", str(value)),))
    if isinstance(value, TenantScope):
        return encode_fields((("scope", "tenant"), ("tenant_id", str(value.tenant_id))))
    if isinstance(value, PlatformScope):
        return encode_fields((("scope", "platform"),))
    if isinstance(value, Money):
        _validate_typed_value(value, Money, "canonical money")
        return encode(value)
    if isinstance(value, Decimal):
        _validate_decimal_v2(value, "canonical decimal")
        return encode(value)
    if is_dataclass(value) and not isinstance(value, type):
        return encode_fields(
            tuple(
                (item.name, _canonical_value_v2(getattr(value, item.name)))
                for item in fields(value)
            )
        )
    if isinstance(value, tuple):
        return encode_ordered(_canonical_value_v2(item) for item in value)
    return encode(value)


def fact_fingerprint_v2(fact: InvoiceDocumentFactV2) -> str:
    _validate_typed_value(fact, InvoiceDocumentFactV2, "fact")
    return digest_of("dotmac-document-rendering.fact.v2", canonical_value_v2(fact))


def canonical_projection_payload_v2(projection: DocumentProjectionV2) -> bytes:
    _validate_typed_value(projection, DocumentProjectionV2, "projection")
    return encode_fields(
        tuple(
            (item.name, canonical_value_v2(getattr(projection, item.name)))
            for item in fields(projection)
            if item.name != "digest"
        )
    )


def projection_digest_v2(projection: DocumentProjectionV2) -> str:
    return digest_of(
        "dotmac-document-rendering.projection.v2",
        canonical_projection_payload_v2(projection),
    )


def validate_payment_link_v2(link: FrozenPaymentLinkV2) -> None:
    if not all(
        isinstance(value, str) and value.strip()
        for value in (link.url, link.owner, link.snapshot_id, link.snapshot_version)
    ):
        raise FactShapeInvalid(
            "frozen payment link requires owner, snapshot id and version"
        )
    _validate_payment_url_v2(link.url)


def _validate_payment_url_v2(url: str) -> None:
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise FactShapeInvalid(
            "frozen payment link contains controls or invalid authority"
        )
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = parsed.hostname
    except ValueError as exc:
        raise FactShapeInvalid("frozen payment link has malformed authority") from exc
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise FactShapeInvalid(
            "frozen payment link must be HTTPS with a host and no credentials"
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in unquote(url)):
        raise FactShapeInvalid("frozen payment link contains escaped controls")
    if (
        not parsed.netloc
        or parsed.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise FactShapeInvalid("frozen payment link has malformed authority")
    if any(char in host for char in '<>"{}|^`') or "%" in parsed.netloc:
        raise FactShapeInvalid("frozen payment link has malformed authority")
    try:
        ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise FactShapeInvalid("frozen payment link has malformed host") from exc
        labels = ascii_host.removesuffix(".").split(".")
        if len(ascii_host) > 253 or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or any(
                not (char.isascii() and (char.isalnum() or char == "-"))
                for char in label
            )
            for label in labels
        ):
            raise FactShapeInvalid("frozen payment link has malformed host") from None


def _walk(value: object, path: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(value, Money) or not (
        is_dataclass(value) or isinstance(value, tuple)
    ):
        yield path, value
    elif isinstance(value, tuple):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    else:
        for item in fields(value):
            yield from _walk(
                getattr(value, item.name), f"{path}.{item.name}" if path else item.name
            )


def _validate_typed_value(value: object, expected: object, path: str) -> None:
    """Validate every declared leaf before canonicalization or presentation."""
    origin = get_origin(expected)
    arguments = get_args(expected)
    if origin is UnionType:
        if value is None and type(None) in arguments:
            return
        alternatives = tuple(item for item in arguments if item is not type(None))
        if len(alternatives) == 1:
            _validate_typed_value(value, alternatives[0], path)
            return
        for item in alternatives:
            if isinstance(item, type) and isinstance(value, item):
                _validate_typed_value(value, item, path)
                return
        raise FactShapeInvalid(f"{path} has an unsupported typed value")
    if origin is tuple:
        if not isinstance(value, tuple):
            raise FactShapeInvalid(f"{path} must be an immutable tuple")
        for index, item in enumerate(value):
            _validate_typed_value(item, arguments[0], f"{path}[{index}]")
        return
    if expected is int:
        if type(value) is not int:
            raise FactShapeInvalid(f"{path} must be an exact integer")
        return
    if not isinstance(expected, type) or not isinstance(value, expected):
        raise FactShapeInvalid(f"{path} has an unsupported typed value")
    if expected is str and isinstance(value, str):
        _require_utf8_text(value, path)
    if expected is Decimal and isinstance(value, Decimal):
        _validate_decimal_v2(value, path)
    if expected is Money:
        if not isinstance(value, Money) or not isinstance(value.amount, Decimal):
            raise FactShapeInvalid(f"{path} must be exact money")
    if expected is RenderedValue and isinstance(value, RenderedValue):
        if value.minor_units is not None and (
            type(value.minor_units) is not int or not 0 <= value.minor_units <= 4_096
        ):
            raise FactShapeInvalid(f"{path} exceeds the projected currency scale bound")
    if is_dataclass(value):
        if type(value) is not expected:
            if expected is LabelledValue and isinstance(value, PaymentLinkValueV2):
                expected = PaymentLinkValueV2
            else:
                raise FactShapeInvalid(f"{path} has an unsupported snapshot subclass")
        annotations = get_type_hints(expected)
        for item in fields(value):
            _validate_typed_value(
                getattr(value, item.name), annotations[item.name], f"{path}.{item.name}"
            )
        if isinstance(value, Money):
            _validate_money_v2(value, path)


def validate_fact_v2(fact: InvoiceDocumentFactV2) -> None:
    if not isinstance(fact, InvoiceDocumentFactV2):
        raise FactShapeInvalid("fact must use the V2 immutable contract")
    _validate_typed_value(fact, InvoiceDocumentFactV2, "fact")
    if fact.contract_version != 2:
        raise FactVersionUnsupported("only fact contract V2 is accepted")
    if not isinstance(fact.scope, TenantScope | PlatformScope):
        raise FactShapeInvalid("an explicit tenant or platform scope is required")
    if type(fact.minor_units) is not int or not 0 <= fact.minor_units <= 9:
        raise FactShapeInvalid(
            "document money scale must be an integer from zero to nine"
        )
    if (
        not isinstance(fact.currency, str)
        or len(fact.currency) != 3
        or not fact.currency.isascii()
        or not fact.currency.isalpha()
        or fact.currency != fact.currency.upper()
    ):
        raise FactShapeInvalid("document currency must be an uppercase ISO code")
    for name, kind in (
        ("due_date_basis", DueDateBasisV2),
        ("payment_instructions", PaymentInstructionsV2),
        ("brand_asset", PresentationAssetV2),
    ):
        if not isinstance(getattr(fact, name), kind):
            raise FactShapeInvalid(f"{name} has an unsupported snapshot shape")
    basis = fact.due_date_basis
    if (
        basis.status not in {"verified", "unknown_unverified"}
        or not basis.source_authority.strip()
        or not basis.evidence_ref.strip()
    ):
        raise FactShapeInvalid(
            "due date basis requires its frozen qualification evidence"
        )
    if basis.status == "verified" and any(
        value is None
        for value in (
            basis.payment_terms_code,
            basis.payment_terms_version,
            basis.issued_at,
            basis.effective_at,
            basis.timezone,
            basis.derivation_policy,
            basis.derivation_version,
        )
    ):
        raise FactShapeInvalid(
            "verified due date basis requires complete frozen qualification"
        )
    for instant in (basis.issued_at, basis.effective_at):
        if instant is not None and not isinstance(instant, datetime):
            raise FactShapeInvalid("due date basis instants must be timezone aware")
    if fact.due_at is not None and not isinstance(fact.due_at, datetime):
        raise FactShapeInvalid("due_at must be an aware instant or explicit absence")
    if (
        fact.emitter != "dotmac-billing"
        or fact.persisted_contract_version != "billing.invoice.document.fact.v1"
    ):
        raise FactShapeInvalid("unsupported persisted Billing evidence contract")
    if not isinstance(fact.invoice_id, UUID) or not isinstance(
        fact.persisted_fact_id, UUID
    ):
        raise FactShapeInvalid("persisted fact and invoice identity must be UUIDs")
    if type(fact.fact_version) is not int or fact.fact_version <= 0:
        raise FactShapeInvalid("fact version must be positive")
    for name in (
        "document_profile_code",
        "document_profile_version",
        "document_number",
        "series_code",
        "locale",
        "timezone",
        "correlation_id",
    ):
        value = getattr(fact, name)
        if not isinstance(value, str) or not value.strip():
            raise FactIncomplete(f"{name} must be a nonempty string")
    if fact.series_version is not None and (
        not isinstance(fact.series_version, str) or not fact.series_version.strip()
    ):
        raise FactShapeInvalid(
            "series_version must be an explicit opaque version or absent"
        )
    if (
        fact.document_kind not in {"invoice", "receipt", "credit_note"}
        or fact.document_state not in {"issued", "corrected", "cancelled"}
        or fact.source_authority
        not in {"internal", "provider_owned", "external_finance"}
    ):
        raise FactShapeInvalid("invalid frozen document classification")
    digest = fact.original_presentation_model_digest
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise FactShapeInvalid(
            "original Billing digest must remain a bare lowercase SHA-256 digest"
        )
    try:
        ZoneInfo(fact.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise FactShapeInvalid("unknown frozen timezone") from exc
    if fact.brand_asset.status not in {"none", "file"} or (
        fact.brand_asset.status == "file"
    ) != isinstance(fact.brand_asset.file_id, UUID):
        raise FactShapeInvalid("brand asset status and frozen file identity disagree")
    if not fact.lines and fact.document_kind != "receipt":
        raise FactIncomplete("invoice and credit note require lines")
    positions = tuple(line.line_number for line in fact.lines)
    if any(
        type(number) is not int or number <= 0 for number in positions
    ) or positions != tuple(sorted(set(positions))):
        raise FactShapeInvalid(
            "recorded line numbers must be positive, unique and ordered"
        )
    amounts = [fact.subtotal, fact.tax_total, fact.grand_total]
    for line in fact.lines:
        if (
            not isinstance(line, DocumentLineV2)
            or not isinstance(line.recorded_line, RecordedLineReferenceV2)
            or not isinstance(line.quantity, Decimal)
        ):
            raise FactShapeInvalid("recorded line requires the complete typed snapshot")
        amounts.extend(
            (
                line.unit_amount,
                line.pre_tax_amount,
                line.discount_total,
                line.tax_amount,
                line.total_amount,
            )
        )
        for discount in line.discounts:
            if not isinstance(discount, DiscountV2):
                raise FactShapeInvalid("discount requires the complete typed snapshot")
            amounts.append(discount.amount)
        ref = line.recorded_line
        if type(ref.fact_version) is not int or type(ref.line_number) is not int:
            raise FactShapeInvalid(
                "recorded line identity versions must be exact integers"
            )
        if (ref.emitter, ref.fact_id, ref.fact_version, ref.line_number) != (
            fact.emitter,
            fact.persisted_fact_id,
            fact.fact_version,
            line.line_number,
        ):
            raise FactShapeInvalid(
                "recorded line reference does not match its actual parent fact and line"
            )
        if (
            not line.description.strip()
            or not line.unit_code.strip()
            or not line.price_source_version.strip()
        ):
            raise FactIncomplete(
                "recorded line description, unit and price source version are required"
            )
    for party in (fact.seller_snapshot, fact.customer_snapshot):
        if (
            not isinstance(party, PartySnapshotV2)
            or not isinstance(party.address, PostalAddressV2)
            or any(
                not isinstance(identity, RegisteredIdentifierV2)
                for identity in party.registered_identifiers
            )
        ):
            raise FactShapeInvalid("party requires the complete structured snapshot")
        if (
            not party.legal_name.strip()
            or not party.address.line_one.strip()
            or not party.address.country_code.strip()
        ):
            raise FactIncomplete("structured party name and address are required")
    for tax in fact.tax_snapshots:
        if not isinstance(tax, TaxSnapshotV2) or not isinstance(tax.rate, Decimal):
            raise FactShapeInvalid("tax requires the complete typed snapshot")
        amounts.extend((tax.taxable_basis, tax.tax_amount))
    if any(not isinstance(amount, Money) for amount in amounts):
        raise FactShapeInvalid("all frozen financial amounts must be exact Money")
    if fact.fx_snapshot is not None and (
        not isinstance(fact.fx_snapshot, FxSnapshotV2)
        or not isinstance(fact.fx_snapshot.rate, Decimal)
    ):
        raise FactShapeInvalid("FX requires the complete typed snapshot")
    if fact.fx_snapshot is not None and any(
        not isinstance(instant, datetime)
        for instant in (fact.fx_snapshot.observed_at, fact.fx_snapshot.effective_at)
    ):
        raise FactShapeInvalid("FX instants must be timezone aware")
    for identity in fact.party_tax_identities:
        if not isinstance(identity, PartyTaxIdentityV2):
            raise FactShapeInvalid("tax identity requires the complete typed snapshot")
        if identity.party_role not in {"seller", "customer"}:
            raise FactShapeInvalid("tax identity must name its frozen party role")
    for path, value in _walk(fact):
        if isinstance(value, Money) and (
            value.currency.code != fact.currency
            or value.currency.minor_units != fact.minor_units
            or not value.amount.is_finite()
        ):
            raise FactShapeInvalid(
                f"{path} does not match exact document currency and scale"
            )
        if isinstance(value, Decimal) and not value.is_finite():
            raise FactShapeInvalid(f"{path} must be a finite exact decimal")
        if isinstance(value, float):
            raise FactShapeInvalid(f"{path} must never be floating point")
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise FactShapeInvalid(f"{path} must be timezone aware")
    for name in ("recorded_at", "issued_at", "frozen_at"):
        if not isinstance(getattr(fact, name), datetime):
            raise FactShapeInvalid(f"{name} must be an aware instant")
    if fact.payment_instructions.payment_link is not None:
        validate_payment_link_v2(fact.payment_instructions.payment_link)


def _rendered(
    path: str, value: object, fact: InvoiceDocumentFactV2, selected: SelectedTemplateV2
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
            selected.template.date_format_code
            if path in {"issued_at", "due_at"}
            else selected.template.datetime_format_code
        )
        text = value.astimezone(ZoneInfo(fact.timezone)).strftime(format_code)
        return RenderedValue(
            "datetime",
            path,
            canonical_instant(value),
            text,
            timezone=fact.timezone,
            format_code=format_code,
        )
    if isinstance(value, Enum):
        value = value.value
    text = (
        _canonical_decimal_text_v2(value) if isinstance(value, Decimal) else str(value)
    )
    return RenderedValue(
        "decimal" if isinstance(value, Decimal) else "text", path, text, text
    )


def project_invoice_v2(
    fact: InvoiceDocumentFactV2,
    selected: SelectedTemplateV2,
    *,
    renderer_code: str,
    renderer_version: str,
    media_type: str,
) -> DocumentProjectionV2:
    validate_fact_v2(fact)
    _validate_typed_value(selected, SelectedTemplateV2, "selected_template")
    binding = selected.binding
    if (binding.template_code, binding.template_version) != (
        selected.template.template_code,
        selected.template.template_version,
    ):
        raise FactShapeInvalid("selected template does not match its exact binding")
    if (
        binding.document_profile_code,
        binding.document_profile_version,
        binding.media_type,
    ) != (fact.document_profile_code, fact.document_profile_version, media_type):
        raise FactShapeInvalid(
            "selected template binding does not exactly match the fact"
        )
    labels = {label.key: label.text for label in selected.template.labels}
    sections: list[Section] = []
    decisions = [
        TemplateDecision(path, "absent" if leaf is None else "present")
        for path, leaf in _walk(fact)
    ]

    def value(path: str, leaf: object) -> RenderedValue:
        return _rendered(path, leaf, fact, selected)

    def field(key: str, path: str, leaf: object) -> LabelledValue:
        return LabelledValue(key, labels[key], value(path, leaf))

    def section(key: str, blocks: list[Block]) -> None:
        sections.append(
            Section(
                key,
                len(sections) + 1,
                (StaticText("section_title", labels[key]), *blocks),
            )
        )

    header: list[Block] = [
        StaticText("document_kind", labels[fact.document_kind]),
        field("document_number", "document_number", fact.document_number),
        field("state", "document_state", fact.document_state),
        field("issue_date", "issued_at", fact.issued_at),
    ]
    if fact.due_at is not None:
        header.append(field("due_date", "due_at", fact.due_at))
    decisions.append(
        TemplateDecision("due_date", "omitted" if fact.due_at is None else "shown")
    )
    section("document", header)

    for role, party in (
        ("seller", fact.seller_snapshot),
        ("customer", fact.customer_snapshot),
    ):
        prefix = f"{role}_snapshot"
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
                            name,
                            f"{prefix}.registered_identifiers[{index}].{name}",
                            leaf,
                        )
                    )
        for index, tax_identity in enumerate(fact.party_tax_identities):
            if tax_identity.party_role == role:
                for name in ("identity_type", "identity_value", "country_code"):
                    blocks.append(
                        field(
                            name,
                            f"party_tax_identities[{index}].{name}",
                            getattr(tax_identity, name),
                        )
                    )
        section(role, blocks)

    line_columns = (
        ("line_number", "position"),
        ("description", "description"),
        ("quantity", "quantity"),
        ("unit_code", "unit"),
        ("unit_amount", "unit_amount"),
        ("pre_tax_amount", "pre_tax_amount"),
        ("discount_total", "discount_total"),
        ("tax_amount", "tax_amount"),
        ("total_amount", "line_total"),
    )
    line_rows = tuple(
        tuple(
            value(f"lines[{index}].{name}", getattr(line, name))
            for name, _ in line_columns
        )
        for index, line in enumerate(fact.lines)
    )
    section(
        "line_items",
        [
            Table(
                tuple(ColumnSpec(name, labels[label]) for name, label in line_columns),
                line_rows,
            )
        ],
    )
    decisions.append(TemplateDecision("lines.rows", str(len(fact.lines))))

    discount_rows = []
    for line_index, line in enumerate(fact.lines):
        for index, discount in enumerate(line.discounts):
            prefix = f"lines[{line_index}].discounts[{index}]"
            discount_rows.append(
                (
                    value(f"lines[{line_index}].line_number", line.line_number),
                    value(f"{prefix}.discount_code", discount.discount_code),
                    value(f"{prefix}.description", discount.description),
                    value(f"{prefix}.amount", discount.amount),
                )
            )
    if discount_rows:
        section(
            "discounts",
            [
                Table(
                    tuple(
                        ColumnSpec(key, labels[key])
                        for key in (
                            "position",
                            "discount_code",
                            "description",
                            "discount",
                        )
                    ),
                    tuple(discount_rows),
                )
            ],
        )
    decisions.append(
        TemplateDecision("discounts", "shown" if discount_rows else "omitted")
    )

    tax_columns = (
        ("treatment_code", "tax_treatment"),
        ("jurisdiction_code", "tax_jurisdiction"),
        ("rate", "rate"),
        ("taxable_basis", "taxable_basis"),
        ("tax_amount", "tax_amount"),
    )
    if fact.tax_snapshots:
        rows = tuple(
            tuple(
                value(f"tax_snapshots[{index}].{name}", getattr(tax, name))
                for name, _ in tax_columns
            )
            for index, tax in enumerate(fact.tax_snapshots)
        )
        section(
            "taxes",
            [
                Table(
                    tuple(
                        ColumnSpec(name, labels[label]) for name, label in tax_columns
                    ),
                    rows,
                )
            ],
        )
    decisions.append(
        TemplateDecision("taxes", "shown" if fact.tax_snapshots else "omitted")
    )
    section(
        "totals",
        [
            field("subtotal", "subtotal", fact.subtotal),
            field("tax_total", "tax_total", fact.tax_total),
            field("total", "grand_total", fact.grand_total),
        ],
    )

    payment = fact.payment_instructions
    blocks = [
        field("method_code", "payment_instructions.method_code", payment.method_code)
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
        link = payment.payment_link
        blocks.append(
            PaymentLinkValueV2(
                "payment_link",
                labels["payment_link"],
                value("payment_instructions.payment_link.url", link.url),
                link.url,
            )
        )
    decisions.append(
        TemplateDecision("payment_link", "shown" if payment.payment_link else "omitted")
    )
    section("payment_instructions", blocks)

    if fact.fx_snapshot is not None:
        fx = fact.fx_snapshot
        section(
            "fx",
            [
                field(name, f"fx_snapshot.{name}", getattr(fx, name))
                for name in (
                    "base_currency",
                    "quote_currency",
                    "rate",
                    "rate_purpose",
                    "observed_at",
                    "effective_at",
                )
            ],
        )
    decisions.append(TemplateDecision("brand_asset", "reference_only_not_fetched"))
    projection = DocumentProjectionV2(
        2,
        fact.invoice_id,
        fact.fact_version,
        fact_fingerprint_v2(fact),
        fact.document_profile_code,
        fact.document_profile_version,
        selected.template.template_code,
        selected.template.template_version,
        renderer_code,
        renderer_version,
        media_type,
        tuple(decisions),
        tuple(sections),
        "",
    )
    return replace(projection, digest=projection_digest_v2(projection))


__all__ = [
    "PROJECTION_CONTRACT_VERSION_V2",
    "canonical_projection_payload_v2",
    "fact_fingerprint_v2",
    "projection_digest_v2",
    "project_invoice_v2",
    "validate_fact_v2",
    "validate_payment_link_v2",
]
