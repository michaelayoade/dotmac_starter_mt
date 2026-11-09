"""A catalog_price contract line must name, and equal, a published offer price.

``su_0001`` .. ``su_0003`` are released history and are not edited.  This
revision is additive: one nullable column on each contract-line table, a
composite foreign key to the offer-version price it names, and one INSERT
trigger that states the rule the foreign key cannot.

The rule
--------
* ``catalog_price`` offer version: the line MUST carry ``price_key`` and must
  equal that ``OfferVersionPrice`` (charge model, amount, currency, scale).
* ``contract_price`` offer version: the line is the only price and MUST NOT
  carry ``price_key``.
* The referenced offer version must be ``published`` and its ``version`` and
  ``charge_model_code`` must be the ones the line states.

The composite foreign key (MATCH SIMPLE, so a NULL ``price_key`` is skipped)
guarantees the named key exists on the named version; the trigger carries the
cross-table equality a foreign key cannot.  Contract lines are already
immutable (``contract_lines_immutable``), so an INSERT trigger is sufficient.

Existing rows
-------------
Every existing line has ``price_key IS NULL``, so the foreign key is satisfied
trivially and is added fully validated; there is nothing to defer with
``NOT VALID``.  The trigger fires on INSERT only, so historical lines are
neither rewritten nor re-judged.  Lines of ``catalog_price`` versions that
pre-date the column are, by definition, lines that do not name a price; the
upgrade LOGS how many there are and changes nothing.  They are recorded as a
grandfathered exceptions set in
``docs/inventories/subscriptions-catalog-price-exceptions.md`` for manual
review.  There is no repair and no backfill: a price is never inferred.

Downgrade refuses while any line names a price, because dropping the column
would erase that evidence.

Revision ID: su_0004_catalog_price_line
Revises: su_0003_billing_treatments
Create Date: 2026-11-09
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "su_0004_catalog_price_line"
down_revision = "su_0003_billing_treatments"
branch_labels = None
depends_on = None

MODULE_CODE = "subscriptions"
_SCHEMA = "mod_subscriptions"
_LOG = logging.getLogger("alembic.runtime.migration")


class _PlaneTables(NamedTuple):
    versions: str
    prices: str
    lines: str
    fk_name: str
    trigger_name: str
    function_name: str
    tenant_scoped: bool


_TABLES = {
    ModulePlane.TENANT: _PlaneTables(
        versions="offer_versions",
        prices="offer_version_prices",
        lines="subscription_contract_lines",
        fk_name="fk_contract_lines_catalog_price",
        trigger_name="contract_lines_catalog_price",
        function_name="enforce_tenant_catalog_price_line",
        tenant_scoped=True,
    ),
    ModulePlane.PLATFORM: _PlaneTables(
        versions="platform_offer_versions",
        prices="platform_offer_version_prices",
        lines="platform_subscription_contract_lines",
        fk_name="fk_platform_contract_lines_catalog_price",
        trigger_name="platform_contract_lines_catalog_price",
        function_name="enforce_platform_catalog_price_line",
        tenant_scoped=False,
    ),
}


def _q(table: str) -> str:
    return f"{_SCHEMA}.{table}"


def _selected() -> tuple[_PlaneTables, ...]:
    planes = selected_module_planes(MODULE_CODE)
    return tuple(_TABLES[plane] for plane in ModulePlane if plane in planes)


def _log_legacy_census(tables: _PlaneTables) -> None:
    """Count, never change, the lines that pre-date the named-price rule."""
    count = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT count(*) FROM {_q(tables.lines)} l "  # noqa: S608 # nosec B608 -- identifiers are module-owned literals; no input reaches this SQL
                f"JOIN {_q(tables.versions)} v ON v.id = l.offer_version_id"
                + (" AND v.tenant_id = l.tenant_id" if tables.tenant_scoped else "")
                + " WHERE v.pricing_mode = 'catalog_price'"
            )
        )
        .scalar_one()
    )
    _LOG.warning(
        "%s: %s catalog_price line(s) pre-date price_key and are grandfathered "
        "unrepaired; see docs/inventories/subscriptions-catalog-price-exceptions.md",
        tables.lines,
        count,
    )


def _function_sql(tables: _PlaneTables) -> str:
    tenant_version = " AND ov.tenant_id = NEW.tenant_id" if tables.tenant_scoped else ""
    tenant_price = " AND pp.tenant_id = NEW.tenant_id" if tables.tenant_scoped else ""
    return f"""
        CREATE OR REPLACE FUNCTION {_q(tables.function_name)}()
        RETURNS trigger AS $$
        DECLARE
          ver RECORD;
          pr RECORD;
        BEGIN
          SELECT ov.version, ov.state, ov.pricing_mode, ov.charge_model_code
            INTO ver
            FROM {_q(tables.versions)} ov
           WHERE ov.id = NEW.offer_version_id{tenant_version};
          IF NOT FOUND THEN
            RAISE EXCEPTION 'contract line names an unknown offer version'
              USING ERRCODE = 'foreign_key_violation';
          END IF;
          IF ver.state <> 'published' THEN
            RAISE EXCEPTION 'contract line must reference a published offer version'
              USING ERRCODE = 'check_violation';
          END IF;
          IF ver.version <> NEW.offer_version
             OR ver.charge_model_code <> NEW.charge_model_code THEN
            RAISE EXCEPTION
              'contract line version or charge model differs from its offer version'
              USING ERRCODE = 'check_violation';
          END IF;
          IF ver.pricing_mode = 'catalog_price' THEN
            IF NEW.price_key IS NULL THEN
              RAISE EXCEPTION 'a catalog_price line must name a price_key'
                USING ERRCODE = 'check_violation';
            END IF;
            SELECT pp.charge_model_code, pp.amount, pp.currency, pp.scale
              INTO pr
              FROM {_q(tables.prices)} pp
             WHERE pp.offer_version_id = NEW.offer_version_id
               AND pp.price_key = NEW.price_key{tenant_price};
            IF NOT FOUND THEN
              RAISE EXCEPTION 'a catalog_price line names a price_key the offer version does not publish'
                USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF pr.charge_model_code <> NEW.charge_model_code
               OR pr.amount <> NEW.unit_price
               OR pr.currency <> NEW.currency
               OR pr.scale <> NEW.scale THEN
              RAISE EXCEPTION 'a catalog_price line must equal the published offer-version price'
                USING ERRCODE = 'check_violation';
            END IF;
          ELSIF NEW.price_key IS NOT NULL THEN
            RAISE EXCEPTION 'a contract_price line must not name a catalogue price_key'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """  # noqa: S608 # nosec B608 -- identifiers are module-owned literals; no input reaches this SQL


def _upgrade_plane(tables: _PlaneTables) -> None:
    lines = _q(tables.lines)
    op.execute(f"LOCK TABLE {lines} IN ACCESS EXCLUSIVE MODE;")
    _log_legacy_census(tables)
    op.add_column(
        tables.lines,
        sa.Column("price_key", sa.String(120), nullable=True),
        schema=_SCHEMA,
    )
    if tables.tenant_scoped:
        local = ["tenant_id", "offer_version_id", "price_key"]
        remote = ["tenant_id", "offer_version_id", "price_key"]
    else:
        local = ["offer_version_id", "price_key"]
        remote = ["offer_version_id", "price_key"]
    op.create_foreign_key(
        tables.fk_name,
        tables.lines,
        tables.prices,
        local,
        remote,
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.execute(_function_sql(tables))
    op.execute(
        f"CREATE TRIGGER {tables.trigger_name} BEFORE INSERT ON {lines} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q(tables.function_name)}();"
    )


def upgrade() -> None:
    for tables in _selected():
        _upgrade_plane(tables)


def _downgrade_plane(tables: _PlaneTables) -> None:
    lines = _q(tables.lines)
    op.execute(f"LOCK TABLE {lines} IN ACCESS EXCLUSIVE MODE;")
    named = (
        op.get_bind()
        .execute(sa.text(f"SELECT count(*) FROM {lines} WHERE price_key IS NOT NULL"))  # noqa: S608 # nosec B608 -- identifiers are module-owned literals; no input reaches this SQL
        .scalar_one()
    )
    if named:
        raise RuntimeError(
            f"{lines} holds {named} line(s) that name a catalogue price; "
            "dropping price_key would erase that evidence"
        )
    op.execute(f"DROP TRIGGER IF EXISTS {tables.trigger_name} ON {lines};")
    op.execute(f"DROP FUNCTION IF EXISTS {_q(tables.function_name)}();")
    op.drop_constraint(tables.fk_name, tables.lines, type_="foreignkey", schema=_SCHEMA)
    op.drop_column(tables.lines, "price_key", schema=_SCHEMA)


def downgrade() -> None:
    for tables in reversed(_selected()):
        _downgrade_plane(tables)
