"""Add per-tenant job state (pause flag and last outcome) for the job runner.

Tenant plane only. A platform-only deployment has no tenant to pause a job
for, and the runner refuses platform-plane jobs, so this revision is a no-op
there. The table is tenant-scoped under FORCE RLS in the same revision and the
online role receives SELECT, INSERT and UPDATE only (state rows are never
deleted online; they cascade with the tenant).

Revision ID: dt_0002_job_states
Revises: dt_0001_durable_timers
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "dt_0002_job_states"
down_revision = "dt_0001_durable_timers"
branch_labels = None

MODULE_CODE = "durable_timers"
# The runner's dead-letter and staff commands write tenant audit rows.
TENANT_REQUIRES = ("tenant_audit_log.v1",)
COMMON_REQUIRES: tuple[str, ...] = ()
PLATFORM_REQUIRES: tuple[str, ...] = ()

depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    module=MODULE_CODE,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
)
_SCHEMA = "mod_timers"
_TABLE = "job_states"


def upgrade() -> None:
    if ModulePlane.TENANT not in selected_module_planes(MODULE_CODE):
        return
    require_prerequisites(op.get_bind(), TENANT_REQUIRES)
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("module_code", sa.String(120), nullable=False),
        sa.Column("job_code", sa.String(120), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("paused_by", sa.String(255)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_generation", sa.Integer()),
        sa.Column("last_outcome", sa.String(20)),
        sa.Column("last_detail", sa.String(500)),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("dead_letter_event_id", sa.Uuid()),
        sa.Column("dead_letter_generation", sa.Integer()),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_job_states_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "module_code", "job_code", name="uq_job_states_tenant_job"
        ),
        sa.CheckConstraint(
            "last_outcome IS NULL OR last_outcome IN "
            "('ok', 'failed', 'dead_letter', 'skipped_paused')",
            name="ck_job_states_last_outcome",
        ),
        sa.CheckConstraint("consecutive_failures >= 0", name="ck_job_states_failures"),
        schema=_SCHEMA,
    )
    op.execute(f"ALTER TABLE {_SCHEMA}.{_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_SCHEMA}.{_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_SCHEMA}.{_TABLE}
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(f"REVOKE ALL ON {_SCHEMA}.{_TABLE} FROM PUBLIC;")
    op.execute(f"REVOKE ALL ON {_SCHEMA}.{_TABLE} FROM platform_api;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_SCHEMA}.{_TABLE} TO app_user;")


def downgrade() -> None:
    if ModulePlane.TENANT not in selected_module_planes(MODULE_CODE):
        return
    op.execute(f"DROP TABLE IF EXISTS {_SCHEMA}.{_TABLE} CASCADE;")
