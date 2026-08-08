# dotmac_starter_mt

**Hard rules live in `AGENTS.md`** (tool-neutral, canonical — this file
indexes them and adds repo-map/portal specifics). Docs hierarchy:
`docs/ARCHITECTURE.md` = as-built truth; `docs/adr/` = decisions + status;
`docs/inventories/` = dated cross-repo as-built characterization (starter,
Sub, ERP, vendor control plane) — facts, not mandates, and explicitly not a
licence to extract shared code (see ADR-0006 § "The extraction rule");
`docs/superpowers/plans|specs/` = non-authoritative intent; `README.md` =
onboarding; `CONTRIBUTING.md` = human dev rules; `docs/SECURITY.md` =
security posture.

The consolidated DotMac starter (spec:
`docs/superpowers/specs/2026-07-17-starter-consolidation-design.md`, decision:
`docs/adr/0002-starter-consolidation.md`). Multi-tenant always; a
single-tenant app is simply a deployment with one tenant row.

ADR-0003 makes this repo the strategic foundation for new SaaS, dedicated,
self-hosted/on-premise, OEM, and single-tenant deployments. Its profile,
provider, entitlement, subscription, billing, metering, and licensing
contracts are an accepted target—not implemented runtime APIs. Follow
`docs/superpowers/plans/2026-07-18-deployment-profiles-commercial-platform.md`
when adding them; do not invent interim competing authorities.

## Layout

This repo is the reference **assembly** (`app/`) plus the **kernel package** it
consumes. The kernel lives at `packages/dotmac-kernel/src/dotmac_kernel/`
(distribution `dotmac-kernel`, import `dotmac_kernel`), installed as an editable
path dependency — the assembly imports `dotmac_kernel.*`, never a copied module.

- `dotmac_kernel` (the kernel package) — config, db, models base, security,
  platform auth, deps (route guards), middleware, logging, errors, crud,
  features registry, module registry (`modules`: the versioned
  `ModuleManifest` + the `ModuleRegistry` that validates unique codes,
  contract compatibility, dependencies and cycles, and derives the
  deterministic startup order `create_app` mounts in), audit write-side,
  templating, settings resolver, branding, identity. The kernel never
  imports `app` (import-linter contract
  "Kernel must not import the assembly", `make lint-imports`) and never
  imports `dotmac_ui` ("Kernel must not import the UI package").
