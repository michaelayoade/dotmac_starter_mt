"""dotmac-kernel — the DotMac platform kernel public API.

This module is the SINGLE CANONICAL public-surface manifest (kernel-boundary
Task 2). Two ways to consume the kernel, both governed here:

1. Curated top-level names re-exported below (``from dotmac_kernel import
   Party, settings, resolve_value``) — the import-safe subset (no DB engine or
   I/O at import time), listed in ``__all__``.
2. Supported submodules (``from dotmac_kernel.db import get_db``) — every
   module in ``SUPPORTED_MODULES``, each exporting its own ``__all__``. The
   eager DB-session owner remains submodule-only. Guard imports through
   ``dotmac_kernel.deps`` are database-configuration-safe: its request
   dependencies enter that owner only when FastAPI resolves a request.

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
from dotmac_kernel.assembly import ProductAssemblySpec, ProductSecurityPolicy
from dotmac_kernel.audit import (
    ACTOR_TYPES,
    AuditEvent,
    MissingAuditActorError,
    PlatformAuditEvent,
    UnknownAuditActorTypeError,
    resolve_audit_actor,
    write_audit_event,
    write_platform_audit_event,
)
from dotmac_kernel.audit_actions import (
    AuditActionRegistry,
    AuditActionsNotInstalledError,
    DuplicateAuditActionError,
    UndeclaredAuditActionError,
    active_audit_actions,
    install_audit_actions,
)
from dotmac_kernel.cache import (
    CacheStore,
    MemoryCache,
    PlatformScope,
    TenantScope,
    cache_key,
)
from dotmac_kernel.capabilities import (
    CapabilityCatalogue,
    DuplicateCapabilityError,
    UndeclaredCapabilityError,
    active_capabilities,
    install_capabilities,
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
from dotmac_kernel.external_identity import ResolvedExternalIdentity
from dotmac_kernel.features import (
    FeatureManifest,
    NavItem,
    load_manifests,
    mount_features,
)
from dotmac_kernel.flags import (
    FeatureFlagSpec,
    FlagCatalogue,
    FlagEvaluation,
    UndeclaredFlagError,
    active_flags,
    evaluate,
    install_flags,
)
from dotmac_kernel.identity import normalize_email, person_display_name
from dotmac_kernel.models import (
    AuthSession,
    Base,
    ExternalIdentityBinding,
    Party,
    PartyOrganization,
    PartyPerson,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
    TenantDomain,
    TimestampMixin,
    UserCredential,
    uuid_pk,
)
from dotmac_kernel.models_platform import PlatformAdmin, PlatformSession
from dotmac_kernel.modules import (
    KERNEL_MODULE_CONTRACT_VERSION,
    SUPPORTED_MODULE_CONTRACT_VERSIONS,
    UNVERSIONED,
    AnyManifest,
    DuplicateModuleError,
    MissingModuleDependencyError,
    ModuleContractVersionError,
    ModuleDependencyCycleError,
    ModuleInventoryEntry,
    ModuleManifest,
    ModuleRegistry,
    ModuleRegistryError,
    UnknownModuleError,
)
from dotmac_kernel.money import (
    Currency,
    CurrencyMismatchError,
    ExchangeRate,
    Money,
    MoneyError,
    currency,
)
from dotmac_kernel.namespaces import (
    DURABLE_TIMERS_MIGRATION_OWNER,
    HOST_SCHEMA,
    MAX_REVISION_ID_LENGTH,
    MEDIA_OBSERVATIONS_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    PEOPLE_MIGRATION_OWNER,
    DuplicateBranchLabelError,
    DuplicateMigrationPrefixError,
    DuplicateSchemaError,
    DuplicateTableOwnerError,
    HostSchemaClaimError,
    InvalidMigrationPrefixError,
    InvalidRevisionIdError,
    InvalidSchemaError,
    MigrationOwner,
    NamespaceAllocationError,
    NamespaceError,
    NamespaceRegistry,
    UnallocatedNamespaceError,
    module_schema,
    qualified,
    revision_id,
    schema_table_args,
)
from dotmac_kernel.outbox_event_types import (
    DuplicateOutboxEventTypeError,
    OutboxEventTypeRegistry,
    OutboxEventTypesNotInstalledError,
    UndeclaredOutboxEventTypeError,
    active_outbox_event_types,
    install_outbox_event_types,
)
from dotmac_kernel.permissions import (
    DuplicatePermissionError,
    PermissionCatalogue,
    PermissionSpec,
    UndeclaredPermissionError,
    active_permissions,
    install_permissions,
)
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from dotmac_kernel.product_manifest import (
    PRODUCT_MANIFEST_SCHEMA,
    ProductManifestDigestMismatchError,
    ProductManifestError,
    ProductManifestSnapshot,
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

__version__ = "0.1.0a78"

# ── Supported public submodules ─────────────────────────────────────────────
# The exhaustive list of kernel modules a consumer (assembly) may import from.
# A name is public only if it is in that module's own `__all__`.
SUPPORTED_MODULES: frozenset[str] = frozenset(
    {
        "dotmac_kernel.app_factory",
        "dotmac_kernel.assembly",
        "dotmac_kernel.audit",
        "dotmac_kernel.audit_actions",
        "dotmac_kernel.branding",
        "dotmac_kernel.capabilities",
        "dotmac_kernel.config",
        "dotmac_kernel.channel_policy",
        "dotmac_kernel.consent",
        "dotmac_kernel.consent_models",
        "dotmac_kernel.crud",
        "dotmac_kernel.db",
        "dotmac_kernel.delivery",
        "dotmac_kernel.delivery_models",
        "dotmac_kernel.delivery_providers",
        "dotmac_kernel.deps",
        "dotmac_kernel.entitlements",
        "dotmac_kernel.errors",
        "dotmac_kernel.exceptions",
        "dotmac_kernel.external_identity",
        "dotmac_kernel.features",
        "dotmac_kernel.idempotency",
        "dotmac_kernel.idempotency_models",
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
        "dotmac_kernel.migrations.catalog",
        "dotmac_kernel.migrations.gate",
        # Public because an ASSEMBLY must declare its prerequisite bindings and
        # a MODULE migration must verify them — both are consumer-facing halves
        # of the composition contract, not kernel internals (ADR-0006 D1
        # amendment).
        "dotmac_kernel.migrations.verify",
        "dotmac_kernel.models",
        "dotmac_kernel.models_platform",
        "dotmac_kernel.modules",
        "dotmac_kernel.money",
        "dotmac_kernel.namespaces",
        "dotmac_kernel.outbox_event_types",
        "dotmac_kernel.permissions",
        "dotmac_kernel.planes",
        "dotmac_kernel.platform_auth",
        "dotmac_kernel.prerequisites",
        "dotmac_kernel.product_manifest",
        "dotmac_kernel.profiles",
        "dotmac_kernel.providers",
        "dotmac_kernel.providers.provisioning",
        "dotmac_kernel.query",
        "dotmac_kernel.security",
        "dotmac_kernel.setting_domains",
        "dotmac_kernel.setting_scopes",
        "dotmac_kernel.setting_value_types",
        "dotmac_kernel.settings_admin",
        "dotmac_kernel.settings_cache",
        "dotmac_kernel.settings_crypto",
        "dotmac_kernel.settings_models",
        "dotmac_kernel.settings_resolver",
        "dotmac_kernel.templating",
        "dotmac_kernel.tenancy",
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
# not import from these. `_transactions` is the engine-free savepoint mechanic
# for caller-session kernel services; its public spelling remains
# `dotmac_kernel.db.conflict_savepoint`. `display` is consumed only within the
# kernel (by `templating` / `web_deps`); the `settings_resolver` write helpers
# are the `settings_admin` narrow surface, not general API (see that module).
INTERNAL_MODULES: frozenset[str] = frozenset(
    {
        "dotmac_kernel._transactions",
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
    "ProductSecurityPolicy",
    "ModulePlane",
    "ModulePlaneSelection",
    "create_app",
    # audit
    "AuditEvent",
    "write_audit_event",
    "PlatformAuditEvent",
    "write_platform_audit_event",
    "ACTOR_TYPES",
    "resolve_audit_actor",
    "MissingAuditActorError",
    "UnknownAuditActorTypeError",
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
    # features (the pre-ModuleManifest surface — still fully supported)
    "FeatureManifest",
    "NavItem",
    "load_manifests",
    "mount_features",
    # module manifest + registry (module control-plane step 2)
    "ModuleManifest",
    "ModuleRegistry",
    "ModuleInventoryEntry",
    "AnyManifest",
    "KERNEL_MODULE_CONTRACT_VERSION",
    "SUPPORTED_MODULE_CONTRACT_VERSIONS",
    "UNVERSIONED",
    "ModuleRegistryError",
    "DuplicateModuleError",
    "ModuleContractVersionError",
    "MissingModuleDependencyError",
    "ModuleDependencyCycleError",
    "UnknownModuleError",
    # database namespaces + migration lineage identity (ADR-0006 D1)
    "HOST_SCHEMA",
    "DURABLE_TIMERS_MIGRATION_OWNER",
    "MAX_REVISION_ID_LENGTH",
    "MEDIA_OBSERVATIONS_MIGRATION_OWNER",
    "MIGRATION_OWNER_LEDGER",
    "MigrationOwner",
    "NamespaceRegistry",
    "PEOPLE_MIGRATION_OWNER",
    "module_schema",
    "qualified",
    "schema_table_args",
    "revision_id",
    "NamespaceError",
    "InvalidSchemaError",
    "InvalidMigrationPrefixError",
    "InvalidRevisionIdError",
    "DuplicateSchemaError",
    "DuplicateMigrationPrefixError",
    "DuplicateBranchLabelError",
    "DuplicateTableOwnerError",
    "UnallocatedNamespaceError",
    "NamespaceAllocationError",
    "HostSchemaClaimError",
    # capability catalogue (WS1)
    "CacheStore",
    "MemoryCache",
    "PlatformScope",
    "TenantScope",
    "cache_key",
    "FeatureFlagSpec",
    "FlagCatalogue",
    "FlagEvaluation",
    "UndeclaredFlagError",
    "active_flags",
    "evaluate",
    "install_flags",
    "CapabilityCatalogue",
    "active_capabilities",
    "install_capabilities",
    "DuplicateCapabilityError",
    "UndeclaredCapabilityError",
    # release-bound product manifest
    "PRODUCT_MANIFEST_SCHEMA",
    "ProductManifestDigestMismatchError",
    "ProductManifestError",
    "ProductManifestSnapshot",
    # permission catalogue (module control-plane step 3)
    "PermissionSpec",
    "PermissionCatalogue",
    "DuplicatePermissionError",
    "UndeclaredPermissionError",
    "install_permissions",
    "active_permissions",
    # audit-action registry (module control-plane step 3)
    "AuditActionRegistry",
    "AuditActionsNotInstalledError",
    "DuplicateAuditActionError",
    "UndeclaredAuditActionError",
    "install_audit_actions",
    "active_audit_actions",
    # outbox event-type registry
    "DuplicateOutboxEventTypeError",
    "OutboxEventTypeRegistry",
    "OutboxEventTypesNotInstalledError",
    "UndeclaredOutboxEventTypeError",
    "active_outbox_event_types",
    "install_outbox_event_types",
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
    "ResolvedExternalIdentity",
    "PartyPerson",
    "PartyOrganization",
    "Role",
    "PartyRoleGrant",
    "AuthSession",
    "ExternalIdentityBinding",
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
