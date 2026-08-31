"""Create the tenant and platform numbering planes (ADR-0023, ADR-0030).

## Prerequisites

- `tenant_scope_catalog.v1` — the FK target `public.tenants.id` and the
  `public.app_current_tenant_id()` the RLS policies evaluate;
- `module_database_roles.v1` — `app_user`, `platform_api` and `app_admin`.

## Four tables per plane, and why the counter is separate

`number_series` is configuration; `series_counters` is state, one row per
`(scope, series, period)`. They are separate tables because collapsing them
makes reconfiguration and allocation contend for the same row, and because a
resetting series does not have one counter — it has one per period, and a
backdated allocation must read ITS period's counter rather than whichever
period the series last advanced into.

## Immutability is structural, not conventional

`allocation_receipts` and `series_repairs` (and their platform peers) are
append-only, and this migration makes that a property of the database:

- the online roles are granted `SELECT, INSERT` and nothing else, so `UPDATE`
  and `DELETE` fail on privileges before any trigger runs;
- a `BEFORE UPDATE OR DELETE` trigger refuses anyway, which covers `app_admin`
  and any future role that is granted more than it should be;
- there is no `updated_at` column, because a column recording a change to a row
  that cannot change is either dead or a lie.

A service-level promise would not survive the first migration script that
"fixes up" a number by hand — which is exactly what the ERP source's
`reset_sequence` does.

Revision ID: nu_0001_numbering
Revises: (lineage root)
Create Date: 2026-08-15
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "nu_0001_numbering"
down_revision = None
branch_labels = ("numbering",)

MODULE_CODE = "numbering"
# `idempotency_ledger.v1` is verified here even though no statement below
# touches it: `allocate` writes that ledger at REQUEST time, and deploy is the
# last moment at which a missing ledger is a failed migration rather than a
# failed allocation in production.
COMMON_REQUIRES = ("module_database_roles.v1", "idempotency_ledger.v1")
TENANT_REQUIRES = ("tenant_scope_catalog.v1",)
PLATFORM_REQUIRES: tuple[str, ...] = ()
REQUIRES = COMMON_REQUIRES + TENANT_REQUIRES + PLATFORM_REQUIRES

depends_on = resolve_depends_on(
    COMMON_REQUIRES,
    module=MODULE_CODE,
    tenant=TENANT_REQUIRES,
    platform=PLATFORM_REQUIRES,
)

_SCHEMA = "mod_numbering"
_SERIES_CODE = sa.String(80)
_PERIOD = sa.String(7)

_TENANT_MUTABLE = ("number_series", "series_counters")
_TENANT_IMMUTABLE = ("allocation_receipts", "series_repairs")
_PLATFORM_MUTABLE = ("platform_number_series", "platform_series_counters")
_PLATFORM_IMMUTABLE = ("platform_allocation_receipts", "platform_series_repairs")

_REFUSE_FUNCTION = "mod_numbering.refuse_mutation"
_FREEZE_TENANT_FUNCTION = "mod_numbering.refuse_tenant_identity_change"
_FREEZE_PLATFORM_FUNCTION = "mod_numbering.refuse_platform_identity_change"

# The fields that shape what a rendered number looks like, or which period it
# belongs to. `start_value` is deliberately absent: it is a PROSPECTIVE SEED
# for periods that have not opened yet, never part of the rendered identity,
# so changing it can reissue nothing.
_IDENTITY_COLUMNS = (
    "prefix",
    "suffix",
    "separator",
    "min_digits",
    "include_year",
    "year_digits",
    "include_month",
    "reset_policy",
)


def _identity_tuple(alias: str) -> str:
    return ", ".join(f"{alias}.{column}" for column in _IDENTITY_COLUMNS)


def _freeze_function_sql(name: str, counters: str, receipts: str, scope: str) -> str:
    """The identity-freeze trigger body for one plane.

    Every interpolated value is a module-level literal — the column names above
    and the two table names for this plane. noqa: the check cannot see that.
    """
    tenant_predicate = "c.tenant_id = NEW.tenant_id AND " if scope == "tenant" else ""
    receipt_predicate = "r.tenant_id = NEW.tenant_id AND " if scope == "tenant" else ""
    table = "number_series" if scope == "tenant" else "platform_number_series"
    return f"""
        CREATE OR REPLACE FUNCTION {name}() RETURNS trigger AS $$
        BEGIN
            IF ({_identity_tuple("NEW")})
               IS NOT DISTINCT FROM ({_identity_tuple("OLD")}) THEN
                RETURN NEW;
            END IF;
            IF EXISTS (
                SELECT 1 FROM mod_numbering.{counters} c
                 WHERE {tenant_predicate}c.series_code = NEW.series_code
                UNION ALL
                SELECT 1 FROM mod_numbering.{receipts} r
                 WHERE {receipt_predicate}r.series_code = NEW.series_code
            ) THEN
                RAISE EXCEPTION
                    'mod_numbering.{table} identity fields are frozen once the '
                    'series has allocated (series_code=%)', NEW.series_code
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """  # noqa: S608


def _series_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("series_code", _SERIES_CODE, nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False, server_default=""),
        sa.Column("suffix", sa.String(32), nullable=False, server_default=""),
        sa.Column("separator", sa.String(8), nullable=False, server_default="-"),
        sa.Column("min_digits", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("include_year", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("year_digits", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("include_month", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "reset_policy", sa.String(16), nullable=False, server_default="never"
        ),
        sa.Column("start_value", sa.BigInteger(), nullable=False, server_default="1"),
    ]


def _counter_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("series_code", _SERIES_CODE, nullable=False),
        # NOT NULL everywhere: a non-resetting series uses '*', so no unique
        # key that includes the period has a nullable member.
        sa.Column("period_key", _PERIOD, nullable=False),
        sa.Column("next_value", sa.BigInteger(), nullable=False),
    ]


def _receipt_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("series_code", _SERIES_CODE, nullable=False),
        sa.Column("period_key", _PERIOD, nullable=False),
        sa.Column("allocated_value", sa.BigInteger(), nullable=False),
        sa.Column("formatted_number", sa.String(255), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        # Server default: transaction time records evidence and never feeds a
        # decision, so the module has no reason to read a clock at all.
        sa.Column(
            "allocated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("allocated_by", sa.String(255), nullable=True),
    ]


def _repair_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("series_code", _SERIES_CODE, nullable=False),
        sa.Column("period_key", _PERIOD, nullable=False),
        sa.Column("previous_next_value", sa.BigInteger(), nullable=False),
        sa.Column("new_next_value", sa.BigInteger(), nullable=False),
        sa.Column("proven_minimum", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("repaired_by", sa.String(255), nullable=False),
        sa.Column(
            "repaired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _timestamps() -> list[sa.Column[Any]]:
    return [
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
    ]


def _series_checks(prefix: str) -> list[sa.CheckConstraint]:
    """Mirror the service validation in the database.

    The online roles can write the configuration tables directly, so a rule
    enforced only in Python is a rule a migration or a psql session can walk
    straight past — and a bad configuration here reissues numbers.
    """
    return [
        sa.CheckConstraint(
            "min_digits BETWEEN 1 AND 18", name=f"ck_{prefix}_min_digits"
        ),
        sa.CheckConstraint("year_digits IN (2, 4)", name=f"ck_{prefix}_year_digits"),
        sa.CheckConstraint("start_value >= 1", name=f"ck_{prefix}_start_value"),
        sa.CheckConstraint(
            "reset_policy IN ('never', 'yearly', 'monthly')",
            name=f"ck_{prefix}_reset_policy",
        ),
        # Reset/format coherence: a resetting series that does not print its
        # period reissues the same strings every period.
        sa.CheckConstraint(
            "reset_policy <> 'yearly' OR include_year = 1",
            name=f"ck_{prefix}_yearly_prints_year",
        ),
        sa.CheckConstraint(
            "reset_policy <> 'monthly' OR (include_year = 1 AND include_month = 1)",
            name=f"ck_{prefix}_monthly_prints_month",
        ),
        sa.CheckConstraint(
            "include_month = 0 OR include_year = 1",
            name=f"ck_{prefix}_month_needs_year",
        ),
    ]


def _counter_checks(prefix: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint("next_value >= 1", name=f"ck_{prefix}_next_value"),
        sa.CheckConstraint("period_key <> ''", name=f"ck_{prefix}_period_key"),
    ]


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE", name=name
    )


def upgrade() -> None:
    planes = selected_module_planes(MODULE_CODE)
    require_prerequisites(op.get_bind(), COMMON_REQUIRES)
    if ModulePlane.TENANT in planes:
        require_prerequisites(op.get_bind(), TENANT_REQUIRES)
    if ModulePlane.PLATFORM in planes:
        require_prerequisites(op.get_bind(), PLATFORM_REQUIRES)

    op.execute("CREATE SCHEMA IF NOT EXISTS mod_numbering;")
    op.execute("GRANT USAGE ON SCHEMA mod_numbering TO app_admin;")
    if ModulePlane.PLATFORM in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_numbering TO platform_api;")
    if ModulePlane.TENANT in planes:
        op.execute("GRANT USAGE ON SCHEMA mod_numbering TO app_user;")

    # One refusal function for every append-only table on both planes.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_REFUSE_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'mod_numbering.% is append-only; % is refused',
                TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # The identity freeze, enforced by the database rather than by the service.
    # `configure_series` refuses the transition first and gives a better error;
    # this exists because the online roles can write the configuration tables
    # directly, and a migration or a psql session would otherwise walk straight
    # past a rule that lives only in Python.
    op.execute(
        _freeze_function_sql(
            _FREEZE_TENANT_FUNCTION,
            "series_counters",
            "allocation_receipts",
            "tenant",
        )
    )
    op.execute(
        _freeze_function_sql(
            _FREEZE_PLATFORM_FUNCTION,
            "platform_series_counters",
            "platform_allocation_receipts",
            "platform",
        )
    )

    if ModulePlane.TENANT in planes:
        _upgrade_tenant_plane()
    if ModulePlane.PLATFORM in planes:
        _upgrade_platform_plane()


def _make_append_only(table: str) -> None:
    """Refuse UPDATE and DELETE at the database, for every role."""
    op.execute(
        f"""
        CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON mod_numbering.{table}
            FOR EACH ROW EXECUTE FUNCTION {_REFUSE_FUNCTION}();
        """
    )


def _upgrade_tenant_plane() -> None:
    op.create_table(
        "number_series",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_series_columns(),
        *_timestamps(),
        *_series_checks("number_series"),
        _tenant_fk("fk_number_series_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_number_series_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "series_code", name="uq_number_series_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "series_counters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_counter_columns(),
        *_timestamps(),
        *_counter_checks("series_counters"),
        _tenant_fk("fk_series_counters_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "series_code", "period_key", name="uq_series_counters_identity"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "allocation_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_receipt_columns(),
        _tenant_fk("fk_allocation_receipts_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "series_code",
            "period_key",
            "allocated_value",
            name="uq_allocation_receipts_value",
        ),
        # The rendered string itself. A configuration change could restart a
        # new period scheme at 1 and reproduce an issued number; the
        # value-and-period key cannot see that collision and this can.
        sa.UniqueConstraint(
            "tenant_id",
            "series_code",
            "formatted_number",
            name="uq_allocation_receipts_rendered",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "series_repairs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_repair_columns(),
        _tenant_fk("fk_series_repairs_tenant"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_allocation_receipts_tenant_series",
        "allocation_receipts",
        ["tenant_id", "series_code"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_series_repairs_tenant_series",
        "series_repairs",
        ["tenant_id", "series_code"],
        schema=_SCHEMA,
    )

    for table in (*_TENANT_MUTABLE, *_TENANT_IMMUTABLE):
        op.execute(f"ALTER TABLE mod_numbering.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_numbering.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation
                ON mod_numbering.{table}
                USING (tenant_id = public.app_current_tenant_id())
                WITH CHECK (tenant_id = public.app_current_tenant_id());
            """
        )

    for table in _TENANT_MUTABLE:
        for role in ("app_user", "platform_api"):
            op.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE "
                f"ON mod_numbering.{table} TO {role};"
            )

    op.execute(
        f"""
        CREATE TRIGGER number_series_identity_freeze
            BEFORE UPDATE ON mod_numbering.number_series
            FOR EACH ROW EXECUTE FUNCTION {_FREEZE_TENANT_FUNCTION}();
        """
    )

    for table in _TENANT_IMMUTABLE:
        # Append-only: no UPDATE, no DELETE, for anyone online.
        for role in ("app_user", "platform_api"):
            op.execute(f"GRANT SELECT, INSERT ON mod_numbering.{table} TO {role};")
        _make_append_only(table)


