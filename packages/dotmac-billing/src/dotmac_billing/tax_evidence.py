"""Billing-owned, lossless copies of Tax-authored sealed determination results.

Assemblies translate the public Tax DTOs at the module boundary. Billing checks
identity, money and totals; it never selects a rule, calculates a rate or rerates
historical evidence. Expected withholding is display evidence, not discharge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import ClassVar, Literal
from uuid import UUID

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.money import Money


def _money(value: Money) -> None:
    if not isinstance(value, Money) or not value.amount.is_finite() or value.amount < 0:
        raise ValueError("Tax evidence requires nonnegative finite Money")
    with localcontext() as context:
        context.prec = 60
        if (
            not 0 <= value.currency.minor_units <= 6
            or value.amount.adjusted() >= 14
            or value.amount
            != value.amount.quantize(Decimal(1).scaleb(-value.currency.minor_units))
        ):
            raise ValueError("Tax evidence money exceeds Billing precision")


def _rate(value: Decimal | None) -> None:
    if value is not None and (
        not isinstance(value, Decimal) or not value.is_finite() or value < 0
    ):
        raise ValueError(
            "Tax evidence rate must be absent or a nonnegative finite Decimal"
        )


def _positive(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("Tax evidence version/sequence must be a positive integer")


def _total(values: tuple[Money, ...]) -> Decimal:
    with localcontext() as context:
        context.prec = 60
        return sum((value.amount for value in values), Decimal(0))


def _ordered(values: tuple[int, ...]) -> None:
    if not values or values != tuple(sorted(set(values))):
        raise ValueError("Tax evidence must have nonempty strict unique ordering")


@dataclass(frozen=True, slots=True)
class TaxDeterminationLineEvidenceV1:
    sequence: int
    taxable_amount: Money
    rate: Decimal | None
    tax_amount: Money

    def __post_init__(self) -> None:
        _positive(self.sequence)
        _rate(self.rate)
        _money(self.taxable_amount)
        _money(self.tax_amount)
        if self.taxable_amount.currency != self.tax_amount.currency:
            raise ValueError("Tax line currency differs")


@dataclass(frozen=True, slots=True)
class TaxDeterminationComponentEvidenceV1:
    determination_id: UUID
    determination_set_id: UUID
    component_sequence: int
    tax_code_id: UUID
    rule_id: UUID
    rule_version: int
    treatment_code: str
    calculation_base_code: str
    inclusive: bool
    party_category: str | None
    supply_category: str | None
    place_code: str | None
    party_classification_id: UUID | None
    supply_classification_id: UUID | None
    place_classification_id: UUID | None
    base_amount: Money
    tax_amount: Money
    recoverable_amount: Money
    non_recoverable_amount: Money
    lines: tuple[TaxDeterminationLineEvidenceV1, ...]

    def __post_init__(self) -> None:
        _positive(self.component_sequence)
        _positive(self.rule_version)
        for identity in (
            self.determination_id,
            self.determination_set_id,
            self.tax_code_id,
            self.rule_id,
        ):
            if not isinstance(identity, UUID):
                raise ValueError("Tax component identity must be a UUID")
        if (
            not isinstance(self.treatment_code, str)
            or not self.treatment_code.strip()
            or not isinstance(self.calculation_base_code, str)
            or not self.calculation_base_code.strip()
            or type(self.inclusive) is not bool
        ):
            raise ValueError(
                "Tax component requires treatment, base and inclusive evidence"
            )
        amounts = (
            self.base_amount,
            self.tax_amount,
            self.recoverable_amount,
            self.non_recoverable_amount,
        )
        for amount in amounts:
            _money(amount)
        if any(amount.currency != self.tax_amount.currency for amount in amounts):
            raise ValueError("Tax component currency differs")
        if (
            _total((self.recoverable_amount, self.non_recoverable_amount))
            != self.tax_amount.amount
        ):
            raise ValueError("Tax component recovery split must total tax")
        object.__setattr__(self, "lines", tuple(self.lines))
        if any(
            not isinstance(line, TaxDeterminationLineEvidenceV1) for line in self.lines
        ):
            raise ValueError("Tax component requires typed lines")
        for optional_identity in (
            self.party_classification_id,
            self.supply_classification_id,
            self.place_classification_id,
        ):
            if optional_identity is not None and not isinstance(
                optional_identity, UUID
            ):
                raise ValueError("Tax classification identity must be an optional UUID")
        for category in (self.party_category, self.supply_category, self.place_code):
            if category is not None and not isinstance(category, str):
                raise ValueError("Tax classification must be optional text")
        _ordered(tuple(line.sequence for line in self.lines))
        for line in self.lines:
            if not isinstance(line, TaxDeterminationLineEvidenceV1):
                raise ValueError("Tax component requires typed lines")
            line.__post_init__()
            if line.tax_amount.currency != self.tax_amount.currency:
                raise ValueError("Tax component line currency differs")
        if (
            _total(tuple(line.tax_amount for line in self.lines))
            != self.tax_amount.amount
        ):
            raise ValueError("Tax component lines must total tax")


@dataclass(frozen=True, slots=True)
class TaxDeterminationSetEvidenceV1:
    _fingerprint_prefix: ClassVar[str] = "rv1:"
    _component_type: ClassVar[type] = TaxDeterminationComponentEvidenceV1
    tenant_id: UUID
    determination_set_id: UUID
    jurisdiction_id: UUID
    occurred_on: date
    fact_kind: str
    recognition_basis_code: str
    transaction_side: str
    source_amount: Money
    net_amount: Money
    tax_amount: Money
    gross_amount: Money
    source_ref: str
    source_version: str
    source_fingerprint: str
    result_fingerprint: str
    evidence_ref: str
    counterparty_ref: str | None
    supply_ref: str | None
    place_ref: str | None
    determined_at: datetime
    components: tuple[TaxDeterminationComponentEvidenceV1, ...]

    def __post_init__(self) -> None:
        if any(
            not isinstance(identity, UUID)
            for identity in (
                self.tenant_id,
                self.determination_set_id,
                self.jurisdiction_id,
            )
        ):
            raise ValueError("Tax set identity must be a UUID")
        for name in (
            "fact_kind",
            "recognition_basis_code",
            "transaction_side",
            "source_ref",
            "source_version",
            "evidence_ref",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Tax set requires source and recognition evidence")
        if (
            re.fullmatch(r"[0-9a-f]{64}", self.source_fingerprint) is None
            or re.fullmatch(
                self._fingerprint_prefix + r"[0-9a-f]{64}", self.result_fingerprint
            )
            is None
        ):
            raise ValueError("Tax set requires source and sealed result fingerprints")
        if (
            type(self.occurred_on) is not date
            or not isinstance(self.determined_at, datetime)
            or self.determined_at.tzinfo is None
            or self.determined_at.utcoffset() is None
        ):
            raise ValueError(
                "Tax set requires exact tax date and aware determination time"
            )
        amounts = (
            self.source_amount,
            self.net_amount,
            self.tax_amount,
            self.gross_amount,
        )
        for amount in amounts:
            _money(amount)
        if any(amount.currency != self.tax_amount.currency for amount in amounts):
            raise ValueError("Tax set currency differs")
        object.__setattr__(self, "components", tuple(self.components))
        if any(
            type(component) is not self._component_type for component in self.components
        ):
            raise ValueError("Tax set requires typed components")
        for reference in (self.counterparty_ref, self.supply_ref, self.place_ref):
            if reference is not None and not isinstance(reference, str):
                raise ValueError("Tax set references must be optional text")
        _ordered(tuple(component.component_sequence for component in self.components))
        if len({component.determination_id for component in self.components}) != len(
            self.components
        ):
            raise ValueError("Tax component identities must be unique")
        for component in self.components:
            if type(component) is not self._component_type:
                raise ValueError("Tax set requires typed components")
            component.__post_init__()
            if (
                component.determination_set_id != self.determination_set_id
                or component.tax_amount.currency != self.tax_amount.currency
            ):
                raise ValueError("Tax component set or currency differs")
        if (
            _total(tuple(component.tax_amount for component in self.components))
            != self.tax_amount.amount
        ):
            raise ValueError("Tax components must total set tax")
        if _total((self.net_amount, self.tax_amount)) != self.gross_amount.amount:
            raise ValueError("Tax set net plus tax must total gross")
        inclusive = any(component.inclusive for component in self.components)
        if inclusive and len(self.components) != 1:
            raise ValueError("Tax inclusive set requires exactly one component")
        if self.source_amount != (self.gross_amount if inclusive else self.net_amount):
            raise ValueError(
                "Tax source must match the authored inclusive/exclusive result"
            )


@dataclass(frozen=True, slots=True)
class InvoiceTaxEvidenceV1:
    purpose: Literal["invoice_tax", "expected_withholding"]
    determination: TaxDeterminationSetEvidenceV1

    def __post_init__(self) -> None:
        if (
            self.purpose
            not in (
                "invoice_tax",
                "expected_withholding",
            )
            or type(self.determination) is not TaxDeterminationSetEvidenceV1
        ):
            raise ValueError(
                "Invoice Tax evidence requires explicit purpose and typed determination"
            )
        self.determination.__post_init__()


def validate_invoice_tax_evidence(
    evidence: tuple[InvoiceTaxEvidenceV1, ...], *, scope: Scope, tax_amount: Money
) -> None:
    """Validate the retained evidence; do not infer a base from invoice subtotal."""
    seen: set[UUID] = set()
    for item in evidence:
        if not isinstance(item, InvoiceTaxEvidenceV1):
            raise ValueError("Invoice Tax evidence must be typed")
        item.__post_init__()
        result = item.determination
        # The current Tax result contract is tenant-only. No invented platform
        # tenant or implicit cross-plane translation is admissible.
        if not isinstance(scope, TenantScope) or result.tenant_id != scope.tenant_id:
            raise ValueError("Tax evidence scope differs from invoice scope")
        if result.tax_amount.currency != tax_amount.currency:
            raise ValueError("Tax evidence currency differs from invoice currency")
        if result.determination_set_id in seen:
            raise ValueError("Tax evidence determination set is duplicated")
        seen.add(result.determination_set_id)
    if (
        _total(
            tuple(
                item.determination.tax_amount
                for item in evidence
                if item.purpose == "invoice_tax"
            )
        )
        != tax_amount.amount
    ):
        raise ValueError("Invoice Tax evidence must total rated invoice tax")


__all__ = [
    "InvoiceTaxEvidenceV1",
    "TaxDeterminationLineEvidenceV1",
    "TaxDeterminationComponentEvidenceV1",
    "TaxDeterminationSetEvidenceV1",
]
