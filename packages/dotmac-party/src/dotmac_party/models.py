"""Tenant Party context tables in the immutable ``mod_party`` namespace."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("party")
_JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class PartyRole(Base, TimestampMixin):
    """One concurrent, temporal business capacity held by a kernel Party."""

    __tablename__ = "party_roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_party_roles_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "party_id",
            "role_type",
            "role_key",
            name="uq_party_roles_tenant_party_type_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_party_roles_tenant_party",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'ended')",
            name="ck_party_roles_status",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="ck_party_roles_valid_window",
        ),
        Index("ix_party_roles_tenant_type_status", "tenant_id", "role_type", "status"),
        Index("ix_party_roles_tenant_party", "tenant_id", "party_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    role_type: Mapped[str] = mapped_column(String(63), nullable=False)
    role_key: Mapped[str] = mapped_column(
        String(80), nullable=False, default="default", server_default="default"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(120))
    metadata_: Mapped[dict[str, object] | None] = mapped_column(
        "metadata", _JSON_VARIANT
    )


class PartyRelationship(Base, TimestampMixin):
    """A directional business fact between two exact Party capacities."""

    __tablename__ = "party_relationships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_party_relationships_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "subject_role_id",
            "object_role_id",
            "relationship_type",
            "relationship_key",
            name="uq_party_relationships_tenant_roles_type_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "subject_role_id"],
            [f"{SCHEMA}.party_roles.tenant_id", f"{SCHEMA}.party_roles.id"],
            ondelete="CASCADE",
            name="fk_party_relationships_subject_role",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "object_role_id"],
            [f"{SCHEMA}.party_roles.tenant_id", f"{SCHEMA}.party_roles.id"],
            ondelete="CASCADE",
            name="fk_party_relationships_object_role",
        ),
        CheckConstraint(
            "subject_role_id <> object_role_id",
            name="ck_party_relationships_not_self",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'inactive', 'ended')",
            name="ck_party_relationships_status",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="ck_party_relationships_valid_window",
        ),
        Index(
            "ix_party_relationships_tenant_subject",
            "tenant_id",
            "subject_role_id",
            "relationship_type",
            "status",
        ),
        Index(
            "ix_party_relationships_tenant_object",
            "tenant_id",
            "object_role_id",
            "relationship_type",
            "status",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_role_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    object_role_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(63), nullable=False)
    relationship_key: Mapped[str] = mapped_column(
        String(80), nullable=False, default="default", server_default="default"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active"
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(120))
    metadata_: Mapped[dict[str, object] | None] = mapped_column(
        "metadata", _JSON_VARIANT
    )


class PartyMembership(Base, TimestampMixin):
    """A Person Party's explicit Organization Party context and bounded scope."""

    __tablename__ = "party_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_party_memberships_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "person_party_id",
            "organization_party_id",
            "membership_type",
            "membership_key",
            name="uq_party_memberships_tenant_parties_type_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_party_memberships_tenant_person_party",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "organization_party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_party_memberships_tenant_organization_party",
        ),
        ForeignKeyConstraint(
            ["person_party_id"],
            ["party_persons.party_id"],
            ondelete="CASCADE",
            name="fk_party_memberships_person_profile",
        ),
        ForeignKeyConstraint(
            ["organization_party_id"],
            ["party_organizations.party_id"],
            ondelete="CASCADE",
            name="fk_party_memberships_organization_profile",
        ),
        CheckConstraint(
            "person_party_id <> organization_party_id",
            name="ck_party_memberships_not_self",
        ),
        CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'ended')",
            name="ck_party_memberships_status",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="ck_party_memberships_valid_window",
        ),
        Index(
            "ix_party_memberships_tenant_person",
            "tenant_id",
            "person_party_id",
            "status",
        ),
        Index(
            "ix_party_memberships_tenant_organization",
            "tenant_id",
            "organization_party_id",
            "status",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    person_party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    organization_party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    membership_type: Mapped[str] = mapped_column(String(63), nullable=False)
    membership_key: Mapped[str] = mapped_column(
        String(80), nullable=False, default="default", server_default="default"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="invited", server_default="invited"
    )
    access_scope: Mapped[dict[str, object]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(120))
    metadata_: Mapped[dict[str, object] | None] = mapped_column(
        "metadata", _JSON_VARIANT
    )


class PartyContactPoint(Base, TimestampMixin):
    """Reachability evidence; a value is never proof that two Parties are one."""

    __tablename__ = "party_contact_points"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_party_contact_points_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "party_id",
            "channel_type",
            "normalized_value",
            "scope_key",
            name="uq_party_contact_points_tenant_party_channel_value_scope",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_party_contact_points_tenant_party",
        ),
        CheckConstraint(
            "verification_status IN ('unverified', 'pending', 'verified', 'failed')",
            name="ck_party_contact_points_verification",
        ),
        CheckConstraint(
            "consent_status IN "
            "('unknown', 'opted_in', 'opted_out', 'not_applicable')",
            name="ck_party_contact_points_consent",
        ),
        CheckConstraint(
            "(provider IS NULL AND provider_account_id IS NULL AND "
            "external_subject_id IS NULL) OR "
            "(provider IS NOT NULL AND provider_account_id IS NOT NULL AND "
            "external_subject_id IS NOT NULL)",
            name="ck_party_contact_points_provider_identity_complete",
        ),
        Index(
            "ix_party_contact_points_tenant_lookup",
            "tenant_id",
            "channel_type",
            "normalized_value",
            "is_active",
        ),
        Index(
            "uq_party_contact_points_tenant_primary",
            "tenant_id",
            "party_id",
            "channel_type",
            "scope_key",
            unique=True,
            postgresql_where=sa.text("is_primary AND is_active"),
            sqlite_where=sa.text("is_primary AND is_active"),
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(63), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(320), nullable=False)
    display_value: Mapped[str | None] = mapped_column(String(320))
    scope_key: Mapped[str] = mapped_column(
        String(200), nullable=False, default="default", server_default="default"
    )
    provider: Mapped[str | None] = mapped_column(String(80))
    provider_account_id: Mapped[str | None] = mapped_column(String(200))
    external_subject_id: Mapped[str | None] = mapped_column(String(200))
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )
    verification_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unverified", server_default="unverified"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_source: Mapped[str | None] = mapped_column(String(120))
    consent_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unknown", server_default="unknown"
    )
    consent_captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    consent_source: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str | None] = mapped_column(String(120))
    metadata_: Mapped[dict[str, object] | None] = mapped_column(
        "metadata", _JSON_VARIANT
    )


