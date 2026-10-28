"""Frozen bounded requests and results for tenant-owned tax policy browsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from dotmac_tax.contracts import (
    TaxAuthorityV1,
    TaxCodeV1,
    TaxJurisdictionV1,
    TaxRuleV1,
    TaxRuleV2,
)


def _optional_text(value: str | None, label: str, maximum: int) -> None:
    if value is not None and (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(
            f"{label} must be nonblank trimmed text of at most {maximum} characters"
        )


def _optional_uuid(value: UUID | None, label: str) -> None:
    if value is not None and not isinstance(value, UUID):
        raise ValueError(f"{label} must be a UUID")


@dataclass(frozen=True, slots=True, kw_only=True)
class _PolicyPageRequest:
    page_size: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if type(self.page_size) is not int or not 1 <= self.page_size <= 100:
            raise ValueError("page_size must be an integer between 1 and 100")
        _optional_text(self.cursor, "cursor", 1024)


@dataclass(frozen=True, slots=True, kw_only=True)
class ListTaxAuthoritiesV1(_PolicyPageRequest):
    status: str | None = None

    def __post_init__(self) -> None:
        _PolicyPageRequest.__post_init__(self)
        if self.status not in (None, "active", "retired"):
            raise ValueError("status must be active or retired")


@dataclass(frozen=True, slots=True, kw_only=True)
class ListTaxJurisdictionsV1(ListTaxAuthoritiesV1):
    authority_id: UUID | None = None
    country_code: str | None = None

    def __post_init__(self) -> None:
        ListTaxAuthoritiesV1.__post_init__(self)
        _optional_uuid(self.authority_id, "authority_id")
        if self.country_code is not None and (
            not isinstance(self.country_code, str)
            or len(self.country_code) != 2
            or not self.country_code.isascii()
            or not self.country_code.isalpha()
            or self.country_code != self.country_code.upper()
        ):
            raise ValueError("country_code must be two uppercase ASCII letters")


@dataclass(frozen=True, slots=True, kw_only=True)
class ListTaxCodesV1(ListTaxAuthoritiesV1):
    jurisdiction_id: UUID | None = None
    tax_kind_code: str | None = None

    def __post_init__(self) -> None:
        ListTaxAuthoritiesV1.__post_init__(self)
        _optional_uuid(self.jurisdiction_id, "jurisdiction_id")
        _optional_text(self.tax_kind_code, "tax_kind_code", 100)


@dataclass(frozen=True, slots=True, kw_only=True)
class ListTaxRuleHistoryV1(_PolicyPageRequest):
    tax_code_id: UUID

    def __post_init__(self) -> None:
        _PolicyPageRequest.__post_init__(self)
        if not isinstance(self.tax_code_id, UUID):
            raise ValueError("tax_code_id must be a UUID")


@dataclass(frozen=True, slots=True)
class TaxAuthorityPageV1:
    items: tuple[TaxAuthorityV1, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TaxJurisdictionPageV1:
    items: tuple[TaxJurisdictionV1, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TaxCodePageV1:
    items: tuple[TaxCodeV1, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TaxRuleHistorySupersessionV1:
    """Append-only publication provenance linking predecessor and replacement."""

    supersession_id: UUID
    predecessor_rule_id: UUID
    predecessor_version: int
    replacement_rule_id: UUID
    replacement_version: int
    effective_from: date
    actor_id: UUID
    published_by_ref: str
    reason: str
    evidence_ref: str
    source_ref: str
    source_version: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class TaxRuleHistoryEntryV1:
    rule: TaxRuleV1
    supersession: TaxRuleHistorySupersessionV1 | None


@dataclass(frozen=True, slots=True)
class TaxRuleHistoryPageV1:
    items: tuple[TaxRuleHistoryEntryV1, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ListTaxRuleHistoryV2(ListTaxRuleHistoryV1):
    pass


@dataclass(frozen=True, slots=True)
class TaxRuleHistoryEntryV2(TaxRuleHistoryEntryV1):
    rule: TaxRuleV2


@dataclass(frozen=True, slots=True)
class TaxRuleHistoryPageV2(TaxRuleHistoryPageV1):
    items: tuple[TaxRuleHistoryEntryV2, ...]
