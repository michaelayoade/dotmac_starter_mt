"""Fresh Academy 096 attempts/grants DDL; no legacy duplicate-closing backfill."""

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
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "lr_0004_assessment"
down_revision = "lr_0003_participation"
branch_labels = None
REQUIRES = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    PARTY_PERSON_CATALOG_V1.name,
)
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_learning"
_TABLES = ("activity_attempts", "attempt_grants")


def _identity() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("activity_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
    )


def _timestamps() -> tuple[sa.Column[Any], ...]:
    return tuple(
        sa.Column(
            name,
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        )
        for name in ("created_at", "updated_at")
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.create_table(
        "activity_attempts",
        *_identity(),
        sa.Column(
            "question_ext_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_activity_attempts_tenant_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "activity_id"],
            ["mod_learning.activities.tenant_id", "mod_learning.activities.id"],
            ondelete="CASCADE",
            name="fk_activity_attempts_tenant_activity",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_activity_attempts_open",
        "activity_attempts",
        ["tenant_id", "activity_id", "person_id"],
        unique=True,
        postgresql_where=sa.text("submitted_at IS NULL"),
        schema=_SCHEMA,
    )
    op.create_table(
        "attempt_grants",
        *_identity(),
        sa.Column("extra_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_attempt_grants_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "activity_id"],
            ["mod_learning.activities.tenant_id", "mod_learning.activities.id"],
            ondelete="CASCADE",
            name="fk_attempt_grants_tenant_activity",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_attempt_grants_lookup",
        "attempt_grants",
        ["tenant_id", "activity_id", "person_id"],
        schema=_SCHEMA,
    )
    for table in _TABLES:
        for column in ("tenant_id", "activity_id", "person_id"):
            op.create_index(f"ix_{table}_{column}", table, [column], schema=_SCHEMA)
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
