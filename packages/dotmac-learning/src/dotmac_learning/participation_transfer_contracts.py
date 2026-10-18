"""Version-three CURRENT storage contracts for all twenty-one learning tables.

Every historical field is mandatory. V1 and V2 remain their original scoped
protocols. This extension is neither a release identity nor cutover authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from . import curriculum_transfer_contracts as _v2
from .transfer_contracts import (
    ActivityRowV1,
    CertificateRowV1,
    CourseCompletionRowV1,
    CourseRowV1,
    Float8Bits,
    LearningEventRowV1,
    RowCountObservation,
    ScoreRowV1,
    SnapshotValidationError,
    SubmissionRowV1,
    _StorageRowV1,
)


@dataclass(frozen=True, slots=True, repr=False)
class CohortRowV3(_StorageRowV1):
    name: str
    discipline: str
    status: str
    delivery_mode: str
    entrance_bank_id: UUID | None
    entrance_time_limit_minutes: int | None
    auto_accept_threshold: Float8Bits | None


@dataclass(frozen=True, slots=True, repr=False)
class EnrollmentRowV3(_StorageRowV1):
    cohort_id: UUID
    person_id: UUID
    track_id: UUID | None
    role_in_cohort: str
    status: str
    audience: str | None
    employee_ref: str | None
    access_ends_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False)
class TrackRowV3(_StorageRowV1):
    slug: str
    name: str
    status: str


@dataclass(frozen=True, slots=True, repr=False)
class TrackCourseRowV3(_StorageRowV1):
    track_id: UUID
    course_id: UUID
    order_index: int


@dataclass(frozen=True, slots=True, repr=False)
class CohortTrackRowV3(_StorageRowV1):
    cohort_id: UUID
    track_id: UUID
    status: str


@dataclass(frozen=True, slots=True, repr=False)
class CourseOfferingRowV3(_StorageRowV1):
    cohort_id: UUID
    course_id: UUID
    status: str
    starts_at: datetime | None
    ends_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False)
class CoursePrerequisiteRowV3(_StorageRowV1):
    course_id: UUID
    requires_course_id: UUID


ParticipationStorageRowV3 = (
    CohortRowV3
    | EnrollmentRowV3
    | TrackRowV3
    | TrackCourseRowV3
    | CohortTrackRowV3
    | CourseOfferingRowV3
    | CoursePrerequisiteRowV3
)

_PARTICIPATION_ROW_TYPES = (
    ("cohorts", CohortRowV3),
    ("enrollments", EnrollmentRowV3),
    ("tracks", TrackRowV3),
    ("track_courses", TrackCourseRowV3),
    ("cohort_tracks", CohortTrackRowV3),
    ("course_offerings", CourseOfferingRowV3),
    ("course_prerequisites", CoursePrerequisiteRowV3),
)


@dataclass(frozen=True, slots=True, repr=False)
class LearningSnapshotV3:
    """Full CURRENT selected extent, including explicitly empty tables."""

    version: Literal[3]
    tenant_ids: tuple[UUID, ...]
    courses: tuple[CourseRowV1, ...]
    activities: tuple[ActivityRowV1, ...]
    submissions: tuple[SubmissionRowV1, ...]
    scores: tuple[ScoreRowV1, ...]
    course_completions: tuple[CourseCompletionRowV1, ...]
    certificates: tuple[CertificateRowV1, ...]
    learning_events: tuple[LearningEventRowV1, ...]
    chapters: tuple[_v2.ChapterRowV2, ...]
    question_banks: tuple[_v2.QuestionBankRowV2, ...]
    questions: tuple[_v2.QuestionRowV2, ...]
    chapter_reads: tuple[_v2.ChapterReadRowV2, ...]
    subtopic_reads: tuple[_v2.SubtopicReadRowV2, ...]
    chapter_bookmarks: tuple[_v2.ChapterBookmarkRowV2, ...]
    chapter_notes: tuple[_v2.ChapterNoteRowV2, ...]
    cohorts: tuple[CohortRowV3, ...]
    enrollments: tuple[EnrollmentRowV3, ...]
    tracks: tuple[TrackRowV3, ...]
    track_courses: tuple[TrackCourseRowV3, ...]
    cohort_tracks: tuple[CohortTrackRowV3, ...]
    course_offerings: tuple[CourseOfferingRowV3, ...]
    course_prerequisites: tuple[CoursePrerequisiteRowV3, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 3:
            raise SnapshotValidationError(
                "unsupported twenty-one-table snapshot version"
            )
        _curriculum_slice(self)
        extent = set(self.tenant_ids)
        for table, row_type in _PARTICIPATION_ROW_TYPES:
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
        return "LearningSnapshotV3(<twenty-one-table storage rows>)"


def _curriculum_slice(snapshot: LearningSnapshotV3) -> _v2.LearningSnapshotV2:
    """Internal fourteen-table projection; does not prove full V3 scope."""
    return _v2.LearningSnapshotV2(
        version=2,
        tenant_ids=snapshot.tenant_ids,
        courses=snapshot.courses,
        activities=snapshot.activities,
        submissions=snapshot.submissions,
        scores=snapshot.scores,
        course_completions=snapshot.course_completions,
        certificates=snapshot.certificates,
        learning_events=snapshot.learning_events,
        chapters=snapshot.chapters,
        question_banks=snapshot.question_banks,
        questions=snapshot.questions,
        chapter_reads=snapshot.chapter_reads,
        subtopic_reads=snapshot.subtopic_reads,
        chapter_bookmarks=snapshot.chapter_bookmarks,
        chapter_notes=snapshot.chapter_notes,
    )


class SnapshotClosureErrorV3(SnapshotValidationError):
    """A missing actual FK parent, with child location and no payload values."""

    def __init__(self, table: str, key: UUID, column: str) -> None:
        self.table, self.key, self.column = table, key, column
        super().__init__(f"unclosed storage reference: {table}/{key}/{column}")


@dataclass(frozen=True, slots=True)
class VerificationObservationV3:
    """Uncommitted exact full-table observation, never completeness authority."""

    version: Literal[3]
    tenant_ids: tuple[UUID, ...]
    row_counts: tuple[RowCountObservation, ...]

    @property
    def matched(self) -> Literal[True]:
        return True


@dataclass(frozen=True, slots=True)
class ImportObservationV3:
    """Uncommitted twenty-one-table inserts and independent exact readback."""

    version: Literal[3]
    verification: VerificationObservationV3
    inserted_rows: tuple[RowCountObservation, ...]


__all__ = [
    "CohortRowV3",
    "EnrollmentRowV3",
    "TrackRowV3",
    "TrackCourseRowV3",
    "CohortTrackRowV3",
    "CourseOfferingRowV3",
    "CoursePrerequisiteRowV3",
    "ParticipationStorageRowV3",
    "LearningSnapshotV3",
    "SnapshotClosureErrorV3",
    "VerificationObservationV3",
    "ImportObservationV3",
]
