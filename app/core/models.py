"""Declarative base + shared mixins + core cross-cutting models.

`Tenant`, `TenantDomain`, `Party` (+ subtype tables `PartyPerson`/
`PartyOrganization`), `Role`, `PartyRole`, and `AuthSession` live here — not
under `app/features/*` — because core code needs them directly:
`app.core.deps` (the `require_*` route guards) queries `Party`, `AuthSession`,
`Role`, and `PartyRole`, and `app.core.middleware.tenant` (the tenant resolver)
queries `Tenant`/`TenantDomain`. Core must not import features (import-linter
enforces this), so these identity/tenancy primitives — genuinely cross-cutting,
same rationale as `app.core.audit.AuditEvent` — live in core instead.

**Party identity model (spec amendment 2026-07-17):** `Person` is replaced by
`Party` (`party_type` person|organization) with subtype tables. This is the
fleet-wide identity source of truth — ERP customers/suppliers, CRM
contacts/companies, and sub subscribers are all party roles; future features
attach role tables to `parties` instead of inventing new identity tables.
Subtype tables (`party_persons`, `party_organizations`) carry NO `tenant_id`
of their own — they inherit tenant isolation through the FK to `parties`,
enforced by an `EXISTS`-based RLS policy (see the migration). Auth
credentials, RBAC grants, and audit actors all bind to `party_id`.

Everything that is *not* needed outside its own feature stays local to that
feature's `models.py` (e.g. `UserCredential` in `app.features.auth.models`,
referencing these tables only via string-form `ForeignKey`/`ForeignKeyConstraint`
— no import required).
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


def uuid_pk() -> Mapped[UUID]:
    return mapped_column(Uuid(), primary_key=True, default=uuid4)


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class Tenant(Base, TimestampMixin):
    """Platform-level table — NO `tenant_id` column on it (it IS the tenant).

    RLS is NOT applied to `tenants` or `tenant_domains` — those are read by the
    resolver middleware before tenant context is established.
    """

    __tablename__ = "tenants"

    id: Mapped[UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(
        String(63), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    domains: Mapped[list[TenantDomain]] = relationship(
        back_populates="tenant",
        cascade="all, delete-orphan",
    )


class TenantDomain(Base, TimestampMixin):
    """Custom-domain mapping.

    Subdomain on platform_root_domain works without a row here.
    """

    __tablename__ = "tenant_domains"
    __table_args__ = (UniqueConstraint("domain", name="uq_tenant_domains_domain"),)

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped[Tenant] = relationship(back_populates="domains")


class PartyType(str, enum.Enum):
    person = "person"
    organization = "organization"


class Party(Base, TimestampMixin):
    """Party — the identity source of truth (person | organization).

    Every tenant-scoped model follows this template:
    - `tenant_id UUID NOT NULL REFERENCES tenants(id)`
    - Composite uniqueness on `(tenant_id, X)` for any X that's "globally unique"
      per tenant
    - RLS enabled in the migration that creates the table

    `email` is nullable (organization parties commonly have none) with a
    case-insensitive partial unique index `(tenant_id, lower(email))
    WHERE email IS NOT NULL` — see the migration. Profile data lives on the
    subtype tables (`PartyPerson`/`PartyOrganization`), joined 1:1 on `id`.
    """

    __tablename__ = "parties"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_parties_tenant_id_id"),
        Index(
            "uq_parties_tenant_lower_email",
            "tenant_id",
            sa.text("lower(email)"),
            unique=True,
            postgresql_where=sa.text("email IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    party_type: Mapped[PartyType] = mapped_column(
        sa.Enum(
            PartyType,
            name="ck_parties_party_type",
            native_enum=False,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    person_profile: Mapped[PartyPerson | None] = relationship(
        back_populates="party",
        uselist=False,
        cascade="all, delete-orphan",
    )
    organization_profile: Mapped[PartyOrganization | None] = relationship(
        back_populates="party",
        uselist=False,
        cascade="all, delete-orphan",
    )


class PartyPerson(Base):
    """Person subtype profile — 1:1 with a `party_type == person` `Party`.

    No `tenant_id` column: isolation is inherited through the FK to
    `parties`, enforced by an `EXISTS`-based RLS policy (see the migration).
    """

    __tablename__ = "party_persons"

    party_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("parties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    party: Mapped[Party] = relationship(back_populates="person_profile")


class PartyOrganization(Base):
    """Organization subtype profile — 1:1 with a `party_type == organization` `Party`.

    No `tenant_id` column: isolation is inherited through the FK to
    `parties`, enforced by an `EXISTS`-based RLS policy (see the migration).
    """

    __tablename__ = "party_organizations"

    party_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("parties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)

    party: Mapped[Party] = relationship(back_populates="organization_profile")


class Role(Base, TimestampMixin):
    """Tenant-scoped role.

    The audit event model lives in app.core.audit (cross-cutting write-side).
    """

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_roles_tenant_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_roles_tenant_id_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class PartyRole(Base, TimestampMixin):
    __tablename__ = "party_roles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "party_id", "role_id", name="uq_party_roles_member"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_party_roles_tenant_party",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            ondelete="CASCADE",
            name="fk_party_roles_tenant_role",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False, index=True)
    role_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False, index=True)


class AuthSession(Base, TimestampMixin):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "token_hash", name="uq_auth_sessions_tenant_token_hash"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_auth_sessions_tenant_party",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    party_id: Mapped[UUID] = mapped_column(
        Uuid(),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
