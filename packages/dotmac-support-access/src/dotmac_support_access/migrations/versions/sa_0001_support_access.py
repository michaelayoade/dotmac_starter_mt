"""Create temporary support-access request, grant and event evidence.

Revision ID: sa_0001_support_access
Revises: (lineage root)
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "sa_0001_support_access"
down_revision = None
branch_labels = ("support_access",)
REQUIRES = ("module_database_roles.v1",)
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_supportaccess"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_supportaccess;")
    op.execute("GRANT USAGE ON SCHEMA mod_supportaccess TO platform_api, app_admin;")
    op.create_table(
        "support_access_requests",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("case_ref", sa.String(200), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("target_ref", sa.String(200), nullable=False),
        sa.Column("requester_ref", sa.String(200), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_evidence_ref", sa.String(200)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("request_key", name="uq_support_requests_key"),
        schema=_SCHEMA,
    )
    op.create_table(
        "support_access_grants",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_ref", sa.String(200), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("target_ref", sa.String(200), nullable=False),
        sa.Column("requester_ref", sa.String(200), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("consent_evidence_ref", sa.String(200)),
        sa.Column("break_glass_reason", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["mod_supportaccess.support_access_requests.id"],
            name="fk_support_grants_request",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("request_id", name="uq_support_grants_request"),
        schema=_SCHEMA,
    )
    op.create_table(
        "support_access_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("actor_ref", sa.String(200)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["mod_supportaccess.support_access_requests.id"],
            name="fk_support_events_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["mod_supportaccess.support_access_grants.id"],
            name="fk_support_events_grant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "request_id", "sequence", name="uq_support_events_sequence"
        ),
        schema=_SCHEMA,
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_supportaccess.support_access_requests TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_supportaccess.support_access_grants TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_supportaccess.support_access_events TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA mod_supportaccess TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_supportaccess.support_access_requests FROM app_user;")
    op.execute("REVOKE ALL ON mod_supportaccess.support_access_grants FROM app_user;")
    op.execute("REVOKE ALL ON mod_supportaccess.support_access_events FROM app_user;")


def downgrade() -> None:
    op.drop_table("support_access_events", schema=_SCHEMA)
    op.drop_table("support_access_grants", schema=_SCHEMA)
    op.drop_table("support_access_requests", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_supportaccess;")
