"""The inbound seam: connected accounts, and the observation ledger.

The kernel's communications stack was entirely outbound before this. These two
tables are the durable half of the inbound seam — see
`dotmac_kernel.inbound_models` for the design and
`docs/inventories/inbox-sources.md` § "Prerequisites" for the gap they close.

Hard rule 11 for both: `tenant_id NOT NULL`, composite uniques including
`tenant_id`, RLS ENABLEd *and* FORCEd, a tenant-isolation policy, and the online
role grants. FORCE matters — without it the table owner, which migrations run
as, bypasses its own policy.

## `connected_accounts.credential_name` holds a NAME, never a secret

ADR-0009. The value lives in whatever secret store the product installed through
`dotmac_kernel.secret_sources`, is read once at startup, and is held in memory.
A column holding an API token would put every tenant's provider credentials in
the database the application already has broad read access to, and would turn a
database backup into a credential leak.

## Why `channel` has no CHECK constraint and `processing_status` does

`channel` is an open declaration registry (`dotmac_kernel.channels`) — the whole
point is that a product adds one without a migration, so a CHECK would
reintroduce the `ALTER TYPE` growth problem ADR-0008 records against native
enums. `processing_status` is a closed, mechanical three-value vocabulary that no
product extends, so it is constrained in the database, exactly as
`communication_suppressions` constrains `scope` and `reason`.

Revision ID: 0022_inbound_seam
Revises: 0021_setting_scope_alignment
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_inbound_seam"
down_revision = "0021_setting_scope_alignment"
branch_labels = None
depends_on = None

_ACCOUNTS = "connected_accounts"
_OBSERVATIONS = "inbound_observations"

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        _ACCOUNTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("account_scope", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        # A NAME resolved through secret_sources — never a secret value.
        sa.Column("credential_name", sa.String(160), nullable=True),
        sa.Column("config", _JSON, nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_connected_accounts_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "account_scope",
            name="uq_connected_accounts_tenant_provider_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_connected_accounts_tenant_id_id"
        ),
    )
    op.create_index("ix_connected_accounts_tenant_id", _ACCOUNTS, ["tenant_id"])
    op.create_index(
        "ix_connected_accounts_tenant_channel",
        _ACCOUNTS,
        ["tenant_id", "channel", "is_active"],
    )

    op.create_table(
        _OBSERVATIONS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("account_scope", sa.String(160), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("payload", _JSON, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "processing_status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'recorded'"),
        ),
        sa.Column("error_code", sa.String(120), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_inbound_observations_tenant",
        ),
        # Belt-and-braces: `dotmac_kernel.inbound.admit` makes the at-most-once
        # decision through `idempotency`. This constraint means a writer that
        # bypassed `admit` collides rather than duplicating the fact.
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "account_scope",
            "provider_event_id",
            name="uq_inbound_observations_tenant_event",
        ),
        sa.CheckConstraint(
            "processing_status IN ('recorded', 'processed', 'rejected')",
            name="ck_inbound_observations_status",
        ),
    )
    op.create_index("ix_inbound_observations_tenant_id", _OBSERVATIONS, ["tenant_id"])
    op.create_index(
        "ix_inbound_observations_tenant_status",
        _OBSERVATIONS,
        ["tenant_id", "processing_status", "observed_at"],
    )

    # Literal SQL per table, never looped: the composed gate reads this file
    # statically without importing it, so a statement built from a loop variable
    # is uninspectable and fails closed — correctly.
    op.execute("ALTER TABLE connected_accounts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE connected_accounts FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY connected_accounts_tenant_isolation ON connected_accounts
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON connected_accounts TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON connected_accounts TO platform_api;"
    )

    op.execute("ALTER TABLE inbound_observations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE inbound_observations FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY inbound_observations_tenant_isolation ON inbound_observations
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON inbound_observations TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON inbound_observations TO platform_api;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inbound_observations CASCADE;")
    op.execute("DROP TABLE IF EXISTS connected_accounts CASCADE;")