def _upgrade_platform_plane() -> None:
    op.create_table(
        "platform_number_series",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_series_columns(),
        *_timestamps(),
        *_series_checks("platform_number_series"),
        sa.UniqueConstraint("series_code", name="uq_platform_number_series_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_series_counters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_counter_columns(),
        *_timestamps(),
        *_counter_checks("platform_series_counters"),
        sa.UniqueConstraint(
            "series_code", "period_key", name="uq_platform_series_counters_identity"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_allocation_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_receipt_columns(),
        sa.UniqueConstraint(
            "series_code",
            "period_key",
            "allocated_value",
            name="uq_platform_allocation_receipts_value",
        ),
        sa.UniqueConstraint(
            "series_code",
            "formatted_number",
            name="uq_platform_allocation_receipts_rendered",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_series_repairs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_repair_columns(),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_allocation_receipts_series",
        "platform_allocation_receipts",
        ["series_code"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_series_repairs_series",
        "platform_series_repairs",
        ["series_code"],
        schema=_SCHEMA,
    )

    for table in _PLATFORM_MUTABLE:
        for role in ("platform_api", "app_admin"):
            op.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE "
                f"ON mod_numbering.{table} TO {role};"
            )
        op.execute(f"REVOKE ALL ON mod_numbering.{table} FROM app_user;")

    op.execute(
        f"""
        CREATE TRIGGER platform_number_series_identity_freeze
            BEFORE UPDATE ON mod_numbering.platform_number_series
            FOR EACH ROW EXECUTE FUNCTION {_FREEZE_PLATFORM_FUNCTION}();
        """
    )

    for table in _PLATFORM_IMMUTABLE:
        for role in ("platform_api", "app_admin"):
            op.execute(f"GRANT SELECT, INSERT ON mod_numbering.{table} TO {role};")
        # The revocation IS the isolation on this plane.
        op.execute(f"REVOKE ALL ON mod_numbering.{table} FROM app_user;")
        _make_append_only(table)


def downgrade() -> None:
    for table in (
        *_PLATFORM_IMMUTABLE,
        *_PLATFORM_MUTABLE,
        *_TENANT_IMMUTABLE,
        *_TENANT_MUTABLE,
    ):
        op.execute(f"DROP TABLE IF EXISTS mod_numbering.{table} CASCADE;")
    op.execute(f"DROP FUNCTION IF EXISTS {_REFUSE_FUNCTION}() CASCADE;")
    op.execute(f"DROP FUNCTION IF EXISTS {_FREEZE_TENANT_FUNCTION}() CASCADE;")
    op.execute(f"DROP FUNCTION IF EXISTS {_FREEZE_PLATFORM_FUNCTION}() CASCADE;")
