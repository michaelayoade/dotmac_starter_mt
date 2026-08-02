"""dotmac-kernel — the DotMac platform kernel public API.

This module is the SINGLE CANONICAL public-surface manifest (kernel-boundary
Task 2). Two ways to consume the kernel, both governed here:

1. Curated top-level names re-exported below (``from dotmac_kernel import
   Party, settings, resolve_value``) — the import-safe subset (no DB engine or
   I/O at import time), listed in ``__all__``.
2. Supported submodules (``from dotmac_kernel.db import get_db``) — every
   module in ``SUPPORTED_MODULES``, each exporting its own ``__all__``. The
   DB-session / guard / middleware APIs are submodule-only because importing
   them constructs the SQLAlchemy engine from ``DATABASE_URL``; keeping them
   out of the top level lets ``import dotmac_kernel`` succeed without a database.

Anything NOT in a supported module's ``__all__`` (or in ``INTERNAL_MODULES``)
is private and may change without notice. The compatibility policy and the
full supported/internal breakdown live in ``COMPATIBILITY.md``; the
``tests/architecture`` governance test enforces that the reference assembly
imports only names declared here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Static type for consumers: `create_app` is provided lazily by `__getattr__`
    # at runtime (kept out of the eager import path so `import dotmac_kernel`
    # stays DB-free), but a type-checker must see its real signature rather than
    # the `Any` a bare module `__getattr__` would yield.
    from dotmac_kernel.app_factory import create_app as create_app

# ── Curated, import-safe top-level API ──────────────────────────────────────
# Only names whose defining module has no import-time engine/I/O side effect,
# so `import dotmac_kernel` never requires DATABASE_URL. Engine-touching APIs
# (db sessions, guards, middleware, platform auth) are submodule imports.
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.audit import (
    AuditEvent,
    PlatformAuditEvent,
    write_audit_event,
    write_platform_audit_event,
)
from dotmac_kernel.capabilities import (
    CapabilityCatalogue,
    DuplicateCapabilityError,
    UndeclaredCapabilityError,
)
from dotmac_kernel.config import Settings, settings, validate_settings
from dotmac_kernel.entitlements import (
    EntitlementDecision,
    TenantEntitlementGrant,
    grant_entitlement,
    is_entitled,
)
from dotmac_kernel.exceptions import (
    BadRequestError,
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from dotmac_kernel.features import (
    FeatureManifest,
    NavItem,
    load_manifests,
    mount_features,
)
from dotmac_kernel.identity import normalize_email, person_display_name
from dotmac_kernel.models import (
    AuthSession,
    Base,
    Party,
    PartyOrganization,
    PartyPerson,
    PartyRole,
    PartyType,
    Role,
    Tenant,
    TenantDomain,
    TimestampMixin,
    UserCredential,
    uuid_pk,
)
from dotmac_kernel.models_platform import PlatformAdmin, PlatformSession
from dotmac_kernel.money import (
    Currency,
    CurrencyMismatchError,
    ExchangeRate,
    Money,
    MoneyError,
    currency,
)
from dotmac_kernel.profiles import (
    DeploymentProfileRegistry,
    DeploymentProfileSpec,
    DuplicateProfileError,
    ProfileValidationReport,
    UnknownProfileError,
)
from dotmac_kernel.query import apply_pagination, escape_like
from dotmac_kernel.security import (
    hash_password,
    hash_token,
    issue_access_token,
    password_needs_rehash,
    verify_password,
)
from dotmac_kernel.settings_models import SettingDomain, SettingValueType
from dotmac_kernel.settings_resolver import (
    SettingSpec,
    register_specs,
    resolve_value,
)

__version__ = "0.1.0a8"

# ── Supported public submodules ─────────────────────────────────────────────
# The exhaustive list of kernel modules a consumer (assembly) may import from.
# A name is public only if it is in that module's own `__all__`.
SUPPORTED_MODULES: frozenset[str] = frozenset(
    {
        "dotmac_kernel.app_factory",
        "dotmac_kernel.assembly",
        "dotmac_kernel.audit",
        "dotmac_kernel.branding",
        "dotmac_kernel.capabilities",
        "dotmac_kernel.config",
        "dotmac_kernel.crud",
        "dotmac_kernel.db",
        "dotmac_kernel.deps",
        "dotmac_kernel.entitlements",
        "dotmac_kernel.errors",
        "dotmac_kernel.exceptions",
        "dotmac_kernel.features",
        "dotmac_kernel.identity",
        "dotmac_kernel.licensing",
        "dotmac_kernel.logging",
        "dotmac_kernel.messaging",
        "dotmac_kernel.messaging.envelope",
        "dotmac_kernel.messaging.inbox",
        "dotmac_kernel.messaging.models",
        "dotmac_kernel.messaging.outbox",
        "dotmac_kernel.messaging.platform",
        "dotmac_kernel.messaging.platform_relay",
        "dotmac_kernel.messaging.platform_worker",
        "dotmac_kernel.messaging.relay",
        "dotmac_kernel.messaging.worker",
        "dotmac_kernel.middleware.csrf",
        "dotmac_kernel.middleware.observability",
        "dotmac_kernel.middleware.rate_limit",
        "dotmac_kernel.middleware.security_headers",
        "dotmac_kernel.middleware.tenant",
        "dotmac_kernel.migrations",
        "dotmac_kernel.models",
        "dotmac_kernel.models_platform",
        "dotmac_kernel.money",
        "dotmac_kernel.platform_auth",
        "dotmac_kernel.profiles",
        "dotmac_kernel.providers",
        "dotmac_kernel.providers.provisioning",
        "dotmac_kernel.query",
        "dotmac_kernel.security",
        "dotmac_kernel.settings_admin",
        "dotmac_kernel.settings_models",
        "dotmac_kernel.settings_resolver",
        "dotmac_kernel.templating",
        "dotmac_kernel.testing",
        "dotmac_kernel.testing.fakes",
        "dotmac_kernel.testing.harness",
        "dotmac_kernel.testing.licensing",
        "dotmac_kernel.testing.provisioning",
        "dotmac_kernel.web_deps",
    }
)

# ── Deliberately-internal modules ───────────────────────────────────────────
# Present in the package but NOT part of the public surface — a consumer must
# not import from these. `display` is consumed only within the kernel (by
# `templating` / `web_deps`); the `settings_resolver` write helpers are the
# `settings_admin` narrow surface, not general API (see that module).
INTERNAL_MODULES: frozenset[str] = frozenset(
    {
        "dotmac_kernel.display",
    }
)


def __getattr__(name: str):
    """Lazy top-level access to `create_app` (kernel-boundary Task 3A). It lives
    in `app_factory`, which imports the DB/middleware stack and constructs the
    SQLAlchemy engine at import — resolving it lazily keeps `import dotmac_kernel`
    itself DB-free while still allowing `from dotmac_kernel import create_app`."""
    if name == "create_app":
        from dotmac_kernel.app_factory import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "__version__",
    "SUPPORTED_MODULES",
    "INTERNAL_MODULES",
    # assembly composition
    "ProductAssemblySpec",
    "create_app",
    # audit
    "AuditEvent",
    "write_audit_event",
    "PlatformAuditEvent",
    "write_platform_audit_event",
    # config
    "Settings",
    "settings",
    "validate_settings",
    # exceptions
    "DomainError",
    "NotFoundError",
    "BadRequestError",
    "ConflictError",
    "UnauthorizedError",
    "ForbiddenError",
    # features
    "FeatureManifest",
    "NavItem",
    "load_manifests",
    "mount_features",
    # capability catalogue (WS1)
    "CapabilityCatalogue",
    "DuplicateCapabilityError",
    "UndeclaredCapabilityError",
    # deployment-profile registry (WS1)
    "DeploymentProfileSpec",
    "DeploymentProfileRegistry",
    "ProfileValidationReport",
    "DuplicateProfileError",
    "UnknownProfileError",
    # entitlements (WS2)
    "TenantEntitlementGrant",
    "EntitlementDecision",
    "grant_entitlement",
    "is_entitled",
    # identity
    "normalize_email",
    "person_display_name",
    # models
    "Base",
    "TimestampMixin",
    "uuid_pk",
    "Tenant",
    "TenantDomain",
    "Party",
    "PartyType",
    "PartyPerson",
    "PartyOrganization",
    "Role",
    "PartyRole",
    "AuthSession",
    "UserCredential",
    "PlatformAdmin",
    "PlatformSession",
    # money / FX primitives
    "Money",
    "Currency",
    "currency",
    "ExchangeRate",
    "MoneyError",
    "CurrencyMismatchError",
    # query
    "apply_pagination",
    "escape_like",
    # security
    "hash_password",
    "verify_password",
    "password_needs_rehash",
    "hash_token",
    "issue_access_token",
    # settings read/declare
    "SettingDomain",
    "SettingValueType",
    "SettingSpec",
    "register_specs",
    "resolve_value",
]
