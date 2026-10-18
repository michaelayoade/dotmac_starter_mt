"""Product-neutral commands and detached reading/annotation projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .event_contracts import EventEmission


class ReadingCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    person_id: UUID
    chapter_id: UUID


class ChapterReadingCommand(ReadingCommand):
    course_id: UUID


class AnnotationCommand(ChapterReadingCommand):
    """Caller resolves/authorizes references, as in the source service.

    Individual course/chapter FK and plain person UUID semantics are retained;
    this is neither a tenant-composite reference nor an authorization token.
    """


class NoteCommand(AnnotationCommand):
    body: str | None


class SubtopicReadCommand(ReadingCommand):
    subtopic_slug: str | None


class Heading(BaseModel):
    """Ordered h2/h3 text extracted by the product from stored chapter content.

    The adapter follows source HTMLParser whitespace/character-reference
    handling; this domain seam does not render or accept browser claims as
    proof of the chapter's headings.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    level: Literal["h2", "h3"]
    title: str


class ChapterSubtopicCommand(ChapterReadingCommand):
    subtopic_slug: str
    headings: tuple[Heading, ...]


@dataclass(frozen=True, slots=True)
class SubtopicFact:
    level: str
    title: str
    slug: str


@dataclass(frozen=True, slots=True)
class SubtopicCompletion:
    created: bool
    completed: frozenset[str]
    chapter_complete: bool


@dataclass(frozen=True, slots=True)
class ChapterReadWrite:
    completed: bool
    event: EventEmission | None = None


@dataclass(frozen=True, slots=True)
class NoteFact:
    id: UUID
    tenant_id: UUID
    person_id: UUID
    course_id: UUID
    chapter_id: UUID
    body: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AnnotationState:
    bookmarked: bool
    note: NoteFact | None


@dataclass(frozen=True, slots=True)
class ReadingChapterFact:
    id: UUID
    tenant_id: UUID
    course_id: UUID
    number: int
    title: str


@dataclass(frozen=True, slots=True)
class ReadingCourseFact:
    id: UUID
    tenant_id: UUID
    slug: str
    title: str


@dataclass(frozen=True, slots=True)
class AnnotatedChapter:
    chapter: ReadingChapterFact
    bookmarked: bool = False
    note: NoteFact | None = None


@dataclass(frozen=True, slots=True)
class AnnotationGroup:
    course: ReadingCourseFact
    chapters: tuple[AnnotatedChapter, ...]
