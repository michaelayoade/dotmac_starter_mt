"""Flush-only persistence owner for projects, tasks, and templates."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from dotmac_projects.contracts import (
    DependencySpec,
    DependencyType,
    Priority,
    ProjectCreate,
    ProjectStatus,
    TaskCreate,
    TaskSnapshot,
    TaskStatus,
    TemplateDefinition,
    TemplateDependency,
    TemplateTask,
)
from dotmac_projects.lifecycle import (
    RelationshipConflict,
    StaleState,
    schedule_template,
    transition_project,
    transition_task,
    validate_dependency_replacement,
)
from dotmac_projects.models import (
    Project,
    ProjectTask,
    ProjectTaskAssignee,
    ProjectTaskDependency,
    ProjectTemplate,
    ProjectTemplateTask,
    ProjectTemplateTaskDependency,
)


class ProjectNotFound(LookupError):
    """The requested active aggregate member is absent from this tenant."""


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must not be blank")
    return cleaned


def _project(db: Session, tenant_id: UUID, project_id: UUID) -> Project:
    row = db.scalar(
        select(Project)
        .where(Project.tenant_id == tenant_id, Project.id == project_id)
        .with_for_update()
    )
    if row is None or not row.is_active:
        raise ProjectNotFound("active project not found")
    return row


def _task_scope(
    db: Session, tenant_id: UUID, task_id: UUID
) -> tuple[Project, ProjectTask]:
    project_id = db.scalar(
        select(ProjectTask.project_id).where(
            ProjectTask.tenant_id == tenant_id, ProjectTask.id == task_id
        )
    )
    if project_id is None:
        raise ProjectNotFound("active project task not found")
    project = _project(db, tenant_id, project_id)
    task = db.scalar(
        select(ProjectTask)
        .where(ProjectTask.tenant_id == tenant_id, ProjectTask.id == task_id)
        .with_for_update()
    )
    if task is None or not task.is_active:
        raise ProjectNotFound("active project task not found")
    if task.project_id != project.id:
        raise RelationshipConflict("task project changed while acquiring locks")
    return project, task


def create_project(db: Session, *, tenant_id: UUID, command: ProjectCreate) -> Project:
    row = Project(
        tenant_id=tenant_id,
        code=_clean(command.code, "project code"),
        name=_clean(command.name, "project name"),
        description=command.description,
        status=command.status.value,
        priority=command.priority.value,
        starts_at=command.starts_at,
        due_at=command.due_at,
        created_by_id=command.created_by_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def transition_project_status(
    db: Session,
    *,
    tenant_id: UUID,
    project_id: UUID,
    expected: ProjectStatus,
    requested: ProjectStatus,
    occurred_at: datetime,
) -> Project:
    row = _project(db, tenant_id, project_id)
    row.status = transition_project(
        ProjectStatus(row.status), requested, expected=expected
    ).value
    row.completed_at = occurred_at if requested is ProjectStatus.COMPLETED else None
    db.flush()
    return row


def _validate_parent(
    db: Session,
    *,
    tenant_id: UUID,
    project_id: UUID,
    task_id: UUID | None,
    parent_task_id: UUID,
) -> None:
    visited: set[UUID] = set()
    current_id: UUID | None = parent_task_id
    while current_id is not None:
        if current_id == task_id:
            raise RelationshipConflict("parent relationship would create a cycle")
        if current_id in visited:
            raise RelationshipConflict("existing parent relationship contains a cycle")
        visited.add(current_id)
        parent = db.scalar(
            select(ProjectTask)
            .where(
                ProjectTask.tenant_id == tenant_id,
                ProjectTask.id == current_id,
            )
            .with_for_update()
        )
        if parent is None or not parent.is_active:
            raise ProjectNotFound("active parent task not found")
        if parent.project_id != project_id:
            raise RelationshipConflict("parent task must belong to the same project")
        current_id = parent.parent_task_id


def create_task(db: Session, *, tenant_id: UUID, command: TaskCreate) -> ProjectTask:
    project = _project(db, tenant_id, command.project_id)
    if command.parent_task_id is not None:
        _validate_parent(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            task_id=None,
            parent_task_id=command.parent_task_id,
        )
    if command.effort_hours < 0:
        raise ValueError("task effort_hours must be non-negative")
    row = ProjectTask(
        tenant_id=tenant_id,
        project_id=project.id,
        parent_task_id=command.parent_task_id,
        title=_clean(command.title, "task title"),
        description=command.description,
        status=command.status.value,
        priority=command.priority.value,
        effort_hours=command.effort_hours,
        starts_at=command.starts_at,
        due_at=command.due_at,
        created_by_id=command.created_by_id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def transition_task_status(
    db: Session,
    *,
    tenant_id: UUID,
    task_id: UUID,
    expected: TaskStatus,
    requested: TaskStatus,
    occurred_at: datetime,
) -> ProjectTask:
    project, task = _task_scope(db, tenant_id, task_id)
    blockers = db.scalars(
        select(ProjectTaskDependency.depends_on_task_id)
        .join(
            ProjectTask,
            (
                (ProjectTask.tenant_id == ProjectTaskDependency.tenant_id)
                & (ProjectTask.project_id == ProjectTaskDependency.project_id)
                & (ProjectTask.id == ProjectTaskDependency.depends_on_task_id)
            ),
        )
        .where(
            ProjectTaskDependency.tenant_id == tenant_id,
            ProjectTaskDependency.project_id == project.id,
            ProjectTaskDependency.task_id == task.id,
            (ProjectTask.is_active.is_(False))
            | (ProjectTask.status != TaskStatus.DONE.value),
        )
        .order_by(ProjectTaskDependency.depends_on_task_id)
    ).all()
    task.status = transition_task(
        TaskStatus(task.status),
        requested,
        expected=expected,
        incomplete_dependency_ids=blockers,
    ).value
    task.completed_at = occurred_at if requested is TaskStatus.DONE else None
    db.flush()
    return task


def replace_task_dependencies(
    db: Session,
    *,
    tenant_id: UUID,
    task_id: UUID,
    expected_status: TaskStatus,
    dependencies: Iterable[DependencySpec],
) -> ProjectTask:
    project, task = _task_scope(db, tenant_id, task_id)
    if TaskStatus(task.status) is not expected_status:
        raise StaleState(
            f"task status expected {expected_status.value}, found {task.status}"
        )
    tasks = db.scalars(
        select(ProjectTask)
        .where(
            ProjectTask.tenant_id == tenant_id,
            ProjectTask.project_id == project.id,
        )
        .order_by(ProjectTask.id)
        .with_for_update()
    ).all()
    snapshots = {
        row.id: TaskSnapshot(
            id=row.id,
            project_id=row.project_id,
            status=TaskStatus(row.status),
            active=row.is_active,
        )
        for row in tasks
    }
    rows = db.scalars(
        select(ProjectTaskDependency).where(
            ProjectTaskDependency.tenant_id == tenant_id,
            ProjectTaskDependency.project_id == project.id,
        )
    ).all()
    edges: dict[UUID, set[UUID]] = {}
    for row in rows:
        edges.setdefault(row.task_id, set()).add(row.depends_on_task_id)
    reviewed = validate_dependency_replacement(
        snapshots[task.id],
        dependencies,
        tasks=snapshots,
        existing_edges=edges,
    )
    db.execute(
        delete(ProjectTaskDependency).where(
            ProjectTaskDependency.tenant_id == tenant_id,
            ProjectTaskDependency.task_id == task.id,
        )
    )
    for item in reviewed:
        db.add(
            ProjectTaskDependency(
                tenant_id=tenant_id,
                project_id=project.id,
                task_id=task.id,
                depends_on_task_id=item.depends_on_task_id,
                dependency_type=item.dependency_type.value,
                lag_days=item.lag_days,
            )
        )
    db.flush()
    return task


def replace_task_assignees(
    db: Session,
    *,
    tenant_id: UUID,
    task_id: UUID,
    assignee_ids: Iterable[UUID],
) -> tuple[UUID, ...]:
    project, task = _task_scope(db, tenant_id, task_id)
    reviewed = tuple(dict.fromkeys(assignee_ids))
    db.execute(
        delete(ProjectTaskAssignee).where(
            ProjectTaskAssignee.tenant_id == tenant_id,
            ProjectTaskAssignee.task_id == task.id,
        )
    )
    for assignee_id in reviewed:
        db.add(
            ProjectTaskAssignee(
                tenant_id=tenant_id,
                project_id=project.id,
                task_id=task.id,
                assignee_id=assignee_id,
            )
        )
    db.flush()
    return reviewed


def create_template(
    db: Session, *, tenant_id: UUID, definition: TemplateDefinition
) -> ProjectTemplate:
    # Validates unique keys, references and cycles before any row is staged.
    schedule_template(
        datetime(2000, 1, 1, tzinfo=UTC), definition.tasks, definition.dependencies
    )
    template = ProjectTemplate(
        tenant_id=tenant_id,
        code=_clean(definition.code, "template code"),
        name=_clean(definition.name, "template name"),
        description=definition.description,
        is_active=True,
    )
    db.add(template)
    db.flush()
    by_key: dict[str, ProjectTemplateTask] = {}
    for task_definition in definition.tasks:
        row = ProjectTemplateTask(
            tenant_id=tenant_id,
            template_id=template.id,
            key=task_definition.key,
            title=task_definition.title,
            description=task_definition.description,
            status=task_definition.status.value,
            priority=task_definition.priority.value,
            sort_order=task_definition.sort_order,
            effort_hours=task_definition.effort_hours,
            is_active=True,
        )
        db.add(row)
        db.flush()
        by_key[task_definition.key] = row
    for dependency_definition in definition.dependencies:
        db.add(
            ProjectTemplateTaskDependency(
                tenant_id=tenant_id,
                template_id=template.id,
                template_task_id=by_key[dependency_definition.task_key].id,
                depends_on_template_task_id=by_key[
                    dependency_definition.depends_on_key
                ].id,
                dependency_type=dependency_definition.dependency_type.value,
                lag_days=dependency_definition.lag_days,
            )
        )
    db.flush()
    return template


def instantiate_template(
    db: Session,
    *,
    tenant_id: UUID,
    project_id: UUID,
    template_id: UUID,
    starts_at: datetime | None = None,
) -> tuple[ProjectTask, ...]:
    project = _project(db, tenant_id, project_id)
    template = db.scalar(
        select(ProjectTemplate)
        .where(
            ProjectTemplate.tenant_id == tenant_id,
            ProjectTemplate.id == template_id,
            ProjectTemplate.is_active.is_(True),
        )
        .with_for_update()
    )
    if template is None:
        raise ProjectNotFound("active project template not found")
    template_tasks = db.scalars(
        select(ProjectTemplateTask)
        .where(
            ProjectTemplateTask.tenant_id == tenant_id,
            ProjectTemplateTask.template_id == template.id,
            ProjectTemplateTask.is_active.is_(True),
        )
        .order_by(ProjectTemplateTask.sort_order, ProjectTemplateTask.key)
    ).all()
    dependencies = db.scalars(
        select(ProjectTemplateTaskDependency).where(
            ProjectTemplateTaskDependency.tenant_id == tenant_id,
            ProjectTemplateTaskDependency.template_id == template.id,
        )
    ).all()
    task_by_id = {row.id: row for row in template_tasks}
    task_values = tuple(
        TemplateTask(
            key=row.key,
            title=row.title,
            effort_hours=row.effort_hours,
            sort_order=row.sort_order,
            description=row.description,
            status=TaskStatus(row.status),
            priority=Priority(row.priority),
        )
        for row in template_tasks
    )
    dependency_values = tuple(
        TemplateDependency(
            task_key=task_by_id[row.template_task_id].key,
            depends_on_key=task_by_id[row.depends_on_template_task_id].key,
            dependency_type=DependencyType(row.dependency_type),
            lag_days=row.lag_days,
        )
        for row in dependencies
    )
    schedule_start = starts_at or project.starts_at
    if schedule_start is None:
        raise RelationshipConflict(
            "template instantiation needs an explicit or project start time"
        )
    schedule = {
        row.key: row
        for row in schedule_template(schedule_start, task_values, dependency_values)
    }
    created_by_key: dict[str, ProjectTask] = {}
    for source in template_tasks:
        timing = schedule[source.key]
        task = ProjectTask(
            tenant_id=tenant_id,
            project_id=project.id,
            template_task_id=source.id,
            title=source.title,
            description=source.description,
            status=source.status,
            priority=source.priority,
            effort_hours=source.effort_hours,
            starts_at=timing.starts_at,
            due_at=timing.due_at,
            is_active=True,
        )
        db.add(task)
        db.flush()
        created_by_key[source.key] = task
    for item in dependency_values:
        db.add(
            ProjectTaskDependency(
                tenant_id=tenant_id,
                project_id=project.id,
                task_id=created_by_key[item.task_key].id,
                depends_on_task_id=created_by_key[item.depends_on_key].id,
                dependency_type=item.dependency_type.value,
                lag_days=item.lag_days,
            )
        )
    db.flush()
    return tuple(created_by_key[row.key] for row in template_tasks)


__all__ = [
    "ProjectNotFound",
    "create_project",
    "create_task",
    "create_template",
    "instantiate_template",
    "replace_task_assignees",
    "replace_task_dependencies",
    "transition_project_status",
    "transition_task_status",
]
