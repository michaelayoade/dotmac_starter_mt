"""Typed, persistence-neutral values for projects and tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ProjectStatus(StrEnum):
    OPEN = "open"
    PLANNED = "planned"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELED = "canceled"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"
    CANCELED = "canceled"


class Priority(StrEnum):
    LOWER = "lower"
    LOW = "low"
    MEDIUM = "medium"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"


class DependencyType(StrEnum):
    FINISH_TO_START = "finish_to_start"
    START_TO_START = "start_to_start"
    FINISH_TO_FINISH = "finish_to_finish"
    START_TO_FINISH = "start_to_finish"


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    id: UUID
    project_id: UUID
    status: TaskStatus
    active: bool = True


@dataclass(frozen=True, slots=True)
class DependencySpec:
    depends_on_task_id: UUID
    dependency_type: DependencyType = DependencyType.FINISH_TO_START
    lag_days: int = 0


@dataclass(frozen=True, slots=True)
class TemplateTask:
    key: str
    title: str
    effort_hours: int = 0
    sort_order: int = 0
    description: str | None = None
    status: TaskStatus = TaskStatus.TODO
    priority: Priority = Priority.NORMAL

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("template task key must not be blank")
        if not self.title.strip():
            raise ValueError("template task title must not be blank")
        if self.effort_hours < 0:
            raise ValueError("template task effort_hours must be non-negative")


@dataclass(frozen=True, slots=True)
class TemplateDependency:
    task_key: str
    depends_on_key: str
    dependency_type: DependencyType = DependencyType.FINISH_TO_START
    lag_days: int = 0


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    key: str
    starts_at: datetime
    due_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectCreate:
    code: str
    name: str
    description: str | None = None
    status: ProjectStatus = ProjectStatus.PLANNED
    priority: Priority = Priority.NORMAL
    starts_at: datetime | None = None
    due_at: datetime | None = None
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TaskCreate:
    project_id: UUID
    title: str
    description: str | None = None
    parent_task_id: UUID | None = None
    status: TaskStatus = TaskStatus.TODO
    priority: Priority = Priority.NORMAL
    effort_hours: int = 0
    starts_at: datetime | None = None
    due_at: datetime | None = None
    created_by_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    code: str
    name: str
    tasks: tuple[TemplateTask, ...]
    dependencies: tuple[TemplateDependency, ...] = ()
    description: str | None = None


__all__ = [
    "DependencySpec",
    "DependencyType",
    "Priority",
    "ProjectCreate",
    "ProjectStatus",
    "ScheduledTask",
    "TaskCreate",
    "TaskSnapshot",
    "TaskStatus",
    "TemplateDefinition",
    "TemplateDependency",
    "TemplateTask",
]
