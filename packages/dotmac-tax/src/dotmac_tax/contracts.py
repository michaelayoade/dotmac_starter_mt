"""Typed tax commands with jurisdiction-specific vocabulary held as data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.money import Currency, Money

_REPORTABLE_ZERO_TREATMENTS = frozenset({"zero_rated", "exempt", "out_of_scope"})
_KNOWN_TREATMENTS = _REPORTABLE_ZERO_TREATMENTS | {"standard_rated"}
_KNOWN_CALCULATION_BASES = frozenset({"source_amount", "source_plus_prior_tax"})
_ZERO = Decimal(0)


def _require_non_negative_money(value: Money, label: str) -> Money:
    if not isinstance(value, Money):
        raise ValueError(f"{label} must be Money")
    if not value.amount.is_finite():
        raise ValueError(f"{label} must be finite")
    if value.amount < _ZERO:
        raise ValueError(f"{label} must be non-negative")
    return value


def _require_positive_int(value: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


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
class TaxDeterminationLineV1:
    """One exact progressive-band line in a determined tax component."""

    sequence: int
    taxable_amount: Money
    rate: Decimal | None
    tax_amount: Money

    def __post_init__(self) -> None:
        _require_positive_int(self.sequence, "determination line sequence")
        taxable = _require_non_negative_money(
            self.taxable_amount, "determination line taxable amount"
        )
        tax = _require_non_negative_money(
            self.tax_amount, "determination line tax amount"
        )
        if taxable.currency != tax.currency:
            raise ValueError("determination line amounts must use one currency")
        if self.rate is not None:
            if not isinstance(self.rate, Decimal) or not self.rate.is_finite():
                raise ValueError(
                    "determination line rate must be an exact finite Decimal"
                )
            if self.rate < _ZERO:
                raise ValueError("determination line rate must be non-negative")


@dataclass(frozen=True, slots=True)
class TaxDeterminationComponentV1:
    """One ordered, immutable component of a tax determination result.

    The component retains the selected rule and classification evidence even
    when its tax amount is zero. Accounting remains a product consequence;
    this contract contains no account, journal or posting direction.
    """

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
    lines: tuple[TaxDeterminationLineV1, ...]

    def __post_init__(self) -> None:
        _require_positive_int(
            self.component_sequence, "determination component sequence"
        )
        _require_positive_int(self.rule_version, "determination component rule version")
        if not isinstance(self.inclusive, bool):
            raise ValueError("determination component inclusive flag must be boolean")
        if self.treatment_code not in _KNOWN_TREATMENTS:
            raise ValueError(f"unknown tax treatment {self.treatment_code!r}")
        if self.calculation_base_code not in _KNOWN_CALCULATION_BASES:
            raise ValueError(
                f"unknown tax calculation base {self.calculation_base_code!r}"
            )
        base = _require_non_negative_money(
            self.base_amount, "determination component base amount"
        )
        tax = _require_non_negative_money(
            self.tax_amount, "determination component tax amount"
        )
        recoverable = _require_non_negative_money(
            self.recoverable_amount,
            "determination component recoverable amount",
        )
        non_recoverable = _require_non_negative_money(
            self.non_recoverable_amount,
            "determination component non-recoverable amount",
        )
        if any(
            value.currency != tax.currency
            for value in (base, recoverable, non_recoverable)
        ):
            raise ValueError("determination component amounts must use one currency")
        if recoverable + non_recoverable != tax:
            raise ValueError(
                "determination component recovery split must equal its tax amount"
            )
        if self.treatment_code in _REPORTABLE_ZERO_TREATMENTS and not tax.is_zero:
            raise ValueError(
                f"{self.treatment_code} determination component must have zero tax"
            )

        lines = tuple(self.lines)
        object.__setattr__(self, "lines", lines)
        if not lines:
            raise ValueError("determination component requires at least one line")
        sequences = [line.sequence for line in lines]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError(
                "determination component lines must have strict unique ordering"
            )
        if any(line.tax_amount.currency != tax.currency for line in lines):
            raise ValueError(
                "determination component lines must use the component currency"
            )
        line_tax = sum((line.tax_amount.amount for line in lines), _ZERO)
        if line_tax != tax.amount:
            raise ValueError("determination component lines must total its tax amount")

    @property
    def has_tax_consequence(self) -> bool:
        """Whether this component carries money for a product to account for."""

        return not self.tax_amount.is_zero

    @property
    def is_reportable_zero(self) -> bool:
        """Whether this is an explicit zero treatment retained for reporting."""

        return (
            self.treatment_code in _REPORTABLE_ZERO_TREATMENTS
            and self.tax_amount.is_zero
        )


@dataclass(frozen=True, slots=True)
class TaxDeterminationSetV1:
    """Published read-side result for one source fact determination.

    This is the module boundary returned by :func:`determine_tax_set`; callers
    never need to import the module's SQLAlchemy models. All monetary values
    are exact kernel ``Money`` carrying the persisted currency and minor-unit
    scale, and components retain distinct zero-treatment identities.
    """

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
    components: tuple[TaxDeterminationComponentV1, ...]

    def __post_init__(self) -> None:
        for name in (
            "source_ref",
            "source_version",
            "source_fingerprint",
            "result_fingerprint",
            "evidence_ref",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"determination {name.replace('_', ' ')} is required")
        if (
            len(self.result_fingerprint) != 68
            or not self.result_fingerprint.startswith("rv1:")
            or any(
                character not in "0123456789abcdef"
                for character in self.result_fingerprint[4:]
            )
        ):
            raise ValueError(
                "determination result fingerprint must be an rv1 SHA-256 digest"
            )
        if not isinstance(self.determined_at, datetime) or (
            self.determined_at.tzinfo is None or self.determined_at.utcoffset() is None
        ):
            raise ValueError("determination time must be timezone-aware")
        source = _require_non_negative_money(
            self.source_amount, "determination source amount"
        )
        net = _require_non_negative_money(self.net_amount, "determination net amount")
        tax = _require_non_negative_money(self.tax_amount, "determination tax amount")
        gross = _require_non_negative_money(
            self.gross_amount, "determination gross amount"
        )
        if any(value.currency != tax.currency for value in (source, net, gross)):
            raise ValueError("determination set amounts must use one currency")

        components = tuple(self.components)
        object.__setattr__(self, "components", components)
        if not components:
            raise ValueError("determination set requires at least one component")
        sequences = [component.component_sequence for component in components]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError(
                "determination components must have strict unique ordering"
            )
        if any(
            component.tax_amount.currency != tax.currency for component in components
        ):
            raise ValueError(
                "determination components must use the determination set currency"
            )
        if any(
            component.determination_set_id != self.determination_set_id
            for component in components
        ):
            raise ValueError(
                "determination component belongs to another determination set"
            )
        component_tax = sum(
            (component.tax_amount.amount for component in components), _ZERO
        )
        if component_tax != tax.amount:
            raise ValueError("determination components must total the set tax amount")
        if net + tax != gross:
            raise ValueError("determination net plus tax must equal gross")

        inclusive = [component for component in components if component.inclusive]
        if inclusive and len(components) > 1:
            raise ValueError(
                "an inclusive determination component cannot be combined with others"
            )
        expected_source = gross if inclusive else net
        if source != expected_source:
            relation = "gross" if inclusive else "net"
            raise ValueError(f"determination source amount must equal {relation}")

    @property
    def id(self) -> UUID:
        """Compatibility identity for callers previously reading the ORM row."""

        return self.determination_set_id

    @property
    def reportable_zero_components(self) -> tuple[TaxDeterminationComponentV1, ...]:
        """Explicit legal zero outcomes that still belong in statutory reports."""

        return tuple(
            component for component in self.components if component.is_reportable_zero
        )


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
    "TaxDeterminationComponentV1",
    "TaxDeterminationLineV1",
    "TaxDeterminationSetV1",
    "TaxFact",
    "TaxJurisdictionInput",
    "TaxRuleBandInput",
    "TaxRuleInput",
    "TaxSubjectClassificationInput",
]
