"""Declarative base + shared mixins + core cross-cutting models.

`Tenant`, `TenantDomain`, `Person`, `Role`, `PersonRole`, and `AuthSession` live
here — not under `app/features/*` — because core code needs them directly:
`app.core.deps` (the `require_*` route guards) queries `Person`, `AuthSession`,
`Role`, and `PersonRole`, and `app.core.middleware.tenant` (the tenant resolver)
queries `Tenant`/`TenantDomain`. Core must not import features (import-linter
enforces this), so these identity/tenancy primitives — genuinely cross-cutting,
same rationale as `app.core.audit.AuditEvent` — live in core instead.

Everything that is *not* needed outside its own feature stays local to that
feature's `models.py` (e.g. `UserCredential` in `app.features.auth.models`,
referencing these tables only via string-form `ForeignKey`/`ForeignKeyConstraint`
— no import required).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class Person(Base, TimestampMixin):
    """Person — example tenant-scoped model.

    Every tenant-scoped model follows this template:
    - `tenant_id UUID NOT NULL REFERENCES tenants(id)`
    - Composite uniqueness on `(tenant_id, X)` for any X that's "globally unique"
      per tenant
    - RLS enabled in the migration that creates the table
    """

    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_people_tenant_email"),
        UniqueConstraint("tenant_id", "id", name="uq_people_tenant_id_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)


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


class PersonRole(Base, TimestampMixin):
    __tablename__ = "person_roles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "person_id", "role_id", name="uq_person_roles_member"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            ondelete="CASCADE",
            name="fk_person_roles_tenant_person",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            ondelete="CASCADE",
            name="fk_person_roles_tenant_role",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False, index=True)
    role_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False, index=True)


class AuthSession(Base, TimestampMixin):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "token_hash", name="uq_auth_sessions_tenant_token_hash"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            ondelete="CASCADE",
            name="fk_auth_sessions_tenant_person",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[UUID] = mapped_column(
        Uuid(),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
