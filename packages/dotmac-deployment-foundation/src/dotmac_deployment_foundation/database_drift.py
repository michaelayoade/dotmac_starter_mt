"""Pure two-way comparison of a database declaration and catalogue facts.

The declaration is never derived from the observation.  This module takes two
already-typed values, normalizes each into the facts the current
``DatabaseContract`` can express, and compares those sets exactly.  A fact on
either side with no counterpart is a finding:

* ``declared_but_absent`` means the accepted descriptor is ahead of the target,
  or the target lost an authorized fact;
* ``present_but_undeclared`` means the target advanced without descriptor
  promotion, or an unauthorized fact appeared.

No connection, query, repair or descriptor write exists here.  Catalogue
collection remains a host/provider responsibility and descriptor promotion
remains a deployment-control responsibility.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from enum import Enum
from typing import Final

from .database_structure import (
    DatabaseDescriptorCatalogBindingV1,
    DatabaseStructureObservationEvidenceV1,
    DatabaseStructureWitnessV1,
    StructureFactDimension,
    StructureFactDirection,
)
from .errors import PreconditionFailed
from .recovery import CatalogEvidence, EffectivePrivilegeFact
from .render.compose import configuration_digest
from .spec import SCHEMA_V2, DatabaseContract, ProductDeploymentSpec

__all__ = [
    "POSTGRES_SYSTEM_EXCLUSIONS",
    "DatabaseDriftClassification",
    "DatabaseContractGap",
    "DatabaseContractGapCode",
    "DatabaseDriftExclusions",
    "DatabaseDriftFinding",
    "DatabaseDriftPhase",
    "DatabaseContractDriftReport",
    "DatabaseFactDirection",
    "DatabaseFactDimension",
    "EffectivePrivilegeExclusion",
    "EffectivePrivilegeAuditUniverse",
    "EffectivePrivilegeKey",
    "NameExclusion",
    "ObservedDatabaseState",
    "PrivilegeUniverseDerivation",
    "PrefixExclusion",
    "compare_database_contract",
]


class DatabaseDriftPhase(str, Enum):
    """When the same comparison is being used."""

    PREFLIGHT = "preflight"
    POSTFLIGHT = "postflight"
    RECOVERY = "recovery"


class DatabaseDriftClassification(str, Enum):
    """Operator-facing meaning of a report in its execution phase."""

    MATCH = "match"
    PRECONDITION_DRIFT = "precondition_drift"
    POSTCONDITION_FAILURE = "postcondition_failure"
    RECOVERY_DRIFT = "recovery_drift"
    UNMEASURABLE = "unmeasurable"


class DatabaseFactDirection(str, Enum):
    DECLARED_BUT_ABSENT = "declared_but_absent"
    PRESENT_BUT_UNDECLARED = "present_but_undeclared"


class DatabaseFactDimension(str, Enum):
    POSTGRES_MAJOR = "postgres_major"
    ROLE = "role"
    ROLE_ATTRIBUTE = "role_attribute"
    MEMBERSHIP = "membership"
    SCHEMA = "schema"
    ISOLATION = "isolation"
    TABLESPACES = "tablespaces"
    MIGRATION_HEAD = "migration_head"
    TABLE = "table"
    COLUMN = "column"


class DatabaseContractGapCode(str, Enum):
    TABLE_DECLARATION = "table_declaration_unavailable"
    COLUMN_DECLARATION = "column_declaration_unavailable"
    PRODUCT_STRUCTURE_COVERAGE = "product_structure_coverage_incomplete"
    DESCRIPTOR_CATALOG_BINDING = "descriptor_catalog_binding_unavailable"


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseContractGap:
    code: DatabaseContractGapCode
    dimension: DatabaseFactDimension
    detail: str


STRUCTURE_CONTRACT_GAPS: Final = (
    DatabaseContractGap(
        code=DatabaseContractGapCode.TABLE_DECLARATION,
        dimension=DatabaseFactDimension.TABLE,
        detail=(
            "ProductDeploymentSpec.v1 DatabaseContract declares schemas but no "
            "exact table set. ModuleManifest table names are outside this "
            "zero-dependency descriptor surface and are bound only by an opaque "
            "manifest digest, so live tables cannot author the missing claim"
        ),
    ),
    DatabaseContractGap(
        code=DatabaseContractGapCode.COLUMN_DECLARATION,
        dimension=DatabaseFactDimension.COLUMN,
        detail=(
            "ProductDeploymentSpec.v1, ModuleManifest and CatalogEvidence expose "
            "no authoritative exact column contract. Live columns therefore "
            "remain unmeasurable until a versioned declaration surface owns them"
        ),
    ),
)


@dataclasses.dataclass(frozen=True, slots=True)
class NameExclusion:
    """One exact catalogue name excluded for a stated reason."""

    name: str
    reason: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("an exact catalogue exclusion needs a name")
        if not self.reason.strip():
            raise ValueError(f"catalogue exclusion {self.name!r} needs a reason")


@dataclasses.dataclass(frozen=True, slots=True)
class PrefixExclusion:
    """One explicit namespace prefix, used for PostgreSQL-generated names."""

    prefix: str
    reason: str

    def __post_init__(self) -> None:
        if not self.prefix:
            raise ValueError("a catalogue prefix exclusion needs a prefix")
        if not self.reason.strip():
            raise ValueError(
                f"catalogue prefix exclusion {self.prefix!r} needs a reason"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class EffectivePrivilegeKey:
    """The exact question one effective-privilege observation answers."""

    role: str
    scope: str
    identity: str
    privilege: str

    @classmethod
    def from_fact(cls, fact: EffectivePrivilegeFact) -> EffectivePrivilegeKey:
        return cls(
            role=fact.role,
            scope=fact.scope,
            identity=fact.identity,
            privilege=fact.privilege,
        )

    @property
    def subject(self) -> str:
        return f"{self.role}:{self.scope}:{self.identity}:{self.privilege}"


@dataclasses.dataclass(frozen=True, slots=True)
class EffectivePrivilegeExclusion:
    key: EffectivePrivilegeKey
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(
                f"effective privilege exclusion {self.key.subject!r} needs a reason"
            )


class PrivilegeUniverseDerivation(str, Enum):
    """Sources that discover the privilege audit extent without the descriptor."""

    CATALOG_DISCOVERY = "catalog_discovery"
    RECOVERY_BUNDLE_CATALOG = "recovery_bundle_catalog"


@dataclasses.dataclass(frozen=True, slots=True)
class EffectivePrivilegeAuditUniverse:
    """Proof of the independently selected effective-privilege audit extent.

    ``keys`` is the complete set the collector promised to answer.  It is
    derived from catalogue-managed objects and roles (or the equivalent frozen
    recovery-bundle catalogue), never by expanding the descriptor's isolation
    invariants.  The comparator checks that every key has exactly one observed
    answer before it may report agreement.
    """

    keys: frozenset[EffectivePrivilegeKey]
    derivation: PrivilegeUniverseDerivation

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError(
                "an effective-privilege audit universe may not be empty; green "
                "over an empty independently derived extent proves nothing"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseDriftExclusions:
    """The complete, caller-selected exclusion policy.

    These values are static inputs.  The comparator never adds an exclusion
    because an observed name looks unfamiliar and never learns one from the
    target it is checking.
    """

    role_names: tuple[NameExclusion, ...] = ()
    role_prefixes: tuple[PrefixExclusion, ...] = ()
    schema_names: tuple[NameExclusion, ...] = ()
    schema_prefixes: tuple[PrefixExclusion, ...] = ()
    effective_privileges: tuple[EffectivePrivilegeExclusion, ...] = ()


POSTGRES_SYSTEM_EXCLUSIONS: Final = DatabaseDriftExclusions(
    role_prefixes=(
        PrefixExclusion(
            prefix="pg_",
            reason="PostgreSQL reserves pg_ role names for built-in roles",
        ),
    ),
    schema_names=(
        NameExclusion(name="pg_catalog", reason="PostgreSQL system catalogue"),
        NameExclusion(name="information_schema", reason="SQL metadata catalogue"),
        NameExclusion(name="pg_toast", reason="PostgreSQL TOAST storage schema"),
    ),
    schema_prefixes=(
        PrefixExclusion(
            prefix="pg_toast_",
            reason="PostgreSQL-generated per-database TOAST schema",
        ),
        PrefixExclusion(
            prefix="pg_temp_",
            reason="PostgreSQL-generated temporary schema",
        ),
    ),
)


@dataclasses.dataclass(frozen=True, slots=True)
class ObservedDatabaseState:
    """A complete read-only catalogue snapshot plus its server major."""

    postgres_major: int
    catalog: CatalogEvidence
    effective_privilege_universe: EffectivePrivilegeAuditUniverse | None = None
    structure_observation: DatabaseStructureObservationEvidenceV1 | None = None

    def __post_init__(self) -> None:
        if self.postgres_major < 1:
            raise ValueError("an observed PostgreSQL major must be positive")
        if (
            self.structure_observation is not None
            and self.structure_observation.postgres_major != self.postgres_major
        ):
            raise ValueError(
                "the structural observation and observed database state bind "
                "different PostgreSQL majors"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseDriftFinding:
    dimension: DatabaseFactDimension
    direction: DatabaseFactDirection
    subject: str
    declared: str
    observed: str

    def __str__(self) -> str:
        return (
            f"{self.direction.value}: {self.dimension.value} {self.subject!r} "
            f"declared={self.declared}, observed={self.observed}"
        )


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseContractDriftReport:
    phase: DatabaseDriftPhase
    descriptor_digest: str
    declared_fact_count: int
    observed_fact_count: int
    findings: tuple[DatabaseDriftFinding, ...] = ()
    measurement_issues: tuple[str, ...] = ()
    contract_gaps: tuple[DatabaseContractGap, ...] = ()

    @property
    def clean(self) -> bool:
        return (
            not self.findings and not self.measurement_issues and not self.contract_gaps
        )

    @property
    def matched_descriptor_digest(self) -> str:
        """The descriptor identity only when every comparison was measurable."""
        return self.descriptor_digest if self.clean else ""

    @property
    def declared_but_absent(self) -> tuple[DatabaseDriftFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.direction is DatabaseFactDirection.DECLARED_BUT_ABSENT
        )

    @property
    def present_but_undeclared(self) -> tuple[DatabaseDriftFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.direction is DatabaseFactDirection.PRESENT_BUT_UNDECLARED
        )

    @property
    def classification(self) -> DatabaseDriftClassification:
        if self.measurement_issues:
            return DatabaseDriftClassification.UNMEASURABLE
        if self.findings:
            return {
                DatabaseDriftPhase.PREFLIGHT: (
                    DatabaseDriftClassification.PRECONDITION_DRIFT
                ),
                DatabaseDriftPhase.POSTFLIGHT: (
                    DatabaseDriftClassification.POSTCONDITION_FAILURE
                ),
                DatabaseDriftPhase.RECOVERY: (
                    DatabaseDriftClassification.RECOVERY_DRIFT
                ),
            }[self.phase]
        if self.contract_gaps:
            return DatabaseDriftClassification.UNMEASURABLE
        return DatabaseDriftClassification.MATCH

    @property
    def exit_code(self) -> int:
        """The Foundation CLI contract: 0 agreement, 1 refusal/drift."""
        return 0 if self.clean else 1

    def render(self) -> str:
        lines = [
            f"database contract comparison: {self.classification.value}",
            f"  descriptor: {self.descriptor_digest}",
            (
                f"  compared {self.declared_fact_count} declared fact(s) with "
                f"{self.observed_fact_count} observed fact(s)"
            ),
        ]
        lines.extend(f"  unmeasurable: {issue}" for issue in self.measurement_issues)
        lines.extend(
            f"  contract gap [{gap.code.value}]: {gap.detail}"
            for gap in self.contract_gaps
        )
        lines.extend(f"  {finding}" for finding in self.findings)
        return "\n".join(lines) + "\n"


@dataclasses.dataclass(frozen=True, slots=True)
class _Fact:
    dimension: DatabaseFactDimension
    subject: str
    value: str

    @property
    def identity(self) -> tuple[DatabaseFactDimension, str]:
        return (self.dimension, self.subject)


def _bool(value: bool) -> str:
    return str(value).lower()


def _is_excluded(
    value: str,
    names: Iterable[NameExclusion],
    prefixes: Iterable[PrefixExclusion],
) -> bool:
    return any(value == item.name for item in names) or any(
        value.startswith(item.prefix) for item in prefixes
    )


def _role_excluded(name: str, exclusions: DatabaseDriftExclusions) -> bool:
    return _is_excluded(name, exclusions.role_names, exclusions.role_prefixes)


def _schema_excluded(name: str, exclusions: DatabaseDriftExclusions) -> bool:
    return _is_excluded(name, exclusions.schema_names, exclusions.schema_prefixes)


def _privilege_excluded(
    key: EffectivePrivilegeKey, exclusions: DatabaseDriftExclusions
) -> bool:
    if any(key == item.key for item in exclusions.effective_privileges):
        return True
    if _role_excluded(key.role, exclusions):
        return True
    schema = key.identity.split(".", 1)[0]
    return _schema_excluded(schema, exclusions)


def _declared_facts(
    contract: DatabaseContract,
    *,
    expected_heads: Sequence[str],
    exclusions: DatabaseDriftExclusions,
) -> frozenset[_Fact]:
    facts: set[_Fact] = {
        _Fact(
            DatabaseFactDimension.POSTGRES_MAJOR,
            "server",
            str(contract.postgres_major),
        ),
        _Fact(
            DatabaseFactDimension.TABLESPACES,
            "decision",
            contract.tablespaces,
        ),
    }
    for role in contract.roles:
        if _role_excluded(role.name, exclusions):
            continue
        facts.add(_Fact(DatabaseFactDimension.ROLE, role.name, "present"))
        for attribute, value in (
            ("inherit", role.inherit),
            ("login", role.login),
            ("bypassrls", role.bypassrls),
        ):
            facts.add(
                _Fact(
                    DatabaseFactDimension.ROLE_ATTRIBUTE,
                    f"{role.name}.{attribute}",
                    _bool(value),
                )
            )
        for group in role.member_of:
            if not _role_excluded(group, exclusions):
                facts.add(
                    _Fact(
                        DatabaseFactDimension.MEMBERSHIP,
                        f"{role.name}->{group}",
                        "present",
                    )
                )
    for schema in contract.expected_schemas:
        if not _schema_excluded(schema, exclusions):
            facts.add(_Fact(DatabaseFactDimension.SCHEMA, schema, "present"))
    for invariant in contract.isolation:
        for identity in invariant.objects:
            for privilege in invariant.privileges:
                key = EffectivePrivilegeKey(
                    role=invariant.role,
                    scope=invariant.scope,
                    identity=identity,
                    privilege=privilege,
                )
                if _privilege_excluded(key, exclusions):
                    continue
                facts.add(
                    _Fact(
                        DatabaseFactDimension.ISOLATION,
                        key.subject,
                        f"holds={_bool(not invariant.denied)}",
                    )
                )
    for head in expected_heads:
        facts.add(_Fact(DatabaseFactDimension.MIGRATION_HEAD, head, "present"))
    return frozenset(facts)


def _observed_facts(
    observation: ObservedDatabaseState,
    *,
    exclusions: DatabaseDriftExclusions,
) -> frozenset[_Fact]:
    catalog = observation.catalog
    facts: set[_Fact] = {
        _Fact(
            DatabaseFactDimension.POSTGRES_MAJOR,
            "server",
            str(observation.postgres_major),
        ),
        _Fact(
            DatabaseFactDimension.TABLESPACES,
            "decision",
            catalog.tablespaces.kind,
        ),
    }
    for role in catalog.roles:
        if _role_excluded(role.name, exclusions):
            continue
        facts.add(_Fact(DatabaseFactDimension.ROLE, role.name, "present"))
        for attribute, value in (
            ("inherit", role.inherit),
            ("login", role.can_login),
            ("bypassrls", role.bypassrls),
        ):
            facts.add(
                _Fact(
                    DatabaseFactDimension.ROLE_ATTRIBUTE,
                    f"{role.name}.{attribute}",
                    _bool(value),
                )
            )
    for membership in catalog.memberships:
        if _role_excluded(membership.member, exclusions) or _role_excluded(
            membership.role, exclusions
        ):
            continue
        facts.add(
            _Fact(
                DatabaseFactDimension.MEMBERSHIP,
                f"{membership.member}->{membership.role}",
                "present",
            )
        )
    for schema in catalog.schemas:
        if not _schema_excluded(schema, exclusions):
            facts.add(_Fact(DatabaseFactDimension.SCHEMA, schema, "present"))
    for privilege in catalog.effective_privileges:
        key = EffectivePrivilegeKey.from_fact(privilege)
        if _privilege_excluded(key, exclusions):
            continue
        facts.add(
            _Fact(
                DatabaseFactDimension.ISOLATION,
                key.subject,
                f"holds={_bool(privilege.holds)}",
            )
        )
    for head in catalog.migration_heads:
        facts.add(_Fact(DatabaseFactDimension.MIGRATION_HEAD, head, "present"))
    return frozenset(facts)


def _audit_issues(
    observation: ObservedDatabaseState,
    *,
    declared: frozenset[_Fact],
    exclusions: DatabaseDriftExclusions,
) -> tuple[str, ...]:
    universe = observation.effective_privilege_universe
    if universe is None:
        return (
            "no independently derived effective-privilege audit universe was "
            "supplied. Selecting observations from the descriptor cannot detect "
            "a live privilege or revocation the descriptor omits",
        )

    expected_keys = {
        key for key in universe.keys if not _privilege_excluded(key, exclusions)
    }
    answers: dict[EffectivePrivilegeKey, list[bool]] = {}
    for fact in observation.catalog.effective_privileges:
        key = EffectivePrivilegeKey.from_fact(fact)
        if not _privilege_excluded(key, exclusions):
            answers.setdefault(key, []).append(fact.holds)
    observed_keys = set(answers)
    declared_keys = {
        fact.subject
        for fact in declared
        if fact.dimension is DatabaseFactDimension.ISOLATION
    }
    issues: list[str] = []
    for key, values in sorted(answers.items(), key=lambda item: item[0].subject):
        if len(values) != 1:
            issues.append(
                f"the catalogue supplied {len(values)} effective-privilege "
                f"answers for {key.subject!r}; the audit universe requires "
                "exactly one answer per key"
            )
    for key in sorted(expected_keys - observed_keys, key=lambda item: item.subject):
        issues.append(
            f"the independently derived audit universe includes {key.subject!r} "
            "and the catalogue supplied no effective-privilege answer"
        )
    for key in sorted(observed_keys - expected_keys, key=lambda item: item.subject):
        issues.append(
            f"the catalogue supplied effective privilege {key.subject!r} outside "
            "the independently derived audit universe; its completeness cannot "
            "be established"
        )
    universe_subjects = {key.subject for key in expected_keys}
    for subject in sorted(declared_keys - universe_subjects):
        issues.append(
            f"declared isolation fact {subject!r} is outside the independently "
            "derived audit universe and therefore unmeasured"
        )
    return tuple(issues)


def _finding_sort_key(finding: DatabaseDriftFinding) -> tuple[str, str, str, str]:
    return (
        finding.dimension.value,
        finding.subject,
        finding.direction.value,
        finding.declared,
    )


def compare_database_contract(
    spec: ProductDeploymentSpec,
    observation: ObservedDatabaseState,
    *,
    phase: DatabaseDriftPhase,
    exclusions: DatabaseDriftExclusions = POSTGRES_SYSTEM_EXCLUSIONS,
    catalog_binding: DatabaseDescriptorCatalogBindingV1 | None = None,
    structure_witnesses: tuple[DatabaseStructureWitnessV1, ...] = (),
) -> DatabaseContractDriftReport:
    """Compare every currently expressible database fact, in both directions.

    The full spec is accepted so the report binds the canonical descriptor
    digest and obtains migration heads from the same accepted document as the
    database declaration.  The observation is never used to fill either.
    """
    contract = spec.database
    if contract is None:
        raise ValueError("the accepted descriptor declares no [database] contract")

    descriptor_digest = configuration_digest(spec)
    structural_findings: list[DatabaseDriftFinding] = []
    structural_measurement_issues: list[str] = []
    contract_gaps: tuple[DatabaseContractGap, ...]
    if catalog_binding is None:
        if structure_witnesses:
            raise PreconditionFailed(
                "a structural witness requires its exact descriptor-catalog binding"
            )
        contract_gaps = STRUCTURE_CONTRACT_GAPS
    else:
        if not isinstance(catalog_binding, DatabaseDescriptorCatalogBindingV1):
            raise PreconditionFailed(
                "catalog_binding must be DatabaseDescriptorCatalogBindingV1"
            )
        if catalog_binding.descriptor_digest != descriptor_digest:
            raise PreconditionFailed(
                "the catalog sidecar binds a different descriptor digest"
            )
        if catalog_binding.product != spec.product:
            raise PreconditionFailed("the catalog sidecar binds a different product")
        if catalog_binding.postgres_major != contract.postgres_major:
            raise PreconditionFailed(
                "the catalog sidecar binds a different PostgreSQL major"
            )
        if set(catalog_binding.expected_schemas) != set(contract.expected_schemas):
            raise PreconditionFailed(
                "the catalog sidecar binds a different expected schema extent"
            )
        descriptor_binds_catalogs = spec.descriptor_schema == SCHEMA_V2
        if descriptor_binds_catalogs and catalog_binding.catalogs != contract.catalogs:
            raise PreconditionFailed(
                "the catalog evidence binding differs from the coordinates "
                "embedded in ProductDeploymentSpec.v2"
            )
        if not isinstance(structure_witnesses, tuple) or not all(
            isinstance(witness, DatabaseStructureWitnessV1)
            for witness in structure_witnesses
        ):
            raise PreconditionFailed(
                "structure_witnesses must be a tuple of "
                "DatabaseStructureWitnessV1 values"
            )
        witnessed_catalogs = tuple(
            witness.result.catalog for witness in structure_witnesses
        )
        if len(set(witnessed_catalogs)) != len(witnessed_catalogs):
            raise PreconditionFailed(
                "database structure witnesses repeat a catalog coordinate"
            )
        gaps: list[DatabaseContractGap] = []
        if not descriptor_binds_catalogs:
            gaps.append(
                DatabaseContractGap(
                    code=DatabaseContractGapCode.DESCRIPTOR_CATALOG_BINDING,
                    dimension=DatabaseFactDimension.SCHEMA,
                    detail=(
                        "ProductDeploymentSpec.v1 cannot contain database catalog "
                        "coordinates. This sidecar is evidence design input, not a "
                        "descriptor fact; only ProductDeploymentSpec.v2 may make it "
                        "part of the authorized descriptor and enable a full match"
                    ),
                )
            )
        covered_schemas: set[str] = set()
        for catalog in catalog_binding.catalogs:
            matching = tuple(
                witness
                for witness in structure_witnesses
                if witness.result.catalog == catalog
            )
            if not matching:
                scope = ", ".join(catalog.complete_schemas)
                gaps.extend(
                    (
                        DatabaseContractGap(
                            code=DatabaseContractGapCode.TABLE_DECLARATION,
                            dimension=DatabaseFactDimension.TABLE,
                            detail=(
                                f"catalog {catalog.path!r} binds complete schemas "
                                f"[{scope}], but no accepted structural witness "
                                "proves their independently observed table extent"
                            ),
                        ),
                        DatabaseContractGap(
                            code=DatabaseContractGapCode.COLUMN_DECLARATION,
                            dimension=DatabaseFactDimension.COLUMN,
                            detail=(
                                f"catalog {catalog.path!r} binds complete schemas "
                                f"[{scope}], but no accepted structural witness "
                                "proves their independently observed column extent"
                            ),
                        ),
                    )
                )
                continue
            structural = matching[0].result
            if structural.descriptor_digest != descriptor_digest:
                raise PreconditionFailed(
                    "the structural witness binds a different descriptor digest"
                )
            if structural.postgres_major != observation.postgres_major:
                raise PreconditionFailed(
                    "the structural witness and observed database state bind "
                    "different PostgreSQL majors"
                )
            if observation.structure_observation is None:
                raise PreconditionFailed(
                    "a structural witness requires its exact observation evidence "
                    "on ObservedDatabaseState"
                )
            if structural.observation != observation.structure_observation:
                raise PreconditionFailed(
                    "the structural witness was produced from different observation "
                    "evidence than ObservedDatabaseState"
                )
            structural_measurement_issues.extend(structural.measurement_issues)
            covered_schemas.update(catalog.complete_schemas)
            for finding in structural.findings:
                structural_findings.append(
                    DatabaseDriftFinding(
                        dimension={
                            StructureFactDimension.TABLE: DatabaseFactDimension.TABLE,
                            StructureFactDimension.COLUMN: DatabaseFactDimension.COLUMN,
                        }[finding.dimension],
                        direction={
                            StructureFactDirection.DECLARED_BUT_ABSENT: (
                                DatabaseFactDirection.DECLARED_BUT_ABSENT
                            ),
                            StructureFactDirection.PRESENT_BUT_UNDECLARED: (
                                DatabaseFactDirection.PRESENT_BUT_UNDECLARED
                            ),
                        }[finding.direction],
                        subject=finding.subject,
                        declared=finding.declared,
                        observed=finding.observed,
                    )
                )
        undeclared_witnesses = set(witnessed_catalogs) - set(catalog_binding.catalogs)
        if undeclared_witnesses:
            raise PreconditionFailed(
                "a structural witness binds a database catalog the sidecar "
                "does not declare"
            )
        uncovered = sorted(set(contract.expected_schemas) - covered_schemas)
        if uncovered:
            gaps.append(
                DatabaseContractGap(
                    code=DatabaseContractGapCode.PRODUCT_STRUCTURE_COVERAGE,
                    dimension=DatabaseFactDimension.SCHEMA,
                    detail=(
                        "structural witnesses do not cover declared schema(s) "
                        f"{uncovered}; partial module evidence cannot produce a "
                        "whole-descriptor match"
                    ),
                )
            )
        contract_gaps = tuple(gaps)

    declared = _declared_facts(
        contract,
        expected_heads=spec.migration.expected_heads,
        exclusions=exclusions,
    )
    observed = _observed_facts(observation, exclusions=exclusions)
    measurement_issues = tuple(
        sorted(
            set(structural_measurement_issues).union(
                _audit_issues(observation, declared=declared, exclusions=exclusions)
            )
        )
    )
    declared_by_identity = {fact.identity: fact.value for fact in declared}
    observed_by_identity = {fact.identity: fact.value for fact in observed}

    findings: list[DatabaseDriftFinding] = []
    findings.extend(structural_findings)
    for fact in declared - observed:
        findings.append(
            DatabaseDriftFinding(
                dimension=fact.dimension,
                direction=DatabaseFactDirection.DECLARED_BUT_ABSENT,
                subject=fact.subject,
                declared=fact.value,
                observed=observed_by_identity.get(fact.identity, "(absent)"),
            )
        )
    for fact in observed - declared:
        findings.append(
            DatabaseDriftFinding(
                dimension=fact.dimension,
                direction=DatabaseFactDirection.PRESENT_BUT_UNDECLARED,
                subject=fact.subject,
                declared=declared_by_identity.get(fact.identity, "(absent)"),
                observed=fact.value,
            )
        )

    return DatabaseContractDriftReport(
        phase=phase,
        # The renderer owns this digest, and this reuses it rather than
        # re-deriving it. Deriving it here would mean a SECOND caller of
        # `refuse_resolved_material=False` -- the exact thing
        # `test_canonical_document_boundary_flag.py` pins to one site, because
        # the danger is not the flag but its second caller: the moment
        # something that really does send a document to Control passes False,
        # the boundary is gone and the call site looks ordinary.
        #
        # Binding the SAME digest the rendered identity carries is also the
        # point: a report bound to a digest nothing else references cannot be
        # checked against the deployment it claims to describe.
        descriptor_digest=descriptor_digest,
        declared_fact_count=len(declared),
        observed_fact_count=len(observed),
        findings=tuple(sorted(findings, key=_finding_sort_key)),
        measurement_issues=measurement_issues,
        contract_gaps=contract_gaps,
    )
