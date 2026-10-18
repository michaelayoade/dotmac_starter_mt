"""Academy 096 actual delivery/support DDL, with Kernel person translation.

No backfill, cohort delivery_mode rewrite, or invented logical-reference FKs.
The preceding revision verifies the independently owned tenant audit provider.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    resolve_depends_on,
)
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "lr_0006_delivery_support"
down_revision = "lr_0005_tenant_audit"
branch_labels = None
REQUIRES = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    PARTY_PERSON_CATALOG_V1.name,
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_learning"
_TABLES = (
    "offering_activities",
    "class_sessions",
    "announcements",
    "success_queue_entries",
)


def _ts() -> list[sa.Column[Any]]:
    return [
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
    ]


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.create_table(
        "offering_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("offering_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_offering_activities_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "offering_id",
            "activity_id",
            name="uq_offering_activities_offering_activity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            [
                "mod_learning.course_offerings.tenant_id",
                "mod_learning.course_offerings.id",
            ],
            ondelete="CASCADE",
            name="fk_offering_activities_tenant_offering",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "activity_id"],
            ["mod_learning.activities.tenant_id", "mod_learning.activities.id"],
            ondelete="CASCADE",
            name="fk_offering_activities_tenant_activity",
        ),
        schema=_SCHEMA,
    )
    for c in ("tenant_id", "offering_id", "activity_id"):
        op.create_index(
            f"ix_offering_activities_{c}", "offering_activities", [c], schema=_SCHEMA
        )
    op.create_table(
        "class_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offering_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "session_type", sa.String(20), nullable=False, server_default="live_class"
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instructor_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("location", sa.String(160), nullable=True),
        sa.Column("join_url", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
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
        sa.UniqueConstraint("tenant_id", "id", name="uq_class_sessions_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_class_sessions_tenant_cohort",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_class_sessions_tenant_id", "class_sessions", ["tenant_id"], schema=_SCHEMA
    )
    op.create_index(
        "ix_class_sessions_cohort_start",
        "class_sessions",
        ["tenant_id", "cohort_id", "starts_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_class_sessions_instructor",
        "class_sessions",
        ["tenant_id", "instructor_person_id"],
        schema=_SCHEMA,
    )
    op.create_table(
        "announcements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("author_person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        *_ts(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_announcements_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_announcements_tenant_cohort",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "author_person_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="CASCADE",
            name="fk_announcements_tenant_author",
        ),
        sa.ForeignKeyConstraint(
            ["author_person_id"],
            ["public.party_persons.party_id"],
            ondelete="CASCADE",
            name="fk_announcements_party_person",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_announcements_tenant_id", "announcements", ["tenant_id"], schema=_SCHEMA
    )
    op.create_index(
        "ix_announcements_cohort_id", "announcements", ["cohort_id"], schema=_SCHEMA
    )
    op.create_table(
        "success_queue_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_kind", sa.String(30), nullable=False),
        sa.Column(
            "supporting_facts",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("severity", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommended_action", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(15), nullable=False, server_default="open"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("score_hint", sa.Float, nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="CASCADE",
            name="fk_success_queue_tenant_person",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_success_queue_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["public.party_persons.party_id"],
            ondelete="CASCADE",
            name="fk_success_queue_party_person",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_success_queue_tenant_id",
        "success_queue_entries",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_success_queue_person_id",
        "success_queue_entries",
        ["person_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_success_queue_cohort_id",
        "success_queue_entries",
        ["cohort_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_success_queue_status",
        "success_queue_entries",
        ["tenant_id", "status", "severity"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_success_queue_open_person_reason",
        "success_queue_entries",
        ["tenant_id", "person_id", "reason_kind"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
        schema=_SCHEMA,
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_learning.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE mod_learning.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_learning.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id())"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_learning.{table} TO app_user, platform_api"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
