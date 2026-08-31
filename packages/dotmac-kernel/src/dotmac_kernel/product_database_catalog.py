"""Canonical, release-bound product database-catalogue snapshots.

Module manifests already own table names and persistence planes.  Their optional
``database_catalog`` contribution adds the immutable column structure for those
same tables; it cannot declare another table or move one between planes.  A
product assembly freezes the selected module contributions together with its
explicit host-owned fragments into one canonical document.

This module is deliberately static and zero-I/O.  It neither imports product
models nor reads migrations or a live database.  Products author facts in their
own packages, and deployment/recovery tooling compares the accepted snapshot to
the catalogue it observes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import InitVar, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final, cast

if TYPE_CHECKING:
    from dotmac_kernel.assembly import ProductAssemblySpec

PRODUCT_DATABASE_CATALOG_SCHEMA: Final[str] = "dotmac.product-database-catalog/v1"
MODULE_DATABASE_CATALOG_SCHEMA: Final[str] = "dotmac.module-database-catalog/v1"
DATABASE_CATALOG_SCOPE: Final[str] = "tables_and_columns"

_OWNER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$")
_PRODUCT_CODE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")
_SNAPSHOT_FIELDS = frozenset(
    {
        "schema",
        "scope",
        "product_code",
        "product_version",
        "postgres_major",
        "complete_schemas",
        "fragments",
    }
)
_MODULE_SNAPSHOT_FIELDS = frozenset(
    {
        "schema",
        "scope",
        "distribution_name",
        "distribution_version",
        "module_code",
        "module_release_version",
        "manifest_contract_version",
        "database_schema",
        "lineage_head",
        "tables",
    }
)
_SNAPSHOT_FACTORY_TOKEN = object()


class ProductDatabaseCatalogError(ValueError):
    """A database-catalogue declaration is incomplete or incoherent."""


class ProductDatabaseCatalogDigestMismatchError(ProductDatabaseCatalogError):
    """Supplied bytes do not match the attested catalogue digest."""


class DatabasePersistencePlane(StrEnum):
    """The security/persistence plane a relation belongs to."""

    TENANT = "tenant"
    PLATFORM = "platform"
    HOST = "host"


class DatabaseCatalogOwnerKind(StrEnum):
    """The authority class that owns a catalogue fragment."""

    KERNEL = "kernel"
    ASSEMBLY = "assembly"
    MODULE = "module"
    EXTENSION = "extension"


class DatabaseRelationKind(StrEnum):
    """Postgres relation kinds supported by this contract generation."""

    TABLE = "table"
    PARTITIONED_TABLE = "partitioned_table"


class PostgresTypeKind(StrEnum):
    """The catalogue identity class of a PostgreSQL column type."""

    BASE = "base"
    DOMAIN = "domain"
    ENUM = "enum"
    COMPOSITE = "composite"
    RANGE = "range"
    MULTIRANGE = "multirange"
    ARRAY = "array"


class DatabaseColumnGeneration(StrEnum):
    """How PostgreSQL supplies a column value when a row is written."""

    NONE = "none"
    DEFAULT = "default"
    IDENTITY_ALWAYS = "identity_always"
    IDENTITY_BY_DEFAULT = "identity_by_default"
    GENERATED_STORED = "generated_stored"


def _identifier(value: object, field_name: str) -> str:
    """Validate one already-structured PostgreSQL catalogue identifier.

    The value is not SQL text and is never parsed. PostgreSQL quoted names may
    contain punctuation, uppercase letters and Unicode; only the catalogue's
    hard identifier constraints belong here.
    """

    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or len(value.encode("utf-8")) > 63
    ):
        raise ProductDatabaseCatalogError(
            f"{field_name} must be a non-empty PostgreSQL identifier with no NUL "
            "and at most 63 UTF-8 bytes"
        )
    return value


def _owner(value: object, field_name: str = "owner") -> str:
    if not isinstance(value, str) or _OWNER_RE.fullmatch(value) is None:
        raise ProductDatabaseCatalogError(
            f"{field_name} must be a lowercase stable owner code of at most 120 "
            "characters using letters, digits, '.', '_' or '-'"
        )
    return value


def _trimmed(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProductDatabaseCatalogError(
            f"{field_name} must be a non-empty trimmed string"
        )
    return value


def _revision(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _REVISION_RE.fullmatch(value) is None:
        raise ProductDatabaseCatalogError(
            f"{field_name} must be a lowercase Alembic revision id of at most "
            "32 characters using letters, digits and '_'"
        )
    return value


def _enum(value: object, enum_type: type[StrEnum], field_name: str) -> StrEnum:
    if not isinstance(value, str):
        raise ProductDatabaseCatalogError(f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = sorted(item.value for item in enum_type)
        raise ProductDatabaseCatalogError(
            f"{field_name} must be one of {allowed}, got {value!r}"
        ) from exc


@dataclass(frozen=True, slots=True)
class PostgresTypeContractV1:
    """A PostgreSQL type's catalogue identity plus rendered SQL spelling."""

    kind: PostgresTypeKind
    schema: str
    name: str
    formatted: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            _enum(self.kind, PostgresTypeKind, "type.kind"),
        )
        _identifier(self.schema, "type.schema")
        _identifier(self.name, "type.name")
        _trimmed(self.formatted, "type.formatted")


@dataclass(frozen=True, slots=True)
class DatabaseCatalogOwnerV1:
    """Structured identity of the lineage authority for a table/fragment."""

    kind: DatabaseCatalogOwnerKind
    code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "kind", _enum(self.kind, DatabaseCatalogOwnerKind, "owner.kind")
        )
        _owner(self.code, "owner.code")

    @property
    def coordinate(self) -> tuple[str, str]:
        return (self.kind.value, self.code)


