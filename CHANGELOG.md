# Changelog

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
