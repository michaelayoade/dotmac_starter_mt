"""Create tenant remote-access request, grant and observation evidence.

All tenant tables carry ``UNIQUE (tenant_id, id)`` for composite references.
Revision ID: ra_0001_remote_access
Revises: (lineage root)
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ra_0001_remote_access"
down_revision = None
branch_labels = ("remote_access",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_remoteaccess"


def _tenant_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    ]


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_remoteaccess;")
    op.execute("GRANT USAGE ON SCHEMA mod_remoteaccess TO app_user, app_admin;")
    op.create_table(
        "remote_access_requests",
        *_tenant_columns(),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("target_ref", sa.String(240), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("requester_ref", sa.String(200), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_remote_requests_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "request_key", name="uq_remote_requests_key"),
        schema=_SCHEMA,
    )
    op.create_table(
        "remote_access_grants",
        *_tenant_columns(),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_ref", sa.String(240), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
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
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                "mod_remoteaccess.remote_access_requests.tenant_id",
                "mod_remoteaccess.remote_access_requests.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_remote_grants_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "request_id", name="uq_remote_grants_request"),
        schema=_SCHEMA,
    )
    op.create_table(
        "remote_access_observations",
        *_tenant_columns(),
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_key", sa.String(200), nullable=False),
        sa.Column("observation_digest", sa.String(64), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "grant_id"],
            [
                "mod_remoteaccess.remote_access_grants.tenant_id",
                "mod_remoteaccess.remote_access_grants.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_remote_observations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "observation_key", name="uq_remote_observations_key"
        ),
        schema=_SCHEMA,
    )
    for table in (
        "remote_access_requests",
        "remote_access_grants",
        "remote_access_observations",
    ):
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {_SCHEMA}.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.{table} TO app_user, app_admin;"
        )


def downgrade() -> None:
    for table in (
        "remote_access_observations",
        "remote_access_grants",
        "remote_access_requests",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_remoteaccess;")
