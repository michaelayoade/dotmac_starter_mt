# Changelog

## Unreleased

### Added

- Runtime brand projection at `GET /branding/theme.css`: the assembly resolves
  tenant branding through the kernel, generates complete brand/accent ramps and
  their semantic role channels with `dotmac-ui`, and links the result after
  package defaults. The public pre-auth route remains tenant-guarded, sends
  `Cache-Control: private, no-store`, and falls back to the generic package
  palette without logging brand inputs.
- A stateless `presentation` feature owns that assembly adapter explicitly;
  Template Studio remains limited to notification authoring, versioning,
  publication, preview and safe rendering.

## 0.9.0 — 2026-07-30

Kernel-boundary program: this repo is now the reference **assembly** consuming a
genuinely publishable `dotmac-kernel` package (extracted from `app/core`), with
the `0.1.0a1` kernel alpha prepared for publication to PyPI via OIDC trusted
publishing.

### Kernel extraction (consumes `dotmac-kernel`, no copied modules)
- `packages/dotmac-kernel/` — the installable kernel (distribution
  `dotmac-kernel`, import `dotmac_kernel`), an editable path dependency; the
  assembly imports `dotmac_kernel.*` and never a copied core module (import-linter
  "Kernel must not import the assembly").
- **Public surface sealed** — `SUPPORTED_MODULES`/`INTERNAL_MODULES`, per-module
  `__all__`, a `COMPATIBILITY.md` SemVer policy, and an AST governance test that
  blocks the assembly from importing private names (K2).
- **App composition** — `ProductAssemblySpec` + `create_app(spec)`; `app/main.py`
  is now a thin spec-and-call (K3A). Byte-identical route inventory preserved.
- **Provisioning provider contract** — `dotmac_kernel.providers.provisioning`
  (typed Protocol + result/error hierarchy), pulled into the alpha per ruling C6
  (K3B).
- **Testing kit** — `dotmac_kernel.testing` (harness + fakes +
  `FakeProvisioningProvider`/contract), supported public API; the old hand-built
  unit harness in `tests/unit/conftest.py` deleted in favor of it (K5).
- **Migration lineage split** — kernel base (`0001`–`0007`) + an independent
  assembly lineage (`a001`, `depends_on` `0007`), idempotent-adoptive, with a
  seven-rehearsal acceptance suite.
- **Publish path** — `consumer-boot` CI job (clean-venv wheel proof, now a
  required check) + `release-kernel.yml` (protected `workflow_dispatch`, OIDC
  trusted publishing, build→publish→verify, tag after registry verification).

## 0.8.0 — 2026-07-30

Control-plane security (plan
`docs/superpowers/plans/2026-07-18-control-plane-security.md`, all seven
tasks): the platform control plane is actually secured — independent
platform-admin identity, exact-host platform routing, atomic audited tenant
provisioning, RLS-active development, one transaction authority, and the
security baseline that precedes the module control-plane program.
Decisions recorded in ADR-0004.

### Breaking
- **`/platform/*` requires platform-admin authentication** and resolves
  ONLY on `PLATFORM_ROOT_DOMAIN` (host-exact middleware + guard; the old
  `startswith("/platform/")` any-host branch and the unauthenticated
  `require_platform` stub are gone). Bootstrap the first admin with
  `scripts/create_platform_admin.py` (CLI-only, migration trust boundary).
- **`POST /platform/tenants` provisions atomically** — payload now requires
  `owner_email`/`owner_password` (optional `owner_first_name`/
  `owner_last_name`); one transaction creates tenant + owner party/person/
  credential + `admin` role grant + two audit events naming the platform
  actor. `TenantCreate {slug,name}` no longer exists.
- **Registration is policy-closed by default**: `auth.registration_policy`
  setting (`open`|`closed`, default `closed`) — closed returns 403
  `registration_closed`; an open-policy registration creates a PLAIN user.
  The race-prone `_assign_first_user_admin` first-registrant bootstrap is
  deleted; platform provisioning is the only owner/admin-creation path.
- **`UnitOfWork` deleted** (zero consumers): `app/core/db.py` is the one
  transaction authority, enforced by an AST governance test
  (`tests/architecture/test_session_authority.py`).
- **Dev quickstart role change**: dev runs RLS-ACTIVE via the same three DB
  roles as production (`scripts/dev-db-init.sh` initdb hook); recreate dev
  volumes (`docker compose -f docker-compose.dev.yml down -v`). The
  superuser dev flow (and its "RLS not enforced in dev" caveat) is gone.
