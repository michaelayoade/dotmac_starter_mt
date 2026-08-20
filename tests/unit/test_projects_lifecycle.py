"""Behavior canaries for the product-neutral project lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_projects import (
    DependencySpec,
    DependencyType,
    InvalidTransition,
    ProjectStatus,
    RelationshipConflict,
    StaleState,
    TaskSnapshot,
    TaskStatus,
    TemplateDependency,
    TemplateTask,
    schedule_template,
    transition_project,
    transition_task,
    validate_dependency_replacement,
)


def test_a_terminal_project_cannot_be_reopened() -> None:
    with pytest.raises(InvalidTransition, match="terminal"):
        transition_project(
            ProjectStatus.COMPLETED,
            ProjectStatus.ACTIVE,
            expected=ProjectStatus.COMPLETED,
        )


def test_a_stale_project_transition_is_refused() -> None:
    with pytest.raises(StaleState, match="expected planned"):
        transition_project(
            ProjectStatus.ACTIVE,
            ProjectStatus.ON_HOLD,
            expected=ProjectStatus.PLANNED,
        )


def test_a_task_cannot_finish_before_its_dependencies() -> None:
    blocker = uuid4()
    with pytest.raises(RelationshipConflict) as exc:
        transition_task(
            TaskStatus.IN_PROGRESS,
            TaskStatus.DONE,
            expected=TaskStatus.IN_PROGRESS,
            incomplete_dependency_ids=(blocker,),
        )
    assert exc.value.related_ids == (blocker,)


def test_a_non_completion_transition_does_not_consult_dependency_state() -> None:
    assert (
        transition_task(
            TaskStatus.TODO,
            TaskStatus.BLOCKED,
            expected=TaskStatus.TODO,
            incomplete_dependency_ids=(uuid4(),),
        )
        is TaskStatus.BLOCKED
    )


def test_dependency_replacement_rejects_cross_project_edges() -> None:
    project = uuid4()
    other_project = uuid4()
    task = TaskSnapshot(uuid4(), project, TaskStatus.TODO)
    foreign = TaskSnapshot(uuid4(), other_project, TaskStatus.TODO)

    with pytest.raises(RelationshipConflict, match="same project"):
        validate_dependency_replacement(
            task,
            (DependencySpec(foreign.id),),
            tasks={foreign.id: foreign},
            existing_edges={},
        )


def test_dependency_replacement_rejects_a_cycle() -> None:
    project = uuid4()
    first = TaskSnapshot(uuid4(), project, TaskStatus.TODO)
    second = TaskSnapshot(uuid4(), project, TaskStatus.TODO)

    with pytest.raises(RelationshipConflict, match="cycle"):
        validate_dependency_replacement(
            first,
            (DependencySpec(second.id),),
            tasks={first.id: first, second.id: second},
            existing_edges={second.id: frozenset({first.id})},
        )


def test_dependency_replacement_rejects_duplicates_before_persistence() -> None:
    project = uuid4()
    task = TaskSnapshot(uuid4(), project, TaskStatus.TODO)
    predecessor = TaskSnapshot(uuid4(), project, TaskStatus.DONE)
    edge = DependencySpec(predecessor.id)

    with pytest.raises(RelationshipConflict, match="duplicate"):
        validate_dependency_replacement(
            task,
            (edge, edge),
            tasks={predecessor.id: predecessor},
            existing_edges={},
        )


def test_template_schedule_honours_all_dependency_types_and_lag() -> None:
    start = datetime(2026, 8, 18, 8, tzinfo=UTC)
    tasks = (
        TemplateTask("survey", "Survey", effort_hours=4, sort_order=1),
        TemplateTask("build", "Build", effort_hours=8, sort_order=2),
        TemplateTask("inspect", "Inspect", effort_hours=2, sort_order=3),
        TemplateTask("handover", "Handover", effort_hours=1, sort_order=4),
        TemplateTask("archive", "Archive", effort_hours=1, sort_order=5),
    )
    dependencies = (
        TemplateDependency(
            "build", "survey", DependencyType.FINISH_TO_START, lag_days=1
        ),
        TemplateDependency("inspect", "build", DependencyType.START_TO_START),
        TemplateDependency("handover", "build", DependencyType.FINISH_TO_FINISH),
        TemplateDependency("archive", "handover", DependencyType.START_TO_FINISH),
    )

    scheduled = {
        item.key: item for item in schedule_template(start, tasks, dependencies)
    }

    assert scheduled["survey"].starts_at == start
    assert scheduled["survey"].due_at == start + timedelta(hours=4)
    assert scheduled["build"].starts_at == start + timedelta(hours=4, days=1)
    assert scheduled["inspect"].starts_at == scheduled["build"].starts_at
    assert scheduled["handover"].due_at == scheduled["build"].due_at
    assert scheduled["archive"].due_at == scheduled["handover"].starts_at


def test_a_dependency_constraint_never_schedules_before_the_project_start() -> None:
    start = datetime(2026, 8, 18, 8, tzinfo=UTC)
    tasks = (
        TemplateTask("first", "First", effort_hours=1),
        TemplateTask("long", "Long", effort_hours=8),
    )

    scheduled = {
        item.key: item
        for item in schedule_template(
            start,
            tasks,
            (TemplateDependency("long", "first", DependencyType.START_TO_FINISH),),
        )
    }

    assert scheduled["long"].starts_at == start


def test_template_schedule_rejects_an_unknown_task_and_a_cycle() -> None:
    start = datetime(2026, 8, 18, 8, tzinfo=UTC)
    tasks = (
        TemplateTask("a", "A", effort_hours=1),
        TemplateTask("b", "B", effort_hours=1),
    )

    with pytest.raises(RelationshipConflict, match="unknown"):
        schedule_template(
            start,
            tasks,
            (TemplateDependency("a", "missing"),),
        )

    with pytest.raises(RelationshipConflict, match="cycle"):
        schedule_template(
            start,
            tasks,
            (TemplateDependency("a", "b"), TemplateDependency("b", "a")),
        )