@dataclass(frozen=True, slots=True)
class ComposedDatabaseLineageHeadV1:
    """One owner/head fact produced by the composed Alembic graph.

    Contributions author the structure expected at a head. This independent
    build input prevents an arbitrary, well-shaped revision string in a
    contribution from being accepted as the head the artifact actually
    composed.
    """

    owner: DatabaseCatalogOwnerV1
    revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.owner, DatabaseCatalogOwnerV1):
            raise ProductDatabaseCatalogError(
                "composed lineage head.owner must be DatabaseCatalogOwnerV1"
            )
        _revision(self.revision, "composed lineage head.revision")

    @property
    def coordinate(self) -> tuple[str, str]:
        return self.owner.coordinate


@dataclass(frozen=True, slots=True)
class PostgresCollationContractV1:
    """A non-default collation's structured catalogue coordinate."""

    schema: str
    name: str

    def __post_init__(self) -> None:
        _identifier(self.schema, "collation.schema")
        _identifier(self.name, "collation.name")


@dataclass(frozen=True, slots=True)
class DatabaseColumnContractV1:
    """One column in physical ordinal order."""

    name: str
    ordinal: int
    postgres_type: PostgresTypeContractV1
    nullable: bool
    generation: DatabaseColumnGeneration = DatabaseColumnGeneration.NONE
    expression: str = ""
    collation: PostgresCollationContractV1 | None = None

    def __post_init__(self) -> None:
        _identifier(self.name, "column.name")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ProductDatabaseCatalogError(
                "column.ordinal must be a positive integer"
            )
        if not isinstance(self.postgres_type, PostgresTypeContractV1):
            raise ProductDatabaseCatalogError(
                "column.postgres_type must be PostgresTypeContractV1"
            )
        if type(self.nullable) is not bool:
            raise ProductDatabaseCatalogError("column.nullable must be a boolean")
        generation = _enum(
            self.generation, DatabaseColumnGeneration, "column.generation"
        )
        object.__setattr__(self, "generation", generation)
        if (
            not isinstance(self.expression, str)
            or self.expression != self.expression.strip()
        ):
            raise ProductDatabaseCatalogError(
                "column.expression must be a trimmed string (empty when absent)"
            )
        requires_expression = generation in {
            DatabaseColumnGeneration.DEFAULT,
            DatabaseColumnGeneration.GENERATED_STORED,
        }
        if requires_expression and not self.expression:
            raise ProductDatabaseCatalogError(
                f"column generation {generation.value!r} requires an expression"
            )
        if not requires_expression and self.expression:
            raise ProductDatabaseCatalogError(
                f"column generation {generation.value!r} forbids an expression"
            )
        if (
            generation
            in {
                DatabaseColumnGeneration.IDENTITY_ALWAYS,
                DatabaseColumnGeneration.IDENTITY_BY_DEFAULT,
            }
            and self.nullable
        ):
            raise ProductDatabaseCatalogError("an identity column cannot be nullable")
        if self.collation is not None and not isinstance(
            self.collation, PostgresCollationContractV1
        ):
            raise ProductDatabaseCatalogError(
                "column.collation must be PostgresCollationContractV1 or None"
            )


@dataclass(frozen=True, slots=True)
class DatabaseTableContractV1:
    """The exact structural contract for one owned table."""

    schema: str
    name: str
    owner: DatabaseCatalogOwnerV1
    plane: DatabasePersistencePlane
    relation_kind: DatabaseRelationKind
    columns: tuple[DatabaseColumnContractV1, ...]

    def __post_init__(self) -> None:
        _identifier(self.schema, "table.schema")
        _identifier(self.name, "table.name")
        if not isinstance(self.owner, DatabaseCatalogOwnerV1):
            raise ProductDatabaseCatalogError(
                "table.owner must be DatabaseCatalogOwnerV1"
            )
        object.__setattr__(
            self, "plane", _enum(self.plane, DatabasePersistencePlane, "table.plane")
        )
        object.__setattr__(
            self,
            "relation_kind",
            _enum(self.relation_kind, DatabaseRelationKind, "table.relation_kind"),
        )
        _validate_columns(self.columns, f"table {self.schema}.{self.name}")

    @property
    def coordinate(self) -> tuple[str, str]:
        return (self.schema, self.name)


def _validate_columns(columns: object, context: str) -> None:
    if not isinstance(columns, tuple) or not columns:
        raise ProductDatabaseCatalogError(
            f"{context} requires a non-empty columns tuple"
        )
    if not all(isinstance(column, DatabaseColumnContractV1) for column in columns):
        raise ProductDatabaseCatalogError(
            f"{context} columns must be DatabaseColumnContractV1 values"
        )
    names = [column.name for column in columns]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ProductDatabaseCatalogError(f"{context} repeats columns {duplicates}")
    ordinals = tuple(column.ordinal for column in columns)
    expected = tuple(range(1, len(columns) + 1))
    if ordinals != expected:
        raise ProductDatabaseCatalogError(
            f"{context} column ordinals must be contiguous and ordered "
            f"{expected}, got {ordinals}"
        )


