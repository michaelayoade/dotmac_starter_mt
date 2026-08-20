"""Create the tenant-only inventory owner in ``mod_inventory``.

Revision ID: iv_0001_inventory
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "iv_0001_inventory"
down_revision = None
branch_labels = ("inventory",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_inventory"
_QTY = sa.Numeric(20, 6)
_MONEY = sa.Numeric(24, 6)


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


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], name=name, ondelete="CASCADE"
    )


def _item_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "item_id"],
        ["mod_inventory.items.tenant_id", "mod_inventory.items.id"],
        name=name,
        ondelete="RESTRICT",
    )


def _warehouse_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "warehouse_id"],
        ["mod_inventory.warehouses.tenant_id", "mod_inventory.warehouses.id"],
        name=name,
        ondelete="RESTRICT",
    )


def _lot_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "lot_id"],
        ["mod_inventory.lots.tenant_id", "mod_inventory.lots.id"],
        name=name,
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_inventory;")
    op.execute("GRANT USAGE ON SCHEMA mod_inventory TO app_user, platform_api;")

    op.create_table(
        "items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("base_uom", sa.String(32), nullable=False),
        sa.Column("costing_method", sa.String(32), nullable=False),
        sa.Column("standard_cost", _MONEY, nullable=True),
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
            server_default="NGN",
        ),
        sa.Column(
            "track_lots", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "track_serials", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("fk_inventory_items_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_inventory_items_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_inventory_items_tenant_sku"),
        sa.CheckConstraint(
            "standard_cost IS NULL OR standard_cost >= 0",
            name="ck_inventory_items_standard_cost_nonnegative",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inventory_items_tenant_active",
        "items",
        ["tenant_id", "is_active"],
        schema=_SCHEMA,
    )

    op.create_table(
        "warehouses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "allows_receipts", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "allows_issues", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("fk_inventory_warehouses_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inventory_warehouses_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_inventory_warehouses_tenant_code"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "stock_balances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("quantity_on_hand", _QTY, nullable=False, server_default="0"),
        sa.Column("quantity_reserved", _QTY, nullable=False, server_default="0"),
        sa.Column("total_value", _MONEY, nullable=False, server_default="0"),
        sa.Column("current_unit_cost", _MONEY, nullable=False, server_default="0"),
        *_timestamps(),
        _tenant_fk("fk_inventory_stock_balances_tenant"),
        _item_fk("fk_inventory_stock_balances_item"),
        _warehouse_fk("fk_inventory_stock_balances_warehouse"),
        sa.UniqueConstraint(
            "tenant_id", "item_id", "warehouse_id", name="uq_inventory_stock_balance"
        ),
        sa.CheckConstraint(
            "quantity_on_hand >= 0", name="ck_inventory_stock_on_hand_nonnegative"
        ),
        sa.CheckConstraint(
            "quantity_reserved >= 0", name="ck_inventory_stock_reserved_nonnegative"
        ),
        sa.CheckConstraint(
            "quantity_reserved <= quantity_on_hand",
            name="ck_inventory_stock_reserved_lte_on_hand",
        ),
        sa.CheckConstraint(
            "total_value >= 0", name="ck_inventory_stock_value_nonnegative"
        ),
        sa.CheckConstraint(
            "current_unit_cost >= 0", name="ck_inventory_stock_cost_nonnegative"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "lots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("supplier_lot_ref", sa.String(160), nullable=True),
        sa.Column("manufacture_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unit_cost", _MONEY, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("quarantine_reason", sa.String(240), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_inventory_lots_tenant"),
        _item_fk("fk_inventory_lots_item"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_inventory_lots_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "item_id", "code", name="uq_inventory_lots_item_code"
        ),
        sa.CheckConstraint("unit_cost >= 0", name="ck_inventory_lots_cost_nonnegative"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inventory_lots_fifo",
        "lots",
        ["tenant_id", "item_id", "received_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "lot_balances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("quantity_on_hand", _QTY, nullable=False, server_default="0"),
        sa.Column("quantity_reserved", _QTY, nullable=False, server_default="0"),
        *_timestamps(),
        _tenant_fk("fk_inventory_lot_balances_tenant"),
        _lot_fk("fk_inventory_lot_balances_lot"),
        _warehouse_fk("fk_inventory_lot_balances_warehouse"),
        sa.UniqueConstraint(
            "tenant_id", "lot_id", "warehouse_id", name="uq_inventory_lot_balance"
        ),
        sa.CheckConstraint(
            "quantity_on_hand >= 0", name="ck_inventory_lot_on_hand_nonnegative"
        ),
        sa.CheckConstraint(
            "quantity_reserved >= 0", name="ck_inventory_lot_reserved_nonnegative"
        ),
        sa.CheckConstraint(
            "quantity_reserved <= quantity_on_hand",
            name="ck_inventory_lot_reserved_lte_on_hand",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "serials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("serial_number", sa.String(160), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("last_movement_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_inventory_serials_tenant"),
        _item_fk("fk_inventory_serials_item"),
        _warehouse_fk("fk_inventory_serials_warehouse"),
        _lot_fk("fk_inventory_serials_lot"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inventory_serials_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "item_id",
            "serial_number",
            name="uq_inventory_serials_item_number",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inventory_serials_warehouse",
        "serials",
        ["tenant_id", "warehouse_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("movement_group_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("quantity_delta", _QTY, nullable=False),
        sa.Column("unit_cost", _MONEY, nullable=False),
        sa.Column("value_delta", _MONEY, nullable=False),
        sa.Column("cost_variance", _MONEY, nullable=False, server_default="0"),
        sa.Column("quantity_after", _QTY, nullable=False),
        sa.Column("value_after", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("actor_ref", sa.String(160), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        _tenant_fk("fk_inventory_movements_tenant"),
        _item_fk("fk_inventory_movements_item"),
        _warehouse_fk("fk_inventory_movements_warehouse"),
        _lot_fk("fk_inventory_movements_lot"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inventory_movements_tenant_id_id"
        ),
        sa.CheckConstraint(
            "quantity_delta <> 0", name="ck_inventory_movement_quantity_nonzero"
        ),
        sa.CheckConstraint(
            "unit_cost >= 0", name="ck_inventory_movement_cost_nonnegative"
        ),
        sa.CheckConstraint(
            "quantity_after >= 0", name="ck_inventory_movement_after_nonnegative"
        ),
        sa.CheckConstraint(
            "value_after >= 0", name="ck_inventory_movement_value_after_nonnegative"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inventory_movements_balance",
        "stock_movements",
        ["tenant_id", "item_id", "warehouse_id", "occurred_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inventory_movements_group",
        "stock_movements",
        ["tenant_id", "movement_group_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "movement_serials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("movement_id", sa.Uuid(), nullable=False),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        _tenant_fk("fk_inventory_movement_serials_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "movement_id"],
            [
                "mod_inventory.stock_movements.tenant_id",
                "mod_inventory.stock_movements.id",
            ],
            name="fk_inventory_movement_serials_movement",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "serial_id"],
            ["mod_inventory.serials.tenant_id", "mod_inventory.serials.id"],
            name="fk_inventory_movement_serials_serial",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "movement_id", "serial_id", name="uq_inventory_movement_serial"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "stock_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("reservation_ref", sa.String(240), nullable=False),
        sa.Column("quantity_reserved", _QTY, nullable=False),
        sa.Column("quantity_fulfilled", _QTY, nullable=False, server_default="0"),
        sa.Column("quantity_released", _QTY, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("actor_ref", sa.String(160), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(240), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_inventory_reservations_tenant"),
        _item_fk("fk_inventory_reservations_item"),
        _warehouse_fk("fk_inventory_reservations_warehouse"),
        _lot_fk("fk_inventory_reservations_lot"),
        sa.UniqueConstraint(
            "tenant_id", "reservation_ref", name="uq_inventory_reservation_ref"
        ),
        sa.CheckConstraint(
            "quantity_reserved > 0", name="ck_inventory_reservation_quantity_positive"
        ),
        sa.CheckConstraint(
            "quantity_fulfilled >= 0",
            name="ck_inventory_reservation_fulfilled_nonnegative",
        ),
        sa.CheckConstraint(
            "quantity_released >= 0",
            name="ck_inventory_reservation_released_nonnegative",
        ),
        sa.CheckConstraint(
            "quantity_fulfilled + quantity_released <= quantity_reserved",
            name="ck_inventory_reservation_consumed_lte_reserved",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inventory_reservations_active",
        "stock_reservations",
        ["tenant_id", "item_id", "warehouse_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "valuation_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("valuation_ref", sa.String(240), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("quantity_on_hand", _QTY, nullable=False),
        sa.Column("unit_cost", _MONEY, nullable=False),
        sa.Column("total_cost", _MONEY, nullable=False),
        sa.Column("costing_method", sa.String(32), nullable=False),
        sa.Column("net_realizable_value", _MONEY, nullable=True),
        sa.Column("carrying_amount", _MONEY, nullable=False),
        sa.Column("write_down", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        *_timestamps(),
        _tenant_fk("fk_inventory_valuations_tenant"),
        _item_fk("fk_inventory_valuations_item"),
        _warehouse_fk("fk_inventory_valuations_warehouse"),
        _lot_fk("fk_inventory_valuations_lot"),
        sa.UniqueConstraint(
            "tenant_id", "valuation_ref", name="uq_inventory_valuation_ref"
        ),
        sa.CheckConstraint(
            "quantity_on_hand >= 0", name="ck_inventory_valuation_quantity_nonnegative"
        ),
        sa.CheckConstraint(
            "total_cost >= 0", name="ck_inventory_valuation_cost_nonnegative"
        ),
        sa.CheckConstraint(
            "carrying_amount >= 0", name="ck_inventory_valuation_carrying_nonnegative"
        ),
        sa.CheckConstraint(
            "write_down >= 0", name="ck_inventory_valuation_write_down_nonnegative"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inventory_valuations_as_of",
        "valuation_snapshots",
        ["tenant_id", "as_of_date"],
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_inventory.refuse_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'inventory evidence table % is append-only',
                TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER inventory_stock_movements_append_only "
        "BEFORE UPDATE OR DELETE ON mod_inventory.stock_movements "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_inventory.refuse_evidence_mutation();"
    )
    op.execute(
        "CREATE TRIGGER inventory_movement_serials_append_only "
        "BEFORE UPDATE OR DELETE ON mod_inventory.movement_serials "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_inventory.refuse_evidence_mutation();"
    )
    op.execute(
        "CREATE TRIGGER inventory_valuation_snapshots_append_only "
        "BEFORE UPDATE OR DELETE ON mod_inventory.valuation_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_inventory.refuse_evidence_mutation();"
    )

    op.execute("ALTER TABLE mod_inventory.items ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inventory.items FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inventory_items_tenant_isolation ON mod_inventory.items USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.items TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.items TO platform_api;"
    )

    op.execute("ALTER TABLE mod_inventory.warehouses ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inventory.warehouses FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inventory_warehouses_tenant_isolation ON mod_inventory.warehouses USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.warehouses TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.warehouses TO platform_api;"
    )

    op.execute("ALTER TABLE mod_inventory.stock_balances ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inventory.stock_balances FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inventory_stock_balances_tenant_isolation ON mod_inventory.stock_balances USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.stock_balances TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.stock_balances TO platform_api;"
    )

    op.execute("ALTER TABLE mod_inventory.lots ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inventory.lots FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inventory_lots_tenant_isolation ON mod_inventory.lots USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.lots TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.lots TO platform_api;"
    )

    op.execute("ALTER TABLE mod_inventory.lot_balances ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inventory.lot_balances FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inventory_lot_balances_tenant_isolation ON mod_inventory.lot_balances USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.lot_balances TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.lot_balances TO platform_api;"
    )

    op.execute("ALTER TABLE mod_inventory.serials ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inventory.serials FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inventory_serials_tenant_isolation ON mod_inventory.serials USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.serials TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.serials TO platform_api;"
    )

    op.execute("ALTER TABLE mod_inventory.stock_movements ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inventory.stock_movements FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inventory_stock_movements_tenant_isolation ON mod_inventory.stock_movements USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_inventory.stock_movements TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_inventory.stock_movements TO platform_api;")

    op.execute("ALTER TABLE mod_inventory.movement_serials ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inventory.movement_serials FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inventory_movement_serials_tenant_isolation ON mod_inventory.movement_serials USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_inventory.movement_serials TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT ON mod_inventory.movement_serials TO platform_api;"
    )

    op.execute(
        "ALTER TABLE mod_inventory.stock_reservations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_inventory.stock_reservations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inventory_stock_reservations_tenant_isolation ON mod_inventory.stock_reservations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.stock_reservations TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inventory.stock_reservations TO platform_api;"
    )

    op.execute(
        "ALTER TABLE mod_inventory.valuation_snapshots ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_inventory.valuation_snapshots FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY inventory_valuation_snapshots_tenant_isolation ON mod_inventory.valuation_snapshots USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_inventory.valuation_snapshots TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT ON mod_inventory.valuation_snapshots TO platform_api;"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inventory_valuations_as_of",
        table_name="valuation_snapshots",
        schema=_SCHEMA,
    )
    op.drop_table("valuation_snapshots", schema=_SCHEMA)
    op.drop_index(
        "ix_inventory_reservations_active",
        table_name="stock_reservations",
        schema=_SCHEMA,
    )
    op.drop_table("stock_reservations", schema=_SCHEMA)
    op.drop_table("movement_serials", schema=_SCHEMA)
    op.drop_index(
        "ix_inventory_movements_group", table_name="stock_movements", schema=_SCHEMA
    )
    op.drop_index(
        "ix_inventory_movements_balance", table_name="stock_movements", schema=_SCHEMA
    )
    op.drop_table("stock_movements", schema=_SCHEMA)
    op.drop_index(
        "ix_inventory_serials_warehouse", table_name="serials", schema=_SCHEMA
    )
    op.drop_table("serials", schema=_SCHEMA)
    op.drop_table("lot_balances", schema=_SCHEMA)
    op.drop_index("ix_inventory_lots_fifo", table_name="lots", schema=_SCHEMA)
    op.drop_table("lots", schema=_SCHEMA)
    op.drop_table("stock_balances", schema=_SCHEMA)
    op.drop_table("warehouses", schema=_SCHEMA)
    op.drop_index(
        "ix_inventory_items_tenant_active", table_name="items", schema=_SCHEMA
    )
    op.drop_table("items", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_inventory.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_inventory RESTRICT;")
