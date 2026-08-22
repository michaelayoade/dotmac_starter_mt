"""Create service-delivery orders and append-only readiness evidence.

Revision ID: so_0001_service_delivery_orders
Revises: (lineage root)
Create Date: 2026-08-22
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "so_0001_service_delivery_orders"
down_revision = None
branch_labels = ("service_orders",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_serviceorders"

_ORDER_TYPES = (
    "NEW_INSTALL",
    "UPGRADE",
    "DOWNGRADE",
    "DISCONNECT",
    "RECONNECT",
    "CHANGE_SERVICE",
)
_ORDER_STATUSES = (
    "DRAFT",
    "SUBMITTED",
    "IN_DELIVERY",
    "ACTIVATED",
    "CANCELLED",
    "FAILED",
)
_DECISION_STATUSES = ("BLOCKED", "ACTIVATION_REQUESTED", "ACTIVATED", "FAILED")
_CHECK_KINDS = (
    "DELIVERY_RUN",
    "DELIVERY_PLAN_BINDING",
    "ACTIVATION_TASK",
    "FIELD_WORK",
    "ACCESS_ASSIGNMENT",
)
_CHECK_RESULTS = ("PASSED", "FAILED", "NOT_APPLICABLE")

_TENANT_TABLES = (
    "service_orders",
    "service_order_readiness_decisions",
    "service_order_readiness_checks",
)


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_serviceorders;")
    op.execute("REVOKE ALL ON SCHEMA mod_serviceorders FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_serviceorders TO app_user, app_admin;")

    op.create_table(
        "service_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("customer_reference", sa.String(160), nullable=False),
        sa.Column("commercial_order_reference", sa.String(160), nullable=True),
        sa.Column("specification_reference", sa.String(160), nullable=True),
        sa.Column("service_reference", sa.String(160), nullable=True),
        sa.Column("request_key", sa.String(240), nullable=False),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_orders_tenant",
        ),
        sa.CheckConstraint(
            _in_list("order_type", _ORDER_TYPES), name="ck_service_orders_order_type"
        ),
        sa.CheckConstraint(
            _in_list("status", _ORDER_STATUSES), name="ck_service_orders_status"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_service_orders_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "request_key", name="uq_service_orders_tenant_request_key"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_orders_tenant_customer",
        "service_orders",
        ["tenant_id", "customer_reference"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_orders_tenant_status",
        "service_orders",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "service_order_readiness_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("service_order_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_readiness_decisions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "service_order_id"],
            [
                "mod_serviceorders.service_orders.tenant_id",
                "mod_serviceorders.service_orders.id",
            ],
            ondelete="CASCADE",
            name="fk_readiness_decisions_tenant_order",
        ),
        sa.CheckConstraint(
            _in_list("status", _DECISION_STATUSES),
            name="ck_readiness_decisions_status",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_readiness_decisions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_readiness_decisions_tenant_command"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_readiness_decisions_tenant_order_decided",
        "service_order_readiness_decisions",
        ["tenant_id", "service_order_id", "decided_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "service_order_readiness_checks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_reference", sa.String(160), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_readiness_checks_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "decision_id"],
            [
                "mod_serviceorders.service_order_readiness_decisions.tenant_id",
                "mod_serviceorders.service_order_readiness_decisions.id",
            ],
            ondelete="CASCADE",
            name="fk_readiness_checks_tenant_decision",
        ),
        sa.CheckConstraint(
            _in_list("kind", _CHECK_KINDS), name="ck_readiness_checks_kind"
        ),
        sa.CheckConstraint(
            _in_list("result", _CHECK_RESULTS), name="ck_readiness_checks_result"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_readiness_checks_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "decision_id",
            "kind",
            name="uq_readiness_checks_tenant_decision_kind",
        ),
        schema=_SCHEMA,
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_serviceorders.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_serviceorders.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_serviceorders.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON "
            f"mod_serviceorders.{table} TO app_user;"
        )


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_serviceorders;")
