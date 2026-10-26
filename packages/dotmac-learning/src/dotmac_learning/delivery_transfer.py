"""Caller-owned CURRENT transfer of 27 Learning tables and 260 persisted fields.

V1/V2/V3/V4 retain their original scopes. Full V5 preflight precedes every
INSERT; independent raw readback includes all keys and deletes.
This does not prove product completeness, writer fences, identity, ACK, incoming
references, grants, lineage, or compatible product reversal with current facts.
"""

from __future__ import annotations

import struct
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

from . import assessment_transfer as _v4
from . import curriculum_transfer as _v2
from . import transfer as _v1
from .delivery_transfer_contracts import (
    AnnouncementRowV5,
    ClassSessionRowV5,
    DeliveryStorageRowV5,
    ImportObservationV5,
    LearningSnapshotV5,
    OfferingActivityRowV5,
    SnapshotClosureErrorV5,
    SuccessQueueEntryRowV5,
    VerificationObservationV5,
    _assessment_slice,
)
from .transfer_contracts import (
    DestinationNotEmpty,
    Float8Bits,
    JsonbText,
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
class _DeliveryTable:
    name: str
    row_type: type[DeliveryStorageRowV5]
    columns: tuple[_v1._Column, ...]

    @property
    def qualified(self) -> str:
        return f"{_v1._SCHEMA}.{self.name}"

    @property
    def clause(self) -> TableClause:
        return _RELATIONS[self.name]


_C = _v1._Column
_TABLES = (
    _DeliveryTable(
        "offering_activities",
        OfferingActivityRowV5,
        (
            *_v1._IDENTITY,
            _C("offering_id", "uuid"),
            _C("activity_id", "uuid"),
            _C("release_at", "timestamp with time zone", True),
            _C("due_at", "timestamp with time zone", True),
            *_v1._TIMESTAMPS,
        ),
    ),
    _DeliveryTable(
        "class_sessions",
        ClassSessionRowV5,
        (
            *_v1._IDENTITY,
            _C("cohort_id", "uuid"),
            _C("offering_id", "uuid", True),
            _C("session_type", "character varying(20)"),
            _C("title", "character varying(200)"),
            _C("starts_at", "timestamp with time zone"),
            _C("ends_at", "timestamp with time zone", True),
            _C("instructor_person_id", "uuid", True),
            _C("location", "character varying(160)", True),
            _C("join_url", "character varying(500)", True),
            _C("notes", "text", True),
            _C("status", "character varying(20)"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _DeliveryTable(
        "announcements",
        AnnouncementRowV5,
        (
            *_v1._IDENTITY,
            _C("cohort_id", "uuid", True),
            _C("author_person_id", "uuid"),
            _C("title", "character varying(200)"),
            _C("body_md", "text"),
            _C("body_html", "text"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _DeliveryTable(
        "success_queue_entries",
        SuccessQueueEntryRowV5,
        (
            *_v1._IDENTITY,
            _C("person_id", "uuid"),
            _C("cohort_id", "uuid", True),
            _C("reason_kind", "character varying(30)"),
            _C("supporting_facts", "jsonb"),
            _C("severity", "character varying(10)"),
            _C("detected_at", "timestamp with time zone"),
            _C("assigned_to", "uuid", True),
            _C("recommended_action", "text"),
            _C("status", "character varying(15)"),
            _C("resolved_at", "timestamp with time zone", True),
            _C("resolved_by", "uuid", True),
            _C("score_hint", "double precision"),
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
_ALL_TABLES = (*_v4._ALL_TABLES, *_TABLES)


def _validate(snapshot: LearningSnapshotV5) -> None:
    if type(snapshot) is not LearningSnapshotV5:
        raise SnapshotValidationError(
            "version-five twenty-seven-table snapshot required"
        )
    snapshot.__post_init__()


def _qualify(scope: _v1._Scope, tenant_ids: tuple[UUID, ...]) -> None:
    _v4._qualify(scope, tenant_ids)
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
    for contract in _ALL_TABLES:
        if scope.execute(
            select(select(1).select_from(contract.clause).exists())
        ).scalar_one():
            raise DestinationNotEmpty(
                f"twenty-seven-table destination is not empty: {contract.name}"
            )


def _preflight(scope: _v1._Scope, snapshot: LearningSnapshotV5) -> None:
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
        raise SnapshotClosureErrorV5(table_name, key, column_name)


def _read_rows(
    scope: _v1._Scope, contract: _DeliveryTable, tenant_ids: tuple[UUID, ...]
) -> tuple[DeliveryStorageRowV5, ...]:
    projection: list[ColumnElement[object]] = []
    for spec in contract.columns:
        stored = contract.clause.c[spec.name]
        expression: ColumnElement[object] | ColumnElement[str]
        if spec.pg_type == "double precision":
            expression = func.pg_catalog.encode(
                func.pg_catalog.float8send(stored), "hex"
            )
        elif spec.pg_type == "jsonb":
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
    rows: list[DeliveryStorageRowV5] = []
    for record in result.mappings():
        values = dict(record)
        for spec in contract.columns:
            if spec.pg_type == "double precision":
                values[spec.name] = Float8Bits(values[spec.name])
            elif spec.pg_type == "jsonb":
                values[spec.name] = JsonbText(values[spec.name])
            elif spec.pg_type == "timestamp with time zone":
                values[spec.name] = _v1._timestamp_from_bits(
                    values[spec.name], contract.name, values["id"], spec.name
                )
        rows.append(contract.row_type(**values))
    scope.check()
    return tuple(rows)


def _verify(
    scope: _v1._Scope, snapshot: LearningSnapshotV5
) -> VerificationObservationV5:
    mismatches: list[RowMismatch] = []
    counts: list[RowCountObservation] = []
    try:
        first = _v4._verify(scope, _assessment_slice(snapshot))
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
            if spec.pg_type == "double precision":
                observed = func.pg_catalog.encode(
                    func.pg_catalog.float8send(value), "hex"
                )
            elif spec.pg_type == "jsonb":
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
                    value.hex
                    if isinstance(value, Float8Bits)
                    else str(value)
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
    return VerificationObservationV5(5, snapshot.tenant_ids, tuple(counts))


def verify_snapshot_v5(
    connection: Connection, snapshot: LearningSnapshotV5
) -> VerificationObservationV5:
    """Independently compare all 260 fields, both key directions and outside rows."""
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _v1._refuse_non_default_prerequisite_policy(
        scope, snapshot.tenant_ids, include_participation=True
    )
    return _verify(scope, snapshot)


def _preflight_values(scope: _v1._Scope, snapshot: LearningSnapshotV5) -> None:
    # ALL old physical scalar/JSON/float/time checks finish before delegated V4
    # writes, as do the new checks below. No domain normalization is performed.
    _v4._preflight(scope, _assessment_slice(snapshot))
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
                elif isinstance(value, Float8Bits):
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
                elif isinstance(value, str):
                    value.encode("utf-8")
                    if "\x00" in value:
                        raise SnapshotValidationError(
                            "unsupported storage representation"
                        ) from None
                    if spec.pg_type.startswith("character varying(") and len(
                        value
                    ) > int(
                        spec.pg_type.removeprefix("character varying(").removesuffix(
                            ")"
                        )
                    ):
                        raise SnapshotValidationError(
                            "unsupported storage representation: "
                            f"{contract.name}/{row.id}/{spec.name}"
                        )
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


# Fixed Academy 096 source unique keys (including subsequent DDL), not ORM discovery.
# Every table also has a primary key, validated by the strict snapshot contract;
# tenant/id uniques are consequently implied. SQL NULL-distinct semantics apply.
_SOURCE_UNIQUE_KEYS = {
    "courses": (("tenant_id", "slug"),),
    "activities": (),
    "submissions": (("tenant_id", "activity_id", "person_id", "attempt_no"),),
    "scores": (),
    "course_completions": (("tenant_id", "person_id", "course_id"),),
    "certificates": (("tenant_id", "person_id", "course_id"), ("tenant_id", "serial")),
    "learning_events": (),
    "chapters": (("tenant_id", "course_id", "number"),),
    "question_banks": (),
    "questions": (("tenant_id", "bank_id", "ext_id"),),
    "chapter_reads": (("tenant_id", "person_id", "chapter_id"),),
    "subtopic_reads": (("tenant_id", "person_id", "chapter_id", "subtopic_slug"),),
    "chapter_bookmarks": (("tenant_id", "person_id", "chapter_id"),),
    "chapter_notes": (("tenant_id", "person_id", "chapter_id"),),
    "cohorts": (),
    "enrollments": (("tenant_id", "cohort_id", "person_id"),),
    "tracks": (("tenant_id", "slug"),),
    "track_courses": (("tenant_id", "track_id", "course_id"),),
    "cohort_tracks": (("tenant_id", "cohort_id", "track_id"),),
    "course_offerings": (("tenant_id", "cohort_id", "course_id"),),
    "course_prerequisites": (("tenant_id", "course_id", "requires_course_id"),),
    "activity_attempts": (),
    "attempt_grants": (),
    "offering_activities": (("tenant_id", "offering_id", "activity_id"),),
    "class_sessions": (),
    "announcements": (),
    "success_queue_entries": (),
}


def _unique_closure(snapshot: LearningSnapshotV5) -> None:
    """Reject source unique conflicts before delegated V4's first INSERT."""
    for name, constraints in _SOURCE_UNIQUE_KEYS.items():
        for columns in (("id",), *constraints):
            seen: set[tuple[object, ...]] = set()
            for row in getattr(snapshot, name):
                key = tuple(getattr(row, col) for col in columns)
                if None in key:
                    continue
                if key in seen:
                    raise SnapshotValidationError(
                        f"duplicate source unique key: {name}/{row.id}/{columns[-1]}"
                    )
                seen.add(key)
    # Explicit predicates of Academy 0041, 0047 and 0046 respectively.
    for name, columns in (
        ("activity_attempts", ("tenant_id", "activity_id", "person_id")),
        ("learning_events", ("tenant_id", "person_id", "kind", "subject_id")),
        ("success_queue_entries", ("tenant_id", "person_id", "reason_kind")),
    ):
        seen = set()
        for row in getattr(snapshot, name):
            if name == "activity_attempts" and row.submitted_at is not None:
                continue
            if name == "learning_events" and row.kind not in (
                "chapter_completed",
                "certificate_earned",
            ):
                continue
            if name == "success_queue_entries" and row.status != "open":
                continue
            key = tuple(getattr(row, col) for col in columns)
            if None in key:
                continue
            if key in seen:
                raise SnapshotValidationError(
                    f"duplicate source unique key: {name}/{row.id}/{columns[-1]}"
                )
            seen.add(key)


def _closure(scope: _v1._Scope, snapshot: LearningSnapshotV5) -> None:
    _v4._closure(scope, _assessment_slice(snapshot))
    offerings = {(row.tenant_id, row.id) for row in snapshot.course_offerings}
    activities = {(row.tenant_id, row.id) for row in snapshot.activities}
    cohorts = {(row.tenant_id, row.id) for row in snapshot.cohorts}
    for pacing_row in snapshot.offering_activities:
        _reference(
            (pacing_row.tenant_id, pacing_row.offering_id) in offerings,
            "offering_activities",
            pacing_row.id,
            "offering_id",
        )
        _reference(
            (pacing_row.tenant_id, pacing_row.activity_id) in activities,
            "offering_activities",
            pacing_row.id,
            "activity_id",
        )
    for session_row in snapshot.class_sessions:
        _reference(
            (session_row.tenant_id, session_row.cohort_id) in cohorts,
            "class_sessions",
            session_row.id,
            "cohort_id",
        )
    for announcement_row in snapshot.announcements:
        if announcement_row.cohort_id is not None:
            _reference(
                (announcement_row.tenant_id, announcement_row.cohort_id) in cohorts,
                "announcements",
                announcement_row.id,
                "cohort_id",
            )
    identities = tuple(
        ("announcements", row, "author_person_id") for row in snapshot.announcements
    ) + tuple(
        ("success_queue_entries", row, "person_id")
        for row in snapshot.success_queue_entries
    )
    person_ids = tuple(
        sorted({getattr(row, col) for _, row, col in identities}, key=str)
    )
    if person_ids:
        parties = {
            (r["tenant_id"], r["id"])
            for r in scope.execute(
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
        for name, row, col in identities:
            person = getattr(row, col)
            _reference(
                (row.tenant_id, person) in parties and person in persons,
                name,
                row.id,
                col,
            )


def export_snapshot_v5(
    connection: Connection, tenant_ids: tuple[UUID, ...]
) -> LearningSnapshotV5:
    """Export CURRENT selected rows, without claiming the product extent is complete."""
    validate_tenant_extent(tenant_ids)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, tenant_ids)
    _v1._refuse_non_default_prerequisite_policy(
        scope, tenant_ids, include_participation=True
    )
    base = _v4.export_snapshot_v4(connection, tenant_ids)
    rows = {
        contract.name: _read_rows(scope, contract, tenant_ids) for contract in _TABLES
    }
    snapshot = LearningSnapshotV5(
        version=5,
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
        activity_attempts=base.activity_attempts,
        attempt_grants=base.attempt_grants,
        offering_activities=cast(
            tuple[OfferingActivityRowV5, ...], rows["offering_activities"]
        ),
        class_sessions=cast(tuple[ClassSessionRowV5, ...], rows["class_sessions"]),
        announcements=cast(tuple[AnnouncementRowV5, ...], rows["announcements"]),
        success_queue_entries=cast(
            tuple[SuccessQueueEntryRowV5, ...], rows["success_queue_entries"]
        ),
    )
    scope.check()
    return snapshot


def import_snapshot_v5(
    connection: Connection, snapshot: LearningSnapshotV5
) -> ImportObservationV5:
    """Restore ALL-tenant-empty 27-table storage in the exact caller transaction.

    No outcomes, retries or savepoints. Required writes propagate for caller
    rollback; full representation/FK preflight precedes V4's first INSERT.
    """
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _v1._refuse_non_default_prerequisite_policy(
        scope, snapshot.tenant_ids, include_participation=True
    )
    _require_empty(scope)
    _preflight(scope, snapshot)
    _closure(scope, snapshot)
    _unique_closure(snapshot)
    scope.check()
    base = _v4.import_snapshot_v4(connection, _assessment_slice(snapshot))
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
                    struct.unpack("!d", bytes.fromhex(value.hex))[0]
                    if isinstance(value, Float8Bits)
                    else str(value)
                    if isinstance(value, JsonbText)
                    else value
                )
            scope.execute(statement, parameters)
        counts.append(RowCountObservation(contract.name, len(rows)))
    verification = _verify(scope, snapshot)
    scope.check()
    return ImportObservationV5(5, verification, tuple(counts))


__all__ = ["export_snapshot_v5", "import_snapshot_v5", "verify_snapshot_v5"]
