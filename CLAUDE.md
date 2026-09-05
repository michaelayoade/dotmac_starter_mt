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
- `dotmac_application_directory` (`packages/dotmac-application-directory/`,
  distribution `dotmac-application-directory`) — a tenant's
  connected-application portfolio, and the PERMANENT owner of the
  `ApplicationDescriptor` contract (ADR-0021 §4 — not module code awaiting
  kernel promotion; only the generic signed-envelope mechanism is a promotion
  candidate). **Built and tested here, composed elsewhere:** `app/assembly.py`
  does not list it and `alembic.ini` does not carry its `ad` lineage, because
  the starter is a target application rather than a workspace and composing it
  would create `mod_appdir` in every starter deployment. Its consumer is the
  `dotmac_workspace` assembly (a separate repository). The one rule to know:
  **directory visibility is not authorization** — a binding is inventory,
  `ACTIVE` means launchable rather than permitted, the table may never hold a
  person/role/grant column (`test_the_directory_holds_no_authorization_column`),
  and the target application stays the only writer of its own effective role
  grants. Access allocation is `dotmac-application-access`, deferred by
  ADR-0021 §5 until the kernel has a generic signed-document mechanism.
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
`Role`, `PartyRoleGrant`, `AuthSession` live in `dotmac_kernel/models.py` because
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
  (`packaged_static_dirs` + `stylesheets`); the kernel never imports it. The
  assembly independently composes modules, the kernel and `dotmac-ui`, while
  `dotmac-ui` imports none of them.
  Consumers run NO Tailwind/PostCSS/npm step and need no particular
  Tailwind major (ADR-0006 D3). Author against `var(--dmui-<role>)` — the
  192 role-named tokens — not raw hex; the compiled asset is COMMITTED and
  regenerated by `make ui-build` (never hand-edited; `make ui-check` in
  `make check` fails on drift). The package also ships the Jinja component
  library as INERT package data — `dotmac_ui.template_dir()` on the assembly's
  `packaged_template_dirs`, templates addressed `dotmac_ui/components/*.html`,
  and `dotmac_ui` still importing no Jinja. Two components today —
  `empty_state` (reuse-proven; ERP and Sub both pin it) and `map_frame`
  (`audit-complete`, zero adopters, first published in `0.1.0a8`); every other
  `.dmui-*` name stays reserved, because ADR-0006 § 5 forbids harvesting
  components that merely look alike. The set is READ from
  `dotmac_ui.COMPONENTS`, never enumerated by hand: the release lane's
  wheel-inspection and installed-artifact proofs derive from it
  (`scripts/verify_ui_release_artifact.py`,
  `tests/architecture/test_ui_release_contract.py`), because `map_frame`
  shipped to `main` while the hand-written release smoke still exercised only
  `empty_state`, and `0.1.0a7` reached the registry without it.
- **Fragment composition, not imports** — see Extension points above
  (values-panel pattern) for how one feature's admin page shows another
  feature's data without a Python import.
- **CSRF transport contract.** Every composed browser route carries the explicit
  `require_csrf` dependency. It validates a signed, expiring, session-bound
  token against the CSRF cookie; the proof may arrive through the
  `X-CSRF-Token` header or a hidden `csrf_token` form field.
  `static/js/csrf.js` copies the cookie onto that header for every htmx
  request (`htmx:configRequest`) and every `fetch()` call (monkey-patched).
  A native `<form method="post">` is supported only with the hidden field;
  htmx-only controls use `hx-post`/`hx-put`/`hx-delete` and the header bridge.
  Enforced by `test_native_post_forms_carry_hidden_csrf_proof` and the composed
  route dependency canary.
- **Session-mutating routes are POST, CSRF-bridged.** A GET must never
  mutate session/auth state — a bare `<a href="/admin/logout">` was exactly
  this mistake (F7): a CSRF-exempt safe method that a third-party page could
  trigger just by loading it (`<img src=...>`), forcing a victim's logout.
  `POST /admin/logout` (`app.features.auth.web`) fixed it by putting logout
  back under the CSRF contract above, same as every other mutation —
  there is no separate "logout is special" exemption.
- **Template escaping / `| safe` rule.** Jinja2 autoescapes by default;
  `| safe` opts a value OUT of escaping and must only be used on a value
  that has already been sanitized in Python, with a `sanitiz*` comment
  within 12 lines of the `| safe` use explaining why it's safe. There are
  **ZERO** usages today: the only one was `branding.html`'s `custom_css`
  preview, retired with tenant-supplied CSS (ADR-0006 D8). The guard stays —
  it is about the NEXT `| safe`, not the last one — backed by
  `test_the_safe_filter_guard_still_bites`, since a check over an empty set
  passes for the wrong reason. Enforced by
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

