"""Caller-owned CURRENT transfer of 23 Learning tables and 212 persisted fields.

V1/V2/V3 keep their seven/fourteen/twenty-one-table scopes. Full V4 preflight
precedes every INSERT; independent raw readback includes all keys and deletes.
This does not prove product completeness, writer fences, identity, ACK, incoming
references, grants, lineage, or compatible product reversal with current facts.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Text, bindparam, column, func, select, table
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.elements import BindParameter, ColumnElement
from sqlalchemy.sql.selectable import TableClause

from . import curriculum_transfer as _v2
from . import participation_transfer as _v3
from . import transfer as _v1
from .assessment_transfer_contracts import (
    ActivityAttemptRowV4,
    AssessmentStorageRowV4,
    AttemptGrantRowV4,
    ImportObservationV4,
    LearningSnapshotV4,
    SnapshotClosureErrorV4,
    VerificationObservationV4,
    _participation_slice,
)
from .transfer_contracts import (
    DestinationNotEmpty,
    JsonbText,
    RowCountObservation,
    RowMismatch,
    SnapshotMismatch,
    SnapshotValidationError,
    TransferConnectionError,
    UnsupportedTimestampRepresentation,
    validate_tenant_extent,
)


@dataclass(frozen=True, slots=True)
class _AssessmentTable:
    name: str
    row_type: type[AssessmentStorageRowV4]
    columns: tuple[_v1._Column, ...]

    @property
    def qualified(self) -> str:
        return f"{_v1._SCHEMA}.{self.name}"

    @property
    def clause(self) -> TableClause:
        return _RELATIONS[self.name]


_C = _v1._Column
_TABLES = (
    _AssessmentTable(
        "activity_attempts",
        ActivityAttemptRowV4,
        (
            *_v1._IDENTITY,
            _C("activity_id", "uuid"),
            _C("person_id", "uuid"),
            _C("question_ext_ids", "jsonb"),
            _C("started_at", "timestamp with time zone"),
            _C("submitted_at", "timestamp with time zone", True),
            *_v1._TIMESTAMPS,
        ),
    ),
    _AssessmentTable(
        "attempt_grants",
        AttemptGrantRowV4,
        (
            *_v1._IDENTITY,
            _C("activity_id", "uuid"),
            _C("person_id", "uuid"),
            _C("extra_attempts", "integer"),
            _C("reason", "text"),
            _C("granted_by", "uuid", True),
            *_v1._TIMESTAMPS,
        ),
    ),
)
_RELATIONS = {
    contract.name: table(
        contract.name,
        *(column(spec.name) for spec in contract.columns),
        schema=_v1._SCHEMA,
    )
    for contract in _TABLES
}
_ALL_TABLES = (*_v1._TABLES, *_v2._TABLES, *_v3._TABLES, *_TABLES)


def _validate(snapshot: LearningSnapshotV4) -> None:
    if type(snapshot) is not LearningSnapshotV4:
        raise SnapshotValidationError(
            "version-four twenty-three-table snapshot required"
        )
    snapshot.__post_init__()


def _qualify(scope: _v1._Scope, tenant_ids: tuple[UUID, ...]) -> None:
    _v3._qualify(scope, tenant_ids)
    for contract in _TABLES:
        qualified = scope.execute(
            "SELECT pg_catalog.has_table_privilege(current_user, :relation, 'SELECT') "
            "AND NOT pg_catalog.row_security_active(CAST(:relation AS regclass)) "
            "AND (SELECT relkind = 'r' FROM pg_catalog.pg_class "
            "WHERE oid = CAST(:relation AS regclass))",
            {"relation": contract.qualified},
        ).scalar_one()
        if qualified is not True:
            raise TransferConnectionError(
                f"whole-table visibility required: {contract.qualified}"
            )
        actual = {
            str(row[0]): (str(row[1]), not bool(row[2]))
            for row in scope.execute(
                "SELECT attname, pg_catalog.format_type(atttypid, atttypmod), "
                "attnotnull "
                "FROM pg_catalog.pg_attribute "
                "WHERE attrelid = CAST(:relation AS regclass) "
                "AND attnum > 0 AND NOT attisdropped",
                {"relation": contract.qualified},
            )
        }
        expected = {
            spec.name: (spec.pg_type, spec.nullable) for spec in contract.columns
        }
        if actual != expected:
            raise TransferConnectionError(
                f"canonical column contract mismatch: {contract.qualified}"
            )
        scope.execute(select(*contract.clause.c).limit(0))


def _require_empty(scope: _v1._Scope) -> None:
    for contract in _ALL_TABLES:
        if scope.execute(
            select(select(1).select_from(contract.clause).exists())
        ).scalar_one():
            raise DestinationNotEmpty(
                f"twenty-three-table destination is not empty: {contract.name}"
            )


def _preflight(scope: _v1._Scope, snapshot: LearningSnapshotV4) -> None:
    """Hide data-representation payloads without repairing caller transactions."""
    try:
        _preflight_values(scope, snapshot)
    except DBAPIError as error:
        # Psycopg 3 exposes sqlstate; Psycopg 2 exposes the same PostgreSQL
        # diagnostic as pgcode. Only class 22 qualifies, never arbitrary SQL
        # failures (permission, timeout, connection loss or required writes).
        state = getattr(error.orig, "sqlstate", None)
        if state is None:
            state = getattr(error.orig, "pgcode", None)
        if isinstance(state, str) and state.startswith("22"):
            raise SnapshotValidationError(
                "unsupported storage representation"
            ) from None
        raise
    except (UnicodeError, OverflowError):
        raise SnapshotValidationError("unsupported storage representation") from None


def _reference(present: bool, table_name: str, key: UUID, column_name: str) -> None:
    if not present:
        raise SnapshotClosureErrorV4(table_name, key, column_name)


def _read_rows(
    scope: _v1._Scope, contract: _AssessmentTable, tenant_ids: tuple[UUID, ...]
) -> tuple[AssessmentStorageRowV4, ...]:
    projection: list[ColumnElement[object]] = []
    for spec in contract.columns:
        stored = contract.clause.c[spec.name]
        expression: ColumnElement[object] | ColumnElement[str]
        if spec.pg_type == "jsonb":
            json_text: ColumnElement[str] = sql_cast(stored, Text)
            expression = json_text
        elif spec.pg_type == "timestamp with time zone":
            expression = func.pg_catalog.encode(
                func.pg_catalog.timestamptz_send(stored), "hex"
            )
        else:
            expression = stored
        projection.append(cast(ColumnElement[object], expression.label(spec.name)))
    result = scope.extent_query(
        select(*projection)
        .where(contract.clause.c.tenant_id.in_(bindparam("tenant_ids", expanding=True)))
        .order_by(contract.clause.c.id),
        tenant_ids,
    )
    rows: list[AssessmentStorageRowV4] = []
    for record in result.mappings():
        values = dict(record)
        for spec in contract.columns:
            if spec.pg_type == "jsonb":
                values[spec.name] = JsonbText(values[spec.name])
            elif spec.pg_type == "timestamp with time zone":
                values[spec.name] = _v1._timestamp_from_bits(
                    values[spec.name], contract.name, values["id"], spec.name
                )
        rows.append(contract.row_type(**values))
    scope.check()
    return tuple(rows)


def _verify(
    scope: _v1._Scope, snapshot: LearningSnapshotV4
) -> VerificationObservationV4:
    mismatches: list[RowMismatch] = []
    counts: list[RowCountObservation] = []
    try:
        first = _v3._verify(scope, _participation_slice(snapshot))
        counts.extend(first.row_counts)
    except SnapshotMismatch as error:
        # Still inspect the new tables when older current facts disagree.
        # A failed observation has no row-count success contract to return.
        mismatches.extend(error.mismatches)
    for contract in _TABLES:
        expected = {row.id: row for row in getattr(snapshot, contract.name)}
        # Independent projection and raw readback; do not consume exporter
        # objects, insert bind dictionaries, RETURNING rows, counts or hashes.
        relation = contract.clause
        projection: list[ColumnElement[object]] = []
        for spec in contract.columns:
            value = relation.c[spec.name]
            observed: ColumnElement[object] | ColumnElement[str]
            if spec.pg_type == "jsonb":
                json_text: ColumnElement[str] = sql_cast(value, Text)
                observed = json_text
            elif spec.pg_type == "timestamp with time zone":
                observed = func.pg_catalog.encode(
                    func.pg_catalog.timestamptz_send(value), "hex"
                )
            else:
                observed = value
            projection.append(cast(ColumnElement[object], observed.label(spec.name)))
        actual = {
            cast(UUID, row["id"]): row
            for row in scope.extent_query(
                select(*projection)
                .where(
                    relation.c.tenant_id.in_(bindparam("tenant_ids", expanding=True))
                )
                .order_by(relation.c.id),
                snapshot.tenant_ids,
            ).mappings()
        }
        for key in sorted(expected.keys() - actual.keys(), key=str):
            mismatches.append(RowMismatch(contract.name, key, None, "missing"))
        for key in sorted(actual.keys() - expected.keys(), key=str):
            mismatches.append(RowMismatch(contract.name, key, None, "extra"))
        for key in sorted(expected.keys() & actual.keys(), key=str):
            for spec in contract.columns:
                if spec.pg_type == "timestamp with time zone":
                    _v1._timestamp_from_bits(
                        actual[key][spec.name], contract.name, key, spec.name
                    )
                value = getattr(expected[key], spec.name)
                wanted = (
                    str(value)
                    if isinstance(value, JsonbText)
                    else _v1._timestamp_bits(value, contract.name, key, spec.name)
                    if isinstance(value, datetime)
                    else value
                )
                if actual[key][spec.name] != wanted:
                    mismatches.append(
                        RowMismatch(contract.name, key, spec.name, "value")
                    )
        outside = scope.extent_query(
            select(relation.c.id)
            .where(relation.c.tenant_id.not_in(bindparam("tenant_ids", expanding=True)))
            .order_by(relation.c.id),
            snapshot.tenant_ids,
        ).scalars()
        mismatches.extend(
            RowMismatch(contract.name, cast(UUID, key), None, "outside_extent")
            for key in outside
        )
        counts.append(RowCountObservation(contract.name, len(actual)))
    scope.check()
    if mismatches:
        raise SnapshotMismatch(tuple(mismatches))
    return VerificationObservationV4(4, snapshot.tenant_ids, tuple(counts))


def verify_snapshot_v4(
    connection: Connection, snapshot: LearningSnapshotV4
) -> VerificationObservationV4:
    """Independently compare all 212 fields, both key directions and outside rows."""
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    return _verify(scope, snapshot)


def _preflight_values(scope: _v1._Scope, snapshot: LearningSnapshotV4) -> None:
    # ALL old physical scalar/JSON/float/time checks finish before delegated V3
    # writes, as do the new checks below. No domain normalization is performed.
    _v3._preflight(scope, _participation_slice(snapshot))
    checked_json: set[JsonbText] = set()
    checked_timestamps: set[str] = set()
    for contract in _TABLES:
        for row in getattr(snapshot, contract.name):
            for spec in contract.columns:
                value = getattr(row, spec.name)
                if spec.pg_type == "integer":
                    if not -(2**31) <= value <= 2**31 - 1:
                        raise SnapshotValidationError(
                            "unsupported storage representation"
                        ) from None
                elif isinstance(value, str):
                    value.encode("utf-8")
                    if "\x00" in value:
                        raise SnapshotValidationError(
                            "unsupported storage representation"
                        ) from None
                    if isinstance(value, JsonbText) and value not in checked_json:
                        observed = scope.execute(
                            "SELECT CAST(CAST(:value AS jsonb) AS text)",
                            {"value": str(value)},
                        ).scalar_one()
                        if observed != str(value):
                            raise SnapshotValidationError(
                                "noncanonical JSONB storage text: "
                                f"{contract.name}/{row.id}/{spec.name}"
                            )
                        checked_json.add(value)
                elif isinstance(value, datetime):
                    expected = _v1._timestamp_bits(
                        value, contract.name, row.id, spec.name
                    )
                    if expected not in checked_timestamps:
                        observed = scope.execute(
                            "SELECT pg_catalog.encode(pg_catalog.timestamptz_send("
                            "CAST(:value AS timestamp with time zone)), 'hex')",
                            {"value": value},
                        ).scalar_one()
                        if observed != expected:
                            raise UnsupportedTimestampRepresentation(
                                "unsupported timestamp: "
                                f"{contract.name}/{row.id}/{spec.name}"
                            )
                        checked_timestamps.add(expected)


def _closure(scope: _v1._Scope, snapshot: LearningSnapshotV4) -> None:
    _v3._closure(scope, _participation_slice(snapshot))
    activities = {(row.tenant_id, row.id) for row in snapshot.activities}
    # Tenant roots have already been verified for the entire declared extent by
    # qualification. person_id and granted_by have no source FK; do not invent one.
    for contract in _TABLES:
        for row in getattr(snapshot, contract.name):
            _reference(
                (row.tenant_id, row.activity_id) in activities,
                contract.name,
                row.id,
                "activity_id",
            )


def export_snapshot_v4(
    connection: Connection, tenant_ids: tuple[UUID, ...]
) -> LearningSnapshotV4:
    """Export CURRENT selected rows, without claiming the product extent is complete."""
    validate_tenant_extent(tenant_ids)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, tenant_ids)
    base = _v3.export_snapshot_v3(connection, tenant_ids)
    rows = {
        contract.name: _read_rows(scope, contract, tenant_ids) for contract in _TABLES
    }
    snapshot = LearningSnapshotV4(
        version=4,
        tenant_ids=tenant_ids,
        courses=base.courses,
        activities=base.activities,
        submissions=base.submissions,
        scores=base.scores,
        course_completions=base.course_completions,
        certificates=base.certificates,
        learning_events=base.learning_events,
        chapters=base.chapters,
        question_banks=base.question_banks,
        questions=base.questions,
        chapter_reads=base.chapter_reads,
        subtopic_reads=base.subtopic_reads,
        chapter_bookmarks=base.chapter_bookmarks,
        chapter_notes=base.chapter_notes,
        cohorts=base.cohorts,
        enrollments=base.enrollments,
        tracks=base.tracks,
        track_courses=base.track_courses,
        cohort_tracks=base.cohort_tracks,
        course_offerings=base.course_offerings,
        course_prerequisites=base.course_prerequisites,
        activity_attempts=cast(
            tuple[ActivityAttemptRowV4, ...], rows["activity_attempts"]
        ),
        attempt_grants=cast(tuple[AttemptGrantRowV4, ...], rows["attempt_grants"]),
    )
    scope.check()
    return snapshot


def import_snapshot_v4(
    connection: Connection, snapshot: LearningSnapshotV4
) -> ImportObservationV4:
    """Restore ALL-tenant-empty 23-table storage in the exact caller transaction.

    No outcomes, retries or savepoints. Required writes propagate for caller
    rollback; full representation/FK preflight precedes V3's first INSERT.
    """
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _require_empty(scope)
    _preflight(scope, snapshot)
    _closure(scope, snapshot)
    scope.check()
    base = _v3.import_snapshot_v3(connection, _participation_slice(snapshot))
    scope.check()
    counts = list(base.inserted_rows)
    for contract in _TABLES:
        bindings: dict[str, ColumnElement[object]] = {}
        for spec in contract.columns:
            # Unreflected Core columns/binds use NullType: raw text reaches SQL
            # CAST JSONB without any Python JSON serializer changing its kind.
            bound: BindParameter[object] = bindparam(spec.name)
            bindings[spec.name] = cast(
                ColumnElement[object],
                sql_cast(bound, JSONB) if spec.pg_type == "jsonb" else bound,
            )
        statement = contract.clause.insert().values(bindings)
        rows = getattr(snapshot, contract.name)
        for row in rows:
            parameters: dict[str, object] = {}
            for field in fields(row):
                value = getattr(row, field.name)
                parameters[field.name] = (
                    str(value) if isinstance(value, JsonbText) else value
                )
            scope.execute(statement, parameters)
        counts.append(RowCountObservation(contract.name, len(rows)))
    verification = _verify(scope, snapshot)
    scope.check()
    return ImportObservationV4(4, verification, tuple(counts))


__all__ = ["export_snapshot_v4", "import_snapshot_v4", "verify_snapshot_v4"]
