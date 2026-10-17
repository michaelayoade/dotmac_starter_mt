"""Version-one storage snapshots, independent of domain command contracts.

Every persisted column is mandatory, including nullable history. JSONB is its
PostgreSQL ``jsonb::text`` representation, never decoded through Python JSON.
Float8 values are network-order IEEE-754 bytes, preserving signed zero. These
contracts carry observations, not transfer authorization or cutover evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from datetime import datetime
from types import UnionType
from typing import Literal, get_args, get_origin, get_type_hints
from uuid import UUID


class SnapshotValidationError(ValueError):
    """A storage contract or explicit tenant extent is malformed; no payload text."""


class TransferConnectionError(RuntimeError):
    """Caller transaction, PostgreSQL catalogue, or whole-store visibility refused."""


class DestinationNotEmpty(RuntimeError):
    """At least one canonical table already contains rows, across any tenant."""


class UnsupportedFloatRepresentation(SnapshotValidationError):
    """The supplied connection cannot bind the exact requested float8 bytes."""


class UnsupportedTimestampRepresentation(SnapshotValidationError):
    """A PostgreSQL timestamp cannot retain its instant in native datetime storage."""


class JsonbText(str):
    """Opaque PostgreSQL JSONB text; SQL NULL is not a JSON value.

    PostgreSQL validates syntax and exact representation before import writes.
    JSON null, scalars, arrays and objects are all admissible storage values.
    """

    def __new__(cls, value: str) -> JsonbText:
        if type(value) not in (str, JsonbText):
            raise SnapshotValidationError("JSONB storage text must be a string")
        return str.__new__(cls, value)

    def __repr__(self) -> str:
        return "JsonbText(<storage value>)"


@dataclass(frozen=True, slots=True)
class Float8Bits:
    """Exactly sixteen lowercase hexadecimal digits from PostgreSQL float8send."""

    hex: str

    def __post_init__(self) -> None:
        if type(self.hex) is not str or re.fullmatch(r"[0-9a-f]{16}", self.hex) is None:
            raise SnapshotValidationError(
                "float8 storage requires sixteen lowercase hex digits"
            )

    def __repr__(self) -> str:
        return "Float8Bits(<storage bits>)"


def _typed(value: object, annotation: object) -> bool:
    if get_origin(annotation) is UnionType:
        return any(_typed(value, item) for item in get_args(annotation))
    if annotation is type(None):
        return value is None
    if annotation is datetime:
        return (
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None
        )
    return type(value) is annotation


@dataclass(frozen=True, slots=True, repr=False)
class _StorageRowV1:
    id: UUID
    tenant_id: UUID
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        annotations = get_type_hints(type(self))
        for field in fields(self):
            if not _typed(getattr(self, field.name), annotations[field.name]):
                raise SnapshotValidationError(
                    f"invalid storage field: {type(self).__name__}.{field.name}"
                )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.id})"


@dataclass(frozen=True, slots=True, repr=False)
class CourseRowV1(_StorageRowV1):
    slug: str
    title: str
    discipline: str
    source_ref: str
    version: int
    status: str
    listed: bool


@dataclass(frozen=True, slots=True, repr=False)
class ActivityRowV1(_StorageRowV1):
    course_id: UUID
    chapter_number: int | None
    type: str
    bank_id: UUID | None
    title: str
    pass_threshold: Float8Bits
    max_attempts: int | None
    assessment_mode: str
    grading: str
    weight: Float8Bits
    question_count: int | None


@dataclass(frozen=True, slots=True, repr=False)
class SubmissionRowV1(_StorageRowV1):
    activity_id: UUID
    person_id: UUID
    answers: JsonbText
    attempt_no: int


@dataclass(frozen=True, slots=True, repr=False)
class ScoreRowV1(_StorageRowV1):
    submission_id: UUID
    score: Float8Bits
    max_score: Float8Bits
    fraction: Float8Bits
    passed: bool
    per_item: JsonbText
    source: str
    override_reason: str | None


@dataclass(frozen=True, slots=True, repr=False)
class CourseCompletionRowV1(_StorageRowV1):
    person_id: UUID
    course_id: UUID
    status: str
    pct: Float8Bits
    completed_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False)
class CertificateRowV1(_StorageRowV1):
    person_id: UUID
    course_id: UUID
    serial: str
    issued_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class LearningEventRowV1(_StorageRowV1):
    person_id: UUID
    course_id: UUID | None
    kind: str
    subject_id: UUID | None
    detail: JsonbText
    occurred_at: datetime


StorageRowV1 = (
    CourseRowV1
    | ActivityRowV1
    | SubmissionRowV1
    | ScoreRowV1
    | CourseCompletionRowV1
    | CertificateRowV1
    | LearningEventRowV1
)

_ROW_TYPES = (
    ("courses", CourseRowV1),
    ("activities", ActivityRowV1),
    ("submissions", SubmissionRowV1),
    ("scores", ScoreRowV1),
    ("course_completions", CourseCompletionRowV1),
    ("certificates", CertificateRowV1),
    ("learning_events", LearningEventRowV1),
)


def validate_tenant_extent(tenant_ids: tuple[UUID, ...]) -> None:
    """Require a nonempty explicit tuple of distinct native UUID tenant ids."""
    if type(tenant_ids) is not tuple or not tenant_ids:
        raise SnapshotValidationError(
            "tenant extent must be an explicit nonempty tuple"
        )
    if any(type(tenant) is not UUID for tenant in tenant_ids):
        raise SnapshotValidationError("tenant extent requires native UUID values")
    if len(set(tenant_ids)) != len(tenant_ids):
        raise SnapshotValidationError("tenant extent contains duplicate ids")


@dataclass(frozen=True, slots=True, repr=False)
class LearningSnapshotV1:
    """Complete current rows for declared tenants, including zero-row tenants."""

    version: Literal[1]
    tenant_ids: tuple[UUID, ...]
    courses: tuple[CourseRowV1, ...]
    activities: tuple[ActivityRowV1, ...]
    submissions: tuple[SubmissionRowV1, ...]
    scores: tuple[ScoreRowV1, ...]
    course_completions: tuple[CourseCompletionRowV1, ...]
    certificates: tuple[CertificateRowV1, ...]
    learning_events: tuple[LearningEventRowV1, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise SnapshotValidationError("unsupported learning snapshot version")
        validate_tenant_extent(self.tenant_ids)
        extent = set(self.tenant_ids)
        for table, row_type in _ROW_TYPES:
            rows = getattr(self, table)
            if type(rows) is not tuple:
                raise SnapshotValidationError(f"storage rows must be a tuple: {table}")
            keys: set[UUID] = set()
            for row in rows:
                if type(row) is not row_type:
                    raise SnapshotValidationError(
                        f"wrong storage row contract: {table}"
                    )
                row.__post_init__()
                if row.tenant_id not in extent:
                    raise SnapshotValidationError(
                        f"storage row outside declared extent: {table}/{row.id}"
                    )
                if row.id in keys:
                    raise SnapshotValidationError(
                        f"duplicate storage key: {table}/{row.id}"
                    )
                keys.add(row.id)

    def __repr__(self) -> str:
        return "LearningSnapshotV1(<storage rows>)"


@dataclass(frozen=True, slots=True)
class RowCountObservation:
    table: str
    count: int


@dataclass(frozen=True, slots=True)
class VerificationObservation:
    """Exact equality observed in the caller's uncommitted transaction only."""

    tenant_ids: tuple[UUID, ...]
    row_counts: tuple[RowCountObservation, ...]

    @property
    def matched(self) -> Literal[True]:
        return True


