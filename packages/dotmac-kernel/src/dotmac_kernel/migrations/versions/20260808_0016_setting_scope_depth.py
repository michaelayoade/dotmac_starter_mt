"""Setting scopes gain depth, and isolation stops doing double duty.

Kernel lineage continuation (0015 -> 0016).

`tenant_id` carried two meanings: which tenant owns the row (isolation, what RLS
keys on) and how specific the value is (precedence, what resolution walks).
Conflating them capped the hierarchy at platform-or-tenant, because there was
nowhere to put a third level.

`tenant_id` now does isolation and only isolation — **the RLS policies are not
touched by this migration**, which is the point: tenant ownership stays a stored
fact the security predicate reads directly, rather than something derived per
row. `scope_kind` and `scope_id` carry precedence, always within that tenant.

Backfill is exact: a row with no tenant was the platform scope, and any other
row was tenant-wide. Nothing moves, and every existing value resolves to the
same answer afterwards.

The two partial unique indexes are replaced by ONE index over
`COALESCE`d columns. Postgres treats every NULL as distinct inside a unique
index, so a nullable column in one admits duplicates — that is how `dotmac_erp`
came to hold duplicate global settings. Coalescing to a sentinel removes the
NULL, closing the bug class rather than patching an instance.

Revision ID: 0016_setting_scope_depth
Revises: 0015_open_value_types
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016_setting_scope_depth"
down_revision = "0015_open_value_types"
branch_labels = None
depends_on = None

_NIL = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.add_column(
        "domain_settings",
        sa.Column("scope_kind", sa.String(40), nullable=False, server_default="tenant"),
    )
    op.add_column("domain_settings", sa.Column("scope_id", sa.Uuid(), nullable=True))
    # Exact backfill: no tenant meant the platform scope; anything else was
    # tenant-wide. Every existing value resolves to the same answer after this.
    op.execute(
        sa.text(
            "UPDATE domain_settings SET scope_kind = 'platform' "
            "WHERE tenant_id IS NULL"
        )
    )

    op.drop_index("uq_domain_settings_platform", table_name="domain_settings")
    op.drop_index("uq_domain_settings_tenant", table_name="domain_settings")
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_domain_settings_scope ON domain_settings ("
            f"COALESCE(tenant_id, '{_NIL}'), scope_kind, "
            f"COALESCE(scope_id, '{_NIL}'), domain, key)"
        )
    )
    op.create_index(
        "ix_domain_settings_scope_lookup",
        "domain_settings",
        ["scope_kind", "scope_id"],
    )


def downgrade() -> None:
    """Drop the depth. DESTRUCTIVE for any row below tenant level.

    A row scoped to a site or a user has nowhere to go in a two-level model —
    keeping it would silently promote it to a tenant-wide value, which is worse
    than removing it.
    """
    op.execute(
        sa.text(
            "DELETE FROM domain_settings WHERE scope_kind NOT IN "
            "('platform', 'tenant')"
        )
    )
    op.drop_index("ix_domain_settings_scope_lookup", table_name="domain_settings")
    op.drop_index("uq_domain_settings_scope", table_name="domain_settings")
    op.create_index(
        "uq_domain_settings_platform",
        "domain_settings",
        ["domain", "key"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
    )
    op.create_index(
        "uq_domain_settings_tenant",
        "domain_settings",
        ["tenant_id", "domain", "key"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )
    op.drop_column("domain_settings", "scope_id")
    op.drop_column("domain_settings", "scope_kind")
