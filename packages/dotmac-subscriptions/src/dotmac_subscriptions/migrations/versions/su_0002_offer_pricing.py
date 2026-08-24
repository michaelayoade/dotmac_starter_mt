"""Add explicit offer pricing ownership without rewriting released a1.

``su_0001_subscriptions`` shipped in ``dotmac-subscriptions 0.1.0a1`` and is
immutable.  Its public writer required every offer version to carry at least
one price, but it permitted zero-valued prices and more than one charge model.
The a2 contract instead puts one charge model and one pricing policy on the
offer version, permits a contract-priced offer to omit catalogue prices, and
requires every amount that is stored as a price to be strictly positive.

Existing a1 rows can be upgraded without inventing commercial meaning only
when they contain at least one price, all their prices name the same charge
model, and every stored price is positive.  The migration locks and validates
all selected planes before changing either one, then derives the version's
charge model from its child prices and records the historical policy as
``catalog_price``.  Ambiguous or zero-valued data fails closed for an explicit
product-owned repair; no product, provider, currency or tariff code is baked
into this reusable lineage.

Revision ID: su_0002_offer_pricing
Revises: su_0001_subscriptions
Create Date: 2026-08-24
"""

from __future__ import annotations

from typing import NamedTuple

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "su_0002_offer_pricing"
down_revision = "su_0001_subscriptions"
branch_labels = None
depends_on = None

MODULE_CODE = "subscriptions"
_SCHEMA = "mod_subscriptions"


class _PlaneTables(NamedTuple):
    versions: str
    prices: str
    lines: str
    freeze_trigger: str
    pricing_constraint: str
    price_constraint: str
    line_constraint: str


_TABLES = {
    ModulePlane.TENANT: _PlaneTables(
        versions="offer_versions",
        prices="offer_version_prices",
        lines="subscription_contract_lines",
        freeze_trigger="offer_versions_content_freeze",
        pricing_constraint="ck_offer_versions_pricing_mode",
        price_constraint="ck_offer_version_prices_amounts",
        line_constraint="ck_contract_lines_amounts",
    ),
    ModulePlane.PLATFORM: _PlaneTables(
        versions="platform_offer_versions",
        prices="platform_offer_version_prices",
        lines="platform_subscription_contract_lines",
        freeze_trigger="platform_offer_versions_content_freeze",
        pricing_constraint="ck_platform_offer_versions_pricing_mode",
        price_constraint="ck_platform_offer_prices_amounts",
        line_constraint="ck_platform_contract_lines_amounts",
    ),
}


def _qualified(table: str) -> str:
    return f"{_SCHEMA}.{table}"


def _reflected(table: str) -> sa.Table:
    return sa.Table(
        table,
        sa.MetaData(),
        schema=_SCHEMA,
        autoload_with=op.get_bind(),
    )


def _has_row(statement: sa.Select[tuple[object]]) -> bool:
    return op.get_bind().execute(statement.limit(1)).first() is not None


def _lock_and_validate(tables: _PlaneTables) -> None:
    versions = _qualified(tables.versions)
    prices = _qualified(tables.prices)
    lines = _qualified(tables.lines)
    op.execute(f"LOCK TABLE {versions}, {prices}, {lines} IN ACCESS EXCLUSIVE MODE;")
    version_table = _reflected(tables.versions)
    price_table = _reflected(tables.prices)
    line_table = _reflected(tables.lines)
    ambiguous_versions = (
        sa.select(version_table.c.id)
        .select_from(
            version_table.outerjoin(
                price_table,
                price_table.c.offer_version_id == version_table.c.id,
            )
        )
        .group_by(version_table.c.id)
        .having(
            sa.or_(
                sa.func.count(price_table.c.id) == 0,
                sa.func.count(sa.distinct(price_table.c.charge_model_code)) != 1,
            )
        )
    )
    if _has_row(ambiguous_versions):
        raise RuntimeError(
            f"{versions} contains an offer version without exactly one legacy "
            "charge model; repair its catalogue evidence explicitly"
        )
    if _has_row(sa.select(price_table.c.id).where(price_table.c.amount <= 0)):
        raise RuntimeError(
            f"{prices} contains a non-positive price; repair its commercial "
            "evidence explicitly before upgrading"
        )
    if _has_row(sa.select(line_table.c.id).where(line_table.c.unit_price <= 0)):
        raise RuntimeError(
            f"{lines} contains a non-positive contract price; repair its "
            "commercial evidence explicitly before upgrading"
        )


