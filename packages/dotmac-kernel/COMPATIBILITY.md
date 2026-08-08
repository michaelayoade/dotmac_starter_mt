# dotmac-kernel — compatibility & public API

This document defines the **supported public surface** of `dotmac-kernel` and
the stability guarantees around it. The authoritative machine-readable manifest
is `dotmac_kernel.__init__` (`SUPPORTED_MODULES`, `INTERNAL_MODULES`, the
curated top-level `__all__`, and each supported module's own `__all__`); this
document is its prose companion. The governance test
`tests/architecture/test_kernel_public_surface.py` enforces that the reference
assembly imports only what is documented here.

The current kernel version is the `[tool.poetry] version` in this package's
`pyproject.toml` (pre-release `0.x` alphas; per-release notes in
`CHANGELOG.md`) — it is deliberately not repeated here, where it would drift.

## Settings: the minimal core, and everything else

Nine modules carry settings — `settings_resolver`, `settings_models`,
`settings_admin`, `settings_cache`, `settings_crypto`, `setting_domains`,
`setting_scopes`, `setting_value_types`, `secret_sources` — plus history,
retention, BYOK, change events and bulk reads inside them. **A new application
needs three things from all of that.** The rest exists because a product asked
for it, and adopting a piece because it exists is how a configuration system
becomes a subsystem.

Everything below is public and supported. This section is about what to REACH
FOR, not what is allowed.

### The core three

1. **Declare your domains** on the owning module's manifest
   (`setting_domains=(...)`). A domain is an open registered string, so naming
   one costs no kernel change (ADR-0008).
2. **Register your specs** — `register_specs([SettingSpec(...)])` at import
   time, in the module that owns them.
3. **Read them** — `resolve_value(db, domain, key, tenant_id=...)`.

That is a complete, multi-tenant, RLS-isolated, admin-editable settings system.
Single-tenant is the same code path with one tenant row, not a second one.

### Everything else, and the condition for reaching for it

Take these ONE AT A TIME, when the stated condition is true of your
application. None is a prerequisite for another unless noted.

| Capability | Reach for it when |
|---|---|
| `resolve_with_source` | A screen must show WHERE a value came from. |
| `resolve_many` | One screen resolves many keys and the per-key queries show up. |
| `settings_cache` | Profiling says resolution is hot. Inert until you `install_settings_cache(store)`; secrets are never cached. |
| `setting_scopes` (beyond platform/tenant) | You genuinely have a level between or below them — per-site, per-reseller, per-user. Most products never do. |
| `setting_value_types` (beyond the built-ins) | A value has a shape the built-in types cannot store correctly. `money` already exists; do not model currency as a string. |
| `settings_crypto` | You store a secret AS a setting. Needs the `settings-crypto` extra. |
| `KeyProvider` | Encryption keys live in a secret store rather than the environment. |
| `secret_sources` | Secret material lives in a store and is NOT a setting (ADR-0009). |
| BYOK (per-tenant keys) | A specific customer contractually requires its own key material. |
| `DomainSettingHistory` + `SettingChangeContext` | You must answer "who changed this, and when". |
| `prune_setting_history` | History growth is measured and a retention period is decided. |
| Change events | Another system must react to a setting changing. `settings_change_events` defaults to `False`: an event with no relay accumulates. |
| `required_at` + `validate_required_settings` | A deployment is invalid without a value, and you want it to fail at boot rather than at first use. |
| `seed_settings_from_env` | Automatic. Env is a bootstrap input, never a read-time fallback. |

### What adopting the core does NOT commit you to

Nothing in the second table. There is no initialisation order that forces the
cache on, no migration that presumes history is wanted, and no scope kind you
must declare. A deployment that registers specs and calls `resolve_value` and
does nothing else is a supported, complete configuration.

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
| `dotmac_kernel.audit` | `AuditEvent`, `write_audit_event`, `PlatformAuditEvent`, `write_platform_audit_event` |
| `dotmac_kernel.audit_actions` | `AuditActionRegistry`, `AuditActionsNotInstalledError`, `DuplicateAuditActionError`, `UndeclaredAuditActionError`, `install_audit_actions`, `active_audit_actions` (audit-action registry; also top-level — see "Manifest declaration catalogues" below) |
| `dotmac_kernel.branding` | `get_brand`, `get_request_branding`, `load_branding`, `reset_brand_cache`, `sanitize_branding_css` |
| `dotmac_kernel.capabilities` | `CapabilityCatalogue`, `DuplicateCapabilityError`, `UndeclaredCapabilityError` (WS1 capability catalogue; also top-level) |
| `dotmac_kernel.config` | `Settings`, `settings`, `validate_settings` |
| `dotmac_kernel.entitlements` | `TenantEntitlementGrant`, `EntitlementDecision`, `grant_entitlement`, `is_entitled` (WS2 entitlement grant store + evaluator; also top-level) |
| `dotmac_kernel.crud` | `CRUDManager` |
| `dotmac_kernel.db` | `get_db`, `get_platform_db`, `platform_session`, `conflict_savepoint`, `engine`, `platform_engine` |
| `dotmac_kernel.deps` | `require_tenant`, `require_user_auth`, `require_role`, `require_permission`, `get_db`, `get_platform_db`, `authenticate_request`, `Depends` |
| `dotmac_kernel.errors` | `register_error_handlers` |
| `dotmac_kernel.exceptions` | `DomainError`, `NotFoundError`, `BadRequestError`, `ConflictError`, `UnauthorizedError`, `ForbiddenError` |
| `dotmac_kernel.features` | `FeatureManifest`, `NavItem`, `load_manifests`, `mount_features` |
| `dotmac_kernel.identity` | `normalize_email`, `person_display_name` |
| `dotmac_kernel.licensing` | `ENVELOPE_SCHEMA`, `LICENCE_SCHEMA`, `REVOCATION_SCHEMA`, `KeyStatus`, `LicenceKey`, `LicenceKeyRing`, `LicenceSubject`, `CapabilityGrant`, `LicenceDocument`, `AppliedLicence`, `VerifiedLicence`, `RevocationList`, `LicenceAcknowledgement`, `ReceiverAppliedState`, `APPLIED_STATE_SCHEMA`, `UNKNOWN_DIGEST`, `applied_state_payload`, `parse_applied_state`, `payload_digest`, `verify_licence`, `verify_revocation_list`, `LicenceError` + its subclasses, and the ADR-0007 applied-state envelope: `APPLIED_STATE_ENVELOPE_SCHEMA`, `APPLIED_STATE_DOMAIN`, `DEPLOYMENT_CHALLENGE_DOMAIN`, `DEPLOYMENT_CHALLENGE_SCHEMA`, `DEPLOYMENT_RESPONSE_SCHEMA`, `AppliedStateEnvelope`, `DeploymentSigner`, `DeploymentVerificationKey`, `VerifiedAppliedState`, `DeploymentPossessionChallenge`, `DeploymentPossessionResponse`, `VerifiedDeploymentPossession`, `applied_state_signing_input`, `seal_applied_state`, `verify_applied_state`, `answer_possession_challenge`, `verify_possession` (WS8 signed-licence verification; submodule-only; see "Signed-licence verification" below) |
| `dotmac_kernel.logging` | `setup_logging` |
| `dotmac_kernel.messaging` | `CommandEnvelope`, `process_once`, `ProcessOutcome`, `CommandHandler`, `process_once_platform`, `PlatformCommandHandler`, `enqueue_event`, `enqueue_platform_event`, `ClaimedPlatformEvent`, `claim_platform_batch`, `PlatformDeliveryTransport`, `LoggingPlatformTransport`, `InboxRecord`, `PlatformInboxRecord`, `OutboxEvent`, `PlatformOutboxEvent`, `InboxStatus`, `OutboxStatus` (see "Outbox/inbox" below) |
| `dotmac_kernel.messaging.envelope` | `CommandEnvelope` |
| `dotmac_kernel.messaging.inbox` | `process_once`, `ProcessOutcome`, `CommandHandler` |
| `dotmac_kernel.messaging.outbox` | `enqueue_event`, `enqueue_platform_event` |
| `dotmac_kernel.messaging.platform` | `process_once_platform`, `PlatformCommandHandler` |
| `dotmac_kernel.messaging.relay` | `RelayPolicy`, `ClaimedEvent`, `FailureOutcome`, `claim_batch`, `record_success`, `record_failure` (WS3 relay behavior; dispatcher-bound session; see "Outbox/inbox" below) |
| `dotmac_kernel.messaging.platform_relay` | `ClaimedPlatformEvent`, `claim_platform_batch`, `record_success`, `record_failure` (platform relay behavior; reuses `relay`'s `RelayPolicy`/`FailureOutcome`/backoff; `platform_outbox_dispatcher`-bound session) |
| `dotmac_kernel.messaging.worker` | `DeliveryTransport`, `LoggingTransport`, `run_once`, `run_forever` (WS3 relay polling worker; receives session factories, never builds engines; run via `scripts/run_relay.py`) |
| `dotmac_kernel.messaging.platform_worker` | `PlatformDeliveryTransport`, `LoggingPlatformTransport`, `run_once`, `run_forever` (platform relay worker; dispatcher claims/settles, delivery on a separate `platform_api` session with no tenant context; run via `scripts/run_platform_relay.py`) |
| `dotmac_kernel.messaging.models` | `InboxRecord`, `PlatformInboxRecord`, `OutboxEvent`, `PlatformOutboxEvent`, `InboxStatus`, `OutboxStatus` |
| `dotmac_kernel.middleware.csrf` | `CSRFMiddleware` |
| `dotmac_kernel.middleware.observability` | `ObservabilityMiddleware` |
| `dotmac_kernel.middleware.rate_limit` | `RateLimitMiddleware`, `RateLimitStore`, `MemoryStore` |
| `dotmac_kernel.middleware.security_headers` | `SecurityHeadersMiddleware` |
| `dotmac_kernel.middleware.tenant` | `TenantResolverMiddleware` |
| `dotmac_kernel.migrations` | `versions_dir` (the kernel base Alembic revisions, for a consuming assembly's `version_locations`) |
| `dotmac_kernel.migrations.gate` | `run_gate`, `GateReport`, `RevisionRecord`, `scan_location`, `scan_revision_file`, `version_locations_from_ini`, `SCHEMA_QUALIFIED_OPS` (the composed migration gate — see "Database namespaces and migration lineage" below) |
| `dotmac_kernel.migrations.catalog` | `audit_snapshot`, `audit_live_schemas`, `audited_schemas`, `fetch_snapshot`, `catalog_queries`, `SchemaSnapshot`, `TableFacts`, `PolicyFacts`, `ForeignKeyFacts`, `TENANT_COLUMN`, `DEFAULT_APP_ROLE` (the post-migration live-catalog contract — see the same section) |
| `dotmac_kernel.models` | `Base`, `TimestampMixin`, `uuid_pk`, `Tenant`, `TenantDomain`, `Party`, `PartyType`, `PartyPerson`, `PartyOrganization`, `Role`, `PartyRole`, `AuthSession`, `UserCredential` |
| `dotmac_kernel.models_platform` | `PlatformAdmin`, `PlatformSession`, `PlatformAuditEvent` |
| `dotmac_kernel.modules` | `ModuleManifest`, `ModuleRegistry`, `ModuleInventoryEntry`, `AnyManifest`, `KERNEL_MODULE_CONTRACT_VERSION`, `SUPPORTED_MODULE_CONTRACT_VERSIONS`, `UNVERSIONED`, `ModuleRegistryError` + its subclasses (`DuplicateModuleError`, `ModuleContractVersionError`, `MissingModuleDependencyError`, `ModuleDependencyCycleError`), `UnknownModuleError` (module manifest + registry; also top-level — see "Module manifest and registry" below) |
| `dotmac_kernel.money` | `Money`, `Currency`, `currency`, `ExchangeRate`, `MoneyError`, `CurrencyMismatchError`, `Amountable`, `DEFAULT_ROUNDING` (exact money + FX value objects; also top-level) |
| `dotmac_kernel.namespaces` | `MigrationOwner`, `NamespaceRegistry`, `MIGRATION_OWNER_LEDGER`, `KERNEL_MIGRATION_OWNER`, `ASSEMBLY_MIGRATION_OWNER`, `HOST_MIGRATION_OWNERS`, `HOST_SCHEMA`, `MODULE_SCHEMA_PREFIX`, `RESERVED_SCHEMAS`, `MAX_REVISION_ID_LENGTH`, `MAX_IDENTIFIER_LENGTH`, `MAX_MIGRATION_PREFIX_LENGTH`, `REVISION_SEQUENCE_DIGITS`, `module_schema`, `qualified`, `schema_table_args`, `revision_id`, `revision_id_pattern`, `validate_schema`, `validate_short_code`, `validate_migration_prefix`, `validate_branch_label`, `NamespaceError` + its subclasses (`InvalidSchemaError`, `InvalidMigrationPrefixError`, `InvalidRevisionIdError`, `DuplicateSchemaError`, `DuplicateMigrationPrefixError`, `DuplicateBranchLabelError`, `DuplicateTableOwnerError`, `UnallocatedNamespaceError`, `NamespaceAllocationError`, `HostSchemaClaimError`) (ADR-0006 D1; most also top-level — see "Database namespaces and migration lineage" below) |
| `dotmac_kernel.profiles` | `DeploymentProfileSpec`, `DeploymentProfileRegistry`, `ProfileValidationReport`, `DuplicateProfileError`, `UnknownProfileError` (WS1 deployment-profile registry; also top-level) |
| `dotmac_kernel.permissions` | `PermissionSpec`, `PermissionCatalogue`, `DuplicatePermissionError`, `UndeclaredPermissionError`, `install_permissions`, `active_permissions` (permission catalogue; also top-level — see "Manifest declaration catalogues" below) |
| `dotmac_kernel.platform_auth` | `require_platform_admin`, `platform_auth_router`, `PLATFORM_AUDIENCE` |
| `dotmac_kernel.providers` | re-exports the provisioning surface (see below) |
| `dotmac_kernel.providers.provisioning` | `ProvisioningProvider`, `ProvisioningRequest`, `ProvisioningStep`, `PlanResult`, `ApplyResult`, `ObserveResult`, `ProvisioningStatus`, `StepStatus`, `ProvisioningError`, `ProvisioningRetryableError`, `ProvisioningTerminalError`, `ProvisioningPlanError`, `ProvisioningApplyError`, `ProvisioningCancelled` |
| `dotmac_kernel.query` | `apply_pagination`, `escape_like` |
| `dotmac_kernel.security` | `hash_password`, `verify_password`, `password_needs_rehash`, `hash_token`, `issue_access_token`, `decode_access_token` |
| `dotmac_kernel.setting_domains` | `SettingDomainRegistry`, `SettingDomainsNotInstalledError`, `DuplicateSettingDomainError`, `UndeclaredSettingDomainError`, `install_setting_domains`, `active_setting_domains` |
| `dotmac_kernel.settings_models` | `SettingDomain`, `SettingValueType`, `DomainSetting`, `KERNEL_SETTING_DOMAINS` |
| `dotmac_kernel.settings_resolver` | `SettingSpec`, `register_specs`, `resolve_value` |
| `dotmac_kernel.settings_cache` | `MISS`, `install_settings_cache`, `active_settings_cache`, `cached`, `store_resolved`, `invalidate`, `setting_cache_key`, `setting_key_prefix` (scoped read cache for resolved settings; OFF until a store is installed) |
| `dotmac_kernel.settings_crypto` | `ENCRYPTED_PREFIX`, `DEFAULT_KEY_ID`, `KEYRING_ENV_VAR`, `KEY_ENV_VAR`, `KEY_FILE_ENV_VAR`, `EncryptionKey`, `KeyStatus`, `Keyring`, `KeyringError`, `SettingsEncryptionError`, `encrypt_value`, `decrypt_value`, `encrypted_key_id`, `encryption_configured`, `is_encrypted`, `keyring`, `reencrypt_secrets` (at-rest encryption of secret settings, with a rotatable keyring; needs the `settings-crypto` extra) |
| `dotmac_kernel.settings_admin` | `all_specs`, `get_spec`, `resolve_with_source`, `upsert_by_key`, `ensure_by_key`, `validate_spec_value` |
| `dotmac_kernel.templating` | `render`, `install_surface_globals`, `install_stylesheets`, `static_dir`, `use_assembly_templates` |
| `dotmac_kernel.testing` | `create_test_engine`, `isolated_session`, `assembly_test_client`, `FakeClock`, `FakeSeeder`, `InMemoryRateLimitStore`, `fake_branding`, `FakeProvisioningProvider`, `check_provisioning_provider_contract`, `FakeLicenceSigner` (see "Testing kit" below) |
| `dotmac_kernel.testing.harness` | `create_test_engine`, `isolated_session`, `assembly_test_client` |
| `dotmac_kernel.testing.fakes` | `FakeClock`, `FakeSeeder`, `InMemoryRateLimitStore`, `fake_branding` |
| `dotmac_kernel.testing.licensing` | `FakeLicenceSigner` (ephemeral in-memory Ed25519 test signer — the ONLY signer in the kernel; needs the `cryptography` dependency at instantiation) |
| `dotmac_kernel.testing.provisioning` | `FakeProvisioningProvider`, `check_provisioning_provider_contract` |
| `dotmac_kernel.web_deps` | `require_web_auth`, `is_secure_request`, `safe_next_url`, `WebAuthRedirect` |

### Module manifest and registry (`dotmac_kernel.modules`)

The versioned module declaration and the one authority on whether an installed
module set is coherent (module control-plane directive step 2). Pure and
in-memory, like `capabilities` and `profiles`: it **describes installed code**;
it never grants entitlement and it never deploys anything.

- **`ModuleManifest`** (frozen) — `code`, `version`, `contract_version`,
  `dependencies`, `api_routers`, `web_routers`, `nav`, `capabilities`,
  `permissions`, `audit_actions`, `feature_flags`, `setting_domains`,
  `short_code`, `migration_prefix`,
  `migration_branch`, `tables`, `core`, `enabled_by_default`, `seed`. `code`
  is the stable identifier every other authority references (a dependency edge,
  a profile's required/forbidden set, a capability owner). `version` is the module's own release version;
  `contract_version` is the kernel manifest generation it was built against —
  independent facts, and only the latter gates loading.
- **`ModuleRegistry(manifests)`** — construction IS validation, fail-closed on
  all four:
  - a duplicate `code` → `DuplicateModuleError`;
  - a `contract_version` outside `SUPPORTED_MODULE_CONTRACT_VERSIONS` →
    `ModuleContractVersionError` (the kernel's own generation is
    `KERNEL_MODULE_CONTRACT_VERSION`; the supported set is a constructor
    keyword, so supporting two generations is a rollout rather than a flag day);
  - a dependency on a code that is not installed →
    `MissingModuleDependencyError`;
  - a dependency cycle → `ModuleDependencyCycleError`, whose message names the
    actual path (`a -> b -> a`).

  All four share the `ModuleRegistryError` base and are `ValueError`s.
  Construction ALSO assigns database namespaces — it builds a
  `NamespaceRegistry` from the manifests plus the kernel's allocation ledger
  (see "Database namespaces and migration lineage" below), so a contested or
  unallocated namespace raises a `NamespaceError` subclass here too. Read it
  back with `namespaces()`.
- **Deterministic startup order** — `startup_order()` is a pure function of
  (declaration order, dependency edges): dependencies first, **declaration order
  as the tiebreak**. Declaration order, not alphabetical, on purpose: an
  assembly's module list is a deliberate mount order (route matching is
  first-match-wins), so adopting the registry must not silently reorder an
  assembly whose modules declare no dependencies. Same manifests in, same order
  out, every boot.
- **Deployment enablement** — `enabled_codes(disabled)` is the single definition
  of enabled (not in `disabled`, and not `enabled_by_default=False`).
  `enabled_order(disabled)` filters the startup order to those and **fails
  closed if an enabled module depends on one that is not enabled** — installed
  is not sufficient; "dependencies satisfied" means the dependency is running.
- **Inventory** — `inventory(disabled)` returns `ModuleInventoryEntry` rows
  (`code`, `version`, `contract_version`, `dependencies`, `core`, `enabled`,
  `db_schema`, `migration_branch`) sorted by code so two deployments'
  inventories are diffable; `inventory_payload(disabled)` is the JSON-safe
  diagnostics document (`kernel_contract_version`, `modules`, `startup_order`,
  `migration_owners`). The kernel supplies
  the CONTRACT, not an endpoint: public `/health` stays liveness-only and
  discloses none of it, and the authenticated platform diagnostics surface is
  the control plane's own step, composing this payload.
- **Lookup** — `codes()`, `is_installed(code)`, `get(code)` (raising
  `UnknownModuleError`).

**Compatibility with `FeatureManifest`.** `FeatureManifest` remains fully
supported and unchanged. The registry accepts either shape — freely mixed in one
assembly — and adapts a feature via `ModuleManifest.from_feature(manifest, *,
version=UNVERSIONED, contract_version=..., dependencies=(), short_code=None,
migration_prefix=None, migration_branch=None, tables=())`, which carries every
field across and invents nothing (`UNVERSIONED` is `"0.0.0"`, a real sortable
version that reads as "not declared yet"; the keywords let an assembly pin a
version or declare edges for a package it has not migrated). In the other
direction, `ModuleManifest` exposes read-only `name`/`routers` properties
aliasing `code`/`api_routers`, so `mount_features`,
`install_surface_globals`, and `CapabilityCatalogue.from_manifests` take a
module manifest without a call-site change. `AnyManifest` is the union type used
in those signatures.

**Deliberately not declared yet.** The directive's manifest sketch also lists
`settings`, `feature_flags`, `entity_types`, and `health_checks`. Those belong to
later program steps, and the same directive requires CI to fail when "a
declaration has no consumer" — each field lands with the registry code that
derives behavior from it, as `permissions` and `audit_actions` did in step 3
(below).

### Database namespaces and migration lineage (`dotmac_kernel.namespaces`, `dotmac_kernel.migrations.gate`, `dotmac_kernel.migrations.catalog`)

ADR-0006 **D1**. One immutable Postgres schema and one immutable migration
lineage identity per **stateful** module; a **stateless** module declares none.

**Schema assignment.** A stateful module declares a `short_code` on its
manifest; its schema is the derived, read-only `ModuleManifest.db_schema`,
always `mod_<short_code>` and always built through `module_schema()`. It is
never inferred from `code`, `name`, or any brand string, and there is no
settable schema attribute to re-point at runtime. `public` (`HOST_SCHEMA`)
stays the **compatibility** namespace of the kernel and the one host assembly
and is not available to installable modules — claiming it raises
`HostSchemaClaimError`.

**Full qualification.** Module models, migrations, foreign keys, policies,
functions and raw SQL name their schema explicitly; nothing may rely on
`search_path`, which is connection state anything can change. `qualified()`
builds `schema.table`; `schema_table_args(schema)` is the `__table_args__`
fragment that binds a SQLAlchemy model to a module schema.

**Migration identity.** Each owner receives an immutable, globally unique short
`prefix` and `branch_label`. Revision ids are `<prefix>_<sequence>_<slug>`,
built by `revision_id()`, which **raises rather than truncating** past
`MAX_REVISION_ID_LENGTH` — Alembic declares `alembic_version.version_num` as
`String(32)`, so an over-long id fails at `alembic upgrade` against a real
database, not at authoring time. Each module lineage has its own base and
branch label; cross-lineage ordering uses `depends_on`, never `down_revision`.

**Where immutability is enforced.** `MIGRATION_OWNER_LEDGER` is the checked-in,
kernel-shipped allocation record — the kernel is the shared dependency, so this
is the one table where "globally unique across Dotmac repos" can be true.
`NamespaceRegistry.from_manifests` validates the entire ledger even when an
allocated owner is not installed, then refuses a stateful module absent from it
(`UnallocatedNamespaceError`) or differing from it in schema, prefix or branch
label (`NamespaceAllocationError`). Changing a row is therefore a visible
kernel diff plus a release.

**`NamespaceRegistry`** is construction-is-validation and rejects a duplicate
schema claim (`DuplicateSchemaError`), migration prefix
(`DuplicateMigrationPrefixError`), branch label (`DuplicateBranchLabelError`)
or table (`DuplicateTableOwnerError`). `ModuleRegistry` builds one during its
own construction and exposes it via `namespaces()`.

**The composed CI gate** (`dotmac_kernel.migrations.gate.run_gate`) is the
build-time enforcement: it loads every selected version location — resolved
from the deployment's own Alembic config via `version_locations_from_ini()` —
and rejects duplicate revisions, unregistered or mismatched prefixes,
duplicate/foreign branch labels, duplicate schema claims and duplicate table
ownership, plus lineage-root, `down_revision`-crossing, id-length and
`schema=` qualification faults. The scanner follows local `upgrade()` helpers,
understands typed Alembic metadata and D1 schema constants, checks inline and
imperative foreign keys, and rejects schema-qualified writes aimed at another
owner. It is a pure **AST** scan: nothing is imported and no database is
touched, so it runs in the same cheap CI step as lint and fails **before an
image can be built**. Locations are attributed to owners through the lineage
root's branch label, which is also what makes an `alembic_version` row
explainable (`GateReport.attribution`).

**The post-migration live-catalog gate**
(`dotmac_kernel.migrations.catalog.audit_live_schemas`) applies the kernel's
RLS/grant contract across every registered module schema after migrations run.
It is deliberately split into parameterised SQL builders (`catalog_queries()`;
`:schema` is always a bind parameter) and a pure decision function
(`audit_snapshot(SchemaSnapshot)`), so the contract is exercisable from
synthetic snapshots without Postgres.

**Two grandfathered lineages.** `kernel` and `assembly` predate D1; their
revision ids are already recorded in live `alembic_version` rows, so
`MigrationOwner.legacy_revision_pattern` keeps their original format and
exempts them from the strict id and `schema=` rules. Their tables legitimately
live in `public`. Every installable module gets the strict rules.

### Manifest declaration catalogues (`dotmac_kernel.permissions`, `dotmac_kernel.audit_actions`, `dotmac_kernel.setting_domains`)

Siblings of `dotmac_kernel.capabilities.CapabilityCatalogue` and
`dotmac_kernel.flags.FlagCatalogue` — same shape, same fail-closed posture, same
invariant: **a code is declared by exactly one module's manifest and may never be
invented anywhere else.** Pure and in-memory; no engine, no I/O.

**ADR-0008 makes this the standard.** A kernel-level vocabulary whose members
belong to modules is declared on manifests and validated by a registry — never
enumerated by the kernel as an enum or a fixed list, and never pinned by a CHECK
constraint on the backing column. If you are adding a sixth, copy
`dotmac_kernel.audit_actions` and change the nouns.

- **`PermissionSpec(code, description="", default_roles=("admin",))`** — one
  permission a module owns. `default_roles` is the code-declared default binding
  (the role slugs whose holders satisfy it), the same relationship a
  `SettingSpec.default` has to a `domain_settings` row; it must be non-empty.
- **`PermissionCatalogue.from_manifests(manifests)`** — construction IS
  validation: a code declared by two modules raises `DuplicatePermissionError`.
  `require(code)` returns the declared spec or raises
  `UndeclaredPermissionError`; `is_declared`/`spec`/`owner`/`codes` read it.
- **`AuditActionRegistry.from_manifests(manifests)`** — the same, for the
  free-text-no-longer `audit_events.action` vocabulary:
  `DuplicateAuditActionError` on two owners, `require(action)` raising
  `UndeclaredAuditActionError`, plus `is_declared`/`owner`/`actions`.
- **`SettingDomainRegistry.from_manifests(manifests)`** — the same again, for
  `domain_settings.domain`: `DuplicateSettingDomainError`, `require(domain)`
  returning a `SettingDomain` or raising `UndeclaredSettingDomainError`, plus
  `is_declared`/`owner`/`domains`. **`SettingDomain` is an open `str` subclass,
  not an enum** — a kernel cannot enumerate its consumers' domains (this repo
  declares five; `dotmac_erp` runs twenty-one), so the column is a plain
  `String(120)` and correctness comes from the write boundary. Kernel-owned
  domains are bound as class attributes (`SettingDomain.branding`); a product
  constructs its own (`SettingDomain("payroll")`).
- **Process-active install.** `install_permissions` / `install_audit_actions` /
  `install_setting_domains` set the process-active catalogue and registries;
  `active_permissions` / `active_audit_actions` / `active_setting_domains` read
  them. `create_app` installs all of them from the INSTALLED module set before
  mounting anything. Permissions default to EMPTY so an uninstalled
  authorization catalogue denies safely. Audit actions and setting domains
  distinguish NOT INSTALLED (`AuditActionsNotInstalledError` /
  `SettingDomainsNotInstalledError`) from INSTALLED-EMPTY (every write is
  undeclared) — an uninstalled write-path registry would otherwise reject writes
  inside the caller's transaction and turn a wiring mistake into a failed
  business operation. A consumer that builds an app by hand (a test mounting a
  router on a bare `FastAPI()`) must install them itself, exactly as it must call
  `install_surface_globals`.

**The consumers, which is why the fields exist at all:**

- `dotmac_kernel.deps.require_permission(code)` is the route guard. It resolves
  the declared spec at request time and requires the actor to hold one of its
  `default_roles`, 403 otherwise — a strict generalisation of `require_role`,
  which remains supported and is the raw role check underneath. The returned
  dependency carries the code, and `create_app` walks every MOUNTED route and
  raises `UndeclaredPermissionError` at boot if any references a code the
  catalogue does not declare.
- `dotmac_kernel.audit.write_audit_event` validates `action` against the active
  registry **before** it adds anything to the session, so a rejected write leaves
  no partial state. `write_platform_audit_event` is deliberately NOT validated
  this way: platform actions are written by the kernel's own control plane, which
  has no module manifest to declare them on.

### Composing an app: `ProductAssemblySpec` + `create_app`

A product assembly declares itself as a frozen `dotmac_kernel.assembly.ProductAssemblySpec`
(`name`, `modules`, `settings_overrides`, `branding`, `providers`, `web_enabled`,
`disabled_modules`, `assembly_template_dir`, `assembly_static_dir`,
`packaged_static_dirs`, `stylesheets`, `assembly_migrations`)
and calls `dotmac_kernel.create_app(spec) -> FastAPI` (also reachable as
`from dotmac_kernel import create_app`; it is lazily loaded so `import dotmac_kernel`
stays DB-free). `create_app` wires logging, module-registry validation, the lifespan
(config validation + module seeds), the middleware stack, error handlers, `/health`, the
platform-auth surface, the static mount, and module mounting.
`assembly_template_dir`/`assembly_static_dir` layer the
assembly's own templates/static OVER the kernel's (first-match-wins, via `use_assembly_templates`
and `LayeredStaticFiles`).

**Presentation-package composition (0.1.0a13).** `packaged_static_dirs` and
`stylesheets` are the two slots an assembly fills to adopt an installed
presentation package — a `dotmac-ui` release, a packaged theme.
`packaged_static_dirs` layers those packages' static directories *under* the
assembly's own and *over* the kernel's, so their assets are reachable;
`stylesheets` adds their compiled CSS URLs to every page's `<head>` (after the
kernel's own links, in declaration order — later wins on equal specificity),
installed as the `extra_stylesheets` Jinja global by `install_stylesheets`. Both
are ignored in API-only mode (`web_enabled=False`).

The kernel deliberately does not know what fills them. ADR-0006 § 2 fixes the
dependency direction as `assembly → module → dotmac-ui → dotmac-kernel`, so these
are anonymous slots: the kernel never imports, names, or resolves a presentation
package (import-linter contract "Kernel must not import the UI package"), and
`stylesheets` takes URLs rather than paths because the assembly owns the mapping
from a package's static directory to a URL.

**Capability enforcement (0.1.0a13).** `require_capability(code)` is the
tenant-entitlement guard; `require_permission(code)` remains the actor guard.
They are different questions and compose on the same route. A capability is
declared as a `CapabilitySpec` on the owning module's manifest — a bare string
is still accepted and means `default_granted=True`. `install_capabilities` /
`active_capabilities` mirror the permission seam, `create_app` installs the
catalogue from the INSTALLED module set, and a mounted route referencing an
undeclared code fails the boot. `provision_tenant` applies
`default_granted_codes()` in the transaction that creates the tenant, so a new
tenant is usable without an operator granting each capability by hand.

**Packaged templates (0.1.0a13).** `packaged_template_dirs` is the template
counterpart, and the reason an installable MODULE can ship an `/admin/...`
screen: its Jinja files are package data outside any assembly's template root.
`dotmac_kernel.templating.compose_templates` is the one loader authority —
assembly directory, then packaged directories in declaration order, then the
kernel's — and `create_app` calls it unconditionally. `use_assembly_templates`
remains published and delegates to it. Note the consequence of "unconditional":
an empty composition resets to kernel-only, so a second `create_app` in one
process does not inherit a previous spec's override.

`spec.modules` accepts `ModuleManifest`s and/or `FeatureManifest`s. `create_app`
validates them into a `ModuleRegistry` **before mounting anything** — an
incoherent set raises a `ModuleRegistryError` at boot rather than surfacing as a
mystery 500 — then drives surface globals, mounting, and seeds from that one
deterministic order. The validated registry and its inventory are published on
`app.state.module_registry` / `app.state.module_inventory` for a product's own
health/diagnostics surface.

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
- **`licensing`** — `FakeLicenceSigner`: signs licence payloads /
  revocation lists with an **ephemeral, per-instance, in-memory Ed25519 key**
  (the only private key anywhere in the kernel — never persisted, never a real
  issuer key), so vendor-plane and product tests can build valid and
  deliberately-broken envelopes for `dotmac_kernel.licensing` without key
  custody. Instantiation needs the `cryptography` package (the `testing` extra
  now includes it, as does `licensing`).

### Outbox/inbox + idempotent commands (`dotmac_kernel.messaging`)

The kernel primitive for exactly-once command processing and atomic event
emission (WS3). Submodule-only (like `deps`): the write helpers pull in the DB
transaction authority, so `messaging` is NOT re-exported at the DB-free top
level — import it directly (`from dotmac_kernel.messaging import ...`).

- **Idempotent command** — `CommandEnvelope(command_id, command_type, tenant_id,
  payload, actor_party_id=None, correlation_id=None, issued_at=None)` (frozen)
  wraps a command whose `command_id` is its per-tenant idempotency key.
  `process_once(db, envelope, handler) -> ProcessOutcome` runs `handler` (a
  `CommandHandler`, `Callable[[Session, CommandEnvelope], Mapping | None]`) AT
  MOST ONCE per `(tenant_id, command_id)`: the first delivery runs it and records
  an `InboxRecord` with the result; a later delivery replays that result without
  re-running. `ProcessOutcome(command_id, status, result)` exposes
  `was_duplicate`. Concurrency-safe via the `uq_inbox_records_tenant_command_id`
  constraint + a SAVEPOINT rollback of the losing racer.
- **Transactional outbox** — `enqueue_event(db, *, tenant_id, event_type,
  payload=None, correlation_id=None) -> OutboxEvent` inserts a `pending` event in
  the CALLER's transaction, so the event persists iff that transaction commits
  (atomic with the state change). A relay (WS3 slice 2) drains pending events
  out-of-band — the named reconciler for the side effect.
- **Platform-scoped idempotent command** — the platform-level counterpart for a
  platform actor operating on platform-level resources (no tenant context, so no
  envelope). `process_once_platform(db, *, command_id, command_type, handler,
  correlation_id=None) -> ProcessOutcome` runs `handler` (a
  `PlatformCommandHandler`, `Callable[[Session], Mapping | None]`) AT MOST ONCE
  per `command_id` ALONE (globally unique, not per-tenant) and records a
  `PlatformInboxRecord`; a later delivery replays the result. Concurrency-safe via
  the `uq_platform_inbox_command_id` constraint + the same SAVEPOINT-rollback of
  the losing racer.
- **Persisted state** — `InboxRecord` / `OutboxEvent` (both tenant-scoped,
  RLS-protected, kernel migration `0008`); `PlatformInboxRecord` (a PLATFORM
  catalog table — no `tenant_id`, no RLS, grants-not-RLS, kernel migration
  `0009`); `InboxStatus` (`PROCESSED`/`FAILED`) and `OutboxStatus`
  (`PENDING`/`SENT`/`FAILED`) status vocabularies.

Transaction-authority contract: `process_once` / `process_once_platform` /
`enqueue_event` RECEIVE a `Session` and only `add`/`flush` — they never construct
a session or `commit`/`rollback`; the request (or a `platform_session`) boundary
owns that.

### Capability catalogue + deployment profiles (WS1)

Two pure, in-memory code contracts (no database, no fleet state). They
**describe**; they never **grant** or **deploy**.

- **Capability catalogue** (`dotmac_kernel.capabilities`) — a module declares its
  capability codes on `FeatureManifest.capabilities` (e.g. `"inventory.use"`).
  `CapabilityCatalogue.from_manifests(...)` builds the catalogue; construction
  fails closed on a duplicate code (one owning module per code).
  `is_declared`/`require`/`owner`/`codes` answer "is this code real, and whose is
  it?". **Boundary:** the catalogue *describes supported capabilities* — it does
  NOT grant entitlement. A capability code may only be *referenced* by a grant or
  a profile; it may never be **invented** outside a manifest. Applied entitlement
  is data-plane-owned; commercial allocation is vendor-control-plane-owned.
- **Deployment-profile registry** (`dotmac_kernel.profiles`) —
  `DeploymentProfileSpec` is a frozen, **versioned** declaration over independent
  axes (required/forbidden module codes; one provider name per seam;
  locale/currency/legal/residency). `(code, version)` is the stable identifier
  consumers pin to; a profile's effective set changes only via an explicit
  version bump. `DeploymentProfileRegistry` enforces unique `code`, answers
  `is_valid_code`, and `validate(...)` runs a **deterministic, fail-closed** check
  (sorted errors) that a required module is enabled, no forbidden module is
  installed, and every named provider resolves — returning a
  `ProfileValidationReport` with a human-readable `render()`. **Boundary:** a
  profile *describes desired composition + constraints*; it is NOT a fleet
  deployment and NOT an update authority, and feature code must never branch on a
  profile string.

Stable identifiers (capability codes; `(profile code, version)`), deterministic
resolution, and the version rule above are part of the public contract and are
covered by consumer tests.

### Signed-licence verification (WS8, `dotmac_kernel.licensing`)

The kernel slice of signed/versioned licence delivery (design brief:
`docs/superpowers/reviews/2026-08-01-ws8-signed-licence-design.md`). The kernel
**verifies only** — issuance and private-key custody are vendor-control-plane
concerns; a product data plane verifies a delivered envelope, projects the
verified capabilities into its OWN local WS2 grants (`grant_entitlement`), and
acknowledges the applied `(licence_id, licence_version, digest)`.

- **Envelope** — DSSE-style `dotmac-licence-envelope/1`: signatures over the
  exact payload **bytes** (no canonical-JSON step); the payload is parsed only
  after a signature verifies; `payload_digest` (sha256 of those bytes) is the
  identity used by replay protection and acknowledgement. Ed25519 only.
- **Keyring** — `LicenceKey`/`LicenceKeyRing` with `KeyStatus`
  `active`/`retired`/`revoked` rotation semantics (`retired` still verifies —
  rotation overlap; `revoked` never does; unknown keys fail closed; duplicate
  `key_id` fails ring construction).
- **`verify_licence(envelope, *, keyring, now, expected_deployment_id,
  require_binding, applied, revoked_licence_ids)`** — fail-closed, offline,
  deterministic (the clock is always injected). Check order is contractual:
  envelope shape → signature → payload parse → licence revocation → deployment
  binding → validity (`valid`/`in_grace`; absent `expires_at` = perpetual) →
  replay/rollback against the receiver's `AppliedLicence` record (stale
  version rejected; same version+digest is an idempotent `reapplied`; same
  version, different digest is a hard conflict). Returns `VerifiedLicence`;
  raises a `LicenceError` subclass whose NAME is the stable rejection reason.
- **`verify_revocation_list`** — signed `dotmac-licence-revocation/1` over the
  same envelope mechanics, with monotonic `list_version` (a stale list cannot
  un-revoke; equal version is an idempotent re-import).
- **`LicenceAcknowledgement`** — the shared cross-plane ack value object
  (`applied`/`rejected` + reason); its transport is vendor/product-owned.
- **`ReceiverAppliedState`** (+ `applied_state_payload` / `parse_applied_state`,
  schema `dotmac-licence-applied-state/1`, sentinel `UNKNOWN_DIGEST`) — what a
  deployment reports about the licence state it is running: deployment ref,
  licence id/version/digest, keyring generation, applied revocation-list
  version (`None` = none imported, which is deliberately distinct from
  version 0), an `observed_at` timestamp, and a `report_id` idempotency key.
  Every field is a CLAIM — authentication and proof happen at the vendor
  plane, which verifies who sent the report and matches the digest against
  what it issued; nothing is trusted on the report's own say-so.
  `status="applied"` requires a real committed identity (version >= 1, real
  digest); `status="rejected"` requires a `reason` and remains representable
  when the envelope never validated (version 0, `UNKNOWN_DIGEST`). This is
  the channel that lets a vendor measure keyring-uptake and
  revocation-application lag, which it cannot infer from its own publishing.
  Validation is strict, fail-closed, and identical for direct construction
  and parsing; unknown fields are ignored so a newer receiver cannot break an
  older vendor.
- **Applied-state envelope (ADR-0007)** — `AppliedStateEnvelope`
  (`seal_applied_state` / `verify_applied_state`, schema
  `dotmac-applied-state-envelope/1`) is how a deployment PROVES which report
  is its own, so the claims above stop being taken on the report's own say-so.
  The signature covers `APPLIED_STATE_DOMAIN ‖ len(key_id) ‖ key_id ‖
  len(payload) ‖ payload` (`applied_state_signing_input`, pinned as a
  conformance vector): the domain separator stops a signature made for any
  other purpose being replayed as a report, and `key_id` is signed because it
  is what resolves to an identity — leaving it out lets the same public key
  registered under two ids attribute a captured report to either. The envelope
  carries the exact signed BYTES, never a re-serialisation.
  `verify_applied_state` resolves `key_id` through the
  `DeploymentVerificationKey`s the caller considers usable right now (status,
  windows and revocation are the registry's decisions, not the kernel's) and
  returns `VerifiedAppliedState`, whose `deployment_ref` is the PROVEN
  identity; the report's own `deployment_ref` stays separately readable via
  `claim_matches_proof` so a contradiction can be quarantined rather than
  resolved in the caller's favour.
- **Possession proof (ADR-0007 §2)** — `DeploymentPossessionChallenge` /
  `DeploymentPossessionResponse` (schemas `dotmac-deployment-challenge/1` and
  `dotmac-deployment-possession-response/1`, under their own domain separator)
  are how a registered key moves `pending → active`. The challenge binds
  `challenge_id`, `key_id`, `deployment_ref`, a >=16-byte nonce and a
  timezone-aware `expires_at` into its signing input; the response carries
  ONLY `challenge_id`, `key_id` and the signature, and `from_wire` REJECTS a
  response that echoes the nonce, deployment or expiry — the issuer's stored
  challenge is authoritative for those. `answer_possession_challenge` signs
  one (receiver side); `verify_possession` checks structural bindings, then
  identifier match, then expiry, then the signature, and returns
  `VerifiedDeploymentPossession`. Consuming the single-use challenge and
  activating the key are the vendor's to do, atomically — the kernel proves
  possession and retires nothing.
- **`DeploymentSigner` owns both halves of the identity** — `key_id` AND
  `deployment_ref`. `seal_applied_state` and `answer_possession_challenge`
  both verify both BEFORE calling `sign`: a deployment never signs a statement
  about another deployment, and because these signatures are portable evidence
  a third party can check, the refusal has to precede the signature rather
  than discard it afterwards.
- **Dependency** — Ed25519 needs `cryptography`, installed via the
  `licensing` extra (`pip install dotmac-kernel[licensing]`). The module
  imports it lazily: types/parsing/digests work without it, and signature
  verification without it raises `VerificationUnavailableError` (fail closed).
  Submodule-only by design (like `messaging`) — nothing licensing-related is
  re-exported at the top level.
- **Boundaries** — no signing API (the testing kit's `FakeLicenceSigner` is an
  ephemeral in-memory test key, never a real issuer key), no storage/tables,
  no delivery transport, no entitlement writes, no interpretation of the
  document's `constraints`.

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

## Dependency floors — and what they do and do not promise

The kernel declares `fastapi>=0.111,<0.116`, `pydantic>=2.7.4,<3.0`,
`pydantic-settings>=2.2,<3.0`, `python>=3.11,<3.14`, and (optional, via the
`licensing` extra) `cryptography>=42` — every Ed25519 API the kernel uses
predates 42, and the floor probe SIGNS AND VERIFIES a licence and a revocation
list on 42.0.8, dotmac_sub's exact production pin. That is the extent of the
claim: the kernel's own full licensing test suite runs on the current
cryptography in this repo's CI, not at the floor.

**Extras are split by consumer need.** `[testing]` pulls only `httpx` (the
TestClient stack); the ordinary fakes/harness/provisioning kit never touches
cryptography, so a product consuming the test kit does not inherit the
licensing crypto stack. `[licensing]` pulls `cryptography` and is also what
`FakeLicenceSigner` needs — install `[testing,licensing]` to use the fake
signer.

**Scope of the claim.** The floor is proven for exactly the union of the two
adopting products' kernel allowlists, and nothing wider:

| Module | Exercised at the floor by |
|---|---|
| `assembly` | constructing a `ProductAssemblySpec` |
| `capabilities` | building a catalogue and requiring a declared code |
| `features` | building a `FeatureManifest` |
| `modules` | building a dependency graph, asserting the order, serializing the inventory, and proving a missing dependency fails closed |
| `money` | exact addition and an `ExchangeRate` conversion |
| `profiles` | building a spec + registry and reading provider selections |
| `providers.provisioning` | the protocol, `FakeProvisioningProvider`, and the reusable contract suite |
| `licensing` | signing AND verifying a licence and a revocation list, plus an applied-state round-trip |
| `testing` | engine, isolated session, and the fakes — with no `DATABASE_URL` |

Everything else in this document — including `db`, `deps`, `app_factory`,
`platform_auth`, the middleware stack, `crud`, `templating`, `branding`,
`settings_admin`, `messaging` and `audit` — is NOT covered by the floor
proof. Those are exercised by this repo's full CI on current versions, and
both products' architecture guards forbid importing them anyway. An assembly
that mounts `create_app` should track a current FastAPI rather than sit at the
floor.

**Why it is a floor and not a preference.** Products adopting the kernel
selectively (`dotmac_sub`, `dotmac_erp`) pin fastapi 0.111.0 / pydantic 2.7.4.
A `^0.115` floor excluded them from consuming *contracts* that never touch
FastAPI at all, which is a packaging accident rather than a compatibility fact.

**How the claim is kept honest.** The required `kernel-floors` CI job runs on
BOTH 3.11 and 3.12. It builds the wheel, installs it with `[testing,licensing]`
into a clean virtualenv with all four floors pinned EXACTLY — failing if the
resolver moves past any of them, which would make the check vacuous — then
CONSTRUCTS, not merely imports, the union of the two products' kernel
allowlists (`assembly`, `capabilities`, `features`, `licensing`, `modules`,
`money` incl. `ExchangeRate`, `profiles`, `providers.provisioning` incl. the
fake and the reusable contract suite, and `testing`), with no `DATABASE_URL`
present. The
licensing leg SIGNS and VERIFIES a licence and a revocation list rather than
merely building keys, because a crypto backend only fails when it is used.

Lowering a floor without that job would convert a clean resolve-time failure
into a runtime one for exactly the consumers the lowering is meant to serve.
Writing the probe immediately caught four API misuses in it — which is the
level of coverage the claim needs to be worth anything.

## Versioning & deprecation policy

`dotmac-kernel` follows **Semantic Versioning** for its public surface:

- **MAJOR** — a breaking change to any public name (removal, signature change,
  behavior change a caller can observe), or removing a module from
  `SUPPORTED_MODULES`.
- **MINOR** — additive: new public names/modules, new optional parameters.
- **PATCH** — bug fixes with no public-surface change.

**Pre-1.0 (`0.x`, incl. the current alphas):** the surface is still settling;
a `0.MINOR` bump may carry breaking changes, each called out in the kernel
`CHANGELOG`. The public surface and this document are nonetheless authoritative
for what is *intended* to be stable.

**Deprecation:** once past `1.0`, a public name is removed only after at least
one MINOR release in which it is documented as deprecated (in `CHANGELOG` and,
where practical, a `DeprecationWarning`) with a stated replacement.

**Private surface:** carries no guarantee at any version. Reaching into a
private name or module is unsupported and the governance test blocks the
reference assembly from doing so.
