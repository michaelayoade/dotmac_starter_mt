"""Create service-change requests and append-only checkpoints.

Revision ID: sch_0001_service_changes
Revises: (lineage root)
Create Date: 2026-08-22
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "sch_0001_service_changes"
down_revision = None
branch_labels = ("service_changes",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_servicechanges"

_CHANGE_TYPES = ("PLAN_CHANGE", "RELOCATION", "VACATION_HOLD", "VACATION_RESUME")
_STATUSES = ("PENDING", "APPROVED", "REJECTED", "APPLIED", "CANCELLED")
_EXECUTION_STATES = (
    "AWAITING_PAYMENT",
    "PAYMENT_SETTLED",
    "FULFILLMENT_RELEASED",
    "DELIVERY_IN_PROGRESS",
    "DELIVERY_VERIFIED",
    "COMPLETED",
    "FAILED",
)
_DOMAINS = (
    "QUALIFICATION",
    "BILLING",
    "PAYMENT",
    "FULFILLMENT",
    "SERVICE_ORDER",
    "WORK_ORDER",
    "SERVICE_ACCESS",
)
_TENANT_TABLES = ("service_change_requests", "service_change_checkpoints")


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


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_servicechanges;")
    op.execute("REVOKE ALL ON SCHEMA mod_servicechanges FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_servicechanges TO app_user, app_admin;")

    op.create_table(
        "service_change_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_reference", sa.String(160), nullable=False),
        sa.Column("confirmation_key", sa.String(240), nullable=False),
        sa.Column("change_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("execution_state", sa.String(24), nullable=True),
        sa.Column("current_offer_reference", sa.String(160), nullable=True),
        sa.Column("requested_offer_reference", sa.String(160), nullable=True),
        sa.Column("target_location_reference", sa.String(160), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(160), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_change_requests_tenant",
        ),
        sa.CheckConstraint(
            _in_list("change_type", _CHANGE_TYPES),
            name="ck_service_change_requests_change_type",
        ),
        sa.CheckConstraint(
            _in_list("status", _STATUSES), name="ck_service_change_requests_status"
        ),
        sa.CheckConstraint(
            "execution_state IS NULL OR "
            + _in_list("execution_state", _EXECUTION_STATES),
            name="ck_service_change_requests_execution_state",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_service_change_requests_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "confirmation_key",
            name="uq_service_change_requests_tenant_confirmation",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_change_requests_tenant_subject",
        "service_change_requests",
        ["tenant_id", "subject_reference"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_change_requests_tenant_status",
        "service_change_requests",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "service_change_checkpoints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(20), nullable=False),
        sa.Column("evidence_reference", sa.String(160), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_change_checkpoints_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                "mod_servicechanges.service_change_requests.tenant_id",
                "mod_servicechanges.service_change_requests.id",
            ],
            ondelete="CASCADE",
            name="fk_service_change_checkpoints_tenant_request",
        ),
        sa.CheckConstraint(
            _in_list("domain", _DOMAINS), name="ck_service_change_checkpoints_domain"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_service_change_checkpoints_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "request_id",
            "domain",
            "evidence_reference",
            name="uq_service_change_checkpoints_tenant_request_domain_evidence",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_change_checkpoints_tenant_request_observed",
        "service_change_checkpoints",
        ["tenant_id", "request_id", "observed_at"],
        schema=_SCHEMA,
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_servicechanges.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_servicechanges.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_servicechanges.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON "
            f"mod_servicechanges.{table} TO app_user;"
        )


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_servicechanges;")
