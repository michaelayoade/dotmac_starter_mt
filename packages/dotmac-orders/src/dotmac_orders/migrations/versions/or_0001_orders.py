"""Create the tenant-only Orders lineage with structural snapshot immutability.

Revision ID: or_0001_orders
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "or_0001_orders"
down_revision = None
branch_labels = ("orders",)

COMMON_REQUIRES = (
    "module_database_roles.v1",
    "idempotency_ledger.v1",
    "outbox_relay.v1",
)
TENANT_REQUIRES = ("tenant_scope_catalog.v1", "tenant_audit_log.v1")
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES

depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_orders"
_MONEY = sa.Numeric(20, 6)
_QUANTITY = sa.Numeric(20, 6)
_FX_RATE = sa.Numeric(38, 18)


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _tenant_id() -> sa.Column[Any]:
    return sa.Column("tenant_id", sa.Uuid(), nullable=False)


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["public.tenants.id"],
        name=name,
        ondelete="CASCADE",
    )


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _updated_at() -> sa.Column[Any]:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_orders;")
    op.execute("GRANT USAGE ON SCHEMA mod_orders TO app_user, platform_api, app_admin;")

    op.create_table(
        "orders",
        _id(),
        _tenant_id(),
        sa.Column("order_reference", sa.String(120), nullable=False),
        sa.Column("customer_ref", sa.String(255), nullable=False),
        sa.Column("state", sa.String(80), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("currency_minor_units", sa.Integer(), nullable=False),
        sa.Column("subtotal_amount", _MONEY, nullable=False),
        sa.Column("discount_amount", _MONEY, nullable=False),
        sa.Column("tax_amount", _MONEY, nullable=False),
        sa.Column("total_amount", _MONEY, nullable=False),
        sa.Column("snapshot_fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot_frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("source_version", sa.String(120), nullable=True),
        sa.Column("fx_rate", _FX_RATE, nullable=True),
        sa.Column("fx_base_currency_code", sa.String(3), nullable=True),
        sa.Column("fx_rate_ref", sa.String(255), nullable=True),
        sa.Column("fx_source", sa.String(255), nullable=True),
        sa.Column("fx_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_actor_type", sa.String(32), nullable=False),
        sa.Column("submitted_actor_id", sa.String(120), nullable=True),
        sa.Column("submitted_actor_label", sa.String(160), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_actor_type", sa.String(32), nullable=True),
        sa.Column("accepted_actor_id", sa.String(120), nullable=True),
        sa.Column("accepted_actor_label", sa.String(160), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("covered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_actor_type", sa.String(32), nullable=True),
        sa.Column("cancelled_actor_id", sa.String(120), nullable=True),
        sa.Column("cancelled_actor_label", sa.String(160), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        _tenant_fk("fk_orders_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_orders_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "order_reference", name="uq_orders_tenant_reference"
        ),
        sa.CheckConstraint(
            "currency_code = upper(currency_code)", name="ck_orders_currency_upper"
        ),
        sa.CheckConstraint(
            "currency_minor_units BETWEEN 0 AND 6",
            name="ck_orders_currency_minor_units",
        ),
        sa.CheckConstraint("fx_rate IS NULL OR fx_rate > 0", name="ck_orders_fx_rate"),
        sa.CheckConstraint("subtotal_amount >= 0", name="ck_orders_subtotal"),
        sa.CheckConstraint("discount_amount >= 0", name="ck_orders_discount"),
        sa.CheckConstraint("tax_amount >= 0", name="ck_orders_tax"),
        sa.CheckConstraint("total_amount >= 0", name="ck_orders_total"),
        sa.CheckConstraint(
            "subtotal_amount - discount_amount + tax_amount = total_amount",
            name="ck_orders_totals_balance",
        ),
        sa.CheckConstraint(
            "(fx_rate IS NULL AND fx_base_currency_code IS NULL "
            "AND fx_rate_ref IS NULL AND fx_source IS NULL AND fx_as_of IS NULL) "
            "OR (fx_rate IS NOT NULL AND fx_base_currency_code IS NOT NULL "
            "AND fx_rate_ref IS NOT NULL AND fx_source IS NOT NULL "
            "AND fx_as_of IS NOT NULL)",
            name="ck_orders_fx_snapshot_complete",
        ),
        sa.CheckConstraint(
            "fx_base_currency_code IS NULL "
            "OR (fx_base_currency_code = upper(fx_base_currency_code) "
            "AND fx_base_currency_code <> currency_code)",
            name="ck_orders_fx_pair",
        ),
        sa.CheckConstraint(
            "(source_ref IS NULL AND source_version IS NULL) "
            "OR (source_ref IS NOT NULL AND source_version IS NOT NULL)",
            name="ck_orders_source_provenance_complete",
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL AND accepted_actor_type IS NULL "
            "AND accepted_actor_id IS NULL AND accepted_actor_label IS NULL) "
            "OR (accepted_at IS NOT NULL AND accepted_actor_type IS NOT NULL)",
            name="ck_orders_acceptance_evidence_complete",
        ),
        sa.CheckConstraint(
            "(cancelled_at IS NULL AND cancelled_actor_type IS NULL "
            "AND cancelled_actor_id IS NULL AND cancelled_actor_label IS NULL "
            "AND cancellation_reason IS NULL) "
            "OR (cancelled_at IS NOT NULL AND cancelled_actor_type IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_orders_cancellation_evidence_complete",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_orders_tenant_state", "orders", ["tenant_id", "state"], schema=_SCHEMA
    )
    op.create_index(
        "ix_orders_tenant_customer",
        "orders",
        ["tenant_id", "customer_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "order_line_snapshots",
        _id(),
        _tenant_id(),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("line_key", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", _QUANTITY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("currency_minor_units", sa.Integer(), nullable=False),
        sa.Column("unit_price", _MONEY, nullable=False),
        sa.Column("extended_price", _MONEY, nullable=False),
        sa.Column("discount_amount", _MONEY, nullable=False),
        sa.Column("tax_amount", _MONEY, nullable=False),
        sa.Column("tax_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("line_total", _MONEY, nullable=False),
        sa.Column("price_version_ref", sa.String(255), nullable=False),
        sa.Column("terms_ref", sa.String(255), nullable=False),
        sa.Column("terms_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("specification_ref", sa.String(255), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("source_version", sa.String(120), nullable=True),
        sa.Column("snapshot_fingerprint", sa.String(64), nullable=False),
        _created_at(),
        _tenant_fk("fk_order_line_snapshots_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_order_line_snapshots_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "order_id",
            "id",
            name="uq_order_line_snapshots_tenant_order_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "order_id",
            "line_key",
            name="uq_order_line_snapshots_tenant_order_key",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["mod_orders.orders.tenant_id", "mod_orders.orders.id"],
            name="fk_order_line_snapshots_order",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_order_line_snapshots_quantity"),
        sa.CheckConstraint(
            "currency_code = upper(currency_code)",
            name="ck_order_line_snapshots_currency_upper",
        ),
        sa.CheckConstraint(
            "currency_minor_units BETWEEN 0 AND 6",
            name="ck_order_line_snapshots_currency_minor_units",
        ),
        sa.CheckConstraint(
            "unit_price >= 0", name="ck_order_line_snapshots_unit_price"
        ),
        sa.CheckConstraint(
            "extended_price >= 0",
            name="ck_order_line_snapshots_extended_price",
        ),
        sa.CheckConstraint(
            "discount_amount >= 0", name="ck_order_line_snapshots_discount"
        ),
        sa.CheckConstraint("tax_amount >= 0", name="ck_order_line_snapshots_tax"),
        sa.CheckConstraint("line_total >= 0", name="ck_order_line_snapshots_total"),
        sa.CheckConstraint(
            "extended_price - discount_amount + tax_amount = line_total",
            name="ck_order_line_snapshots_totals_balance",
        ),
        sa.CheckConstraint(
            "(source_ref IS NULL AND source_version IS NULL) "
            "OR (source_ref IS NOT NULL AND source_version IS NOT NULL)",
            name="ck_order_line_snapshots_source_provenance_complete",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_order_line_snapshots_tenant_order",
        "order_line_snapshots",
        ["tenant_id", "order_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "coverage_gates",
        _id(),
        _tenant_id(),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("obligation_count", sa.Integer(), nullable=False),
        sa.Column("resolved_count", sa.Integer(), nullable=False),
        sa.Column("satisfied_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        _tenant_fk("fk_coverage_gates_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_coverage_gates_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "order_id", name="uq_coverage_gates_tenant_order"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["mod_orders.orders.tenant_id", "mod_orders.orders.id"],
            name="fk_coverage_gates_order",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "obligation_count > 0", name="ck_coverage_gates_obligation_count"
        ),
        sa.CheckConstraint(
            "resolved_count >= 0 AND resolved_count <= obligation_count",
            name="ck_coverage_gates_resolved_count",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_coverage_gates_tenant_state",
        "coverage_gates",
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "coverage_obligations",
        _id(),
        _tenant_id(),
        sa.Column("gate_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_ref", sa.String(255), nullable=False),
        _created_at(),
        _tenant_fk("fk_coverage_obligations_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_coverage_obligations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "gate_id",
            "obligation_ref",
            name="uq_coverage_obligations_tenant_gate_ref",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "gate_id"],
            ["mod_orders.coverage_gates.tenant_id", "mod_orders.coverage_gates.id"],
            name="fk_coverage_obligations_gate",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_coverage_obligations_tenant_gate",
        "coverage_obligations",
        ["tenant_id", "gate_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "coverage_resolution_receipts",
        _id(),
        _tenant_id(),
        sa.Column("gate_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_ref", sa.String(255), nullable=False),
        sa.Column("resolution_ref", sa.String(255), nullable=False),
        sa.Column("resolution_kind", sa.String(80), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("source_version", sa.String(120), nullable=False),
        sa.Column("receipt_fingerprint", sa.String(64), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _tenant_fk("fk_coverage_resolution_receipts_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_coverage_resolution_receipts_tenant_id_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "gate_id",
            "obligation_ref",
            name="uq_coverage_resolution_receipts_tenant_gate_obligation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "resolution_ref",
            name="uq_coverage_resolution_receipts_tenant_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "gate_id", "obligation_ref"],
            [
                "mod_orders.coverage_obligations.tenant_id",
                "mod_orders.coverage_obligations.gate_id",
                "mod_orders.coverage_obligations.obligation_ref",
            ],
            name="fk_coverage_resolution_receipts_obligation",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_coverage_resolution_receipts_tenant_gate",
        "coverage_resolution_receipts",
        ["tenant_id", "gate_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "fulfillment_requests",
        _id(),
        _tenant_id(),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("line_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("publication_count", sa.Integer(), nullable=False),
        sa.Column("last_outbox_event_id", sa.Uuid(), nullable=True),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acceptance_ref", sa.String(255), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        _tenant_fk("fk_fulfillment_requests_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_requests_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "order_id",
            "line_snapshot_id",
            name="uq_fulfillment_requests_tenant_order_line",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["mod_orders.orders.tenant_id", "mod_orders.orders.id"],
            name="fk_fulfillment_requests_order",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id", "line_snapshot_id"],
            [
                "mod_orders.order_line_snapshots.tenant_id",
                "mod_orders.order_line_snapshots.order_id",
                "mod_orders.order_line_snapshots.id",
            ],
            name="fk_fulfillment_requests_line",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "publication_count >= 1", name="ck_fulfillment_requests_publications"
        ),
        sa.CheckConstraint(
            "(acceptance_ref IS NULL AND accepted_at IS NULL) "
            "OR (acceptance_ref IS NOT NULL AND accepted_at IS NOT NULL)",
            name="ck_fulfillment_requests_acceptance_complete",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fulfillment_requests_tenant_order",
        "fulfillment_requests",
        ["tenant_id", "order_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fulfillment_requests_tenant_state",
        "fulfillment_requests",
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "order_events",
        _id(),
        _tenant_id(),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("event_ref", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("from_state", sa.String(80), nullable=True),
        sa.Column("to_state", sa.String(80), nullable=True),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(120), nullable=True),
        sa.Column("actor_label", sa.String(160), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _tenant_fk("fk_order_events_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_order_events_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "event_ref", name="uq_order_events_tenant_event_ref"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "order_id",
            "event_sequence",
            name="uq_order_events_tenant_order_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["mod_orders.orders.tenant_id", "mod_orders.orders.id"],
            name="fk_order_events_order",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("event_sequence > 0", name="ck_order_events_sequence"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_order_events_tenant_order",
        "order_events",
        ["tenant_id", "order_id"],
        schema=_SCHEMA,
    )

    _install_structural_immutability()
    _install_deferred_snapshot_checks()
    _install_rls_and_grants()


def _install_structural_immutability() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_orders.protect_frozen_order_snapshot()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.snapshot_frozen_at IS NOT NULL AND ROW(
                NEW.order_reference,
                NEW.customer_ref,
                NEW.currency_code,
                NEW.currency_minor_units,
                NEW.subtotal_amount,
                NEW.discount_amount,
                NEW.tax_amount,
                NEW.total_amount,
                NEW.snapshot_fingerprint,
                NEW.snapshot_frozen_at,
                NEW.source_ref,
                NEW.source_version,
                NEW.fx_rate,
                NEW.fx_base_currency_code,
                NEW.fx_rate_ref,
                NEW.fx_source,
                NEW.fx_as_of,
                NEW.submitted_actor_type,
                NEW.submitted_actor_id,
                NEW.submitted_actor_label,
                NEW.submitted_at
            ) IS DISTINCT FROM ROW(
                OLD.order_reference,
                OLD.customer_ref,
                OLD.currency_code,
                OLD.currency_minor_units,
                OLD.subtotal_amount,
                OLD.discount_amount,
                OLD.tax_amount,
                OLD.total_amount,
                OLD.snapshot_fingerprint,
                OLD.snapshot_frozen_at,
                OLD.source_ref,
                OLD.source_version,
                OLD.fx_rate,
                OLD.fx_base_currency_code,
                OLD.fx_rate_ref,
                OLD.fx_source,
                OLD.fx_as_of,
                OLD.submitted_actor_type,
                OLD.submitted_actor_id,
                OLD.submitted_actor_label,
                OLD.submitted_at
            ) THEN
                RAISE EXCEPTION 'order % commercial snapshot is frozen', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.accepted_at IS NOT NULL AND ROW(
                NEW.accepted_actor_type,
                NEW.accepted_actor_id,
                NEW.accepted_actor_label,
                NEW.accepted_at
            ) IS DISTINCT FROM ROW(
                OLD.accepted_actor_type,
                OLD.accepted_actor_id,
                OLD.accepted_actor_label,
                OLD.accepted_at
            ) THEN
                RAISE EXCEPTION 'order % acceptance evidence is final', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.covered_at IS NOT NULL AND
               NEW.covered_at IS DISTINCT FROM OLD.covered_at THEN
                RAISE EXCEPTION 'order % coverage instant is final', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.cancelled_at IS NOT NULL AND ROW(
                NEW.state,
                NEW.cancelled_actor_type,
                NEW.cancelled_actor_id,
                NEW.cancelled_actor_label,
                NEW.cancelled_at,
                NEW.cancellation_reason
            ) IS DISTINCT FROM ROW(
                OLD.state,
                OLD.cancelled_actor_type,
                OLD.cancelled_actor_id,
                OLD.cancelled_actor_label,
                OLD.cancelled_at,
                OLD.cancellation_reason
            ) THEN
                RAISE EXCEPTION 'order % cancellation evidence is final', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_orders.protect_coverage_gate()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.obligation_count IS DISTINCT FROM OLD.obligation_count THEN
                RAISE EXCEPTION 'coverage gate % obligation set is frozen', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.state IS DISTINCT FROM 'binding' AND NEW.state = 'binding' THEN
                RAISE EXCEPTION 'coverage gate % cannot be reopened', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.resolved_count < OLD.resolved_count THEN
                RAISE EXCEPTION 'coverage gate % resolved count cannot decrease', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.satisfied_at IS NOT NULL AND
               NEW.satisfied_at IS DISTINCT FROM OLD.satisfied_at THEN
                RAISE EXCEPTION 'coverage gate % satisfaction is final', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER orders_protect_frozen_snapshot "
        "BEFORE UPDATE ON mod_orders.orders "
        "FOR EACH ROW EXECUTE FUNCTION mod_orders.protect_frozen_order_snapshot();"
    )
    op.execute(
        "CREATE TRIGGER coverage_gates_protect_finite_set "
        "BEFORE UPDATE ON mod_orders.coverage_gates "
        "FOR EACH ROW EXECUTE FUNCTION mod_orders.protect_coverage_gate();"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_orders.refuse_immutable_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'mod_orders.% is append-only', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_orders.refuse_line_insert_after_freeze()
        RETURNS trigger AS $$
        DECLARE frozen_at timestamptz;
        BEGIN
            SELECT snapshot_frozen_at INTO frozen_at
              FROM mod_orders.orders
             WHERE tenant_id = NEW.tenant_id AND id = NEW.order_id
             FOR KEY SHARE;
            IF frozen_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'order line snapshot set is frozen (order_id=%)', NEW.order_id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_orders.refuse_obligation_insert_after_binding()
        RETURNS trigger AS $$
        DECLARE gate_state text;
        BEGIN
            SELECT state INTO gate_state
              FROM mod_orders.coverage_gates
             WHERE tenant_id = NEW.tenant_id AND id = NEW.gate_id
             FOR KEY SHARE;
            IF gate_state IS DISTINCT FROM 'binding' THEN
                RAISE EXCEPTION
                    'coverage obligation set is frozen (gate_id=%)', NEW.gate_id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER order_line_snapshots_append_only "
        "BEFORE UPDATE OR DELETE ON mod_orders.order_line_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION mod_orders.refuse_immutable_mutation();"
    )
    op.execute(
        "CREATE TRIGGER order_line_snapshots_freeze_insert "
        "BEFORE INSERT ON mod_orders.order_line_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION mod_orders.refuse_line_insert_after_freeze();"
    )
    op.execute(
        "CREATE TRIGGER coverage_obligations_append_only "
        "BEFORE UPDATE OR DELETE ON mod_orders.coverage_obligations "
        "FOR EACH ROW EXECUTE FUNCTION mod_orders.refuse_immutable_mutation();"
    )
    op.execute(
        "CREATE TRIGGER coverage_obligations_freeze_insert "
        "BEFORE INSERT ON mod_orders.coverage_obligations "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_orders.refuse_obligation_insert_after_binding();"
    )
    op.execute(
        "CREATE TRIGGER coverage_resolution_receipts_append_only "
        "BEFORE UPDATE OR DELETE ON mod_orders.coverage_resolution_receipts "
        "FOR EACH ROW EXECUTE FUNCTION mod_orders.refuse_immutable_mutation();"
    )
    op.execute(
        "CREATE TRIGGER order_events_append_only "
        "BEFORE UPDATE OR DELETE ON mod_orders.order_events "
        "FOR EACH ROW EXECUTE FUNCTION mod_orders.refuse_immutable_mutation();"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_orders.protect_fulfillment_request()
        RETURNS trigger AS $$
        BEGIN
            IF ROW(
                NEW.id,
                NEW.tenant_id,
                NEW.order_id,
                NEW.line_snapshot_id,
                NEW.request_fingerprint
            ) IS DISTINCT FROM ROW(
                OLD.id,
                OLD.tenant_id,
                OLD.order_id,
                OLD.line_snapshot_id,
                OLD.request_fingerprint
            ) THEN
                RAISE EXCEPTION 'fulfillment request % identity is final', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.publication_count < OLD.publication_count THEN
                RAISE EXCEPTION
                    'fulfillment request % publication count cannot decrease', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.accepted_at IS NOT NULL AND ROW(
                NEW.state,
                NEW.acceptance_ref,
                NEW.accepted_at
            ) IS DISTINCT FROM ROW(
                OLD.state,
                OLD.acceptance_ref,
                OLD.accepted_at
            ) THEN
                RAISE EXCEPTION
                    'fulfillment request % acceptance evidence is final', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.state = 'cancelled' AND NEW.state IS DISTINCT FROM OLD.state THEN
                RAISE EXCEPTION
                    'fulfillment request % cancellation is final', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER fulfillment_requests_protect_evidence "
        "BEFORE UPDATE ON mod_orders.fulfillment_requests "
        "FOR EACH ROW EXECUTE FUNCTION mod_orders.protect_fulfillment_request();"
    )


def _install_deferred_snapshot_checks() -> None:
    """Refuse any committed half-snapshot, even after a direct SQL write."""

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_orders.require_complete_order_snapshot()
        RETURNS trigger AS $$
        DECLARE
            current_order mod_orders.orders%ROWTYPE;
            line_count bigint;
            line_subtotal numeric;
            line_discount numeric;
            line_tax numeric;
            line_total numeric;
        BEGIN
            SELECT * INTO current_order
              FROM mod_orders.orders
             WHERE tenant_id = NEW.tenant_id AND id = NEW.id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            IF current_order.snapshot_frozen_at IS NULL THEN
                RAISE EXCEPTION 'order % snapshot was not finalized', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            SELECT count(frozen_lines.id),
                   COALESCE(sum(frozen_lines.extended_price), 0),
                   COALESCE(sum(frozen_lines.discount_amount), 0),
                   COALESCE(sum(frozen_lines.tax_amount), 0),
                   COALESCE(sum(frozen_lines.line_total), 0)
              INTO line_count, line_subtotal, line_discount, line_tax, line_total
              FROM mod_orders.order_line_snapshots AS frozen_lines
             WHERE frozen_lines.tenant_id = NEW.tenant_id
               AND frozen_lines.order_id = NEW.id;
            IF line_count = 0 OR ROW(
                current_order.subtotal_amount,
                current_order.discount_amount,
                current_order.tax_amount,
                current_order.total_amount
            ) IS DISTINCT FROM ROW(
                line_subtotal,
                line_discount,
                line_tax,
                line_total
            ) THEN
                RAISE EXCEPTION 'order % header does not match its frozen lines', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER orders_require_complete_snapshot "
        "AFTER INSERT OR UPDATE OF snapshot_frozen_at ON mod_orders.orders "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION mod_orders.require_complete_order_snapshot();"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_orders.require_consistent_coverage_gate()
        RETURNS trigger AS $$
        DECLARE
            gate_id uuid;
            current_gate mod_orders.coverage_gates%ROWTYPE;
            obligations bigint;
            receipts bigint;
        BEGIN
            gate_id := CASE
                WHEN TG_TABLE_NAME = 'coverage_gates' THEN NEW.id
                ELSE NEW.gate_id
            END;
            SELECT * INTO current_gate
              FROM mod_orders.coverage_gates
             WHERE tenant_id = NEW.tenant_id AND id = gate_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            SELECT count(*) INTO obligations
              FROM mod_orders.coverage_obligations
             WHERE tenant_id = NEW.tenant_id AND gate_id = current_gate.id;
            SELECT count(*) INTO receipts
              FROM mod_orders.coverage_resolution_receipts
             WHERE tenant_id = NEW.tenant_id AND gate_id = current_gate.id;
            IF current_gate.state = 'binding'
               OR obligations <> current_gate.obligation_count
               OR receipts <> current_gate.resolved_count
               OR (
                    receipts = obligations
                    AND (
                        current_gate.state <> 'satisfied'
                        OR current_gate.satisfied_at IS NULL
                    )
               )
               OR (
                    receipts < obligations
                    AND (
                        current_gate.state <> 'open'
                        OR current_gate.satisfied_at IS NOT NULL
                    )
               ) THEN
                RAISE EXCEPTION 'coverage gate % does not match its finite evidence',
                    current_gate.id USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER coverage_gates_require_consistency "
        "AFTER INSERT OR UPDATE ON mod_orders.coverage_gates "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION mod_orders.require_consistent_coverage_gate();"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER coverage_obligations_require_consistency "
        "AFTER INSERT ON mod_orders.coverage_obligations "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION mod_orders.require_consistent_coverage_gate();"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER coverage_receipts_require_consistency "
        "AFTER INSERT ON mod_orders.coverage_resolution_receipts "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION mod_orders.require_consistent_coverage_gate();"
    )


def _install_rls_and_grants() -> None:
    op.execute("ALTER TABLE mod_orders.orders ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_orders.orders FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY orders_tenant_isolation ON mod_orders.orders "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mod_orders.orders "
        "TO app_user, platform_api;"
    )

    op.execute("ALTER TABLE mod_orders.order_line_snapshots ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_orders.order_line_snapshots FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY order_line_snapshots_tenant_isolation "
        "ON mod_orders.order_line_snapshots "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_orders.order_line_snapshots "
        "TO app_user, platform_api;"
    )

    op.execute("ALTER TABLE mod_orders.coverage_gates ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_orders.coverage_gates FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY coverage_gates_tenant_isolation ON mod_orders.coverage_gates "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mod_orders.coverage_gates "
        "TO app_user, platform_api;"
    )

    op.execute("ALTER TABLE mod_orders.coverage_obligations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_orders.coverage_obligations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY coverage_obligations_tenant_isolation "
        "ON mod_orders.coverage_obligations "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_orders.coverage_obligations "
        "TO app_user, platform_api;"
    )

    op.execute(
        "ALTER TABLE mod_orders.coverage_resolution_receipts "
        "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_orders.coverage_resolution_receipts "
        "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY coverage_resolution_receipts_tenant_isolation "
        "ON mod_orders.coverage_resolution_receipts "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_orders.coverage_resolution_receipts "
        "TO app_user, platform_api;"
    )

    op.execute("ALTER TABLE mod_orders.fulfillment_requests ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_orders.fulfillment_requests FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY fulfillment_requests_tenant_isolation "
        "ON mod_orders.fulfillment_requests "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mod_orders.fulfillment_requests "
        "TO app_user, platform_api;"
    )

    op.execute("ALTER TABLE mod_orders.order_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_orders.order_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY order_events_tenant_isolation ON mod_orders.order_events "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_orders.order_events TO app_user, platform_api;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_orders.order_events CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_orders.fulfillment_requests CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_orders.coverage_resolution_receipts CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_orders.coverage_obligations CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_orders.coverage_gates CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_orders.order_line_snapshots CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_orders.orders CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS mod_orders.require_consistent_coverage_gate();")
    op.execute("DROP FUNCTION IF EXISTS mod_orders.require_complete_order_snapshot();")
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "mod_orders.refuse_obligation_insert_after_binding();"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_orders.refuse_line_insert_after_freeze();")
    op.execute("DROP FUNCTION IF EXISTS mod_orders.refuse_immutable_mutation();")
    op.execute("DROP FUNCTION IF EXISTS mod_orders.protect_fulfillment_request();")
    op.execute("DROP FUNCTION IF EXISTS mod_orders.protect_coverage_gate();")
    op.execute("DROP FUNCTION IF EXISTS mod_orders.protect_frozen_order_snapshot();")
    op.execute("DROP SCHEMA IF EXISTS mod_orders;")
