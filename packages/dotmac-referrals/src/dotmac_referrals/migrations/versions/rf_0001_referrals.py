"""Create the tenant referral owner.

Revision ID: rf_0001_referrals
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

revision = "rf_0001_referrals"
down_revision = None
branch_labels = ("referrals",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "outbox_relay.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_referrals"
_TABLES = (
    "referral_programmes",
    "referral_programme_versions",
    "referral_codes",
    "referrals",
    "referral_conversions",
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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_referrals;")
    op.execute("REVOKE ALL ON SCHEMA mod_referrals FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_referrals TO app_user, app_admin;")

    op.create_table(
        "referral_programmes",
        *_identity(),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("qualification_policy_ref", sa.String(255), nullable=False),
        sa.Column("reward_policy_ref", sa.String(255), nullable=False),
        sa.Column("active_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        *_timestamps(),
        *_tenant_constraints("referral_programmes"),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_referral_programmes_tenant_code"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'retired')",
            name="ck_referral_programmes_status",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "referral_programme_versions",
        *_identity(),
        sa.Column("programme_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("qualification_policy_ref", sa.String(255), nullable=False),
        sa.Column("reward_policy_ref", sa.String(255), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("referral_programme_versions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "programme_id"],
            [
                "mod_referrals.referral_programmes.tenant_id",
                "mod_referrals.referral_programmes.id",
            ],
            ondelete="CASCADE",
            name="fk_referral_programme_versions_programme",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "programme_id",
            "version_number",
            name="uq_referral_programme_versions_tenant_programme_version",
        ),
        sa.CheckConstraint(
            "version_number > 0", name="ck_referral_programme_versions_number"
        ),
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_referral_programmes_active_version",
        "referral_programmes",
        "referral_programme_versions",
        ["tenant_id", "active_version_id"],
        ["tenant_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "referral_codes",
        *_identity(),
        sa.Column("programme_id", sa.Uuid(), nullable=False),
        sa.Column("referrer_ref", sa.String(255), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("referral_codes"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "programme_id"],
            [
                "mod_referrals.referral_programmes.tenant_id",
                "mod_referrals.referral_programmes.id",
            ],
            ondelete="RESTRICT",
            name="fk_referral_codes_programme",
        ),
        sa.UniqueConstraint("tenant_id", "code", name="uq_referral_codes_tenant_code"),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_referral_codes_status",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at", name="ck_referral_codes_finite_expiry"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_referral_codes_tenant_programme",
        "referral_codes",
        ["tenant_id", "programme_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "referrals",
        *_identity(),
        sa.Column("programme_id", sa.Uuid(), nullable=False),
        sa.Column("code_id", sa.Uuid(), nullable=False),
        sa.Column("referred_subject_ref", sa.String(255), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(255), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="attributed"),
        sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("referrals"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "programme_id"],
            [
                "mod_referrals.referral_programmes.tenant_id",
                "mod_referrals.referral_programmes.id",
            ],
            ondelete="RESTRICT",
            name="fk_referrals_programme",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "code_id"],
            [
                "mod_referrals.referral_codes.tenant_id",
                "mod_referrals.referral_codes.id",
            ],
            ondelete="RESTRICT",
            name="fk_referrals_code",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_referrals_tenant_source_event",
        ),
        sa.CheckConstraint(
            "status IN ('attributed', 'converted', 'rejected', 'expired')",
            name="ck_referrals_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_referrals_tenant_subject",
        "referrals",
        ["tenant_id", "referred_subject_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "referral_conversions",
        *_identity(),
        sa.Column("referral_id", sa.Uuid(), nullable=False),
        sa.Column("conversion_ref", sa.String(255), nullable=False),
        sa.Column("qualification_evidence_digest", sa.String(64), nullable=False),
        sa.Column("reward_request_ref", sa.String(255), nullable=False),
        sa.Column("outbox_event_id", sa.Uuid(), nullable=False),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("referral_conversions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "referral_id"],
            ["mod_referrals.referrals.tenant_id", "mod_referrals.referrals.id"],
            ondelete="RESTRICT",
            name="fk_referral_conversions_referral",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "referral_id",
            name="uq_referral_conversions_tenant_referral",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "conversion_ref",
            name="uq_referral_conversions_tenant_ref",
        ),
        schema=_SCHEMA,
    )

    _secure_tenant_tables(_TABLES)


def downgrade() -> None:
    op.drop_constraint(
        "fk_referral_programmes_active_version",
        "referral_programmes",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_referrals;")
