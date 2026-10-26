"""Version-six CURRENT storage snapshots covering twenty-eight tables.

V1-V5 row contracts and snapshots remain frozen. V6 is the first version
that carries every a3 column the frozen versions deliberately refused
(S1 prerequisite conditions/composition/gating, S2 composable policies)
plus the lr_0010 bank-version layer: the new question_bank_versions
table, bank/activity/attempt version pins, and prerequisite origin.
Tables whose storage did not change reuse their frozen row contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from . import assessment_transfer_contracts as _v4
from . import curriculum_transfer_contracts as _v2
from . import delivery_transfer_contracts as _v5
from . import participation_transfer_contracts as _v3
from . import transfer_contracts as _v1
from .transfer_contracts import (
    Float8Bits,
    JsonbText,
    RowCountObservation,
    SnapshotValidationError,
    _StorageRowV1,
    validate_tenant_extent,
)


@dataclass(frozen=True, slots=True, repr=False)
class CourseRowV6(_StorageRowV1):
    slug: str
    title: str
    discipline: str
    source_ref: str
    version: int
    status: str
    listed: bool
    prerequisite_composition: str
    completion_fraction: Float8Bits
    certificate_auto_issue: bool
    certificate_validity_days: int | None


@dataclass(frozen=True, slots=True, repr=False)
class ActivityRowV6(_StorageRowV1):
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
    scoring_policy: str
    is_final: bool
    min_attempt_interval_seconds: int
    bank_version_id: UUID | None


@dataclass(frozen=True, slots=True, repr=False)
class CertificateRowV6(_StorageRowV1):
    person_id: UUID
    course_id: UUID
    serial: str
    issued_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False)
class QuestionBankRowV6(_StorageRowV1):
    course_id: UUID
    chapter_number: int | None
    kind: str
    version: int
    current_version_id: UUID | None


@dataclass(frozen=True, slots=True, repr=False)
class QuestionBankVersionRowV6(_StorageRowV1):
    bank_id: UUID
    version_no: int | None
    state: str
    origin: str
    content_hash: str
    lint_violations: JsonbText
    base_version_id: UUID | None
    draft_revision: int
    created_by_actor: UUID | None
    published_by: UUID | None
    published_at: datetime | None
    retired_by: UUID | None
    retired_at: datetime | None
    retire_reason: str | None
    superseded_by_version_id: UUID | None


@dataclass(frozen=True, slots=True, repr=False)
class QuestionRowV6(_StorageRowV1):
    bank_id: UUID
    ext_id: str
    stem: str
    type: str
    options: JsonbText
    correct: JsonbText
    rubric_category: str
    category: str | None
    explanation: str
    weight: int
    bank_version_id: UUID | None


@dataclass(frozen=True, slots=True, repr=False)
class CohortRowV6(_StorageRowV1):
    name: str
    discipline: str
    status: str
    delivery_mode: str
    entrance_bank_id: UUID | None
    entrance_time_limit_minutes: int | None
    auto_accept_threshold: Float8Bits | None
    auto_accept_below_action: str


@dataclass(frozen=True, slots=True, repr=False)
class TrackRowV6(_StorageRowV1):
    slug: str
    name: str
    status: str
    gating_mode: str


@dataclass(frozen=True, slots=True, repr=False)
class CourseOfferingRowV6(_StorageRowV1):
    cohort_id: UUID
    course_id: UUID
    status: str
    starts_at: datetime | None
    ends_at: datetime | None
    pacing_default_open: bool


@dataclass(frozen=True, slots=True, repr=False)
class CoursePrerequisiteRowV6(_StorageRowV1):
    course_id: UUID
    requires_course_id: UUID
    condition_kind: str
    params: JsonbText
    group_key: str
    origin: str


@dataclass(frozen=True, slots=True, repr=False)
class ActivityAttemptRowV6(_StorageRowV1):
    activity_id: UUID
    person_id: UUID
    question_ext_ids: JsonbText
    started_at: datetime
    submitted_at: datetime | None
    bank_version_id: UUID | None


BankStorageRowV6 = (
    CourseRowV6
    | ActivityRowV6
    | CertificateRowV6
    | QuestionBankRowV6
    | QuestionBankVersionRowV6
    | QuestionRowV6
    | CohortRowV6
    | TrackRowV6
    | CourseOfferingRowV6
    | CoursePrerequisiteRowV6
    | ActivityAttemptRowV6
)

_BANK_ROW_TYPES = (
    ("courses", CourseRowV6),
    ("activities", ActivityRowV6),
    ("certificates", CertificateRowV6),
    ("question_banks", QuestionBankRowV6),
    ("question_bank_versions", QuestionBankVersionRowV6),
    ("questions", QuestionRowV6),
    ("cohorts", CohortRowV6),
    ("tracks", TrackRowV6),
    ("course_offerings", CourseOfferingRowV6),
    ("course_prerequisites", CoursePrerequisiteRowV6),
    ("activity_attempts", ActivityAttemptRowV6),
)

# Unchanged tables keep their frozen row contracts.
_UNCHANGED_ROW_TYPES = (
    ("submissions", _v1.SubmissionRowV1),
    ("scores", _v1.ScoreRowV1),
    ("course_completions", _v1.CourseCompletionRowV1),
    ("learning_events", _v1.LearningEventRowV1),
    ("chapters", _v2.ChapterRowV2),
    ("chapter_reads", _v2.ChapterReadRowV2),
    ("subtopic_reads", _v2.SubtopicReadRowV2),
    ("chapter_bookmarks", _v2.ChapterBookmarkRowV2),
    ("chapter_notes", _v2.ChapterNoteRowV2),
    ("enrollments", _v3.EnrollmentRowV3),
    ("track_courses", _v3.TrackCourseRowV3),
    ("cohort_tracks", _v3.CohortTrackRowV3),
    ("attempt_grants", _v4.AttemptGrantRowV4),
    ("offering_activities", _v5.OfferingActivityRowV5),
    ("class_sessions", _v5.ClassSessionRowV5),
    ("announcements", _v5.AnnouncementRowV5),
    ("success_queue_entries", _v5.SuccessQueueEntryRowV5),
)

_ALL_ROW_TYPES_V6 = _BANK_ROW_TYPES + _UNCHANGED_ROW_TYPES


@dataclass(frozen=True, slots=True, repr=False)
class LearningSnapshotV6:
    """All twenty-eight tables within explicit genuine tenants."""

    version: Literal[6]
    tenant_ids: tuple[UUID, ...]
    courses: tuple[CourseRowV6, ...]
    activities: tuple[ActivityRowV6, ...]
    submissions: tuple[_v1.SubmissionRowV1, ...]
    scores: tuple[_v1.ScoreRowV1, ...]
    course_completions: tuple[_v1.CourseCompletionRowV1, ...]
    certificates: tuple[CertificateRowV6, ...]
    learning_events: tuple[_v1.LearningEventRowV1, ...]
    chapters: tuple[_v2.ChapterRowV2, ...]
    question_banks: tuple[QuestionBankRowV6, ...]
    question_bank_versions: tuple[QuestionBankVersionRowV6, ...]
    questions: tuple[QuestionRowV6, ...]
    chapter_reads: tuple[_v2.ChapterReadRowV2, ...]
    subtopic_reads: tuple[_v2.SubtopicReadRowV2, ...]
    chapter_bookmarks: tuple[_v2.ChapterBookmarkRowV2, ...]
    chapter_notes: tuple[_v2.ChapterNoteRowV2, ...]
    cohorts: tuple[CohortRowV6, ...]
    enrollments: tuple[_v3.EnrollmentRowV3, ...]
    tracks: tuple[TrackRowV6, ...]
    track_courses: tuple[_v3.TrackCourseRowV3, ...]
    cohort_tracks: tuple[_v3.CohortTrackRowV3, ...]
    course_offerings: tuple[CourseOfferingRowV6, ...]
    course_prerequisites: tuple[CoursePrerequisiteRowV6, ...]
    activity_attempts: tuple[ActivityAttemptRowV6, ...]
    attempt_grants: tuple[_v4.AttemptGrantRowV4, ...]
    offering_activities: tuple[_v5.OfferingActivityRowV5, ...]
    class_sessions: tuple[_v5.ClassSessionRowV5, ...]
    announcements: tuple[_v5.AnnouncementRowV5, ...]
    success_queue_entries: tuple[_v5.SuccessQueueEntryRowV5, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 6:
            raise SnapshotValidationError(
                "unsupported twenty-eight-table snapshot version"
            )
        validate_tenant_extent(self.tenant_ids)
        extent = set(self.tenant_ids)
        for table, row_type in _ALL_ROW_TYPES_V6:
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
        return "LearningSnapshotV6(<twenty-eight-table storage rows>)"


class SnapshotClosureErrorV6(SnapshotValidationError):
    """Missing actual FK parent, reporting only child location."""

    def __init__(self, table: str, key: UUID, column: str) -> None:
        self.table, self.key, self.column = table, key, column
        super().__init__(f"unclosed storage reference: {table}/{key}/{column}")


@dataclass(frozen=True, slots=True)
class VerificationObservationV6:
    """Uncommitted exact full-table observation, not completeness authority."""

    version: Literal[6]
    tenant_ids: tuple[UUID, ...]
    row_counts: tuple[RowCountObservation, ...]

    @property
    def matched(self) -> Literal[True]:
        return True


@dataclass(frozen=True, slots=True)
class ImportObservationV6:
    """Uncommitted twenty-eight-table inserts and independent raw readback."""

    version: Literal[6]
    verification: VerificationObservationV6
    inserted_rows: tuple[RowCountObservation, ...]


__all__ = [
    "ActivityAttemptRowV6",
    "ActivityRowV6",
    "BankStorageRowV6",
    "CertificateRowV6",
    "CohortRowV6",
    "CourseOfferingRowV6",
    "CoursePrerequisiteRowV6",
    "CourseRowV6",
    "ImportObservationV6",
    "LearningSnapshotV6",
    "QuestionBankRowV6",
    "QuestionBankVersionRowV6",
    "QuestionRowV6",
    "SnapshotClosureErrorV6",
    "TrackRowV6",
    "VerificationObservationV6",
]
