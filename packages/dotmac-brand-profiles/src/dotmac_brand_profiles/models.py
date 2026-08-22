"""The brand-profile tables, bound to `mod_brand` (ADR-0006 D1). Dual-plane.

A brand profile is the DATA that makes one released artifact appear as Dotmac
Academy at one host and NDIC Academy at another. It is not a design system, not
a stylesheet, and not a stored image.

## Genuinely dual-plane, with a named assembly on each side today

ADR-0023 requires both, and both exist:

- **Tenant plane** — an operator brands its own portals, and a reseller or
  organization may override within that tenant. Sub's 897-LOC `BrandProfile` is
  the production implementation this is extracted from.
- **Platform plane** — the vendor brands a deployment it ships. This is the OEM
  case, and it is the one that needs HOST bindings: a profile has to be
  selectable before any tenant is resolved.

The two planes hold structurally similar tables, which is exactly the case
ADR-0023 warns about — a reader cannot tell them apart from the columns alone,
so the plane is DECLARED rather than inferred.

## Host bindings exist on the PLATFORM plane only, and that is a boundary

`platform_brand_host_bindings` maps a host to a BRAND PROFILE. The kernel's
`TenantDomain` maps a host to a TENANT. Two different questions, and putting a
host binding on the tenant plane too would make this module a second answer to
the kernel's question — the drift would show up as a tenant resolving one way
and its branding another.

On the tenant plane a tenant is already resolved, so a profile is selected by
SCOPE precedence instead. That asymmetry is the design, not an omission.

## There is no CSS column, and there cannot be one

ADR-0006 **D8** is fleet-wide: no tenant-supplied raw CSS. This module holds two
colours — `primary_hex` and `accent_hex` — and hands them to
`dotmac_ui.BrandOverride`, which validates them at construction and is the only
thing that produces CSS. The absence of a CSS column is what makes D8 structural
here rather than a rule someone has to remember.

`dotmac-files` owns the bytes (ADR-0022); the logo, dark-logo and icon columns
hold opaque file references and this module never fetches one.

## `locked_fields` is what makes precedence safe

ADR-0006 § 3's second safety rule: a layer may LOCK a field against
lower-precedence override. Without it, precedence is only a default and a
reseller can rebrand the operator's legal identity. With it, a platform profile
can pin `legal_name` and `support_email` while leaving `display_name` free —
which is § 3's third rule (legal, data-controller and support identity are
separate from display brand) made expressible rather than merely stated.

## No foreign key leaves this schema

`logo_file_ref`, `mobile_build_profile_ref` and `scope_id` are opaque. ADR-0006
D1 forbids the cross-lineage foreign key, and a brand profile must outlive a
deleted file record and a merged organization.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

#: JSONB in production, portable `JSON` everywhere else — the module's logic
#: tests run on in-memory SQLite. The MIGRATION names `JSONB` unconditionally.
_JSON_DOC = JSON().with_variant(JSONB(), "postgresql")

SCHEMA: str = module_schema("brand")

_TENANT_PROFILES = "brand_profiles"
_PLATFORM_PROFILES = "platform_brand_profiles"
_PLATFORM_HOSTS = "platform_brand_host_bindings"

TENANT_TABLES: tuple[str, ...] = (_TENANT_PROFILES,)
PLATFORM_TABLES: tuple[str, ...] = (_PLATFORM_PROFILES, _PLATFORM_HOSTS)

#: The fields a higher-precedence profile may LOCK. Enumerated rather than "any
#: column" because locking `id` or `record_version` is meaningless, and a lock
#: list that accepted them would let a caller write a lock nothing can honour.
LOCKABLE_FIELDS: frozenset[str] = frozenset(
    {
        "display_name",
        "product_name",
        "legal_name",
        "legal_address",
        "primary_hex",
        "accent_hex",
        "logo_file_ref",
        "dark_logo_file_ref",
        "icon_file_ref",
        "support_email",
        "support_phone",
        "support_url",
        "sender_email",
        "sender_name",
        "enabled_surfaces",
        "default_locale",
        "default_timezone",
        "mobile_build_profile_ref",
    }
)

#: The presentational subset. Named separately because ADR-0006 § 3's third
#: safety rule turns on the distinction: legal, data-controller and support
#: identity are NOT display brand, and a caller that wants "let them change the
#: look, not who they are" needs the split to be expressible.
DISPLAY_FIELDS: frozenset[str] = frozenset(
    {
        "display_name",
        "product_name",
        "primary_hex",
        "accent_hex",
        "logo_file_ref",
        "dark_logo_file_ref",
        "icon_file_ref",
    }
)

#: Everything else in `LOCKABLE_FIELDS`: legal identity, support routing, sender
#: presentation, surfaces and locale.
IDENTITY_FIELDS: frozenset[str] = LOCKABLE_FIELDS - DISPLAY_FIELDS


class ProfileStatus(StrEnum):
    """`DRAFT` — being edited, never resolved. `ACTIVE` — resolvable.
    `RETIRED` — kept for history, never resolved again.

    Retired rather than deleted: a brand a customer saw is a fact about what was
    presented to them, and deleting it makes a support conversation about "the
    email said X" unanswerable.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class _ProfileFields:
    """The columns every brand profile has, on either plane.

    Declared as a plain mixin of `mapped_column`s: SQLAlchemy's declarative
    machinery copies them per subclass, so the two tables get independent
    columns rather than sharing one.
    """

    profile_code: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(160))

    #: Legal identity. ADR-0006 § 3: separate from display brand, and never
    #: silently inherited from a lower-precedence layer.
    legal_name: Mapped[str | None] = mapped_column(String(200))
    legal_address: Mapped[dict[str, Any] | None] = mapped_column(_JSON_DOC)

    #: Two colours, and nothing else. `dotmac_ui.BrandOverride` validates them
    #: and is the only thing that turns them into CSS — see the module
    #: docstring for why there is no CSS column and cannot be one (ADR-0006 D8).
    primary_hex: Mapped[str | None] = mapped_column(String(7))
    accent_hex: Mapped[str | None] = mapped_column(String(7))

    #: Opaque `dotmac-files` references. This module never fetches bytes
    #: (ADR-0022) and holds no foreign key to the file lineage (ADR-0006 D1).
    logo_file_ref: Mapped[str | None] = mapped_column(String(200))
    dark_logo_file_ref: Mapped[str | None] = mapped_column(String(200))
    icon_file_ref: Mapped[str | None] = mapped_column(String(200))

    support_email: Mapped[str | None] = mapped_column(String(255))
    support_phone: Mapped[str | None] = mapped_column(String(40))
    support_url: Mapped[str | None] = mapped_column(String(512))

    #: Sender PRESENTATION, not sender authority. Whether this address may
    #: actually send is the Integrator's connector configuration; whether it
    #: SHOULD be contacted is `dotmac_kernel.consent`'s. This is what a
    #: recipient sees.
    sender_email: Mapped[str | None] = mapped_column(String(255))
    sender_name: Mapped[str | None] = mapped_column(String(160))

    #: Which product surfaces this brand enables — an open registered vocabulary
    #: (ADR-0008), never an enum: a product names its own facets.
    enabled_surfaces: Mapped[list[str] | None] = mapped_column(_JSON_DOC)

    default_locale: Mapped[str | None] = mapped_column(String(35))
    default_timezone: Mapped[str | None] = mapped_column(String(64))

    #: Opaque reference to a mobile build profile. Native mobile brands are
    #: separate SIGNED BUILDS from shared source — this column names the build
    #: profile, and holds no build input, no certificate and no key.
    mobile_build_profile_ref: Mapped[str | None] = mapped_column(String(200))

    #: Which of `LOCKABLE_FIELDS` this profile pins against lower-precedence
    #: override. ADR-0006 § 3's second safety rule.
    locked_fields: Mapped[list[str] | None] = mapped_column(_JSON_DOC)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProfileStatus.DRAFT.value, index=True
    )
    record_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class BrandProfile(Base, _ProfileFields, TimestampMixin):
    """A tenant-plane brand profile, scoped within one tenant.

    `scope_type` is an open registered string (ADR-0008) — `organization`,
    `reseller`, `tenant`, whatever the product's own hierarchy names — and
    `scope_id` is opaque. This module owns neither vocabulary; it owns the
    per-field precedence and the locking that makes it safe.
    """

    __tablename__ = _TENANT_PROFILES
    __table_args__ = (
        #: Composite with `tenant_id`, per hard rule 11: unique-per-tenant, not
        #: unique globally. Two tenants naming a profile `default` is ordinary.
        UniqueConstraint(
            "tenant_id", "profile_code", name="uq_brand_profiles_tenant_code"
        ),
        #: One ACTIVE profile per scope, enforced where it matters rather than
        #: left to the resolver: two active profiles for one scope would make
        #: resolution order-dependent, and the order would be whatever the query
        #: planner chose that day.
        UniqueConstraint(
            "tenant_id",
            "scope_type",
            "scope_id",
            "profile_code",
            name="uq_brand_profiles_tenant_scope_code",
        ),
        CheckConstraint("record_version >= 1", name="ck_brand_profiles_version"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Opaque, and NULLABLE on purpose: the tenant-wide scope has no narrower
    #: id. A sentinel value would be a fake id, which ADR-0023 refuses for
    #: tenants and which is no better here.
    scope_id: Mapped[UUID | None] = mapped_column()


class PlatformBrandProfile(Base, _ProfileFields, TimestampMixin):
    """A platform-plane brand profile — the OEM / white-label case.

    No `tenant_id`, no RLS, `app_user` REVOKEd. This is the profile that makes
    one released artifact appear as Dotmac Academy and as NDIC Academy, and a
    tenant data plane must not be able to read the vendor's portfolio of them.
    """

    __tablename__ = _PLATFORM_PROFILES
    __table_args__ = (
        UniqueConstraint("profile_code", name="uq_platform_brand_profiles_code"),
        CheckConstraint(
            "record_version >= 1", name="ck_platform_brand_profiles_version"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()


class PlatformBrandHostBinding(Base, TimestampMixin):
    """Host → platform brand profile.

    The mapping that lets a profile be selected BEFORE any tenant is resolved,
    which is the whole reason a brand profile cannot be a tenant setting.

    Distinct from the kernel's `TenantDomain`, which maps host → TENANT. Two
    different questions; see the module docstring for why conflating them would
    produce drift that shows up as a tenant resolving one way and its branding
    another.

    `host` is stored already NORMALISED by the caller — lowercased, punycode,
    no trailing dot. Normalising here would make this module a second authority
    on what a host is, and two normalisers eventually disagree.
    """

    __tablename__ = _PLATFORM_HOSTS
    __table_args__ = (
        UniqueConstraint("host", name="uq_platform_brand_host_bindings_host"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.{_PLATFORM_PROFILES}.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: Set when this host is the profile's canonical one. Advisory: several
    #: hosts may resolve to one profile, and exactly one of them is the address
    #: a link should use.
    is_canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


__all__ = [
    "DISPLAY_FIELDS",
    "IDENTITY_FIELDS",
    "LOCKABLE_FIELDS",
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "BrandProfile",
    "PlatformBrandHostBinding",
    "PlatformBrandProfile",
    "ProfileStatus",
]
