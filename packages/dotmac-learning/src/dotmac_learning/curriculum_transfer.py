"""Caller-transaction CURRENT transfer of all fourteen learning storage tables.

V1 remains a seven-table protocol. V2 qualifies fourteen tables, validates every
representation and actual FK edge before ANY insert, then reuses the V1 writer
and writes only the added seven tables. No connection or transaction is created
or completed here. Academy still owns writer fences, external reference/ACK
closure, grants, compatible reverse storage, authorization and source selection.
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

from . import transfer as _v1
from .curriculum_transfer_contracts import (
    ChapterBookmarkRowV2,
    ChapterNoteRowV2,
    ChapterReadRowV2,
    ChapterRowV2,
    CurriculumStorageRowV2,
    ImportObservationV2,
    LearningSnapshotV2,
    QuestionBankRowV2,
    QuestionRowV2,
    SnapshotClosureErrorV2,
    SubtopicReadRowV2,
    VerificationObservationV2,
    _first_slice,
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
class _CurriculumTable:
    name: str
    row_type: type[CurriculumStorageRowV2]
    columns: tuple[_v1._Column, ...]

    @property
    def qualified(self) -> str:
        return f"{_v1._SCHEMA}.{self.name}"

    @property
    def clause(self) -> TableClause:
        return _RELATIONS[self.name]


# Independent source-derived declarations. Question.weight is INTEGER;
# Activity's pre-existing float8 weights remain owned by V1's exact contract.
_C = _v1._Column
_TABLES = (
    _CurriculumTable(
        "chapters",
        ChapterRowV2,
        (
            *_v1._IDENTITY,
            _C("course_id", "uuid"),
            _C("number", "integer"),
            _C("title", "character varying(200)"),
            _C("part", "character varying(20)"),
            _C("body_html", "text"),
            _C("body_md", "text"),
            _C("source_hash", "character varying(64)"),
            _C("order_index", "integer"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _CurriculumTable(
        "question_banks",
        QuestionBankRowV2,
        (
            *_v1._IDENTITY,
            _C("course_id", "uuid"),
            _C("chapter_number", "integer", True),
            _C("kind", "character varying(10)"),
            _C("version", "integer"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _CurriculumTable(
        "questions",
        QuestionRowV2,
        (
            *_v1._IDENTITY,
            _C("bank_id", "uuid"),
            _C("ext_id", "character varying(60)"),
            _C("stem", "text"),
            _C("type", "character varying(12)"),
            _C("options", "jsonb"),
            _C("correct", "jsonb"),
            _C("rubric_category", "character varying(12)"),
            _C("category", "character varying(40)", True),
            _C("explanation", "text"),
            _C("weight", "integer"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _CurriculumTable(
        "chapter_reads",
        ChapterReadRowV2,
        (
            *_v1._IDENTITY,
            _C("person_id", "uuid"),
            _C("chapter_id", "uuid"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _CurriculumTable(
        "subtopic_reads",
        SubtopicReadRowV2,
        (
            *_v1._IDENTITY,
            _C("person_id", "uuid"),
            _C("chapter_id", "uuid"),
            _C("subtopic_slug", "character varying(64)"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _CurriculumTable(
        "chapter_bookmarks",
        ChapterBookmarkRowV2,
        (
            *_v1._IDENTITY,
            _C("person_id", "uuid"),
            _C("course_id", "uuid"),
            _C("chapter_id", "uuid"),
            *_v1._TIMESTAMPS,
        ),
    ),
    _CurriculumTable(
        "chapter_notes",
        ChapterNoteRowV2,
        (
            *_v1._IDENTITY,
            _C("person_id", "uuid"),
            _C("course_id", "uuid"),
            _C("chapter_id", "uuid"),
            _C("body", "text"),
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
_PARTIES = table("parties", column("id"), column("tenant_id"), schema="public")
_PERSONS = table("party_persons", column("party_id"), schema="public")


def _validate(snapshot: LearningSnapshotV2) -> None:
    if type(snapshot) is not LearningSnapshotV2:
        raise SnapshotValidationError("version-two fourteen-table snapshot required")
    snapshot.__post_init__()


def _qualify(scope: _v1._Scope, tenant_ids: tuple[UUID, ...]) -> None:
    _v1._qualify(scope, tenant_ids)
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
    # V2's closure preflight additionally needs only the actual Kernel FK
    # columns. Effective column privileges admit either table or column grants;
    # no role or privilege is created, selected, or widened by this participant.
    for relation, columns in (
        ("public.parties", ("id", "tenant_id")),
        ("public.party_persons", ("party_id",)),
    ):
        for name in columns:
            qualified = scope.execute(
                "SELECT pg_catalog.has_column_privilege("
                "current_user, :relation, :column, 'SELECT') "
                "AND NOT pg_catalog.row_security_active(CAST(:relation AS regclass)) "
                "AND (SELECT relkind = 'r' FROM pg_catalog.pg_class "
                "WHERE oid = CAST(:relation AS regclass))",
                {"relation": relation, "column": name},
            ).scalar_one()
            shape = scope.execute(
                "SELECT pg_catalog.format_type(atttypid, atttypmod), attnotnull "
                "FROM pg_catalog.pg_attribute "
                "WHERE attrelid=CAST(:relation AS regclass) "
                "AND attname=:column AND attnum>0 AND NOT attisdropped",
                {"relation": relation, "column": name},
            ).one_or_none()
            if qualified is not True or shape is None or tuple(shape) != ("uuid", True):
                raise TransferConnectionError(
                    "whole Kernel FK-column visibility required"
                )
    scope.execute(select(_PARTIES.c.id, _PARTIES.c.tenant_id).limit(0))
    scope.execute(select(_PERSONS.c.party_id).limit(0))


def _require_empty(scope: _v1._Scope) -> None:
    for contract in (*_v1._TABLES, *_TABLES):
        if scope.execute(
            select(select(1).select_from(contract.clause).exists())
        ).scalar_one():
            raise DestinationNotEmpty(
                f"fourteen-table destination is not empty: {contract.name}"
            )


def _preflight_values(scope: _v1._Scope, snapshot: LearningSnapshotV2) -> None:
    # Both old and new storage representations finish before the V1 writer is
    # invoked. V1's repeated preflight is pure and cannot change frozen rows.
    _v1._preflight_values(scope, _first_slice(snapshot))
    checked_json: set[JsonbText] = set()
    checked_timestamps: set[str] = set()
    for contract in _TABLES:
        for row in getattr(snapshot, contract.name):
            for spec in contract.columns:
                value = getattr(row, spec.name)
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


def _preflight(scope: _v1._Scope, snapshot: LearningSnapshotV2) -> None:
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
        raise SnapshotClosureErrorV2(table_name, key, column_name)


def _closure(scope: _v1._Scope, snapshot: LearningSnapshotV2) -> None:
    courses = {(row.tenant_id, row.id) for row in snapshot.courses}
    course_ids = {row.id for row in snapshot.courses}
    activities = {(row.tenant_id, row.id) for row in snapshot.activities}
    submissions = {(row.tenant_id, row.id) for row in snapshot.submissions}
    banks = {(row.tenant_id, row.id) for row in snapshot.question_banks}
    chapter_ids = {row.id for row in snapshot.chapters}
    for submission in snapshot.submissions:
        _reference(
            (submission.tenant_id, submission.activity_id) in activities,
            "submissions",
            submission.id,
            "activity_id",
        )
    for score in snapshot.scores:
        _reference(
            (score.tenant_id, score.submission_id) in submissions,
            "scores",
            score.id,
            "submission_id",
        )
    for name in ("course_completions", "certificates", "chapters"):
        for row in getattr(snapshot, name):
            _reference(
                (row.tenant_id, row.course_id) in courses, name, row.id, "course_id"
            )
    for question in snapshot.questions:
        _reference(
            (question.tenant_id, question.bank_id) in banks,
            "questions",
            question.id,
            "bank_id",
        )
    for name in (
        "chapter_reads",
        "subtopic_reads",
        "chapter_bookmarks",
        "chapter_notes",
    ):
        for row in getattr(snapshot, name):
            _reference(row.chapter_id in chapter_ids, name, row.id, "chapter_id")
            if name in ("chapter_bookmarks", "chapter_notes"):
                _reference(row.course_id in course_ids, name, row.id, "course_id")
    # Actual dual identity FKs only. No PartyType constraint is invented, and
    # logical bank/course/activity/person UUID columns gain no references.
    person_ids = tuple(
        sorted({row.person_id for row in snapshot.learning_events}, key=str)
    )
    if person_ids:
        parties = {
            (row["tenant_id"], row["id"])
            for row in scope.execute(
                select(_PARTIES.c.tenant_id, _PARTIES.c.id).where(
                    _PARTIES.c.id.in_(bindparam("person_ids", expanding=True))
                ),
                {"person_ids": person_ids},
            ).mappings()
        }
        persons = set(
            scope.execute(
                select(_PERSONS.c.party_id).where(
                    _PERSONS.c.party_id.in_(bindparam("person_ids", expanding=True))
                ),
                {"person_ids": person_ids},
            ).scalars()
        )
        for event in snapshot.learning_events:
            _reference(
                (event.tenant_id, event.person_id) in parties
                and event.person_id in persons,
                "learning_events",
                event.id,
                "person_id",
            )


def _read_rows(
    scope: _v1._Scope, contract: _CurriculumTable, tenant_ids: tuple[UUID, ...]
) -> tuple[CurriculumStorageRowV2, ...]:
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
    rows: list[CurriculumStorageRowV2] = []
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


def export_snapshot_v2(
    connection: Connection, tenant_ids: tuple[UUID, ...]
) -> LearningSnapshotV2:
    """Export every CURRENT fourteen-table row in the explicit selected extent.

    Selection need not be FK-closed when source SIMPLE FKs cross tenants;
    importing that selection into an empty store will explicitly refuse closure.
    The owner still supplies stable writer fencing and source completeness.
    """
    validate_tenant_extent(tenant_ids)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, tenant_ids)
    _v1._refuse_non_default_prerequisite_policy(
        scope, tenant_ids, include_participation=False
    )
    first = _v1.export_snapshot(connection, tenant_ids)
    rows = {
        contract.name: _read_rows(scope, contract, tenant_ids) for contract in _TABLES
    }
    snapshot = LearningSnapshotV2(
        version=2,
        tenant_ids=tenant_ids,
        courses=first.courses,
        activities=first.activities,
        submissions=first.submissions,
        scores=first.scores,
        course_completions=first.course_completions,
        certificates=first.certificates,
        learning_events=first.learning_events,
        chapters=cast(tuple[ChapterRowV2, ...], rows["chapters"]),
        question_banks=cast(tuple[QuestionBankRowV2, ...], rows["question_banks"]),
        questions=cast(tuple[QuestionRowV2, ...], rows["questions"]),
        chapter_reads=cast(tuple[ChapterReadRowV2, ...], rows["chapter_reads"]),
        subtopic_reads=cast(tuple[SubtopicReadRowV2, ...], rows["subtopic_reads"]),
        chapter_bookmarks=cast(
            tuple[ChapterBookmarkRowV2, ...], rows["chapter_bookmarks"]
        ),
        chapter_notes=cast(tuple[ChapterNoteRowV2, ...], rows["chapter_notes"]),
    )
    scope.check()
    return snapshot


def _verify(
    scope: _v1._Scope, snapshot: LearningSnapshotV2
) -> VerificationObservationV2:
    first = _v1._verify(scope, _first_slice(snapshot), entire_store=True)
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
    return VerificationObservationV2(2, snapshot.tenant_ids, tuple(counts))


def verify_snapshot_v2(
    connection: Connection, snapshot: LearningSnapshotV2
) -> VerificationObservationV2:
    """Compare all 135 fields and keys, refusing rows outside the declared extent.

    Equality is an uncommitted whole-fourteen-table destination observation,
    never authority for source completeness, product references or cutover.
    """
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _v1._refuse_non_default_prerequisite_policy(
        scope, snapshot.tenant_ids, include_participation=False
    )
    return _verify(scope, snapshot)


def import_snapshot_v2(
    connection: Connection, snapshot: LearningSnapshotV2
) -> ImportObservationV2:
    """Restore an empty fourteen-table store after ALL-row preflight and FK closure.

    V2 additionally requires effective SELECT on public.parties(id, tenant_id)
    and public.party_persons(party_id) for its actual dual-FK preflight. The V1
    connection/public API contract stays unchanged. Required SQL failures escape
    unchanged for caller root rollback, retaining earlier writes until cleanup.
    """
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _v1._refuse_non_default_prerequisite_policy(
        scope, snapshot.tenant_ids, include_participation=False
    )
    _require_empty(scope)
    _preflight(scope, snapshot)
    _closure(scope, snapshot)
    scope.check()
    first = _v1.import_snapshot(connection, _first_slice(snapshot))
    scope.check()
    counts = list(first.inserted_rows)
    for contract in _TABLES:
        bindings: dict[str, ColumnElement[object]] = {}
        for spec in contract.columns:
            bound: BindParameter[object] = bindparam(spec.name)
            # Inner bind is NullType: JSONB receives raw canonical PostgreSQL
            # text, without SQLAlchemy serializing that string as JSON again.
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
    return ImportObservationV2(2, verification, tuple(counts))


__all__ = ["export_snapshot_v2", "import_snapshot_v2", "verify_snapshot_v2"]
