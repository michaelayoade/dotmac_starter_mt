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
| `dotmac_kernel.app_factory` | `create_app`, `LayeredStaticFiles` |
| `dotmac_kernel.assembly` | `ProductAssemblySpec` |
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
| `dotmac_kernel.providers` | re-exports the provisioning surface (see below) |
| `dotmac_kernel.providers.provisioning` | `ProvisioningProvider`, `ProvisioningRequest`, `ProvisioningStep`, `PlanResult`, `ApplyResult`, `ObserveResult`, `ProvisioningStatus`, `StepStatus`, `ProvisioningError`, `ProvisioningRetryableError`, `ProvisioningTerminalError`, `ProvisioningPlanError`, `ProvisioningApplyError`, `ProvisioningCancelled` |
| `dotmac_kernel.query` | `apply_pagination`, `escape_like` |
| `dotmac_kernel.security` | `hash_password`, `verify_password`, `password_needs_rehash`, `hash_token`, `issue_access_token`, `decode_access_token` |
| `dotmac_kernel.settings_models` | `SettingDomain`, `SettingValueType` |
| `dotmac_kernel.settings_resolver` | `SettingSpec`, `register_specs`, `resolve_value` |
| `dotmac_kernel.settings_admin` | `all_specs`, `get_spec`, `resolve_with_source`, `upsert_by_key`, `ensure_by_key`, `validate_spec_value` |
| `dotmac_kernel.templating` | `render`, `install_surface_globals`, `static_dir` |
| `dotmac_kernel.testing` | `create_test_engine`, `isolated_session`, `assembly_test_client`, `FakeClock`, `FakeSeeder`, `InMemoryRateLimitStore`, `fake_branding`, `FakeProvisioningProvider`, `check_provisioning_provider_contract` (see "Testing kit" below) |
| `dotmac_kernel.testing.harness` | `create_test_engine`, `isolated_session`, `assembly_test_client` |
| `dotmac_kernel.testing.fakes` | `FakeClock`, `FakeSeeder`, `InMemoryRateLimitStore`, `fake_branding` |
| `dotmac_kernel.testing.provisioning` | `FakeProvisioningProvider`, `check_provisioning_provider_contract` |
| `dotmac_kernel.web_deps` | `require_web_auth`, `is_secure_request`, `safe_next_url`, `WebAuthRedirect` |

### Composing an app: `ProductAssemblySpec` + `create_app`

A product assembly declares itself as a frozen `dotmac_kernel.assembly.ProductAssemblySpec`
(`name`, `modules`, `settings_overrides`, `branding`, `providers`, `web_enabled`,
`disabled_modules`, `assembly_template_dir`, `assembly_static_dir`, `assembly_migrations`)
and calls `dotmac_kernel.create_app(spec) -> FastAPI` (also reachable as
`from dotmac_kernel import create_app`; it is lazily loaded so `import dotmac_kernel`
stays DB-free). `create_app` wires logging, the lifespan (config validation + feature
seeds), the middleware stack, error handlers, `/health`, the platform-auth surface, the
static mount, and feature mounting. `assembly_template_dir`/`assembly_static_dir` layer the
assembly's own templates/static OVER the kernel's (first-match-wins, via `use_assembly_templates`
and `LayeredStaticFiles`).

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

### Provisioning provider (`dotmac_kernel.providers.provisioning`)

The one provider seam pulled into the kernel alpha ahead of the general
workstream-5 provider fill (ruling C6): a **product-neutral** provisioning
contract the vendor control plane consumes so it never defines a local
replacement. There are **no cloud, deployment, or fleet specifics** here — only
the shape of the conversation. It is a contract, not a runner
(contracts-not-implementations, same posture as `RateLimitStore`); concrete
providers and fakes live outside the kernel. The canonical import is the
submodule (`from dotmac_kernel.providers.provisioning import
ProvisioningProvider`); `dotmac_kernel.providers` re-exports the same names.

- **Protocol** — `ProvisioningProvider` (`typing.Protocol`, `runtime_checkable`)
  with four methods, all keyed on opaque identifiers:
  - `plan(request: ProvisioningRequest) -> PlanResult` — read-only; diff the
    opaque desired-state `spec` and return ordered steps + a stable `plan_hash`.
  - `apply(request: ProvisioningRequest) -> ApplyResult` — execute the plan;
    idempotent by `operation_id`; may return a partial result.
  - `observe(operation_id: str) -> ObserveResult` — read-only status snapshot.
  - `cancel(operation_id: str) -> ObserveResult` — cooperative cancellation.
