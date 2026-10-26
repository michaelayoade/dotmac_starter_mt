"""Seven-table lossless storage transfer inside the caller's existing transaction.

The caller supplies a PostgreSQL Connection with whole-catalog visibility and
owns transaction cleanup, writer fencing, reference closure and authorization.
This participant never opens a connection or starts, commits, or rolls back a
transaction. Export reads the complete CURRENT declared extent, including
updates and deletions; it is not a timestamp delta or a recovery/cutover proof.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from dotmac_kernel.namespaces import module_schema
from sqlalchemy import Text, bindparam, func, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy import column as sql_column
from sqlalchemy import table as sql_table
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB
from sqlalchemy.engine import (
    Connection,
    CursorResult,
    NestedTransaction,
    RootTransaction,
)
from sqlalchemy.sql import Executable
from sqlalchemy.sql.elements import BindParameter, ColumnElement
from sqlalchemy.sql.selectable import TableClause

from .transfer_contracts import (
    ActivityRowV1,
    CertificateRowV1,
    CourseCompletionRowV1,
    CourseRowV1,
    DestinationNotEmpty,
    Float8Bits,
    ImportObservation,
    JsonbText,
    LearningEventRowV1,
    LearningSnapshotV1,
    RowCountObservation,
    RowMismatch,
    ScoreRowV1,
    SnapshotMismatch,
    SnapshotValidationError,
    StorageRowV1,
    SubmissionRowV1,
    TransferConnectionError,
    UnsupportedFloatRepresentation,
    UnsupportedTimestampRepresentation,
    VerificationObservation,
    validate_tenant_extent,
)

_SCHEMA = module_schema("learning")


@dataclass(frozen=True, slots=True)
class _Column:
    name: str
    pg_type: str
    nullable: bool = False


@dataclass(frozen=True, slots=True)
class _Table:
    name: str
    row_type: type[StorageRowV1]
    columns: tuple[_Column, ...]

    @property
    def qualified(self) -> str:
        return f"{_SCHEMA}.{self.name}"

    @property
    def clause(self) -> TableClause:
        return _RELATIONS[self.name]


# Source-derived static declarations, not learned from a destination catalogue.
# Column order is explicit for binding/readback and independent of live attnum.
_IDENTITY = (_Column("id", "uuid"), _Column("tenant_id", "uuid"))
_TIMESTAMPS = (
    _Column("created_at", "timestamp with time zone"),
    _Column("updated_at", "timestamp with time zone"),
)
_TABLES = (
    _Table(
        "courses",
        CourseRowV1,
        (
            *_IDENTITY,
            _Column("slug", "character varying(63)"),
            _Column("title", "character varying(200)"),
            _Column("discipline", "character varying(40)"),
            _Column("source_ref", "character varying(120)"),
            _Column("version", "integer"),
            _Column("status", "character varying(20)"),
            _Column("listed", "boolean"),
            *_TIMESTAMPS,
        ),
    ),
    _Table(
        "activities",
        ActivityRowV1,
        (
            *_IDENTITY,
            _Column("course_id", "uuid"),
            _Column("chapter_number", "integer", True),
            _Column("type", "character varying(16)"),
            _Column("bank_id", "uuid", True),
            _Column("title", "character varying(200)"),
            _Column("pass_threshold", "double precision"),
            _Column("max_attempts", "integer", True),
            _Column("assessment_mode", "character varying(10)"),
            _Column("grading", "character varying(10)"),
            _Column("weight", "double precision"),
            _Column("question_count", "integer", True),
            *_TIMESTAMPS,
        ),
    ),
    _Table(
        "submissions",
        SubmissionRowV1,
        (
            *_IDENTITY,
            _Column("activity_id", "uuid"),
            _Column("person_id", "uuid"),
            _Column("answers", "jsonb"),
            _Column("attempt_no", "integer"),
            *_TIMESTAMPS,
        ),
    ),
    _Table(
        "scores",
        ScoreRowV1,
        (
            *_IDENTITY,
            _Column("submission_id", "uuid"),
            _Column("score", "double precision"),
            _Column("max_score", "double precision"),
            _Column("fraction", "double precision"),
            _Column("passed", "boolean"),
            _Column("per_item", "jsonb"),
            _Column("source", "character varying(10)"),
            _Column("override_reason", "text", True),
            *_TIMESTAMPS,
        ),
    ),
    _Table(
        "course_completions",
        CourseCompletionRowV1,
        (
            *_IDENTITY,
            _Column("person_id", "uuid"),
            _Column("course_id", "uuid"),
            _Column("status", "character varying(20)"),
            _Column("pct", "double precision"),
            _Column("completed_at", "timestamp with time zone", True),
            *_TIMESTAMPS,
        ),
    ),
    _Table(
        "certificates",
        CertificateRowV1,
        (
            *_IDENTITY,
            _Column("person_id", "uuid"),
            _Column("course_id", "uuid"),
            _Column("serial", "character varying(32)"),
            _Column("issued_at", "timestamp with time zone"),
            *_TIMESTAMPS,
        ),
    ),
    _Table(
        "learning_events",
        LearningEventRowV1,
        (
            *_IDENTITY,
            _Column("person_id", "uuid"),
            _Column("course_id", "uuid", True),
            _Column("kind", "character varying(30)"),
            _Column("subject_id", "uuid", True),
            _Column("detail", "jsonb"),
            _Column("occurred_at", "timestamp with time zone"),
            *_TIMESTAMPS,
        ),
    ),
)


# SQL identifiers come only from the static storage declarations above.
# Untyped Core columns intentionally leave native values with the DBAPI and
# keep JSONB strings out of SQLAlchemy's Python JSON serializer. JSONB/float
# INSERT expressions cast the bound value explicitly, as preflight does.
_RELATIONS = {
    contract.name: sql_table(
        contract.name,
        *(sql_column(spec.name) for spec in contract.columns),
        schema=_SCHEMA,
    )
    for contract in _TABLES
}
_TENANTS = sql_table("tenants", sql_column("id"), schema="public")

# RD-1 seam (a3 Waves 0+1L): lr_0008/lr_0009 added composable-policy
# columns that the frozen V1-V5 row contracts deliberately do NOT carry.
# Qualification admits exactly these columns, with pinned
# type/nullability, as a whole set per migration per table (any prefix of
# the migration chain qualifies; partial sets and other drift still
# mismatch). Frozen rows neither read nor write them, so a transfer is
# refused while any covered row in the tenant extent is non-default.
# The refusal message is kept byte-stable across the a3 column waves.
_A3_S1_COLUMNS: dict[str, dict[str, tuple[str, bool]]] = {
    "courses": {"prerequisite_composition": ("character varying(8)", False)},
    "course_prerequisites": {
        "condition_kind": ("character varying(32)", False),
        "params": ("jsonb", False),
        "group_key": ("character varying(32)", False),
    },
    "tracks": {"gating_mode": ("character varying(16)", False)},
}
_A3_S2_COLUMNS: dict[str, dict[str, tuple[str, bool]]] = {
    "courses": {
        "completion_fraction": ("double precision", False),
        "certificate_auto_issue": ("boolean", False),
        "certificate_validity_days": ("integer", True),
    },
    "activities": {
        "scoring_policy": ("character varying(10)", False),
        "is_final": ("boolean", False),
        "min_attempt_interval_seconds": ("integer", False),
    },
    "certificates": {"expires_at": ("timestamp with time zone", True)},
    "cohorts": {"auto_accept_below_action": ("character varying(16)", False)},
    "course_offerings": {"pacing_default_open": ("boolean", False)},
}
_A3_BANK_COLUMNS: dict[str, dict[str, tuple[str, bool]]] = {
    "activities": {"bank_version_id": ("uuid", True)},
    "question_banks": {"current_version_id": ("uuid", True)},
    "questions": {"bank_version_id": ("uuid", True)},
    "activity_attempts": {"bank_version_id": ("uuid", True)},
    "course_prerequisites": {"origin": ("character varying(16)", False)},
}
_A3_COLUMN_GROUPS = (_A3_S1_COLUMNS, _A3_S2_COLUMNS, _A3_BANK_COLUMNS)
_A3_REFUSAL_MESSAGE = (
    "non-default prerequisite policy data is not carried by this "
    "snapshot version; transfer refused (RD-1)"
)
_A3_COURSES = sql_table(
    "courses",
    sql_column("tenant_id"),
    sql_column("prerequisite_composition"),
    sql_column("completion_fraction"),
    sql_column("certificate_auto_issue"),
    sql_column("certificate_validity_days"),
    schema=_SCHEMA,
)
_A3_ACTIVITIES = sql_table(
    "activities",
    sql_column("tenant_id"),
    sql_column("scoring_policy"),
    sql_column("is_final"),
    sql_column("min_attempt_interval_seconds"),
    schema=_SCHEMA,
)
_A3_CERTIFICATES = sql_table(
    "certificates",
    sql_column("tenant_id"),
    sql_column("expires_at"),
    schema=_SCHEMA,
)
_A3_COHORTS = sql_table(
    "cohorts",
    sql_column("tenant_id"),
    sql_column("auto_accept_below_action"),
    schema=_SCHEMA,
)
_A3_OFFERINGS = sql_table(
    "course_offerings",
    sql_column("tenant_id"),
    sql_column("pacing_default_open"),
    schema=_SCHEMA,
)
_A3_PREREQUISITES = sql_table(
    "course_prerequisites",
    sql_column("tenant_id"),
    sql_column("condition_kind"),
    sql_column("params"),
    sql_column("group_key"),
    schema=_SCHEMA,
)
_A3_TRACKS = sql_table(
    "tracks",
    sql_column("tenant_id"),
    sql_column("gating_mode"),
    schema=_SCHEMA,
)
_A3_BANK_ACTIVITIES = sql_table(
    "activities",
    sql_column("tenant_id"),
    sql_column("bank_version_id"),
    schema=_SCHEMA,
)
_A3_BANKS = sql_table(
    "question_banks",
    sql_column("tenant_id"),
    sql_column("current_version_id"),
    schema=_SCHEMA,
)
_A3_QUESTIONS = sql_table(
    "questions",
    sql_column("tenant_id"),
    sql_column("bank_version_id"),
    schema=_SCHEMA,
)
_A3_BANK_VERSIONS = sql_table(
    "question_bank_versions",
    sql_column("tenant_id"),
    schema=_SCHEMA,
)
_A3_ATTEMPTS = sql_table(
    "activity_attempts",
    sql_column("tenant_id"),
    sql_column("bank_version_id"),
    schema=_SCHEMA,
)
_A3_PREREQ_ORIGINS = sql_table(
    "course_prerequisites",
    sql_column("tenant_id"),
    sql_column("origin"),
    schema=_SCHEMA,
)


def _column_contract_matches(
    table_name: str,
    actual: dict[str, tuple[str, bool]],
    expected: dict[str, tuple[str, bool]],
) -> bool:
    admitted = dict(expected)
    if actual == admitted:
        return True
    # Each migration's columns are admitted only as a whole set, in chain
    # order: a database at any prefix of lr_0008 -> lr_0009 qualifies.
    for group in _A3_COLUMN_GROUPS:
        extras = group.get(table_name)
        if not extras:
            continue
        admitted = {**admitted, **extras}
        if actual == admitted:
            return True
    return False


def _a3_group_present(
    scope: _Scope,
    table_name: str,
    group: dict[str, dict[str, tuple[str, bool]]],
) -> bool:
    """True iff the table carries one migration's full a3 column set.

    Qualification runs first and refuses partial drift, so post-qualify
    each group is either wholly absent (never probed) or wholly present.
    """
    extras = group.get(table_name)
    if not extras:
        return False
    found = scope.execute(
        "SELECT attname FROM pg_catalog.pg_attribute "
        "WHERE attrelid = CAST(:relation AS regclass) "
        "AND attnum > 0 AND NOT attisdropped",
        {"relation": f"{_SCHEMA}.{table_name}"},
    ).scalars()
    present = {str(name) for name in found}
    return all(name in present for name in extras)


def _non_default_exists(
    scope: _Scope,
    tenant_ids: tuple[UUID, ...],
    relation: TableClause,
    condition: ColumnElement[bool],
) -> bool:
    return bool(
        scope.extent_query(
            select(
                select(1)
                .select_from(relation)
                .where(
                    relation.c.tenant_id.in_(bindparam("tenant_ids", expanding=True))
                )
                .where(condition)
                .exists()
            ),
            tenant_ids,
        ).scalar_one()
    )


def _refuse_non_default_prerequisite_policy(
    scope: _Scope,
    tenant_ids: tuple[UUID, ...],
    *,
    include_participation: bool,
    include_curriculum: bool = False,
    include_attempts: bool = False,
) -> None:
    """Raise iff any covered row in the tenant extent is non-default.

    Called after qualification from every export/verify (and the
    destination-reading import paths, where the empty destination always
    passes). At defaults nothing raises and snapshot bytes are unchanged.

    Coverage follows each snapshot version's own tables: courses,
    activities and certificates ride every version (V1+), while
    course_prerequisites, tracks, cohorts and course_offerings join at
    V3 (``include_participation``). For ``activities.is_final`` the
    default is exactly ``false`` — the column server default the frozen
    imports materialize — so any ``is_final=true`` refuses until a
    snapshot version carries the flag.

    The lr_0010 bank group follows the same rule per version coverage:
    any non-NULL bank-version pin, any question_bank_versions row, or a
    prerequisite origin other than the yaml_import backfill default
    refuses until SnapshotV6 carries it.
    """
    checks: list[bool] = []
    if _a3_group_present(scope, "courses", _A3_S1_COLUMNS):
        checks.append(
            _non_default_exists(
                scope,
                tenant_ids,
                _A3_COURSES,
                _A3_COURSES.c.prerequisite_composition != "all",
            )
        )
    if _a3_group_present(scope, "courses", _A3_S2_COLUMNS):
        checks.append(
            _non_default_exists(
                scope,
                tenant_ids,
                _A3_COURSES,
                (_A3_COURSES.c.completion_fraction != 1.0)
                | (_A3_COURSES.c.certificate_auto_issue.is_not(True))
                | (_A3_COURSES.c.certificate_validity_days.is_not(None)),
            )
        )
    if _a3_group_present(scope, "activities", _A3_S2_COLUMNS):
        checks.append(
            _non_default_exists(
                scope,
                tenant_ids,
                _A3_ACTIVITIES,
                (_A3_ACTIVITIES.c.scoring_policy != "best")
                | (_A3_ACTIVITIES.c.min_attempt_interval_seconds != 0)
                | (_A3_ACTIVITIES.c.is_final.is_not(False)),
            )
        )
    if _a3_group_present(scope, "certificates", _A3_S2_COLUMNS):
        checks.append(
            _non_default_exists(
                scope,
                tenant_ids,
                _A3_CERTIFICATES,
                _A3_CERTIFICATES.c.expires_at.is_not(None),
            )
        )
    if include_participation:
        if _a3_group_present(scope, "course_prerequisites", _A3_S1_COLUMNS):
            checks.append(
                _non_default_exists(
                    scope,
                    tenant_ids,
                    _A3_PREREQUISITES,
                    (_A3_PREREQUISITES.c.condition_kind != "course_completed")
                    | (sql_cast(_A3_PREREQUISITES.c.params, Text) != "{}")
                    | (_A3_PREREQUISITES.c.group_key != "g0"),
                )
            )
        if _a3_group_present(scope, "tracks", _A3_S1_COLUMNS):
            checks.append(
                _non_default_exists(
                    scope,
                    tenant_ids,
                    _A3_TRACKS,
                    _A3_TRACKS.c.gating_mode != "previous",
                )
            )
        if _a3_group_present(scope, "cohorts", _A3_S2_COLUMNS):
            checks.append(
                _non_default_exists(
                    scope,
                    tenant_ids,
                    _A3_COHORTS,
                    _A3_COHORTS.c.auto_accept_below_action != "waitlisted",
                )
            )
        if _a3_group_present(scope, "course_offerings", _A3_S2_COLUMNS):
            checks.append(
                _non_default_exists(
                    scope,
                    tenant_ids,
                    _A3_OFFERINGS,
                    _A3_OFFERINGS.c.pacing_default_open.is_not(True),
                )
            )
    if _a3_group_present(scope, "activities", _A3_BANK_COLUMNS):
        checks.append(
            _non_default_exists(
                scope,
                tenant_ids,
                _A3_BANK_ACTIVITIES,
                _A3_BANK_ACTIVITIES.c.bank_version_id.is_not(None),
            )
        )
    if include_curriculum and _a3_group_present(
        scope, "question_banks", _A3_BANK_COLUMNS
    ):
        checks.append(
            _non_default_exists(
                scope,
                tenant_ids,
                _A3_BANKS,
                _A3_BANKS.c.current_version_id.is_not(None),
            )
        )
        checks.append(
            _non_default_exists(
                scope,
                tenant_ids,
                _A3_QUESTIONS,
                _A3_QUESTIONS.c.bank_version_id.is_not(None),
            )
        )
        checks.append(
            bool(
                scope.extent_query(
                    select(
                        select(1)
                        .select_from(_A3_BANK_VERSIONS)
                        .where(
                            _A3_BANK_VERSIONS.c.tenant_id.in_(
                                bindparam("tenant_ids", expanding=True)
                            )
                        )
                        .exists()
                    ),
                    tenant_ids,
                ).scalar_one()
            )
        )
    if include_participation and _a3_group_present(
        scope, "course_prerequisites", _A3_BANK_COLUMNS
    ):
        checks.append(
            _non_default_exists(
                scope,
                tenant_ids,
                _A3_PREREQ_ORIGINS,
                _A3_PREREQ_ORIGINS.c.origin != "yaml_import",
            )
        )
    if include_attempts and _a3_group_present(
        scope, "activity_attempts", _A3_BANK_COLUMNS
    ):
        checks.append(
            _non_default_exists(
                scope,
                tenant_ids,
                _A3_ATTEMPTS,
                _A3_ATTEMPTS.c.bank_version_id.is_not(None),
            )
        )
    if any(checks):
        raise SnapshotValidationError(_A3_REFUSAL_MESSAGE)


@dataclass(frozen=True, slots=True)
class _Scope:
    connection: Connection
    root: RootTransaction
    nested: NestedTransaction | None

    @classmethod
    def capture(cls, connection: Connection) -> _Scope:
        if (
            not isinstance(connection, Connection)
            or connection.closed
            or connection.invalidated
        ):
            raise TransferConnectionError("an open caller-owned Connection is required")
        root = connection.get_transaction()
        if root is None or not root.is_active:
            raise TransferConnectionError(
                "an existing active caller root transaction is required"
            )
        scope = cls(connection, root, connection.get_nested_transaction())
        scope.check()
        if connection.dialect.name != "postgresql":
            raise TransferConnectionError("PostgreSQL storage transfer is required")
        return scope

    def check(self) -> None:
        if (
            self.connection.closed
            or self.connection.invalidated
            or self.connection.get_transaction() is not self.root
            or not self.root.is_active
            or self.connection.get_nested_transaction() is not self.nested
            or (self.nested is not None and not self.nested.is_active)
        ):
            raise TransferConnectionError(
                "caller transaction continuity changed during transfer"
            )
        # SQLAlchemy can hold an active logical RootTransaction while the
        # physical driver commits every statement. Inspect the held public
        # proxy only; never open a connection or issue a probe transaction.
        driver = self.connection.connection.dbapi_connection
        autocommit = getattr(driver, "autocommit", None)
        option = self.connection.get_execution_options().get("isolation_level")
        if autocommit is not False or (
            isinstance(option, str) and option.upper() == "AUTOCOMMIT"
        ):
            raise TransferConnectionError(
                "observable disabled DBAPI autocommit and transactional "
                "execution options are required"
            )

    def execute(
        self, statement: str | Executable, parameters: dict[str, object] | None = None
    ) -> CursorResult[tuple[object, ...]]:
        self.check()
        executable = text(statement) if isinstance(statement, str) else statement
        result = self.connection.execute(executable, parameters or {})
        self.check()
        return cast(CursorResult[tuple[object, ...]], result)

    def extent_query(
        self, statement: Executable, tenant_ids: tuple[UUID, ...]
    ) -> CursorResult[tuple[object, ...]]:
        return self.execute(statement, {"tenant_ids": tenant_ids})


def _qualify(scope: _Scope, tenant_ids: tuple[UUID, ...]) -> None:
    """Refuse RLS-filtered and structurally incompatible connections before writes."""
    qualified_role = scope.execute(
        "SELECT rolsuper OR rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname = current_user"
    ).scalar_one()
    if qualified_role is not True:
        raise TransferConnectionError(
            "current_user must have its own SUPERUSER or BYPASSRLS attribute"
        )
    for qualified in ("public.tenants", *(table.qualified for table in _TABLES)):
        visible = scope.execute(
            "SELECT pg_catalog.has_table_privilege(current_user, :relation, 'SELECT') "
            "AND NOT pg_catalog.row_security_active(CAST(:relation AS regclass))",
            {"relation": qualified},
        ).scalar_one()
        if visible is not True:
            raise TransferConnectionError(
                f"whole-table visibility required: {qualified}"
            )
        kind = scope.execute(
            "SELECT relkind FROM pg_catalog.pg_class "
            "WHERE oid = CAST(:relation AS regclass)",
            {"relation": qualified},
        ).scalar_one()
        if kind != "r":
            raise TransferConnectionError(
                f"canonical ordinary table required: {qualified}"
            )
    kernel_id = scope.execute(
        "SELECT pg_catalog.format_type(atttypid, atttypmod), attnotnull "
        "FROM pg_catalog.pg_attribute WHERE attrelid = 'public.tenants'::regclass "
        "AND attname = 'id' AND attnum > 0 AND NOT attisdropped"
    ).one_or_none()
    if kernel_id is None or tuple(kernel_id) != ("uuid", True):
        raise TransferConnectionError("genuine Kernel tenant id storage is required")
    # These actual reads also verify schema USAGE and the declared column reach.
    scope.execute("SELECT id FROM public.tenants LIMIT 0")
    for table in _TABLES:
        actual = {
            str(row[0]): (str(row[1]), not bool(row[2]))
            for row in scope.execute(
                "SELECT attname, pg_catalog.format_type(atttypid, atttypmod), "
                "attnotnull "
                "FROM pg_catalog.pg_attribute "
                "WHERE attrelid = CAST(:relation AS regclass) "
                "AND attnum > 0 AND NOT attisdropped",
                {"relation": table.qualified},
            )
        }
        expected = {
            column.name: (column.pg_type, column.nullable) for column in table.columns
        }
        if not _column_contract_matches(table.name, actual, expected):
            raise TransferConnectionError(
                f"canonical column contract mismatch: {table.qualified}"
            )
        scope.execute(select(*table.clause.c).limit(0))
    found = set(
        scope.extent_query(
            select(_TENANTS.c.id).where(
                _TENANTS.c.id.in_(bindparam("tenant_ids", expanding=True))
            ),
            tenant_ids,
        ).scalars()
    )
    if found != set(tenant_ids):
        raise SnapshotValidationError(
            "declared extent contains an absent Kernel tenant"
        )


def _validate_snapshot(snapshot: LearningSnapshotV1) -> None:
    if type(snapshot) is not LearningSnapshotV1:
        raise SnapshotValidationError("versioned learning storage snapshot required")
    snapshot.__post_init__()


def _timestamp_bits(value: datetime, table: str, key: UUID, column: str) -> str:
    try:
        delta = value.astimezone(UTC) - datetime(2000, 1, 1, tzinfo=UTC)
    except (OverflowError, ValueError):
        raise UnsupportedTimestampRepresentation(
            f"unsupported timestamp: {table}/{key}/{column}"
        ) from None
    micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    return struct.pack("!q", micros).hex()


def _preflight_values(scope: _Scope, snapshot: LearningSnapshotV1) -> None:
    """Validate all representations before the first INSERT, without normalization."""
    checked_floats: set[Float8Bits] = set()
    checked_json: set[JsonbText] = set()
    checked_timestamps: set[str] = set()
    for table in _TABLES:
        for row in getattr(snapshot, table.name):
            for column in table.columns:
                value = getattr(row, column.name)
                if isinstance(value, Float8Bits) and value not in checked_floats:
                    native = struct.unpack("!d", bytes.fromhex(value.hex))[0]
                    observed = scope.execute(
                        "SELECT pg_catalog.encode(pg_catalog.float8send("
                        "CAST(:value AS double precision)), 'hex')",
                        {"value": native},
                    ).scalar_one()
                    if observed != value.hex:
                        raise UnsupportedFloatRepresentation(
                            f"unsupported float8 representation: "
                            f"{table.name}/{row.id}/{column.name}"
                        )
                    checked_floats.add(value)
                elif isinstance(value, datetime):
                    expected = _timestamp_bits(value, table.name, row.id, column.name)
                    if expected not in checked_timestamps:
                        observed = scope.execute(
                            "SELECT pg_catalog.encode(pg_catalog.timestamptz_send("
                            "CAST(:value AS timestamp with time zone)), 'hex')",
                            {"value": value},
                        ).scalar_one()
                        if observed != expected:
                            raise UnsupportedTimestampRepresentation(
                                f"unsupported timestamp: "
                                f"{table.name}/{row.id}/{column.name}"
                            )
                        checked_timestamps.add(expected)
                elif isinstance(value, JsonbText) and value not in checked_json:
                    observed = scope.execute(
                        "SELECT CAST(CAST(:value AS jsonb) AS text)",
                        {"value": str(value)},
                    ).scalar_one()
                    if observed != str(value):
                        raise SnapshotValidationError(
                            f"noncanonical JSONB storage text: "
                            f"{table.name}/{row.id}/{column.name}"
                        )
                    checked_json.add(value)


def _timestamp_from_bits(
    value: object, table: str, key: UUID, column: str
) -> datetime | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) != 16:
            raise ValueError
        micros = struct.unpack("!q", bytes.fromhex(value))[0]
        return datetime(2000, 1, 1, tzinfo=UTC) + timedelta(microseconds=micros)
    except (OverflowError, ValueError, struct.error):
        # PostgreSQL infinity sentinels and finite values beyond Python's
        # supported UTC range cannot silently become datetime.max/min.
        raise UnsupportedTimestampRepresentation(
            f"unsupported timestamp: {table}/{key}/{column}"
        ) from None


def _export_column(table: _Table, column: _Column) -> ColumnElement[object]:
    value = table.clause.c[column.name]
    expression: ColumnElement[object] | ColumnElement[str]
    if column.pg_type == "jsonb":
        json_text: ColumnElement[str] = sql_cast(value, Text)
        expression = json_text
    elif column.pg_type == "double precision":
        expression = func.pg_catalog.encode(func.pg_catalog.float8send(value), "hex")
    elif column.pg_type == "timestamp with time zone":
        expression = func.pg_catalog.encode(
            func.pg_catalog.timestamptz_send(value), "hex"
        )
    else:
        expression = value
    return cast(ColumnElement[object], expression.label(column.name))


def _read_rows(
    scope: _Scope, table: _Table, tenant_ids: tuple[UUID, ...]
) -> tuple[StorageRowV1, ...]:
    projection = tuple(_export_column(table, column) for column in table.columns)
    relation = table.clause
    result = scope.extent_query(
        select(*projection)
        .where(relation.c.tenant_id.in_(bindparam("tenant_ids", expanding=True)))
        .order_by(relation.c.id),
        tenant_ids,
    )
    rows: list[StorageRowV1] = []
    for record in result.mappings():
        values = dict(record)
        for column in table.columns:
            if column.pg_type == "jsonb":
                values[column.name] = JsonbText(values[column.name])
            elif column.pg_type == "double precision":
                values[column.name] = Float8Bits(values[column.name])
            elif column.pg_type == "timestamp with time zone":
                values[column.name] = _timestamp_from_bits(
                    values[column.name], table.name, values["id"], column.name
                )
        rows.append(table.row_type(**values))
    scope.check()
    return tuple(rows)


def export_snapshot(
    connection: Connection, tenant_ids: tuple[UUID, ...]
) -> LearningSnapshotV1:
    """Read all current rows for explicit genuine tenants, preserving storage values.

    The caller owns snapshot isolation and writer fencing; this function does
    not claim completeness against concurrent writers or external references.
    """
    validate_tenant_extent(tenant_ids)
    scope = _Scope.capture(connection)
    _qualify(scope, tenant_ids)
    _refuse_non_default_prerequisite_policy(
        scope, tenant_ids, include_participation=False
    )
    rows = {table.name: _read_rows(scope, table, tenant_ids) for table in _TABLES}
    snapshot = LearningSnapshotV1(
        version=1,
        tenant_ids=tenant_ids,
        courses=cast(tuple[CourseRowV1, ...], rows["courses"]),
        activities=cast(tuple[ActivityRowV1, ...], rows["activities"]),
        submissions=cast(tuple[SubmissionRowV1, ...], rows["submissions"]),
        scores=cast(tuple[ScoreRowV1, ...], rows["scores"]),
        course_completions=cast(
            tuple[CourseCompletionRowV1, ...], rows["course_completions"]
        ),
        certificates=cast(tuple[CertificateRowV1, ...], rows["certificates"]),
        learning_events=cast(tuple[LearningEventRowV1, ...], rows["learning_events"]),
    )
    scope.check()
    return snapshot


def _verify(
    scope: _Scope, snapshot: LearningSnapshotV1, *, entire_store: bool
) -> VerificationObservation:
    # Independent explicit key/column reader: no reuse of importer parameters,
    # cached export objects, domain DTOs, aggregate counts or insert return values.
    mismatches: list[RowMismatch] = []
    counts: list[RowCountObservation] = []
    for table in _TABLES:
        expected = {row.id: row for row in getattr(snapshot, table.name)}
        # Deliberately spell the readback projection here, independent of the
        # import binding and exporter conversion paths.
        relation = table.clause
        projection: list[ColumnElement[object]] = []
        for column in table.columns:
            stored = relation.c[column.name]
            observed: ColumnElement[object] | ColumnElement[str]
            if column.pg_type == "jsonb":
                json_text: ColumnElement[str] = sql_cast(stored, Text)
                observed = json_text
            elif column.pg_type == "double precision":
                observed = func.pg_catalog.encode(
                    func.pg_catalog.float8send(stored), "hex"
                )
            elif column.pg_type == "timestamp with time zone":
                observed = func.pg_catalog.encode(
                    func.pg_catalog.timestamptz_send(stored), "hex"
                )
            else:
                observed = stored
            projection.append(cast(ColumnElement[object], observed.label(column.name)))
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
            mismatches.append(RowMismatch(table.name, key, None, "missing"))
        for key in sorted(actual.keys() - expected.keys(), key=str):
            mismatches.append(RowMismatch(table.name, key, None, "extra"))
        for key in sorted(expected.keys() & actual.keys(), key=str):
            for column in table.columns:
                if column.pg_type == "timestamp with time zone":
                    _timestamp_from_bits(
                        actual[key][column.name], table.name, key, column.name
                    )
                value = getattr(expected[key], column.name)
                wanted = (
                    value.hex
                    if isinstance(value, Float8Bits)
                    else str(value)
                    if isinstance(value, JsonbText)
                    else _timestamp_bits(value, table.name, key, column.name)
                    if isinstance(value, datetime)
                    else value
                )
                if actual[key][column.name] != wanted:
                    mismatches.append(
                        RowMismatch(table.name, key, column.name, "value")
                    )
        if entire_store:
            outside = scope.extent_query(
                select(relation.c.id)
                .where(
                    relation.c.tenant_id.not_in(bindparam("tenant_ids", expanding=True))
                )
                .order_by(relation.c.id),
                snapshot.tenant_ids,
            ).scalars()
            mismatches.extend(
                RowMismatch(table.name, cast(UUID, key), None, "outside_extent")
                for key in outside
            )
        counts.append(RowCountObservation(table.name, len(actual)))
    scope.check()
    if mismatches:
        raise SnapshotMismatch(tuple(mismatches))
    return VerificationObservation(snapshot.tenant_ids, tuple(counts))


def verify_snapshot(
    connection: Connection, snapshot: LearningSnapshotV1
) -> VerificationObservation:
    """Compare exact keys and every stored field in both directions within extent.

    Other tenants are outside this verifier's declared extent. Import additionally
    verifies that no destination rows exist outside its snapshot tenant extent.
    Mismatches expose table/key/column locations and never payload contents.
    """
    _validate_snapshot(snapshot)
    scope = _Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _refuse_non_default_prerequisite_policy(
        scope, snapshot.tenant_ids, include_participation=False
    )
    return _verify(scope, snapshot, entire_store=False)


def import_snapshot(
    connection: Connection, snapshot: LearningSnapshotV1
) -> ImportObservation:
    """Insert into an entirely empty canonical store, then independently compare.

    All rows and float/JSON representations are validated before any insert.
    Required SQL/constraint failures propagate untouched for caller rollback;
    earlier rows and unrelated caller work remain in that caller transaction.
    No conflict ignore, history defaults, invented references or domain effects.
    """
    _validate_snapshot(snapshot)
    scope = _Scope.capture(connection)
    _qualify(scope, snapshot.tenant_ids)
    _refuse_non_default_prerequisite_policy(
        scope, snapshot.tenant_ids, include_participation=False
    )
    for table in _TABLES:
        if scope.execute(
            select(select(1).select_from(table.clause).exists())
        ).scalar_one():
            raise DestinationNotEmpty(
                f"canonical destination is not empty: {table.name}"
            )
    _preflight_values(scope, snapshot)
    counts: list[RowCountObservation] = []
    for table in _TABLES:
        bindings: dict[str, ColumnElement[object]] = {}
        for column in table.columns:
            bound_value: BindParameter[object] = bindparam(column.name)
            binding: ColumnElement[object]
            if column.pg_type == "jsonb":
                binding = sql_cast(bound_value, JSONB)
            elif column.pg_type == "double precision":
                binding = sql_cast(bound_value, DOUBLE_PRECISION)
            else:
                binding = bound_value
            bindings[column.name] = cast(ColumnElement[object], binding)
        statement = table.clause.insert().values(bindings)
        rows = getattr(snapshot, table.name)
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
        counts.append(RowCountObservation(table.name, len(rows)))
    verification = _verify(scope, snapshot, entire_store=True)
    scope.check()
    return ImportObservation(verification, tuple(counts))


__all__ = ["export_snapshot", "import_snapshot", "verify_snapshot"]
