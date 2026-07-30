# dotmac-kernel — compatibility & public API

This document defines the **supported public surface** of `dotmac-kernel` and
the stability guarantees around it. The authoritative machine-readable manifest
is `dotmac_kernel.__init__` (`SUPPORTED_MODULES`, `INTERNAL_MODULES`, the
curated top-level `__all__`, and each supported module's own `__all__`); this
document is its prose companion. The governance test
`tests/architecture/test_kernel_public_surface.py` enforces that the reference
assembly imports only what is documented here.

Current kernel version: **0.1.0a1** (pre-release).

## What is public

A name is public **only** if it is either:

- in the curated top-level `dotmac_kernel.__all__` (`from dotmac_kernel import
  X`), or
- in the `__all__` of a module listed in `SUPPORTED_MODULES`
  (`from dotmac_kernel.<module> import X`).

Everything else — any module not in `SUPPORTED_MODULES`, any name not in a
supported module's `__all__`, and every underscore-prefixed name — is **private
and may change or disappear without a deprecation cycle**.

### Two entry points

1. **Top-level (import-safe) API** — `from dotmac_kernel import ...`. This subset
   has no import-time database/engine or I/O side effect, so `import
   dotmac_kernel` succeeds without `DATABASE_URL`. It covers models, exceptions,
   feature manifests, config, identity/query helpers, security primitives, audit,
   and the settings read/declare contract.
2. **Supported submodules** — `from dotmac_kernel.<module> import ...`. The DB
   session/guard/middleware/platform-auth APIs live here (not at the top level)
   because importing them constructs the SQLAlchemy engine from `DATABASE_URL`.

### Supported modules and their public names

| Module | Public names |
|---|---|
| `dotmac_kernel.audit` | `AuditEvent`, `write_audit_event` |
| `dotmac_kernel.branding` | `get_brand`, `get_request_branding`, `load_branding`, `reset_brand_cache`, `sanitize_branding_css` |
| `dotmac_kernel.config` | `Settings`, `settings`, `validate_settings` |
| `dotmac_kernel.crud` | `CRUDManager` |
| `dotmac_kernel.db` | `get_db`, `get_platform_db`, `platform_session`, `conflict_savepoint`, `engine`, `platform_engine` |
| `dotmac_kernel.deps` | `require_tenant`, `require_user_auth`, `require_role`, `get_db`, `get_platform_db`, `authenticate_request`, `Depends` |
| `dotmac_kernel.errors` | `register_error_handlers` |
| `dotmac_kernel.exceptions` | `DomainError`, `NotFoundError`, `BadRequestError`, `ConflictError`, `UnauthorizedError`, `ForbiddenError` |
| `dotmac_kernel.features` | `FeatureManifest`, `NavItem`, `load_manifests`, `mount_features` |
| `dotmac_kernel.identity` | `normalize_email`, `person_display_name` |
| `dotmac_kernel.logging` | `setup_logging` |
| `dotmac_kernel.middleware.csrf` | `CSRFMiddleware` |
| `dotmac_kernel.middleware.observability` | `ObservabilityMiddleware` |
| `dotmac_kernel.middleware.rate_limit` | `RateLimitMiddleware`, `RateLimitStore`, `MemoryStore` |
| `dotmac_kernel.middleware.security_headers` | `SecurityHeadersMiddleware` |
| `dotmac_kernel.middleware.tenant` | `TenantResolverMiddleware` |
| `dotmac_kernel.migrations` | `versions_dir` (the kernel base Alembic revisions, for a consuming assembly's `version_locations`) |
| `dotmac_kernel.models` | `Base`, `TimestampMixin`, `uuid_pk`, `Tenant`, `TenantDomain`, `Party`, `PartyType`, `PartyPerson`, `PartyOrganization`, `Role`, `PartyRole`, `AuthSession`, `UserCredential` |
| `dotmac_kernel.models_platform` | `PlatformAdmin`, `PlatformSession` |
| `dotmac_kernel.platform_auth` | `require_platform_admin`, `platform_auth_router`, `PLATFORM_AUDIENCE` |
| `dotmac_kernel.query` | `apply_pagination`, `escape_like` |
| `dotmac_kernel.security` | `hash_password`, `verify_password`, `password_needs_rehash`, `hash_token`, `issue_access_token`, `decode_access_token` |
| `dotmac_kernel.settings_models` | `SettingDomain`, `SettingValueType` |
| `dotmac_kernel.settings_resolver` | `SettingSpec`, `register_specs`, `resolve_value` |
| `dotmac_kernel.settings_admin` | `all_specs`, `get_spec`, `resolve_with_source`, `upsert_by_key`, `ensure_by_key`, `validate_spec_value` |
| `dotmac_kernel.templating` | `render`, `install_surface_globals`, `static_dir` |
| `dotmac_kernel.web_deps` | `require_web_auth`, `is_secure_request`, `safe_next_url`, `WebAuthRedirect` |

### Settings: two distinct surfaces

- **Read / declare (general API):** `settings_resolver.{resolve_value,
  register_specs, SettingSpec}` + `settings_models.{SettingDomain,
  SettingValueType}`. Any feature reads a value or declares a spec through these.
- **Admin / registry (narrow API):** `dotmac_kernel.settings_admin` — the
  write-path and registry-introspection helpers (`upsert_by_key`, `ensure_by_key`,
  `resolve_with_source`, `validate_spec_value`, `all_specs`, `get_spec`). Import
  these **only** when implementing a settings-admin surface. They are defined in
  `settings_resolver` (so features can reach them without importing another
  feature) but are re-exported here to keep the write path from being mistaken
  for general API.

## Internal modules and names (do not import)

- `dotmac_kernel.display` — consumed only within the kernel (by `templating` /
  `web_deps`). Display formatting reaches templates through the registered Jinja
  filters, not a consumer import.
- The `settings_resolver` **write helpers** are private *as `settings_resolver`
  names* — reach them via `settings_admin` (above).
- Import-time singletons (`templating.templates` the Jinja environment,
  `logging.request_id_var` / `JsonLogFormatter`) are internal. Use the behavior
  (`render`, `install_surface_globals`, `setup_logging`); never construct a
  second Jinja environment or reach the singletons directly.
- Every underscore-prefixed name in any module is private.

## Versioning & deprecation policy

`dotmac-kernel` follows **Semantic Versioning** for its public surface:

- **MAJOR** — a breaking change to any public name (removal, signature change,
  behavior change a caller can observe), or removing a module from
  `SUPPORTED_MODULES`.
- **MINOR** — additive: new public names/modules, new optional parameters.
- **PATCH** — bug fixes with no public-surface change.

**Pre-1.0 (`0.x`, incl. the `0.1.0a1` alpha):** the surface is still settling;
a `0.MINOR` bump may carry breaking changes, each called out in the kernel
`CHANGELOG`. The public surface and this document are nonetheless authoritative
for what is *intended* to be stable.

**Deprecation:** once past `1.0`, a public name is removed only after at least
one MINOR release in which it is documented as deprecated (in `CHANGELOG` and,
where practical, a `DeprecationWarning`) with a stated replacement.

**Private surface:** carries no guarantee at any version. Reaching into a
private name or module is unsupported and the governance test blocks the
reference assembly from doing so.
