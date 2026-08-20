"""Project lifecycle, dependency integrity, and deterministic scheduling."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from uuid import UUID

from dotmac_projects.contracts import (
    DependencySpec,
    DependencyType,
    ProjectStatus,
    ScheduledTask,
    TaskSnapshot,
    TaskStatus,
    TemplateDependency,
    TemplateTask,
)


class ProjectLifecycleError(ValueError):
    """Base refusal raised by the transport-neutral owner."""


class InvalidTransition(ProjectLifecycleError):
    """A lifecycle transition is not available from the current state."""


class StaleState(ProjectLifecycleError):
    """The caller's expected state is no longer authoritative."""


class RelationshipConflict(ProjectLifecycleError):
    """A hierarchy or dependency relation would violate aggregate integrity."""

    def __init__(self, message: str, *, related_ids: Iterable[UUID] = ()) -> None:
        super().__init__(message)
        self.related_ids = tuple(related_ids)


_TERMINAL_PROJECTS = frozenset({ProjectStatus.COMPLETED, ProjectStatus.CANCELED})
_TERMINAL_TASKS = frozenset({TaskStatus.DONE, TaskStatus.CANCELED})


def transition_project(
    current: ProjectStatus,
    requested: ProjectStatus,
    *,
    expected: ProjectStatus,
) -> ProjectStatus:
    if current is not expected:
        raise StaleState(
            f"project status expected {expected.value}, found {current.value}"
        )
    if current is requested:
        return current
    if current in _TERMINAL_PROJECTS:
        raise InvalidTransition(
            f"terminal project status {current.value} cannot change"
        )
    return requested


def transition_task(
    current: TaskStatus,
    requested: TaskStatus,
    *,
    expected: TaskStatus,
    incomplete_dependency_ids: Iterable[UUID] = (),
) -> TaskStatus:
    if current is not expected:
        raise StaleState(
            f"task status expected {expected.value}, found {current.value}"
        )
    if current is requested:
        return current
    if current in _TERMINAL_TASKS:
        raise InvalidTransition(f"terminal task status {current.value} cannot change")
    blockers = tuple(incomplete_dependency_ids)
    if requested is TaskStatus.DONE and blockers:
        raise RelationshipConflict(
            "task cannot finish before every active dependency is done",
            related_ids=blockers,
        )
    return requested


def _graph_has_cycle(edges: Mapping[UUID, Iterable[UUID]]) -> bool:
    visiting: set[UUID] = set()
    visited: set[UUID] = set()

    def visit(node: UUID) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(predecessor) for predecessor in edges.get(node, ())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in edges)


def validate_dependency_replacement(
    task: TaskSnapshot,
    replacements: Iterable[DependencySpec],
    *,
    tasks: Mapping[UUID, TaskSnapshot],
    existing_edges: Mapping[UUID, Iterable[UUID]],
) -> tuple[DependencySpec, ...]:
    reviewed = tuple(replacements)
    ids = tuple(item.depends_on_task_id for item in reviewed)
    if len(ids) != len(set(ids)):
        raise RelationshipConflict("duplicate dependency task")
    if task.id in ids:
        raise RelationshipConflict("task cannot depend on itself")
    for predecessor_id in ids:
        predecessor = tasks.get(predecessor_id)
        if predecessor is None or not predecessor.active:
            raise RelationshipConflict(
                "dependency must name an active task", related_ids=(predecessor_id,)
            )
        if predecessor.project_id != task.project_id:
            raise RelationshipConflict(
                "dependency tasks must belong to the same project",
                related_ids=(predecessor_id,),
            )

    edges = {
        node: set(predecessors)
        for node, predecessors in existing_edges.items()
        if node != task.id
    }
    edges[task.id] = set(ids)
    if _graph_has_cycle(edges):
        raise RelationshipConflict("dependency replacement would create a cycle")
    return reviewed


def _template_order(
    tasks: Mapping[str, TemplateTask],
    dependencies: tuple[TemplateDependency, ...],
) -> tuple[str, ...]:
    incoming: dict[str, set[str]] = {key: set() for key in tasks}
    dependents: dict[str, set[str]] = {key: set() for key in tasks}
    seen_edges: set[tuple[str, str]] = set()
    for dependency in dependencies:
        if dependency.task_key not in tasks or dependency.depends_on_key not in tasks:
            raise RelationshipConflict("template dependency names an unknown task")
        if dependency.task_key == dependency.depends_on_key:
            raise RelationshipConflict("template task cannot depend on itself")
        edge = (dependency.task_key, dependency.depends_on_key)
        if edge in seen_edges:
            raise RelationshipConflict("duplicate template dependency")
        seen_edges.add(edge)
        incoming[dependency.task_key].add(dependency.depends_on_key)
        dependents[dependency.depends_on_key].add(dependency.task_key)

    ready = sorted(
        (key for key, predecessors in incoming.items() if not predecessors),
        key=lambda key: (tasks[key].sort_order, key),
    )
    ordered: list[str] = []
    while ready:
        key = ready.pop(0)
        ordered.append(key)
        for dependent in sorted(
            dependents[key], key=lambda item: (tasks[item].sort_order, item)
        ):
            incoming[dependent].discard(key)
            if (
                not incoming[dependent]
                and dependent not in ordered
                and dependent not in ready
            ):
                ready.append(dependent)
        ready.sort(key=lambda item: (tasks[item].sort_order, item))
    if len(ordered) != len(tasks):
        raise RelationshipConflict("template dependency graph contains a cycle")
    return tuple(ordered)


def schedule_template(
    project_start: datetime,
    tasks: Iterable[TemplateTask],
    dependencies: Iterable[TemplateDependency],
) -> tuple[ScheduledTask, ...]:
    task_list = tuple(tasks)
    by_key = {task.key: task for task in task_list}
    if len(by_key) != len(task_list):
        raise RelationshipConflict("duplicate template task key")
    dependency_list = tuple(dependencies)
    ordered = _template_order(by_key, dependency_list)
    by_target: dict[str, list[TemplateDependency]] = {}
    for dependency in dependency_list:
        by_target.setdefault(dependency.task_key, []).append(dependency)

    scheduled: dict[str, ScheduledTask] = {}
    for key in ordered:
        task = by_key[key]
        duration = timedelta(hours=task.effort_hours)
        constraints: list[datetime] = []
        for dependency in by_target.get(key, []):
            predecessor = scheduled[dependency.depends_on_key]
            lag = timedelta(days=dependency.lag_days)
            if dependency.dependency_type is DependencyType.FINISH_TO_START:
                constraints.append(predecessor.due_at + lag)
            elif dependency.dependency_type is DependencyType.START_TO_START:
                constraints.append(predecessor.starts_at + lag)
            elif dependency.dependency_type is DependencyType.FINISH_TO_FINISH:
                constraints.append(predecessor.due_at + lag - duration)
            else:
                constraints.append(predecessor.starts_at + lag - duration)
        starts_at = max((project_start, *constraints))
        scheduled[key] = ScheduledTask(
            key=key,
            starts_at=starts_at,
            due_at=starts_at + duration,
        )
    return tuple(scheduled[key] for key in ordered)


__all__ = [
    "InvalidTransition",
    "ProjectLifecycleError",
    "RelationshipConflict",
    "StaleState",
    "schedule_template",
    "transition_project",
    "transition_task",
    "validate_dependency_replacement",
]