- **`UserCredential` moved to `app/core/models.py`** (PORT-DELTA; the
  `app.features.auth.models` module is deleted).

### Added
- Platform identity: `platform_admins`/`platform_sessions` (migration 0007,
  granted to `platform_api`/`app_admin` only, REVOKEd from `app_user`),
  `require_platform_admin` guard (`aud="platform"` token separation),
  `POST /platform/auth/login` + `/logout`, CLI bootstrap script.
- Deny-by-default canaries (`tests/test_platform_auth_denies.py`) pinning
  the middleware and guard layers independently; provisioning atomicity
  canaries (`tests/test_tenant_provisioning.py`).
- Dynamic RLS/grants catalog audit (`tests/test_rls_catalog.py`): RLS +
  FORCE + policy + role hygiene + platform-table grants + composite
  tenant FKs + metadata↔schema parity, sensitivity self-tested.
- Security baseline: Argon2id password storage (OWASP parameters) with
  legacy-PBKDF2 verify + upgrade-on-login; constant-work login miss paths;
  `SecurityHeadersMiddleware` (headers + computed-strict CSP, zero external
  origins — fonts now vendored under `static/fonts/`); bounded rate-limit
  store contract (`RateLimitStore` protocol, LRU-capped `MemoryStore`,
  route-template keys); `docs/SECURITY.md` with an honest ASVS 5.0 L2
  mapping.
- `GET /platform/tenants` pagination (`limit`/`offset`, clamped ≤200).
- `app.core.db.platform_session` — the non-request platform-session
  boundary (lifespan seeds/jobs).
- Docs authority: ADR-0004 (platform control plane security), ADR-0001
  amended to reality, `AGENTS.md` (canonical agent rules),
  `CONTRIBUTING.md`, README documentation map.

## 0.7.0 — 2026-07-18

Display settings: a tenant-configurable `display` `SettingDomain`
(timezone + date/datetime formats) consumed at render time by the admin
portal, plus the `SettingSpec.validator` mechanism that makes it possible.

### Added
- **Display settings domain**: `timezone`, `date_format`, `datetime_format`
  specs on the new `SettingDomain.display` — tenant-configurable via the
  same tenant→platform→default resolver as every other setting, and
  auto-appearing in `/admin/settings` (registry-driven index, no dedicated
  screen needed).
- **`SettingSpec.validator`**: an optional `Callable[[object], None]` run
  after type/`allowed`/range checks. Write path
  (`validate_spec_value`) raises a loud `BadRequestError` on failure; read
  path (`resolve_with_source`/`resolve_value`) silently degrades to the
  spec default instead — a corrupted or since-invalid stored value can
  never 500 a render.
- **`local_datetime`/`local_date` Jinja filters** (`app.core.templating`):
  the one and only way a template renders a `*_at` timestamp, formatted in
  the viewing tenant's timezone/format via `request.state.display`
  (`app.core.display.get_request_display`, warmed in `require_web_auth` —
  same per-request seam as tenant branding). New governance test,
  `tests/architecture/test_web_conventions.py
  ::test_timestamp_renders_go_through_local_filters`, fails the build on
  any raw `*_at` render that bypasses these filters.

No breaking changes. The JSON API is untouched — every response stays
ISO-8601 UTC before and after this release.

## 0.6.1 — 2026-07-18

Phase 2b.1: closes all seven findings (F1–F7) from Michael's post-merge
review of 0.6.0 — a canonical feature/surface capability model driving
mounting, navigation, and fragment composition; conflict handling that
preserves RLS tenant context; one email authority; per-request tenant
branding; consumed visibility flags; CSRF-protected logout. Every finding
has a regression-catching test (see below).

### BREAKING
- **`user_credentials.email` column dropped** (finding F2). Login now
  resolves `Party` by `(tenant_id, normalize_email(email),
  party_type=person)` first, then `UserCredential` by `party_id` only —
  `Party.email` is the single email authority system-wide. Migration
  `alembic/versions/20260718_0005_single_email_authority.py` drops the
  column and its unique constraint. Intended consequence: NULLing a
  person party's email now disables login for that party outright
  (`tests/test_auth_email_authority.py`,
  `tests/unit/test_auth_service.py::test_login_null_party_email_rejected`).
