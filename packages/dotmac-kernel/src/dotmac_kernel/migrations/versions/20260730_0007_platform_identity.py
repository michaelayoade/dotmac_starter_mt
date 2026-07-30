"""Platform-admin identity tables (control-plane security Task 1)

Creates `platform_admins` + `platform_sessions` — the platform control
plane's OWN identity tables, deliberately NOT tenant `Party`/`AuthSession`
rows (those are tenant-bound by design: composite FKs, tenant-claim JWT).

These are PLATFORM catalog tables (like `tenants`/`tenant_domains`): no
`tenant_id`, no RLS. The grant model is the load-bearing part and lands in
the SAME migration as the tables:

- GRANT to `platform_api` (the online platform role) and `app_admin` only.
- REVOKE ALL from `app_user`: a tenant-scoped application session must not
  even be able to SELECT a platform credential row. `app_user` never
  received a grant on these tables, but the explicit REVOKE both documents
  the boundary and stays correct if a later blanket grant appears.

Revision ID: 0007_platform_identity
Revises: 0006_display_setting_domain
Create Date: 2026-07-30

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_platform_identity"
down_revision = "0006_display_setting_domain"
branch_labels = None
depends_on = None

_PLATFORM_TABLES = ("platform_admins", "platform_sessions")


def upgrade() -> None:
    op.create_table(
        "platform_admins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
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
    # Case-insensitive uniqueness, mirroring the `parties` email semantics.
    op.create_index(
        "uq_platform_admins_lower_email",
        "platform_admins",
        [sa.text("lower(email)")],
        unique=True,
    )

    op.create_table(
        "platform_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "admin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_admins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
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
        sa.UniqueConstraint("token_hash", name="uq_platform_sessions_token_hash"),
    )
    op.create_index("ix_platform_sessions_admin_id", "platform_sessions", ["admin_id"])

    tables = ", ".join(_PLATFORM_TABLES)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO platform_api;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO app_admin;")
    op.execute(f"REVOKE ALL ON {tables} FROM app_user;")


def downgrade() -> None:
    tables = ", ".join(_PLATFORM_TABLES)
    op.execute(f"REVOKE ALL ON {tables} FROM platform_api, app_admin;")
    op.drop_index("ix_platform_sessions_admin_id", table_name="platform_sessions")
    op.drop_table("platform_sessions")
    op.drop_index("uq_platform_admins_lower_email", table_name="platform_admins")
    op.drop_table("platform_admins")
