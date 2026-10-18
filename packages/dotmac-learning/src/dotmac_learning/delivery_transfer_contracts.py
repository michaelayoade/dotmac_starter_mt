"""Version-five CURRENT storage contracts for twenty-seven learning tables.

Every historical field is mandatory. V1/V2/V3 retain their original scope.
This protocol extension is not a distribution version or cutover authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from . import assessment_transfer_contracts as _v4
from . import curriculum_transfer_contracts as _v2
from . import participation_transfer_contracts as _v3
from . import transfer_contracts as _v1
from .transfer_contracts import (
    Float8Bits,
    JsonbText,
    RowCountObservation,
    SnapshotValidationError,
    _StorageRowV1,
)


@dataclass(frozen=True, slots=True, repr=False)
class OfferingActivityRowV5(_StorageRowV1):
    offering_id: UUID
    activity_id: UUID
    release_at: datetime | None
    due_at: datetime | None


@dataclass(frozen=True, slots=True, repr=False)
class ClassSessionRowV5(_StorageRowV1):
    cohort_id: UUID
    offering_id: UUID | None
    session_type: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    instructor_person_id: UUID | None
    location: str | None
    join_url: str | None
    notes: str | None
    status: str


@dataclass(frozen=True, slots=True, repr=False)
class AnnouncementRowV5(_StorageRowV1):
    cohort_id: UUID | None
    author_person_id: UUID
    title: str
    body_md: str
    body_html: str


@dataclass(frozen=True, slots=True, repr=False)
class SuccessQueueEntryRowV5(_StorageRowV1):
    person_id: UUID
    cohort_id: UUID | None
    reason_kind: str
    supporting_facts: JsonbText
    severity: str
    detected_at: datetime
    assigned_to: UUID | None
    recommended_action: str
    status: str
    resolved_at: datetime | None
    resolved_by: UUID | None
    score_hint: Float8Bits


DeliveryStorageRowV5 = (
    OfferingActivityRowV5
    | ClassSessionRowV5
    | AnnouncementRowV5
    | SuccessQueueEntryRowV5
)
_DELIVERY_ROW_TYPES = (
    ("offering_activities", OfferingActivityRowV5),
    ("class_sessions", ClassSessionRowV5),
    ("announcements", AnnouncementRowV5),
    ("success_queue_entries", SuccessQueueEntryRowV5),
)


@dataclass(frozen=True, slots=True, repr=False)
class LearningSnapshotV5:
    """All twenty-seven tables, with explicit genuinely scoped tenant extent."""

    version: Literal[5]
    tenant_ids: tuple[UUID, ...]
    courses: tuple[_v1.CourseRowV1, ...]
    activities: tuple[_v1.ActivityRowV1, ...]
    submissions: tuple[_v1.SubmissionRowV1, ...]
    scores: tuple[_v1.ScoreRowV1, ...]
    course_completions: tuple[_v1.CourseCompletionRowV1, ...]
    certificates: tuple[_v1.CertificateRowV1, ...]
    learning_events: tuple[_v1.LearningEventRowV1, ...]
    chapters: tuple[_v2.ChapterRowV2, ...]
    question_banks: tuple[_v2.QuestionBankRowV2, ...]
    questions: tuple[_v2.QuestionRowV2, ...]
    chapter_reads: tuple[_v2.ChapterReadRowV2, ...]
    subtopic_reads: tuple[_v2.SubtopicReadRowV2, ...]
    chapter_bookmarks: tuple[_v2.ChapterBookmarkRowV2, ...]
    chapter_notes: tuple[_v2.ChapterNoteRowV2, ...]
    cohorts: tuple[_v3.CohortRowV3, ...]
    enrollments: tuple[_v3.EnrollmentRowV3, ...]
    tracks: tuple[_v3.TrackRowV3, ...]
    track_courses: tuple[_v3.TrackCourseRowV3, ...]
    cohort_tracks: tuple[_v3.CohortTrackRowV3, ...]
    course_offerings: tuple[_v3.CourseOfferingRowV3, ...]
    course_prerequisites: tuple[_v3.CoursePrerequisiteRowV3, ...]
    activity_attempts: tuple[_v4.ActivityAttemptRowV4, ...]
    attempt_grants: tuple[_v4.AttemptGrantRowV4, ...]

    offering_activities: tuple[OfferingActivityRowV5, ...]
    class_sessions: tuple[ClassSessionRowV5, ...]
    announcements: tuple[AnnouncementRowV5, ...]
    success_queue_entries: tuple[SuccessQueueEntryRowV5, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 5:
            raise SnapshotValidationError(
                "unsupported twenty-seven-table snapshot version"
            )
        _assessment_slice(self)
        extent = set(self.tenant_ids)
        for table, row_type in _DELIVERY_ROW_TYPES:
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
        return "LearningSnapshotV5(<twenty-seven-table storage rows>)"


def _assessment_slice(snapshot: LearningSnapshotV5) -> _v4.LearningSnapshotV4:
    """Internal twenty-three-table projection; does not prove full V5 scope."""
    return _v4.LearningSnapshotV4(
        version=4,
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
        cohorts=snapshot.cohorts,
        enrollments=snapshot.enrollments,
        tracks=snapshot.tracks,
        track_courses=snapshot.track_courses,
        cohort_tracks=snapshot.cohort_tracks,
        course_offerings=snapshot.course_offerings,
        course_prerequisites=snapshot.course_prerequisites,
        activity_attempts=snapshot.activity_attempts,
        attempt_grants=snapshot.attempt_grants,
    )


class SnapshotClosureErrorV5(SnapshotValidationError):
    """Missing actual FK parent, reporting only child location."""

    def __init__(self, table: str, key: UUID, column: str) -> None:
        self.table, self.key, self.column = table, key, column
        super().__init__(f"unclosed storage reference: {table}/{key}/{column}")


@dataclass(frozen=True, slots=True)
class VerificationObservationV5:
    """Uncommitted exact full-table observation, not completeness authority."""

    version: Literal[5]
    tenant_ids: tuple[UUID, ...]
    row_counts: tuple[RowCountObservation, ...]

    @property
    def matched(self) -> Literal[True]:
        return True


@dataclass(frozen=True, slots=True)
class ImportObservationV5:
    """Uncommitted twenty-seven-table inserts and independent raw readback."""

    version: Literal[5]
    verification: VerificationObservationV5
    inserted_rows: tuple[RowCountObservation, ...]


__all__ = [
    "OfferingActivityRowV5",
    "ClassSessionRowV5",
    "AnnouncementRowV5",
    "SuccessQueueEntryRowV5",
    "DeliveryStorageRowV5",
    "LearningSnapshotV5",
    "SnapshotClosureErrorV5",
    "VerificationObservationV5",
    "ImportObservationV5",
]
