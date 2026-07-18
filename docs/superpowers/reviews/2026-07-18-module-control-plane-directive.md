# Module/plugin control plane directive (Michael, 2026-07-18) — target architecture

Recorded verbatim. Companion to `2026-07-18-adoption-review.md`; supersedes the
registry-building shape of the capability-hardening plan ("The existing
capability-hardening work should be evolved toward this manifest-driven model rather than
building separate registries independently"). Michael's preferred sequence (bottom of this
doc) is the program order; step 1 = the adoption review's item 1 (platform-admin auth).

---

Yes — this should be a first-class part of the starter. The current FeatureManifest,
DISABLED_FEATURES, and settings registry are a good beginning, but they are
deployment-static and too small for a robust module/plugin control plane.

The key is to keep four concepts separate:

| Concept | Authority | Purpose |
|---|---|---|
| Module registry | Installed code manifests | What capabilities physically exist |
| Module entitlement | Platform database | Which tenants may use a module |
| Feature flag | Flag registry + overrides | Controlled rollout or kill switch |
| Setting | Existing typed settings registry | Runtime behavior/configuration |

A feature flag must never substitute for authorization, and a tenant setting must never
claim that uninstalled code exists.

## Recommended effective-capability model

A module is available only when:

installed AND deployment-enabled AND migrations-current AND dependencies-satisfied AND
tenant-entitled AND module-healthy AND actor-has-permission

Feature flags then control behavior inside that available module.

Return:
- 404 when the module/capability is unavailable to the tenant.
- 403 when the module exists but the actor lacks permission.
- A structured `CapabilityDecision` internally containing allowed, reason, and source.

This gives support staff an explainable answer such as: "Inventory disabled because tenant
entitlement is absent."

## Expand FeatureManifest into ModuleManifest

The manifest should become the single declaration point:

```python
ModuleManifest(
    code="inventory",
    version="1.4.0",
    contract_version=1,
    dependencies=("parties",),
    api_routers=(router,),
    web_routers=(web_router,),
    nav=(...),
    permissions=("inventory.read", "inventory.manage"),
    settings=(...),
    feature_flags=(...),
    audit_actions=(...),
    entity_types=(...),
    health_checks=(...),
    core=False,
)
```

From this manifest, derive: mounted routes; navigation; permission catalog; settings
specifications; feature-flag catalog; audit-action registry; custom-field entity
registrations; dependency graph; admin module descriptions; health/readiness checks.

That removes the present duplication between FEATURE_MODULES, import-linter configuration,
settings side-effect registration, permission strings, navigation, and future plugin
declarations.

## Database model

Keep code declarations authoritative, with the database storing operational state.

Suggested tables:

`module_installations` — module_code, installed_version, manifest_hash, contract_version,
migration_status, health_status, last_error, discovered_at.

`tenant_module_entitlements` — tenant_id, module_code, enabled, source (manual, plan,
contract, trial), starts_at, expires_at, version (optimistic concurrency), changed_by,
changed_at.

`feature_flag_overrides` — flag_code, scope_type (deployment, tenant, actor, cohort),
scope_id, value_json, rollout_percentage, starts_at, expires_at, reason, version.

`module_change_requests` — requested operation, impact snapshot, approval status,
requested_by / approved_by.

Continue using `domain_settings` for module configuration. Plugins should contribute
ordinary SettingSpec declarations rather than creating another configuration store.

## Feature-flag standard

Each flag needs a typed declaration:

```python
FeatureFlagSpec(
    code="inventory.new_receiving_flow",
    value_type=bool,
    default=False,
    allowed_scopes={"deployment", "tenant"},
    owner="inventory",
    description="Use the new receiving workflow",
    expires_on=date(2026, 12, 31),
    operational=False,
)
```

The evaluator should return more than a boolean:

```python
FlagEvaluation(
    value=True,
    source="tenant_override",
    reason="Enabled for pilot tenant",
    rule_id=...,
    evaluated_version=...,
)
```

Required rules:
- Flags cannot grant permissions.
- Every flag has an owner and optional expiry/removal date.
- Emergency kill switches outrank rollout overrides.
- Percentage rollouts use deterministic hashing.
- Changes are audited.
- Stale flags fail CI after their expiry date.
- Every declared flag has a real evaluator consumer.
- Every referenced flag is declared.
- Flag evaluation is cached with explicit invalidation/versioning.
- Experiments may record exposure events; ordinary operational flags should not generate
  high-volume audit noise.