@dataclass(frozen=True, slots=True)
class ImportObservation:
    """Uncommitted inserts and readback; no authorization or cutover assertion."""

    verification: VerificationObservation
    inserted_rows: tuple[RowCountObservation, ...]


@dataclass(frozen=True, slots=True)
class RowMismatch:
    table: str
    key: UUID
    column: str | None
    reason: Literal["missing", "extra", "value", "outside_extent"]


class SnapshotMismatch(RuntimeError):
    """Exact readback disagreed; report locations only, never field contents."""

    def __init__(self, mismatches: tuple[RowMismatch, ...]) -> None:
        self.mismatches = mismatches
        super().__init__(
            "snapshot readback mismatch: "
            + "; ".join(
                f"{item.table}/{item.key}/{item.column or '<row>'}:{item.reason}"
                for item in mismatches
            )
        )


__all__ = [
    "ActivityRowV1",
    "CertificateRowV1",
    "CourseCompletionRowV1",
    "CourseRowV1",
    "DestinationNotEmpty",
    "Float8Bits",
    "ImportObservation",
    "JsonbText",
    "LearningEventRowV1",
    "LearningSnapshotV1",
    "RowCountObservation",
    "RowMismatch",
    "ScoreRowV1",
    "SnapshotMismatch",
    "SnapshotValidationError",
    "StorageRowV1",
    "SubmissionRowV1",
    "TransferConnectionError",
    "UnsupportedFloatRepresentation",
    "UnsupportedTimestampRepresentation",
    "VerificationObservation",
    "validate_tenant_extent",
]
