# dotmac_starter_mt

The consolidated DotMac starter (spec:
`docs/superpowers/specs/2026-07-17-starter-consolidation-design.md`, decision:
`docs/adr/0002-starter-consolidation.md`). Multi-tenant always; a
single-tenant app is simply a deployment with one tenant row.

## Layout

- `app/core/` — config, db, models base, security, deps (route guards),
  middleware, logging, errors, crud, unit_of_work, features registry, audit
  write-side. Core never imports `app/features` (import-linter contract
  "Core must not import features", `make lint-imports`).
- `app/features/<name>/` — self-contained: `models.py`, `schemas.py`,
  `service.py`, `router.py` (JSON API), `web.py` (HTML/HTMX admin-portal
  routes, mounted under `/admin/...` — see "Web portal (admin UI)" below),
  `feature.py` (exports `feature: FeatureManifest`). Features never import
  each other (import-linter contract "Features are independent of each
  other"); cross-feature references use FK strings / UUID columns, never a
  Python import. Seven registered today: `tenants`, `auth`, `parties`,
  `rbac`, `settings` (tenant-scoped settings-as-data admin API —
  spec/seed/router/schemas only; the registry/resolver mechanics it depends
  on live in core, see below), `custom_fields` (definitions CRUD + values on
  a registered entity's `custom_fields` JSONB column — 13 field types,
  zero-migration field creation), `web` (`core=False`, deletable — the
  admin-portal dashboard shell; `DISABLED_FEATURES=web` drops only `GET
  /admin`, every other feature's own `/admin/*` routes and the API stay up).

**Model placement rule:** models queried by core (deps/middleware) live in
core; feature-local models live in the feature. Concretely: `Tenant`,
`TenantDomain`, `Party` (+ subtype tables `PartyPerson`/`PartyOrganization`),
`Role`, `PartyRole`, `AuthSession` live in `app/core/models.py` because
`app.core.deps` (the `require_*` guards) and `app.core.middleware.tenant`
(the resolver) query them directly, and core cannot import features to get
at them. `Party` (`party_type` person|organization) is the fleet-wide
identity source of truth — it replaced the bare `Person` model (spec
amendment 2026-07-17); profile data lives on the subtype tables, which carry
no `tenant_id` of their own and inherit isolation via an `EXISTS`-based RLS
policy joined through the FK to `parties`. `AuditEvent` + `write_audit_event`
live in `app/core/audit.py` for the same cross-cutting reason (every
feature writes audit events). `DomainSetting` (`app/core/settings_models.py`)
and the spec registry/tenant→platform→default resolver
(`app/core/settings_resolver.py`) live in core for the identical reason: the
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
stays local to its feature — e.g. `UserCredential` lives in
`app/features/auth/models.py` because nothing outside `auth` touches it; it
references `parties`/`tenants` via string-form
`ForeignKey`/`ForeignKeyConstraint`, no import needed. This is a deliberate
deviation from "one model per feature package" — see ADR-0002. The full
model-by-model provenance (owner + port source-of-truth) is the table in
`docs/ARCHITECTURE.md` — don't duplicate it here.

## Extension points

Four points let a project built from this template add its own surface
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
  module and call `app.core.settings_resolver.register_specs([...])` at
  import time (see `app/features/settings/spec.py`). A registered spec with
  no reader anywhere under `app/` (outside the settings feature and the
  resolver module itself) fails the no-orphan-settings test — wire a real
  `resolve_value(...)` call before shipping it, or don't register it yet.
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

## Web portal (admin UI)

Every feature that has an admin-facing HTML surface puts it in that
feature's own `web.py` (never in `router.py`, which is the JSON API), mounts
under `/admin/...`, and renders through `app.core.templating.render()` — the
one shared Jinja2 environment (see that module's docstring for the
`brand`/`static_asset_url`/`current_year` globals every template gets for
free). `web.py` is held to the same thin-wrapper rule as `router.py` (no
`db.query`/`db.execute`/`select(` — logic stays in `service.py`) and may
only import `app.core.*` or its OWN feature's modules — a cross-feature
import (e.g. `parties/web.py` importing `rbac.service`) is caught by
`tests/architecture/test_web_conventions.py::test_web_py_imports_only_its_own_feature_and_core`.

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
- **Template escaping / `| safe` rule.** Jinja2 autoescapes by default;
  `| safe` opts a value OUT of escaping and must only be used on a value
  that has already been sanitized in Python, with a `sanitiz*` comment
  within 12 lines of the `| safe` use explaining why it's safe (the one real
  usage today: `templates/admin/settings/branding.html`'s `custom_css`
  preview, sanitized by `app.core.branding.sanitize_branding_css` before
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
  `require_web_auth`, `require_platform` — deliberately NOT a `require_`
  prefix match, since `require_tenant` would wrongly pass) unless it's in
  `MUTATING_ALLOWLIST` (the genuinely pre-auth routes: `POST /auth/register`,
  `POST /auth/login`, `POST /admin/login`, each commented inline with why).
  A per-route non-admin sweep,
  `tests/unit/test_admin_route_sweep.py::test_non_admin_cookie_gets_redirected_not_200_or_500`,
  independently drives every mutating `/admin/*` route with an
  authenticated-but-non-admin cookie and asserts a redirect, not a 200 or a
  500 (a 500 would mean the guard was missing and the request reached real
  business logic).
- **Auth model: cookie + bearer share one seam.** `app.core.deps
  .authenticate_request` is the single token/session/tenant/party-type
  validation function for BOTH the JSON API (bearer `Authorization` header)
  and the portal (`app.core.web_deps.require_web_auth`, which reads the
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

## Hard rules (enforced — test/contract named per rule)

- Routers (`router.py`, `web.py`) never issue direct DB queries (no
  `db.query(`, `db.execute(`, `select(`) — logic lives in `service.py`.
  (`tests/architecture/test_thin_wrappers.py::test_routers_do_not_issue_direct_queries`)
- Every mounted route carries a `require_*` guard dependency (route-level or
  router-level `dependencies=[...]`), or is in the explicit
  `ALLOWLIST` with a comment explaining why it's unauthenticated.
  (`tests/architecture/test_route_guards.py::test_every_route_has_a_guard`)
- Every `app/features/<name>` package on disk is registered in
  `app.features.FEATURE_MODULES` and exports a `feature.py` manifest named
  after its package.
  (`tests/architecture/test_feature_manifests.py`)
- Features never import each other; core never imports features.
  (`pyproject.toml` `[tool.importlinter]` contracts, `make lint-imports`)
- The import-linter "Features are independent of each other" contract's
  `modules` list stays byte-for-byte in sync with `FEATURE_MODULES` — a
  feature registered in one but not the other would silently escape
  `make lint-imports`.
  (`tests/architecture/test_feature_manifests.py::test_importlinter_independence_contract_matches_feature_modules`)
- Feature `service.py` functions never take `payload: Any` — every payload
  parameter is a concrete Pydantic schema.
  (`tests/unit/test_service_typing.py::test_no_any_typed_payloads_in_services`)
- Every registered `SettingSpec` key must have a real reader (a quoted-string
  `resolve_value(...)`-style reference) somewhere under `app/` outside the
  `settings` feature package and `app/core/settings_resolver.py` itself — a
  setting nobody reads is a dead control. The allowlist for known,
  intentionally-not-yet-wired keys is EMPTY as of plan 2b Task 2
  (`ui_branding` was the one entry, now consumed by
  `app.core.branding.load_branding`) and may only shrink, never grow,
  without a task/plan reference.
  (`tests/architecture/test_no_orphan_settings.py`)
- Every tenant-scoped model: `tenant_id UUID NOT NULL REFERENCES tenants(id)`
  + a composite unique on `(tenant_id, ...)` for anything unique-per-tenant,
  and an RLS `ENABLE/FORCE ROW LEVEL SECURITY` + `CREATE POLICY` in the same
  migration that creates the table (the settings table's `domain_settings`
  is the one deliberate exception — `tenant_id` is nullable and it carries a
  split read/write policy pair instead of a single policy; see
  `docs/ARCHITECTURE.md`). Not statically checked — enforced by the
  Postgres RLS integration canaries (`tests/test_cross_tenant_isolation.py`,
  `tests/test_rbac_audit_isolation.py`, `tests/test_auth_tenant_claim.py`,
  `tests/test_party_isolation.py`, `tests/test_settings_isolation.py`,
  `tests/test_custom_fields_isolation.py`, `tests/test_web_auth_isolation.py`,
  `tests/test_admin_portal_e2e.py`), which fail if isolation is
  missing. Run these against real Postgres
  (`make test-db-up && make test-integration`) — SQLite cannot enforce RLS.
- Migrations run as `app_admin` (`MIGRATION_DATABASE_URL`), never on
  container boot. The Dockerfile `CMD` only runs `uvicorn` — no `alembic`
  step — and `scripts/deploy.sh` is the only place migrations run
  (`alembic upgrade heads`, before recreating the app container). CI's
  `docker-build` job health-gates a container booted with a deliberately
  unreachable `DATABASE_URL`, which passes because `/health` is DB-free and
  because the lifespan's feature-seed step (see below) attempts but never
  blocks on the DB: a seed failure is caught, logged, and skipped so
  startup always reaches the point where `/health` can serve.
- New feature: create the package + `feature.py`, register it in
  `app/features/__init__.py` (`FEATURE_MODULES`), add it to the
  import-linter "Features are independent" contract in `pyproject.toml`,
  and write the cross-tenant isolation test **first** (process discipline —
  not mechanically enforced, but every existing feature follows it; see
  `tests/test_cross_tenant_isolation.py` for the pattern).

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

Env-specific values are overridable variables with documented defaults, not
literals buried in code: Make vars use `?=` (see `Makefile`'s
`TEST_DB_PORT ?= 5433` etc.), compose files use `${VAR:-default}`, and
`scripts/deploy.sh` sources `.env` then falls back to `: "${VAR:=default}"`.
When adding a new environment-specific value, add it as an overridable knob
in the same style — don't hardcode ports, hosts, image names, or paths.

## Commands

- `make help` — list every target. `make check` before any commit (ruff
  lint, import-linter, mypy, bandit, `ruff format --check`).
- `make test-unit` (SQLite, fast — `tests/unit` + `tests/architecture`, no
  DB required) / `make test-db-up && make test-integration && make
  test-db-down` (Postgres RLS canaries; `TEST_DB_PORT` overridable if the
  default port is taken, e.g. `TEST_DB_PORT=5437 make test-db-up`).
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

## Testing model

- Unit tests (`tests/unit`, `tests/architecture`): in-memory SQLite, no RLS —
  do not test tenancy correctness there, only logic and static structure.
- Tenancy correctness: Postgres RLS canaries in `tests/` (top-level, not
  under `tests/unit`) — require a real, migrated database
  (`make test-db-up`).
