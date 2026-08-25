"""Flush-only owner for tax policy, determinations, reports and returns."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from dotmac_kernel.money import Currency, Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_tax.contracts import (
    StatutoryReportBoxInput,
    TaxAuthorityInput,
    TaxFact,
    TaxJurisdictionInput,
    TaxRuleInput,
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
    TaxFilingObligation,
    TaxJurisdiction,
    TaxReturn,
    TaxReturnEvent,
    TaxRule,
    TaxRuleBand,
)


class TaxNotFound(LookupError):
    """A tenant-local tax record does not exist."""


class TaxConflict(ValueError):
    """A tax identity or immutable evidence conflicts with existing data."""


class TaxRuleViolation(ValueError):
    """A tax policy, determination or filing transition is invalid."""


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


def _optional_match(configured: str | None, observed: str | None) -> bool:
    return configured is None or configured == observed


def _applicable_rule(db: Session, tenant_id: UUID, fact: TaxFact) -> TaxRule:
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
    ranked = [
        (
            row.priority,
            sum(
                value is not None
                for value in (row.party_category, row.supply_category, row.place_code)
            ),
            row.version,
            row,
        )
        for row in rows
        if _optional_match(row.party_category, fact.party_category)
        and _optional_match(row.supply_category, fact.supply_category)
        and _optional_match(row.place_code, fact.place_code)
    ]
    if not ranked:
        raise TaxRuleViolation("no applicable tax rule")
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    top = ranked[0]
    if len(ranked) > 1 and ranked[1][:2] == top[:2]:
        raise TaxRuleViolation("tax determination is ambiguous at the highest priority")
    return top[3]


def determine_tax(
    db: Session,
    *,
    tenant_id: UUID,
    fact: TaxFact,
    determined_at: datetime,
) -> TaxDetermination:
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
    existing = db.scalar(
        select(TaxDetermination).where(
            TaxDetermination.tenant_id == tenant_id,
            TaxDetermination.source_ref == source_ref,
            TaxDetermination.source_version == source_version,
        )
    )
    if existing is not None:
        if existing.source_fingerprint != fingerprint:
            raise TaxConflict("tax source version was reused with different facts")
        return existing
    rule = _applicable_rule(db, tenant_id, fact)
    base = fact.base_amount.amount
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
        tax_amount = _round(tax_amount, jurisdiction.minor_units)
        base = _round(base, jurisdiction.minor_units)
        line_values.append((base, rule.rate, tax_amount))
    elif rule.calculation_method == "fixed":
        if rule.fixed_amount is None:
            raise TaxRuleViolation("fixed tax rule has no amount")
        tax_amount = _round(rule.fixed_amount, jurisdiction.minor_units)
        line_values.append((base, None, tax_amount))
    else:
        tax_amount = Decimal(0)
        if not rule.bands:
            raise TaxRuleViolation("progressive tax rule has no bands")
        for band in rule.bands:
            upper = base if band.upper_bound is None else min(base, band.upper_bound)
            taxable = max(Decimal(0), upper - band.lower_bound)
            amount = _round(taxable * band.rate, jurisdiction.minor_units)
            line_values.append((taxable, band.rate, amount))
            tax_amount += amount
        tax_amount = _round(tax_amount, jurisdiction.minor_units)
    recoverable = _round(tax_amount * rule.recoverable_rate, jurisdiction.minor_units)
    non_recoverable = tax_amount - recoverable
    row = TaxDetermination(
        tenant_id=tenant_id,
        jurisdiction_id=jurisdiction.id,
        tax_code_id=rule.tax_code_id,
        rule_id=rule.id,
        rule_version=rule.version,
        occurred_on=fact.occurred_on,
        fact_kind=fact.fact_kind,
        recognition_basis_code=fact.recognition_basis_code,
        transaction_side=fact.transaction_side,
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
    row.lines.extend(
        TaxDeterminationLine(
            tenant_id=tenant_id,
            sequence=sequence,
            taxable_amount=taxable,
            rate=rate,
            tax_amount=amount,
        )
        for sequence, (taxable, rate, amount) in enumerate(line_values, start=1)
    )
    db.add(row)
    db.flush()
    return row


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
    amounts: dict[str, Decimal] = {}
    for box in definition.boxes:
        column = getattr(TaxDetermination, box.value_source)
        raw = db.scalar(
            select(func.coalesce(func.sum(column), 0)).where(
                TaxDetermination.tenant_id == tenant_id,
                TaxDetermination.jurisdiction_id == definition.jurisdiction_id,
                TaxDetermination.tax_code_id == box.tax_code_id,
                TaxDetermination.occurred_on >= obligation.period_start,
                TaxDetermination.occurred_on <= obligation.period_end,
            )
        )
        amounts[box.box_code] = _round(
            Decimal(raw or 0) * box.multiplier, definition.minor_units
        )
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
    "file_tax_return",
    "generate_statutory_report",
    "prepare_tax_return",
    "publish_tax_rule",
    "reject_tax_return",
    "update_tax_authority",
    "update_tax_code",
    "update_tax_jurisdiction",
]
