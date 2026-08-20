"""Create the tenant procurement decision plane.

Revision ID: pc_0001_procurement
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "pc_0001_procurement"
down_revision = None
branch_labels = ("procurement",)

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_procurement"


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


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_procurement;")
    op.execute("REVOKE ALL ON SCHEMA mod_procurement FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_procurement TO app_user, app_admin;")

    op.create_table(
        "purchase_requisitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requisition_number", sa.String(80), nullable=False),
        sa.Column("requested_on", sa.Date(), nullable=False),
        sa.Column("requester_ref", sa.String(255), nullable=False),
        sa.Column("created_by_ref", sa.String(255), nullable=False),
        sa.Column("urgency", sa.String(40), nullable=False, server_default="normal"),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("total_estimated_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("source_owner", sa.String(120), nullable=True),
        sa.Column("source_event_id", sa.String(255), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("budget_authorization_ref", sa.String(255), nullable=True),
        sa.Column("budget_authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_decision_ref", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_purchase_requisitions_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_purchase_requisitions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "requisition_number",
            name="uq_purchase_requisitions_tenant_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_purchase_requisitions_source",
        ),
        sa.CheckConstraint(
            "status IN ('draft','submitted','budget_verified','approved','rejected','cancelled','sourced')",
            name="procurement_requisition_status",
        ),
        sa.CheckConstraint(
            "total_estimated_amount >= 0",
            name="ck_purchase_requisitions_total_non_negative",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_purchase_requisitions_tenant_status",
        "purchase_requisitions",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "purchase_requisition_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requisition_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("estimated_unit_cost", sa.Numeric(20, 6), nullable=False),
        sa.Column("estimated_total", sa.Numeric(20, 6), nullable=False),
        sa.Column("item_ref", sa.String(255), nullable=True),
        sa.Column("expense_ref", sa.String(255), nullable=True),
        sa.Column("cost_center_ref", sa.String(255), nullable=True),
        sa.Column("subject_ref", sa.String(255), nullable=True),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requisition_id"],
            [
                "mod_procurement.purchase_requisitions.tenant_id",
                "mod_procurement.purchase_requisitions.id",
            ],
            ondelete="CASCADE",
            name="fk_purchase_requisition_lines_tenant_requisition",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_purchase_requisition_lines_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "requisition_id",
            "line_number",
            name="uq_purchase_requisition_lines_position",
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_requisition_lines_quantity_positive"
        ),
        sa.CheckConstraint(
            "estimated_unit_cost >= 0",
            name="ck_requisition_lines_unit_cost_non_negative",
        ),
        sa.CheckConstraint(
            "estimated_total >= 0", name="ck_requisition_lines_total_non_negative"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "sourcing_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_number", sa.String(80), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("criteria_json", sa.Text(), nullable=False),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("estimated_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("source_requisition_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_ref", sa.String(255), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_sourcing_events_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_requisition_id"],
            [
                "mod_procurement.purchase_requisitions.tenant_id",
                "mod_procurement.purchase_requisitions.id",
            ],
            ondelete="RESTRICT",
            name="fk_sourcing_events_tenant_requisition",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sourcing_events_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "event_number", name="uq_sourcing_events_tenant_number"
        ),
        sa.CheckConstraint(
            "method IN ('direct','selective','open_competitive')",
            name="procurement_sourcing_method",
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','closed','evaluated','awarded','cancelled')",
            name="procurement_sourcing_status",
        ),
        sa.CheckConstraint("closes_at > opens_at", name="ck_sourcing_events_window"),
        sa.CheckConstraint(
            "estimated_amount >= 0", name="ck_sourcing_events_estimate_non_negative"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sourcing_events_tenant_status",
        "sourcing_events",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "sourcing_event_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("source_requisition_line_id", sa.Uuid(), nullable=True),
        sa.Column("item_ref", sa.String(255), nullable=True),
        sa.Column("target_unit_cost", sa.Numeric(20, 6), nullable=True),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [
                "mod_procurement.sourcing_events.tenant_id",
                "mod_procurement.sourcing_events.id",
            ],
            ondelete="CASCADE",
            name="fk_sourcing_event_lines_tenant_event",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_requisition_line_id"],
            [
                "mod_procurement.purchase_requisition_lines.tenant_id",
                "mod_procurement.purchase_requisition_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_sourcing_event_lines_tenant_requisition_line",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_sourcing_event_lines_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "event_id",
            "id",
            name="uq_sourcing_event_lines_tenant_event_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "event_id",
            "line_number",
            name="uq_sourcing_event_lines_position",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_sourcing_event_lines_quantity"),
        sa.CheckConstraint(
            "target_unit_cost IS NULL OR target_unit_cost >= 0",
            name="ck_sourcing_event_lines_target_cost",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "sourcing_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_ref", sa.String(255), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by_ref", sa.String(255), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [
                "mod_procurement.sourcing_events.tenant_id",
                "mod_procurement.sourcing_events.id",
            ],
            ondelete="CASCADE",
            name="fk_sourcing_invitations_tenant_event",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_sourcing_invitations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "event_id",
            "supplier_ref",
            name="uq_sourcing_invitations_supplier",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "bid_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("response_number", sa.String(80), nullable=False),
        sa.Column("supplier_ref", sa.String(255), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("total_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("validity_days", sa.Integer(), nullable=True),
        sa.Column("delivery_period_days", sa.Integer(), nullable=True),
        sa.Column("technical_proposal", sa.Text(), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(255), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [
                "mod_procurement.sourcing_events.tenant_id",
                "mod_procurement.sourcing_events.id",
            ],
            ondelete="CASCADE",
            name="fk_bid_submissions_tenant_event",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bid_submissions_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "event_id", "id", name="uq_bid_submissions_tenant_event_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "event_id",
            "supplier_ref",
            name="uq_bid_submissions_supplier_event",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_bid_submissions_source",
        ),
        sa.CheckConstraint(
            "status IN ('draft','submitted','under_evaluation','selected','rejected')",
            name="procurement_bid_status",
        ),
        sa.CheckConstraint("total_amount >= 0", name="ck_bid_submissions_total"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_bid_submissions_tenant_event",
        "bid_submissions",
        ["tenant_id", "event_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "bid_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("bid_id", sa.Uuid(), nullable=False),
        sa.Column("sourcing_line_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("line_total", sa.Numeric(20, 6), nullable=False),
        sa.Column("promised_delivery_date", sa.Date(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id", "bid_id"],
            [
                "mod_procurement.bid_submissions.tenant_id",
                "mod_procurement.bid_submissions.event_id",
                "mod_procurement.bid_submissions.id",
            ],
            ondelete="CASCADE",
            name="fk_bid_lines_tenant_event_bid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id", "sourcing_line_id"],
            [
                "mod_procurement.sourcing_event_lines.tenant_id",
                "mod_procurement.sourcing_event_lines.event_id",
                "mod_procurement.sourcing_event_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_bid_lines_tenant_event_sourcing_line",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bid_lines_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "bid_id", "line_number", name="uq_bid_lines_position"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_bid_lines_quantity"),
        sa.CheckConstraint("unit_price >= 0", name="ck_bid_lines_unit_price"),
        sa.CheckConstraint("line_total >= 0", name="ck_bid_lines_total"),
        schema=_SCHEMA,
    )

    op.create_table(
        "bid_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("selected_bid_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="completed"),
        sa.Column("scores_json", sa.Text(), nullable=False),
        sa.Column("selected_total_score", sa.Numeric(10, 4), nullable=False),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("evaluated_by_ref", sa.String(255), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("approval_decision_ref", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [
                "mod_procurement.sourcing_events.tenant_id",
                "mod_procurement.sourcing_events.id",
            ],
            ondelete="CASCADE",
            name="fk_bid_evaluations_tenant_event",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id", "selected_bid_id"],
            [
                "mod_procurement.bid_submissions.tenant_id",
                "mod_procurement.bid_submissions.event_id",
                "mod_procurement.bid_submissions.id",
            ],
            ondelete="RESTRICT",
            name="fk_bid_evaluations_tenant_event_bid",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bid_evaluations_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "event_id", name="uq_bid_evaluations_tenant_event"
        ),
        sa.CheckConstraint(
            "status IN ('completed','approved')",
            name="procurement_evaluation_status",
        ),
        sa.CheckConstraint(
            "selected_total_score >= 0 AND selected_total_score <= 100",
            name="ck_bid_evaluations_score",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(80), nullable=False),
        sa.Column("supplier_ref", sa.String(255), nullable=False),
        sa.Column("ordered_on", sa.Date(), nullable=False),
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("subtotal", sa.Numeric(20, 6), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("total_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("source_requisition_id", sa.Uuid(), nullable=True),
        sa.Column("source_evaluation_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_ref", sa.String(255), nullable=False),
        sa.Column("ship_to_ref", sa.String(255), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_decision_ref", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_purchase_orders_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_requisition_id"],
            [
                "mod_procurement.purchase_requisitions.tenant_id",
                "mod_procurement.purchase_requisitions.id",
            ],
            ondelete="RESTRICT",
            name="fk_purchase_orders_tenant_requisition",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_evaluation_id"],
            [
                "mod_procurement.bid_evaluations.tenant_id",
                "mod_procurement.bid_evaluations.id",
            ],
            ondelete="RESTRICT",
            name="fk_purchase_orders_tenant_evaluation",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_purchase_orders_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "order_number", name="uq_purchase_orders_tenant_number"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_requisition_id",
            name="uq_purchase_orders_tenant_requisition_source",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_evaluation_id",
            name="uq_purchase_orders_tenant_evaluation_source",
        ),
        sa.CheckConstraint(
            "status IN ('draft','pending_approval','approved','partially_received','received','cancelled','closed')",
            name="procurement_purchase_order_status",
        ),
        sa.CheckConstraint(
            "subtotal >= 0 AND tax_amount >= 0 AND total_amount >= 0",
            name="ck_purchase_orders_totals",
        ),
        sa.CheckConstraint(
            "source_requisition_id IS NOT NULL OR source_evaluation_id IS NOT NULL",
            name="ck_purchase_orders_source_required",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_purchase_orders_tenant_status",
        "purchase_orders",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "purchase_order_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity_ordered", sa.Numeric(20, 6), nullable=False),
        sa.Column(
            "quantity_received", sa.Numeric(20, 6), nullable=False, server_default="0"
        ),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("line_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("item_ref", sa.String(255), nullable=True),
        sa.Column("expense_ref", sa.String(255), nullable=True),
        sa.Column("asset_ref", sa.String(255), nullable=True),
        sa.Column("cost_center_ref", sa.String(255), nullable=True),
        sa.Column("subject_ref", sa.String(255), nullable=True),
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            [
                "mod_procurement.purchase_orders.tenant_id",
                "mod_procurement.purchase_orders.id",
            ],
            ondelete="CASCADE",
            name="fk_purchase_order_lines_tenant_order",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_purchase_order_lines_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "order_id",
            "line_number",
            name="uq_purchase_order_lines_position",
        ),
        sa.CheckConstraint(
            "quantity_ordered > 0", name="ck_purchase_order_lines_quantity"
        ),
        sa.CheckConstraint(
            "quantity_received >= 0 AND quantity_received <= quantity_ordered",
            name="ck_purchase_order_lines_received",
        ),
        sa.CheckConstraint(
            "unit_price >= 0 AND line_amount >= 0 AND tax_amount >= 0",
            name="ck_purchase_order_lines_totals",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "receipt_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(255), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("lines_json", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            [
                "mod_procurement.purchase_orders.tenant_id",
                "mod_procurement.purchase_orders.id",
            ],
            ondelete="CASCADE",
            name="fk_receipt_observations_tenant_order",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_receipt_observations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_receipt_observations_source",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "procurement_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_kind", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=True),
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_procurement_evidence_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_procurement_evidence_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "aggregate_kind",
            "aggregate_id",
            "sequence",
            name="uq_procurement_evidence_sequence",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_procurement_evidence_sequence"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_procurement_evidence_aggregate",
        "procurement_evidence",
        ["tenant_id", "aggregate_kind", "aggregate_id"],
        schema=_SCHEMA,
    )

    _install_immutability_triggers()
    _install_rls_and_grants()


def _install_immutability_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_requisition_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
              RAISE EXCEPTION 'submitted requisition is immutable';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.status <> 'draft' AND (
             NEW.id IS DISTINCT FROM OLD.id OR
             NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
             NEW.requisition_number IS DISTINCT FROM OLD.requisition_number OR
             NEW.requested_on IS DISTINCT FROM OLD.requested_on OR
             NEW.requester_ref IS DISTINCT FROM OLD.requester_ref OR
             NEW.created_by_ref IS DISTINCT FROM OLD.created_by_ref OR
             NEW.urgency IS DISTINCT FROM OLD.urgency OR
             NEW.justification IS DISTINCT FROM OLD.justification OR
             NEW.currency_code IS DISTINCT FROM OLD.currency_code OR
             NEW.total_estimated_amount IS DISTINCT FROM OLD.total_estimated_amount OR
             NEW.source_owner IS DISTINCT FROM OLD.source_owner OR
             NEW.source_event_id IS DISTINCT FROM OLD.source_event_id OR
             NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 OR
             NEW.submitted_at IS DISTINCT FROM OLD.submitted_at OR
             NEW.created_at IS DISTINCT FROM OLD.created_at
          ) THEN
            RAISE EXCEPTION 'submitted requisition content is immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER purchase_requisitions_snapshot_immutable
        BEFORE UPDATE OR DELETE ON mod_procurement.purchase_requisitions
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_requisition_snapshot();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_requisition_line()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          old_parent_status text;
          new_parent_status text;
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT status INTO old_parent_status
            FROM mod_procurement.purchase_requisitions
            WHERE tenant_id = OLD.tenant_id AND id = OLD.requisition_id;
            IF old_parent_status IS NOT NULL AND old_parent_status <> 'draft' THEN
              RAISE EXCEPTION 'submitted requisition lines are immutable';
            END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          SELECT status INTO new_parent_status
          FROM mod_procurement.purchase_requisitions
          WHERE tenant_id = NEW.tenant_id AND id = NEW.requisition_id;
          IF new_parent_status IS NOT NULL AND new_parent_status <> 'draft' THEN
            RAISE EXCEPTION 'submitted requisition lines are immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER purchase_requisition_lines_snapshot_immutable
        BEFORE INSERT OR UPDATE OR DELETE
        ON mod_procurement.purchase_requisition_lines
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_requisition_line();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_sourcing_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
              RAISE EXCEPTION 'published sourcing event is immutable';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.status <> 'draft' AND (
             NEW.id IS DISTINCT FROM OLD.id OR
             NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
             NEW.event_number IS DISTINCT FROM OLD.event_number OR
             NEW.title IS DISTINCT FROM OLD.title OR
             NEW.method IS DISTINCT FROM OLD.method OR
             NEW.opens_at IS DISTINCT FROM OLD.opens_at OR
             NEW.closes_at IS DISTINCT FROM OLD.closes_at OR
             NEW.currency_code IS DISTINCT FROM OLD.currency_code OR
             NEW.criteria_json IS DISTINCT FROM OLD.criteria_json OR
             NEW.terms IS DISTINCT FROM OLD.terms OR
             NEW.estimated_amount IS DISTINCT FROM OLD.estimated_amount OR
             NEW.source_requisition_id IS DISTINCT FROM OLD.source_requisition_id OR
             NEW.created_by_ref IS DISTINCT FROM OLD.created_by_ref OR
             NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 OR
             NEW.published_at IS DISTINCT FROM OLD.published_at OR
             NEW.created_at IS DISTINCT FROM OLD.created_at
          ) THEN
            RAISE EXCEPTION 'published sourcing snapshot is immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER sourcing_events_snapshot_immutable
        BEFORE UPDATE OR DELETE ON mod_procurement.sourcing_events
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_sourcing_snapshot();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_sourcing_line()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          old_parent_status text;
          new_parent_status text;
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT status INTO old_parent_status FROM mod_procurement.sourcing_events
            WHERE tenant_id = OLD.tenant_id AND id = OLD.event_id;
            IF old_parent_status IS NOT NULL AND old_parent_status <> 'draft' THEN
              RAISE EXCEPTION 'published sourcing lines are immutable';
            END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          SELECT status INTO new_parent_status FROM mod_procurement.sourcing_events
          WHERE tenant_id = NEW.tenant_id AND id = NEW.event_id;
          IF new_parent_status IS NOT NULL AND new_parent_status <> 'draft' THEN
            RAISE EXCEPTION 'published sourcing lines are immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER sourcing_event_lines_snapshot_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON mod_procurement.sourcing_event_lines
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_sourcing_line();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_sourcing_invitation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          old_parent_status text;
          new_parent_status text;
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT status INTO old_parent_status FROM mod_procurement.sourcing_events
            WHERE tenant_id = OLD.tenant_id AND id = OLD.event_id;
            IF old_parent_status IS NOT NULL AND old_parent_status <> 'draft' THEN
              RAISE EXCEPTION 'published sourcing invitations are immutable';
            END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          SELECT status INTO new_parent_status FROM mod_procurement.sourcing_events
          WHERE tenant_id = NEW.tenant_id AND id = NEW.event_id;
          IF new_parent_status IS NOT NULL AND new_parent_status <> 'draft' THEN
            RAISE EXCEPTION 'published sourcing invitations are immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER sourcing_invitations_snapshot_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON mod_procurement.sourcing_invitations
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_sourcing_invitation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_submitted_bid()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
              RAISE EXCEPTION 'submitted bid is immutable';
            END IF;
            RETURN OLD;
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.status <> 'draft' AND (
             NEW.id IS DISTINCT FROM OLD.id OR
             NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
             NEW.event_id IS DISTINCT FROM OLD.event_id OR
             NEW.response_number IS DISTINCT FROM OLD.response_number OR
             NEW.supplier_ref IS DISTINCT FROM OLD.supplier_ref OR
             NEW.received_at IS DISTINCT FROM OLD.received_at OR
             NEW.currency_code IS DISTINCT FROM OLD.currency_code OR
             NEW.total_amount IS DISTINCT FROM OLD.total_amount OR
             NEW.validity_days IS DISTINCT FROM OLD.validity_days OR
             NEW.delivery_period_days IS DISTINCT FROM OLD.delivery_period_days OR
             NEW.technical_proposal IS DISTINCT FROM OLD.technical_proposal OR
             NEW.terms IS DISTINCT FROM OLD.terms OR
             NEW.source_owner IS DISTINCT FROM OLD.source_owner OR
             NEW.source_event_id IS DISTINCT FROM OLD.source_event_id OR
             NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 OR
             NEW.submitted_at IS DISTINCT FROM OLD.submitted_at OR
             NEW.created_at IS DISTINCT FROM OLD.created_at
          ) THEN
            RAISE EXCEPTION 'submitted bid content is immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER bid_submissions_immutable_after_submit
        BEFORE UPDATE OR DELETE ON mod_procurement.bid_submissions
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_submitted_bid();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_bid_line()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          old_parent_status text;
          new_parent_status text;
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT status INTO old_parent_status FROM mod_procurement.bid_submissions
            WHERE tenant_id = OLD.tenant_id AND id = OLD.bid_id;
            IF old_parent_status IS NOT NULL AND old_parent_status <> 'draft' THEN
              RAISE EXCEPTION 'submitted bid lines are immutable';
            END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          SELECT status INTO new_parent_status FROM mod_procurement.bid_submissions
          WHERE tenant_id = NEW.tenant_id AND id = NEW.bid_id;
          IF new_parent_status IS NOT NULL AND new_parent_status <> 'draft' THEN
            RAISE EXCEPTION 'submitted bid lines are immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER bid_lines_immutable_after_submit
        BEFORE INSERT OR UPDATE OR DELETE ON mod_procurement.bid_lines
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_bid_line();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_bid_evaluation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'bid evaluation evidence is immutable';
          END IF;
          IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
             NEW.id IS DISTINCT FROM OLD.id OR
             NEW.event_id IS DISTINCT FROM OLD.event_id OR
             NEW.selected_bid_id IS DISTINCT FROM OLD.selected_bid_id OR
             NEW.scores_json IS DISTINCT FROM OLD.scores_json OR
             NEW.selected_total_score IS DISTINCT FROM OLD.selected_total_score OR
             NEW.report IS DISTINCT FROM OLD.report OR
             NEW.evaluated_by_ref IS DISTINCT FROM OLD.evaluated_by_ref OR
             NEW.evaluated_at IS DISTINCT FROM OLD.evaluated_at OR
             NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 OR
             NEW.created_at IS DISTINCT FROM OLD.created_at
          THEN
            RAISE EXCEPTION 'bid evaluation content is immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER bid_evaluations_content_immutable
        BEFORE UPDATE OR DELETE ON mod_procurement.bid_evaluations
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_bid_evaluation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_purchase_order_snapshot()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
              RAISE EXCEPTION 'submitted purchase order is immutable';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.status <> 'draft' AND (
             NEW.id IS DISTINCT FROM OLD.id OR
             NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
             NEW.order_number IS DISTINCT FROM OLD.order_number OR
             NEW.supplier_ref IS DISTINCT FROM OLD.supplier_ref OR
             NEW.ordered_on IS DISTINCT FROM OLD.ordered_on OR
             NEW.expected_delivery_date IS DISTINCT FROM OLD.expected_delivery_date OR
             NEW.currency_code IS DISTINCT FROM OLD.currency_code OR
             NEW.subtotal IS DISTINCT FROM OLD.subtotal OR
             NEW.tax_amount IS DISTINCT FROM OLD.tax_amount OR
             NEW.total_amount IS DISTINCT FROM OLD.total_amount OR
             NEW.source_requisition_id IS DISTINCT FROM OLD.source_requisition_id OR
             NEW.source_evaluation_id IS DISTINCT FROM OLD.source_evaluation_id OR
             NEW.created_by_ref IS DISTINCT FROM OLD.created_by_ref OR
             NEW.ship_to_ref IS DISTINCT FROM OLD.ship_to_ref OR
             NEW.terms IS DISTINCT FROM OLD.terms OR
             NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 OR
             NEW.submitted_at IS DISTINCT FROM OLD.submitted_at OR
             NEW.created_at IS DISTINCT FROM OLD.created_at
          ) THEN
            RAISE EXCEPTION 'submitted purchase order is immutable';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER purchase_orders_snapshot_immutable
        BEFORE UPDATE OR DELETE ON mod_procurement.purchase_orders
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_purchase_order_snapshot();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_purchase_order_line()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          old_parent_status text;
          new_parent_status text;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            SELECT status INTO old_parent_status FROM mod_procurement.purchase_orders
            WHERE tenant_id = OLD.tenant_id AND id = OLD.order_id;
            IF old_parent_status IS NOT NULL AND old_parent_status <> 'draft' THEN
              RAISE EXCEPTION 'submitted purchase order lines are immutable';
            END IF;
            RETURN OLD;
          END IF;
          IF TG_OP = 'INSERT' THEN
            SELECT status INTO new_parent_status FROM mod_procurement.purchase_orders
            WHERE tenant_id = NEW.tenant_id AND id = NEW.order_id;
            IF new_parent_status IS NOT NULL AND new_parent_status <> 'draft' THEN
              RAISE EXCEPTION 'submitted purchase order lines are immutable';
            END IF;
            RETURN NEW;
          END IF;
          SELECT status INTO old_parent_status FROM mod_procurement.purchase_orders
          WHERE tenant_id = OLD.tenant_id AND id = OLD.order_id;
          IF old_parent_status IS NOT NULL AND old_parent_status <> 'draft' AND (
             NEW.id IS DISTINCT FROM OLD.id OR
             NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
             NEW.order_id IS DISTINCT FROM OLD.order_id OR
             NEW.line_number IS DISTINCT FROM OLD.line_number OR
             NEW.description IS DISTINCT FROM OLD.description OR
             NEW.quantity_ordered IS DISTINCT FROM OLD.quantity_ordered OR
             NEW.unit IS DISTINCT FROM OLD.unit OR
             NEW.unit_price IS DISTINCT FROM OLD.unit_price OR
             NEW.line_amount IS DISTINCT FROM OLD.line_amount OR
             NEW.tax_amount IS DISTINCT FROM OLD.tax_amount OR
             NEW.item_ref IS DISTINCT FROM OLD.item_ref OR
             NEW.expense_ref IS DISTINCT FROM OLD.expense_ref OR
             NEW.asset_ref IS DISTINCT FROM OLD.asset_ref OR
             NEW.cost_center_ref IS DISTINCT FROM OLD.cost_center_ref OR
             NEW.subject_ref IS DISTINCT FROM OLD.subject_ref OR
             NEW.expected_delivery_date IS DISTINCT FROM OLD.expected_delivery_date
          ) THEN
            RAISE EXCEPTION 'submitted purchase order lines are immutable';
          END IF;
          IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
             NEW.order_id IS DISTINCT FROM OLD.order_id
          THEN
            SELECT status INTO new_parent_status FROM mod_procurement.purchase_orders
            WHERE tenant_id = NEW.tenant_id AND id = NEW.order_id;
            IF new_parent_status IS NOT NULL AND new_parent_status <> 'draft' THEN
              RAISE EXCEPTION 'submitted purchase order lines are immutable';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER purchase_order_lines_snapshot_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON mod_procurement.purchase_order_lines
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_purchase_order_line();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_receipt_observation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'receipt observations are append-only';
        END;
        $$;
        CREATE TRIGGER receipt_observations_append_only
        BEFORE UPDATE OR DELETE ON mod_procurement.receipt_observations
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_receipt_observation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_procurement.protect_procurement_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'procurement evidence is append-only';
        END;
        $$;
        CREATE TRIGGER procurement_evidence_append_only
        BEFORE UPDATE OR DELETE ON mod_procurement.procurement_evidence
        FOR EACH ROW EXECUTE FUNCTION mod_procurement.protect_procurement_evidence();
        """
    )


