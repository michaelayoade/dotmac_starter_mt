"""Create the tenant publication lifecycle plane.

Revision ID: pb_0001_publishing
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "pb_0001_publishing"
down_revision = None
branch_labels = ("publishing",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
    "outbox_relay.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_publishing"


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
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


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_publishing;")
    op.execute("REVOKE ALL ON SCHEMA mod_publishing FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_publishing TO app_user, app_admin;")

    op.create_table(
        "publication_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("requested_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="scheduled"),
        sa.Column("timer_generation", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_publication_releases_tenant",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_publication_releases_request_fingerprint",
        ),
        sa.CheckConstraint(
            "length(snapshot_digest) = 64",
            name="ck_publication_releases_snapshot_digest",
        ),
        sa.CheckConstraint(
            "state IN ('scheduled', 'dispatching', 'partial', 'published', "
            "'failed', 'cancelled')",
            name="ck_publication_releases_state",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_publication_releases_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "request_key",
            name="uq_publication_releases_tenant_request_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_publication_releases_tenant_state",
        "publication_releases",
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_publication_releases_tenant_requested",
        "publication_releases",
        ["tenant_id", "requested_for"],
        schema=_SCHEMA,
    )

    op.create_table(
        "publication_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("publication_release_id", sa.Uuid(), nullable=False),
        sa.Column("target_ref", sa.String(255), nullable=False),
        sa.Column("target_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("variant_key", sa.String(120), nullable=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("remote_ref", sa.String(500), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_publication_deliveries_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "publication_release_id"],
            [
                "mod_publishing.publication_releases.tenant_id",
                "mod_publishing.publication_releases.id",
            ],
            ondelete="CASCADE",
            name="fk_publication_deliveries_tenant_release",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'intent_published', 'accepted', 'published', "
            "'failed', 'cancelled')",
            name="ck_publication_deliveries_state",
        ),
        sa.CheckConstraint(
            "target_order >= 0", name="ck_publication_deliveries_target_order"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_publication_deliveries_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "publication_release_id",
            "target_ref",
            name="uq_publication_deliveries_tenant_release_target",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "publication_release_id",
            "target_order",
            name="uq_publication_deliveries_tenant_release_order",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_publication_deliveries_tenant_release",
        "publication_deliveries",
        ["tenant_id", "publication_release_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_publication_deliveries_tenant_state",
        "publication_deliveries",
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "publication_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("publication_delivery_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("outbox_event_ref", sa.String(36), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_publication_attempts_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "publication_delivery_id"],
            [
                "mod_publishing.publication_deliveries.tenant_id",
                "mod_publishing.publication_deliveries.id",
            ],
            ondelete="CASCADE",
            name="fk_publication_attempts_tenant_delivery",
        ),
        sa.CheckConstraint(
            "attempt_number > 0", name="ck_publication_attempts_positive_number"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'intent_published', 'accepted', 'published', "
            "'failed', 'cancelled')",
            name="ck_publication_attempts_state",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_publication_attempts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "publication_delivery_id",
            "attempt_number",
            name="uq_publication_attempts_tenant_delivery_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "outbox_event_ref",
            name="uq_publication_attempts_tenant_outbox_ref",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_publication_attempts_tenant_delivery",
        "publication_attempts",
        ["tenant_id", "publication_delivery_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "publication_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("publication_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_ref", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("remote_ref", sa.String(500), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_publication_observations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "publication_attempt_id"],
            [
                "mod_publishing.publication_attempts.tenant_id",
                "mod_publishing.publication_attempts.id",
            ],
            ondelete="CASCADE",
            name="fk_publication_observations_tenant_attempt",
        ),
        sa.CheckConstraint(
            "length(fingerprint) = 64",
            name="ck_publication_observations_fingerprint",
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'published', 'failed', 'cancelled')",
            name="ck_publication_observations_outcome",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_publication_observations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "receipt_ref",
            name="uq_publication_observations_tenant_receipt_ref",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_publication_observations_tenant_attempt",
        "publication_observations",
        ["tenant_id", "publication_attempt_id"],
        schema=_SCHEMA,
    )

    op.execute(
        "ALTER TABLE mod_publishing.publication_releases ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_publishing.publication_releases FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY publication_releases_tenant_isolation "
        "ON mod_publishing.publication_releases "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_publishing.publication_releases TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_publishing.publication_deliveries ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_publishing.publication_deliveries FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY publication_deliveries_tenant_isolation "
        "ON mod_publishing.publication_deliveries "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_publishing.publication_deliveries TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_publishing.publication_attempts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_publishing.publication_attempts FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY publication_attempts_tenant_isolation "
        "ON mod_publishing.publication_attempts "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_publishing.publication_attempts TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_publishing.publication_observations "
        "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_publishing.publication_observations "
        "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY publication_observations_tenant_isolation "
        "ON mod_publishing.publication_observations "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_publishing.publication_observations TO app_user;"
    )


def downgrade() -> None:
    for table in (
        "publication_observations",
        "publication_attempts",
        "publication_deliveries",
        "publication_releases",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_publishing;")