- **Input** — `ProvisioningRequest(intent_id, spec, operation_id=None)`: an
  opaque intent id, an opaque product-neutral desired-state `spec`
  (`Mapping[str, object]`, never interpreted by the kernel), and the optional
  idempotency/resume key.
- **Result types (frozen)** — `PlanResult` (`intent_id`, `plan_hash`, `steps`),
  `ApplyResult` (`intent_id`, `operation_id`, `plan_hash`, `status`, `steps`),
  `ObserveResult` (`intent_id`, `operation_id`, `status`, `steps`, `plan_hash`).
  Steps are `ProvisioningStep(step_id, status, detail)`. `ProvisioningStatus`
  (`PENDING`/`IN_PROGRESS`/`PARTIAL`/`SUCCEEDED`/`FAILED`/`CANCELLED`) and
  `StepStatus` are the status vocabularies. Convenience properties:
  `PlanResult.is_noop`; `ApplyResult.{is_terminal, is_partial, succeeded,
  outstanding_steps}`; `ObserveResult.{is_terminal, outstanding_steps}`;
  `ProvisioningStep.is_settled`.
- **Error hierarchy (stable API)** — `ProvisioningError` base with a
  machine-readable `retryable` class attribute (unknown errors fail closed as
  terminal). `ProvisioningRetryableError` (`retryable = True`) means the same
  `operation_id` may be retried/resumed; `ProvisioningTerminalError`
  (`retryable = False`) will not succeed on retry, with `ProvisioningPlanError`
  (invalid spec) and `ProvisioningApplyError` (terminal mid-apply failure) as
  its subclasses; `ProvisioningCancelled` is the cooperative-cancel outcome
  raised by an in-flight `apply`.

**Semantics encoded in the types:** *cancellation* is cooperative
(`cancel()` signals, the operation settles to `CANCELLED`, `apply` may raise
`ProvisioningCancelled`); *retry vs terminal* is the error hierarchy +
`retryable`; *idempotency* is `operation_id` (re-`apply` with a seen id is a
no-op returning the prior `ApplyResult`; a provider derives a stable id from
`(intent_id, plan_hash)` when omitted); *partial results* are the explicit
`PARTIAL` status + per-step breakdown that `observe` / re-`apply` reconcile via
`outstanding_steps`. A concrete `FakeProvisioningProvider` + a parametrized
contract suite are **not** here — they live in `dotmac_kernel.testing`
(see "Testing kit" below).

### Testing kit (`dotmac_kernel.testing`)

The kernel's **supported test kit** — a consumer assembly builds its unit tests
on this instead of hand-rolling a harness. It is public API under this
document's SemVer policy (its HTTP helper needs the `testing` extra:
`pip install dotmac-kernel[testing]`, which adds `httpx`; the engine/session and
fakes work without it). The package re-exports everything from three submodules:

- **`harness`** — the in-memory-SQLite + savepoint-isolation wiring:
  - `create_test_engine() -> Engine` — a fresh in-memory SQLite engine with
    `Base.metadata` created. The **assembly must import its own feature models
    first** so `Base.metadata` is fully populated (SQLite has no RLS — this is
    for service-logic/unit tests; tenancy is proven separately on Postgres).
  - `isolated_session(engine)` — a context-managed session wrapped in an outer
    transaction + restarting SAVEPOINT, so a test rolls back even if service
    code commits.
  - `assembly_test_client(app, *, session)` — a context-managed `TestClient` for
    a `create_app`-built app with `get_db`/`get_platform_db` overridden onto the
    isolated session; overrides are removed on exit. (Needs the `testing` extra.)
- **`fakes`** — deterministic fakes for the seams that exist today (no fakes for
  protocols that do not yet exist): `FakeClock` (advanceable `now()`),
  `FakeSeeder` (a recording/failing `FeatureManifest.seed` hook),
  `InMemoryRateLimitStore` (the shipped `MemoryStore`, re-exported under a
  test-facing name so tests use the SAME store the kernel ships), and
  `fake_branding(**overrides)` (a fixed brand dict).
- **`provisioning`** — `FakeProvisioningProvider` (a deterministic, in-memory
  `ProvisioningProvider` with failure injection, call recording, and
  partial/resume behavior) and `check_provisioning_provider_contract(factory)`,
  the reusable assertion suite a consumer runs against THEIR provider factory to
  prove it honors the protocol's determinism / idempotency / partial-resume /
  cancellation semantics.

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
