"""Caller-owned CURRENT transfer of 28 Learning tables (SnapshotV6).

V1-V5 retain their frozen scopes and refuse non-default a3/bank data.
V6 carries everything: the S1/S2 composable-policy columns, prerequisite
origin, and the lr_0010 bank-version layer. Full preflight precedes every
INSERT; independent raw readback covers all keys and deletes. Qualification
is exact — a destination must be at lr_0010, neither behind nor drifted.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, cast
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
from . import delivery_transfer as _v5
from . import participation_transfer as _v3
from . import transfer as _v1
from .bank_transfer_contracts import (
    ActivityAttemptRowV6,
    ActivityRowV6,
    BankStorageRowV6,
    CertificateRowV6,
    CohortRowV6,
    CourseOfferingRowV6,
    CoursePrerequisiteRowV6,
    CourseRowV6,
    ImportObservationV6,
    LearningSnapshotV6,
    QuestionBankRowV6,
    QuestionBankVersionRowV6,
    QuestionRowV6,
    SnapshotClosureErrorV6,
    TrackRowV6,
    VerificationObservationV6,
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

_C = _v1._Column


@dataclass(frozen=True, slots=True)
class _BankTable:
    name: str
    row_type: type[BankStorageRowV6]
    columns: tuple[_v1._Column, ...]

    @property
    def qualified(self) -> str:
        return f"{_v1._SCHEMA}.{self.name}"

    @property
    def clause(self) -> TableClause:
        return _RELATIONS[self.name]


def _base_columns(contracts: tuple[Any, ...], name: str) -> tuple[_v1._Column, ...]:
    for contract in contracts:
        if contract.name == name:
            return tuple(contract.columns)
    raise AssertionError(f"missing base contract: {name}")


def _extended(
    contracts: tuple[Any, ...], name: str, extra: tuple[_v1._Column, ...]
) -> tuple[_v1._Column, ...]:
    base = _base_columns(contracts, name)
    # Identity leads and timestamps trail in every prior contract; new
    # persisted fields sit between the domain columns and timestamps.
    return (*base[:-2], *extra, *base[-2:])


_CHANGED_TABLES = (
    _BankTable(
        "courses",
        CourseRowV6,
        _extended(
            _v1._TABLES,
            "courses",
            (
                _C("prerequisite_composition", "character varying(8)"),
                _C("completion_fraction", "double precision"),
                _C("certificate_auto_issue", "boolean"),
                _C("certificate_validity_days", "integer", True),
            ),
        ),
    ),
    _BankTable(
        "activities",
        ActivityRowV6,
        _extended(
            _v1._TABLES,
            "activities",
            (
                _C("scoring_policy", "character varying(10)"),
                _C("is_final", "boolean"),
                _C("min_attempt_interval_seconds", "integer"),
                _C("bank_version_id", "uuid", True),
            ),
        ),
    ),
    _BankTable(
        "certificates",
        CertificateRowV6,
        _extended(
            _v1._TABLES,
            "certificates",
            (_C("expires_at", "timestamp with time zone", True),),
        ),
    ),
    _BankTable(
        "question_banks",
        QuestionBankRowV6,
        _extended(
            _v2._TABLES,
            "question_banks",
            (_C("current_version_id", "uuid", True),),
        ),
    ),
    _BankTable(
        "question_bank_versions",
        QuestionBankVersionRowV6,
        (
            *_v1._IDENTITY,
            _C("bank_id", "uuid"),
            _C("version_no", "integer", True),
            _C("state", "character varying(10)"),
            _C("origin", "character varying(16)"),
            _C("content_hash", "character varying(64)"),
            _C("lint_violations", "jsonb"),
            _C("base_version_id", "uuid", True),
            _C("draft_revision", "integer"),
            _C("created_by_actor", "uuid", True),
            _C("published_by", "uuid", True),
            _C("published_at", "timestamp with time zone", True),
            _C("retired_by", "uuid", True),
            _C("retired_at", "timestamp with time zone", True),
            _C("retire_reason", "text", True),
            _C("superseded_by_version_id", "uuid", True),
            *_v1._TIMESTAMPS,
        ),
    ),
    _BankTable(
        "questions",
        QuestionRowV6,
        _extended(
            _v2._TABLES,
            "questions",
            (_C("bank_version_id", "uuid", True),),
        ),
    ),
    _BankTable(
        "cohorts",
        CohortRowV6,
        _extended(
            _v3._TABLES,
            "cohorts",
            (_C("auto_accept_below_action", "character varying(16)"),),
        ),
    ),
    _BankTable(
        "tracks",
        TrackRowV6,
        _extended(
            _v3._TABLES,
            "tracks",
            (_C("gating_mode", "character varying(16)"),),
        ),
    ),
    _BankTable(
        "course_offerings",
        CourseOfferingRowV6,
        _extended(
            _v3._TABLES,
            "course_offerings",
            (_C("pacing_default_open", "boolean"),),
        ),
    ),
    _BankTable(
        "course_prerequisites",
        CoursePrerequisiteRowV6,
        _extended(
            _v3._TABLES,
            "course_prerequisites",
            (
                _C("condition_kind", "character varying(32)"),
                _C("params", "jsonb"),
                _C("group_key", "character varying(32)"),
                _C("origin", "character varying(16)"),
            ),
        ),
    ),
    _BankTable(
        "activity_attempts",
        ActivityAttemptRowV6,
        _extended(
            _v4._TABLES,
            "activity_attempts",
            (_C("bank_version_id", "uuid", True),),
        ),
    ),
)
_RELATIONS = {
    contract.name: table(
        contract.name,
        *(column(spec.name) for spec in contract.columns),
        schema=_v1._SCHEMA,
    )
    for contract in _CHANGED_TABLES
}


def _unchanged_contracts() -> tuple[Any, ...]:
    changed = {contract.name for contract in _CHANGED_TABLES}
    out = []
    for module_tables in (
        _v1._TABLES,
        _v2._TABLES,
        _v3._TABLES,
        _v4._TABLES,
        _v5._TABLES,
    ):
        for contract in module_tables:
            if contract.name not in changed:
                out.append(contract)
    return tuple(out)


# FK-safe insert/readback order across all twenty-eight tables. The
# bank <-> current-version cycle is safe at any position because that
# FK is DEFERRABLE INITIALLY DEFERRED (lr_0010).
_ORDER = (
    "courses",
    "chapters",
    "question_banks",
    "question_bank_versions",
    "questions",
    "chapter_reads",
    "subtopic_reads",
    "chapter_bookmarks",
    "chapter_notes",
    "cohorts",
    "tracks",
    "track_courses",
    "cohort_tracks",
    "course_offerings",
    "enrollments",
    "course_prerequisites",
    "activities",
    "submissions",
    "scores",
    "course_completions",
    "certificates",
    "learning_events",
    "activity_attempts",
    "attempt_grants",
    "offering_activities",
    "class_sessions",
    "announcements",
    "success_queue_entries",
)
_BY_NAME = {c.name: c for c in (*_CHANGED_TABLES, *_unchanged_contracts())}
assert set(_BY_NAME) == set(_ORDER) and len(_ORDER) == 28
_ALL_TABLES = tuple(_BY_NAME[name] for name in _ORDER)


def _validate(snapshot: LearningSnapshotV6) -> None:
    if type(snapshot) is not LearningSnapshotV6:
        raise SnapshotValidationError(
            "version-six twenty-eight-table snapshot required"
        )
    snapshot.__post_init__()


def _qualify(scope: _v1._Scope, tenant_ids: tuple[UUID, ...]) -> None:
    # Tenant extent and Kernel FK-column visibility come from the frozen
    # chain; the exact loop below then requires the full lr_0010 shape.
    _v5._qualify(scope, tenant_ids)
    for contract in _ALL_TABLES:
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
                f"twenty-eight-table destination is not empty: {contract.name}"
            )


def _preflight(scope: _v1._Scope, snapshot: LearningSnapshotV6) -> None:
    """Hide data-representation payloads without repairing caller transactions."""
    try:
        _preflight_values(scope, snapshot)
    except DBAPIError as error:
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


def _preflight_values(scope: _v1._Scope, snapshot: LearningSnapshotV6) -> None:
    checked_json: set[JsonbText] = set()
    checked_timestamps: set[str] = set()
    for contract in _ALL_TABLES:
        for row in getattr(snapshot, contract.name):
            for spec in contract.columns:
                value = getattr(row, spec.name)
                if value is None:
                    continue
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


def _reference(present: bool, table_name: str, key: UUID, column_name: str) -> None:
    if not present:
        raise SnapshotClosureErrorV6(table_name, key, column_name)


def _read_rows(
    scope: _v1._Scope, contract: Any, tenant_ids: tuple[UUID, ...]
) -> tuple[Any, ...]:
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
    rows: list[Any] = []
    for record in result.mappings():
        values = dict(record)
        for spec in contract.columns:
            if values[spec.name] is None:
                continue
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
    scope: _v1._Scope, snapshot: LearningSnapshotV6
) -> VerificationObservationV6:
    mismatches: list[RowMismatch] = []
    counts: list[RowCountObservation] = []
    for contract in _ALL_TABLES:
        expected = {row.id: row for row in getattr(snapshot, contract.name)}
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
                if (
                    spec.pg_type == "timestamp with time zone"
                    and actual[key][spec.name] is not None
                ):
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
    return VerificationObservationV6(6, snapshot.tenant_ids, tuple(counts))


def verify_snapshot_v6(
    connection: Connection, snapshot: LearningSnapshotV6
) -> VerificationObservationV6:
    """Independently compare every field, both key directions and outside rows."""
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    return _verify(scope, snapshot)


def _downgrade_row(row: Any, target_type: Any) -> Any:
    names = {field.name for field in fields(target_type)}
    return target_type(**{name: getattr(row, name) for name in names})


def _v5_slice(snapshot: LearningSnapshotV6) -> Any:
    from .delivery_transfer_contracts import LearningSnapshotV5

    values: dict[str, object] = {}
    for contract in _v5._ALL_TABLES:
        rows = getattr(snapshot, contract.name)
        values[contract.name] = tuple(
            _downgrade_row(row, contract.row_type) for row in rows
        )
    return LearningSnapshotV5(
        version=5, tenant_ids=snapshot.tenant_ids, **cast(Any, values)
    )


def _closure(scope: _v1._Scope, snapshot: LearningSnapshotV6) -> None:
    # Every pre-lr_0010 FK keeps its frozen closure via the V5 slice.
    _v5._closure(scope, _v5_slice(snapshot))
    banks = {(row.tenant_id, row.id) for row in snapshot.question_banks}
    versions = {(row.tenant_id, row.id): row for row in snapshot.question_bank_versions}
    for version in snapshot.question_bank_versions:
        _reference(
            (version.tenant_id, version.bank_id) in banks,
            "question_bank_versions",
            version.id,
            "bank_id",
        )
        # Lineage references are plain UUID columns (no DB FK): closure
        # is the only guard, and lineage never crosses banks.
        if version.base_version_id is not None:
            base = versions.get((version.tenant_id, version.base_version_id))
            _reference(
                base is not None and base.bank_id == version.bank_id,
                "question_bank_versions",
                version.id,
                "base_version_id",
            )
        if version.superseded_by_version_id is not None:
            successor = versions.get(
                (version.tenant_id, version.superseded_by_version_id)
            )
            _reference(
                successor is not None and successor.bank_id == version.bank_id,
                "question_bank_versions",
                version.id,
                "superseded_by_version_id",
            )
    for bank in snapshot.question_banks:
        if bank.current_version_id is not None:
            current = versions.get((bank.tenant_id, bank.current_version_id))
            _reference(
                current is not None
                and current.bank_id == bank.id
                and current.state == "published",
                "question_banks",
                bank.id,
                "current_version_id",
            )
    for question in snapshot.questions:
        if question.bank_version_id is not None:
            parent = versions.get((question.tenant_id, question.bank_version_id))
            _reference(
                parent is not None and parent.bank_id == question.bank_id,
                "questions",
                question.id,
                "bank_version_id",
            )
    activities = {(row.tenant_id, row.id): row for row in snapshot.activities}
    for activity in snapshot.activities:
        if activity.bank_version_id is not None:
            pinned = versions.get((activity.tenant_id, activity.bank_version_id))
            _reference(
                pinned is not None
                and (activity.bank_id is None or pinned.bank_id == activity.bank_id),
                "activities",
                activity.id,
                "bank_version_id",
            )
    for attempt in snapshot.activity_attempts:
        if attempt.bank_version_id is not None:
            pinned = versions.get((attempt.tenant_id, attempt.bank_version_id))
            owner = activities.get((attempt.tenant_id, attempt.activity_id))
            _reference(
                pinned is not None
                and (
                    owner is None
                    or owner.bank_id is None
                    or pinned.bank_id == owner.bank_id
                ),
                "activity_attempts",
                attempt.id,
                "bank_version_id",
            )


# Current (post-lr_0010) source unique keys. Changes from the V5 map:
# questions are unique per bank VERSION, prerequisites per typed
# condition, banks by natural key with NULLS NOT DISTINCT.
_SOURCE_UNIQUE_KEYS = {
    **_v5._SOURCE_UNIQUE_KEYS,
    "questions": (("tenant_id", "bank_version_id", "ext_id"),),
    "course_prerequisites": (
        (
            "tenant_id",
            "course_id",
            "requires_course_id",
            "condition_kind",
            "group_key",
        ),
    ),
    "question_bank_versions": (("tenant_id", "bank_id", "version_no"),),
}


def _unique_closure(snapshot: LearningSnapshotV6) -> None:
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
    # Bank natural key is NULLS NOT DISTINCT: a NULL chapter still counts.
    seen_banks: set[tuple[object, ...]] = set()
    for bank in snapshot.question_banks:
        key = (bank.tenant_id, bank.course_id, bank.chapter_number, bank.kind)
        if key in seen_banks:
            raise SnapshotValidationError(
                f"duplicate source unique key: question_banks/{bank.id}/kind"
            )
        seen_banks.add(key)
    # At most one draft per bank (partial unique index predicate).
    seen_drafts: set[tuple[UUID, UUID]] = set()
    for version in snapshot.question_bank_versions:
        if version.state != "draft":
            continue
        key = (version.tenant_id, version.bank_id)
        if key in seen_drafts:
            raise SnapshotValidationError(
                "duplicate source unique key: "
                f"question_bank_versions/{version.id}/bank_id"
            )
        seen_drafts.add(key)
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


def export_snapshot_v6(
    connection: Connection, tenant_ids: tuple[UUID, ...]
) -> LearningSnapshotV6:
    """Export CURRENT selected rows for all twenty-eight tables."""
    validate_tenant_extent(tenant_ids)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, tenant_ids)
    rows = {
        contract.name: _read_rows(scope, contract, tenant_ids)
        for contract in _ALL_TABLES
    }
    snapshot = LearningSnapshotV6(version=6, tenant_ids=tenant_ids, **cast(Any, rows))
    scope.check()
    return snapshot


def import_snapshot_v6(
    connection: Connection, snapshot: LearningSnapshotV6
) -> ImportObservationV6:
    """Restore ALL-tenant-empty 28-table storage in the caller transaction."""
    _validate(snapshot)
    scope = _v1._Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _require_empty(scope)
    _preflight(scope, snapshot)
    _closure(scope, snapshot)
    _unique_closure(snapshot)
    scope.check()
    counts: list[RowCountObservation] = []
    for contract in _ALL_TABLES:
        bindings: dict[str, ColumnElement[object]] = {}
        for spec in contract.columns:
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
    return ImportObservationV6(6, verification, tuple(counts))


__all__ = ["export_snapshot_v6", "import_snapshot_v6", "verify_snapshot_v6"]
