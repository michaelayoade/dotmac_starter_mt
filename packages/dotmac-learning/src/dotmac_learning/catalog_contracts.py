"""Frozen catalog structure facts; products render labels and navigation URLs."""

from dataclasses import dataclass

from .curriculum_contracts import ChapterFact
from .references import ActivityFact


@dataclass(frozen=True, slots=True)
class CatalogActivity:
    activity: ActivityFact
    passed: bool
    pct: int


@dataclass(frozen=True, slots=True)
class CatalogChapter:
    chapter: ChapterFact
    activities: tuple[CatalogActivity, ...]


@dataclass(frozen=True, slots=True)
class CatalogPart:
    part: str
    chapters: tuple[CatalogChapter, ...]


@dataclass(frozen=True, slots=True)
class CourseStructure:
    parts: tuple[CatalogPart, ...]
    course_activities: tuple[CatalogActivity, ...]
    continue_target: ChapterFact | None
    locked: bool
