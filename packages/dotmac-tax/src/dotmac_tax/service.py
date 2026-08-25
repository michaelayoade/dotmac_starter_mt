"""Flush-only owner for tax policy, determinations, reports and returns."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from uuid import UUID, uuid4

from dotmac_kernel.money import Currency, Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_tax.contracts import (
    StatutoryReportBoxInput,
    TaxAuthorityInput,
    TaxDeterminationComponentV1,
    TaxDeterminationLineV1,
    TaxDeterminationSetV1,
    TaxFact,
    TaxJurisdictionInput,
    TaxRuleInput,
    TaxSubjectClassificationInput,
)
from dotmac_tax.models import (
    StatutoryReport,
    StatutoryReportBox,
    StatutoryReportDefinition,
    StatutoryReportValue,
    TaxAuthority,
    TaxCode,
    TaxDetermination,
    TaxDeterminationLine,
    TaxDeterminationSet,
    TaxFilingObligation,
    TaxJurisdiction,
    TaxReturn,
    TaxReturnEvent,
    TaxRule,
    TaxRuleBand,
    TaxSubjectClassification,
)


class TaxNotFound(LookupError):
    """A tenant-local tax record does not exist."""


class TaxConflict(ValueError):
    """A tax identity or immutable evidence conflicts with existing data."""


class TaxRuleViolation(ValueError):
    """A tax policy, determination or filing transition is invalid."""


@dataclass(frozen=True, slots=True)
class _ApplicableTaxRule:
    rule: TaxRule
    party_category: str | None
    supply_category: str | None
    place_code: str | None
    party_classification: TaxSubjectClassification | None
    supply_classification: TaxSubjectClassification | None
    place_classification: TaxSubjectClassification | None
    tax_code: str


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise TaxRuleViolation(f"{label} must not be blank")
    return cleaned


def _country(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 2 or not code.isalpha() or not code.isascii():
        raise TaxRuleViolation("country code must be a two-letter code")
    return code


def _authority(db: Session, tenant_id: UUID, authority_id: UUID) -> TaxAuthority:
    row = db.scalar(
        select(TaxAuthority).where(
            TaxAuthority.tenant_id == tenant_id, TaxAuthority.id == authority_id
        )
    )
    if row is None:
        raise TaxNotFound("tax authority not found")
    return row


def _jurisdiction(
    db: Session, tenant_id: UUID, jurisdiction_id: UUID
) -> TaxJurisdiction:
    row = db.scalar(
        select(TaxJurisdiction).where(
            TaxJurisdiction.tenant_id == tenant_id,
            TaxJurisdiction.id == jurisdiction_id,
        )
    )
    if row is None:
        raise TaxNotFound("tax jurisdiction not found")
    return row


def _code(db: Session, tenant_id: UUID, tax_code_id: UUID) -> TaxCode:
    row = db.scalar(
        select(TaxCode).where(TaxCode.tenant_id == tenant_id, TaxCode.id == tax_code_id)
    )
    if row is None:
        raise TaxNotFound("tax code not found")
    return row


def _currency_matches(currency: Currency, code: str, minor_units: int) -> bool:
    return currency.code == code and currency.minor_units == minor_units


def _round(value: Decimal, minor_units: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-minor_units), rounding=ROUND_HALF_UP)


def _require_aware_input(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TaxRuleViolation("determined_at must be timezone-aware")


def _require_aware_persisted(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TaxConflict(f"persisted {label} must be timezone-aware")
    return value


def _fixed_decimal(value: Decimal, scale: int, label: str) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TaxConflict(f"persisted {label} must be a finite Decimal")
    normalized = value.quantize(Decimal(1).scaleb(-scale))
    if normalized != value:
        raise TaxConflict(f"persisted {label} exceeds its storage scale")
    return f"{normalized:.{scale}f}"


def _result_value(value: object, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        aware = _require_aware_persisted(value, label)
        return aware.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise TaxConflict(f"persisted {label} has unsupported result content")


def _result_content_fingerprint(row: TaxDeterminationSet) -> str:
    """Digest the full normalized set/component/line result as ``rv1``."""

    pairs: list[tuple[str, str | None]] = []

    def add(key: str, value: object) -> None:
        pairs.append((key, _result_value(value, key)))

    def money(key: str, value: Decimal) -> None:
        pairs.append((key, _fixed_decimal(value, 6, key)))

    add("set.id", row.id)
    add("set.tenant_id", row.tenant_id)
    add("set.jurisdiction_id", row.jurisdiction_id)
    add("set.occurred_on", row.occurred_on)
    add("set.fact_kind", row.fact_kind)
    add("set.recognition_basis_code", row.recognition_basis_code)
    add("set.transaction_side", row.transaction_side)
    money("set.source_amount", row.source_amount)
    money("set.net_amount", row.net_amount)
    money("set.tax_amount", row.tax_amount)
    money("set.gross_amount", row.gross_amount)
    add("set.currency_code", row.currency_code)
    add("set.minor_units", row.minor_units)
    add("set.source_ref", row.source_ref)
    add("set.source_version", row.source_version)
    add("set.source_fingerprint", row.source_fingerprint)
    add("set.evidence_ref", row.evidence_ref)
    add("set.counterparty_ref", row.counterparty_ref)
    add("set.supply_ref", row.supply_ref)
    add("set.place_ref", row.place_ref)
    add("set.determined_at", row.determined_at)

    for component_index, component in enumerate(row.components, start=1):
        prefix = f"component.{component_index}"
        add(f"{prefix}.id", component.id)
        add(f"{prefix}.tenant_id", component.tenant_id)
        add(f"{prefix}.determination_set_id", component.determination_set_id)
        add(f"{prefix}.component_sequence", component.component_sequence)
        add(f"{prefix}.jurisdiction_id", component.jurisdiction_id)
        add(f"{prefix}.tax_code_id", component.tax_code_id)
        add(f"{prefix}.rule_id", component.rule_id)
        add(f"{prefix}.rule_version", component.rule_version)
        add(f"{prefix}.occurred_on", component.occurred_on)
        add(f"{prefix}.fact_kind", component.fact_kind)
        add(
            f"{prefix}.recognition_basis_code",
            component.recognition_basis_code,
        )
        add(f"{prefix}.transaction_side", component.transaction_side)
        add(f"{prefix}.treatment_code", component.treatment_code)
        add(f"{prefix}.calculation_base_code", component.calculation_base_code)
        add(f"{prefix}.inclusive", component.inclusive)
        add(f"{prefix}.party_category", component.party_category)
        add(f"{prefix}.supply_category", component.supply_category)
        add(f"{prefix}.place_code", component.place_code)
        add(
            f"{prefix}.party_classification_id",
            component.party_classification_id,
        )
        add(
            f"{prefix}.supply_classification_id",
            component.supply_classification_id,
        )
        add(
            f"{prefix}.place_classification_id",
            component.place_classification_id,
        )
        money(f"{prefix}.base_amount", component.base_amount)
        money(f"{prefix}.tax_amount", component.tax_amount)
        money(f"{prefix}.recoverable_amount", component.recoverable_amount)
        money(
            f"{prefix}.non_recoverable_amount",
            component.non_recoverable_amount,
        )
        add(f"{prefix}.currency_code", component.currency_code)
        add(f"{prefix}.minor_units", component.minor_units)
        add(f"{prefix}.source_ref", component.source_ref)
        add(f"{prefix}.source_version", component.source_version)
        add(f"{prefix}.source_fingerprint", component.source_fingerprint)
        add(f"{prefix}.evidence_ref", component.evidence_ref)
        add(f"{prefix}.counterparty_ref", component.counterparty_ref)
        add(f"{prefix}.determined_at", component.determined_at)
        for line_index, line in enumerate(component.lines, start=1):
            line_prefix = f"{prefix}.line.{line_index}"
            add(f"{line_prefix}.id", line.id)
            add(f"{line_prefix}.tenant_id", line.tenant_id)
            add(f"{line_prefix}.determination_id", line.determination_id)
            add(f"{line_prefix}.sequence", line.sequence)
            money(f"{line_prefix}.taxable_amount", line.taxable_amount)
            pairs.append(
                (
                    f"{line_prefix}.rate",
                    None
                    if line.rate is None
                    else _fixed_decimal(line.rate, 8, f"{line_prefix}.rate"),
                )
            )
            money(f"{line_prefix}.tax_amount", line.tax_amount)

    payload = bytearray()
    for key, value in pairs:
        key_bytes = key.encode("utf-8")
        value_bytes = b"\x00" if value is None else value.encode("utf-8")
        payload.extend(str(len(key_bytes)).encode("ascii"))
        payload.extend(b":")
        payload.extend(key_bytes)
        payload.extend(str(len(value_bytes)).encode("ascii"))
        payload.extend(b":")
        payload.extend(value_bytes)
    return f"rv1:{hashlib.sha256(payload).hexdigest()}"


_COMPONENT_SET_FIELDS = (
    "tenant_id",
    "jurisdiction_id",
    "occurred_on",
    "fact_kind",
    "recognition_basis_code",
    "transaction_side",
    "currency_code",
    "minor_units",
    "source_ref",
    "source_version",
    "source_fingerprint",
    "evidence_ref",
    "counterparty_ref",
    "determined_at",
)


def _validate_persisted_result_structure(row: TaxDeterminationSet) -> None:
    _require_aware_persisted(row.determined_at, "determination set time")
    if not row.components:
        raise TaxConflict("persisted determination set requires at least one component")
    sequences = [component.component_sequence for component in row.components]
    if any(
        not isinstance(sequence, int) or isinstance(sequence, bool)
        for sequence in sequences
    ):
        raise TaxConflict(
            "persisted determination components must have strict unique ordering"
        )
    component_sequences = cast(list[int], sequences)
    if component_sequences != sorted(component_sequences) or len(
        component_sequences
    ) != len(set(component_sequences)):
        raise TaxConflict(
            "persisted determination components must have strict unique ordering"
        )
    for component in row.components:
        if component.determination_set_id != row.id:
            raise TaxConflict(
                "persisted determination component belongs to another set"
            )
        _require_aware_persisted(
            component.determined_at, "determination component time"
        )
        for field in _COMPONENT_SET_FIELDS:
            if getattr(component, field) != getattr(row, field):
                label = field.replace("_", " ")
                raise TaxConflict(
                    f"persisted determination component {label} differs from its set"
                )
        if not component.lines:
            raise TaxConflict(
                "persisted determination component requires at least one line"
            )
        line_sequences = [line.sequence for line in component.lines]
        if (
            any(
                not isinstance(sequence, int) or isinstance(sequence, bool)
                for sequence in line_sequences
            )
            or line_sequences != sorted(line_sequences)
            or len(line_sequences) != len(set(line_sequences))
        ):
            raise TaxConflict(
                "persisted determination lines must have strict unique ordering"
            )
        for line in component.lines:
            if line.tenant_id != row.tenant_id:
                raise TaxConflict(
                    "persisted determination line tenant differs from its set"
                )
            if line.determination_id != component.id:
                raise TaxConflict(
                    "persisted determination line belongs to another component"
                )


def _sealed_result_fingerprint(row: TaxDeterminationSet) -> str:
    if row.result_seal_state != "sealed":
        if row.result_seal_state is None:
            raise TaxConflict(
                "persisted determination set predates the rv1 result-content "
                "seal; submit a new source version rather than rewriting evidence"
            )
        raise TaxConflict("persisted determination set is not sealed")
    value = row.result_fingerprint
    if value is None:
        raise TaxConflict("persisted sealed determination has no result fingerprint")
    if (
        len(value) != 68
        or not value.startswith("rv1:")
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise TaxConflict("persisted determination result fingerprint is invalid")
    return value


def _exact_money(
    amount: Decimal,
    *,
    currency: Currency,
    label: str,
) -> Money:
    value = Money(amount=amount, currency=currency)
    if value.amount != amount:
        raise TaxConflict(
            f"persisted {label} exceeds the determination currency's minor units"
        )
    return value


def _determination_set_contract(row: TaxDeterminationSet) -> TaxDeterminationSetV1:
    """Project persisted evidence into the public, ORM-free read contract."""

    _validate_persisted_result_structure(row)
    sealed_fingerprint = _sealed_result_fingerprint(row)
    currency = Currency(row.currency_code, row.minor_units)
    components: list[TaxDeterminationComponentV1] = []
    for component in row.components:
        if (
            component.currency_code != currency.code
            or component.minor_units != currency.minor_units
        ):
            raise TaxConflict(
                "persisted determination component currency differs from its set"
            )
        if component.component_sequence is None:
            raise TaxConflict("persisted determination component has no sequence")
        if component.determination_set_id is None:
            raise TaxConflict(
                "persisted determination component has no determination set"
            )
        if component.determination_set_id != row.id:
            raise TaxConflict(
                "persisted determination component belongs to another set"
            )
        if component.treatment_code is None:
            raise TaxConflict("persisted determination component has no treatment")
        if component.calculation_base_code is None:
            raise TaxConflict(
                "persisted determination component has no calculation base"
            )
        if component.inclusive is None:
            raise TaxConflict("persisted determination component has no inclusive flag")
        lines = tuple(
            TaxDeterminationLineV1(
                sequence=line.sequence,
                taxable_amount=_exact_money(
                    line.taxable_amount,
                    currency=currency,
                    label="determination line taxable amount",
                ),
                rate=line.rate,
                tax_amount=_exact_money(
                    line.tax_amount,
                    currency=currency,
                    label="determination line tax amount",
                ),
            )
            for line in component.lines
        )
        components.append(
            TaxDeterminationComponentV1(
                determination_id=component.id,
                determination_set_id=component.determination_set_id,
                component_sequence=component.component_sequence,
                tax_code_id=component.tax_code_id,
                rule_id=component.rule_id,
                rule_version=component.rule_version,
                treatment_code=component.treatment_code,
                calculation_base_code=component.calculation_base_code,
                inclusive=component.inclusive,
                party_category=component.party_category,
                supply_category=component.supply_category,
                place_code=component.place_code,
                party_classification_id=component.party_classification_id,
                supply_classification_id=component.supply_classification_id,
                place_classification_id=component.place_classification_id,
                base_amount=_exact_money(
                    component.base_amount,
                    currency=currency,
                    label="determination component base amount",
                ),
                tax_amount=_exact_money(
                    component.tax_amount,
                    currency=currency,
                    label="determination component tax amount",
                ),
                recoverable_amount=_exact_money(
                    component.recoverable_amount,
                    currency=currency,
                    label="determination component recoverable amount",
                ),
                non_recoverable_amount=_exact_money(
                    component.non_recoverable_amount,
                    currency=currency,
                    label="determination component non-recoverable amount",
                ),
                lines=lines,
            )
        )
    contract = TaxDeterminationSetV1(
        tenant_id=row.tenant_id,
        determination_set_id=row.id,
        jurisdiction_id=row.jurisdiction_id,
        occurred_on=row.occurred_on,
        fact_kind=row.fact_kind,
        recognition_basis_code=row.recognition_basis_code,
        transaction_side=row.transaction_side,
        source_amount=_exact_money(
            row.source_amount,
            currency=currency,
            label="determination source amount",
        ),
        net_amount=_exact_money(
            row.net_amount,
            currency=currency,
            label="determination net amount",
        ),
        tax_amount=_exact_money(
            row.tax_amount,
            currency=currency,
            label="determination tax amount",
        ),
        gross_amount=_exact_money(
            row.gross_amount,
            currency=currency,
            label="determination gross amount",
        ),
        source_ref=row.source_ref,
        source_version=row.source_version,
        source_fingerprint=row.source_fingerprint,
        result_fingerprint=sealed_fingerprint,
        evidence_ref=row.evidence_ref,
        counterparty_ref=row.counterparty_ref,
        supply_ref=row.supply_ref,
        place_ref=row.place_ref,
        determined_at=row.determined_at,
        components=tuple(components),
    )
    expected_fingerprint = _result_content_fingerprint(row)
    if not hmac.compare_digest(sealed_fingerprint, expected_fingerprint):
        raise TaxConflict("persisted determination result fingerprint does not match")
    return contract


def _fact_fingerprint(fact: TaxFact, *, source_ref: str, source_version: str) -> str:
    payload = {
        "jurisdiction_id": str(fact.jurisdiction_id),
        "occurred_on": fact.occurred_on.isoformat(),
        "fact_kind": fact.fact_kind,
        "recognition_basis_code": fact.recognition_basis_code,
        "transaction_side": fact.transaction_side,
        "amount": str(fact.base_amount.amount),
        "currency_code": fact.base_amount.currency.code,
        "minor_units": fact.base_amount.currency.minor_units,
        "source_ref": source_ref,
        "source_version": source_version,
        "evidence_ref": fact.evidence_ref.strip(),
        "party_category": fact.party_category,
        "supply_category": fact.supply_category,
        "place_code": fact.place_code,
        "counterparty_ref": fact.counterparty_ref,
        "supply_ref": fact.supply_ref,
        "place_ref": fact.place_ref,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _a1_fact_fingerprint(fact: TaxFact, *, source_ref: str, source_version: str) -> str:
    """Reproduce the published a1 fingerprint for standalone replay only."""
    payload = {
        "jurisdiction_id": str(fact.jurisdiction_id),
        "occurred_on": fact.occurred_on.isoformat(),
        "fact_kind": fact.fact_kind,
        "recognition_basis_code": fact.recognition_basis_code,
        "transaction_side": fact.transaction_side,
        "amount": str(fact.base_amount.amount),
        "currency_code": fact.base_amount.currency.code,
        "minor_units": fact.base_amount.currency.minor_units,
        "source_ref": source_ref,
        "source_version": source_version,
        "evidence_ref": fact.evidence_ref.strip(),
        "party_category": fact.party_category,
        "supply_category": fact.supply_category,
        "place_code": fact.place_code,
        "counterparty_ref": fact.counterparty_ref,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _classification_fingerprint(
    command: TaxSubjectClassificationInput,
    *,
    subject_ref: str,
    category_code: str,
    basis_code: str,
    evidence_ref: str,
    published_by_ref: str,
    source_ref: str,
    source_version: str,
) -> str:
    payload = {
        "tax_code_id": str(command.tax_code_id),
        "subject_kind": command.subject_kind,
        "subject_ref": subject_ref,
        "category_code": category_code,
        "version": command.version,
        "effective_from": command.effective_from.isoformat(),
        "effective_to": (
            command.effective_to.isoformat() if command.effective_to else None
        ),
        "basis_code": basis_code,
        "evidence_ref": evidence_ref,
        "published_by_ref": published_by_ref,
        "source_ref": source_ref,
        "source_version": source_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_tax_authority(
    db: Session, *, tenant_id: UUID, command: TaxAuthorityInput
) -> TaxAuthority:
    code = _clean(command.code, "tax authority code")
    existing = db.scalar(
        select(TaxAuthority).where(
            TaxAuthority.tenant_id == tenant_id, TaxAuthority.code == code
        )
    )
    if existing is not None:
        raise TaxConflict(f"tax authority {code} already exists")
    row = TaxAuthority(
        tenant_id=tenant_id,
        code=code,
        name=_clean(command.name, "tax authority name"),
        authority_level_code=(
            command.authority_level_code.strip()
            if command.authority_level_code
            else None
        ),
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def update_tax_authority(
    db: Session,
    *,
    tenant_id: UUID,
    authority_id: UUID,
    name: str,
    authority_level_code: str | None,
) -> TaxAuthority:
    row = _authority(db, tenant_id, authority_id)
    if row.status != "active":
        raise TaxConflict("retired tax authority cannot be changed")
    row.name = _clean(name, "tax authority name")
    row.authority_level_code = (
        authority_level_code.strip() if authority_level_code else None
    )
    db.flush()
    return row


def create_tax_jurisdiction(
    db: Session, *, tenant_id: UUID, command: TaxJurisdictionInput
) -> TaxJurisdiction:
    authority = _authority(db, tenant_id, command.authority_id)
    if authority.status != "active":
        raise TaxConflict("tax authority is retired")
    row = TaxJurisdiction(
        tenant_id=tenant_id,
        authority_id=authority.id,
        code=_clean(command.code, "jurisdiction code"),
        name=_clean(command.name, "jurisdiction name"),
        country_code=_country(command.country_code),
        subdivision_code=(
            command.subdivision_code.strip() if command.subdivision_code else None
        ),
        currency_code=command.currency.code,
        minor_units=command.currency.minor_units,
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def update_tax_jurisdiction(
    db: Session,
    *,
    tenant_id: UUID,
    jurisdiction_id: UUID,
    name: str,
    subdivision_code: str | None,
) -> TaxJurisdiction:
    row = _jurisdiction(db, tenant_id, jurisdiction_id)
    if row.status != "active":
        raise TaxConflict("retired tax jurisdiction cannot be changed")
    row.name = _clean(name, "jurisdiction name")
    row.subdivision_code = subdivision_code.strip() if subdivision_code else None
    db.flush()
    return row


def create_tax_code(
    db: Session,
    *,
    tenant_id: UUID,
    jurisdiction_id: UUID,
    code: str,
    name: str,
    tax_kind_code: str,
    description: str | None = None,
) -> TaxCode:
    jurisdiction = _jurisdiction(db, tenant_id, jurisdiction_id)
    if jurisdiction.status != "active":
        raise TaxConflict("tax jurisdiction is retired")
    row = TaxCode(
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction.id,
        code=_clean(code, "tax code"),
        name=_clean(name, "tax code name"),
        tax_kind_code=_clean(tax_kind_code, "tax kind code"),
        description=description.strip() if description else None,
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def update_tax_code(
    db: Session,
    *,
    tenant_id: UUID,
    tax_code_id: UUID,
    name: str,
    description: str | None,
) -> TaxCode:
    row = _code(db, tenant_id, tax_code_id)
    if row.status != "active":
        raise TaxConflict("retired tax code cannot be changed")
    row.name = _clean(name, "tax code name")
    row.description = description.strip() if description else None
    db.flush()
    return row


def publish_tax_rule(
    db: Session,
    *,
    tenant_id: UUID,
    command: TaxRuleInput,
    published_at: datetime | None = None,
) -> TaxRule:
    code = _code(db, tenant_id, command.tax_code_id)
    jurisdiction = _jurisdiction(db, tenant_id, code.jurisdiction_id)
    if code.status != "active" or jurisdiction.status != "active":
        raise TaxConflict("tax rule cannot be published for retired policy")
    if command.version <= 0:
        raise TaxRuleViolation("tax rule version must be positive")
    if (
        command.effective_to is not None
        and command.effective_to < command.effective_from
    ):
        raise TaxRuleViolation("tax rule effective end precedes its start")
    if command.transaction_side not in {"input", "output", "withholding", "liability"}:
        raise TaxRuleViolation("unknown tax transaction side")
    if command.calculation_method not in {"percentage", "fixed", "progressive"}:
        raise TaxRuleViolation("unknown tax calculation method")
    if command.treatment_code not in {
        "standard_rated",
        "zero_rated",
        "exempt",
        "out_of_scope",
    }:
        raise TaxRuleViolation("unknown tax treatment")
    if command.calculation_sequence <= 0:
        raise TaxRuleViolation("tax calculation sequence must be positive")
    if command.calculation_base_code not in {
        "source_amount",
        "source_plus_prior_tax",
    }:
        raise TaxRuleViolation("unknown tax calculation base")
    if not 0 <= command.recoverable_rate <= 1:
        raise TaxRuleViolation("recoverable rate must be between zero and one")
    if command.calculation_method == "percentage":
        if command.rate is None or command.rate < 0 or command.fixed_amount is not None:
            raise TaxRuleViolation("percentage rule requires only a non-negative rate")
    elif command.calculation_method == "fixed":
        if command.fixed_amount is None or command.rate is not None:
            raise TaxRuleViolation("fixed rule requires only a fixed amount")
        if not _currency_matches(
            command.fixed_amount.currency,
            jurisdiction.currency_code,
            jurisdiction.minor_units,
        ):
            raise TaxRuleViolation(
                "fixed tax amount uses the wrong jurisdiction currency"
            )
    else:
        if (
            command.rate is not None
            or command.fixed_amount is not None
            or not command.bands
        ):
            raise TaxRuleViolation(
                "progressive rule requires bands and no header amount"
            )
    if command.calculation_method != "progressive" and command.bands:
        raise TaxRuleViolation("only progressive rules may declare bands")
    if command.inclusive and (
        command.calculation_method != "percentage"
        or command.calculation_base_code != "source_amount"
    ):
        raise TaxRuleViolation(
            "inclusive tax must be percentage-based on the source amount"
        )
    if command.treatment_code != "standard_rated" and (
        command.calculation_method != "percentage"
        or command.rate != 0
        or command.inclusive
        or command.recoverable_rate != 0
    ):
        raise TaxRuleViolation(
            "zero-rated, exempt and out-of-scope rules must explicitly calculate zero"
        )
    ordered = sorted(command.bands, key=lambda item: item.sequence)
    previous_upper: Decimal | None = None
    for expected, band in enumerate(ordered, start=1):
        if band.sequence != expected or band.lower_bound < 0 or not 0 <= band.rate <= 1:
            raise TaxRuleViolation("progressive tax bands are invalid")
        if previous_upper is not None and band.lower_bound != previous_upper:
            raise TaxRuleViolation("progressive tax bands must be contiguous")
        if band.upper_bound is not None and band.upper_bound <= band.lower_bound:
            raise TaxRuleViolation("progressive tax band upper bound is invalid")
        if previous_upper is None and expected > 1:
            raise TaxRuleViolation("an unbounded tax band must be last")
        previous_upper = band.upper_bound
    existing = db.scalar(
        select(TaxRule).where(
            TaxRule.tenant_id == tenant_id,
            TaxRule.tax_code_id == command.tax_code_id,
            TaxRule.version == command.version,
        )
    )
    if existing is not None:
        raise TaxConflict("tax rule version already exists")
    row = TaxRule(
        tenant_id=tenant_id,
        tax_code_id=command.tax_code_id,
        version=command.version,
        effective_from=command.effective_from,
        effective_to=command.effective_to,
        priority=command.priority,
        fact_kind=_clean(command.fact_kind, "fact kind"),
        recognition_basis_code=_clean(
            command.recognition_basis_code, "recognition basis code"
        ),
        transaction_side=command.transaction_side,
        calculation_method=command.calculation_method,
        rate=command.rate,
        fixed_amount=(command.fixed_amount.amount if command.fixed_amount else None),
        inclusive=command.inclusive,
        recoverable_rate=command.recoverable_rate,
        party_category=command.party_category.strip()
        if command.party_category
        else None,
        supply_category=(
            command.supply_category.strip() if command.supply_category else None
        ),
        place_code=command.place_code.strip() if command.place_code else None,
        treatment_code=command.treatment_code,
        calculation_sequence=command.calculation_sequence,
        calculation_base_code=command.calculation_base_code,
        published_at=published_at or datetime.now(UTC),
    )
    row.bands.extend(
        TaxRuleBand(
            tenant_id=tenant_id,
            sequence=band.sequence,
            lower_bound=band.lower_bound,
            upper_bound=band.upper_bound,
            rate=band.rate,
        )
        for band in ordered
    )
    db.add(row)
    db.flush()
    return row


def publish_tax_subject_classification(
    db: Session,
    *,
    tenant_id: UUID,
    command: TaxSubjectClassificationInput,
    published_at: datetime | None = None,
) -> TaxSubjectClassification:
    code = _code(db, tenant_id, command.tax_code_id)
    if code.status != "active":
        raise TaxConflict("tax classification cannot be published for retired code")
    if command.subject_kind not in {"party", "supply", "place"}:
        raise TaxRuleViolation("unknown tax subject kind")
    if command.version <= 0:
        raise TaxRuleViolation("tax subject classification version must be positive")
    if (
        command.effective_to is not None
        and command.effective_to < command.effective_from
    ):
        raise TaxRuleViolation(
            "tax subject classification effective end precedes its start"
        )
    subject_ref = _clean(command.subject_ref, "tax subject reference")
    category_code = _clean(command.category_code, "tax subject category")
    basis_code = _clean(command.basis_code, "classification basis")
    evidence_ref = _clean(command.evidence_ref, "classification evidence reference")
    published_by_ref = _clean(
        command.published_by_ref, "classification publisher reference"
    )
    source_ref = _clean(command.source_ref, "classification source reference")
    source_version = _clean(command.source_version, "classification source version")
    fingerprint = _classification_fingerprint(
        command,
        subject_ref=subject_ref,
        category_code=category_code,
        basis_code=basis_code,
        evidence_ref=evidence_ref,
        published_by_ref=published_by_ref,
        source_ref=source_ref,
        source_version=source_version,
    )
    existing = db.scalar(
        select(TaxSubjectClassification).where(
            TaxSubjectClassification.tenant_id == tenant_id,
            TaxSubjectClassification.source_ref == source_ref,
            TaxSubjectClassification.source_version == source_version,
        )
    )
    if existing is not None:
        if existing.source_fingerprint != fingerprint:
            raise TaxConflict(
                "tax classification source version was reused with different facts"
            )
        return existing
    current_version = db.scalar(
        select(func.max(TaxSubjectClassification.version)).where(
            TaxSubjectClassification.tenant_id == tenant_id,
            TaxSubjectClassification.tax_code_id == command.tax_code_id,
            TaxSubjectClassification.subject_kind == command.subject_kind,
            TaxSubjectClassification.subject_ref == subject_ref,
        )
    )
    expected_version = int(current_version or 0) + 1
    if command.version != expected_version:
        raise TaxConflict(f"next tax classification version must be {expected_version}")
    row = TaxSubjectClassification(
        tenant_id=tenant_id,
        tax_code_id=command.tax_code_id,
        subject_kind=command.subject_kind,
        subject_ref=subject_ref,
        category_code=category_code,
        version=command.version,
        effective_from=command.effective_from,
        effective_to=command.effective_to,
        basis_code=basis_code,
        evidence_ref=evidence_ref,
        published_by_ref=published_by_ref,
        source_ref=source_ref,
        source_version=source_version,
        source_fingerprint=fingerprint,
        published_at=published_at or datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def _optional_match(configured: str | None, observed: str | None) -> bool:
    return configured is None or configured == observed


def _subject_classification(
    db: Session,
    *,
    tenant_id: UUID,
    tax_code_id: UUID,
    subject_kind: str,
    subject_ref: str | None,
    direct_category: str | None,
    occurred_on: date,
) -> tuple[str | None, TaxSubjectClassification | None]:
    if subject_ref is None:
        return direct_category, None
    cleaned_ref = _clean(subject_ref, f"{subject_kind} reference")
    rows = db.scalars(
        select(TaxSubjectClassification)
        .where(
            TaxSubjectClassification.tenant_id == tenant_id,
            TaxSubjectClassification.tax_code_id == tax_code_id,
            TaxSubjectClassification.subject_kind == subject_kind,
            TaxSubjectClassification.subject_ref == cleaned_ref,
            TaxSubjectClassification.effective_from <= occurred_on,
            (TaxSubjectClassification.effective_to.is_(None))
            | (TaxSubjectClassification.effective_to >= occurred_on),
        )
        .order_by(TaxSubjectClassification.version.desc())
    ).all()
    if not rows:
        return direct_category, None
    selected = rows[0]
    if direct_category is not None and direct_category != selected.category_code:
        raise TaxConflict(
            f"{subject_kind} tax category conflicts with owned classification"
        )
    return selected.category_code, selected


def _applicable_rules(
    db: Session, tenant_id: UUID, fact: TaxFact
) -> list[_ApplicableTaxRule]:
    rows = db.scalars(
        select(TaxRule)
        .join(
            TaxCode,
            (TaxCode.tenant_id == TaxRule.tenant_id)
            & (TaxCode.id == TaxRule.tax_code_id),
        )
        .where(
            TaxRule.tenant_id == tenant_id,
            TaxCode.jurisdiction_id == fact.jurisdiction_id,
            TaxCode.status == "active",
            TaxRule.fact_kind == fact.fact_kind,
            TaxRule.recognition_basis_code == fact.recognition_basis_code,
            TaxRule.transaction_side == fact.transaction_side,
            TaxRule.effective_from <= fact.occurred_on,
            (TaxRule.effective_to.is_(None))
            | (TaxRule.effective_to >= fact.occurred_on),
        )
    ).all()
    if not rows:
        raise TaxRuleViolation("no applicable tax rule")

    codes = {
        row.id: row.code
        for row in db.scalars(
            select(TaxCode).where(
                TaxCode.tenant_id == tenant_id,
                TaxCode.id.in_({rule.tax_code_id for rule in rows}),
            )
        ).all()
    }
    grouped: dict[UUID, list[TaxRule]] = {}
    for row in rows:
        grouped.setdefault(row.tax_code_id, []).append(row)

    selected_rules: list[_ApplicableTaxRule] = []
    for tax_code_id, candidates in grouped.items():
        party_category, party_classification = _subject_classification(
            db,
            tenant_id=tenant_id,
            tax_code_id=tax_code_id,
            subject_kind="party",
            subject_ref=fact.counterparty_ref,
            direct_category=fact.party_category,
            occurred_on=fact.occurred_on,
        )
        supply_category, supply_classification = _subject_classification(
            db,
            tenant_id=tenant_id,
            tax_code_id=tax_code_id,
            subject_kind="supply",
            subject_ref=fact.supply_ref,
            direct_category=fact.supply_category,
            occurred_on=fact.occurred_on,
        )
        place_code, place_classification = _subject_classification(
            db,
            tenant_id=tenant_id,
            tax_code_id=tax_code_id,
            subject_kind="place",
            subject_ref=fact.place_ref,
            direct_category=fact.place_code,
            occurred_on=fact.occurred_on,
        )
        ranked = [
            (
                row.priority,
                sum(
                    value is not None
                    for value in (
                        row.party_category,
                        row.supply_category,
                        row.place_code,
                    )
                ),
                row.version,
                row,
            )
            for row in candidates
            if _optional_match(row.party_category, party_category)
            and _optional_match(row.supply_category, supply_category)
            and _optional_match(row.place_code, place_code)
        ]
        if not ranked:
            raise TaxRuleViolation(f"no applicable tax rule for {codes[tax_code_id]}")
        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        top = ranked[0]
        if len(ranked) > 1 and ranked[1][:2] == top[:2]:
            raise TaxRuleViolation(
                f"tax determination for {codes[tax_code_id]} is ambiguous at "
                "the highest priority"
            )
        selected_rules.append(
            _ApplicableTaxRule(
                rule=top[3],
                party_category=party_category,
                supply_category=supply_category,
                place_code=place_code,
                party_classification=party_classification,
                supply_classification=supply_classification,
                place_classification=place_classification,
                tax_code=codes[tax_code_id],
            )
        )
    if not selected_rules:
        raise TaxRuleViolation("no applicable tax rule")
    selected_rules.sort(
        key=lambda item: (item.rule.calculation_sequence, item.tax_code)
    )
    sequences = [item.rule.calculation_sequence for item in selected_rules]
    if len(sequences) != len(set(sequences)):
        raise TaxRuleViolation("tax calculation sequence is ambiguous")
    if any(item.rule.inclusive for item in selected_rules) and len(selected_rules) > 1:
        raise TaxRuleViolation(
            "inclusive tax cannot be combined with another tax component"
        )
    return selected_rules


def _calculate_rule(
    rule: TaxRule,
    *,
    calculation_base: Decimal,
    minor_units: int,
) -> tuple[Decimal, Decimal, list[tuple[Decimal, Decimal | None, Decimal]]]:
    base = calculation_base
    line_values: list[tuple[Decimal, Decimal | None, Decimal]] = []
    if rule.calculation_method == "percentage":
        if rule.rate is None:
            raise TaxRuleViolation("percentage tax rule has no rate")
        if rule.inclusive:
            taxable = base / (Decimal(1) + rule.rate)
            tax_amount = base - taxable
            base = taxable
        else:
            tax_amount = base * rule.rate
        tax_amount = _round(tax_amount, minor_units)
        base = _round(base, minor_units)
        line_values.append((base, rule.rate, tax_amount))
    elif rule.calculation_method == "fixed":
        if rule.fixed_amount is None:
            raise TaxRuleViolation("fixed tax rule has no amount")
        tax_amount = _round(rule.fixed_amount, minor_units)
        base = _round(base, minor_units)
        line_values.append((base, None, tax_amount))
    else:
        tax_amount = Decimal(0)
        if not rule.bands:
            raise TaxRuleViolation("progressive tax rule has no bands")
        for band in rule.bands:
            upper = base if band.upper_bound is None else min(base, band.upper_bound)
            taxable = max(Decimal(0), upper - band.lower_bound)
            amount = _round(taxable * band.rate, minor_units)
            line_values.append((taxable, band.rate, amount))
            tax_amount += amount
        tax_amount = _round(tax_amount, minor_units)
        base = _round(base, minor_units)
    return base, tax_amount, line_values


def _validate_tax_fact(
    db: Session, *, tenant_id: UUID, fact: TaxFact
) -> tuple[TaxJurisdiction, str, str, str]:
    jurisdiction = _jurisdiction(db, tenant_id, fact.jurisdiction_id)
    if not _currency_matches(
        fact.base_amount.currency,
        jurisdiction.currency_code,
        jurisdiction.minor_units,
    ):
        raise TaxRuleViolation("tax fact uses the wrong jurisdiction currency")
    if fact.base_amount.amount < 0:
        raise TaxRuleViolation("tax fact base amount must be non-negative")
    source_ref = _clean(fact.source_ref, "source reference")
    source_version = _clean(fact.source_version, "source version")
    fingerprint = _fact_fingerprint(
        fact, source_ref=source_ref, source_version=source_version
    )
    return jurisdiction, source_ref, source_version, fingerprint


def _determine_tax_set_row(
    db: Session,
    *,
    tenant_id: UUID,
    fact: TaxFact,
    determined_at: datetime,
) -> TaxDeterminationSet:
    _require_aware_input(determined_at)
    jurisdiction, source_ref, source_version, fingerprint = _validate_tax_fact(
        db, tenant_id=tenant_id, fact=fact
    )
    legacy = db.scalar(
        select(TaxDetermination).where(
            TaxDetermination.tenant_id == tenant_id,
            TaxDetermination.source_ref == source_ref,
            TaxDetermination.source_version == source_version,
            TaxDetermination.determination_set_id.is_(None),
        )
    )
    existing = db.scalar(
        select(TaxDeterminationSet).where(
            TaxDeterminationSet.tenant_id == tenant_id,
            TaxDeterminationSet.source_ref == source_ref,
            TaxDeterminationSet.source_version == source_version,
        )
    )
    if legacy is not None and existing is not None:
        raise TaxConflict("tax source version has conflicting determination owners")
    if legacy is not None:
        a1_fingerprint = _a1_fact_fingerprint(
            fact, source_ref=source_ref, source_version=source_version
        )
        if (
            fact.supply_ref is not None
            or fact.place_ref is not None
            or legacy.source_fingerprint != a1_fingerprint
        ):
            raise TaxConflict("tax source version was reused with different facts")
        raise TaxConflict(
            "tax source version was already determined through the legacy "
            "single-component API"
        )
    if existing is not None:
        if existing.source_fingerprint != fingerprint:
            raise TaxConflict("tax source version was reused with different facts")
        return existing
    if jurisdiction.status != "active":
        raise TaxConflict("tax determination cannot use a retired jurisdiction")

    selected_rules = _applicable_rules(db, tenant_id, fact)
    determination_set_id = uuid4()
    source_amount = _round(fact.base_amount.amount, jurisdiction.minor_units)
    prior_tax = Decimal(0)
    inclusive_tax = Decimal(0)
    components: list[TaxDetermination] = []
    for applicable in selected_rules:
        rule = applicable.rule
        calculation_base = source_amount
        if rule.calculation_base_code == "source_plus_prior_tax":
            calculation_base += prior_tax
        base, tax_amount, line_values = _calculate_rule(
            rule,
            calculation_base=calculation_base,
            minor_units=jurisdiction.minor_units,
        )
        recoverable = _round(
            tax_amount * rule.recoverable_rate, jurisdiction.minor_units
        )
        non_recoverable = tax_amount - recoverable
        determination_id = uuid4()
        component = TaxDetermination(
            id=determination_id,
            tenant_id=tenant_id,
            determination_set_id=determination_set_id,
            component_sequence=rule.calculation_sequence,
            jurisdiction_id=jurisdiction.id,
            tax_code_id=rule.tax_code_id,
            rule_id=rule.id,
            rule_version=rule.version,
            occurred_on=fact.occurred_on,
            fact_kind=fact.fact_kind,
            recognition_basis_code=fact.recognition_basis_code,
            transaction_side=fact.transaction_side,
            treatment_code=rule.treatment_code,
            calculation_base_code=rule.calculation_base_code,
            inclusive=rule.inclusive,
            party_category=applicable.party_category,
            supply_category=applicable.supply_category,
            place_code=applicable.place_code,
            party_classification_id=(
                applicable.party_classification.id
                if applicable.party_classification
                else None
            ),
            supply_classification_id=(
                applicable.supply_classification.id
                if applicable.supply_classification
                else None
            ),
            place_classification_id=(
                applicable.place_classification.id
                if applicable.place_classification
                else None
            ),
            base_amount=base,
            tax_amount=tax_amount,
            recoverable_amount=recoverable,
            non_recoverable_amount=non_recoverable,
            currency_code=jurisdiction.currency_code,
            minor_units=jurisdiction.minor_units,
            source_ref=source_ref,
            source_version=source_version,
            source_fingerprint=fingerprint,
            evidence_ref=_clean(fact.evidence_ref, "evidence reference"),
            counterparty_ref=(
                fact.counterparty_ref.strip() if fact.counterparty_ref else None
            ),
            determined_at=determined_at,
        )
        component.lines.extend(
            TaxDeterminationLine(
                id=uuid4(),
                tenant_id=tenant_id,
                determination_id=determination_id,
                sequence=sequence,
                taxable_amount=taxable,
                rate=rate,
                tax_amount=amount,
            )
            for sequence, (taxable, rate, amount) in enumerate(line_values, start=1)
        )
        components.append(component)
        prior_tax += tax_amount
        if rule.inclusive:
            inclusive_tax += tax_amount

    tax_amount = _round(prior_tax, jurisdiction.minor_units)
    net_amount = _round(source_amount - inclusive_tax, jurisdiction.minor_units)
    gross_amount = _round(net_amount + tax_amount, jurisdiction.minor_units)
    row = TaxDeterminationSet(
        id=determination_set_id,
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction.id,
        occurred_on=fact.occurred_on,
        fact_kind=fact.fact_kind,
        recognition_basis_code=fact.recognition_basis_code,
        transaction_side=fact.transaction_side,
        source_amount=source_amount,
        net_amount=net_amount,
        tax_amount=tax_amount,
        gross_amount=gross_amount,
        currency_code=jurisdiction.currency_code,
        minor_units=jurisdiction.minor_units,
        source_ref=source_ref,
        source_version=source_version,
        source_fingerprint=fingerprint,
        result_seal_state="building",
        result_fingerprint=None,
        evidence_ref=_clean(fact.evidence_ref, "evidence reference"),
        counterparty_ref=(
            fact.counterparty_ref.strip() if fact.counterparty_ref else None
        ),
        supply_ref=fact.supply_ref.strip() if fact.supply_ref else None,
        place_ref=fact.place_ref.strip() if fact.place_ref else None,
        determined_at=determined_at,
    )
    row.components.extend(components)
    _validate_persisted_result_structure(row)
    db.add(row)
    db.flush()
    row.result_fingerprint = _result_content_fingerprint(row)
    row.result_seal_state = "sealed"
    db.flush()
    return row


def determine_tax_set(
    db: Session,
    *,
    tenant_id: UUID,
    fact: TaxFact,
    determined_at: datetime,
) -> TaxDeterminationSetV1:
    """Determine and return an ORM-free, exact-money result contract."""

    row = _determine_tax_set_row(
        db,
        tenant_id=tenant_id,
        fact=fact,
        determined_at=determined_at,
    )
    try:
        return _determination_set_contract(row)
    except TaxConflict:
        raise
    except ValueError as exc:
        raise TaxConflict(f"persisted determination set is invalid: {exc}") from exc


def determine_tax(
    db: Session,
    *,
    tenant_id: UUID,
    fact: TaxFact,
    determined_at: datetime,
) -> TaxDetermination:
    _require_aware_input(determined_at)
    jurisdiction, source_ref, source_version, fingerprint = _validate_tax_fact(
        db, tenant_id=tenant_id, fact=fact
    )
    legacy = db.scalar(
        select(TaxDetermination).where(
            TaxDetermination.tenant_id == tenant_id,
            TaxDetermination.source_ref == source_ref,
            TaxDetermination.source_version == source_version,
            TaxDetermination.determination_set_id.is_(None),
        )
    )
    existing_set = db.scalar(
        select(TaxDeterminationSet).where(
            TaxDeterminationSet.tenant_id == tenant_id,
            TaxDeterminationSet.source_ref == source_ref,
            TaxDeterminationSet.source_version == source_version,
        )
    )
    if legacy is not None and existing_set is not None:
        raise TaxConflict("tax source version has conflicting determination owners")
    if legacy is not None:
        a1_fingerprint = _a1_fact_fingerprint(
            fact, source_ref=source_ref, source_version=source_version
        )
        if (
            fact.supply_ref is not None
            or fact.place_ref is not None
            or legacy.source_fingerprint != a1_fingerprint
        ):
            raise TaxConflict("tax source version was reused with different facts")
        _require_aware_persisted(legacy.determined_at, "legacy determination time")
        return legacy
    if existing_set is not None:
        if existing_set.source_fingerprint != fingerprint:
            raise TaxConflict("tax source version was reused with different facts")
        _determination_set_contract(existing_set)
        if len(existing_set.components) != 1:
            raise TaxRuleViolation(
                "multiple tax components require the determine_tax_set API"
            )
        return existing_set.components[0]
    if jurisdiction.status != "active":
        raise TaxConflict("tax determination cannot use a retired jurisdiction")
    if len(_applicable_rules(db, tenant_id, fact)) != 1:
        raise TaxRuleViolation(
            "multiple tax components require the determine_tax_set API"
        )
    determination_set = _determine_tax_set_row(
        db,
        tenant_id=tenant_id,
        fact=fact,
        determined_at=determined_at,
    )
    if len(determination_set.components) != 1:
        raise TaxRuleViolation(
            "multiple tax components require the determine_tax_set API"
        )
    _determination_set_contract(determination_set)
    return determination_set.components[0]


def create_statutory_report_definition(
    db: Session,
    *,
    tenant_id: UUID,
    jurisdiction_id: UUID,
    code: str,
    name: str,
    currency: Currency,
    payable_box_code: str,
    boxes: tuple[StatutoryReportBoxInput, ...],
) -> StatutoryReportDefinition:
    jurisdiction = _jurisdiction(db, tenant_id, jurisdiction_id)
    if not _currency_matches(
        currency, jurisdiction.currency_code, jurisdiction.minor_units
    ):
        raise TaxRuleViolation("report definition uses the wrong jurisdiction currency")
    if not boxes:
        raise TaxRuleViolation("report definition requires at least one box")
    box_codes = [_clean(item.box_code, "report box code") for item in boxes]
    if len(set(box_codes)) != len(box_codes):
        raise TaxRuleViolation("report box codes must be unique")
    payable = _clean(payable_box_code, "payable box code")
    if payable not in box_codes:
        raise TaxRuleViolation("payable box code is not declared")
    for item in boxes:
        tax_code = _code(db, tenant_id, item.tax_code_id)
        if tax_code.jurisdiction_id != jurisdiction.id:
            raise TaxRuleViolation(
                "report box tax code belongs to another jurisdiction"
            )
        if item.value_source not in {
            "base_amount",
            "tax_amount",
            "recoverable_amount",
            "non_recoverable_amount",
        }:
            raise TaxRuleViolation("unknown report-box value source")
    row = StatutoryReportDefinition(
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction.id,
        code=_clean(code, "report definition code"),
        name=_clean(name, "report definition name"),
        currency_code=currency.code,
        minor_units=currency.minor_units,
        payable_box_code=payable,
        status="active",
    )
    row.boxes.extend(
        StatutoryReportBox(
            tenant_id=tenant_id,
            box_code=item.box_code.strip(),
            label=_clean(item.label, "report box label"),
            sequence=item.sequence,
            tax_code_id=item.tax_code_id,
            value_source=item.value_source,
            multiplier=item.multiplier,
        )
        for item in boxes
    )
    db.add(row)
    db.flush()
    return row


def create_filing_obligation(
    db: Session,
    *,
    tenant_id: UUID,
    definition_id: UUID,
    obligation_ref: str,
    period_start: date,
    period_end: date,
    due_on: date,
    taxpayer_ref: str,
) -> TaxFilingObligation:
    definition = db.scalar(
        select(StatutoryReportDefinition).where(
            StatutoryReportDefinition.tenant_id == tenant_id,
            StatutoryReportDefinition.id == definition_id,
        )
    )
    if definition is None:
        raise TaxNotFound("statutory report definition not found")
    if period_end < period_start:
        raise TaxRuleViolation("filing period end precedes its start")
    row = TaxFilingObligation(
        tenant_id=tenant_id,
        definition_id=definition.id,
        obligation_ref=_clean(obligation_ref, "obligation reference"),
        period_start=period_start,
        period_end=period_end,
        due_on=due_on,
        taxpayer_ref=_clean(taxpayer_ref, "taxpayer reference"),
        status="open",
    )
    db.add(row)
    db.flush()
    return row


def _verified_determination_contracts_for_period(
    db: Session,
    *,
    tenant_id: UUID,
    jurisdiction_id: UUID,
    period_start: date,
    period_end: date,
) -> tuple[TaxDeterminationSetV1, ...]:
    """Return only sealed evidence for an authoritative report projection."""

    legacy_id = db.scalar(
        select(TaxDetermination.id)
        .where(
            TaxDetermination.tenant_id == tenant_id,
            TaxDetermination.jurisdiction_id == jurisdiction_id,
            TaxDetermination.occurred_on >= period_start,
            TaxDetermination.occurred_on <= period_end,
            TaxDetermination.determination_set_id.is_(None),
        )
        .limit(1)
    )
    if legacy_id is not None:
        raise TaxConflict(
            "legacy unsealed determinations cannot feed an a3 statutory report; "
            "submit new source versions through determine_tax_set"
        )
    rows = db.scalars(
        select(TaxDeterminationSet)
        .where(
            TaxDeterminationSet.tenant_id == tenant_id,
            TaxDeterminationSet.jurisdiction_id == jurisdiction_id,
            TaxDeterminationSet.occurred_on >= period_start,
            TaxDeterminationSet.occurred_on <= period_end,
        )
        .order_by(
            TaxDeterminationSet.occurred_on,
            TaxDeterminationSet.source_ref,
            TaxDeterminationSet.source_version,
        )
    ).all()
    return tuple(_determination_set_contract(row) for row in rows)


def generate_statutory_report(
    db: Session,
    *,
    tenant_id: UUID,
    obligation_id: UUID,
    generated_by_id: UUID,
    generated_at: datetime,
) -> StatutoryReport:
    obligation = db.scalar(
        select(TaxFilingObligation).where(
            TaxFilingObligation.tenant_id == tenant_id,
            TaxFilingObligation.id == obligation_id,
        )
    )
    if obligation is None:
        raise TaxNotFound("tax filing obligation not found")
    definition = db.scalar(
        select(StatutoryReportDefinition).where(
            StatutoryReportDefinition.tenant_id == tenant_id,
            StatutoryReportDefinition.id == obligation.definition_id,
        )
    )
    if definition is None:
        raise TaxNotFound("statutory report definition not found")
    previous = db.scalar(
        select(func.max(StatutoryReport.version)).where(
            StatutoryReport.tenant_id == tenant_id,
            StatutoryReport.obligation_id == obligation.id,
        )
    )
    version = int(previous or 0) + 1
    determinations = _verified_determination_contracts_for_period(
        db,
        tenant_id=tenant_id,
        jurisdiction_id=definition.jurisdiction_id,
        period_start=obligation.period_start,
        period_end=obligation.period_end,
    )
    report_currency = Currency(definition.currency_code, definition.minor_units)
    amounts: dict[str, Decimal] = {}
    for box in definition.boxes:
        raw = Decimal(0)
        for determination in determinations:
            for component in determination.components:
                if component.tax_code_id != box.tax_code_id:
                    continue
                value = getattr(component, box.value_source)
                if not isinstance(value, Money) or value.currency != report_currency:
                    raise TaxConflict(
                        "determination component currency differs from statutory report"
                    )
                raw += value.amount
        amounts[box.box_code] = _round(raw * box.multiplier, definition.minor_units)
    row = StatutoryReport(
        tenant_id=tenant_id,
        definition_id=definition.id,
        obligation_id=obligation.id,
        version=version,
        total_payable=amounts[definition.payable_box_code],
        currency_code=definition.currency_code,
        minor_units=definition.minor_units,
        snapshot_ref=f"obligation:{obligation.id}:report-version:{version}",
        generated_by_id=generated_by_id,
        generated_at=generated_at,
    )
    row.values.extend(
        StatutoryReportValue(
            tenant_id=tenant_id,
            box_code=box.box_code,
            label=box.label,
            sequence=box.sequence,
            amount=amounts[box.box_code],
        )
        for box in definition.boxes
    )
    db.add(row)
    db.flush()
    return row


def _event(
    db: Session,
    *,
    tax_return: TaxReturn,
    from_status: str | None,
    to_status: str,
    actor_id: UUID,
    occurred_at: datetime,
    authority_reference: str | None = None,
) -> TaxReturnEvent:
    sequence = db.scalar(
        select(func.coalesce(func.max(TaxReturnEvent.sequence), 0)).where(
            TaxReturnEvent.tenant_id == tax_return.tenant_id,
            TaxReturnEvent.return_id == tax_return.id,
        )
    )
    row = TaxReturnEvent(
        tenant_id=tax_return.tenant_id,
        return_id=tax_return.id,
        sequence=int(sequence or 0) + 1,
        from_status=from_status,
        to_status=to_status,
        actor_id=actor_id,
        authority_reference=authority_reference,
        occurred_at=occurred_at,
    )
    db.add(row)
    db.flush()
    return row


def create_tax_return(
    db: Session,
    *,
    tenant_id: UUID,
    report_id: UUID,
    adjustment: Money,
    created_by_id: UUID,
    created_at: datetime,
    original_return_id: UUID | None = None,
    amendment_reason: str | None = None,
) -> TaxReturn:
    report = db.scalar(
        select(StatutoryReport).where(
            StatutoryReport.tenant_id == tenant_id, StatutoryReport.id == report_id
        )
    )
    if report is None:
        raise TaxNotFound("statutory report not found")
    if not _currency_matches(
        adjustment.currency, report.currency_code, report.minor_units
    ):
        raise TaxRuleViolation("return adjustment uses the wrong currency")
    row = TaxReturn(
        tenant_id=tenant_id,
        report_id=report.id,
        obligation_id=report.obligation_id,
        status="draft",
        report_amount=report.total_payable,
        adjustment_amount=adjustment.amount,
        payable_amount=report.total_payable + adjustment.amount,
        currency_code=report.currency_code,
        minor_units=report.minor_units,
        created_by_id=created_by_id,
        created_at=created_at,
        original_return_id=original_return_id,
        amendment_reason=(amendment_reason.strip() if amendment_reason else None),
    )
    db.add(row)
    db.flush()
    _event(
        db,
        tax_return=row,
        from_status=None,
        to_status="draft",
        actor_id=created_by_id,
        occurred_at=created_at,
    )
    return row


def _return(db: Session, tenant_id: UUID, return_id: UUID) -> TaxReturn:
    row = db.scalar(
        select(TaxReturn)
        .where(TaxReturn.tenant_id == tenant_id, TaxReturn.id == return_id)
        .with_for_update()
    )
    if row is None:
        raise TaxNotFound("tax return not found")
    return row


def prepare_tax_return(
    db: Session,
    *,
    tenant_id: UUID,
    return_id: UUID,
    prepared_by_id: UUID,
    prepared_at: datetime,
) -> TaxReturn:
    row = _return(db, tenant_id, return_id)
    if row.status != "draft":
        raise TaxConflict("only a draft return can be prepared")
    previous = row.status
    row.status = "prepared"
    row.prepared_by_id = prepared_by_id
    row.prepared_at = prepared_at
    _event(
        db,
        tax_return=row,
        from_status=previous,
        to_status=row.status,
        actor_id=prepared_by_id,
        occurred_at=prepared_at,
    )
    db.flush()
    return row


def approve_tax_return(
    db: Session,
    *,
    tenant_id: UUID,
    return_id: UUID,
    approved_by_id: UUID,
    approved_at: datetime,
) -> TaxReturn:
    row = _return(db, tenant_id, return_id)
    if row.status != "prepared":
        raise TaxConflict("only a prepared return can be approved")
    if row.prepared_by_id == approved_by_id:
        raise TaxRuleViolation("return preparer cannot approve")
    previous = row.status
    row.status = "approved"
    row.approved_by_id = approved_by_id
    row.approved_at = approved_at
    _event(
        db,
        tax_return=row,
        from_status=previous,
        to_status=row.status,
        actor_id=approved_by_id,
        occurred_at=approved_at,
    )
    db.flush()
    return row


def file_tax_return(
    db: Session,
    *,
    tenant_id: UUID,
    return_id: UUID,
    filed_by_id: UUID,
    filed_at: datetime,
    filing_reference: str,
) -> TaxReturn:
    row = _return(db, tenant_id, return_id)
    if row.status != "approved":
        raise TaxConflict("only an approved return can be filed")
    previous = row.status
    row.status = "filed"
    row.filed_by_id = filed_by_id
    row.filed_at = filed_at
    row.filing_reference = _clean(filing_reference, "filing reference")
    obligation = db.scalar(
        select(TaxFilingObligation).where(
            TaxFilingObligation.tenant_id == tenant_id,
            TaxFilingObligation.id == row.obligation_id,
        )
    )
    if obligation is None:
        raise TaxNotFound("tax filing obligation not found")
    obligation.status = "filed"
    _event(
        db,
        tax_return=row,
        from_status=previous,
        to_status=row.status,
        actor_id=filed_by_id,
        occurred_at=filed_at,
        authority_reference=row.filing_reference,
    )
    db.flush()
    return row


def _authority_response(
    db: Session,
    *,
    tenant_id: UUID,
    return_id: UUID,
    status: str,
    recorded_by_id: UUID,
    recorded_at: datetime,
    authority_reference: str,
) -> TaxReturn:
    row = _return(db, tenant_id, return_id)
    if row.status != "filed":
        raise TaxConflict("only a filed return can receive an authority response")
    previous = row.status
    row.status = status
    row.authority_reference = _clean(authority_reference, "authority reference")
    obligation = db.scalar(
        select(TaxFilingObligation).where(
            TaxFilingObligation.tenant_id == tenant_id,
            TaxFilingObligation.id == row.obligation_id,
        )
    )
    if obligation is None:
        raise TaxNotFound("tax filing obligation not found")
    if status == "accepted":
        obligation.status = "accepted"
    _event(
        db,
        tax_return=row,
        from_status=previous,
        to_status=status,
        actor_id=recorded_by_id,
        occurred_at=recorded_at,
        authority_reference=row.authority_reference,
    )
    db.flush()
    return row


def accept_tax_return(
    db: Session,
    *,
    tenant_id: UUID,
    return_id: UUID,
    recorded_by_id: UUID,
    recorded_at: datetime,
    authority_reference: str,
) -> TaxReturn:
    return _authority_response(
        db,
        tenant_id=tenant_id,
        return_id=return_id,
        status="accepted",
        recorded_by_id=recorded_by_id,
        recorded_at=recorded_at,
        authority_reference=authority_reference,
    )


def reject_tax_return(
    db: Session,
    *,
    tenant_id: UUID,
    return_id: UUID,
    recorded_by_id: UUID,
    recorded_at: datetime,
    authority_reference: str,
) -> TaxReturn:
    return _authority_response(
        db,
        tenant_id=tenant_id,
        return_id=return_id,
        status="rejected",
        recorded_by_id=recorded_by_id,
        recorded_at=recorded_at,
        authority_reference=authority_reference,
    )


def amend_tax_return(
    db: Session,
    *,
    tenant_id: UUID,
    original_return_id: UUID,
    adjustment: Money,
    reason: str,
    created_by_id: UUID,
    created_at: datetime,
) -> TaxReturn:
    original = _return(db, tenant_id, original_return_id)
    if original.status not in {"filed", "accepted", "rejected"}:
        raise TaxConflict("only a filed or responded return can be amended")
    report = generate_statutory_report(
        db,
        tenant_id=tenant_id,
        obligation_id=original.obligation_id,
        generated_by_id=created_by_id,
        generated_at=created_at,
    )
    previous = original.status
    original.status = "superseded"
    _event(
        db,
        tax_return=original,
        from_status=previous,
        to_status="superseded",
        actor_id=created_by_id,
        occurred_at=created_at,
    )
    return create_tax_return(
        db,
        tenant_id=tenant_id,
        report_id=report.id,
        adjustment=adjustment,
        created_by_id=created_by_id,
        created_at=created_at,
        original_return_id=original.id,
        amendment_reason=_clean(reason, "amendment reason"),
    )


__all__ = [
    "TaxConflict",
    "TaxNotFound",
    "TaxRuleViolation",
    "accept_tax_return",
    "amend_tax_return",
    "approve_tax_return",
    "create_filing_obligation",
    "create_statutory_report_definition",
    "create_tax_authority",
    "create_tax_code",
    "create_tax_jurisdiction",
    "create_tax_return",
    "determine_tax",
    "determine_tax_set",
    "file_tax_return",
    "generate_statutory_report",
    "prepare_tax_return",
    "publish_tax_rule",
    "reject_tax_return",
    "update_tax_authority",
    "update_tax_code",
    "update_tax_jurisdiction",
]
