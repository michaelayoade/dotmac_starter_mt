"""Billing mirrors of Tax dependency contracts; no tax calculation or policy lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, cast
from uuid import UUID

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.money import Money

from dotmac_billing.tax_evidence import (
    InvoiceTaxEvidenceV1,
    TaxDeterminationComponentEvidenceV1,
    TaxDeterminationSetEvidenceV1,
    _money,
    _positive,
    _total,
    validate_invoice_tax_evidence,
)


def _positive_labeled(value: int, label: str) -> None:
    _positive(value)


def _money_labeled(value: Money, label: str) -> None:
    _money(value)


@dataclass(frozen=True, slots=True)
class TaxCommercialSourceEvidenceV1:
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
class TaxFactSignatureEvidenceV1:
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
class TaxBaseDependencyPolicyEvidenceV1:
    prior_signature: TaxFactSignatureEvidenceV1
    selection: Literal["all_prior", "tax_code_ids"]
    tax_code_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        codes = tuple(self.tax_code_ids)
        if not isinstance(self.prior_signature, TaxFactSignatureEvidenceV1):
            raise ValueError("dependency prior signature is required")
        self.prior_signature.__post_init__()
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
class TaxPriorResultReferenceEvidenceV1:
    determination_set_id: UUID
    source_ref: str
    source_version: str
    source_fingerprint: str
    result_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.determination_set_id, UUID):
            raise ValueError("prior determination identity must be a UUID")
        TaxCommercialSourceEvidenceV1(self.source_ref, self.source_version)
        for value, prefix in (
            (self.source_fingerprint, ""),
            (self.result_fingerprint, "rv2:"),
        ):
            if not isinstance(value, str):
                raise ValueError("fingerprints must be text")
            digest = value[len(prefix) :]
            if (
                not value.startswith(prefix)
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ValueError("prior result requires exact V2 fingerprints")


@dataclass(frozen=True, slots=True)
class TaxDependencyComponentEvidenceV1:
    determination_set_id: UUID
    determination_id: UUID
    tax_code_id: UUID
    rule_id: UUID
    rule_version: int
    component_sequence: int
    tax_amount: Money

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (
                self.determination_set_id,
                self.determination_id,
                self.tax_code_id,
                self.rule_id,
            )
        ):
            raise ValueError("dependency identities must be UUIDs")
        _positive_labeled(self.rule_version, "dependency rule version")
        _positive_labeled(self.component_sequence, "dependency component sequence")
        _money_labeled(self.tax_amount, "dependency tax amount")


@dataclass(frozen=True, slots=True)
class TaxDeterminationComponentEvidenceV2(TaxDeterminationComponentEvidenceV1):
    selected_dependency_policy: TaxBaseDependencyPolicyEvidenceV1 | None = None
    dependency_components: tuple[TaxDependencyComponentEvidenceV1, ...] = ()

    def __post_init__(self) -> None:
        TaxDeterminationComponentEvidenceV1.__post_init__(self)
        snapshots = tuple(self.dependency_components)
        object.__setattr__(self, "dependency_components", snapshots)
        if self.selected_dependency_policy is not None:
            if not isinstance(
                self.selected_dependency_policy, TaxBaseDependencyPolicyEvidenceV1
            ):
                raise ValueError("dependency policy must be typed")
            self.selected_dependency_policy.__post_init__()
        for snapshot in snapshots:
            if not isinstance(snapshot, TaxDependencyComponentEvidenceV1):
                raise ValueError("dependency snapshots must be typed")
            snapshot.__post_init__()
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
class TaxDeterminationSetEvidenceV2(TaxDeterminationSetEvidenceV1):
    commercial_source: TaxCommercialSourceEvidenceV1
    prior_results: tuple[TaxPriorResultReferenceEvidenceV1, ...]
    components: tuple[TaxDeterminationComponentEvidenceV2, ...]
    _fingerprint_prefix: ClassVar[str] = "rv2:"
    _component_type: ClassVar[type] = TaxDeterminationComponentEvidenceV2

    def __post_init__(self) -> None:
        TaxDeterminationSetEvidenceV1.__post_init__(self)
        if not isinstance(self.commercial_source, TaxCommercialSourceEvidenceV1):
            raise ValueError("V2 result requires commercial source identity")
        self.commercial_source.__post_init__()
        TaxCommercialSourceEvidenceV1(self.source_ref, self.source_version)
        TaxFactSignatureEvidenceV1(
            self.jurisdiction_id,
            self.fact_kind,
            self.recognition_basis_code,
            self.transaction_side,
        )
        if any(
            not isinstance(item, TaxDeterminationComponentEvidenceV2)
            for item in self.components
        ):
            raise ValueError("V2 result requires V2 components")
        refs = tuple(self.prior_results)
        object.__setattr__(self, "prior_results", refs)
        if any(not isinstance(ref, TaxPriorResultReferenceEvidenceV1) for ref in refs):
            raise ValueError("V2 result requires complete prior references")
        for ref in refs:
            ref.__post_init__()
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
class InvoiceTaxEvidenceV2:
    """Public Tax V2 result and complete referenced results from trusted Tax reads."""

    purpose: Literal["invoice_tax", "expected_withholding"]
    determination: TaxDeterminationSetEvidenceV2
    prior_determinations: tuple[TaxDeterminationSetEvidenceV2, ...] = ()

    def __post_init__(self) -> None:
        if self.purpose not in ("invoice_tax", "expected_withholding"):
            raise ValueError("Tax evidence requires an explicit purpose")
        object.__setattr__(
            self, "prior_determinations", tuple(self.prior_determinations)
        )
        for result in (self.determination, *self.prior_determinations):
            if not isinstance(result, TaxDeterminationSetEvidenceV2):
                raise ValueError("Tax V2 evidence requires typed V2 results")
            result.__post_init__()
        validate_dependency_graph((self.determination, *self.prior_determinations))
        by_id = {
            result.determination_set_id: result for result in self.prior_determinations
        }
        reachable: set[UUID] = set()

        def retain(result: TaxDeterminationSetEvidenceV2) -> None:
            for ref in result.prior_results:
                if ref.determination_set_id not in reachable:
                    reachable.add(ref.determination_set_id)
                    retain(by_id[ref.determination_set_id])

        retain(self.determination)
        if reachable != set(by_id):
            raise ValueError(
                "retained prior results must be the complete referenced lineage"
            )


def validate_dependency_graph(
    results: tuple[TaxDeterminationSetEvidenceV2, ...],
) -> None:
    """Bind observable public evidence; owner fingerprints remain opaque seals."""
    by_id = {result.determination_set_id: result for result in results}
    if len(by_id) != len(results):
        raise ValueError("duplicate retained Tax result")
    for result in results:
        for ref in result.prior_results:
            prior = by_id.get(ref.determination_set_id)
            if prior is None or (
                prior.source_ref,
                prior.source_version,
                prior.source_fingerprint,
                prior.result_fingerprint,
            ) != (
                ref.source_ref,
                ref.source_version,
                ref.source_fingerprint,
                ref.result_fingerprint,
            ):
                raise ValueError("complete prior result differs from its reference")
            if (
                prior.tenant_id != result.tenant_id
                or prior.commercial_source != result.commercial_source
                or prior.source_amount != result.source_amount
                or prior.occurred_on != result.occurred_on
                or prior.recognition_basis_code != result.recognition_basis_code
                or prior.counterparty_ref != result.counterparty_ref
                or prior.supply_ref != result.supply_ref
                or prior.place_ref != result.place_ref
            ):
                raise ValueError("prior result describes a different commercial source")
        for component in result.components:
            policy = component.selected_dependency_policy
            if policy is None:
                continue
            matches = [
                by_id[ref.determination_set_id]
                for ref in result.prior_results
                if TaxFactSignatureEvidenceV1(
                    by_id[ref.determination_set_id].jurisdiction_id,
                    by_id[ref.determination_set_id].fact_kind,
                    by_id[ref.determination_set_id].recognition_basis_code,
                    by_id[ref.determination_set_id].transaction_side,
                )
                == policy.prior_signature
            ]
            if len(matches) != 1:
                raise ValueError(
                    "dependency signature must bind one complete prior result"
                )
            prior = matches[0]
            expected = tuple(
                TaxDependencyComponentEvidenceV1(
                    prior.determination_set_id,
                    item.determination_id,
                    item.tax_code_id,
                    item.rule_id,
                    item.rule_version,
                    item.component_sequence,
                    item.tax_amount,
                )
                for item in prior.components
                if policy.selection == "all_prior"
                or item.tax_code_id in policy.tax_code_ids
            )
            if component.dependency_components != expected:
                raise ValueError(
                    "selected dependency snapshots differ from prior evidence"
                )
            from decimal import localcontext

            with localcontext() as context:
                context.prec = 60
                if component.base_amount.amount != result.source_amount.amount + sum(
                    (item.tax_amount.amount for item in expected), start=0
                ):
                    raise ValueError(
                        "dependency base differs from retained source and snapshots"
                    )

    # A result may reference another result only in an acyclic lineage.
    def visit(identity: UUID, path: frozenset[UUID]) -> None:
        if identity in path:
            raise ValueError("Tax dependency lineage contains a cycle")
        for ref in by_id[identity].prior_results:
            visit(ref.determination_set_id, path | {identity})

    for identity in by_id:
        visit(identity, frozenset())


def validate_invoice_tax_evidence_v2(
    evidence: tuple[InvoiceTaxEvidenceV2, ...], *, scope: Scope, tax_amount: Money
) -> None:
    seen: set[UUID] = set()
    retained: dict[UUID, TaxDeterminationSetEvidenceV2] = {}
    for item in evidence:
        if not isinstance(item, InvoiceTaxEvidenceV2):
            raise ValueError("invoice requires V2 Tax evidence")
        item.__post_init__()
        for result in (item.determination, *item.prior_determinations):
            if (
                not isinstance(scope, TenantScope)
                or result.tenant_id != scope.tenant_id
            ):
                raise ValueError("Tax evidence scope differs from invoice scope")
            if result.tax_amount.currency != tax_amount.currency:
                raise ValueError("Tax evidence currency differs from invoice currency")
            previous = retained.get(result.determination_set_id)
            if previous is not None and previous != result:
                raise ValueError("repeated Tax result has different retained content")
            retained[result.determination_set_id] = result
        if item.determination.determination_set_id in seen:
            raise ValueError("duplicate invoice Tax result")
        seen.add(item.determination.determination_set_id)
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
        raise ValueError("invoice Tax evidence must total rated invoice tax")


def validate_invoice_tax_evidence_versioned(
    evidence: tuple[InvoiceTaxEvidenceV1 | InvoiceTaxEvidenceV2, ...],
    *,
    scope: Scope,
    tax_amount: Money,
    v2: bool,
) -> None:
    if v2:
        validate_invoice_tax_evidence_v2(
            cast(tuple[InvoiceTaxEvidenceV2, ...], evidence),
            scope=scope,
            tax_amount=tax_amount,
        )
    else:
        validate_invoice_tax_evidence(
            cast(tuple[InvoiceTaxEvidenceV1, ...], evidence),
            scope=scope,
            tax_amount=tax_amount,
        )


__all__ = [
    "InvoiceTaxEvidenceV2",
    "TaxCommercialSourceEvidenceV1",
    "TaxFactSignatureEvidenceV1",
    "TaxBaseDependencyPolicyEvidenceV1",
    "TaxPriorResultReferenceEvidenceV1",
    "TaxDependencyComponentEvidenceV1",
    "TaxDeterminationComponentEvidenceV2",
    "TaxDeterminationSetEvidenceV2",
]


def validate_commercial_source_binding(
    evidence: tuple[InvoiceTaxEvidenceV2, ...],
    *,
    commercial_sources: tuple[TaxCommercialSourceEvidenceV1, ...],
) -> None:
    """Bind the exact declared source set, preserving each original line identity."""
    if not isinstance(commercial_sources, tuple):
        raise ValueError("commercial sources must be an explicit tuple")
    for source in commercial_sources:
        if not isinstance(source, TaxCommercialSourceEvidenceV1):
            raise ValueError("commercial sources must be typed")
        source.__post_init__()
    if len(set(commercial_sources)) != len(commercial_sources):
        raise ValueError("commercial sources must be unique")
    expected: set[TaxCommercialSourceEvidenceV1] = set()
    originals: dict[TaxCommercialSourceEvidenceV1, tuple[object, ...]] = {}
    for item in evidence:
        if not isinstance(item, InvoiceTaxEvidenceV2):
            raise ValueError("commercial sources require typed V2 Tax evidence")
        source = item.determination.commercial_source
        expected.add(source)
        for result in (item.determination, *item.prior_determinations):
            original = (
                result.tenant_id,
                result.source_amount,
                result.occurred_on,
                result.recognition_basis_code,
                result.counterparty_ref,
                result.supply_ref,
                result.place_ref,
            )
            previous = originals.get(source)
            if previous is not None and previous != original:
                raise ValueError("commercial source describes different original facts")
            originals[source] = original
        for result in item.prior_determinations:
            if result.commercial_source != source:
                raise ValueError(
                    "Tax lineage differs from its principal commercial source"
                )
    if expected != set(commercial_sources):
        raise ValueError("commercial sources must exactly match principal Tax evidence")