## Admin settings structure

Two administrative planes.

Platform administration: modules and plugin inventory; tenant entitlements; feature flags
and staged rollouts; platform defaults; integrations and credentials;
migration/compatibility status; module health and dependency graph; audit history; pending
approvals.

Tenant administration: effective enabled modules (usually read-only); tenant-configurable
settings grouped by module; tenant-scoped feature flags where permitted; role/permission
assignments; branding and display configuration; integration connections; configuration
change history.

Every setting row should show: effective value; source (tenant, platform, environment, or
built-in default); data type and validation constraints; secret classification; owning
module; whether change is immediate or restart-required; dependencies and affected
features; last changed by/at; reset-to-inherited-default action.

Dangerous changes should use impact preview, confirmation, optimistic locking, and
possibly two-person approval.

## Plugin architecture

Treat built-in features and installed plugins as the same manifest contract.

Use Python package entry points for discovery:

```toml
[project.entry-points."dotmac.modules"]
inventory = "dotmac_inventory.plugin:manifest"
```

At startup:
1. Load built-in manifests.
2. Discover installed entry points.
3. Validate contract versions and unique codes.
4. Build and validate the dependency graph.
5. Verify migration state.
6. Register permissions, settings, flags, audit actions, and entities.
7. Mount deployment-enabled routers.
8. Record the installation inventory and manifest hash.
9. Fail startup for broken core modules; quarantine optional plugins.

Important boundary: in-process Python plugins are fully trusted code. Do not support
arbitrary plugin uploads or pip install from the admin UI. Installation should happen
through CI, image rebuild, vulnerability scanning, and signed artifacts. If third-party
code is untrusted, run it as a separate service behind a versioned API.

The admin UI may enable an already-installed plugin, but should never download and execute
code.

## Plugin lifecycle

Explicit states: discovered → validated → migrated → enabled → disabled → retired
(with failed/quarantined branching off validated/migrated).

Disabling and uninstalling are different:
- Disable: routes remain technically mounted if tenant-specific enablement is supported,
  but capability guards return unavailable and navigation disappears.
- Uninstall: code is absent from the deployment.
- Retire: prevent new use while retaining readable historical data.

Before disable/uninstall, compute impact: dependent modules; tenant entitlements; stored
settings; plugin-owned rows; scheduled jobs; webhooks/integrations; custom-field
registrations; historical records requiring continued rendering.

Never automatically drop plugin data on disable. Require an explicit
archive/export/delete policy.

## Migration contract

Plugin migrations must remain a deploy-time operation, never an admin-button operation.

For internal plugins, the simplest reliable model:
- Plugin migrations are included in the application image.
- One deploy orchestrator validates the migration graph.
- Migrations run before plugin enablement.
- Schema changes follow expand/contract compatibility.
- Old application code must remain compatible through deployment rollback.
- Plugin enablement is blocked when its migration state is behind.

Avoid completely independent Alembic heads unless you build and test a real migration
orchestrator around them.

## Governance tests to add

The starter should fail CI when:
- Two modules declare the same code.
- A dependency is missing or cyclic.
- A route belongs to no manifest.
- A nav entry has no enabled route.
- A permission, flag, setting, audit action, or entity code is undeclared.
- A declaration has no consumer.
- A plugin model lacks migration/RLS coverage.
- A tenant module can be accessed without require_capability.
- A disabled module still appears in navigation or HTMX fragments.
- A stale flag passes its expiry/removal date.
- A plugin's manifest hash changes without a version change.
- A plugin is enabled before migrations or dependencies are ready.

The existing capability-hardening work should be evolved toward this manifest-driven
model rather than building separate registries independently.

## Preferred sequence (the program order)

1. Secure platform-admin authentication.
2. Introduce ModuleManifest and ModuleRegistry.
3. Add permission and audit-action declarations.
4. Add tenant entitlements and require_capability.
5. Add typed feature flags.
6. Build the platform module/flag administration UI.
7. Add trusted entry-point plugin discovery.
8. Add plugin migration, health, retirement, and data-lifecycle contracts.

That gives you a genuine modular platform starter without turning it into a risky runtime
plugin marketplace.
