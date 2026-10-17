"""Product-neutral normalized input and immutable curriculum facts.

File decoding, title presentation, figure discovery, YAML and safe rendering
are adapter responsibilities. No command chooses a transaction outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .references import CourseFact


class CurriculumInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CourseCreate(CurriculumInput):
    tenant_id: UUID
    slug: str
    title: str
    discipline: str


class ChapterWrite(CurriculumInput):
    tenant_id: UUID
    course_id: UUID
    number: int
    title: str
    body_md: str
    # Already rendered and sanitized by the caller's trusted renderer.
    body_html: str
    part: str = ""


class FigureSource(CurriculumInput):
    id: str
    content: bytes | None


class ChapterSource(CurriculumInput):
    # Exact raw.encode('utf-8') supplied by the Academy read_text adapter;
    # never normalized markdown, metadata or rendered HTML.
    raw_utf8: bytes
    # The adapter supplies one entry per distinct referenced figure ID.
    figures: tuple[FigureSource, ...] = ()


class ImportedChapter(CurriculumInput):
    number: int
    title: str
    part: str = ""
    body_html: str
    source_hash: str


class ManualImport(CurriculumInput):
    tenant_id: UUID
    slug: str
    # Adapter supplies learner-facing display title (product prefix removed).
    title: str
    discipline: str
    source_ref: str
    # Preserve source sorted-file encounter order, including repeated numbers.
    chapters: tuple[ImportedChapter, ...] = ()


class BankQuestion(CurriculumInput):
    id: str
    stem: str
    type: str
    rubric_category: str
    options: list[JsonValue] = Field(default_factory=list)
    # Numeric questions carry a scalar; preserve source JSON shape.
    correct: JsonValue = Field(default_factory=list)
    category: JsonValue = None
    competency: JsonValue = None
    explanation: str = ""
    weight: int = 1


class BankDocument(CurriculumInput):
    course: str
    chapter: int | None = None
    kind: str
    version: int = 1
    questions: tuple[BankQuestion, ...]
    # Raw declared keys are retained for the original linter's diagnostics.
    policy: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChapterFact:
    id: UUID
    tenant_id: UUID
    course_id: UUID
    number: int
    title: str
    part: str
    body_md: str
    body_html: str
    source_hash: str
    order_index: int


@dataclass(frozen=True, slots=True)
class BankFact:
    id: UUID
    tenant_id: UUID
    course_id: UUID
    chapter_number: int | None
    kind: str
    version: int


@dataclass(frozen=True, slots=True)
class PublicCatalogItem:
    course: CourseFact
    chapter_count: int
