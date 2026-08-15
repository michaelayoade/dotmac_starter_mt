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
COMMON_REQUIRES = ("module_database_roles.v1",)
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
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column("repaired_at", sa.DateTime(timezone=True), nullable=False),
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


def _created_only() -> list[sa.Column[Any]]:
    """Append-only tables get a creation instant and no `updated_at`."""
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        )
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
        *_created_only(),
        _tenant_fk("fk_allocation_receipts_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "series_code",
            "period_key",
            "allocated_value",
            name="uq_allocation_receipts_value",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "series_repairs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_repair_columns(),
        *_created_only(),
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
        sa.UniqueConstraint("series_code", name="uq_platform_number_series_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_series_counters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_counter_columns(),
        *_timestamps(),
        sa.UniqueConstraint(
            "series_code", "period_key", name="uq_platform_series_counters_identity"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_allocation_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_receipt_columns(),
        *_created_only(),
        sa.UniqueConstraint(
            "series_code",
            "period_key",
            "allocated_value",
            name="uq_platform_allocation_receipts_value",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_series_repairs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_repair_columns(),
        *_created_only(),
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

    for table in _PLATFORM_IMMUTABLE:
        for role in ("platform_api", "app_admin"):
            op.execute(f"GRANT SELECT, INSERT ON mod_numbering.{table} TO {role};")
        # The revocation IS the isolation on this plane.
        op.execute(f"REVOKE ALL ON mod_numbering.{table} FROM app_user;")
        _make_append_only(table)


def downgrade() -> None:
    for table in (*_PLATFORM_IMMUTABLE, *_PLATFORM_MUTABLE, *_TENANT_IMMUTABLE, *_TENANT_MUTABLE):
        op.execute(f"DROP TABLE IF EXISTS mod_numbering.{table} CASCADE;")
    op.execute(f"DROP FUNCTION IF EXISTS {_REFUSE_FUNCTION}() CASCADE;")
