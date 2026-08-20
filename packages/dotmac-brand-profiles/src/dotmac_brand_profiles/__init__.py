"""DotMac Brand Profiles — one artifact, many brands, and every field says where.

The reusable owner of **brand profile DATA**: identity, colours, file references,
host bindings, enabled surfaces, locale defaults, and the per-field precedence
with locking that makes a multi-layer brand safe.

It is what lets the same released LMS artifact appear as Dotmac Academy and as
NDIC Academy through approved profiles.

Extracted product-first from `dotmac_sub`'s `BrandProfile` (897 LOC across seven
modules, in production) under ADR-0033 § 2.

## What it is NOT, and where each of those lives

- **Not a design system.** `dotmac-ui` owns the tokens, the ramp generation, the
  accessibility clamping and the CSS. This module stores two hex values and hands
  them to `dotmac_ui.BrandOverride`, which validates them at construction. There
  is no CSS column, no token map and no colour parser here — ADR-0006 **D8** made
  structural rather than remembered.
- **Not a file store.** `dotmac-files` owns bytes (ADR-0022). Logo, dark-logo and
  icon columns hold opaque references this module never dereferences, so a
  profile stays readable after a file is purged.
- **Not a host→tenant resolver.** The kernel's `TenantDomain` answers that. This
  module's host bindings answer host→BRAND, on the platform plane only, and
  conflating them would produce a tenant resolving one way and its branding
  another.
- **Not a mobile build pipeline.** Native brands are separate SIGNED BUILDS from
  shared source; `mobile_build_profile_ref` names a build profile and holds no
  build input, no certificate and no key.

## Every resolved field reports its source

ADR-0006 § 3's first safety rule. `ResolvedBrand` carries `sources` and `locked`
alongside `values`, because an operator looking at a portal showing the wrong
company name needs to know WHICH layer said it — and a resolver that returns only
the merged result turns that into a debugging session.

Sub's implementation carries one source for the whole record. This generalises it
PER FIELD, which is what ADR-0006 § 3 actually specifies: a whole-record source
cannot express "the name came from the reseller and the legal identity from the
platform".

## A lock beats precedence

ADR-0006 § 3's second and third safety rules together. A higher layer may pin a
field; `IDENTITY_FIELDS` is the set that keeps legal, data-controller and support
identity separate from display brand, so "let them change the look, not who they
are" is one call rather than a convention.

## Dual-plane, with a named assembly on each side today

Sub selects TENANT (RLS, `tenant_id NOT NULL`, composite uniques); the vendor
control plane selects PLATFORM (no RLS, `app_user` REVOKEd, host bindings). The
assembly makes an explicit `ModulePlaneSelection` (ADR-0028) — there is no
default.

## Public surface

Everything importable from this top-level namespace is stable.
"""

from __future__ import annotations

from dotmac_brand_profiles.brand_values import (
    BRAND_OVERRIDE_INPUTS,
    BrandValueTranslation,
    Disposition,
    UnsupportedBrandValue,
    brand_override_fields,
    translate_legacy_brand_values,
    validate_brand_values,
)
from dotmac_brand_profiles.manifest import module
from dotmac_brand_profiles.migrations import versions_dir
from dotmac_brand_profiles.models import (
    DISPLAY_FIELDS,
    IDENTITY_FIELDS,
    LOCKABLE_FIELDS,
    PLATFORM_TABLES,
    SCHEMA,
    TENANT_TABLES,
    BrandProfile,
    PlatformBrandHostBinding,
    PlatformBrandProfile,
    ProfileStatus,
)
from dotmac_brand_profiles.ports import (
    BrandProfileError,
    HostBindingRefusedError,
    ProfileFields,
    ProfileRefusedError,
    UnknownLockedFieldError,
)
from dotmac_brand_profiles.service import (
    AUDIT_ACTION_PLATFORM,
    ResolvedBrand,
    UpsertPlatformProfileCommand,
    UpsertTenantProfileCommand,
    activate_platform_profile,
    activate_tenant_profile,
    bind_host,
    get_platform_profile,
    resolvable_by,
    resolve,
    resolve_by_host,
    resolve_for_tenant,
    upsert_platform_profile,
    upsert_tenant_profile,
)

__version__ = "0.1.0a1"

__all__ = [
    "AUDIT_ACTION_PLATFORM",
    "BRAND_OVERRIDE_INPUTS",
    "DISPLAY_FIELDS",
    "Disposition",
    "IDENTITY_FIELDS",
    "LOCKABLE_FIELDS",
    "PLATFORM_TABLES",
    "SCHEMA",
    "TENANT_TABLES",
    "BrandProfile",
    "BrandProfileError",
    "BrandValueTranslation",
    "HostBindingRefusedError",
    "PlatformBrandHostBinding",
    "PlatformBrandProfile",
    "ProfileFields",
    "ProfileRefusedError",
    "ProfileStatus",
    "ResolvedBrand",
    "UnknownLockedFieldError",
    "UnsupportedBrandValue",
    "UpsertPlatformProfileCommand",
    "UpsertTenantProfileCommand",
    "__version__",
    "activate_platform_profile",
    "activate_tenant_profile",
    "bind_host",
    "brand_override_fields",
    "get_platform_profile",
    "module",
    "resolvable_by",
    "resolve",
    "resolve_by_host",
    "resolve_for_tenant",
    "translate_legacy_brand_values",
    "upsert_platform_profile",
    "upsert_tenant_profile",
    "validate_brand_values",
    "versions_dir",
]
