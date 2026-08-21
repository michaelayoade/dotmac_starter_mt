"""Pure commands, vocabulary and refusals for ``dotmac-accounting``."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID


class AccountingError(ValueError):
    """Base for fail-closed accounting contract errors."""


class NotFound(AccountingError):
    """A row does not exist in the declared tenant scope."""


class Conflict(AccountingError):
    """A tenant identity or source command conflicts with existing state."""


class InvalidAccount(AccountingError):
    """An account/category command is malformed or inadmissible."""


class InvalidPeriod(AccountingError):
    """A fiscal period definition or transition is inadmissible."""


class InvalidDimension(AccountingError):
    """An accounting dimension/value assignment is inadmissible."""


class InvalidJournal(AccountingError):
    """A journal is malformed, unbalanced or cannot be posted."""


class InvalidTransition(AccountingError):
    """A journal lifecycle transition is not allowed."""


class AccountClass(enum.StrEnum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"
    OTHER_COMPREHENSIVE_INCOME = "OTHER_COMPREHENSIVE_INCOME"


class AccountKind(enum.StrEnum):
    CONTROL = "CONTROL"
    POSTING = "POSTING"
    STATISTICAL = "STATISTICAL"


class NormalBalance(enum.StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class PeriodStatus(enum.StrEnum):
    FUTURE = "FUTURE"
    OPEN = "OPEN"
    SOFT_CLOSED = "SOFT_CLOSED"
    REOPENED = "REOPENED"
    LOCKED = "LOCKED"


class JournalKind(enum.StrEnum):
    STANDARD = "STANDARD"
    ADJUSTMENT = "ADJUSTMENT"
    CLOSING = "CLOSING"
    OPENING = "OPENING"
    REVERSAL = "REVERSAL"


class JournalStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    POSTED = "POSTED"
    REVERSED = "REVERSED"
    VOID = "VOID"


def _required(value: str, label: str, *, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise AccountingError(f"{label} is required")
    if len(normalized) > maximum:
        raise AccountingError(f"{label} must be at most {maximum} characters")
    return normalized


def _code(value: str, label: str, *, maximum: int) -> str:
    return _required(value, label, maximum=maximum).upper()


def _currency(value: str) -> str:
    currency = _code(value, "currency code", maximum=3)
    if len(currency) != 3:
        raise AccountingError("currency code must be exactly three characters")
    return currency


def _fingerprint(value: str) -> str:
    fingerprint = value.strip().lower()
    if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        raise AccountingError("source fingerprint must be a 64-character SHA-256")
    return fingerprint


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    owner: str
    document_kind: str
    document_id: str
    version: str
    fingerprint: str

    def normalized(self) -> SourceIdentity:
        return SourceIdentity(
            owner=_required(self.owner, "source owner", maximum=120),
            document_kind=_required(
                self.document_kind, "source document kind", maximum=80
            ),
            document_id=_required(self.document_id, "source document id", maximum=255),
            version=_required(self.version, "source version", maximum=120),
            fingerprint=_fingerprint(self.fingerprint),
        )

    def payload(self) -> dict[str, str]:
        value = self.normalized()
        return {
            "owner": value.owner,
            "document_kind": value.document_kind,
            "document_id": value.document_id,
            "version": value.version,
            "fingerprint": value.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class CreateAccountCategory:
    code: str
    name: str
    account_class: AccountClass
    description: str | None = None
    parent_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CreateAccount:
    code: str
    name: str
    category_id: UUID
    kind: AccountKind
    normal_balance: NormalBalance
    description: str | None = None
    parent_id: UUID | None = None
    currency_code: str | None = None
    posting_allowed: bool = True


@dataclass(frozen=True, slots=True)
class CreateFiscalYear:
    code: str
    name: str
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class CreateFiscalPeriod:
    fiscal_year_id: UUID
    period_number: int
    name: str
    start_date: date
    end_date: date
    is_adjustment: bool = False


@dataclass(frozen=True, slots=True)
class CreateDimension:
    code: str
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CreateDimensionValue:
    dimension_id: UUID
    code: str
    name: str
    parent_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class JournalLineInput:
    account_id: UUID
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str | None = None
    dimension_value_ids: tuple[UUID, ...] = ()

    def validate(self) -> None:
        if self.debit < 0 or self.credit < 0:
            raise InvalidJournal("journal amounts cannot be negative")
        if (self.debit > 0) == (self.credit > 0):
            raise InvalidJournal("each journal line must carry an amount on one side")
        if len(set(self.dimension_value_ids)) != len(self.dimension_value_ids):
            raise InvalidDimension("a journal line cannot repeat a dimension value")

    def payload(self) -> dict[str, object]:
        return {
            "account_id": str(self.account_id),
            "debit": str(self.debit),
            "credit": str(self.credit),
            "description": self.description,
            "dimension_value_ids": sorted(
                str(value) for value in self.dimension_value_ids
            ),
        }


@dataclass(frozen=True, slots=True)
class CreateJournal:
    number: str
    kind: JournalKind
    entry_date: date
    posting_date: date
    currency_code: str
    exchange_rate: Decimal
    description: str
    source: SourceIdentity
    lines: tuple[JournalLineInput, ...]
    reference: str | None = None

    def fingerprint_payload(self) -> dict[str, object]:
        reference = (
            _required(self.reference, "journal reference", maximum=255)
            if self.reference is not None
            else None
        )
        return {
            "number": _code(self.number, "journal number", maximum=50),
            "kind": self.kind.value,
            "entry_date": self.entry_date.isoformat(),
            "posting_date": self.posting_date.isoformat(),
            "currency_code": _currency(self.currency_code),
            "exchange_rate": str(self.exchange_rate),
            "description": _required(self.description, "description", maximum=2000),
            "reference": reference,
            "source": self.source.payload(),
            "lines": [line.payload() for line in self.lines],
        }


@dataclass(frozen=True, slots=True)
class ReverseJournal:
    journal_id: UUID
    number: str
    posting_date: date
    reason: str
    approval_reference: str
    reversed_by: str
    reopen_token: UUID | None = None

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "journal_id": str(self.journal_id),
            "number": _code(self.number, "reversal number", maximum=50),
            "posting_date": self.posting_date.isoformat(),
            "reason": _required(self.reason, "reversal reason", maximum=2000),
            "approval_reference": _required(
                self.approval_reference, "approval reference", maximum=255
            ),
            "reversed_by": _required(self.reversed_by, "reversed by", maximum=255),
            "reopen_token": str(self.reopen_token) if self.reopen_token else None,
        }


@dataclass(frozen=True, slots=True)
class CloseCheck:
    code: str
    passed: bool
    evidence_reference: str
    fingerprint: str

    def payload(self) -> dict[str, object]:
        return {
            "code": _code(self.code, "close check code", maximum=80),
            "passed": self.passed,
            "evidence_reference": _required(
                self.evidence_reference, "close check evidence", maximum=255
            ),
            "fingerprint": _fingerprint(self.fingerprint),
        }


@dataclass(frozen=True, slots=True)
class PeriodCloseEvidence:
    checks: tuple[CloseCheck, ...]

    def payload(self) -> dict[str, object]:
        if not self.checks:
            raise InvalidPeriod("period close requires at least one close check")
        payload = [check.payload() for check in self.checks]
        failed = [str(check["code"]) for check in payload if not check["passed"]]
        if failed:
            raise InvalidPeriod(f"period close checks failed: {', '.join(failed)}")
        codes = [str(check["code"]) for check in payload]
        if len(set(codes)) != len(codes):
            raise InvalidPeriod("period close check codes must be unique")
        return {"checks": payload}


__all__ = [
    "AccountClass",
    "AccountKind",
    "AccountingError",
    "CloseCheck",
    "Conflict",
    "CreateAccount",
    "CreateAccountCategory",
    "CreateDimension",
    "CreateDimensionValue",
    "CreateFiscalPeriod",
    "CreateFiscalYear",
    "CreateJournal",
    "InvalidAccount",
    "InvalidDimension",
    "InvalidJournal",
    "InvalidPeriod",
    "InvalidTransition",
    "JournalKind",
    "JournalLineInput",
    "JournalStatus",
    "NormalBalance",
    "NotFound",
    "PeriodCloseEvidence",
    "PeriodStatus",
    "ReverseJournal",
    "SourceIdentity",
]
