"""Create the tenant and platform document-series planes (ADR-0023, ADR-0030).

## What this lineage needs

A series needs a tenant to hang a foreign key on and roles to grant to. Two
logical prerequisites, never a physical edge to a foreign revision:

- `tenant_scope_catalog.v1` — the FK target `public.tenants.id` and the
  `public.app_current_tenant_id()` the RLS policies evaluate;
- `module_database_roles.v1` — `app_user`, `platform_api` and `app_admin`.

## The two planes

Tenant tables carry `tenant_id NOT NULL`, a composite tenant identity and
FORCEd row-level security — the policy is the isolation (hard rule 11).

Platform tables carry no tenant column and no RLS, and are REVOKEd from
`app_user` across every privilege — there, the revocation IS the isolation
(ADR-0023). No foreign key crosses between the planes.

## Why the constraints are shaped this way

Two uniques per receipt table, and they answer different failures the source
audit found:

- `(scope, series_code, idempotency_key)` is the replay identity. Without it a
  retried request allocates twice.
- `(scope, series_code, allocated_value)` makes a duplicate number impossible
  at the database rather than unlikely in the application. Sub relies on the
  CONSUMING table's unique index plus a ten-thousand-iteration retry loop,
  which means the collision is discovered after the number has been formatted
  and handed around.

There is deliberately no unique index on the counter itself: the counter is
mutable state guarded by `SELECT ... FOR UPDATE`, and the receipt is the
immutable record. Getting that the wrong way round is how a reset rewrites
history.

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

# Literals, not imported constants: a migration is a snapshot of an accepted
# decision, and the composed gate reads this list statically to diff it against
# `dotmac_numbering.manifest`.
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
_TENANT_TABLES = ("number_series", "allocation_receipts")
_PLATFORM_TABLES = ("platform_number_series", "platform_allocation_receipts")


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
        sa.Column("reset_policy", sa.String(16), nullable=False, server_default="never"),
        sa.Column("next_value", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("current_period", sa.String(7), nullable=True),
    ]


def _receipt_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("series_code", _SERIES_CODE, nullable=False),
        sa.Column("allocated_value", sa.BigInteger(), nullable=False),
        sa.Column("formatted_number", sa.String(255), nullable=False),
        # Required, not defaulted. A receipt whose reset decision cannot be
        # re-derived is not evidence.
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("period", sa.String(7), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("allocated_by", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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

    if ModulePlane.TENANT in planes:
        _upgrade_tenant_plane()
    if ModulePlane.PLATFORM in planes:
        _upgrade_platform_plane()


def _upgrade_tenant_plane() -> None:
    """Built only where the assembly explicitly selected TENANT."""
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
        "allocation_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_receipt_columns(),
        *_timestamps(),
        _tenant_fk("fk_allocation_receipts_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "series_code",
            "idempotency_key",
            name="uq_allocation_receipts_identity",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "series_code",
            "allocated_value",
            name="uq_allocation_receipts_value",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_allocation_receipts_tenant_series",
        "allocation_receipts",
        ["tenant_id", "series_code"],
        schema=_SCHEMA,
    )

    for table in _TENANT_TABLES:
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
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON mod_numbering.{table} TO app_user;"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON mod_numbering.{table} TO platform_api;"
        )


def _upgrade_platform_plane() -> None:
    """Built only where the assembly explicitly selected PLATFORM."""
    op.create_table(
        "platform_number_series",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_series_columns(),
        *_timestamps(),
        sa.UniqueConstraint("series_code", name="uq_platform_number_series_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "platform_allocation_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_receipt_columns(),
        *_timestamps(),
        sa.UniqueConstraint(
            "series_code",
            "idempotency_key",
            name="uq_platform_allocation_receipts_identity",
        ),
        sa.UniqueConstraint(
            "series_code",
            "allocated_value",
            name="uq_platform_allocation_receipts_value",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_platform_allocation_receipts_series",
        "platform_allocation_receipts",
        ["series_code"],
        schema=_SCHEMA,
    )

    for table in _PLATFORM_TABLES:
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON mod_numbering.{table} TO platform_api;"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON mod_numbering.{table} TO app_admin;"
        )
        # The revocation IS the isolation on this plane.
        op.execute(f"REVOKE ALL ON mod_numbering.{table} FROM app_user;")


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS mod_numbering.platform_allocation_receipts CASCADE;"
    )
    op.execute("DROP TABLE IF EXISTS mod_numbering.platform_number_series CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_numbering.allocation_receipts CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_numbering.number_series CASCADE;")
