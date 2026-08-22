"""Create tenant customer accounts, profiles, and Party references.

Revision ID: cu_0001_customer_accounts
Revises: (lineage root)
Create Date: 2026-08-20
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "cu_0001_customer_accounts"
down_revision = None
branch_labels = ("customers",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_customers"


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_customers;")
    op.execute("REVOKE ALL ON SCHEMA mod_customers FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_customers TO app_user, app_admin;")

    op.create_table(
        "customer_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_number", sa.String(40), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PROSPECT"),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_customer_accounts_tenant",
        ),
        sa.CheckConstraint(
            "status IN ('PROSPECT', 'ACTIVE', 'SUSPENDED', 'CLOSED')",
            name="ck_customer_accounts_status",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_customer_accounts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "account_number", name="uq_customer_accounts_tenant_number"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_customer_accounts_tenant_status",
        "customer_accounts",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "customer_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("segment", sa.String(40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_customer_profiles_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [
                "mod_customers.customer_accounts.tenant_id",
                "mod_customers.customer_accounts.id",
            ],
            ondelete="CASCADE",
            name="fk_customer_profiles_tenant_account",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_customer_profiles_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "account_id", name="uq_customer_profiles_tenant_account"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "customer_party_references",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("party_system", sa.String(80), nullable=False),
        sa.Column("party_reference", sa.String(160), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_customer_party_references_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [
                "mod_customers.customer_accounts.tenant_id",
                "mod_customers.customer_accounts.id",
            ],
            ondelete="CASCADE",
            name="fk_customer_party_references_tenant_account",
        ),
        sa.CheckConstraint(
            "role IN ('ACCOUNT_HOLDER', 'BILLING_CONTACT', 'SERVICE_USER')",
            name="ck_customer_party_references_role",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_customer_party_references_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "account_id",
            "party_system",
            "party_reference",
            "role",
            name="uq_customer_party_references_identity",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_customer_party_references_tenant_party",
        "customer_party_references",
        ["tenant_id", "party_system", "party_reference"],
        schema=_SCHEMA,
    )

    op.execute("ALTER TABLE mod_customers.customer_accounts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_customers.customer_accounts FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY customer_accounts_tenant_isolation ON mod_customers.customer_accounts USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_customers.customer_accounts TO app_user;"
    )
    op.execute("ALTER TABLE mod_customers.customer_profiles ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_customers.customer_profiles FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY customer_profiles_tenant_isolation ON mod_customers.customer_profiles USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_customers.customer_profiles TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_customers.customer_party_references ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_customers.customer_party_references FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY customer_party_references_tenant_isolation ON mod_customers.customer_party_references USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_customers.customer_party_references TO app_user;"
    )


def downgrade() -> None:
    for table in (
        "customer_party_references",
        "customer_profiles",
        "customer_accounts",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_customers;")
