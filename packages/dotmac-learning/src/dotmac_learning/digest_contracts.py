"""Independent detached learning observations for product-owned digest assembly."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StaffLearningWindow:
    active_ids: frozenset[UUID]
    ever_active_ids: frozenset[UUID]
    completion_count: int


@dataclass(frozen=True, slots=True)
class LearnerWindowActivity:
    chapters: int
    submissions: int
    passed: int
