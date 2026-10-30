"""Typed tax commands with jurisdiction-specific vocabulary held as data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar, Literal
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
class TaxAuthorityV1:
    """Immutable, ORM-free view of one tenant tax authority."""

    tenant_id: UUID
    authority_id: UUID
    code: str
    name: str
    authority_level_code: str | None
    status: str


@dataclass(frozen=True, slots=True)
class TaxJurisdictionV1:
    """Immutable, ORM-free view of one tax jurisdiction."""

    tenant_id: UUID
    jurisdiction_id: UUID
    authority_id: UUID
    code: str
    name: str
    country_code: str
    subdivision_code: str | None
    currency: Currency
    status: str


@dataclass(frozen=True, slots=True)
class TaxCodeV1:
    """Immutable, ORM-free view of one composable tax code."""

    tenant_id: UUID
    tax_code_id: UUID
    jurisdiction_id: UUID
    code: str
    name: str
    tax_kind_code: str
    description: str | None
    status: str


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
class TaxRuleBandV1:
    """One exact progressive band in a published rule."""

    sequence: int
    lower_bound: Decimal
    upper_bound: Decimal | None
    rate: Decimal


@dataclass(frozen=True, slots=True)
class TaxRuleV1:
    """Immutable, ORM-free view of one effective-dated tax rule."""

    tenant_id: UUID
    rule_id: UUID
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
    party_category: str | None
    supply_category: str | None
    place_code: str | None
    treatment_code: str
    calculation_sequence: int
    calculation_base_code: str
    published_at: datetime
    bands: tuple[TaxRuleBandV1, ...]


@dataclass(frozen=True, slots=True)
class TaxRuleSupersessionInput:
    """Explicit replacement of one leaf policy; source identity is retry-stable."""

    predecessor_rule_id: UUID
    predecessor_version: int
    replacement: TaxRuleInput
    effective_from: date
    actor_id: UUID
    published_by_ref: str
    reason: str
    evidence_ref: str
    source_ref: str
    source_version: str


@dataclass(frozen=True, slots=True)
class TaxRuleSupersessionV1:
    tenant_id: UUID
    supersession_id: UUID
    predecessor_rule_id: UUID
    predecessor_version: int
    replacement: TaxRuleV1
    effective_from: date
    actor_id: UUID
    published_by_ref: str
    reason: str
    evidence_ref: str
    source_ref: str
    source_version: str
    published_at: datetime


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

    _calculation_bases: ClassVar[frozenset[str]] = _KNOWN_CALCULATION_BASES

    def __post_init__(self) -> None:
        _require_positive_int(
            self.component_sequence, "determination component sequence"
        )
        _require_positive_int(self.rule_version, "determination component rule version")
        if not isinstance(self.inclusive, bool):
            raise ValueError("determination component inclusive flag must be boolean")
        if self.treatment_code not in _KNOWN_TREATMENTS:
            raise ValueError(f"unknown tax treatment {self.treatment_code!r}")
        if self.calculation_base_code not in self._calculation_bases:
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

    _fingerprint_prefix: ClassVar[str] = "rv1:"

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
            or not self.result_fingerprint.startswith(self._fingerprint_prefix)
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
class TaxPolicyPreviewComponentV1:
    component_sequence: int
    tax_code_id: UUID
    rule_id: UUID
    rule_version: int
    treatment_code: str
    calculation_base_code: str
    inclusive: bool
    base_amount: Money
    tax_amount: Money
    recoverable_amount: Money
    non_recoverable_amount: Money
    lines: tuple[TaxDeterminationLineV1, ...]


@dataclass(frozen=True, slots=True)
class TaxPolicyPreviewV1:
    """Advisory current-policy calculation, never durable financial evidence."""

    tenant_id: UUID
    jurisdiction_id: UUID
    occurred_on: date
    source_amount: Money
    net_amount: Money
    tax_amount: Money
    gross_amount: Money
    components: tuple[TaxPolicyPreviewComponentV1, ...]


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
class TaxSubjectClassificationV1:
    """Immutable policy evidence for one subject classification version."""

    tenant_id: UUID
    classification_id: UUID
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
    source_fingerprint: str
    published_at: datetime


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
    "TaxAuthorityV1",
    "TaxCodeV1",
    "TaxDeterminationComponentV1",
    "TaxDeterminationLineV1",
    "TaxDeterminationSetV1",
    "TaxFact",
    "TaxPolicyPreviewV1",
    "TaxPolicyPreviewComponentV1",
    "TaxJurisdictionInput",
    "TaxJurisdictionV1",
    "TaxRuleBandInput",
    "TaxRuleBandV1",
    "TaxRuleInput",
    "TaxRuleSupersessionInput",
    "TaxRuleSupersessionV1",
    "TaxRuleV1",
    "TaxSubjectClassificationInput",
    "TaxSubjectClassificationV1",
]


@dataclass(frozen=True, slots=True)
class TaxCommercialSourceV1:
    source_ref: str
    source_version: str

    def __post_init__(self) -> None:
        for value in (self.source_ref, self.source_version):
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    "commercial source identity must be nonblank trimmed text"
                )


@dataclass(frozen=True, slots=True)
class TaxFactSignatureV1:
    jurisdiction_id: UUID
    fact_kind: str
    recognition_basis_code: str
    transaction_side: str

    def __post_init__(self) -> None:
        if not isinstance(self.jurisdiction_id, UUID):
            raise ValueError("signature jurisdiction must be a UUID")
        for value in (
            self.fact_kind,
            self.recognition_basis_code,
            self.transaction_side,
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError("signature values must be nonblank trimmed text")
        if self.transaction_side not in {"input", "output", "withholding", "liability"}:
            raise ValueError("unknown dependency signature transaction side")


@dataclass(frozen=True, slots=True)
class TaxBaseDependencyPolicyV1:
    prior_signature: TaxFactSignatureV1
    selection: Literal["all_prior", "tax_code_ids"]
    tax_code_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        codes = tuple(self.tax_code_ids)
        if not isinstance(self.prior_signature, TaxFactSignatureV1):
            raise ValueError("dependency prior signature is required")
        if self.selection not in {"all_prior", "tax_code_ids"}:
            raise ValueError("unknown dependency selection")
        if (
            self.selection == "all_prior"
            and codes
            or self.selection == "tax_code_ids"
            and not codes
        ):
            raise ValueError(
                "selected-code policy requires codes; all-prior forbids codes"
            )
        if any(not isinstance(code, UUID) for code in codes) or len(codes) != len(
            set(codes)
        ):
            raise ValueError("dependency code identities must be unique UUIDs")
        object.__setattr__(self, "tax_code_ids", tuple(sorted(codes, key=str)))


@dataclass(frozen=True, slots=True)
class TaxRuleInputV2(TaxRuleInput):
    dependency_policy: TaxBaseDependencyPolicyV1 | None = None


@dataclass(frozen=True, slots=True)
class TaxRuleV2(TaxRuleV1):
    dependency_policy: TaxBaseDependencyPolicyV1 | None = None


@dataclass(frozen=True, slots=True)
class TaxRuleSupersessionInputV2(TaxRuleSupersessionInput):
    replacement: TaxRuleInputV2


@dataclass(frozen=True, slots=True)
class TaxRuleSupersessionV2(TaxRuleSupersessionV1):
    replacement: TaxRuleV2


@dataclass(frozen=True, slots=True)
class TaxPriorResultReferenceV1:
    determination_set_id: UUID
    source_ref: str
    source_version: str
    source_fingerprint: str
    result_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.determination_set_id, UUID):
            raise ValueError("prior determination identity must be a UUID")
        TaxCommercialSourceV1(self.source_ref, self.source_version)
        for value, prefix in (
            (self.source_fingerprint, ""),
            (self.result_fingerprint, "rv2:"),
        ):
            digest = value[len(prefix) :]
            if (
                not value.startswith(prefix)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ValueError("prior result requires exact V2 fingerprints")


@dataclass(frozen=True, slots=True)
class TaxFactV2:
    fact: TaxFact
    commercial_source: TaxCommercialSourceV1
    prior_results: tuple[TaxPriorResultReferenceV1, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fact, TaxFact) or not isinstance(
            self.commercial_source, TaxCommercialSourceV1
        ):
            raise ValueError("V2 fact and commercial source are required")
        refs = tuple(self.prior_results)
        if any(not isinstance(ref, TaxPriorResultReferenceV1) for ref in refs):
            raise ValueError("prior results must be exact result references")
        if len({ref.determination_set_id for ref in refs}) != len(refs) or len(
            {(ref.source_ref, ref.source_version) for ref in refs}
        ) != len(refs):
            raise ValueError("duplicate prior result reference")
        object.__setattr__(
            self,
            "prior_results",
            tuple(sorted(refs, key=lambda ref: str(ref.determination_set_id))),
        )


@dataclass(frozen=True, slots=True)
class TaxDependencyComponentV1:
    determination_set_id: UUID
    determination_id: UUID
    tax_code_id: UUID
    rule_id: UUID
    rule_version: int
    component_sequence: int
    tax_amount: Money

    def __post_init__(self) -> None:
        _require_positive_int(self.rule_version, "dependency rule version")
        _require_positive_int(self.component_sequence, "dependency component sequence")
        _require_non_negative_money(self.tax_amount, "dependency tax amount")


@dataclass(frozen=True, slots=True)
class TaxDeterminationComponentV2(TaxDeterminationComponentV1):
    selected_dependency_policy: TaxBaseDependencyPolicyV1 | None = None
    dependency_components: tuple[TaxDependencyComponentV1, ...] = ()
    _calculation_bases: ClassVar[frozenset[str]] = _KNOWN_CALCULATION_BASES | {
        "source_plus_dependency_tax"
    }

    def __post_init__(self) -> None:
        TaxDeterminationComponentV1.__post_init__(self)
        snapshots = tuple(self.dependency_components)
        object.__setattr__(self, "dependency_components", snapshots)
        external = self.calculation_base_code == "source_plus_dependency_tax"
        if external != (
            self.selected_dependency_policy is not None
        ) or external != bool(snapshots):
            raise ValueError(
                "dependency basis requires policy and selected component snapshots"
            )
        if external and self.inclusive:
            raise ValueError("inclusive dependency tax is unsupported")
        if any(
            item.tax_amount.currency != self.tax_amount.currency for item in snapshots
        ):
            raise ValueError("dependency components must use the result currency")
        identities = [
            (item.determination_set_id, item.determination_id) for item in snapshots
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate dependency component")
        if snapshots:
            if len({item.determination_set_id for item in snapshots}) != 1:
                raise ValueError("dependency components must belong to one prior set")
            sequences = [item.component_sequence for item in snapshots]
            if sequences != sorted(set(sequences)):
                raise ValueError(
                    "dependency components require strict ordered sequences"
                )
            codes = [item.tax_code_id for item in snapshots]
            if len(codes) != len(set(codes)):
                raise ValueError("dependency code identity is ambiguous")
            policy = self.selected_dependency_policy
            if (
                policy is not None
                and policy.selection == "tax_code_ids"
                and set(codes) != set(policy.tax_code_ids)
            ):
                raise ValueError(
                    "dependency snapshots differ from selected policy codes"
                )


@dataclass(frozen=True, slots=True, kw_only=True)
class TaxDeterminationSetV2(TaxDeterminationSetV1):
    commercial_source: TaxCommercialSourceV1
    prior_results: tuple[TaxPriorResultReferenceV1, ...]
    components: tuple[TaxDeterminationComponentV2, ...]
    _fingerprint_prefix: ClassVar[str] = "rv2:"

    def __post_init__(self) -> None:
        TaxDeterminationSetV1.__post_init__(self)
        if not isinstance(self.commercial_source, TaxCommercialSourceV1):
            raise ValueError("V2 result requires commercial source identity")
        if any(
            not isinstance(item, TaxDeterminationComponentV2)
            for item in self.components
        ):
            raise ValueError("V2 result requires V2 components")
        refs = tuple(self.prior_results)
        object.__setattr__(self, "prior_results", refs)
        if any(not isinstance(ref, TaxPriorResultReferenceV1) for ref in refs):
            raise ValueError("V2 result requires complete prior references")
        if len({ref.determination_set_id for ref in refs}) != len(refs):
            raise ValueError("duplicate prior result identity")
        if len({(ref.source_ref, ref.source_version) for ref in refs}) != len(refs):
            raise ValueError("duplicate prior source identity")
        if [str(ref.determination_set_id) for ref in refs] != sorted(
            str(ref.determination_set_id) for ref in refs
        ):
            raise ValueError("prior references require canonical ordering")
        prior_ids = {ref.determination_set_id for ref in refs}
        if any(
            snapshot.determination_set_id not in prior_ids
            for component in self.components
            for snapshot in component.dependency_components
        ):
            raise ValueError("selected dependency has no complete prior reference")
        if any(
            component.calculation_base_code == "source_plus_dependency_tax"
            for component in self.components
        ) and any(
            component.calculation_base_code == "source_plus_prior_tax"
            or component.inclusive
            for component in self.components
        ):
            raise ValueError(
                "external dependency cannot combine with "
                "local compound or inclusive bases"
            )


@dataclass(frozen=True, slots=True)
class TaxPolicyPreviewComponentV2(TaxPolicyPreviewComponentV1):
    selected_dependency_policy: TaxBaseDependencyPolicyV1 | None = None
    dependency_components: tuple[TaxDependencyComponentV1, ...] = ()


@dataclass(frozen=True, slots=True)
class TaxPolicyPreviewV2(TaxPolicyPreviewV1):
    components: tuple[TaxPolicyPreviewComponentV2, ...]


__all__ += [
    "TaxCommercialSourceV1",
    "TaxFactSignatureV1",
    "TaxBaseDependencyPolicyV1",
    "TaxRuleInputV2",
    "TaxRuleV2",
    "TaxRuleSupersessionInputV2",
    "TaxRuleSupersessionV2",
    "TaxPriorResultReferenceV1",
    "TaxFactV2",
    "TaxDependencyComponentV1",
    "TaxDeterminationComponentV2",
    "TaxDeterminationSetV2",
    "TaxPolicyPreviewComponentV2",
    "TaxPolicyPreviewV2",
]
