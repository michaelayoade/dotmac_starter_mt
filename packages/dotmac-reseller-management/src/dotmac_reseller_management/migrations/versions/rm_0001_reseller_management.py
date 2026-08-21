"""Create the tenant reseller owner.

Revision ID: rm_0001_reseller_management
Revises: (lineage root)
Create Date: 2026-08-20

Every table enforces UNIQUE (tenant_id, id), and every module foreign key
carries that tenant identity.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "rm_0001_reseller_management"
down_revision = None
branch_labels = ("reseller_management",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "outbox_relay.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_reseller"
_TABLES = (
    "reseller_accounts",
    "reseller_authority_revisions",
    "reseller_member_bindings",
    "reseller_customer_account_bindings",
)


def _identity() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
    )


def _tenant_constraints(name: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id"),
    )


def _timestamps() -> tuple[sa.Column[Any], ...]:
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


def _secure_tenant_tables(tables: Iterable[str]) -> None:
    for table in tables:
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {_SCHEMA}.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.{table} TO app_user;"
        )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_reseller;")
    op.execute("REVOKE ALL ON SCHEMA mod_reseller FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_reseller TO app_user, app_admin;")

    op.create_table(
        "reseller_accounts",
        *_identity(),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("party_role_ref", sa.String(255), nullable=False),
        sa.Column("parent_account_id", sa.Uuid(), nullable=True),
        sa.Column("current_authority_revision_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        *_timestamps(),
        *_tenant_constraints("reseller_accounts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_account_id"],
            [
                "mod_reseller.reseller_accounts.tenant_id",
                "mod_reseller.reseller_accounts.id",
            ],
            ondelete="RESTRICT",
            name="fk_reseller_accounts_parent",
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_reseller_accounts_tenant_code"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "party_role_ref",
            name="uq_reseller_accounts_tenant_party_role",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'retired')",
            name="ck_reseller_accounts_status",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "reseller_authority_revisions",
        *_identity(),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("authority_codes", sa.JSON(), nullable=False),
        sa.Column("evidence_ref", sa.String(255), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("reseller_authority_revisions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [
                "mod_reseller.reseller_accounts.tenant_id",
                "mod_reseller.reseller_accounts.id",
            ],
            ondelete="CASCADE",
            name="fk_reseller_authority_revisions_account",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "account_id",
            "version_number",
            name="uq_reseller_authority_revisions_tenant_account_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "account_id",
            "evidence_ref",
            name="uq_reseller_authority_revisions_tenant_account_evidence",
        ),
        sa.CheckConstraint(
            "version_number > 0", name="ck_reseller_authority_revisions_number"
        ),
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_reseller_accounts_current_authority",
        "reseller_accounts",
        "reseller_authority_revisions",
        ["tenant_id", "current_authority_revision_id"],
        ["tenant_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "reseller_member_bindings",
        *_identity(),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("member_ref", sa.String(255), nullable=False),
        sa.Column("evidence_ref", sa.String(255), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("reseller_member_bindings"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [
                "mod_reseller.reseller_accounts.tenant_id",
                "mod_reseller.reseller_accounts.id",
            ],
            ondelete="CASCADE",
            name="fk_reseller_member_bindings_account",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "member_ref",
            name="uq_reseller_member_bindings_tenant_ref",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "reseller_customer_account_bindings",
        *_identity(),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("customer_account_ref", sa.String(255), nullable=False),
        sa.Column("evidence_ref", sa.String(255), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("reseller_customer_account_bindings"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            [
                "mod_reseller.reseller_accounts.tenant_id",
                "mod_reseller.reseller_accounts.id",
            ],
            ondelete="CASCADE",
            name="fk_reseller_customer_account_bindings_account",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "customer_account_ref",
            name="uq_reseller_customer_account_bindings_tenant_ref",
        ),
        schema=_SCHEMA,
    )

    _secure_tenant_tables(_TABLES)


def downgrade() -> None:
    op.drop_constraint(
        "fk_reseller_accounts_current_authority",
        "reseller_accounts",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_reseller;")
