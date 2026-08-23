"""Create tenant Party roles, relationships, memberships, and reachability.

Revision ID: pa_0001_party_context
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "pa_0001_party_context"
down_revision = None
branch_labels = ("party",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "party_person_catalog.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_party"
_JSON = postgresql.JSONB(astext_type=sa.Text())


def _timestamps() -> tuple[sa.Column[Any], sa.Column[Any]]:
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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_party;")
    op.execute("REVOKE ALL ON SCHEMA mod_party FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_party TO app_user, app_admin;")

    op.create_table(
        "party_roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        # Open product vocabulary: deliberately no enum or CHECK list.
        sa.Column("role_type", sa.String(63), nullable=False),
        sa.Column("role_key", sa.String(80), nullable=False, server_default="default"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("metadata", _JSON, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_party_roles_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="CASCADE",
            name="fk_party_roles_tenant_party",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'ended')",
            name="ck_party_roles_status",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="ck_party_roles_valid_window",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_party_roles_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "party_id",
            "role_type",
            "role_key",
            name="uq_party_roles_tenant_party_type_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_party_roles_tenant_type_status",
        "party_roles",
        ["tenant_id", "role_type", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_party_roles_tenant_party",
        "party_roles",
        ["tenant_id", "party_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "party_relationships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_role_id", sa.Uuid(), nullable=False),
        sa.Column("object_role_id", sa.Uuid(), nullable=False),
        # Open product vocabulary: deliberately no enum or CHECK list.
        sa.Column("relationship_type", sa.String(63), nullable=False),
        sa.Column(
            "relationship_key", sa.String(80), nullable=False, server_default="default"
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("metadata", _JSON, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_party_relationships_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "subject_role_id"],
            ["mod_party.party_roles.tenant_id", "mod_party.party_roles.id"],
            ondelete="CASCADE",
            name="fk_party_relationships_subject_role",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "object_role_id"],
            ["mod_party.party_roles.tenant_id", "mod_party.party_roles.id"],
            ondelete="CASCADE",
            name="fk_party_relationships_object_role",
        ),
        sa.CheckConstraint(
            "subject_role_id <> object_role_id",
            name="ck_party_relationships_not_self",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'inactive', 'ended')",
            name="ck_party_relationships_status",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="ck_party_relationships_valid_window",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_party_relationships_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_role_id",
            "object_role_id",
            "relationship_type",
            "relationship_key",
            name="uq_party_relationships_tenant_roles_type_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_party_relationships_tenant_subject",
        "party_relationships",
        ["tenant_id", "subject_role_id", "relationship_type", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_party_relationships_tenant_object",
        "party_relationships",
        ["tenant_id", "object_role_id", "relationship_type", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "party_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("person_party_id", sa.Uuid(), nullable=False),
        sa.Column("organization_party_id", sa.Uuid(), nullable=False),
        # Open product vocabulary: deliberately no enum or CHECK list.
        sa.Column("membership_type", sa.String(63), nullable=False),
        sa.Column(
            "membership_key", sa.String(80), nullable=False, server_default="default"
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="invited"),
        sa.Column(
            "access_scope", _JSON, nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("metadata", _JSON, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_party_memberships_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_party_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="CASCADE",
            name="fk_party_memberships_tenant_person_party",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "organization_party_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="CASCADE",
            name="fk_party_memberships_tenant_organization_party",
        ),
        sa.ForeignKeyConstraint(
            ["person_party_id"],
            ["public.party_persons.party_id"],
            ondelete="CASCADE",
            name="fk_party_memberships_person_profile",
        ),
        sa.ForeignKeyConstraint(
            ["organization_party_id"],
            ["public.party_organizations.party_id"],
            ondelete="CASCADE",
            name="fk_party_memberships_organization_profile",
        ),
        sa.CheckConstraint(
            "person_party_id <> organization_party_id",
            name="ck_party_memberships_not_self",
        ),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'ended')",
            name="ck_party_memberships_status",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="ck_party_memberships_valid_window",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_party_memberships_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "person_party_id",
            "organization_party_id",
            "membership_type",
            "membership_key",
            name="uq_party_memberships_tenant_parties_type_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_party_memberships_tenant_person",
        "party_memberships",
        ["tenant_id", "person_party_id", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_party_memberships_tenant_organization",
        "party_memberships",
        ["tenant_id", "organization_party_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "party_contact_points",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        # Open product vocabulary: deliberately no enum or CHECK list.
        sa.Column("channel_type", sa.String(63), nullable=False),
        sa.Column("normalized_value", sa.String(320), nullable=False),
        sa.Column("display_value", sa.String(320), nullable=True),
        sa.Column(
            "scope_key", sa.String(200), nullable=False, server_default="default"
        ),
        sa.Column("provider", sa.String(80), nullable=True),
        sa.Column("provider_account_id", sa.String(200), nullable=True),
        sa.Column("external_subject_id", sa.String(200), nullable=True),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "verification_status",
            sa.String(24),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_source", sa.String(120), nullable=True),
        sa.Column(
            "consent_status", sa.String(24), nullable=False, server_default="unknown"
        ),
        sa.Column("consent_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_source", sa.String(120), nullable=True),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("metadata", _JSON, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_party_contact_points_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="CASCADE",
            name="fk_party_contact_points_tenant_party",
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'pending', 'verified', 'failed')",
            name="ck_party_contact_points_verification",
        ),
        sa.CheckConstraint(
            "consent_status IN "
            "('unknown', 'opted_in', 'opted_out', 'not_applicable')",
            name="ck_party_contact_points_consent",
        ),
        sa.CheckConstraint(
            "(provider IS NULL AND provider_account_id IS NULL AND "
            "external_subject_id IS NULL) OR "
            "(provider IS NOT NULL AND provider_account_id IS NOT NULL AND "
            "external_subject_id IS NOT NULL)",
            name="ck_party_contact_points_provider_identity_complete",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_party_contact_points_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "party_id",
            "channel_type",
            "normalized_value",
            "scope_key",
            name="uq_party_contact_points_tenant_party_channel_value_scope",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_party_contact_points_tenant_lookup",
        "party_contact_points",
        ["tenant_id", "channel_type", "normalized_value", "is_active"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_party_contact_points_tenant_primary",
        "party_contact_points",
        ["tenant_id", "party_id", "channel_type", "scope_key"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("is_primary AND is_active"),
    )

    op.create_table(
        "party_external_references",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        sa.Column("source_system", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(120), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("metadata", _JSON, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_party_external_refs_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="CASCADE",
            name="fk_party_external_refs_tenant_party",
        ),
        sa.CheckConstraint(
            "length(trim(source_system)) > 0 AND length(trim(entity_type)) > 0 "
            "AND length(trim(external_id)) > 0 AND length(trim(source)) > 0",
            name="ck_party_external_refs_required_evidence",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_party_external_refs_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "party_id",
            "source_system",
            "entity_type",
            "external_id",
            name="uq_party_external_refs_tenant_party_source_entity_external",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "entity_type",
            "external_id",
            name="uq_party_external_refs_tenant_source_entity_external",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_party_external_refs_tenant_party",
        "party_external_references",
        ["tenant_id", "party_id"],
        schema=_SCHEMA,
    )

    for table in (
        "party_roles",
        "party_relationships",
        "party_memberships",
        "party_contact_points",
        "party_external_references",
    ):
        op.execute(f"ALTER TABLE mod_party.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_party.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_party.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON mod_party.{table} TO app_user;"
        )


def downgrade() -> None:
    for table in (
        "party_external_references",
        "party_contact_points",
        "party_memberships",
        "party_relationships",
        "party_roles",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_party;")
