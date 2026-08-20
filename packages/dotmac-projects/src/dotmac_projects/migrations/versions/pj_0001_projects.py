"""Create the tenant-only projects owner in ``mod_projects``.

Revision ID: pj_0001_projects
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "pj_0001_projects"
down_revision = None
branch_labels = ("projects",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_projects"


def _timestamps() -> tuple[sa.Column[Any], sa.Column[Any]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["public.tenants.id"],
        name=name,
        ondelete="CASCADE",
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_projects;")
    op.execute("GRANT USAGE ON SCHEMA mod_projects TO app_user, platform_api;")

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("fk_projects_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_projects_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_projects_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_projects_tenant_status",
        "projects",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "project_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("fk_project_templates_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_project_templates_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_project_templates_tenant_code"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "project_template_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effort_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("fk_project_template_tasks_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            [
                "mod_projects.project_templates.tenant_id",
                "mod_projects.project_templates.id",
            ],
            name="fk_project_template_tasks_template",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_project_template_tasks_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "template_id",
            "id",
            name="uq_project_template_tasks_template_id_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "template_id",
            "key",
            name="uq_project_template_tasks_key",
        ),
        sa.CheckConstraint(
            "effort_hours >= 0", name="ck_project_template_tasks_effort_nonnegative"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_project_template_tasks_order",
        "project_template_tasks",
        ["tenant_id", "template_id", "sort_order"],
        schema=_SCHEMA,
    )

    op.create_table(
        "project_template_task_dependencies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("template_task_id", sa.Uuid(), nullable=False),
        sa.Column("depends_on_template_task_id", sa.Uuid(), nullable=False),
        sa.Column("dependency_type", sa.String(24), nullable=False),
        sa.Column("lag_days", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        _tenant_fk("fk_template_task_dependencies_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "template_id", "template_task_id"],
            [
                "mod_projects.project_template_tasks.tenant_id",
                "mod_projects.project_template_tasks.template_id",
                "mod_projects.project_template_tasks.id",
            ],
            name="fk_template_dependencies_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "template_id", "depends_on_template_task_id"],
            [
                "mod_projects.project_template_tasks.tenant_id",
                "mod_projects.project_template_tasks.template_id",
                "mod_projects.project_template_tasks.id",
            ],
            name="fk_template_dependencies_predecessor",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "template_id",
            "template_task_id",
            "depends_on_template_task_id",
            name="uq_template_task_dependencies_edge",
        ),
        sa.CheckConstraint(
            "template_task_id <> depends_on_template_task_id",
            name="ck_template_task_dependencies_no_self",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "project_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("parent_task_id", sa.Uuid(), nullable=True),
        sa.Column("template_task_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("effort_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("fk_project_tasks_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["mod_projects.projects.tenant_id", "mod_projects.projects.id"],
            name="fk_project_tasks_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "parent_task_id"],
            [
                "mod_projects.project_tasks.tenant_id",
                "mod_projects.project_tasks.project_id",
                "mod_projects.project_tasks.id",
            ],
            name="fk_project_tasks_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "template_task_id"],
            [
                "mod_projects.project_template_tasks.tenant_id",
                "mod_projects.project_template_tasks.id",
            ],
            name="fk_project_tasks_template_task",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_project_tasks_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_project_tasks_project_id_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "template_task_id",
            name="uq_project_tasks_template_instance",
        ),
        sa.CheckConstraint(
            "parent_task_id IS NULL OR parent_task_id <> id",
            name="ck_project_tasks_no_self_parent",
        ),
        sa.CheckConstraint(
            "effort_hours >= 0", name="ck_project_tasks_effort_nonnegative"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_project_tasks_status",
        "project_tasks",
        ["tenant_id", "project_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "project_task_dependencies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("depends_on_task_id", sa.Uuid(), nullable=False),
        sa.Column("dependency_type", sa.String(24), nullable=False),
        sa.Column("lag_days", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        _tenant_fk("fk_project_task_dependencies_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "task_id"],
            [
                "mod_projects.project_tasks.tenant_id",
                "mod_projects.project_tasks.project_id",
                "mod_projects.project_tasks.id",
            ],
            name="fk_task_dependencies_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "depends_on_task_id"],
            [
                "mod_projects.project_tasks.tenant_id",
                "mod_projects.project_tasks.project_id",
                "mod_projects.project_tasks.id",
            ],
            name="fk_task_dependencies_predecessor",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "task_id",
            "depends_on_task_id",
            name="uq_project_task_dependencies_edge",
        ),
        sa.CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_project_task_dependencies_no_self",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "project_task_assignees",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        _tenant_fk("fk_project_task_assignees_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "task_id"],
            [
                "mod_projects.project_tasks.tenant_id",
                "mod_projects.project_tasks.project_id",
                "mod_projects.project_tasks.id",
            ],
            name="fk_project_task_assignees_task",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "task_id",
            "assignee_id",
            name="uq_project_task_assignees_member",
        ),
        schema=_SCHEMA,
    )

    op.execute("ALTER TABLE mod_projects.projects ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_projects.projects FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY projects_tenant_isolation ON mod_projects.projects
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.projects TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.projects TO platform_api;"
    )

    op.execute("ALTER TABLE mod_projects.project_templates ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_projects.project_templates FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY project_templates_tenant_isolation
            ON mod_projects.project_templates
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_templates TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_templates TO platform_api;"
    )

    op.execute(
        "ALTER TABLE mod_projects.project_template_tasks ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_projects.project_template_tasks FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY project_template_tasks_tenant_isolation
            ON mod_projects.project_template_tasks
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_template_tasks TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_template_tasks TO platform_api;"
    )

    op.execute(
        "ALTER TABLE mod_projects.project_template_task_dependencies ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_projects.project_template_task_dependencies FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY project_template_task_dependencies_tenant_isolation
            ON mod_projects.project_template_task_dependencies
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_template_task_dependencies TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_template_task_dependencies TO platform_api;"
    )

    op.execute("ALTER TABLE mod_projects.project_tasks ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_projects.project_tasks FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY project_tasks_tenant_isolation ON mod_projects.project_tasks
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_tasks TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_tasks TO platform_api;"
    )

    op.execute(
        "ALTER TABLE mod_projects.project_task_dependencies ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_projects.project_task_dependencies FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY project_task_dependencies_tenant_isolation
            ON mod_projects.project_task_dependencies
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_task_dependencies TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_task_dependencies TO platform_api;"
    )

    op.execute(
        "ALTER TABLE mod_projects.project_task_assignees ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_projects.project_task_assignees FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY project_task_assignees_tenant_isolation
            ON mod_projects.project_task_assignees
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_task_assignees TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_projects.project_task_assignees TO platform_api;"
    )


def downgrade() -> None:
    op.drop_table("project_task_assignees", schema=_SCHEMA)
    op.drop_table("project_task_dependencies", schema=_SCHEMA)
    op.drop_index("ix_project_tasks_status", table_name="project_tasks", schema=_SCHEMA)
    op.drop_table("project_tasks", schema=_SCHEMA)
    op.drop_table("project_template_task_dependencies", schema=_SCHEMA)
    op.drop_index(
        "ix_project_template_tasks_order",
        table_name="project_template_tasks",
        schema=_SCHEMA,
    )
    op.drop_table("project_template_tasks", schema=_SCHEMA)
    op.drop_table("project_templates", schema=_SCHEMA)
    op.drop_index("ix_projects_tenant_status", table_name="projects", schema=_SCHEMA)
    op.drop_table("projects", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_projects RESTRICT;")
