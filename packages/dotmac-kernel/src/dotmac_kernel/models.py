"""Declarative base + shared mixins + core cross-cutting models.

`Tenant`, `TenantDomain`, `Party` (+ subtype tables `PartyPerson`/
`PartyOrganization`), `Role`, `PartyRoleGrant`, and `AuthSession` live here —
not under `app/features/*` — because core code needs them directly:
`dotmac_kernel.deps` (the `require_*` route guards) queries `Party`,
`AuthSession`, `Role`, and `PartyRoleGrant`, and
`dotmac_kernel.middleware.tenant` (the tenant resolver) queries
`Tenant`/`TenantDomain`. Core must not import features (import-linter
enforces this), so these identity/tenancy primitives — genuinely cross-cutting,
same rationale as `dotmac_kernel.audit.AuditEvent` — live in core instead.

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
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

_JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


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

    `custom_fields` (Task 8) holds field *values* keyed by `field_code`,
    riding on this table's existing RLS policy; field *shape* (type,
    validation, display) is defined per-tenant in
    `app.features.custom_fields.models.CustomFieldDefinition`.
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
    custom_fields: Mapped[dict] = mapped_column(
        _JSON_VARIANT,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'"),
    )

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

    The audit event model lives in dotmac_kernel.audit (cross-cutting write-side).
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


class PartyRoleGrant(Base, TimestampMixin):
    """An RBAC grant: this party holds this `Role` (a permission bundle).

    NOT the Party archetype's "PartyRole", which is a concurrent, temporal
    business capacity — customer, reseller, staff. ADR-0019 reserves the name
    `party_roles` for that concept fleet-wide. This table answers "what may they
    do", which the archetype attaches to a capacity rather than being one.

    Renamed from the former "PartyRole"/`party_roles` by kernel migration
    `0022_party_role_grants`. No compatibility alias is exported, deliberately:
    an alias would preserve exactly the ambiguity ADR-0019 removes.
    """

    __tablename__ = "party_role_grants"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "party_id", "role_id", name="uq_party_role_grants_member"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_party_role_grants_tenant_party",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            ondelete="CASCADE",
            name="fk_party_role_grants_tenant_role",
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


class UserCredential(Base, TimestampMixin):
    """Password credential for a `party_type == person` `Party`.

    MOVED here from `app/features/auth/models.py` (control-plane security
    Task 2, PORT-DELTA): atomic tenant provisioning
    (`app.features.tenants.service.provision_tenant`) must create the owner's
    credential in the same transaction as the tenant, and `tenants` may not
    import the `auth` feature (feature-independence contract) — so the model
    joins the other identity models under ADR-0002's placement rule (models
    needed across feature boundaries live in core). The `auth` feature keeps
    ALL hashing/verification logic via `dotmac_kernel.security`; only the table
    definition moved.

    No `email` column (F2): `Party.email` is the single email authority —
    `login()` resolves the party by email first, then this table by
    `party_id` only. See `docs/ARCHITECTURE.md`'s "Auth credentials" row.
    """

    __tablename__ = "user_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_user_credentials_tenant_party",
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
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)


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
        # RESTRICT, not SET NULL: see migration 0025. NULL on this column means
        # provenance ABSENT (a password login), never provenance unknown, and
        # SET NULL would quietly convert the second into the first.
        ForeignKeyConstraint(
            ["tenant_id", "external_identity_binding_id"],
            ["external_identity_bindings.tenant_id", "external_identity_bindings.id"],
            ondelete="RESTRICT",
            name="fk_auth_sessions_tenant_external_identity_binding",
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
    #: WHICH external identity binding produced this session, or NULL for a
    #: password login. Its only legitimate source is `binding_id` from
    #: `dotmac_kernel.external_identity.finalize_external_login` — a session
    #: minted from that call without this stamped is unattributable, and a
    #: value from anywhere else is a claim nobody verified.
    #:
    #: Read by `revoke_sessions_for_binding`, which is what makes disabling a
    #: binding able to end exactly the sessions it produced.
    external_identity_binding_id: Mapped[UUID | None] = mapped_column(
        Uuid(), nullable=True
    )


class ExternalIdentityBinding(Base, TimestampMixin):
    """A verified external subject, bound to one local `Party`.

    The answer to exactly one question: *this identity provider says it
    authenticated subject S at issuer I — which local party is that, here?*
    Nothing else. It holds no roles, no groups, no claims and no provider
    metadata, and it never creates a party (see
    `dotmac_kernel.external_identity` for why resolution refuses rather than
    provisions).

    ## Two products, two halves, neither one whole

    The 2026-08-14 inventory
    (`docs/inventories/external-identity-sources.md`) measured both:

    - **ERP** owns the external-subject half — `federated_identities`
      (`issuer`, `subject` → `person_id`), a written contract, an
      architecture test forbidding external roles, and an explicit
      no-auto-provision refusal. It has NO local provider record (the issuer
      is echoed from one global config value), NO tenant column, and NO RLS —
      so its `(issuer, subject)` uniqueness is GLOBAL across every ERP
      organization, with the org boundary enforced only transitively through
      the person FK.
    - **Sub** owns the provider-registration half —
      `authentication_bindings`: an installed, configured way of proving you
      are a party, keyed by an immutable deployment-global `binding_key`,
      tagged with an open declared `mechanism_code`. It has no `issuer` and
      no `subject` column anywhere.

    Neither is a superset, so this table is ERP's shape with Sub's
    discriminator and the isolation neither has.

    ## `provider_binding` is a LOCAL fact, never provider metadata

    The load-bearing column. It names WHICH configured provider registration
    the caller just completed a ceremony against — the caller's own trusted
    configuration, not a string parsed out of a token. `issuer` is corroborating
    evidence recorded alongside it; it does not select anything on its own,
    because a value that arrives inside the credential being verified cannot be
    the thing that decides which credential is trusted.

    Concretely, that is why resolution keys on the whole tuple. Sub's design
    note is the reason the discriminator is the BINDING rather than a mechanism
    code: *"two OIDC issuers or two RADIUS verifiers are two bindings of one
    code, and a code-keyed constraint would forbid a party holding a credential
    against each."*

    It is a plain string, deliberately NOT an ADR-0008 declaration. Same line
    ADR-0026 §4 draws for `policy_code`: a subject type is code-owned and
    therefore declared, but a provider registration is created by an OPERATOR
    configuring an IdP, and requiring a manifest entry would put a software
    release between an operator and their own identity provider. Fail-closed
    resolution is what protects a typo — an unknown binding resolves to nothing.

    ## What is deliberately NOT here

    No provider-registration TABLE. Sub has one and this kernel does not yet:
    a first-class row carrying discovery URLs, client ids and key material is a
    second contract with its own lifecycle, and ADR-0009 governs where its
    secret half may live. Until it exists, `provider_binding` is a string whose
    trust the CALLER asserts, and the caller is the product's identity facet
    that owns the provider configuration. That is a real limitation, recorded
    rather than papered over.

    No `email` column. `Party.email` is the single email authority (F2), and
    ERP's contract is explicit that provider email is *"display evidence only
    and is never used for automatic account linking"*.
    """

    __tablename__ = "external_identity_bindings"
    __table_args__ = (
        # Tenant-partitioned, unlike ERP's global pair. One external identity
        # at one configured provider means one party WITHIN a tenant; the same
        # subject may legitimately be a different person in a different tenant.
        UniqueConstraint(
            "tenant_id",
            "provider_binding",
            "issuer",
            "subject",
            name="uq_external_identity_bindings_tenant_provider_subject",
        ),
        # ERP's `(person_id, issuer)`, tenant- and provider-partitioned: a party
        # holds at most ONE identity per configured provider, so a person cannot
        # quietly accumulate several logins into the same account.
        UniqueConstraint(
            "tenant_id",
            "provider_binding",
            "party_id",
            name="uq_external_identity_bindings_tenant_provider_party",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="CASCADE",
            name="fk_external_identity_bindings_tenant_party",
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
    provider_binding: Mapped[str] = mapped_column(String(80), nullable=False)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Sub's evidence quartet, which ERP has no analogue for: a binding grants a
    # login, so who made it and why are part of the record, not a side note in
    # an audit table that may be pruned on a different schedule.
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    bound_by: Mapped[str] = mapped_column(String(120), nullable=False)
    bind_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    # ERP's, and worth keeping: the only evidence a binding is still live.
    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


__all__ = [
    "AuthSession",
    "Base",
    "ExternalIdentityBinding",
    "Party",
    "PartyOrganization",
    "PartyPerson",
    "PartyRoleGrant",
    "PartyType",
    "Role",
    "Tenant",
    "TenantDomain",
    "TimestampMixin",
    "UserCredential",
    "uuid_pk",
]
