"""Typed tax commands with jurisdiction-specific vocabulary held as data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.money import Currency, Money


@dataclass(frozen=True, slots=True)
class TaxAuthorityInput:
    code: str
    name: str
    authority_level_code: str | None = None


@dataclass(frozen=True, slots=True)
class TaxJurisdictionInput:
    authority_id: UUID
    code: str
    name: str
    country_code: str
    currency: Currency
    subdivision_code: str | None = None


@dataclass(frozen=True, slots=True)
class TaxRuleBandInput:
    sequence: int
    lower_bound: Decimal
    upper_bound: Decimal | None
    rate: Decimal


@dataclass(frozen=True, slots=True)
class TaxRuleInput:
    tax_code_id: UUID
    version: int
    effective_from: date
    effective_to: date | None
    priority: int
    fact_kind: str
    recognition_basis_code: str
    transaction_side: str
    calculation_method: str
    rate: Decimal | None
    fixed_amount: Money | None
    inclusive: bool
    recoverable_rate: Decimal
    party_category: str | None = None
    supply_category: str | None = None
    place_code: str | None = None
    bands: tuple[TaxRuleBandInput, ...] = ()
    treatment_code: str = "standard_rated"
    calculation_sequence: int = 100
    calculation_base_code: str = "source_amount"


@dataclass(frozen=True, slots=True)
class TaxFact:
    jurisdiction_id: UUID
    occurred_on: date
    fact_kind: str
    recognition_basis_code: str
    transaction_side: str
    base_amount: Money
    source_ref: str
    source_version: str
    evidence_ref: str
    party_category: str | None = None
    supply_category: str | None = None
    place_code: str | None = None
    counterparty_ref: str | None = None
    supply_ref: str | None = None
    place_ref: str | None = None


@dataclass(frozen=True, slots=True)
class TaxSubjectClassificationInput:
    tax_code_id: UUID
    subject_kind: str
    subject_ref: str
    category_code: str
    version: int
    effective_from: date
    effective_to: date | None
    basis_code: str
    evidence_ref: str
    published_by_ref: str
    source_ref: str
    source_version: str


@dataclass(frozen=True, slots=True)
class StatutoryReportBoxInput:
    box_code: str
    label: str
    sequence: int
    tax_code_id: UUID
    value_source: str
    multiplier: Decimal


__all__ = [
    "StatutoryReportBoxInput",
    "TaxAuthorityInput",
    "TaxFact",
    "TaxJurisdictionInput",
    "TaxRuleBandInput",
    "TaxRuleInput",
    "TaxSubjectClassificationInput",
]