**`AGENTS.md` is the single source of truth for the hard rules**, and its
numbering is authoritative — this index has drifted before and the entries
below are summaries, not the rule text. (each with
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
8. One transaction authority, in two files: `dotmac_kernel/session_runtime.py`
   holds the instantiable `DatabaseRuntime`, `dotmac_kernel/db.py` is the
   reference assembly's single instance of it. A product supplies its own
   configuration, credentials and tenant identity by constructing its own —
   never by growing a third session factory
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
    live-catalog gates enforce revision, namespace, and table ownership.
    Cross-lineage ordering is LOGICAL: a module declares the database effects
    it needs (`requires`), the supplier declares `provides`, and the ASSEMBLY
    binds effect→revision (`app/migration_bindings.py`) — a module never names
    a foreign revision, and a binding is proven against the live catalog, not
    trusted (ADR-0006 D1 amendment; `test_namespaces.py`,
    `test_migration_gate.py`, `test_prerequisites.py`,
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
19. Settings resolution reads rows and defaults, never the environment;
    `env_var` seeds a row at startup and nothing more (ADR-0011;
    `test_settings_resolution_ignores_env.py`,
    `test_settings_env_is_bootstrap_only.py`).
20. A setting declares whether it inherits; `inherits=False` for values that
    identify something owned by one scope, so no less-specific row can answer
    (ADR-0012; `test_setting_inherits.py`).
21. At-most-once execution has ONE owner (`dotmac_kernel.idempotency`);
    `messaging.process_once` is an adapter over it. Nothing is reserved before
    the effect, the fingerprint is its own column, and retention is the
    product's policy (ADR-0014; `test_idempotency.py`).
22. Shared capabilities are extracted PRODUCT-FIRST: inventory ERP/Sub before
    adding kernel behaviour, port the qualifying production implementation and
    its parity tests, and record owner/contract/consumers in the
    distribution's `EXTRACTION.toml`. Copying is a one-time extraction, never
    a permanent fork or a second writer (ADR-0006 amendment;
    `test_product_first_extraction.py`). **A pin is installation, not
    adoption** (2026-08-29): an adoption state needs a row proving COMPOSITION
    or CUTOVER — an `adopted` assertion at an immutable commit, or a
    `live_observation` naming the capability in the consumer's running system.
    `pinned_at`/`contract_binding`/`workflow_run`/`deploy_run`/`image_digest`
    never suffice; an `adopted` row under a non-adoption status fails the same
    way backwards; a moving-ref `locator` is refused; the remaining pin-only
    scopes are the two-directional `PIN_ONLY_ADOPTION_DEBT` backlog
    (`adoption_evidence.py`, `test_product_first_extraction.py`).
23. A guard exemption states an ENFORCEABLE premise, or the region is
    unmonitored rather than exempt. Guards enumerate entry-point families
    (tasks, scripts, CLI, workers, cron), not one directory; an existing
    backlog is a two-directional ratchet that fails when the count rises OR
    falls without being lowered; "grandfathered" stays distinct from
    "reviewed and correct"; and the detector carries a sensitivity proof
    (ADR-0018). — `AGENTS.md` numbers this **25**; the entries above have
    drifted from it and `AGENTS.md` wins.
24. A dual-plane module has ONE behaviour and TWO declared persistence planes:
    `tables` (tenant — `tenant_id NOT NULL`, FORCEd RLS) and `platform_tables`
    (control plane — no tenant column, no RLS, REVOKEd from the tenant app
    role across every table and column privilege, which IS the isolation there;
    reachable by the online platform role through schema `USAGE` plus row DML).
    Declared, never inferred from a missing `tenant_id`; no FK crosses the
    planes; nullable `tenant_id`, sentinel tenants and polymorphic scope columns
    are refused by the gate (ADR-0023; `AGENTS.md` rule **27**).
25. Applications are independent and compose only by synchronizing data through
    versioned APIs/webhooks. Each app owns its database and decisions; inbound
    adapters write typed observations, not authoritative lifecycle fields, and
    local reconcilers own projections/commands. Modules are independently
    released but installed locally per app, never shared persistence; shared
    behavior has no product/provider switches (ADR-0024; `AGENTS.md` rule
    **28**).
26. A repository-local claim is derived from repository-local facts; a release,
    registry or production-adoption claim requires an authoritative external
    oracle carrying immutable coordinates — `release_run`, `peeled_tag` (the
    PEELED commit), `deployment_run` or `adoption_evidence`. A version present
    in `pyproject.toml` or on `main` is not evidence it is published or
    pinnable. Automated only where a machine-readable contract already declares
    an oracle; stated review discipline elsewhere, never an implied guard
    (Governance ADR 0013, accepted 2026-08-22; `AGENTS.md` rule **30**).
27. A PUBLISHED connector version's manifest digest is frozen — an installation
    adopts by digest, so editing a tagged version's manifest makes one version
    name two contracts and every pin against the old digest unidentifiable. The
    repair is a NEW version keeping the exact published manifest in
    `historical_manifests`; two manifests sharing one version STRING is the
    worse shape, not the safe one, because `accepts_manifest_digest` accepts
    both. `docs/inventories/released-manifest-digests.json` records each
    published tag's peeled commit and digest; `make manifest-digest-check`
    compares it with the tree offline and tag-free, and the architecture test
    re-derives every digest from the tag itself
    (`AGENTS.md` rule **34**; `tests/architecture/test_released_manifest_digests.py`).
28. A caller that cannot deploy ATOMICALLY with its destination binds to a
    pinned published contract, and the binding must be able to fail — not a
    blanket rule for every caller, only for independently released or
    asynchronous ones. Non-vacuity is the half that fails in practice: exercise
    the real caller shape, check the contract's identity rather than its
    presence, make a sender prove a receiver, and compare vocabulary across the
    wire. An unchecked caller path is an unmonitored region, never "covered"
    (`AGENTS.md` rule **37**; ADR-0024 amendment 2026-08-26 § 13; enforcement
    `none yet`).
29. A client that persists a REFRESHABLE BEARER CREDENTIAL tears down
    atomically — an atomic credential record, generation fencing with a durable
    half compared on cold start, one wipe coordinator with no subset clears, and
    transport failure is not revocation. Scoped to credential-holding clients,
    NOT to ordinary server/browser cookie sessions
    (`AGENTS.md` rule **38**; ADR-0067, whose mobile expression is ADR-0065
    §§ 3, 7, 8; enforcement `none yet`).
30. A signed release pipeline verifies the PRODUCED artifact's application
    identity and its actual signing certificate, never secret or file
    existence — and a step is renamed if it does not test the property it is
    named for (`AGENTS.md` rule **39**; ADR-0018 amendment 2026-08-26;
    enforcement `none yet`).

31. Deployment is a stateless `universal-facility`
    (`dotmac-deployment-foundation`), a product declares one
    `deploy/product.toml`, every other asset is rendered, and `render --check`
    is a byte comparison. Zero runtime dependencies; no product branch in the
    shared facility. Kernel owns in-process contracts, the foundation owns one
    release on one host, `dotmac-deployment-control` owns fleet intent, the
    assembly owns declarative input. — `AGENTS.md` rule **41**; ADR-0070.

32. A human credential lifecycle has ONE owner
    (`dotmac_kernel.credential_lifecycle`): typed verdicts rather than a
    boolean, provisioning that cannot be handed material and cannot return it,
    recovery by durable intent, and cohort force reset as product security
    authority with a locally-owned typed plan digest. Direct
    `hash_password`/`verify_password`/`password_needs_rehash` calls are frozen
    debt, ratcheted two-directionally across every Python entry-point family. —
    `AGENTS.md` rule **42**; ADR-0006 amendment 2026-08-30.

32. A displaced deployment executor retires on PROVED evidence, never on
    adoption. EIGHT entry-point families (workflow, script, cron, systemd unit,
    SSH credential, webhook, manual runbook, and `runtime_reactivation` — the
    supervisor that can return a displaced executor after reboot with nobody
    invoking anything, so a retirement owes "it cannot come back" and not just
    "the artifact is gone"), absence is never a disposition,
    `active_executor` has no edge to `retired`, two-directional ratchets per
    family AND per disposition, and a removal receipt naming two distinct
    successful controller cycles, every removal class including the credential,
    the zero-surface guard's sensitivity proof and a PROVED recovery verdict.
    `DisplacementWindow.v1` proves the negative half by ATTRIBUTION rather than
    absence: every runtime change linked to a controller receipt or a typed
    same-image cause, zero unattributed, a quiet window refused as an event
    source, and `cannot_establish` forcing UNMONITORED.
    `SshCredentialConstraintV1` characterises every SSH key rather than counting
    it, and a RETAINED rollback key must be source-restricted,
    forced-command-only and incapable of an interactive shell — because this
    model retains the legacy executor's bytes, so a rollback credential outlives
    every retirement. That last clause is a CONJUNCTION (`restrict`, a
    forced command, no pty) with each its own named refusal, planted separately;
    and the `host_observed` evidence coordinate is one of the properties, so a
    coordinate that is absent, points at nothing, carries no moment, or names
    another host does not read as satisfaction. Compose sanction is ENTRY-POINT IDENTITY resolved from
    installed distribution metadata — never a path, filename or declared
    premise — with the unsanctioned set ratcheted to empty and an unresolvable
    distribution reported UNMONITORED. `ADOPTION_TARGETS` names repositories,
    never hosts, and a production host retaining a rollback credential must have
    one. Products own their receipts; there is no registry here. —
    `AGENTS.md` rule **45** (amended 2026-08-31 twice, 2026-09-01); ADR-0072;
    `docs/inventories/executor-retirement.md`.

33. The window between a candidate BUILD and its PUBLICATION is guarded, and a
    build the tree does not record IS the defect. A LIVE CANDIDATE is a
    successful candidate-lane run for the declared version that produced the
    `<facility>-candidate` artifact — not "a receipt in the tree", because
    presupposing the record is what let `0.4.0a1` drift through four green
    guards. "Was it built?" is a claim about the build system, so the oracle is
    the Actions API (rule 30's immutable coordinates: run id, artifact id). It
    compares TREE OBJECTS, runs in `ci.yml`'s `candidate-window` job on every PR
    and push, exits 0/1/2 with an unavailable oracle as 2, and REFUSES without
    ever choosing the successor version. —
    `AGENTS.md` rule **50**; `scripts/candidate_source_binding.py --window`;
    `tests/architecture/candidate_window_baseline.json`.

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
