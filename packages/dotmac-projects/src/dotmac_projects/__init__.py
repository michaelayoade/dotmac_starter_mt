"""Public contract for the reusable Dotmac projects owner."""

from dotmac_projects.contracts import (
    DependencySpec,
    DependencyType,
    Priority,
    ProjectCreate,
    ProjectStatus,
    ScheduledTask,
    TaskCreate,
    TaskSnapshot,
    TaskStatus,
    TemplateDefinition,
    TemplateDependency,
    TemplateTask,
)
from dotmac_projects.lifecycle import (
    InvalidTransition,
    ProjectLifecycleError,
    RelationshipConflict,
    StaleState,
    schedule_template,
    transition_project,
    transition_task,
    validate_dependency_replacement,
)
from dotmac_projects.manifest import module
from dotmac_projects.migrations import versions_dir
from dotmac_projects.service import (
    ProjectNotFound,
    create_project,
    create_task,
    create_template,
    instantiate_template,
    replace_task_assignees,
    replace_task_dependencies,
    transition_project_status,
    transition_task_status,
)

__version__ = "0.1.0a1"

__all__ = [
    "DependencySpec",
    "DependencyType",
    "InvalidTransition",
    "Priority",
    "ProjectCreate",
    "ProjectLifecycleError",
    "ProjectNotFound",
    "ProjectStatus",
    "RelationshipConflict",
    "ScheduledTask",
    "StaleState",
    "TaskCreate",
    "TaskSnapshot",
    "TaskStatus",
    "TemplateDefinition",
    "TemplateDependency",
    "TemplateTask",
    "create_project",
    "create_task",
    "create_template",
    "instantiate_template",
    "module",
    "replace_task_assignees",
    "replace_task_dependencies",
    "schedule_template",
    "transition_project",
    "transition_project_status",
    "transition_task",
    "transition_task_status",
    "validate_dependency_replacement",
    "versions_dir",
]