def _upgrade_plane(tables: _PlaneTables) -> None:
    versions = _qualified(tables.versions)
    op.add_column(
        tables.versions,
        sa.Column("charge_model_code", sa.String(120), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        tables.versions,
        sa.Column("pricing_mode", sa.String(24), nullable=True),
        schema=_SCHEMA,
    )

    # a1 deliberately freezes published content.  This one controlled rewrite
    # derives the new columns from already-immutable price evidence while the
    # tables remain locked; normal writers never receive a trigger bypass.
    op.execute(f"ALTER TABLE {versions} DISABLE TRIGGER {tables.freeze_trigger};")
    version_table = _reflected(tables.versions)
    price_table = _reflected(tables.prices)
    evidence = (
        sa.select(
            price_table.c.offer_version_id,
            sa.func.min(price_table.c.charge_model_code).label("charge_model_code"),
        )
        .group_by(price_table.c.offer_version_id)
        .subquery()
    )
    op.get_bind().execute(
        sa.update(version_table)
        .where(version_table.c.id == evidence.c.offer_version_id)
        .values(
            charge_model_code=evidence.c.charge_model_code,
            pricing_mode="catalog_price",
        )
    )
    op.execute(f"ALTER TABLE {versions} ENABLE TRIGGER {tables.freeze_trigger};")

    op.alter_column(
        tables.versions,
        "charge_model_code",
        existing_type=sa.String(120),
        nullable=False,
        schema=_SCHEMA,
    )
    op.alter_column(
        tables.versions,
        "pricing_mode",
        existing_type=sa.String(24),
        nullable=False,
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        tables.pricing_constraint,
        tables.versions,
        "pricing_mode IN ('catalog_price', 'contract_price')",
        schema=_SCHEMA,
    )

    op.drop_constraint(
        tables.price_constraint,
        tables.prices,
        type_="check",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        tables.price_constraint,
        tables.prices,
        "amount > 0 AND quantity > 0",
        schema=_SCHEMA,
    )
    op.drop_constraint(
        tables.line_constraint,
        tables.lines,
        type_="check",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        tables.line_constraint,
        tables.lines,
        "quantity > 0 AND unit_price > 0",
        schema=_SCHEMA,
    )


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    selected = tuple(_TABLES[plane] for plane in ModulePlane if plane in planes)
    for tables in selected:
        _lock_and_validate(tables)
    for tables in selected:
        _upgrade_plane(tables)


def _lock_and_validate_downgrade(tables: _PlaneTables) -> None:
    versions = _qualified(tables.versions)
    op.execute(f"LOCK TABLE {versions} IN ACCESS EXCLUSIVE MODE;")
    version_table = _reflected(tables.versions)
    if _has_row(
        sa.select(version_table.c.id).where(
            version_table.c.pricing_mode == "contract_price"
        )
    ):
        raise RuntimeError(
            f"{versions} contains contract-priced versions whose charge model "
            "would be lost by the a1 schema"
        )


def _downgrade_plane(tables: _PlaneTables) -> None:
    op.drop_constraint(
        tables.line_constraint,
        tables.lines,
        type_="check",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        tables.line_constraint,
        tables.lines,
        "quantity > 0 AND unit_price >= 0",
        schema=_SCHEMA,
    )
    op.drop_constraint(
        tables.price_constraint,
        tables.prices,
        type_="check",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        tables.price_constraint,
        tables.prices,
        "amount >= 0 AND quantity > 0",
        schema=_SCHEMA,
    )
    op.drop_constraint(
        tables.pricing_constraint,
        tables.versions,
        type_="check",
        schema=_SCHEMA,
    )
    op.drop_column(tables.versions, "pricing_mode", schema=_SCHEMA)
    op.drop_column(tables.versions, "charge_model_code", schema=_SCHEMA)


def downgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    selected = tuple(_TABLES[plane] for plane in ModulePlane if plane in planes)
    for tables in selected:
        _lock_and_validate_downgrade(tables)
    for tables in reversed(selected):
        _downgrade_plane(tables)