- `dotmac_ui` (the design-system package, `packages/dotmac-ui/src/dotmac_ui/`,
  distribution `dotmac-ui`, import `dotmac_ui`) — semantic design tokens,
  the compiled self-hosted stylesheet, the accessibility contract, and the
  UI contract version (ADR-0006 U1; see "Design system" under "Web portal
  (admin UI)" below). Dependency-free and one-way: `assembly → module →
  dotmac-ui → dotmac-kernel`.
- `app/features/<name>/` — self-contained: `models.py`, `schemas.py`,
  `service.py`, `router.py` (JSON API), `web.py` (HTML/HTMX admin-portal
  routes, mounted under `/admin/...` — see "Web portal (admin UI)" below),
  `feature.py` (exports `feature: FeatureManifest`). Features never import
  each other (import-linter contract "Features are independent of each
  other"); cross-feature references use FK strings / UUID columns, never a
  Python import. Eight registered today: `tenants`, `auth`, `parties`,
  `rbac`, `settings` (tenant-scoped settings-as-data admin API —
  spec/seed/router/schemas only; the registry/resolver mechanics it depends
  on live in core, see below), `custom_fields` (definitions CRUD + values on
  a registered entity's `custom_fields` JSONB column — 13 field types,
  zero-migration field creation), `licensing` (the WS8 reference receiver:
  verifies a vendor-signed licence envelope via `dotmac_kernel.licensing`,
  projects it into local WS2 grants, keeps the durable
  `TenantAppliedLicence` replay record — assembly migration `a002` — and
  returns the version/digest acknowledgement; also imports signed revocation
  lists (`TenantRevocationList`, migration `a003`), which revoke matching
  grants IMMEDIATELY and are fed into every later `verify_licence`, with
  accepted imports required to be a superset of the stored set; trust config
  via `LICENCE_VERIFICATION_KEYS`/`LICENCE_DEPLOYMENT_ID`/
  `LICENCE_REQUIRE_BINDING` env knobs), `web` (`core=False`, deletable — the
  admin-portal dashboard shell; `DISABLED_FEATURES=web` drops only `GET
  /admin`, every other feature's own `/admin/*` routes and the API stay up —
  see "Extension points" below for `WEB_ENABLED`, the different,
  whole-portal switch).

**Model placement rule:** models queried by core (deps/middleware) live in
core; feature-local models live in the feature. Concretely: `Tenant`,
`TenantDomain`, `Party` (+ subtype tables `PartyPerson`/`PartyOrganization`),
`Role`, `PartyRole`, `AuthSession` live in `dotmac_kernel/models.py` because
`dotmac_kernel.deps` (the `require_*` guards) and `dotmac_kernel.middleware.tenant`
(the resolver) query them directly, and core cannot import features to get
at them. `Party` (`party_type` person|organization) is the fleet-wide
identity source of truth — it replaced the bare `Person` model (spec
amendment 2026-07-17); profile data lives on the subtype tables, which carry
no `tenant_id` of their own and inherit isolation via an `EXISTS`-based RLS
policy joined through the FK to `parties`. `AuditEvent` + `write_audit_event`
live in `dotmac_kernel/audit.py` for the same cross-cutting reason (every
feature writes audit events). `DomainSetting` (`dotmac_kernel/settings_models.py`)
and the spec registry/tenant→platform→default resolver
(`dotmac_kernel/settings_resolver.py`) live in core for the identical reason: the
`custom_fields` feature must consume `resolve_value` directly (per-entity
field limit), and features may never import each other — so the mechanics
both `settings` and `custom_fields` need sit in core, while the `settings`
feature package keeps only what nothing else needs (spec *declarations* in
`app/features/settings/spec.py`, seed data, router, schemas).
`CustomFieldDefinition` (field *shape*: type, validation, display) stays
feature-local in `app/features/custom_fields/models.py` — nothing outside
that feature touches it; field *values* live on the entity's own model
(e.g. `Party.custom_fields` JSONB), resolved generically through the
`ENTITY_MODELS` registry (see Extension points below). Everything else
stays local to its feature. `UserCredential` MOVED to `dotmac_kernel/models.py`
(control-plane security Task 2, PORT-DELTA): atomic tenant provisioning
(`tenants` feature) creates the owner credential in the same transaction,
and features never import each other — so it joined the other identity
models under the placement rule above; the `auth` feature keeps all
hashing/verification via `dotmac_kernel.security`. Platform-actor identity
(`PlatformAdmin`/`PlatformSession`) lives in `dotmac_kernel/models_platform.py`
— platform catalog tables (no `tenant_id`, no RLS, revoked from
`app_user`); see ADR-0004. This is a deliberate deviation from "one model
per feature package" — see ADR-0002. The full model-by-model provenance
(owner + port source-of-truth) is the table in `docs/ARCHITECTURE.md` —
don't duplicate it here.

## Extension points

These points let a project built from this template add its own surface
without touching core:

- **Register a feature package.** Add `app/features/<name>/` (with
  `feature.py` exporting `feature: FeatureManifest`), append the dotted
  module path to `FEATURE_MODULES` in `app/features/__init__.py`, and add it
  to the "Features are independent of each other" import-linter contract in
  `pyproject.toml`. `tests/architecture/test_feature_manifests.py` fails the
  build if any of these three drift apart (see contract-sync rule below).
- **Register an entity for custom fields.** Add the entity's model class to
  `ENTITY_MODELS` in `app/features/custom_fields/registry.py`
  (`resolve_entity`). An unregistered `entity_type` fails loudly at
  `CustomFieldDefinition` creation, naming this file as the fix. The
  registered model must have its own `custom_fields` JSONB column (see
  `Party.custom_fields` for the pattern) — `set_values`/`get_values` read
  and write it generically via `db.get(model, entity_id)`.
- **Declare a setting spec.** Add a `SettingSpec` to a feature's own spec
  module and call `dotmac_kernel.settings_resolver.register_specs([...])` at
  import time (see `app/features/settings/spec.py`), and declare the spec's
  DOMAIN on the owning module's manifest (`setting_domains=(...)`) — a write
  to an undeclared domain is rejected, and CI checks both directions
  (declared-with-no-spec, spec-with-no-declaration). `SettingDomain` is an
  open registered string, not an enum, so a product names its own domains
  without a kernel change — ADR-0008. A registered spec with no reader
  anywhere under `app/` (outside the settings feature and the resolver
  module itself) fails the no-orphan-settings test — wire a real
  `resolve_value(...)` call before shipping it, or don't register it yet.
- **Supply encryption keys from a secret store.** By default settings
  encryption keys come from the environment
  (`SETTINGS_ENCRYPTION_KEY`/`_FILE`/`SETTINGS_ENCRYPTION_KEYS`). A deployment
  that keeps keys in a secret store instead implements
  `dotmac_kernel.settings_crypto.KeyProvider` (one method, `load_keys`) and
  calls `install_key_provider(...)` at startup. The kernel ships no provider
  and no store client — the dependency stays in the product. The provider is
  read ONCE, at install, and held in memory: a key is fetched at boot, so the
  store being unreachable an hour later cannot touch the per-request read
  path. Rotation is an explicit `refresh_keys()`, never a TTL. A provider that
  fails raises at install rather than letting the process start with no keys
  and silently degrade every secret to its spec default; there is deliberately
  no degraded-start knob. Key material is never stored in `domain_settings` —
  a key that protects data at rest must not live in the database it protects.
- **Install secret material a product resolved itself.** The kernel never
  fetches a secret while handling a request (ADR-0009), so anything living in
  a secret store is read by the PRODUCT and installed at startup:
  `dotmac_kernel.secret_sources.install_secret_source(...)` for named material
  (`get_secret`/`require_secret` are dict lookups afterwards), or
  `install_key_provider(...)` above for settings encryption keys. Same
  semantics for both: loaded once, explicit `refresh_secrets()`/`refresh_keys()`
  for rotation, a failed refresh keeps the working set, a failing source raises
  rather than starting degraded, and names are logged but never values. A
  setting whose value merely looks like a reference (`bao://...`) is just that
  string — the kernel does not dereference it.
- **Add an admin-portal surface (the capability model — THE surface
  extension point).** A feature's `FeatureManifest` (`dotmac_kernel.features`)
  declares `web_routers` (its `web.py` router, HTML/HTMX) and `nav` (a
  tuple of `NavItem(label, path)`) SEPARATELY from `routers` (its JSON
  API) — these two fields are the ONLY place a feature adds itself to the
  admin portal's sidebar or mounts an `/admin/*` screen; there is no
  parallel hardcoded nav list in a template to keep in sync
  (`templates/components/sidebar.html` renders from the `nav_items` Jinja
  global, itself built from every manifest's `nav` by
  `dotmac_kernel.templating.install_surface_globals`). Two independent on/off
  switches, do not conflate them: `DISABLED_FEATURES=<name>` turns off ONE
  named feature's `routers` AND `web_routers` together (still a per-feature,
  not a per-surface, toggle); `WEB_ENABLED` (env var, default `true`) is the
  whole-portal surface switch — `WEB_ENABLED=false` mounts NO feature's
  `web_routers` at all (zero `/admin` routes, no `/static` mount) while
  every feature's JSON `routers` keeps working unchanged — this is the real
  API-only deployment mode. `tests/architecture/test_feature_manifests.py
  ::test_nav_items_paths_exist_in_web_routers` fails the build if a
  manifest's `NavItem.path` doesn't resolve to a route actually mounted in
  that same manifest's `web_routers` (a dead/stale sidebar link — this is
  also how a disabled feature's nav entry is kept from linking to a 404;
  see `templates/admin/parties/detail.html`'s
  `{% if 'custom_fields' in enabled_features %}` guard for the matching
  optional-slot pattern on an embedded fragment, not just a nav link).
- **Compose a cross-feature admin-UI fragment (values-panel pattern).** A
  feature never imports another feature's Python — but its web page can
  still show another feature's data, via an htmx-loaded fragment the OWNING
  feature serves at its own URL. `templates/admin/parties/detail.html`
  wants a party's custom-field values; `parties` cannot import
  `custom_fields`, so the party detail template instead does
  `hx-get="/admin/custom-fields/party/{{ party.id }}/values-panel"
  hx-trigger="load"` — zero Python import, composition happens in the
  browser. `custom_fields/web.py` owns both routes (`GET`/`POST
  .../values-panel`) and the partial it renders
  (`templates/admin/custom_fields/_values_panel.html`): the feature that
  renders a partial owns that partial. Follow this pattern — an
  htmx-fetched URL, not an import — any time one feature's admin page needs
  another feature's UI.

## Planned deployment composition (ADR-0003)

These are accepted design constraints for future profile/control-plane work;
they are not claims that the current runtime already enforces them:

- Compose deployment types from module manifests and provider interfaces. Do
  not scatter `if deployment_mode == ...`, plan-name, payment-state, or raw
  license checks through feature code.
- A single-tenant deployment keeps `Tenant`, request tenant context, composite
  tenant constraints, and RLS. It is a topology, not a second schema or code
  path.
- Keep actor permission, tenant entitlement, rollout flag, runtime setting,
  and quota as separate decisions. Entitlements are common; subscriptions,
  billing, metering, and signed licensing are independently optional.
- Feature code consumes explainable local entitlement/quota decisions. A
  request-time access check never calls a payment provider or depends on
  network license validation.
- Treat Python plugins as trusted in-process code. Install and verify them in
  the build/deploy supply chain; the admin UI may enable only already-installed,
  migrated, dependency-complete, healthy code and may never run `pip install`.
- Run plugin migrations at deploy time. Disabling a module preserves its data;
  retirement requires an explicit impact preview and archive/export/delete
  policy.
- Treat tenant domains as lifecycle resources, not generic settings. Normalize
  and prove DNS ownership before activation; reconcile desired bindings through
  an ingress/TLS provider; never issue a certificate for an arbitrary first
  request `Host`. Nginx is one provider, not an architecture dependency.
- Keep the exact platform host, tenant base domain(s), and custom-domain target
  distinct in the planned profile contract. Unknown/unverified hosts fail
  closed, and forwarded headers are trusted only from configured proxies.
- Products compose a pinned versioned kernel, versioned modules, product-owned
  modules/providers/brand/policies, and a deployment profile. Do not copy,
  monkey-patch, or fork kernel files for product behavior; add or improve a
  declared extension point and propagate fixes through tested release updates.
- Existing products adopt through assemblies, adapters, contract/shadow tests,
  expand/contract migrations, reconciliation, and one-writer cutovers—never a
  big-bang rewrite. ERP and ISP remain separate data planes; an ISP operator is a
  tenant, while its subscribers remain product-domain parties/customers.
- Keep business rules in services. JSON routers, Jinja/HTMX web routes, workers,
  CLI, and external frontends are adapters. API-first does not mean deleting the
  built-in `web` module; API-only is one profile, and both surfaces must reach the
  same authorization and lifecycle decisions.
- Model tenant, subscription, entitlement, provider-job, domain, and license
  lifecycles separately. Cross-module transitions require idempotency,
  transaction owner, outbox/inbox, audit, retry/compensation, and repair; payment
  failure or module disablement never implicitly deletes tenant data.
- Locale/language, timezone, currency, legal entity, tax jurisdiction, and data
  residency are independent. Use stable language-neutral codes/message IDs,
  exact Money (never float), immutable FX/price/rating/tax-policy snapshots, and
  declared provider/policy versions—never country or plan-name branches in
  features.

## Web portal (admin UI)

Every feature that has an admin-facing HTML surface puts it in that
feature's own `web.py` (never in `router.py`, which is the JSON API), mounts
under `/admin/...`, and renders through `dotmac_kernel.templating.render()` — the
one shared Jinja2 environment (see that module's docstring for the
`brand`/`static_asset_url`/`current_year` globals every template gets for
free). `web.py` is held to the same thin-wrapper rule as `router.py` (no
`db.query`/`db.execute`/`select(` — logic stays in `service.py`) and may
only import `dotmac_kernel.*` or its OWN feature's modules — a cross-feature
import (e.g. `parties/web.py` importing `rbac.service`) is caught by
`tests/architecture/test_web_conventions.py::test_web_py_imports_only_its_own_feature_and_core`.

- **Design system (`dotmac-ui`).** `packages/dotmac-ui/` (distribution
  `dotmac-ui`, import `dotmac_ui`) owns the semantic design tokens and the
  compiled, self-hosted stylesheet every page loads (ADR-0006 U1; public
  surface + stability policy in that package's `COMPATIBILITY.md`). It is
  DEPENDENCY-FREE — no kernel, no ORM, no web framework, no Jinja — which is
  what lets a product adopt the design system without adopting anything
  else. The whole integration is two lines in `app/assembly.py`
  (`packaged_static_dirs` + `stylesheets`); the kernel never imports it
  (dependency direction is `assembly → module → dotmac-ui →
  dotmac-kernel`) and fills those two anonymous spec slots instead.
  Consumers run NO Tailwind/PostCSS/npm step and need no particular
  Tailwind major (ADR-0006 D3). Author against `var(--dmui-<role>)` — the
  190 role-named tokens — not raw hex; the compiled asset is COMMITTED and
  regenerated by `make ui-build` (never hand-edited; `make ui-check` in
  `make check` fails on drift). No `.dmui-*` component class exists yet, on
  purpose: U1 is the token foundation, and ADR-0006 § 5 forbids harvesting
  components that merely look alike.
- **Fragment composition, not imports** — see Extension points above
  (values-panel pattern) for how one feature's admin page shows another
  feature's data without a Python import.
- **CSRF header-bridge contract.** `CSRFMiddleware` validates the
  `X-CSRF-Token` HEADER against the `csrf_token` COOKIE (double-submit;
  the cookie is deliberately not `HttpOnly` so JS can read it).
  `static/js/csrf.js` copies the cookie onto that header for every htmx
  request (`htmx:configRequest`) and every `fetch()` call (monkey-patched).
  A plain `<form method="post">` has no hook to attach a custom header, so
  **every mutating form/link MUST use `hx-post`/`hx-put`/`hx-delete`**, never
  bare `method="post"` — enforced by
  `tests/architecture/test_web_conventions.py::test_no_template_uses_a_plain_method_post_form`.
- **Session-mutating routes are POST, CSRF-bridged.** A GET must never
  mutate session/auth state — a bare `<a href="/admin/logout">` was exactly
  this mistake (F7): a CSRF-exempt safe method that a third-party page could
  trigger just by loading it (`<img src=...>`), forcing a victim's logout.
  `POST /admin/logout` (`app.features.auth.web`) fixed it by putting logout
  back under the CSRF header-bridge above, same as every other mutation —
  there is no separate "logout is special" exemption.
- **Template escaping / `| safe` rule.** Jinja2 autoescapes by default;
  `| safe` opts a value OUT of escaping and must only be used on a value
  that has already been sanitized in Python, with a `sanitiz*` comment
  within 12 lines of the `| safe` use explaining why it's safe (the one real
  usage today: `templates/admin/settings/branding.html`'s `custom_css`
  preview, sanitized by `dotmac_kernel.branding.sanitize_branding_css` before
  `load_branding` ever returns it). Enforced by
  `tests/architecture/test_web_conventions.py::test_safe_filter_only_used_with_a_sanitize_comment_nearby`.
  Every `templates/admin/**/*.html` + `templates/auth/*.html` file must also
  either `{% extends %}` a layout or be `_`-prefixed (a fragment) —
  `test_every_admin_or_auth_template_extends_a_layout_or_is_a_fragment`.
- **Tiered guard rule.** `tests/architecture/test_route_guards.py`'s plain
  `test_every_route_has_a_guard` accepts ANY `require_*`-prefixed
  dependency, including `require_tenant` alone — not enough for a mutating
  route, which needs an actual auth-tier guard. A second, stricter test,
  `test_mutating_routes_require_an_auth_tier_guard`, requires every
  POST/PUT/PATCH/DELETE route to carry a guard from the hand-built
  `AUTH_GUARD_NAMES` set (`require_user_auth`, `require_role`,
  `require_web_auth`, `require_platform_admin` — deliberately NOT a
  `require_` prefix match, since `require_tenant` would wrongly pass) unless
  it's in `MUTATING_ALLOWLIST` (the genuinely pre-auth routes:
  `POST /auth/register`, `POST /auth/login`, `POST /admin/login`,
  `POST /platform/auth/login`, each commented inline with why).
  A per-route non-admin sweep,
  `tests/unit/test_admin_route_sweep.py::test_non_admin_cookie_gets_redirected_not_200_or_500`,
  independently drives every mutating `/admin/*` route with an
  authenticated-but-non-admin cookie and asserts a redirect, not a 200 or a
  500 (a 500 would mean the guard was missing and the request reached real
  business logic).
- **Auth model: cookie + bearer share one seam.** `dotmac_kernel.deps
  .authenticate_request` is the single token/session/tenant/party-type
  validation function for BOTH the JSON API (bearer `Authorization` header)
  and the portal (`dotmac_kernel.web_deps.require_web_auth`, which reads the
  `access_token` cookie, calls `authenticate_request`, then additionally
  requires the `"admin"` role — every portal page is admin-only until phase
  phase 3 adds finer-grained portal roles). Any auth-tightening fix (token
  expiry, tenant-claim check, revocation) lands once, in
  `authenticate_request`, and both surfaces get it — never re-implement
  token validation in `web_deps.py`.
- **Governance scope disclosure.** The web-conventions checks above and the
  non-admin sweep are scoped to `templates/{admin,auth}` and the `/admin`
  path prefix. A future non-admin portal surface (e.g. a self-service party
  view) escapes all of them until their globs/prefixes are extended — do
  that in the same task that adds such a surface (tracked in
  `docs/superpowers/phase2-backlog.md`).
- **Display settings (tenant timezone + date/datetime formats).** A
  `display` `SettingDomain` (three specs: `timezone`, `date_format`,
  `datetime_format`) auto-appears in `/admin/settings` like any other
  registered spec — no dedicated screen. `dotmac_kernel.display.get_request_display`
  resolves it once per request and memoizes on `request.state.display`,
  warmed in `require_web_auth` — the exact same per-request seam shape as
  `request.state.branding` (see "Branding pipeline" in
  `docs/ARCHITECTURE.md`). The JSON API is untouched: responses stay
  ISO-8601 UTC always: display formatting is a web-portal presentation
  concern only. See `docs/ARCHITECTURE.md`'s "Display settings" subsection
  for the write-loud/read-degrade validator split and the filter fallback
  invariant.

## Hard rules — canonical list lives in `AGENTS.md`

**`AGENTS.md` is the single source of truth for the hard rules** (each with
its enforcing test/contract). This section is only an index — adapters
point, never duplicate. If a rule here and `AGENTS.md` ever disagree,
`AGENTS.md` wins; fix the drift.

1. Adapters (`router.py`/`web.py`) never issue direct DB queries — logic in
   `service.py`. The filename convention is what makes this enforceable
   (`test_thin_wrappers.py`; ADR-0010, fleet-wide).
2. Templates render `*_at` timestamps only via `local_datetime`/`local_date`
   filters (`test_web_conventions.py`).
3. Every route carries a `require_*` guard or a commented `ALLOWLIST` entry;
   mutating routes need an `AUTH_GUARD_NAMES`-tier guard
   (`test_route_guards.py`).
4. Every feature package is registered and exports a matching manifest
   (`test_feature_manifests.py`).
5. Features never import each other; core never imports features
   (import-linter, `make lint-imports`).
6. Import-linter independence contract stays in byte-for-byte sync with
   `FEATURE_MODULES` (`test_feature_manifests.py`).
7. No `payload: Any` in feature services (`test_service_typing.py`).
8. `dotmac_kernel/db.py` is the one transaction authority
   (`test_session_authority.py`; ARCHITECTURE.md § "Transaction authority").
9. Feature services never call `db.rollback()` — use `conflict_savepoint`,
   mutation INSIDE the `with` block (`test_no_feature_rollback.py`;
   ARCHITECTURE.md § "Conflict handling" for the full F3 rationale).
10. Every registered `SettingSpec` has a real reader; the unwired allowlist
    is empty and only shrinks (`test_no_orphan_settings.py`).
11. Tenant-scoped tables: `tenant_id NOT NULL` + composite uniques + RLS in
    the same migration (`domain_settings` is the documented exception;
    platform catalog tables get grants-not-RLS) — enforced on Postgres by
    `tests/test_rls_catalog.py` + the per-feature isolation canaries.
12. Manifest declarations (`permissions`, `capabilities`, `audit_actions`,
    `feature_flags`, `setting_domains`) have ONE owning module, are only
    referenced when declared (`require_permission` fails the boot,
    `write_audit_event` fails the write), and every declared code has a real
    consumer (`test_manifest_declarations.py`). A new vocabulary is a
    declaration registry, never an enum — ADR-0008, a FLEET-WIDE standard.
13. Migrations run as `app_admin`, never on container boot;
    `scripts/deploy.sh` is the only production migration path.
14. Each stateful module has one immutable `mod_<short_code>` schema and one
    registered migration lineage; `public` is reserved for the kernel and host
    assembly. Module SQL is fully schema-qualified, and the composed static and
    live-catalog gates enforce revision, namespace, and table ownership
    (`test_namespaces.py`, `test_migration_gate.py`,
    `test_live_catalog_contract.py`, `test_module_schema_catalog.py`).
15. Cross-repository engineering governance is pinned by exact commit and the
    product workflow must execute that same accepted revision
    (`.dotmac/standards-profile.json`, `engineering-standards.yml`).
16. `dotmac_ui` is consumed through its published surface only; it imports no
    kernel/assembly/ORM/web-framework and has zero runtime deps; tokens are
    role-named, never value-named; no undeclared `.dmui-*` class ships
    (`test_ui_public_surface.py` + two import-linter contracts).
17. `dotmac-ui`'s compiled assets are committed, never hand-edited, and match
    their token source; the stylesheet stays self-contained and
    preprocessor-free (`make ui-check`; `test_dotmac_ui_tokens.py`).
18. A secret is HELD, never dereferenced — nothing on the settings resolution
    path reaches a network, and a value that cannot be held is not a setting
    (ADR-0009; `test_secret_sources_no_network.py`, `test_secrets_are_held.py`).

Process: a new feature starts with its package, manifest, registry entry,
import-linter contract, and cross-tenant isolation test.

## SOT-complete criteria

The architecture's definition of done (five criteria — every mutable
resource has one named owner, routes/tasks only validate-authorize-delegate,
every projection has provenance + drift detection + repair, external
systems are transports or contracted authorities, no dangling legacy
writers) is defined once, in
`docs/superpowers/specs/2026-07-17-starter-consolidation-design.md` (§
"Model source-of-truth and the Party identity model") — not duplicated here.
`docs/ARCHITECTURE.md`'s provenance + ownership table is criterion 1's
concrete evidence; open gaps against all five criteria are tracked in
`docs/superpowers/phase2-backlog.md`.

## User rule: everything by config, no hardcoding

Canonical statement in `AGENTS.md` § "Everything by config". Short form:
every env-specific value is an overridable knob with a documented default
(`Settings`/`.env.example`, Make `?=`, compose `${VAR:-default}`,
deploy-script `: "${VAR:=default}"`); prod-unsafe defaults go in
`validate_settings`'s prod-fatal list. Never hardcode ports, hosts, image
names, or paths.

## Commands

- Validation gates (`make check`, `make test-unit`, `make test-db-up &&
  make test-integration`): canonical list in `AGENTS.md` § "Validation
  before any commit". `make help` lists every target.
- `make dev` — run the dev server. `make css-build` (`npm install && npm run
  css:build`) compiles `static/css/src/main.css` (Tailwind v4, CSS-first —
  `@theme`/`@source`/`@custom-variant`, no `tailwind.config.js`) into
  `static/css/main.css`; run it at least once before `make dev`, since
  templates reference the compiled file and it's gitignored (build
  artifact). `make css-watch` rebuilds on save while iterating. Both are
  thin wrappers over `package.json`'s `npm run css:build`/`css:watch`; the
  Dockerfile's `css-builder` stage runs the same `npm ci && npm run
  css:build` to produce the image's static assets (`npm ci`, not `install`
  — fails loudly on lockfile drift instead of silently rewriting it).
  `make docker-build` / `make docker-dev` — build/run the container
  locally. `make migrate` / `make migrate-new` — Alembic. `make deploy
  TAG=...` — production deploy via `scripts/deploy.sh`.
- `make ui-build` / `make ui-check` — the design system's assets. Note the
  deliberate contrast with `css-build` above: NO npm, pure Python,
  deterministic, and the output is COMMITTED rather than gitignored, because
  it is `dotmac-ui`'s published contract (a reviewer sees a token change as a
  CSS diff; an air-gapped consumer gets working assets from a checkout).
  `ui-check` is wired into `make check` and fails if the committed assets
  drift from `packages/dotmac-ui/src/dotmac_ui/tokens.py`.

## Testing model

- Unit tests (`tests/unit`, `tests/architecture`): in-memory SQLite, no RLS —
  do not test tenancy correctness there, only logic and static structure.
- Tenancy correctness: Postgres RLS canaries in `tests/` (top-level, not
  under `tests/unit`) — require a real, migrated database
  (`make test-db-up`).
