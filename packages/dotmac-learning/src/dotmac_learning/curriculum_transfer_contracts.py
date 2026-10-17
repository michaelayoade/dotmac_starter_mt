"""Version-two CURRENT storage snapshots covering fourteen canonical tables.

The V1 seven-table protocol and its row contracts remain unchanged. Every new
persisted column is mandatory, including nullable and raw historical content.
This protocol extension is not a distribution version or cutover authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from .transfer_contracts import (
    ActivityRowV1,
    CertificateRowV1,
    CourseCompletionRowV1,
    CourseRowV1,
    JsonbText,
    LearningEventRowV1,
    LearningSnapshotV1,
    RowCountObservation,
    ScoreRowV1,
    SnapshotValidationError,
    SubmissionRowV1,
    _StorageRowV1,
)


@dataclass(frozen=True, slots=True, repr=False)
class ChapterRowV2(_StorageRowV1):
    course_id: UUID
    number: int
    title: str
    part: str
    body_html: str
    body_md: str
    source_hash: str
    order_index: int


@dataclass(frozen=True, slots=True, repr=False)
class QuestionBankRowV2(_StorageRowV1):
    course_id: UUID
    chapter_number: int | None
    kind: str
    version: int


@dataclass(frozen=True, slots=True, repr=False)
class QuestionRowV2(_StorageRowV1):
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


@dataclass(frozen=True, slots=True, repr=False)
class ChapterReadRowV2(_StorageRowV1):
    person_id: UUID
    chapter_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class SubtopicReadRowV2(_StorageRowV1):
    person_id: UUID
    chapter_id: UUID
    subtopic_slug: str


@dataclass(frozen=True, slots=True, repr=False)
class ChapterBookmarkRowV2(_StorageRowV1):
    person_id: UUID
    course_id: UUID
    chapter_id: UUID


@dataclass(frozen=True, slots=True, repr=False)
class ChapterNoteRowV2(_StorageRowV1):
    person_id: UUID
    course_id: UUID
    chapter_id: UUID
    body: str


CurriculumStorageRowV2 = (
    ChapterRowV2
    | QuestionBankRowV2
    | QuestionRowV2
    | ChapterReadRowV2
    | SubtopicReadRowV2
    | ChapterBookmarkRowV2
    | ChapterNoteRowV2
)

_CURRICULUM_ROW_TYPES = (
    ("chapters", ChapterRowV2),
    ("question_banks", QuestionBankRowV2),
    ("questions", QuestionRowV2),
    ("chapter_reads", ChapterReadRowV2),
    ("subtopic_reads", SubtopicReadRowV2),
    ("chapter_bookmarks", ChapterBookmarkRowV2),
    ("chapter_notes", ChapterNoteRowV2),
)


@dataclass(frozen=True, slots=True, repr=False)
class LearningSnapshotV2:
    """All fourteen tables within explicit genuine tenants, including zero-row ones."""

    version: Literal[2]
    tenant_ids: tuple[UUID, ...]
    courses: tuple[CourseRowV1, ...]
    activities: tuple[ActivityRowV1, ...]
    submissions: tuple[SubmissionRowV1, ...]
    scores: tuple[ScoreRowV1, ...]
    course_completions: tuple[CourseCompletionRowV1, ...]
    certificates: tuple[CertificateRowV1, ...]
    learning_events: tuple[LearningEventRowV1, ...]
    chapters: tuple[ChapterRowV2, ...]
    question_banks: tuple[QuestionBankRowV2, ...]
    questions: tuple[QuestionRowV2, ...]
    chapter_reads: tuple[ChapterReadRowV2, ...]
    subtopic_reads: tuple[SubtopicReadRowV2, ...]
    chapter_bookmarks: tuple[ChapterBookmarkRowV2, ...]
    chapter_notes: tuple[ChapterNoteRowV2, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 2:
            raise SnapshotValidationError("unsupported fourteen-table snapshot version")
        _first_slice(self)  # Existing closed row/extent validation, without writes.
        extent = set(self.tenant_ids)
        for table, row_type in _CURRICULUM_ROW_TYPES:
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
        return "LearningSnapshotV2(<fourteen-table storage rows>)"


def _first_slice(snapshot: LearningSnapshotV2) -> LearningSnapshotV1:
    """Internal seven-table projection; it does not verify fourteen-table scope."""
    return LearningSnapshotV1(
        version=1,
        tenant_ids=snapshot.tenant_ids,
        courses=snapshot.courses,
        activities=snapshot.activities,
        submissions=snapshot.submissions,
        scores=snapshot.scores,
        course_completions=snapshot.course_completions,
        certificates=snapshot.certificates,
        learning_events=snapshot.learning_events,
    )


class SnapshotClosureErrorV2(SnapshotValidationError):
    """An actual storage FK lacks its parent; report the child location only."""

    def __init__(self, table: str, key: UUID, column: str) -> None:
        self.table, self.key, self.column = table, key, column
        super().__init__(f"unclosed storage reference: {table}/{key}/{column}")


@dataclass(frozen=True, slots=True)
class VerificationObservationV2:
    """Uncommitted exact fourteen-table observation, never completeness authority."""

    version: Literal[2]
    tenant_ids: tuple[UUID, ...]
    row_counts: tuple[RowCountObservation, ...]

    @property
    def matched(self) -> Literal[True]:
        return True


@dataclass(frozen=True, slots=True)
class ImportObservationV2:
    """Uncommitted fourteen-table inserts and independent exact readback."""

    version: Literal[2]
    verification: VerificationObservationV2
    inserted_rows: tuple[RowCountObservation, ...]


__all__ = [
    "ChapterBookmarkRowV2",
    "ChapterNoteRowV2",
    "ChapterReadRowV2",
    "ChapterRowV2",
    "CurriculumStorageRowV2",
    "ImportObservationV2",
    "LearningSnapshotV2",
    "QuestionBankRowV2",
    "QuestionRowV2",
    "SnapshotClosureErrorV2",
    "SubtopicReadRowV2",
    "VerificationObservationV2",
]
