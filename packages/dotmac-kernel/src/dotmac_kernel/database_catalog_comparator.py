"""Read-only PostgreSQL observation and pure tables/columns comparison.

The observer is an evidence producer, never a declaration author. V1 is
explicitly limited to schemas, tables and columns; constraints, indexes,
policies, privileges and triggers are outside its scope and cannot be inferred
from a successful result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import InitVar, dataclass
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import text

from dotmac_kernel.product_database_catalog import (
    DATABASE_CATALOG_SCOPE,
    DatabaseColumnContractV1,
    DatabaseColumnGeneration,
    DatabaseRelationKind,
    ModuleDatabaseCatalogSnapshot,
    PostgresCollationContractV1,
    PostgresTypeContractV1,
    PostgresTypeKind,
    ProductDatabaseCatalogError,
    ProductDatabaseCatalogSnapshot,
)

POSTGRES_TABLES_COLUMNS_OBSERVATION_SCHEMA: Final[str] = (
    "dotmac.postgresql-tables-columns-observation/v1"
)
DATABASE_CATALOG_COMPARATOR_ID: Final[str] = (
    "dotmac.database-catalog-tables-columns-comparator/v1"
)
_COMPARISON_FACTORY_TOKEN = object()

_SELECTED_SCHEMAS_SQL: Final[str] = (
    "SELECT nspname FROM pg_namespace WHERE nspname = ANY(:schemas) ORDER BY nspname"
)
_APPLICATION_SCHEMAS_SQL: Final[str] = (
    "SELECT nspname FROM pg_namespace "
    "WHERE nspname <> 'information_schema' "
    "AND nspname NOT LIKE 'pg_%' ORDER BY nspname"
)
_COLUMNS_SQL: Final[str] = """
SELECT n.nspname, c.relname, c.relkind, a.attname, a.attnum,
       tn.nspname, t.typname, t.typtype, t.typelem,
       format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
       a.attidentity, a.attgenerated,
       CASE
         WHEN a.attidentity <> '' THEN ''
         WHEN a.attgenerated <> '' THEN pg_get_expr(ad.adbin, ad.adrelid)
         WHEN ad.oid IS NOT NULL THEN pg_get_expr(ad.adbin, ad.adrelid)
         ELSE ''
       END,
       CASE WHEN a.attcollation <> 0 AND a.attcollation <> t.typcollation
            THEN cn.nspname ELSE NULL END,
       CASE WHEN a.attcollation <> 0 AND a.attcollation <> t.typcollation
            THEN coll.collname ELSE NULL END
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid
JOIN pg_namespace tn ON tn.oid = t.typnamespace
LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
LEFT JOIN pg_collation coll ON coll.oid = a.attcollation
LEFT JOIN pg_namespace cn ON cn.oid = coll.collnamespace
WHERE n.nspname = ANY(:schemas)
  AND c.relkind IN ('r', 'p')
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY n.nspname, c.relname, a.attnum
"""


class DatabaseCatalogFactDimension(StrEnum):
    TABLE = "table"
    COLUMN = "column"


class DatabaseCatalogFactDirection(StrEnum):
    DECLARED_BUT_ABSENT = "declared_but_absent"
    PRESENT_BUT_UNDECLARED = "present_but_undeclared"


class DatabaseCatalogFactAttribute(StrEnum):
    PRESENCE = "presence"
    RELATION_KIND = "relation_kind"
    ORDINAL = "ordinal"
    POSTGRES_TYPE = "postgres_type"
    NULLABLE = "nullable"
    GENERATION = "generation"
    EXPRESSION = "expression"
    COLLATION = "collation"


class DatabaseCatalogDeclarationScope(StrEnum):
    MODULE = "module"
    PRODUCT = "product"


def _catalog_identifier(value: object, where: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or len(value.encode("utf-8")) > 63
    ):
        raise ProductDatabaseCatalogError(f"{where} is not a PostgreSQL identifier")
    return value


@dataclass(frozen=True, slots=True)
class ObservedDatabaseTableV1:
    schema: str
    name: str
    relation_kind: DatabaseRelationKind
    columns: tuple[DatabaseColumnContractV1, ...]

    def __post_init__(self) -> None:
        _catalog_identifier(self.schema, "observed table.schema")
        _catalog_identifier(self.name, "observed table.name")
        try:
            relation_kind = DatabaseRelationKind(self.relation_kind)
        except (TypeError, ValueError) as exc:
            raise ProductDatabaseCatalogError(
                "observed table.relation_kind is unknown"
            ) from exc
        object.__setattr__(self, "relation_kind", relation_kind)
        if (
            not isinstance(self.columns, tuple)
            or not self.columns
            or not all(
                isinstance(column, DatabaseColumnContractV1) for column in self.columns
            )
        ):
            raise ProductDatabaseCatalogError(
                "observed table.columns must be a non-empty typed tuple"
            )
        names = tuple(column.name for column in self.columns)
        if len(names) != len(set(names)):
            raise ProductDatabaseCatalogError("observed table column names repeat")
        ordinals = tuple(column.ordinal for column in self.columns)
        if ordinals != tuple(range(1, len(self.columns) + 1)):
            raise ProductDatabaseCatalogError(
                "observed table columns must be in contiguous ordinal order"
            )

    @property
    def coordinate(self) -> tuple[str, str]:
        return (self.schema, self.name)


@dataclass(frozen=True, slots=True)
class PostgresTablesColumnsObservationV1:
    postgres_major: int
    covered_schemas: tuple[str, ...]
    present_schemas: tuple[str, ...]
    tables: tuple[ObservedDatabaseTableV1, ...]
    database_extent_complete: bool

    schema: Final[str] = POSTGRES_TABLES_COLUMNS_OBSERVATION_SCHEMA
    scope: Final[str] = DATABASE_CATALOG_SCOPE

    def __post_init__(self) -> None:
        if type(self.postgres_major) is not int or self.postgres_major < 10:
            raise ProductDatabaseCatalogError("observed postgres_major is invalid")
        for name, values in (
            ("covered_schemas", self.covered_schemas),
            ("present_schemas", self.present_schemas),
        ):
            if not isinstance(values, tuple) or not all(
                isinstance(value, str) for value in values
            ):
                raise ProductDatabaseCatalogError(
                    f"observation {name} must be a string tuple"
                )
            for value in values:
                _catalog_identifier(value, f"observation {name} item")
            if values != tuple(sorted(set(values))):
                raise ProductDatabaseCatalogError(
                    f"observation {name} must be unique and sorted"
                )
        if not set(self.present_schemas) <= set(self.covered_schemas):
            raise ProductDatabaseCatalogError(
                "present_schemas must be a subset of covered_schemas"
            )
        if not isinstance(self.tables, tuple) or not all(
            isinstance(table, ObservedDatabaseTableV1) for table in self.tables
        ):
            raise ProductDatabaseCatalogError(
                "observation tables must be an ObservedDatabaseTableV1 tuple"
            )
        if type(self.database_extent_complete) is not bool:
            raise ProductDatabaseCatalogError(
                "observation database_extent_complete must be a boolean"
            )
        coordinates = tuple(table.coordinate for table in self.tables)
        if coordinates != tuple(sorted(set(coordinates))):
            raise ProductDatabaseCatalogError(
                "observed table coordinates must be unique and sorted"
            )
        if any(table.schema not in self.present_schemas for table in self.tables):
            raise ProductDatabaseCatalogError(
                "an observed table belongs to a schema not marked present"
            )

    def to_json_bytes(self) -> bytes:
        document = {
            "schema": self.schema,
            "scope": self.scope,
            "postgres_major": self.postgres_major,
            "covered_schemas": list(self.covered_schemas),
            "present_schemas": list(self.present_schemas),
            "database_extent_complete": self.database_extent_complete,
            "tables": [_observed_table_document(table) for table in self.tables],
        }
        return json.dumps(
            document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    @property
    def digest(self) -> str:
        return _digest(self.to_json_bytes())

    @classmethod
    def from_json_bytes(
        cls, payload: bytes, *, expected_digest: str
    ) -> PostgresTablesColumnsObservationV1:
        if _digest(payload) != expected_digest:
            raise ProductDatabaseCatalogError("observation digest mismatch")
        try:
            item = json.loads(
                payload.decode("utf-8"), object_pairs_hook=_without_duplicate_fields
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProductDatabaseCatalogError("observation must be UTF-8 JSON") from exc
        fields = {
            "schema",
            "scope",
            "postgres_major",
            "covered_schemas",
            "present_schemas",
            "database_extent_complete",
            "tables",
        }
        if not isinstance(item, dict) or set(item) != fields:
            raise ProductDatabaseCatalogError("observation fields differ")
        if item["schema"] != POSTGRES_TABLES_COLUMNS_OBSERVATION_SCHEMA:
            raise ProductDatabaseCatalogError("unsupported observation schema")
        if item["scope"] != DATABASE_CATALOG_SCOPE:
            raise ProductDatabaseCatalogError("unsupported observation scope")
        if not isinstance(item["covered_schemas"], list) or not isinstance(
            item["present_schemas"], list
        ):
            raise ProductDatabaseCatalogError(
                "observation schema coordinates must be arrays"
            )
        if not isinstance(item["tables"], list):
            raise ProductDatabaseCatalogError("observation tables must be an array")
        snapshot = cls(
            postgres_major=item["postgres_major"],
            covered_schemas=tuple(item["covered_schemas"]),
            present_schemas=tuple(item["present_schemas"]),
            tables=tuple(_parse_observed_table(table) for table in item["tables"]),
            database_extent_complete=item["database_extent_complete"],
        )
        if snapshot.to_json_bytes() != payload:
            raise ProductDatabaseCatalogError("observation is not canonical")
        return snapshot


def _without_duplicate_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProductDatabaseCatalogError(f"duplicate observation field {key!r}")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class DatabaseCatalogDriftV1:
    dimension: DatabaseCatalogFactDimension
    direction: DatabaseCatalogFactDirection
    schema: str
    table: str
    column: str
    attribute: DatabaseCatalogFactAttribute
    declared: str
    observed: str

    def __post_init__(self) -> None:
        try:
            dimension = DatabaseCatalogFactDimension(self.dimension)
            direction = DatabaseCatalogFactDirection(self.direction)
            attribute = DatabaseCatalogFactAttribute(self.attribute)
        except (TypeError, ValueError) as exc:
            raise ProductDatabaseCatalogError(
                "database drift vocabulary is unknown"
            ) from exc
        object.__setattr__(self, "dimension", dimension)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "attribute", attribute)
        _catalog_identifier(self.schema, "database drift.schema")
        _catalog_identifier(self.table, "database drift.table")
        if not isinstance(self.column, str):
            raise ProductDatabaseCatalogError("database drift.column must be a string")
        if dimension is DatabaseCatalogFactDimension.TABLE:
            if self.column or attribute not in {
                DatabaseCatalogFactAttribute.PRESENCE,
                DatabaseCatalogFactAttribute.RELATION_KIND,
            }:
                raise ProductDatabaseCatalogError("invalid table drift fact key")
        else:
            _catalog_identifier(self.column, "database drift.column")
            if attribute is DatabaseCatalogFactAttribute.RELATION_KIND:
                raise ProductDatabaseCatalogError("relation_kind is a table fact")
        if not isinstance(self.declared, str) or not isinstance(self.observed, str):
            raise ProductDatabaseCatalogError(
                "database drift declared/observed values must be strings"
            )


@dataclass(frozen=True, slots=True)
class DatabaseCatalogComparisonV1:
    drifts: tuple[DatabaseCatalogDriftV1, ...]
    declaration_digest: str
    observation_digest: str
    postgres_major: int
    declaration_schema: str
    declaration_scope: DatabaseCatalogDeclarationScope
    complete_schemas: tuple[str, ...]
    product_code: str
    product_version: str
    measurement_issues: tuple[str, ...] = ()
    _factory_token: InitVar[object | None] = None
    comparator_id: Final[str] = DATABASE_CATALOG_COMPARATOR_ID
    scope: Final[str] = DATABASE_CATALOG_SCOPE

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _COMPARISON_FACTORY_TOKEN:
            raise ProductDatabaseCatalogError(
                "DatabaseCatalogComparisonV1 is verifier-produced; use a "
                "verify_*_database_catalog function"
            )
        if not isinstance(self.drifts, tuple) or not all(
            isinstance(drift, DatabaseCatalogDriftV1) for drift in self.drifts
        ):
            raise ProductDatabaseCatalogError("comparison drifts must be typed")
        if not isinstance(self.declaration_schema, str) or not self.declaration_schema:
            raise ProductDatabaseCatalogError(
                "comparison declaration_schema must be non-empty"
            )
        try:
            declaration_scope = DatabaseCatalogDeclarationScope(self.declaration_scope)
        except (TypeError, ValueError) as exc:
            raise ProductDatabaseCatalogError(
                "comparison declaration_scope is unknown"
            ) from exc
        object.__setattr__(self, "declaration_scope", declaration_scope)
        if (
            not isinstance(self.complete_schemas, tuple)
            or not self.complete_schemas
            or not all(isinstance(schema, str) for schema in self.complete_schemas)
            or self.complete_schemas != tuple(sorted(set(self.complete_schemas)))
        ):
            raise ProductDatabaseCatalogError(
                "comparison complete_schemas must be a non-empty canonical tuple"
            )
        for schema in self.complete_schemas:
            _catalog_identifier(schema, "comparison complete_schemas item")
        if declaration_scope is DatabaseCatalogDeclarationScope.MODULE:
            if len(self.complete_schemas) != 1:
                raise ProductDatabaseCatalogError(
                    "a module comparison must bind exactly one complete schema"
                )
            if self.product_code or self.product_version:
                raise ProductDatabaseCatalogError(
                    "a module comparison cannot carry product identity"
                )
        elif (
            not isinstance(self.product_code, str)
            or not self.product_code
            or not isinstance(self.product_version, str)
            or not self.product_version
        ):
            raise ProductDatabaseCatalogError(
                "a product comparison requires product_code and product_version"
            )
        keys = tuple(
            (
                drift.dimension.value,
                drift.schema,
                drift.table,
                drift.column,
                drift.attribute.value,
                drift.direction.value,
                drift.declared,
                drift.observed,
            )
            for drift in self.drifts
        )
        if keys != tuple(sorted(set(keys))):
            raise ProductDatabaseCatalogError(
                "comparison drifts must be unique and sorted"
            )
        if self.measurement_issues != tuple(sorted(set(self.measurement_issues))):
            raise ProductDatabaseCatalogError(
                "comparison measurement_issues must be unique and sorted"
            )

    @property
    def matched(self) -> bool:
        return not self.drifts and not self.measurement_issues


def observe_postgres_tables_columns(
    conn: Any,
    *,
    schemas: tuple[str, ...] | None,
) -> PostgresTablesColumnsObservationV1:
    """Read an exact selected scope, or every non-system schema when None."""

    if schemas is not None and schemas != tuple(sorted(set(schemas))):
        raise ProductDatabaseCatalogError("observer schemas must be unique and sorted")
    postgres_major = (
        int(conn.execute(text("SHOW server_version_num")).scalar_one()) // 10000
    )
    if schemas is None:
        present = tuple(
            row[0] for row in conn.execute(text(_APPLICATION_SCHEMAS_SQL)).all()
        )
        covered = present
        extent_complete = True
    else:
        covered = schemas
        present = tuple(
            row[0]
            for row in conn.execute(
                text(_SELECTED_SCHEMAS_SQL), {"schemas": list(schemas)}
            ).all()
        )
        extent_complete = False
    rows = conn.execute(text(_COLUMNS_SQL), {"schemas": list(present)}).all()
    grouped: dict[tuple[str, str, str], list[DatabaseColumnContractV1]] = {}
    for row in rows:
        (
            schema,
            table,
            relkind,
            column,
            ordinal,
            type_schema,
            type_name,
            type_kind,
            type_element,
            formatted,
            nullable,
            identity,
            generated,
            expression,
            collation_schema,
            collation_name,
        ) = row
        generation = _generation(identity, generated, expression)
        grouped.setdefault((schema, table, relkind), []).append(
            DatabaseColumnContractV1(
                name=column,
                ordinal=int(ordinal),
                postgres_type=PostgresTypeContractV1(
                    kind=_type_kind(type_kind, type_element),
                    schema=type_schema,
                    name=type_name,
                    formatted=formatted,
                ),
                nullable=bool(nullable),
                generation=generation,
                expression=str(expression or ""),
                collation=(
                    PostgresCollationContractV1(collation_schema, collation_name)
                    if collation_schema is not None
                    else None
                ),
            )
        )
    tables = tuple(
        ObservedDatabaseTableV1(
            schema=key[0],
            name=key[1],
            relation_kind=(
                DatabaseRelationKind.PARTITIONED_TABLE
                if key[2] == "p"
                else DatabaseRelationKind.TABLE
            ),
            columns=tuple(columns),
        )
        for key, columns in sorted(grouped.items())
    )
    return PostgresTablesColumnsObservationV1(
        postgres_major=postgres_major,
        covered_schemas=covered,
        present_schemas=present,
        tables=tables,
        database_extent_complete=extent_complete,
    )


def _type_kind(typtype: str, type_element: int) -> PostgresTypeKind:
    if type_element:
        return PostgresTypeKind.ARRAY
    return {
        "b": PostgresTypeKind.BASE,
        "d": PostgresTypeKind.DOMAIN,
        "e": PostgresTypeKind.ENUM,
        "c": PostgresTypeKind.COMPOSITE,
        "r": PostgresTypeKind.RANGE,
        "m": PostgresTypeKind.MULTIRANGE,
    }[typtype]


def _generation(
    identity: str, generated: str, expression: str | None
) -> DatabaseColumnGeneration:
    if identity == "a":
        return DatabaseColumnGeneration.IDENTITY_ALWAYS
    if identity == "d":
        return DatabaseColumnGeneration.IDENTITY_BY_DEFAULT
    if generated:
        return DatabaseColumnGeneration.GENERATED_STORED
    if expression:
        return DatabaseColumnGeneration.DEFAULT
    return DatabaseColumnGeneration.NONE


def _column_document(column: DatabaseColumnContractV1) -> dict[str, Any]:
    return {
        "name": column.name,
        "ordinal": column.ordinal,
        "postgres_type": {
            "kind": column.postgres_type.kind.value,
            "schema": column.postgres_type.schema,
            "name": column.postgres_type.name,
            "formatted": column.postgres_type.formatted,
        },
        "nullable": column.nullable,
        "generation": column.generation.value,
        "expression": column.expression,
        "collation": (
            {"schema": column.collation.schema, "name": column.collation.name}
            if column.collation
            else None
        ),
    }


def _observed_table_document(table: ObservedDatabaseTableV1) -> dict[str, Any]:
    return {
        "schema": table.schema,
        "name": table.name,
        "relation_kind": table.relation_kind.value,
        "columns": [_column_document(column) for column in table.columns],
    }


def _parse_observed_table(item: Any) -> ObservedDatabaseTableV1:
    if not isinstance(item, dict) or set(item) != {
        "schema",
        "name",
        "relation_kind",
        "columns",
    }:
        raise ProductDatabaseCatalogError("observed table fields differ")
    if not isinstance(item["columns"], list):
        raise ProductDatabaseCatalogError("observed table.columns must be an array")
    columns = []
    for column in item["columns"]:
        if not isinstance(column, dict) or set(column) != {
            "name",
            "ordinal",
            "postgres_type",
            "nullable",
            "generation",
            "expression",
            "collation",
        }:
            raise ProductDatabaseCatalogError("observed column fields differ")
        pg_type = column["postgres_type"]
        collation = column["collation"]
        if not isinstance(pg_type, dict) or set(pg_type) != {
            "kind",
            "schema",
            "name",
            "formatted",
        }:
            raise ProductDatabaseCatalogError("observed postgres_type fields differ")
        if collation is not None and (
            not isinstance(collation, dict) or set(collation) != {"schema", "name"}
        ):
            raise ProductDatabaseCatalogError("observed collation fields differ")
        columns.append(
            DatabaseColumnContractV1(
                name=column["name"],
                ordinal=column["ordinal"],
                postgres_type=PostgresTypeContractV1(
                    PostgresTypeKind(pg_type["kind"]),
                    pg_type["schema"],
                    pg_type["name"],
                    pg_type["formatted"],
                ),
                nullable=column["nullable"],
                generation=DatabaseColumnGeneration(column["generation"]),
                expression=column["expression"],
                collation=(
                    PostgresCollationContractV1(collation["schema"], collation["name"])
                    if collation
                    else None
                ),
            )
        )
    return ObservedDatabaseTableV1(
        schema=item["schema"],
        name=item["name"],
        relation_kind=DatabaseRelationKind(item["relation_kind"]),
        columns=tuple(columns),
    )


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def compare_module_database_catalog(
    declaration: ModuleDatabaseCatalogSnapshot,
    observation: PostgresTablesColumnsObservationV1,
) -> DatabaseCatalogComparisonV1:
    return _compare(
        declaration_schemas=(declaration.database_schema,),
        declaration_tables=declaration.tables,
        postgres_major=None,
        observation=observation,
        require_database_extent=False,
        declaration_digest=declaration.digest,
        declaration_schema=declaration.schema,
        declaration_scope=DatabaseCatalogDeclarationScope.MODULE,
        product_code="",
        product_version="",
    )


def compare_product_database_catalog(
    declaration: ProductDatabaseCatalogSnapshot,
    observation: PostgresTablesColumnsObservationV1,
) -> DatabaseCatalogComparisonV1:
    return _compare(
        declaration_schemas=declaration.complete_schemas,
        declaration_tables=tuple(
            table for fragment in declaration.fragments for table in fragment.tables
        ),
        postgres_major=declaration.postgres_major,
        observation=observation,
        require_database_extent=True,
        declaration_digest=declaration.digest,
        declaration_schema=declaration.schema,
        declaration_scope=DatabaseCatalogDeclarationScope.PRODUCT,
        product_code=declaration.product_code,
        product_version=declaration.product_version,
    )


def _compare(
    *,
    declaration_schemas: tuple[str, ...],
    declaration_tables: tuple[Any, ...],
    postgres_major: int | None,
    observation: PostgresTablesColumnsObservationV1,
    require_database_extent: bool,
    declaration_digest: str,
    declaration_schema: str,
    declaration_scope: DatabaseCatalogDeclarationScope,
    product_code: str,
    product_version: str,
) -> DatabaseCatalogComparisonV1:
    if require_database_extent and not observation.database_extent_complete:
        raise ProductDatabaseCatalogError(
            "product comparison requires an observation of the complete "
            "non-system database schema extent"
        )
    if not set(declaration_schemas) <= set(observation.covered_schemas):
        raise ProductDatabaseCatalogError(
            "observation does not cover every declared schema"
        )
    drifts: list[DatabaseCatalogDriftV1] = []
    issues: list[str] = []
    if postgres_major is not None and postgres_major != observation.postgres_major:
        issues.append(
            f"declared PostgreSQL major {postgres_major} != observed "
            f"{observation.postgres_major}"
        )
    declared_schema_set = set(declaration_schemas)
    declared = {(table.schema, table.name): table for table in declaration_tables}
    observed = {table.coordinate: table for table in observation.tables}
    for coordinate in sorted(set(declared) - set(observed)):
        drifts.append(
            DatabaseCatalogDriftV1(
                DatabaseCatalogFactDimension.TABLE,
                DatabaseCatalogFactDirection.DECLARED_BUT_ABSENT,
                schema=coordinate[0],
                table=coordinate[1],
                column="",
                attribute=DatabaseCatalogFactAttribute.PRESENCE,
                declared=_json_value("present"),
                observed=_json_value("absent"),
            )
        )
    for coordinate in sorted(set(observed) - set(declared)):
        if coordinate[0] in declared_schema_set or require_database_extent:
            drifts.append(
                DatabaseCatalogDriftV1(
                    DatabaseCatalogFactDimension.TABLE,
                    DatabaseCatalogFactDirection.PRESENT_BUT_UNDECLARED,
                    schema=coordinate[0],
                    table=coordinate[1],
                    column="",
                    attribute=DatabaseCatalogFactAttribute.PRESENCE,
                    declared=_json_value("absent"),
                    observed=_json_value("present"),
                )
            )
    for coordinate in sorted(set(declared) & set(observed)):
        wanted = declared[coordinate]
        got = observed[coordinate]
        if wanted.relation_kind != got.relation_kind:
            drifts.extend(
                _changed_fact_pair(
                    dimension=DatabaseCatalogFactDimension.TABLE,
                    schema=coordinate[0],
                    table=coordinate[1],
                    column="",
                    attribute=DatabaseCatalogFactAttribute.RELATION_KIND,
                    declared=_json_value(wanted.relation_kind.value),
                    observed=_json_value(got.relation_kind.value),
                )
            )
        wanted_columns = {column.name: column for column in wanted.columns}
        got_columns = {column.name: column for column in got.columns}
        for name in sorted(set(wanted_columns) - set(got_columns)):
            drifts.append(
                _presence_fact(
                    DatabaseCatalogFactDirection.DECLARED_BUT_ABSENT,
                    coordinate,
                    name,
                )
            )
        for name in sorted(set(got_columns) - set(wanted_columns)):
            drifts.append(
                _presence_fact(
                    DatabaseCatalogFactDirection.PRESENT_BUT_UNDECLARED,
                    coordinate,
                    name,
                )
            )
        for name in sorted(set(wanted_columns) & set(got_columns)):
            for attribute, declared_value, observed_value in _column_facts(
                wanted_columns[name], got_columns[name]
            ):
                if declared_value != observed_value:
                    drifts.extend(
                        _changed_fact_pair(
                            dimension=DatabaseCatalogFactDimension.COLUMN,
                            schema=coordinate[0],
                            table=coordinate[1],
                            column=name,
                            attribute=attribute,
                            declared=declared_value,
                            observed=observed_value,
                        )
                    )
    drifts_tuple = tuple(sorted(drifts, key=_drift_sort_key))
    return DatabaseCatalogComparisonV1(
        drifts=drifts_tuple,
        declaration_digest=declaration_digest,
        observation_digest=observation.digest,
        postgres_major=observation.postgres_major,
        declaration_schema=declaration_schema,
        declaration_scope=declaration_scope,
        complete_schemas=declaration_schemas,
        product_code=product_code,
        product_version=product_version,
        measurement_issues=tuple(sorted(issues)),
        _factory_token=_COMPARISON_FACTORY_TOKEN,
    )


def _json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _presence_fact(
    direction: DatabaseCatalogFactDirection,
    coordinate: tuple[str, str],
    column: str,
) -> DatabaseCatalogDriftV1:
    declared, observed = (
        (_json_value("present"), _json_value("absent"))
        if direction is DatabaseCatalogFactDirection.DECLARED_BUT_ABSENT
        else (_json_value("absent"), _json_value("present"))
    )
    return DatabaseCatalogDriftV1(
        DatabaseCatalogFactDimension.COLUMN,
        direction,
        coordinate[0],
        coordinate[1],
        column,
        DatabaseCatalogFactAttribute.PRESENCE,
        declared,
        observed,
    )


def _changed_fact_pair(
    *,
    dimension: DatabaseCatalogFactDimension,
    schema: str,
    table: str,
    column: str,
    attribute: DatabaseCatalogFactAttribute,
    declared: str,
    observed: str,
) -> tuple[DatabaseCatalogDriftV1, DatabaseCatalogDriftV1]:
    def finding(direction: DatabaseCatalogFactDirection) -> DatabaseCatalogDriftV1:
        return DatabaseCatalogDriftV1(
            dimension,
            direction,
            schema,
            table,
            column,
            attribute,
            declared,
            observed,
        )

    return (
        finding(DatabaseCatalogFactDirection.DECLARED_BUT_ABSENT),
        finding(DatabaseCatalogFactDirection.PRESENT_BUT_UNDECLARED),
    )


def _column_facts(
    declared: DatabaseColumnContractV1,
    observed: DatabaseColumnContractV1,
) -> tuple[tuple[DatabaseCatalogFactAttribute, str, str], ...]:
    def pg_type(column: DatabaseColumnContractV1) -> str:
        value = column.postgres_type
        return _json_value(
            {
                "kind": value.kind.value,
                "schema": value.schema,
                "name": value.name,
                "formatted": value.formatted,
            }
        )

    def collation(column: DatabaseColumnContractV1) -> str:
        value = column.collation
        return _json_value(
            None if value is None else {"schema": value.schema, "name": value.name}
        )

    return (
        (
            DatabaseCatalogFactAttribute.ORDINAL,
            _json_value(declared.ordinal),
            _json_value(observed.ordinal),
        ),
        (
            DatabaseCatalogFactAttribute.POSTGRES_TYPE,
            pg_type(declared),
            pg_type(observed),
        ),
        (
            DatabaseCatalogFactAttribute.NULLABLE,
            _json_value(declared.nullable),
            _json_value(observed.nullable),
        ),
        (
            DatabaseCatalogFactAttribute.GENERATION,
            _json_value(declared.generation.value),
            _json_value(observed.generation.value),
        ),
        (
            DatabaseCatalogFactAttribute.EXPRESSION,
            _json_value(declared.expression),
            _json_value(observed.expression),
        ),
        (
            DatabaseCatalogFactAttribute.COLLATION,
            collation(declared),
            collation(observed),
        ),
    )


def _drift_sort_key(drift: DatabaseCatalogDriftV1) -> tuple[str, ...]:
    return (
        drift.dimension.value,
        drift.schema,
        drift.table,
        drift.column,
        drift.attribute.value,
        drift.direction.value,
        drift.declared,
        drift.observed,
    )


def verify_module_database_catalog(
    *,
    declaration_bytes: bytes,
    declaration_digest: str,
    observation_bytes: bytes,
    observation_digest: str,
) -> DatabaseCatalogComparisonV1:
    return compare_module_database_catalog(
        ModuleDatabaseCatalogSnapshot.from_json_bytes(
            declaration_bytes, expected_digest=declaration_digest
        ),
        PostgresTablesColumnsObservationV1.from_json_bytes(
            observation_bytes, expected_digest=observation_digest
        ),
    )


def verify_product_database_catalog(
    *,
    declaration_bytes: bytes,
    declaration_digest: str,
    observation_bytes: bytes,
    observation_digest: str,
) -> DatabaseCatalogComparisonV1:
    return compare_product_database_catalog(
        ProductDatabaseCatalogSnapshot.from_json_bytes(
            declaration_bytes, expected_digest=declaration_digest
        ),
        PostgresTablesColumnsObservationV1.from_json_bytes(
            observation_bytes, expected_digest=observation_digest
        ),
    )


__all__ = [
    "DATABASE_CATALOG_COMPARATOR_ID",
    "POSTGRES_TABLES_COLUMNS_OBSERVATION_SCHEMA",
    "DatabaseCatalogComparisonV1",
    "DatabaseCatalogDriftV1",
    "DatabaseCatalogDeclarationScope",
    "DatabaseCatalogFactAttribute",
    "DatabaseCatalogFactDimension",
    "DatabaseCatalogFactDirection",
    "ObservedDatabaseTableV1",
    "PostgresTablesColumnsObservationV1",
    "compare_module_database_catalog",
    "compare_product_database_catalog",
    "observe_postgres_tables_columns",
    "verify_module_database_catalog",
    "verify_product_database_catalog",
]
