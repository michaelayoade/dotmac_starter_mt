"""Product-neutral normalized input and immutable curriculum facts.

File decoding, title presentation, figure discovery, YAML and safe rendering
are adapter responsibilities. No command chooses a transaction outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from .references import (
    CourseFact,
    _validate_certificate_validity_days,
    _validate_completion_fraction,
    _validate_prerequisite_composition,
)


class CurriculumInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CourseCreate(CurriculumInput):
    listed: bool = Field(strict=True)
    tenant_id: UUID
    slug: str
    title: str
    discipline: str
    completion_fraction: float = 1.0
    certificate_auto_issue: bool = True
    certificate_validity_days: int | None = None
    prerequisite_composition: str = "all"

    @field_validator("completion_fraction")
    @classmethod
    def validate_completion_fraction(cls, value: float) -> float:
        return _validate_completion_fraction(value)

    @field_validator("certificate_validity_days")
    @classmethod
    def validate_certificate_validity_days(cls, value: int | None) -> int | None:
        return _validate_certificate_validity_days(value)

    @field_validator("prerequisite_composition")
    @classmethod
    def validate_prerequisite_composition(cls, value: str) -> str:
        return _validate_prerequisite_composition(value)


class AuthorChapterCommand(CurriculumInput):
    """Editable source; HTML is supplied by the trusted product renderer."""

    tenant_id: UUID
    course_id: UUID
    number: int
    title: str
    body_md: str
    part: str = ""


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


class ManualImportHeader(CurriculumInput):
    # Used only when the ordered import creates a new course.
    new_course_status: str = Field(strict=True, min_length=1)
    new_course_listed: bool = Field(strict=True)
    new_course_completion_fraction: float = Field(strict=True)
    new_course_certificate_auto_issue: bool = Field(strict=True)
    new_course_certificate_validity_days: int | None = Field(strict=True)
    new_course_prerequisite_composition: str = Field(strict=True)
    tenant_id: UUID
    slug: str
    # Adapter supplies learner-facing display title (product prefix removed).
    title: str
    discipline: str
    source_ref: str

    @field_validator("new_course_completion_fraction")
    @classmethod
    def validate_new_course_completion_fraction(cls, value: float) -> float:
        return _validate_completion_fraction(value)

    @field_validator("new_course_certificate_validity_days")
    @classmethod
    def validate_new_course_validity(cls, value: int | None) -> int | None:
        return _validate_certificate_validity_days(value)

    @field_validator("new_course_prerequisite_composition")
    @classmethod
    def validate_new_course_composition(cls, value: str) -> str:
        return _validate_prerequisite_composition(value)


class ManualImport(ManualImportHeader):
    # Preserve source sorted-file encounter order, including repeated numbers.
    chapters: tuple[ImportedChapter, ...] = ()


class BankQuestion(CurriculumInput):
    id: str
    stem: str
    type: str
    rubric_category: str
    options: JsonValue = Field(default_factory=list)
    # Numeric questions carry a scalar; preserve source JSON shape.
    correct: JsonValue = Field(default_factory=list)
    category: JsonValue = None
    competency: JsonValue = None
    explanation: str = ""
    weight: int


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
    current_version_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PublicCatalogItem:
    course: CourseFact
    chapter_count: int


class BankDocumentHeader(Protocol):
    """Trusted, already supplied bank metadata for a source-faithful raw read."""

    @property
    def tenant_id(self) -> UUID: ...

    @property
    def id(self) -> UUID: ...

    @property
    def chapter_number(self) -> int | None: ...

    @property
    def kind(self) -> str: ...

    @property
    def version(self) -> int: ...