class PartyExternalReference(Base, TimestampMixin):
    """Source-labelled import provenance; never native lookup authority."""

    __tablename__ = "party_external_references"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_party_external_refs_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "party_id",
            "source_system",
            "entity_type",
            "external_id",
            name="uq_party_external_refs_tenant_party_source_entity_external",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "entity_type",
            "external_id",
            name="uq_party_external_refs_tenant_source_entity_external",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_party_external_refs_tenant_party",
        ),
        CheckConstraint(
            "length(trim(source_system)) > 0 AND length(trim(entity_type)) > 0 "
            "AND length(trim(external_id)) > 0 AND length(trim(source)) > 0",
            name="ck_party_external_refs_required_evidence",
        ),
        Index("ix_party_external_refs_tenant_party", "tenant_id", "party_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    metadata_: Mapped[dict[str, object] | None] = mapped_column(
        "metadata", _JSON_VARIANT
    )


TENANT_TABLES = (
    "party_roles",
    "party_relationships",
    "party_memberships",
    "party_contact_points",
    "party_external_references",
)


def metadata_table(name: str) -> sa.Table:
    return Base.metadata.tables[f"{SCHEMA}.{name}"]


__all__ = [
    "PartyContactPoint",
    "PartyExternalReference",
    "PartyMembership",
    "PartyRelationship",
    "PartyRole",
    "SCHEMA",
    "TENANT_TABLES",
    "metadata_table",
]