def _install_rls_and_grants() -> None:
    op.execute(
        "ALTER TABLE mod_procurement.purchase_requisitions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.purchase_requisitions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY purchase_requisitions_tenant_isolation ON mod_procurement.purchase_requisitions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.purchase_requisitions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.purchase_requisition_lines ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.purchase_requisition_lines FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY purchase_requisition_lines_tenant_isolation ON mod_procurement.purchase_requisition_lines USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.purchase_requisition_lines TO app_user;"
    )
    op.execute("ALTER TABLE mod_procurement.sourcing_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_procurement.sourcing_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY sourcing_events_tenant_isolation ON mod_procurement.sourcing_events USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.sourcing_events TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.sourcing_event_lines ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.sourcing_event_lines FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY sourcing_event_lines_tenant_isolation ON mod_procurement.sourcing_event_lines USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.sourcing_event_lines TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.sourcing_invitations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.sourcing_invitations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY sourcing_invitations_tenant_isolation ON mod_procurement.sourcing_invitations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.sourcing_invitations TO app_user;"
    )
    op.execute("ALTER TABLE mod_procurement.bid_submissions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_procurement.bid_submissions FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY bid_submissions_tenant_isolation ON mod_procurement.bid_submissions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.bid_submissions TO app_user;"
    )
    op.execute("ALTER TABLE mod_procurement.bid_lines ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_procurement.bid_lines FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY bid_lines_tenant_isolation ON mod_procurement.bid_lines USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.bid_lines TO app_user;"
    )
    op.execute("ALTER TABLE mod_procurement.bid_evaluations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_procurement.bid_evaluations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY bid_evaluations_tenant_isolation ON mod_procurement.bid_evaluations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.bid_evaluations TO app_user;"
    )
    op.execute("ALTER TABLE mod_procurement.purchase_orders ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_procurement.purchase_orders FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY purchase_orders_tenant_isolation ON mod_procurement.purchase_orders USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.purchase_orders TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.purchase_order_lines ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.purchase_order_lines FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY purchase_order_lines_tenant_isolation ON mod_procurement.purchase_order_lines USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.purchase_order_lines TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.receipt_observations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.receipt_observations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY receipt_observations_tenant_isolation ON mod_procurement.receipt_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.receipt_observations TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.procurement_evidence ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_procurement.procurement_evidence FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY procurement_evidence_tenant_isolation ON mod_procurement.procurement_evidence USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_procurement.procurement_evidence TO app_user;"
    )
    op.execute(
        "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA mod_procurement TO app_admin;"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA mod_procurement CASCADE;")