@dataclass(frozen=True, slots=True)
class ModuleDatabaseCatalogContributionV1:
    """Module-authored structure for tables the manifest already owns.

    ``ModuleManifest`` validates table names against ``tables`` and
    ``platform_tables``. This type intentionally has no owner, schema or plane:
    the snapshot derives all three from that existing authority.
    """

    lineage_head: str
    tables: tuple[ModuleDatabaseTableContractV1, ...]

    def __post_init__(self) -> None:
        _revision(self.lineage_head, "module database_catalog.lineage_head")
        if not isinstance(self.tables, tuple) or not self.tables:
            raise ProductDatabaseCatalogError(
                "module database_catalog requires a non-empty tables tuple"
            )
        if not all(
            isinstance(table, ModuleDatabaseTableContractV1) for table in self.tables
        ):
            raise ProductDatabaseCatalogError(
                "module database_catalog tables must be "
                "ModuleDatabaseTableContractV1 values"
            )
        names = tuple(table.name for table in self.tables)
        if names != tuple(sorted(set(names))):
            raise ProductDatabaseCatalogError(
                "module database_catalog table names must be unique and sorted"
            )


@dataclass(frozen=True, slots=True)
class ModuleDatabaseTableContractV1:
    """Table-local structure; ModuleManifest supplies identity and plane."""

    name: str
    relation_kind: DatabaseRelationKind
    columns: tuple[DatabaseColumnContractV1, ...]

    def __post_init__(self) -> None:
        _identifier(self.name, "module table.name")
        object.__setattr__(
            self,
            "relation_kind",
            _enum(
                self.relation_kind,
                DatabaseRelationKind,
                "module table.relation_kind",
            ),
        )
        _validate_columns(self.columns, f"module table {self.name!r}")


@dataclass(frozen=True, slots=True)
class HostDatabaseCatalogFragmentV1:
    """Explicit structure owned by a host migration lineage (usually public)."""

    owner: DatabaseCatalogOwnerV1
    lineage_head: str
    tables: tuple[DatabaseTableContractV1, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.owner, DatabaseCatalogOwnerV1):
            raise ProductDatabaseCatalogError(
                "host fragment.owner must be DatabaseCatalogOwnerV1"
            )
        if self.owner.kind is DatabaseCatalogOwnerKind.MODULE:
            raise ProductDatabaseCatalogError(
                "a host fragment cannot use the module owner kind"
            )
        if (
            self.owner.kind is DatabaseCatalogOwnerKind.KERNEL
            and self.owner.code != "kernel"
        ):
            raise ProductDatabaseCatalogError(
                "a kernel host fragment must use the stable owner code 'kernel'"
            )
        _revision(self.lineage_head, "host fragment.lineage_head")
        _validate_canonical_tables(
            self.tables, f"host fragment {self.owner.coordinate!r}"
        )
        for table in self.tables:
            if table.owner != self.owner:
                raise ProductDatabaseCatalogError(
                    f"host fragment {self.owner.coordinate!r} contains table "
                    f"{table.schema}.{table.name} owned by "
                    f"{table.owner.coordinate!r}"
                )


@dataclass(frozen=True, slots=True)
class ProductDatabaseCatalogFragmentV1:
    """One selected lineage contribution embedded in a product snapshot."""

    owner: DatabaseCatalogOwnerV1
    lineage_head: str
    selected_planes: tuple[DatabasePersistencePlane, ...]
    tables: tuple[DatabaseTableContractV1, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.owner, DatabaseCatalogOwnerV1):
            raise ProductDatabaseCatalogError(
                "fragment.owner must be DatabaseCatalogOwnerV1"
            )
        _trimmed(self.lineage_head, "fragment.lineage_head")
        if not isinstance(self.selected_planes, tuple) or not self.selected_planes:
            raise ProductDatabaseCatalogError(
                f"fragment {self.owner.coordinate!r} selected_planes must be a "
                "non-empty tuple"
            )
        planes = tuple(
            _enum(plane, DatabasePersistencePlane, "fragment.selected_planes")
            for plane in self.selected_planes
        )
        canonical_planes = tuple(sorted(set(planes), key=lambda item: item.value))
        if planes != canonical_planes:
            raise ProductDatabaseCatalogError(
                f"fragment {self.owner.coordinate!r} selected_planes must be "
                "unique and sorted"
            )
        object.__setattr__(self, "selected_planes", planes)
        _validate_canonical_tables(self.tables, f"fragment {self.owner.coordinate!r}")
        table_planes = frozenset(table.plane for table in self.tables)
        if table_planes != frozenset(planes):
            raise ProductDatabaseCatalogError(
                f"fragment {self.owner.coordinate!r} selected planes do not "
                "exactly match its table planes"
            )
        for table in self.tables:
            if table.owner != self.owner:
                raise ProductDatabaseCatalogError(
                    f"fragment {self.owner.coordinate!r} contains table "
                    f"{table.schema}.{table.name} owned by "
                    f"{table.owner.coordinate!r}"
                )


def _validate_canonical_tables(
    tables: object, context: str
) -> tuple[DatabaseTableContractV1, ...]:
    if not isinstance(tables, tuple) or not tables:
        raise ProductDatabaseCatalogError(
            f"{context} requires a non-empty tables tuple"
        )
    if not all(isinstance(table, DatabaseTableContractV1) for table in tables):
        raise ProductDatabaseCatalogError(
            f"{context} tables must be DatabaseTableContractV1 values"
        )
    coordinates = tuple(table.coordinate for table in tables)
    if coordinates != tuple(sorted(set(coordinates))):
        raise ProductDatabaseCatalogError(
            f"{context} table coordinates must be unique and sorted canonically"
        )
    return tables