- **`GET /admin/logout` removed** (finding F7). Logout is
  `POST /admin/logout` only — a bare `<a href="/admin/logout">` was a
  CSRF-exempt safe method that let a third-party page force a victim's
  logout by merely embedding `<img src="/admin/logout">`. The topbar's
  logout control is now an `hx-post` button, routed through the same CSRF
  header-bridge as every other mutation.
- **`FeatureManifest` signature changed** (findings F1, F5).
  `app.core.features.FeatureManifest` gained `web_routers: Sequence[
  APIRouter] = ()` and `nav: Sequence[NavItem] = ()`; `mount_features` gained
  a required `web_enabled: bool` keyword argument. Every feature moved its
  `web.py` router from `routers` to `web_routers` — a project vendoring its
  own feature packages against the old single-`routers` manifest shape must
  update them.

### Added
- **Manifest capability model** (findings F1, F5): `FeatureManifest.
  web_routers`/`nav` are now THE surface extension point — a feature
  declares its admin-portal routes and sidebar entries there, never in a
  parallel hardcoded template list. `WEB_ENABLED` (env var, default
  `true`) is a new, whole-portal surface switch, distinct from the
  per-feature `DISABLED_FEATURES`: `WEB_ENABLED=false` mounts NO feature's
  `web_routers` (zero `/admin` routes, no `/static` mount) while every
  feature's JSON API keeps working — the real API-only deployment mode.
  `templates/admin/parties/detail.html`'s `{% if 'custom_fields' in
  enabled_features %}` guard is the optional-slot pattern for embedding an
  optional feature's fragment without a dead link/broken embed when that
  feature is disabled. Enforced by
  `tests/architecture/test_feature_manifests.py
  ::test_nav_items_paths_exist_in_web_routers` (nav↔routes coherence) and
  its bogus-entry sensitivity check.
- **`app.core.db.conflict_savepoint`** (finding F3): every feature-service
  conflict site (parties, rbac, tenants, auth, custom_fields) now wraps its
  expected-conflict mutation in a `SAVEPOINT` instead of calling a bare
  `db.rollback()`, so a `ConflictError` no longer discards the request's
  `SET LOCAL app.current_tenant` RLS context. Canary:
  `tests/test_conflict_rls_context.py` (Postgres — the bug is invisible on
  SQLite); hard rule enforced by
  `tests/architecture/test_no_feature_rollback.py`.
- **Per-request tenant branding** (finding F4):
  `app.core.branding.get_request_branding(request, db)` resolves
  `load_branding`/`get_brand()` once per request, memoized on
  `request.state.branding`, and `app.core.templating.render()` injects it
  as the `brand` context for every web render (three call sites: `require_
  web_auth`, `GET`/`POST /admin/login`) — previously only the branding
  editor's own preview reflected a tenant's saved `ui_branding`. `load_
  branding`'s merge is now allowlisted to known brand keys.
- **Consumed visibility flags** (finding F6): `custom_fields_service.
  list_for_entity` gained `visible_in: Literal["form", "detail", "list"] |
  None` as the single query-level owner of `show_in_form`/`show_in_detail`/
  `show_in_list` semantics; the values-panel edit form, its read-only
  "Details" section (detail-only, no duplicate render of fields that are
  both `show_in_form` and `show_in_detail`), and the definitions table's
  "visible in" badge are the three consumers.
- **`WEB_ENABLED`** documented in `.env.example` alongside `DISABLED_
  FEATURES`, with the distinction spelled out (per-feature toggle vs.
  whole-portal surface switch).

### Fixed
- `docs/ARCHITECTURE.md`/`README.md` stale `GET /admin/logout` references
  updated to `POST /admin/logout` throughout (route map, auth-flow
  narrative, mutable-resource ownership list).

## 0.6.0 — 2026-07-18

Phase 2b: a server-rendered admin portal (Jinja2 + HTMX + Alpine + Tailwind
v4) alongside the existing JSON API — same tenants, same services, cookie
auth sharing the bearer path's validation seam. Purely additive; no
breaking changes.

### Added
- **Admin portal** (`templates/`, `static/`, new `web.py` module per
  feature, the deletable `app/features/web/` shell feature): cookie-based
  login/logout (`GET`/`POST /admin/login`, `GET /admin/logout`, owned by
  `auth`), a dashboard (`GET /admin`), and CRUD screens for parties (list/
  detail/create/edit/delete), RBAC (roles, role grants, audit log), settings
  (a generic per-key editor plus a friendly branding editor), and custom
  fields (definitions CRUD, plus a values-panel fragment the party detail
  page embeds via htmx — the cross-feature UI composition pattern; see
  `docs/ARCHITECTURE.md`). `DISABLED_FEATURES=web` drops only the `GET
  /admin` dashboard route — login stays mounted (owned by `auth`, a core
  feature) and every other feature's own `/admin/*` screens are independent
  surfaces on their own manifests, not on `web`.
- **`app.core.web_deps.require_web_auth`** — cookie-based portal auth guard
  routed through the SAME `app.core.deps.authenticate_request` seam the
  JSON API's bearer-token path uses, so token/session/tenant validation has
  exactly one implementation for both surfaces. Every portal page requires
  the `admin` role in this phase (phase 3 loosens this once non-admin
  portal surfaces exist).
- **Branding pipeline** (`app.core.branding`): deployment-static identity
  (`brand.json` + `BRAND_*` env overrides, cached process-lifetime) merged
  per-request with a tenant's `settings/branding/ui_branding` DB override
  (colors validated as hex, `custom_css` sanitized against
  `@import`/`javascript:`/`expression()`/`behavior:`/angle-bracket
  breakouts before being rendered `| safe`). This is the consumer that
  closes the no-orphan-settings allowlist entry `ui_branding` opened in
  0.5.0 — the allowlist is now EMPTY.
- **Branded HTML error pages with a JSON fallback** (`app.core.errors`):
  any error response negotiates JSON vs. a branded HTML page off the
  `Accept` header (`text/html` → HTML, including htmx requests; anything
  else → the unchanged JSON envelope). If rendering the HTML page itself
  fails, the response falls back to the plain JSON envelope rather than
  crashing.
- **`/static/*` tenant-resolution exemption** — static assets bypass
  `TenantResolverMiddleware` before any DB query, same as `/health`; before
  this fix, static assets 500'd whenever the DB was unreachable.
- **CSRF header-bridge** (`static/js/csrf.js`) — copies the `csrf_token`
  cookie onto the `X-CSRF-Token` header for every htmx request and
  `fetch()` call, so every mutating portal form uses `hx-post`/`hx-put`/
  `hx-delete` (never a bare `method="post"`, which has no hook to attach
  the header and 403s).
- **`core/identity.py`** — single-owner `normalize_email`/
  `person_display_name` helpers, closing the `Party.display_name`
  write-once SOT gap tracked since 2a: both writers of a person `Party`
  (`parties` service, `auth` service) now recompute `display_name` on every
  create AND update through this one function.
- **`core/query.py`**: `escape_like` — LIKE-wildcard escaping for the
  parties search box, promoted to core so `rbac`'s audit-event search can
  reuse it too.
- **Governance**: a tiered auth-guard architecture test (mutating routes
  must carry `require_user_auth`/`require_role`/`require_web_auth`/
  `require_platform`, not just any `require_*` dependency —
  `require_tenant` alone no longer satisfies it), web-template/import
  convention checks (`tests/architecture/test_web_conventions.py`), a
  per-route non-admin sweep (`tests/unit/test_admin_route_sweep.py`), and
  an end-to-end portal canary against real Postgres
  (`tests/test_admin_portal_e2e.py`) that drives register → cookie
  login → party create → custom-field values-panel → cross-tenant
  isolation → logout-revokes-session, entirely through cookies/HTML forms.

### Changed
- **Docker build now requires npm.** The `Dockerfile`'s `css-builder` stage
  (`node:20-slim`, `npm ci && npm run css:build`) compiles
  `static/css/main.css` before the final Python stage copies the built
  `static/` tree — `docker-build`/CI's `docker-build` job needs network
  access to install the pinned `node_modules` (or a warm build-cache layer)
  the same as it always needed network access for `poetry install`.
  Nothing changes for `make dev` locally beyond running `make css-build`
  (`npm install && npm run css:build`) at least once first, which was
  already required as of 2b-T1.
- **New env knobs** (see `.env.example`): `BRAND_CONFIG_PATH` (override
  `brand.json`'s location) and the `BRAND_*` static-identity overrides
  (`BRAND_NAME`, `BRAND_TAGLINE`, `BRAND_PRIMARY_COLOR`,
  `BRAND_ACCENT_COLOR`, `BRAND_SUPPORT_EMAIL`, `BRAND_APP_URL`) — all
  optional, all read directly by `app.core.branding` (not `Settings`/
  `app.core.config`), documented in `.env.example` for the first time in
  this release (a real as-built gap this task closed — see the task-9
  report's divergence list).

### BREAKING
None. The admin portal is fully additive: every existing JSON route,
response shape, and error envelope is unchanged; `DISABLED_FEATURES` still
accepts every pre-0.6.0 name plus the new `web` name.

## 0.5.0 — 2026-07-17

Phase 2a: typed schemas everywhere, tenant-scoped settings-as-data, the
Party identity remodel, and a custom-fields feature — the template's
signature "zero-migration runtime field" capability.

### Added
- **Settings-as-data** (`app/core/settings_models.py` +
  `app/core/settings_resolver.py` + `app/features/settings/`): a typed
  `SettingSpec` registry, tenant → platform-default → spec-default
  resolution, secret masking on read, audit-on-write, and a tenant admin
  API — `GET /settings/{domain}`, `PUT /settings/{domain}/{key}`. Platform
  defaults are `tenant_id IS NULL` rows, written only by the `platform_api`
  role and seeded idempotently on boot (see `SEED_ON_STARTUP` below). A
  no-orphan-settings architecture test fails the build if a registered spec
  has no real reader (allowlist: exactly one entry, `ui_branding`, pending
  plan 2b's branding UI).
- **Custom fields** (`app/features/custom_fields/`): per-tenant field
  *definitions* (13 types, ported from `dotmac_erp`) with a runtime CRUD API
  (`POST/GET/PATCH/DELETE /custom-fields/definitions...`) and a generic
  values API (`GET/PUT /custom-fields/{entity_type}/{entity_id}/values`)
  that reads/writes the entity's own `custom_fields` JSONB column. Defining
  a new field is a plain row insert against an already-existing table — no
  Alembic migration, no deploy, no restart. `party` ships registered as an
  entity out of the box (`registry.py::ENTITY_MODELS`); register your own
  entities the same way. Per-entity field count is capped via the
  `custom_fields/max_per_entity` setting spec.
- `GET /rbac/roles` — was declared in `rbac/service.py::list_roles` but
  never mounted; now wired with an explicit tenant filter + pagination.
- `SEED_ON_STARTUP` env var (default `true`) — gates idempotent
  platform-setting-default seeding in the app's lifespan handler; set
  `false` on a read replica or when a separate deploy step seeds instead.

### Changed
- Every feature `service.py` payload parameter is now a concrete Pydantic
  schema — `payload: Any` is banned and enforced by
  `tests/unit/test_service_typing.py`. Internal typing cleanup; no field
  names or response shapes changed by this alone (see the Party remodel
  below for the actual breaking field/route renames).

### BREAKING
- **Party identity remodel** replaces the bare `Person` model with `Party`
  (`party_type` person|organization) + subtype tables `PartyPerson`/
  `PartyOrganization`, and the `parties` feature replaces the old
  person-only surface:
  - `POST /people` → `POST /parties/people`; new `POST /parties/organizations`.
  - `GET /people` (unpaginated) → `GET /parties?party_type=&limit=&offset=`
    (paginated, optional type filter, returns either party type).
  - `GET /people/{id}` → `GET /parties/{id}`.
  - `DELETE /people/{id}` (person-only, 404'd on org) → `DELETE /parties/{id}`
    (204, deletes either party type).
  - `RoleGrantRequest.person_id` → `.party_id`;
    `AuditEventRead.actor_person_id` → `.actor_party_id`; every
    `POST /rbac/role-grants` caller must send `party_id` instead of
    `person_id`.
  - The error-response **envelope is unchanged**
    (`{"code", "message", "details", "request_id"}`, from 0.4.0) — only
    field names inside specific payloads moved, not the wrapper shape.
- `DISABLED_FEATURES` accepted feature names now include `settings` and
  `custom_fields`, and the old feature name `persons` no longer exists
  (renamed to `parties`) — an old `.env` disabling `persons` silently no
  longer matches anything; update it to `parties`.

## 0.4.0 — 2026-07-17
- Phase 1 infrastructure foundation: app/core + feature registry, sub-derived
  CRUD/UoW/logging/errors, architecture governance, CI, Docker/deploy.
- BREAKING (API error bodies): all HTTP errors — including 401/403/404/422/429
  from guards and middleware, not just domain exceptions — now use the JSON
  envelope `{"code", "message", "details", "request_id"}` instead of FastAPI's
  `{"detail": ...}`. Clients parsing `detail` must migrate to `message`/`code`.
