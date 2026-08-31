"""Typed handoff from a database-structure comparator to Foundation.

Foundation can prepare a release-contained database catalogue binding.
``ProductDeploymentSpec.v1`` cannot embed it without redefining a published
schema; ``ProductDeploymentSpec.v2`` embeds the same coordinates. Foundation
does not own the catalogue schema and has no database observer. The result type
describes what a provider-owned typed comparator produces, but a caller cannot
submit a precomputed value as proof. Contract identifiers are not provenance:
no result becomes a witness until an integrated verifier reads the held
catalogue and observation bytes and runs the owning comparator.

This split is intentional.  Adding table/column fields to ``CatalogEvidence``
without an owning collector would turn manually populated values into supposed
live proof.  Copying the kernel catalogue types would create a competing schema.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from typing import Any, Final, Protocol

from .database_catalog import (
    MODULE_DATABASE_CATALOG_SCHEMA,
    PRODUCT_DATABASE_CATALOG_SCHEMA,
    DatabaseCatalogCoordinateV1,
    DatabaseCatalogProductIdentityV1,
    DatabaseCatalogScope,
)
from .digest import Digest
from .errors import PreconditionFailed, SpecError
from .spec import SCHEMA_V2, ProductDeploymentSpec

__all__ = [
    "DATABASE_DESCRIPTOR_CATALOG_BINDING_SCHEMA",
    "DATABASE_CATALOG_COMPARATOR_CONTRACT",
    "DATABASE_CATALOG_FACT_SCOPE",
    "MODULE_DATABASE_CATALOG_SCHEMA",
    "PRODUCT_DATABASE_CATALOG_SCHEMA",
    "POSTGRES_TABLES_COLUMNS_OBSERVATION_SCHEMA",
    "DatabaseCatalogCoordinateV1",
    "DatabaseCatalogProductIdentityV1",
    "DatabaseCatalogScope",
    "DatabaseDescriptorCatalogBindingV1",
    "DatabaseStructureComparisonResultV1",
    "DatabaseStructureCoverageV1",
    "DatabaseStructureFactAttribute",
    "DatabaseStructureFactKeyV1",
    "DatabaseStructureFindingV1",
    "DatabaseStructureObservationEvidenceV1",
    "DatabaseStructureWitnessV1",
    "DatabaseStructureVerifierComparisonV1",
    "DatabaseStructureVerifierDriftV1",
    "DatabaseStructureVerifierV1",
    "StructureFactDimension",
    "StructureFactDirection",
    "accept_database_structure_comparison",
]

DATABASE_DESCRIPTOR_CATALOG_BINDING_SCHEMA: Final = (
    "DatabaseDescriptorCatalogBinding.v1"
)
DATABASE_CATALOG_COMPARATOR_CONTRACT: Final = (
    "dotmac.database-catalog-tables-columns-comparator/v1"
)
POSTGRES_TABLES_COLUMNS_OBSERVATION_SCHEMA: Final = (
    "dotmac.postgresql-tables-columns-observation/v1"
)
DATABASE_CATALOG_FACT_SCOPE: Final = "tables_and_columns"


def _mapping(value: object, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SpecError(f"{where} must be an object")
    return value


def _strict(document: Mapping[str, Any], *, where: str, fields: set[str]) -> None:
    unknown = sorted(set(document) - fields)
    missing = sorted(fields - set(document))
    if unknown:
        raise SpecError(f"{where} has unknown field(s) {unknown}")
    if missing:
        raise SpecError(f"{where} is missing required field(s) {missing}")


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise SpecError(f"database catalog binding repeats JSON field {key!r}")
        document[key] = value
    return document


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseDescriptorCatalogBindingV1:
    """Evidence binding over a descriptor and its catalogue coordinates.

    For v1 it is a migration sidecar and cannot enable a whole-descriptor match.
    For v2, ``from_spec`` requires these to be the exact embedded coordinates;
    the separate digest then binds the comparator's immutable input set.
    """

    descriptor_digest: str
    product: str
    postgres_major: int
    expected_schemas: tuple[str, ...]
    catalogs: tuple[DatabaseCatalogCoordinateV1, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "descriptor_digest",
            str(Digest.parse(self.descriptor_digest, where="binding.descriptor")),
        )
        object.__setattr__(
            self, "product", _required(self.product, where="binding.product")
        )
        if type(self.postgres_major) is not int or self.postgres_major < 1:
            raise SpecError("binding.postgres_major must be a positive integer")
        if (
            not isinstance(self.expected_schemas, tuple)
            or not self.expected_schemas
            or self.expected_schemas != tuple(sorted(set(self.expected_schemas)))
        ):
            raise SpecError("binding.expected_schemas must be sorted and unique")
        if not isinstance(self.catalogs, tuple) or not self.catalogs:
            raise SpecError("binding.catalogs must be a non-empty tuple")
        if not all(
            isinstance(item, DatabaseCatalogCoordinateV1) for item in self.catalogs
        ):
            raise SpecError("binding.catalogs contains an invalid coordinate")
        keys = tuple(
            (item.scope.value, item.schema, item.path, item.digest)
            for item in self.catalogs
        )
        if keys != tuple(sorted(set(keys))):
            raise SpecError("binding.catalogs must be unique and sorted")
        product_catalogs = tuple(
            item for item in self.catalogs if item.scope is DatabaseCatalogScope.PRODUCT
        )
        if product_catalogs and len(self.catalogs) != 1:
            raise SpecError("a product catalog cannot be combined with another catalog")
        expected = set(self.expected_schemas)
        covered: set[str] = set()
        for catalog in self.catalogs:
            schemas = set(catalog.complete_schemas)
            if not schemas <= expected:
                raise SpecError("a catalog covers a schema outside expected_schemas")
            if schemas & covered:
                raise SpecError("database catalogs overlap on a complete schema")
            covered.update(schemas)
            if catalog.scope is DatabaseCatalogScope.PRODUCT and schemas != expected:
                raise SpecError("a product catalog must cover every expected schema")
            if (
                catalog.product_identity is not None
                and catalog.product_identity.descriptor_product != self.product
            ):
                raise SpecError(
                    "a product catalog identity mapping binds a different "
                    "descriptor product"
                )

    @classmethod
    def from_spec(
        cls,
        spec: ProductDeploymentSpec,
        *,
        catalogs: tuple[DatabaseCatalogCoordinateV1, ...],
    ) -> DatabaseDescriptorCatalogBindingV1:
        if spec.database is None:
            raise SpecError("a database catalog binding requires [database]")
        if spec.descriptor_schema == SCHEMA_V2 and catalogs != spec.database.catalogs:
            raise SpecError(
                "a v2 catalog binding must use exactly the coordinates embedded "
                "in the descriptor"
            )
        from .render.compose import configuration_digest

        return cls(
            descriptor_digest=configuration_digest(spec),
            product=spec.product,
            postgres_major=spec.database.postgres_major,
            expected_schemas=tuple(sorted(spec.database.expected_schemas)),
            catalogs=catalogs,
        )

    def as_document(self) -> dict[str, Any]:
        return {
            "schema": DATABASE_DESCRIPTOR_CATALOG_BINDING_SCHEMA,
            "descriptor_digest": self.descriptor_digest,
            "product": self.product,
            "postgres_major": self.postgres_major,
            "expected_schemas": list(self.expected_schemas),
            "catalogs": [item.as_document() for item in self.catalogs],
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.as_document(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def sha256_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_json_bytes(
        cls,
        payload: bytes,
        *,
        expected_digest: str | None = None,
    ) -> DatabaseDescriptorCatalogBindingV1:
        if not isinstance(payload, bytes):
            raise SpecError("database catalog binding must be bytes")
        actual_digest = str(Digest.of(payload))
        if expected_digest is not None and Digest.parse(
            expected_digest, where="binding expected_digest"
        ) != Digest.parse(actual_digest, where="binding actual_digest"):
            raise PreconditionFailed(
                "database catalog binding bytes do not match expected_digest"
            )
        try:
            value = json.loads(
                payload.decode("utf-8"), object_pairs_hook=_object_without_duplicates
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SpecError("database catalog binding must be UTF-8 JSON") from exc
        document = _mapping(value, where=DATABASE_DESCRIPTOR_CATALOG_BINDING_SCHEMA)
        _strict(
            document,
            where=DATABASE_DESCRIPTOR_CATALOG_BINDING_SCHEMA,
            fields={
                "schema",
                "descriptor_digest",
                "product",
                "postgres_major",
                "expected_schemas",
                "catalogs",
            },
        )
        if document["schema"] != DATABASE_DESCRIPTOR_CATALOG_BINDING_SCHEMA:
            raise SpecError(
                f"unsupported catalog binding schema {document['schema']!r}"
            )
        schemas = document["expected_schemas"]
        catalogs = document["catalogs"]
        if not isinstance(schemas, list) or not all(
            isinstance(item, str) for item in schemas
        ):
            raise SpecError("binding expected_schemas must be an array of strings")
        if not isinstance(catalogs, list):
            raise SpecError("binding catalogs must be an array")
        binding = cls(
            descriptor_digest=document["descriptor_digest"],
            product=document["product"],
            postgres_major=document["postgres_major"],
            expected_schemas=tuple(schemas),
            catalogs=tuple(
                DatabaseCatalogCoordinateV1.from_document(item) for item in catalogs
            ),
        )
        if payload != binding.canonical_bytes():
            raise SpecError("database catalog binding is not canonical JSON")
        return binding


class StructureFactDimension(str, Enum):
    TABLE = "table"
    COLUMN = "column"


class StructureFactDirection(str, Enum):
    DECLARED_BUT_ABSENT = "declared_but_absent"
    PRESENT_BUT_UNDECLARED = "present_but_undeclared"


class DatabaseStructureFactAttribute(str, Enum):
    PRESENCE = "presence"
    RELATION_KIND = "relation_kind"
    ORDINAL = "ordinal"
    POSTGRES_TYPE = "postgres_type"
    NULLABLE = "nullable"
    GENERATION = "generation"
    EXPRESSION = "expression"
    COLLATION = "collation"


def _required(value: str, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{where} is required and cannot be empty")
    if value != value.strip():
        raise SpecError(f"{where} must be trimmed")
    return value


def _positive(value: int, *, where: str) -> int:
    if type(value) is not int or value < 1:
        raise SpecError(f"{where} must be a positive integer")
    return value


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseStructureCoverageV1:
    """The independently observed extent a comparator claims to cover.

    Every dimension is explicit because schema discovery is not table
    discovery, and table discovery is not column discovery.  There is no
    default: a newly constructed result has to say all three things.
    """

    schemas_complete: bool
    tables_complete: bool
    columns_complete: bool

    def __post_init__(self) -> None:
        for name in ("schemas_complete", "tables_complete", "columns_complete"):
            if type(getattr(self, name)) is not bool:
                raise SpecError(f"structure coverage {name} must be a boolean")

    @property
    def complete(self) -> bool:
        return self.schemas_complete and self.tables_complete and self.columns_complete


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseStructureFactKeyV1:
    """Structured fact identity; PostgreSQL names are never joined or parsed."""

    dimension: StructureFactDimension
    schema: str
    table: str
    column: str
    attribute: DatabaseStructureFactAttribute

    def __post_init__(self) -> None:
        try:
            dimension = StructureFactDimension(self.dimension)
            attribute = DatabaseStructureFactAttribute(self.attribute)
        except ValueError as exc:
            raise SpecError(
                "database structure fact key has unknown vocabulary"
            ) from exc
        object.__setattr__(self, "dimension", dimension)
        object.__setattr__(self, "attribute", attribute)
        for name in ("schema", "table"):
            object.__setattr__(
                self,
                name,
                _required(getattr(self, name), where=f"structure key.{name}"),
            )
        if not isinstance(self.column, str) or self.column != self.column.strip():
            raise SpecError("structure key.column must be a trimmed string")
        if dimension is StructureFactDimension.TABLE:
            if self.column:
                raise SpecError("a table fact key cannot carry a column")
            if attribute not in {
                DatabaseStructureFactAttribute.PRESENCE,
                DatabaseStructureFactAttribute.RELATION_KIND,
            }:
                raise SpecError(f"attribute {attribute.value!r} is not a table fact")
        elif not self.column:
            raise SpecError("a column fact key requires a column identifier")
        elif attribute is DatabaseStructureFactAttribute.RELATION_KIND:
            raise SpecError("relation_kind is a table fact, not a column fact")

    @property
    def subject(self) -> str:
        """Canonical JSON preserves names containing '.', ':', or '->'."""
        return json.dumps(
            {
                "schema": self.schema,
                "table": self.table,
                "column": self.column,
                "attribute": self.attribute.value,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseStructureFindingV1:
    key: DatabaseStructureFactKeyV1
    direction: StructureFactDirection
    declared: str
    observed: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, DatabaseStructureFactKeyV1):
            raise SpecError("database structure finding needs a typed fact key")
        try:
            direction = StructureFactDirection(self.direction)
        except ValueError as exc:
            raise SpecError(
                "database structure finding has an unknown vocabulary"
            ) from exc
        object.__setattr__(self, "direction", direction)
        for name in ("declared", "observed"):
            object.__setattr__(
                self,
                name,
                _required(getattr(self, name), where=f"structure finding.{name}"),
            )

    @property
    def dimension(self) -> StructureFactDimension:
        return self.key.dimension

    @property
    def subject(self) -> str:
        return self.key.subject


class DatabaseStructureVerifierDriftV1(Protocol):
    """Structural view of one verifier-owned typed drift fact."""

    @property
    def dimension(self) -> object: ...

    @property
    def direction(self) -> object: ...

    @property
    def schema(self) -> str: ...

    @property
    def table(self) -> str: ...

    @property
    def column(self) -> str: ...

    @property
    def attribute(self) -> object: ...

    @property
    def declared(self) -> str: ...

    @property
    def observed(self) -> str: ...


class DatabaseStructureVerifierComparisonV1(Protocol):
    """The factory-only result returned by the owning verifier."""

    @property
    def drifts(self) -> tuple[DatabaseStructureVerifierDriftV1, ...]: ...

    @property
    def declaration_digest(self) -> str: ...

    @property
    def observation_digest(self) -> str: ...

    @property
    def postgres_major(self) -> int: ...

    @property
    def declaration_schema(self) -> str: ...

    @property
    def declaration_scope(self) -> object: ...

    @property
    def complete_schemas(self) -> tuple[str, ...]: ...

    @property
    def product_code(self) -> str: ...

    @property
    def product_version(self) -> str: ...

    @property
    def measurement_issues(self) -> tuple[str, ...]: ...

    @property
    def comparator_id(self) -> str: ...

    @property
    def scope(self) -> str: ...


class DatabaseStructureVerifierV1(Protocol):
    """Exact call shape of either kernel module or product verifier."""

    def __call__(
        self,
        *,
        declaration_bytes: bytes,
        declaration_digest: str,
        observation_bytes: bytes,
        observation_digest: str,
    ) -> DatabaseStructureVerifierComparisonV1: ...


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseStructureObservationEvidenceV1:
    """Identity of the exact immutable structural observation payload."""

    schema: str
    digest: str
    ref: str
    postgres_major: int
    payload: bytes = dataclasses.field(repr=False)

    def __post_init__(self) -> None:
        for name in ("schema", "ref"):
            object.__setattr__(
                self,
                name,
                _required(getattr(self, name), where=f"structure.observation.{name}"),
            )
        object.__setattr__(
            self,
            "digest",
            str(Digest.parse(self.digest, where="structure.observation.digest")),
        )
        object.__setattr__(
            self,
            "postgres_major",
            _positive(
                self.postgres_major, where="structure.observation.postgres_major"
            ),
        )
        if not isinstance(self.payload, bytes):
            raise SpecError("structure observation payload must be bytes")
        if str(Digest.of(self.payload)) != self.digest:
            raise SpecError(
                "structure observation payload does not match its declared digest"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseStructureComparisonResultV1:
    """Result emitted by the owner of the typed comparator and live observer.

    ``comparator_contract`` is a versioned contract identifier, not an
    implementation label. ``observation`` is also the exact
    evidence identity carried by ``ObservedDatabaseState``; PostgreSQL-major
    and catalogue facts cannot be assembled from unrelated observations.
    """

    descriptor_digest: str
    binding_digest: str
    catalog: DatabaseCatalogCoordinateV1
    comparator_contract: str
    postgres_major: int
    observation: DatabaseStructureObservationEvidenceV1
    observed_at: str
    coverage: DatabaseStructureCoverageV1
    findings: tuple[DatabaseStructureFindingV1, ...] = ()
    measurement_issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "descriptor_digest",
            str(Digest.parse(self.descriptor_digest, where="structure.descriptor")),
        )
        object.__setattr__(
            self,
            "binding_digest",
            str(Digest.parse(self.binding_digest, where="structure.binding")),
        )
        if not isinstance(self.catalog, DatabaseCatalogCoordinateV1):
            raise SpecError("structure catalog must be DatabaseCatalogCoordinateV1")
        for name in ("comparator_contract", "observed_at"):
            object.__setattr__(
                self,
                name,
                _required(getattr(self, name), where=f"structure.{name}"),
            )
        object.__setattr__(
            self,
            "postgres_major",
            _positive(self.postgres_major, where="structure.postgres_major"),
        )
        if not isinstance(self.observation, DatabaseStructureObservationEvidenceV1):
            raise SpecError(
                "structure observation must be DatabaseStructureObservationEvidenceV1"
            )
        if self.observation.postgres_major != self.postgres_major:
            raise SpecError(
                "the structural result and observation bind different PostgreSQL majors"
            )
        if not isinstance(self.coverage, DatabaseStructureCoverageV1):
            raise SpecError("structure coverage must be DatabaseStructureCoverageV1")
        if not isinstance(self.findings, tuple) or not all(
            isinstance(item, DatabaseStructureFindingV1) for item in self.findings
        ):
            raise SpecError(
                "structure findings must be a tuple of DatabaseStructureFindingV1"
            )
        finding_keys = tuple(
            (
                item.key.subject,
                item.direction.value,
                item.declared,
                item.observed,
            )
            for item in self.findings
        )
        if finding_keys != tuple(sorted(set(finding_keys))):
            raise SpecError("structure findings must be unique and sorted")
        changed_attributes: dict[str, set[StructureFactDirection]] = {}
        for finding in self.findings:
            if finding.key.attribute is not DatabaseStructureFactAttribute.PRESENCE:
                changed_attributes.setdefault(finding.key.subject, set()).add(
                    finding.direction
                )
        incomplete = sorted(
            subject
            for subject, directions in changed_attributes.items()
            if directions
            != {
                StructureFactDirection.DECLARED_BUT_ABSENT,
                StructureFactDirection.PRESENT_BUT_UNDECLARED,
            }
        )
        if incomplete:
            raise SpecError(
                "changed structural attributes require both set-difference "
                f"directions; incomplete keys={incomplete}"
            )
        if not isinstance(self.measurement_issues, tuple) or not all(
            isinstance(item, str) and item.strip() for item in self.measurement_issues
        ):
            raise SpecError(
                "structure measurement_issues must be a tuple of non-empty strings"
            )
        if self.measurement_issues != tuple(sorted(set(self.measurement_issues))):
            raise SpecError("structure measurement_issues must be unique and sorted")


class _AcceptedWitness:
    __slots__ = ()


_ACCEPTED: Final = _AcceptedWitness()


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseStructureWitnessV1:
    """A recognized complete result, which may carry measured drift findings."""

    _witness: _AcceptedWitness
    result: DatabaseStructureComparisonResultV1

    def __post_init__(self) -> None:
        if self._witness is not _ACCEPTED:
            raise PreconditionFailed(
                "DatabaseStructureWitnessV1 may only be produced by "
                "accept_database_structure_comparison()"
            )


def accept_database_structure_comparison(
    binding: DatabaseDescriptorCatalogBindingV1,
    catalog: DatabaseCatalogCoordinateV1,
    *,
    catalog_payload: bytes,
    observation: DatabaseStructureObservationEvidenceV1,
    observed_at: str,
    verifier: DatabaseStructureVerifierV1,
) -> DatabaseStructureWitnessV1:
    """Accept only verifier-produced evidence bound to held immutable bytes.

    This is an evidence gate, not an observer.  In particular, current
    ``CatalogEvidence`` cannot produce this result because it carries neither a
    table inventory nor a column inventory. Merely naming the intended
    comparator/observer contracts is never enough. The injected verifier is
    invoked with the exact held bytes and must return its factory-only typed
    result before this seam can mint a witness.
    """

    if not isinstance(binding, DatabaseDescriptorCatalogBindingV1):
        raise PreconditionFailed("binding must be DatabaseDescriptorCatalogBindingV1")
    if not isinstance(catalog, DatabaseCatalogCoordinateV1):
        raise PreconditionFailed("catalog must be DatabaseCatalogCoordinateV1")
    if catalog not in binding.catalogs:
        raise PreconditionFailed(
            "the database catalog coordinate is not declared by this binding"
        )
    if not isinstance(catalog_payload, bytes):
        raise PreconditionFailed("database structure catalogue payload must be bytes")
    if not isinstance(observation, DatabaseStructureObservationEvidenceV1):
        raise PreconditionFailed(
            "observation must be DatabaseStructureObservationEvidenceV1"
        )
    observed_at = _required(observed_at, where="structure.observed_at")
    if str(Digest.of(catalog_payload)) != catalog.digest:
        raise PreconditionFailed(
            "held database catalogue bytes do not match its digest"
        )
    if str(Digest.of(observation.payload)) != observation.digest:
        raise PreconditionFailed(
            "held database observation bytes do not match its digest"
        )
    if observation.schema != POSTGRES_TABLES_COLUMNS_OBSERVATION_SCHEMA:
        raise PreconditionFailed(
            f"unsupported database structure observation schema {observation.schema!r}"
        )
    try:
        verified = verifier(
            declaration_bytes=catalog_payload,
            declaration_digest=catalog.digest,
            observation_bytes=observation.payload,
            observation_digest=observation.digest,
        )
    except Exception as exc:
        raise PreconditionFailed(
            "the owning database structure verifier refused the held evidence"
        ) from exc
    try:
        comparator_id = verified.comparator_id
        fact_scope = verified.scope
        declaration_digest = verified.declaration_digest
        observation_digest = verified.observation_digest
        postgres_major = verified.postgres_major
        declaration_schema = verified.declaration_schema
        declaration_scope = _vocabulary(
            verified.declaration_scope, where="verifier declaration_scope"
        )
        complete_schemas = verified.complete_schemas
        product_code = verified.product_code
        product_version = verified.product_version
        verified_drifts = verified.drifts
        measurement_issues = verified.measurement_issues
    except AttributeError as exc:
        raise PreconditionFailed(
            "the database structure verifier returned an incomplete typed result"
        ) from exc
    if comparator_id != DATABASE_CATALOG_COMPARATOR_CONTRACT:
        raise PreconditionFailed(
            f"unsupported database structure comparator {comparator_id!r}"
        )
    if fact_scope != DATABASE_CATALOG_FACT_SCOPE:
        raise PreconditionFailed(
            f"unsupported database structure fact scope {fact_scope!r}"
        )
    if declaration_digest != catalog.digest:
        raise PreconditionFailed(
            "the verifier result binds different database catalogue bytes"
        )
    if observation_digest != observation.digest:
        raise PreconditionFailed(
            "the verifier result binds different database observation bytes"
        )
    if postgres_major != observation.postgres_major:
        raise PreconditionFailed(
            "the verifier result and observation bind different PostgreSQL majors"
        )
    if declaration_schema != catalog.schema:
        raise PreconditionFailed(
            "the verified catalogue schema differs from its descriptor coordinate"
        )
    if declaration_scope != catalog.scope.value:
        raise PreconditionFailed(
            "the verified catalogue scope differs from its descriptor coordinate"
        )
    if complete_schemas != catalog.complete_schemas:
        raise PreconditionFailed(
            "the verified complete schema extent differs from its descriptor coordinate"
        )
    if catalog.scope is DatabaseCatalogScope.PRODUCT:
        identity = catalog.product_identity
        if identity is None:  # defended by DatabaseCatalogCoordinateV1 as well
            raise PreconditionFailed(
                "a product catalogue coordinate has no typed product identity"
            )
        if (
            product_code != identity.catalog_product
            or product_version != identity.catalog_version
        ):
            raise PreconditionFailed(
                "the verified product code/version differs from its descriptor "
                "coordinate"
            )
    elif product_code or product_version:
        raise PreconditionFailed(
            "a verified module catalogue unexpectedly claims product identity"
        )
    if not isinstance(verified_drifts, tuple):
        raise PreconditionFailed("the verifier result drifts must be a tuple")
    normalized_findings = tuple(
        _normalize_verified_drift(item) for item in verified_drifts
    )
    findings = tuple(
        sorted(
            normalized_findings,
            key=lambda item: (
                item.key.subject,
                item.direction.value,
                item.declared,
                item.observed,
            ),
        )
    )
    if not isinstance(measurement_issues, tuple) or not all(
        isinstance(item, str) and item.strip() for item in measurement_issues
    ):
        raise PreconditionFailed(
            "the verifier returned invalid structural measurement issues"
        )
    result = DatabaseStructureComparisonResultV1(
        descriptor_digest=binding.descriptor_digest,
        binding_digest=binding.sha256_digest(),
        catalog=catalog,
        comparator_contract=comparator_id,
        postgres_major=postgres_major,
        observation=observation,
        observed_at=observed_at,
        coverage=DatabaseStructureCoverageV1(
            schemas_complete=True,
            tables_complete=True,
            columns_complete=True,
        ),
        findings=findings,
        measurement_issues=measurement_issues,
    )
    return DatabaseStructureWitnessV1(_ACCEPTED, result)


def _vocabulary(value: object, *, where: str) -> str:
    normalized = getattr(value, "value", value)
    if not isinstance(normalized, str):
        raise PreconditionFailed(f"{where} must be a string vocabulary member")
    return normalized


def _normalize_verified_drift(
    drift: DatabaseStructureVerifierDriftV1,
) -> DatabaseStructureFindingV1:
    try:
        return DatabaseStructureFindingV1(
            key=DatabaseStructureFactKeyV1(
                dimension=StructureFactDimension(
                    _vocabulary(drift.dimension, where="verifier drift.dimension")
                ),
                schema=drift.schema,
                table=drift.table,
                column=drift.column,
                attribute=DatabaseStructureFactAttribute(
                    _vocabulary(drift.attribute, where="verifier drift.attribute")
                ),
            ),
            direction=StructureFactDirection(
                _vocabulary(drift.direction, where="verifier drift.direction")
            ),
            declared=drift.declared,
            observed=drift.observed,
        )
    except (AttributeError, SpecError, TypeError, ValueError) as exc:
        raise PreconditionFailed(
            "the verifier returned an invalid typed structural drift"
        ) from exc
