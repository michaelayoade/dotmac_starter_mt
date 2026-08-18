"""Tenant-scoped persistence for the reusable projects aggregate."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("projects")


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_projects_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_projects_tenant_code"),
        Index("ix_projects_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProjectTemplate(Base, TimestampMixin):
    __tablename__ = "project_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_project_templates_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_project_templates_tenant_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProjectTemplateTask(Base, TimestampMixin):
    __tablename__ = "project_template_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            [
                f"{SCHEMA}.project_templates.tenant_id",
                f"{SCHEMA}.project_templates.id",
            ],
            name="fk_project_template_tasks_template",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_project_template_tasks_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "template_id",
            "id",
            name="uq_project_template_tasks_template_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "template_id",
            "key",
            name="uq_project_template_tasks_key",
        ),
        Index(
            "ix_project_template_tasks_order", "tenant_id", "template_id", "sort_order"
        ),
        CheckConstraint(
            "effort_hours >= 0", name="ck_project_template_tasks_effort_nonnegative"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effort_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProjectTemplateTaskDependency(Base, TimestampMixin):
    __tablename__ = "project_template_task_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "template_id", "template_task_id"],
            [
                f"{SCHEMA}.project_template_tasks.tenant_id",
                f"{SCHEMA}.project_template_tasks.template_id",
                f"{SCHEMA}.project_template_tasks.id",
            ],
            name="fk_template_dependencies_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_id", "depends_on_template_task_id"],
            [
                f"{SCHEMA}.project_template_tasks.tenant_id",
                f"{SCHEMA}.project_template_tasks.template_id",
                f"{SCHEMA}.project_template_tasks.id",
            ],
            name="fk_template_dependencies_predecessor",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "template_id",
            "template_task_id",
            "depends_on_template_task_id",
            name="uq_template_task_dependencies_edge",
        ),
        CheckConstraint(
            "template_task_id <> depends_on_template_task_id",
            name="ck_template_task_dependencies_no_self",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    template_task_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    depends_on_template_task_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    dependency_type: Mapped[str] = mapped_column(String(24), nullable=False)
    lag_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProjectTask(Base, TimestampMixin):
    __tablename__ = "project_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            [f"{SCHEMA}.projects.tenant_id", f"{SCHEMA}.projects.id"],
            name="fk_project_tasks_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "parent_task_id"],
            [
                f"{SCHEMA}.project_tasks.tenant_id",
                f"{SCHEMA}.project_tasks.project_id",
                f"{SCHEMA}.project_tasks.id",
            ],
            name="fk_project_tasks_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_task_id"],
            [
                f"{SCHEMA}.project_template_tasks.tenant_id",
                f"{SCHEMA}.project_template_tasks.id",
            ],
            name="fk_project_tasks_template_task",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_project_tasks_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_project_tasks_project_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "template_task_id",
            name="uq_project_tasks_template_instance",
        ),
        Index("ix_project_tasks_status", "tenant_id", "project_id", "status"),
        CheckConstraint(
            "parent_task_id IS NULL OR parent_task_id <> id",
            name="ck_project_tasks_no_self_parent",
        ),
        CheckConstraint(
            "effort_hours >= 0", name="ck_project_tasks_effort_nonnegative"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    parent_task_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    template_task_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    effort_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProjectTaskDependency(Base, TimestampMixin):
    __tablename__ = "project_task_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "task_id"],
            [
                f"{SCHEMA}.project_tasks.tenant_id",
                f"{SCHEMA}.project_tasks.project_id",
                f"{SCHEMA}.project_tasks.id",
            ],
            name="fk_task_dependencies_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "depends_on_task_id"],
            [
                f"{SCHEMA}.project_tasks.tenant_id",
                f"{SCHEMA}.project_tasks.project_id",
                f"{SCHEMA}.project_tasks.id",
            ],
            name="fk_task_dependencies_predecessor",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "task_id",
            "depends_on_task_id",
            name="uq_project_task_dependencies_edge",
        ),
        CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_project_task_dependencies_no_self",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    depends_on_task_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    dependency_type: Mapped[str] = mapped_column(String(24), nullable=False)
    lag_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProjectTaskAssignee(Base, TimestampMixin):
    __tablename__ = "project_task_assignees"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "task_id"],
            [
                f"{SCHEMA}.project_tasks.tenant_id",
                f"{SCHEMA}.project_tasks.project_id",
                f"{SCHEMA}.project_tasks.id",
            ],
            name="fk_project_task_assignees_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "task_id",
            "assignee_id",
            name="uq_project_task_assignees_member",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    assignee_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


TENANT_MODELS = (
    Project,
    ProjectTemplate,
    ProjectTemplateTask,
    ProjectTemplateTaskDependency,
    ProjectTask,
    ProjectTaskDependency,
    ProjectTaskAssignee,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "Project",
    "ProjectTask",
    "ProjectTaskAssignee",
    "ProjectTaskDependency",
    "ProjectTemplate",
    "ProjectTemplateTask",
    "ProjectTemplateTaskDependency",
]
