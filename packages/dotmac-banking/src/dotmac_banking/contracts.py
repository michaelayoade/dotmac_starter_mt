"""Typed banking commands and module-owned mechanics vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from dotmac_kernel.money import Currency, Money


class StatementLineDirection(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


@dataclass(frozen=True, slots=True)
class BankInstitutionInput:
    code: str
    name: str
    country_code: str
    clearing_code: str | None = None


@dataclass(frozen=True, slots=True)
class BankAccountInput:
    institution_id: UUID
    account_code: str
    account_name: str
    account_identifier: str
    account_type_code: str
    currency: Currency
    cash_account_ref: str


@dataclass(frozen=True, slots=True)
class StatementLineInput:
    line_number: int
    transaction_date: date
    direction: StatementLineDirection
    amount: Money
    external_ref: str
    description: str
    value_date: date | None = None
    reference: str | None = None
    counterparty: str | None = None
    bank_transaction_code: str | None = None


@dataclass(frozen=True, slots=True)
class BankStatementInput:
    account_id: UUID
    statement_ref: str
    period_start: date
    period_end: date
    opening_balance: Money | None
    closing_balance: Money | None
    source_ref: str
    source_version: str
    evidence_ref: str
    lines: tuple[StatementLineInput, ...]


@dataclass(frozen=True, slots=True)
class CashObservationInput:
    account_id: UUID
    effective_on: date
    direction: StatementLineDirection
    amount: Money
    source_ref: str
    source_version: str
    evidence_ref: str
    description: str
    reference: str | None = None
    counterparty_ref: str | None = None


@dataclass(frozen=True, slots=True)
class MatchPolicyInput:
    code: str
    name: str
    amount_tolerance: Decimal
    date_window_days: int
    reference_match_mode: str
    amount_weight: int
    date_weight: int
    reference_weight: int
    minimum_confidence: int
    direction: StatementLineDirection | None = None


@dataclass(frozen=True, slots=True)
class MatchSuggestion:
    observation_id: UUID
    confidence: int
    amount_score: int
    date_score: int
    reference_score: int


__all__ = [
    "BankAccountInput",
    "BankInstitutionInput",
    "BankStatementInput",
    "CashObservationInput",
    "MatchPolicyInput",
    "MatchSuggestion",
    "StatementLineDirection",
    "StatementLineInput",
]
