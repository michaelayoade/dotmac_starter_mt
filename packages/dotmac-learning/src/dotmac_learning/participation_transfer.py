"""Caller-owned CURRENT transfer of all twenty-one Learning storage tables.

V1 and V2 keep their scoped contracts. V3 validates the full representation and
actual FK closure before invoking the V2 writer, then independently reads all
194 fields. Observations do not establish source completeness, writer fences,
Academy identity mapping, external references, ACK or compatible reverse cutover.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, fields
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import bindparam, column, func, select, table
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.elements import BindParameter, ColumnElement
from sqlalchemy.sql.selectable import TableClause

from . import curriculum_transfer as _v2
from . import transfer as _v1
from .participation_transfer_contracts import (
    CohortRowV3,
    CohortTrackRowV3,
    CourseOfferingRowV3,
    CoursePrerequisiteRowV3,
    EnrollmentRowV3,
    ImportObservationV3,
    LearningSnapshotV3,
    ParticipationStorageRowV3,
    SnapshotClosureErrorV3,
    TrackCourseRowV3,
    TrackRowV3,
    VerificationObservationV3,
    _curriculum_slice,
)
from .transfer_contracts import (
    DestinationNotEmpty,
    Float8Bits,
    RowCountObservation,
    RowMismatch,
    SnapshotMismatch,
    SnapshotValidationError,
    TransferConnectionError,
    UnsupportedFloatRepresentation,
    UnsupportedTimestampRepresentation,
    validate_tenant_extent,
)


@dataclass(frozen=True, slots=True)
class _ParticipationTable:
    name: str
    row_type: type[ParticipationStorageRowV3]
    columns: tuple[_v1._Column, ...]

    @property
    def qualified(self) -> str:
        return f"{_v1._SCHEMA}.{self.name}"

    @property
    def clause(self) -> TableClause:
        return _RELATIONS[self.name]


# Closed source-derived columns, in FK-topological insert order. entrance_bank_id
# is a logical UUID; nullable track uses the actual composite MATCH SIMPLE FK.
_C = _v1._Column
_TABLES = (
    _ParticipationTable(
        "cohorts",
        CohortRowV3,
        (
            *_v1._IDENTITY,
            _C("name", "character varying(120)"),
            _C("discipline", "character varying(40)"),
            _C("status", "character varying(20)"),
            _C("delivery_mode", "character varying(20)"),
            _C("entrance_bank_id", "uuid", True),
            _C("entrance_time_limit_minutes", "integer", True),
            _C("auto_accept_threshold", "double precision", True),
            *_v1._TIMESTAMPS,
        ),
    ),
    _ParticipationTable(
        "tracks",
        TrackRowV3,
        (
            *_v1._IDENTITY,
            _C("slug", "character varying(80)"),
            _C("name", "character varying(160)"),
            _C("status", "character varying(20)"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _ParticipationTable(
        "cohort_tracks",
        CohortTrackRowV3,
        (
            *_v1._IDENTITY,
            _C("cohort_id", "uuid"),
            _C("track_id", "uuid"),
            _C("status", "character varying(20)"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _ParticipationTable(
        "track_courses",
        TrackCourseRowV3,
        (
            *_v1._IDENTITY,
            _C("track_id", "uuid"),
            _C("course_id", "uuid"),
            _C("order_index", "integer"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _ParticipationTable(
        "enrollments",
        EnrollmentRowV3,
        (
            *_v1._IDENTITY,
            _C("cohort_id", "uuid"),
            _C("person_id", "uuid"),
            _C("track_id", "uuid", True),
            _C("role_in_cohort", "character varying(20)"),
            _C("status", "character varying(20)"),
            _C("audience", "character varying(20)", True),
            _C("employee_ref", "character varying(64)", True),
            _C("access_ends_at", "timestamp with time zone", True),
            *_v1._TIMESTAMPS,
        ),
    ),
    _ParticipationTable(
        "course_offerings",
        CourseOfferingRowV3,
        (
            *_v1._IDENTITY,
            _C("cohort_id", "uuid"),
            _C("course_id", "uuid"),
            _C("status", "character varying(20)"),
            _C("starts_at", "timestamp with time zone", True),
            _C("ends_at", "timestamp with time zone", True),
            *_v1._TIMESTAMPS,
        ),
    ),
    _ParticipationTable(
        "course_prerequisites",
        CoursePrerequisiteRowV3,
        (
            *_v1._IDENTITY,
            _C("course_id", "uuid"),
            _C("requires_course_id", "uuid"),
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


def _validate(snapshot: LearningSnapshotV3) -> None:
    if type(snapshot) is not LearningSnapshotV3:
        raise SnapshotValidationError(
            "version-three twenty-one-table snapshot required"
        )
    snapshot.__post_init__()


def _qualify(scope: _v1._Scope, tenant_ids: tuple[UUID, ...]) -> None:
    _v2._qualify(scope, tenant_ids)
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
        if not _v1._column_contract_matches(contract.name, actual, expected):
            raise TransferConnectionError(
                f"canonical column contract mismatch: {contract.qualified}"
            )
        scope.execute(select(*contract.clause.c).limit(0))


def _require_empty(scope: _v1._Scope) -> None:
    for contract in (*_v1._TABLES, *_v2._TABLES, *_TABLES):
        if scope.execute(
            select(select(1).select_from(contract.clause).exists())
        ).scalar_one():
            raise DestinationNotEmpty(
                f"twenty-one-table destination is not empty: {contract.name}"
            )


def _preflight_values(scope: _v1._Scope, snapshot: LearningSnapshotV3) -> None:
    # V3 checks physical scalar representations in every slice before any
    # delegated write. Bounds/capacity preserve INTEGER and VARCHAR storage;
    # no domain checks, normalization or truncation are introduced here.
    # JsonbText remains under the existing PostgreSQL JSONB representation probe.
    for contract in (*_v1._TABLES, *_v2._TABLES, *_TABLES):
        for row in getattr(snapshot, contract.name):
            for spec in contract.columns:
                value = getattr(row, spec.name)
                if spec.pg_type == "integer" and value is not None:
                    if not -(2**31) <= value <= 2**31 - 1:
                        raise SnapshotValidationError(
                            "unsupported storage representation"
                        ) from None
                elif type(value) is str:
                    value.encode("utf-8")
                    if "\x00" in value:
                        raise SnapshotValidationError(
                            "unsupported storage representation"
                        ) from None
                    if spec.pg_type.startswith("character varying("):
                        capacity = int(
                            spec.pg_type.removeprefix(
                                "character varying("
                            ).removesuffix(")")
                        )
                        if len(value) > capacity:
                            raise SnapshotValidationError(
                                "unsupported storage representation"
                            ) from None
    _v2._preflight_values(scope, _curriculum_slice(snapshot))
    checked_floats: set[Float8Bits] = set()
    checked_timestamps: set[str] = set()
    for contract in _TABLES:
        for row in getattr(snapshot, contract.name):
            for spec in contract.columns:
                value = getattr(row, spec.name)
                if isinstance(value, Float8Bits) and value not in checked_floats:
                    native = struct.unpack("!d", bytes.fromhex(value.hex))[0]
                    observed = scope.execute(
                        "SELECT pg_catalog.encode(pg_catalog.float8send("
                        "CAST(:value AS double precision)), 'hex')",
                        {"value": native},
                    ).scalar_one()
                    if observed != value.hex:
                        raise UnsupportedFloatRepresentation(
                            "unsupported float8 representation: "
                            f"{contract.name}/{row.id}/{spec.name}"
                        )
                    checked_floats.add(value)
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


def _preflight(scope: _v1._Scope, snapshot: LearningSnapshotV3) -> None:
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
        raise SnapshotClosureErrorV3(table_name, key, column_name)


def _closure(scope: _v1._Scope, snapshot: LearningSnapshotV3) -> None:
    _v2._closure(scope, _curriculum_slice(snapshot))
    courses = {(row.tenant_id, row.id) for row in snapshot.courses}
    cohorts = {(row.tenant_id, row.id) for row in snapshot.cohorts}
    tracks = {(row.tenant_id, row.id) for row in snapshot.tracks}
    cohort_tracks = {
        (row.tenant_id, row.cohort_id, row.track_id) for row in snapshot.cohort_tracks
    }
    for name, references in (
        ("cohort_tracks", (("cohort_id", cohorts), ("track_id", tracks))),
        ("track_courses", (("track_id", tracks), ("course_id", courses))),
        ("enrollments", (("cohort_id", cohorts),)),
        ("course_offerings", (("cohort_id", cohorts), ("course_id", courses))),
        (
            "course_prerequisites",
            (("course_id", courses), ("requires_course_id", courses)),
        ),
    ):
        for row in getattr(snapshot, name):
            for column_name, parents in references:
                _reference(
                    (row.tenant_id, getattr(row, column_name)) in parents,
                    name,
                    row.id,
                    column_name,
                )
    for row in snapshot.enrollments:
        if row.track_id is not None:
            _reference(
                (row.tenant_id, row.cohort_id, row.track_id) in cohort_tracks,
                "enrollments",
                row.id,
                "track_id",
            )
    person_ids = tuple(sorted({row.person_id for row in snapshot.enrollments}, key=str))
    if person_ids:
        parties = {
            (row["tenant_id"], row["id"])
            for row in scope.execute(
                select(_v2._PARTIES.c.tenant_id, _v2._PARTIES.c.id).where(
                    _v2._PARTIES.c.id.in_(bindparam("person_ids", expanding=True))
                ),
                {"person_ids": person_ids},
            ).mappings()
        }
        persons = set(
            scope.execute(
                select(_v2._PERSONS.c.party_id).where(
                    _v2._PERSONS.c.party_id.in_(bindparam("person_ids", expanding=True))
                ),
                {"person_ids": person_ids},
            ).scalars()
        )
        for row in snapshot.enrollments:
            _reference(
                (row.tenant_id, row.person_id) in parties and row.person_id in persons,
                "enrollments",
                row.id,
                "person_id",
            )


def _read_rows(
    scope: _v1._Scope, contract: _ParticipationTable, tenant_ids: tuple[UUID, ...]
) -> tuple[ParticipationStorageRowV3, ...]:
    projection: list[ColumnElement[object]] = []
    for spec in contract.columns:
        stored = contract.clause.c[spec.name]
        expression: ColumnElement[object]
        if spec.pg_type == "double precision":
            expression = func.pg_catalog.encode(
                func.pg_catalog.float8send(stored), "hex"
            )
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
    rows: list[ParticipationStorageRowV3] = []
    for record in result.mappings():
        values = dict(record)
        for spec in contract.columns:
            if spec.pg_type == "double precision" and values[spec.name] is not None:
                values[spec.name] = Float8Bits(values[spec.name])
            elif spec.pg_type == "timestamp with time zone":
                values[spec.name] = _v1._timestamp_from_bits(
                    values[spec.name], contract.name, values["id"], spec.name
                )
        rows.append(contract.row_type(**values))
    scope.check()
    return tuple(rows)


def export_snapshot_v3(
    connection: Connection, tenant_ids: tuple[UUID, ...]
) -> LearningSnapshotV3:
    """Export all CURRENT rows in the selected extent; no source-completeness claim."""
    validate_tenant_extent(tenant_ids)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, tenant_ids)
    _v1._refuse_non_default_prerequisite_policy(
        scope, tenant_ids, include_participation=True, include_curriculum=True
    )
    base = _v2.export_snapshot_v2(connection, tenant_ids)
    rows = {
        contract.name: _read_rows(scope, contract, tenant_ids) for contract in _TABLES
    }
    snapshot = LearningSnapshotV3(
        version=3,
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
        cohorts=cast(tuple[CohortRowV3, ...], rows["cohorts"]),
        tracks=cast(tuple[TrackRowV3, ...], rows["tracks"]),
        cohort_tracks=cast(tuple[CohortTrackRowV3, ...], rows["cohort_tracks"]),
        track_courses=cast(tuple[TrackCourseRowV3, ...], rows["track_courses"]),
        enrollments=cast(tuple[EnrollmentRowV3, ...], rows["enrollments"]),
        course_offerings=cast(
            tuple[CourseOfferingRowV3, ...], rows["course_offerings"]
        ),
        course_prerequisites=cast(
            tuple[CoursePrerequisiteRowV3, ...], rows["course_prerequisites"]
        ),
    )
    scope.check()
    return snapshot


def _verify(
    scope: _v1._Scope, snapshot: LearningSnapshotV3
) -> VerificationObservationV3:
    first = _v2._verify(scope, _curriculum_slice(snapshot))
    counts = list(first.row_counts)
    mismatches: list[RowMismatch] = []
    for contract in _TABLES:
        expected = {row.id: row for row in getattr(snapshot, contract.name)}
        # Independent projection and raw readback; do not consume exporter
        # objects, insert bind dictionaries, RETURNING rows, counts or hashes.
        relation = contract.clause
        projection: list[ColumnElement[object]] = []
        for spec in contract.columns:
            value = relation.c[spec.name]
            observed: ColumnElement[object]
            if spec.pg_type == "double precision":
                observed = func.pg_catalog.encode(
                    func.pg_catalog.float8send(value), "hex"
                )
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
                    value.hex
                    if isinstance(value, Float8Bits)
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
    return VerificationObservationV3(3, snapshot.tenant_ids, tuple(counts))


def verify_snapshot_v3(
    connection: Connection, snapshot: LearningSnapshotV3
) -> VerificationObservationV3:
    """Independently compare all 194 fields, both key directions and outside rows."""
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _v1._refuse_non_default_prerequisite_policy(
        scope, snapshot.tenant_ids, include_participation=True, include_curriculum=True
    )
    return _verify(scope, snapshot)


def import_snapshot_v3(
    connection: Connection, snapshot: LearningSnapshotV3
) -> ImportObservationV3:
    """Restore an ALL-tenant-empty 21-table store in the caller's active root.

    All representations and actual FKs are preflighted before V2's first INSERT.
    Required SQL errors propagate; only the caller completes or rolls back work.
    """
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _v1._refuse_non_default_prerequisite_policy(
        scope, snapshot.tenant_ids, include_participation=True, include_curriculum=True
    )
    _require_empty(scope)
    _preflight(scope, snapshot)
    _closure(scope, snapshot)
    scope.check()
    base = _v2.import_snapshot_v2(connection, _curriculum_slice(snapshot))
    scope.check()
    counts = list(base.inserted_rows)
    for contract in _TABLES:
        bindings: dict[str, ColumnElement[object]] = {}
        for spec in contract.columns:
            bound: BindParameter[object] = bindparam(spec.name)
            bindings[spec.name] = cast(
                ColumnElement[object],
                sql_cast(bound, DOUBLE_PRECISION)
                if spec.pg_type == "double precision"
                else bound,
            )
        statement = contract.clause.insert().values(bindings)
        rows = getattr(snapshot, contract.name)
        for row in rows:
            parameters: dict[str, object] = {}
            for field in fields(row):
                value = getattr(row, field.name)
                parameters[field.name] = (
                    struct.unpack("!d", bytes.fromhex(value.hex))[0]
                    if isinstance(value, Float8Bits)
                    else value
                )
            scope.execute(statement, parameters)
        counts.append(RowCountObservation(contract.name, len(rows)))
    verification = _verify(scope, snapshot)
    scope.check()
    return ImportObservationV3(3, verification, tuple(counts))


__all__ = ["export_snapshot_v3", "import_snapshot_v3", "verify_snapshot_v3"]