@dataclass(frozen=True, slots=True)
class ModuleDatabaseCatalogSnapshot:
    """Canonical tables-and-columns contract for one stateful module release.

    This is the independently publishable unit. A module wheel can attest it
    without claiming that unrelated product schemas are complete. Product
    snapshots remain the all-or-nothing composition of these module snapshots
    plus host fragments.

    V1 deliberately does not describe constraints, indexes, triggers,
    privileges or policies. The explicit ``scope`` field prevents consumers
    from treating those absences as declarations.
    """

    distribution_name: str
    distribution_version: str
    module_code: str
    module_release_version: str
    manifest_contract_version: int
    database_schema: str
    lineage_head: str
    tables: tuple[DatabaseTableContractV1, ...]
    _factory_token: InitVar[object | None] = None

    schema: ClassVar[str] = MODULE_DATABASE_CATALOG_SCHEMA
    scope: ClassVar[str] = DATABASE_CATALOG_SCOPE

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _SNAPSHOT_FACTORY_TOKEN:
            raise ProductDatabaseCatalogError(
                "ModuleDatabaseCatalogSnapshot is factory-only; use "
                "from_manifest or from_json_bytes"
            )
        _owner(self.distribution_name, "distribution_name")
        _trimmed(self.distribution_version, "distribution_version")
        _owner(self.module_code, "module_code")
        _trimmed(self.module_release_version, "module_release_version")
        if (
            type(self.manifest_contract_version) is not int
            or self.manifest_contract_version < 1
        ):
            raise ProductDatabaseCatalogError(
                "manifest_contract_version must be a positive integer"
            )
        _identifier(self.database_schema, "database_schema")
        _revision(self.lineage_head, "lineage_head")
        _validate_canonical_tables(self.tables, f"module {self.module_code!r}")
        expected_owner = DatabaseCatalogOwnerV1(
            DatabaseCatalogOwnerKind.MODULE, self.module_code
        )
        for table in self.tables:
            if table.schema != self.database_schema:
                raise ProductDatabaseCatalogError(
                    f"module {self.module_code!r} table {table.name!r} uses schema "
                    f"{table.schema!r}, expected {self.database_schema!r}"
                )
            if table.owner != expected_owner:
                raise ProductDatabaseCatalogError(
                    f"module {self.module_code!r} table {table.name!r} has owner "
                    f"{table.owner.coordinate!r}"
                )
            if table.plane is DatabasePersistencePlane.HOST:
                raise ProductDatabaseCatalogError(
                    f"module {self.module_code!r} table {table.name!r} cannot use "
                    "the host persistence plane"
                )

    @classmethod
    def from_manifest(
        cls,
        manifest: object,
        *,
        distribution_name: str,
        distribution_version: str,
        composed_lineage_head: ComposedDatabaseLineageHeadV1,
    ) -> ModuleDatabaseCatalogSnapshot:
        """Build one module release snapshot against graph-derived head evidence."""

        from dotmac_kernel.modules import ModuleManifest

        if not isinstance(manifest, ModuleManifest):
            raise ProductDatabaseCatalogError(
                "manifest must be a ModuleManifest; free-form module declarations "
                "are not accepted"
            )
        if not manifest.is_stateful or manifest.db_schema is None:
            raise ProductDatabaseCatalogError(
                f"module {manifest.code!r} is stateless and has no database catalogue"
            )
        contribution = manifest.database_catalog
        if contribution is None:
            raise ProductDatabaseCatalogError(
                f"stateful module {manifest.code!r} has no database_catalog "
                "contribution"
            )
        if not isinstance(composed_lineage_head, ComposedDatabaseLineageHeadV1):
            raise ProductDatabaseCatalogError(
                "composed_lineage_head must be ComposedDatabaseLineageHeadV1"
            )
        expected_owner = DatabaseCatalogOwnerV1(
            DatabaseCatalogOwnerKind.MODULE, manifest.code
        )
        if composed_lineage_head.owner != expected_owner:
            raise ProductDatabaseCatalogError(
                f"composed lineage owner {composed_lineage_head.owner.coordinate!r} "
                f"does not identify module {manifest.code!r}"
            )
        if composed_lineage_head.revision != contribution.lineage_head:
            raise ProductDatabaseCatalogError(
                f"module {manifest.code!r} authored lineage head "
                f"{contribution.lineage_head!r} does not match composed head "
                f"{composed_lineage_head.revision!r}"
            )
        tables = tuple(
            DatabaseTableContractV1(
                schema=manifest.db_schema,
                name=table.name,
                owner=expected_owner,
                plane=(
                    DatabasePersistencePlane.TENANT
                    if table.name in manifest.tables
                    else DatabasePersistencePlane.PLATFORM
                ),
                relation_kind=table.relation_kind,
                columns=table.columns,
            )
            for table in contribution.tables
        )
        return cls(
            distribution_name=distribution_name,
            distribution_version=distribution_version,
            module_code=manifest.code,
            module_release_version=manifest.version,
            manifest_contract_version=manifest.contract_version,
            database_schema=manifest.db_schema,
            lineage_head=contribution.lineage_head,
            tables=tables,
            _factory_token=_SNAPSHOT_FACTORY_TOKEN,
        )

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            {
                "schema": self.schema,
                "scope": self.scope,
                "distribution_name": self.distribution_name,
                "distribution_version": self.distribution_version,
                "module_code": self.module_code,
                "module_release_version": self.module_release_version,
                "manifest_contract_version": self.manifest_contract_version,
                "database_schema": self.database_schema,
                "lineage_head": self.lineage_head,
                "tables": [_table_document(table) for table in self.tables],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        return _digest(self.to_json_bytes())

    @classmethod
    def from_json_bytes(
        cls, payload: bytes, *, expected_digest: str | None = None
    ) -> ModuleDatabaseCatalogSnapshot:
        if not isinstance(payload, bytes):
            raise ProductDatabaseCatalogError("module database catalogue must be bytes")
        _require_expected_digest(payload, expected_digest)
        document = _load_json_document(payload, "module database catalogue")
        item = _require_fields(
            document, _MODULE_SNAPSHOT_FIELDS, "module database catalogue"
        )
        if item["schema"] != MODULE_DATABASE_CATALOG_SCHEMA:
            raise ProductDatabaseCatalogError(
                f"unsupported module database catalogue schema {item['schema']!r}"
            )
        if item["scope"] != DATABASE_CATALOG_SCOPE:
            raise ProductDatabaseCatalogError(
                f"unsupported module database catalogue scope {item['scope']!r}"
            )
        tables = item["tables"]
        if not isinstance(tables, list):
            raise ProductDatabaseCatalogError("tables must be a JSON array")
        snapshot = cls(
            distribution_name=cast(str, item["distribution_name"]),
            distribution_version=cast(str, item["distribution_version"]),
            module_code=cast(str, item["module_code"]),
            module_release_version=cast(str, item["module_release_version"]),
            manifest_contract_version=cast(int, item["manifest_contract_version"]),
            database_schema=cast(str, item["database_schema"]),
            lineage_head=cast(str, item["lineage_head"]),
            tables=tuple(_parse_table(table) for table in tables),
            _factory_token=_SNAPSHOT_FACTORY_TOKEN,
        )
        if payload != snapshot.to_json_bytes():
            raise ProductDatabaseCatalogError(
                "module database catalogue is valid JSON but not canonical"
            )
        return snapshot


@dataclass(frozen=True, slots=True)
class ProductDatabaseCatalogSnapshot:
    """Exact tables-and-columns contract for one product release.

    Construction is factory-only. ``from_assembly`` proves composition from
    module and host declarations; ``from_json_bytes`` proves a strict canonical
    document received across a release boundary. Free-form object construction
    is not a third, weaker path.
    """

    product_code: str
    product_version: str
    postgres_major: int
    complete_schemas: tuple[str, ...]
    fragments: tuple[ProductDatabaseCatalogFragmentV1, ...]
    _factory_token: InitVar[object | None] = None

    schema: ClassVar[str] = PRODUCT_DATABASE_CATALOG_SCHEMA
    scope: ClassVar[str] = DATABASE_CATALOG_SCOPE

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _SNAPSHOT_FACTORY_TOKEN:
            raise ProductDatabaseCatalogError(
                "ProductDatabaseCatalogSnapshot is factory-only; use "
                "from_assembly or from_json_bytes"
            )
        if (
            not isinstance(self.product_code, str)
            or _PRODUCT_CODE_RE.fullmatch(self.product_code) is None
        ):
            raise ProductDatabaseCatalogError(
                "product_code must be a lowercase stable code of at most 120 characters"
            )
        _trimmed(self.product_version, "product_version")
        if type(self.postgres_major) is not int or not 10 <= self.postgres_major <= 99:
            raise ProductDatabaseCatalogError(
                "postgres_major must be an integer between 10 and 99"
            )
        if not isinstance(self.complete_schemas, tuple) or not self.complete_schemas:
            raise ProductDatabaseCatalogError(
                "complete_schemas must be a non-empty tuple"
            )
        for schema in self.complete_schemas:
            _identifier(schema, "complete_schemas item")
        if self.complete_schemas != tuple(sorted(set(self.complete_schemas))):
            raise ProductDatabaseCatalogError(
                "complete_schemas must be unique and sorted canonically"
            )
        if not isinstance(self.fragments, tuple) or not self.fragments:
            raise ProductDatabaseCatalogError("fragments must be a non-empty tuple")
        if not all(
            isinstance(fragment, ProductDatabaseCatalogFragmentV1)
            for fragment in self.fragments
        ):
            raise ProductDatabaseCatalogError(
                "fragments must be ProductDatabaseCatalogFragmentV1 values"
            )
        owners = tuple(fragment.owner.coordinate for fragment in self.fragments)
        if owners != tuple(sorted(set(owners))):
            raise ProductDatabaseCatalogError(
                "fragment owners must be unique and sorted canonically"
            )
        coordinates = [
            table.coordinate for fragment in self.fragments for table in fragment.tables
        ]
        duplicates = sorted(
            {
                coordinate
                for coordinate in coordinates
                if coordinates.count(coordinate) > 1
            }
        )
        if duplicates:
            raise ProductDatabaseCatalogError(
                f"database tables are declared by more than one fragment: {duplicates}"
            )
        schemas = tuple(sorted({schema for schema, _ in coordinates}))
        if schemas != self.complete_schemas:
            raise ProductDatabaseCatalogError(
                "complete_schemas must exactly equal the schemas covered by fragments; "
                f"declared={self.complete_schemas}, fragments={schemas}"
            )

    @classmethod
    def from_assembly(
        cls,
        assembly: ProductAssemblySpec,
        *,
        product_version: str,
        postgres_major: int,
        host_fragments: tuple[HostDatabaseCatalogFragmentV1, ...],
        composed_lineage_heads: tuple[ComposedDatabaseLineageHeadV1, ...],
    ) -> ProductDatabaseCatalogSnapshot:
        """Freeze installed module contributions and explicit host structure."""

        from dotmac_kernel.assembly import ProductAssemblySpec
        from dotmac_kernel.modules import ModuleRegistry
        from dotmac_kernel.namespaces import NamespaceRegistry

        if not isinstance(assembly, ProductAssemblySpec):
            raise ProductDatabaseCatalogError(
                "assembly must be a ProductAssemblySpec; free-form composition "
                "objects are not accepted"
            )
        if not isinstance(host_fragments, tuple):
            raise ProductDatabaseCatalogError("host_fragments must be a tuple")
        if not all(
            isinstance(fragment, HostDatabaseCatalogFragmentV1)
            for fragment in host_fragments
        ):
            raise ProductDatabaseCatalogError(
                "host_fragments must be HostDatabaseCatalogFragmentV1 values"
            )
        if not isinstance(composed_lineage_heads, tuple) or not all(
            isinstance(head, ComposedDatabaseLineageHeadV1)
            for head in composed_lineage_heads
        ):
            raise ProductDatabaseCatalogError(
                "composed_lineage_heads must be a tuple of "
                "ComposedDatabaseLineageHeadV1 values"
            )
        head_coordinates = tuple(head.coordinate for head in composed_lineage_heads)
        if head_coordinates != tuple(sorted(set(head_coordinates))):
            raise ProductDatabaseCatalogError(
                "composed_lineage_heads owners must be unique and sorted " "canonically"
            )
        kernel_fragments = tuple(
            fragment
            for fragment in host_fragments
            if fragment.owner.kind is DatabaseCatalogOwnerKind.KERNEL
        )
        if len(kernel_fragments) != 1:
            raise ProductDatabaseCatalogError(
                "host_fragments must contain exactly one kernel-owned fragment; "
                "omitting the host lineage would make public structure disappear "
                "from the supposedly complete product catalogue"
            )
        assembly_fragments = tuple(
            fragment
            for fragment in host_fragments
            if fragment.owner.kind is DatabaseCatalogOwnerKind.ASSEMBLY
        )
        if len(assembly_fragments) != 1:
            raise ProductDatabaseCatalogError(
                "host_fragments must contain exactly one assembly-owned fragment; "
                "the product's public migration lineage is part of the complete "
                "database declaration even when the kernel owns the schema"
            )
        if "public" not in {table.schema for table in kernel_fragments[0].tables}:
            raise ProductDatabaseCatalogError(
                "the kernel-owned host fragment must cover the public schema"
            )
        wrong_assembly_owners = sorted(
            fragment.owner.code
            for fragment in host_fragments
            if fragment.owner.kind is DatabaseCatalogOwnerKind.ASSEMBLY
            and fragment.owner.code != assembly.name
        )
        if wrong_assembly_owners:
            raise ProductDatabaseCatalogError(
                "assembly-owned host fragments must use ProductAssemblySpec.name; "
                f"expected={assembly.name!r}, got={wrong_assembly_owners}"
            )
        from dotmac_kernel.namespaces import (
            ASSEMBLY_MIGRATION_OWNER,
            KERNEL_MIGRATION_OWNER,
        )

        if (
            KERNEL_MIGRATION_OWNER.revision_pattern().fullmatch(
                kernel_fragments[0].lineage_head
            )
            is None
        ):
            raise ProductDatabaseCatalogError(
                "the kernel host fragment lineage_head does not belong to the "
                "kernel migration lineage"
            )
        if (
            ASSEMBLY_MIGRATION_OWNER.revision_pattern().fullmatch(
                assembly_fragments[0].lineage_head
            )
            is None
        ):
            raise ProductDatabaseCatalogError(
                "the assembly host fragment lineage_head does not belong to the "
                "assembly migration lineage"
            )
        registry = ModuleRegistry(assembly.modules)
        manifests = registry.startup_order()
        namespaces = NamespaceRegistry.from_manifests(
            manifests, module_planes=assembly.module_planes
        )
        fragments: list[ProductDatabaseCatalogFragmentV1] = []
        for manifest in manifests:
            if not manifest.is_stateful:
                if manifest.database_catalog is not None:
                    raise ProductDatabaseCatalogError(
                        f"stateless module {manifest.code!r} has a database catalogue"
                    )
                continue
            contribution = manifest.database_catalog
            if contribution is None:
                raise ProductDatabaseCatalogError(
                    f"stateful module {manifest.code!r} has no database_catalog "
                    "contribution; a complete snapshot cannot omit its structure"
                )
            module_schema = manifest.db_schema
            if module_schema is None:
                raise ProductDatabaseCatalogError(
                    f"stateful module {manifest.code!r} has no derived database "
                    "schema"
                )
            selected_names = namespaces.expected_tables(module_schema)
            module_owner = DatabaseCatalogOwnerV1(
                kind=DatabaseCatalogOwnerKind.MODULE, code=manifest.code
            )
            selected_tables = tuple(
                DatabaseTableContractV1(
                    schema=module_schema,
                    name=table.name,
                    owner=module_owner,
                    plane=(
                        DatabasePersistencePlane.TENANT
                        if table.name in manifest.tables
                        else DatabasePersistencePlane.PLATFORM
                    ),
                    relation_kind=table.relation_kind,
                    columns=table.columns,
                )
                for table in contribution.tables
                if table.name in selected_names
            )
            if not selected_tables:
                raise ProductDatabaseCatalogError(
                    f"module {manifest.code!r} contributes no table in the "
                    "selected planes"
                )
            selected_planes = tuple(
                sorted(
                    {table.plane for table in selected_tables},
                    key=lambda item: item.value,
                )
            )
            fragments.append(
                ProductDatabaseCatalogFragmentV1(
                    owner=module_owner,
                    lineage_head=contribution.lineage_head,
                    selected_planes=selected_planes,
                    tables=selected_tables,
                )
            )
        for host in host_fragments:
            fragments.append(
                ProductDatabaseCatalogFragmentV1(
                    owner=host.owner,
                    lineage_head=host.lineage_head,
                    selected_planes=tuple(
                        sorted(
                            {table.plane for table in host.tables},
                            key=lambda item: item.value,
                        )
                    ),
                    tables=host.tables,
                )
            )
        fragments_tuple = tuple(
            sorted(fragments, key=lambda item: item.owner.coordinate)
        )
        declared_heads = {
            fragment.owner.coordinate: fragment.lineage_head
            for fragment in fragments_tuple
        }
        composed_heads = {
            head.owner.coordinate: head.revision for head in composed_lineage_heads
        }
        if composed_heads != declared_heads:
            missing = sorted(set(declared_heads) - set(composed_heads))
            unexpected = sorted(set(composed_heads) - set(declared_heads))
            mismatched = sorted(
                coordinate
                for coordinate in set(declared_heads) & set(composed_heads)
                if declared_heads[coordinate] != composed_heads[coordinate]
            )
            raise ProductDatabaseCatalogError(
                "database catalogue lineage heads do not match the composed "
                f"migration graph; missing={missing}, unexpected={unexpected}, "
                f"mismatched={mismatched}"
            )
        complete_schemas = tuple(
            sorted(
                {
                    table.schema
                    for fragment in fragments_tuple
                    for table in fragment.tables
                }
            )
        )
        return cls(
            product_code=assembly.name,
            product_version=product_version,
            postgres_major=postgres_major,
            complete_schemas=complete_schemas,
            fragments=fragments_tuple,
            _factory_token=_SNAPSHOT_FACTORY_TOKEN,
        )

    def to_json_bytes(self) -> bytes:
        payload = {
            "schema": self.schema,
            "scope": self.scope,
            "product_code": self.product_code,
            "product_version": self.product_version,
            "postgres_major": self.postgres_major,
            "complete_schemas": list(self.complete_schemas),
            "fragments": [_fragment_document(fragment) for fragment in self.fragments],
        }
        return json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        return _digest(self.to_json_bytes())

    @classmethod
    def from_json_bytes(
        cls, payload: bytes, *, expected_digest: str | None = None
    ) -> ProductDatabaseCatalogSnapshot:
        """Strictly parse the one canonical byte representation."""

        if not isinstance(payload, bytes):
            raise ProductDatabaseCatalogError(
                "database catalogue payload must be bytes"
            )
        _require_expected_digest(payload, expected_digest)
        document = _load_json_document(payload, "database catalogue")
        _require_fields(document, _SNAPSHOT_FIELDS, "database catalogue")
        if document["schema"] != PRODUCT_DATABASE_CATALOG_SCHEMA:
            raise ProductDatabaseCatalogError(
                f"unsupported database catalogue schema {document['schema']!r}"
            )
        if document["scope"] != DATABASE_CATALOG_SCOPE:
            raise ProductDatabaseCatalogError(
                f"unsupported database catalogue scope {document['scope']!r}"
            )
        schemas = document["complete_schemas"]
        fragments = document["fragments"]
        if not isinstance(schemas, list):
            raise ProductDatabaseCatalogError("complete_schemas must be a JSON array")
        if not isinstance(fragments, list):
            raise ProductDatabaseCatalogError("fragments must be a JSON array")
        snapshot = cls(
            product_code=cast(str, document["product_code"]),
            product_version=cast(str, document["product_version"]),
            postgres_major=cast(int, document["postgres_major"]),
            complete_schemas=tuple(schemas),
            fragments=tuple(_parse_fragment(item) for item in fragments),
            _factory_token=_SNAPSHOT_FACTORY_TOKEN,
        )
        if payload != snapshot.to_json_bytes():
            raise ProductDatabaseCatalogError(
                "database catalogue is valid JSON but not the canonical document"
            )
        return snapshot


def _require_expected_digest(payload: bytes, expected_digest: str | None) -> None:
    if expected_digest is None:
        return
    if (
        not isinstance(expected_digest, str)
        or _SHA256_RE.fullmatch(expected_digest) is None
    ):
        raise ProductDatabaseCatalogError(
            "expected_digest must be 'sha256:' plus 64 lowercase hex digits"
        )
    actual_digest = _digest(payload)
    if actual_digest != expected_digest:
        raise ProductDatabaseCatalogDigestMismatchError(
            f"database catalogue digest {actual_digest} does not match "
            f"expected {expected_digest}"
        )


def _load_json_document(payload: bytes, context: str) -> dict[str, object]:
    try:
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_object_without_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductDatabaseCatalogError(
            f"{context} must be a UTF-8 JSON document"
        ) from exc
    if not isinstance(document, dict):
        raise ProductDatabaseCatalogError(f"{context} root must be an object")
    return document


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ProductDatabaseCatalogError(f"duplicate JSON field {key!r}")
        document[key] = value
    return document


def _require_fields(
    document: object, fields: frozenset[str], context: str
) -> dict[str, object]:
    if not isinstance(document, dict):
        raise ProductDatabaseCatalogError(f"{context} must be an object")
    if set(document) != fields:
        missing = sorted(fields - set(document))
        unknown = sorted(set(document) - fields)
        raise ProductDatabaseCatalogError(
            f"{context} fields differ: missing={missing}, unknown={unknown}"
        )
    return document


def _type_document(postgres_type: PostgresTypeContractV1) -> dict[str, object]:
    return {
        "kind": postgres_type.kind.value,
        "schema": postgres_type.schema,
        "name": postgres_type.name,
        "formatted": postgres_type.formatted,
    }


def _collation_document(
    collation: PostgresCollationContractV1 | None,
) -> dict[str, object] | None:
    if collation is None:
        return None
    return {"schema": collation.schema, "name": collation.name}


def _owner_document(owner: DatabaseCatalogOwnerV1) -> dict[str, object]:
    return {"kind": owner.kind.value, "code": owner.code}


def _column_document(column: DatabaseColumnContractV1) -> dict[str, object]:
    return {
        "name": column.name,
        "ordinal": column.ordinal,
        "postgres_type": _type_document(column.postgres_type),
        "nullable": column.nullable,
        "generation": column.generation.value,
        "expression": column.expression,
        "collation": _collation_document(column.collation),
    }


def _table_document(table: DatabaseTableContractV1) -> dict[str, object]:
    return {
        "schema": table.schema,
        "name": table.name,
        "owner": _owner_document(table.owner),
        "plane": table.plane.value,
        "relation_kind": table.relation_kind.value,
        "columns": [_column_document(column) for column in table.columns],
    }


def _fragment_document(
    fragment: ProductDatabaseCatalogFragmentV1,
) -> dict[str, object]:
    return {
        "owner": _owner_document(fragment.owner),
        "lineage_head": fragment.lineage_head,
        "selected_planes": [plane.value for plane in fragment.selected_planes],
        "tables": [_table_document(table) for table in fragment.tables],
    }


def _parse_type(document: object) -> PostgresTypeContractV1:
    item = _require_fields(
        document, frozenset({"kind", "schema", "name", "formatted"}), "postgres_type"
    )
    return PostgresTypeContractV1(
        kind=cast(PostgresTypeKind, item["kind"]),
        schema=cast(str, item["schema"]),
        name=cast(str, item["name"]),
        formatted=cast(str, item["formatted"]),
    )


def _parse_owner(document: object) -> DatabaseCatalogOwnerV1:
    item = _require_fields(document, frozenset({"kind", "code"}), "owner")
    return DatabaseCatalogOwnerV1(
        kind=cast(DatabaseCatalogOwnerKind, item["kind"]),
        code=cast(str, item["code"]),
    )


def _parse_collation(document: object) -> PostgresCollationContractV1 | None:
    if document is None:
        return None
    item = _require_fields(document, frozenset({"schema", "name"}), "collation")
    return PostgresCollationContractV1(
        schema=cast(str, item["schema"]),
        name=cast(str, item["name"]),
    )


def _parse_column(document: object) -> DatabaseColumnContractV1:
    item = _require_fields(
        document,
        frozenset(
            {
                "name",
                "ordinal",
                "postgres_type",
                "nullable",
                "generation",
                "expression",
                "collation",
            }
        ),
        "column",
    )
    return DatabaseColumnContractV1(
        name=cast(str, item["name"]),
        ordinal=cast(int, item["ordinal"]),
        postgres_type=_parse_type(item["postgres_type"]),
        nullable=cast(bool, item["nullable"]),
        generation=cast(DatabaseColumnGeneration, item["generation"]),
        expression=cast(str, item["expression"]),
        collation=_parse_collation(item["collation"]),
    )


def _parse_table(document: object) -> DatabaseTableContractV1:
    item = _require_fields(
        document,
        frozenset({"schema", "name", "owner", "plane", "relation_kind", "columns"}),
        "table",
    )
    columns = item["columns"]
    if not isinstance(columns, list):
        raise ProductDatabaseCatalogError("table.columns must be a JSON array")
    return DatabaseTableContractV1(
        schema=cast(str, item["schema"]),
        name=cast(str, item["name"]),
        owner=_parse_owner(item["owner"]),
        plane=cast(DatabasePersistencePlane, item["plane"]),
        relation_kind=cast(DatabaseRelationKind, item["relation_kind"]),
        columns=tuple(_parse_column(column) for column in columns),
    )


def _parse_fragment(document: object) -> ProductDatabaseCatalogFragmentV1:
    item = _require_fields(
        document,
        frozenset({"owner", "lineage_head", "selected_planes", "tables"}),
        "fragment",
    )
    planes = item["selected_planes"]
    tables = item["tables"]
    if not isinstance(planes, list):
        raise ProductDatabaseCatalogError("fragment.selected_planes must be an array")
    if not isinstance(tables, list):
        raise ProductDatabaseCatalogError("fragment.tables must be an array")
    return ProductDatabaseCatalogFragmentV1(
        owner=_parse_owner(item["owner"]),
        lineage_head=cast(str, item["lineage_head"]),
        selected_planes=cast(tuple[DatabasePersistencePlane, ...], tuple(planes)),
        tables=tuple(_parse_table(table) for table in tables),
    )


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "DATABASE_CATALOG_SCOPE",
    "MODULE_DATABASE_CATALOG_SCHEMA",
    "PRODUCT_DATABASE_CATALOG_SCHEMA",
    "DatabaseCatalogOwnerKind",
    "DatabaseCatalogOwnerV1",
    "ComposedDatabaseLineageHeadV1",
    "DatabaseColumnContractV1",
    "DatabaseColumnGeneration",
    "DatabasePersistencePlane",
    "DatabaseRelationKind",
    "DatabaseTableContractV1",
    "HostDatabaseCatalogFragmentV1",
    "ModuleDatabaseCatalogContributionV1",
    "ModuleDatabaseCatalogSnapshot",
    "ModuleDatabaseTableContractV1",
    "PostgresCollationContractV1",
    "PostgresTypeContractV1",
    "PostgresTypeKind",
    "ProductDatabaseCatalogDigestMismatchError",
    "ProductDatabaseCatalogError",
    "ProductDatabaseCatalogFragmentV1",
    "ProductDatabaseCatalogSnapshot",
]
