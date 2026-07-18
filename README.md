# dotmac_starter_mt

A multi-tenant FastAPI starter **template** — clone it, pick the features you
need, register your own entities and settings, brand it, ship it. Tenant
isolation is enforced at three layers:

1. **Routing** — subdomain (or custom domain) resolves to a tenant before any
   request handler runs.
2. **Application** — every service receives `tenant_id` via request state,
   never from a client-supplied payload.
3. **Database** — PostgreSQL Row-Level Security policies fail closed if app
   code forgets to filter.

A single-tenant product is simply a deployment of this app with **one**
tenant row (`POST /platform/tenants`) — not a different codebase, not a
different code path.

See [`docs/adr/0001-multi-tenant-architecture.md`](docs/adr/0001-multi-tenant-architecture.md)
for the founding tenancy design, [`docs/adr/0002-starter-consolidation.md`](docs/adr/0002-starter-consolidation.md)
for how this repo became the org's one starter template,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full architecture
reference (including the model provenance/ownership tables), and
[`CLAUDE.md`](CLAUDE.md) for the agent-facing rules summary.

## Starting a project from this template

```bash
git clone <this-repo> my-app && cd my-app
# Rename: pyproject.toml [tool.poetry].name, IMAGE_NAME defaults in
# Makefile/scripts/deploy.sh, and this README's title.
```

Then, in order:

1. **Pick your features.** Seven ship today: `tenants`, `auth`, `parties`,
   `rbac`, `settings`, `custom_fields`, `web` (see "What's in this template"
   below). Disable what you don't need via `DISABLED_FEATURES` (fast — see
   "Disabling a feature" below), or delete the package under
   `app/features/` and remove it from `FEATURE_MODULES` in
   `app/features/__init__.py` plus the import-linter independence contract
   in `pyproject.toml` (permanent). Either way, run `make test-unit`
   afterward — `tests/architecture/test_feature_manifests.py` fails if the
   registry, the import-linter contract, and the `app/features/` directory
   drift apart.
2. **Register your own entities.** `Party` (person/organization) is the
   identity source of truth every shipped feature binds to — don't invent a
   parallel "customer" or "user" table. If your domain needs its own
   entities (orders, devices, tickets, whatever), add them as a new feature
   package following the existing ones as a template (`models.py`,
   `schemas.py`, `service.py`, `router.py`, `feature.py`), and register any
   entity that should carry runtime-defined custom fields in
   `app/features/custom_fields/registry.py`'s `ENTITY_MODELS` (see the
   quick example below).
3. **Register your own settings.** Add a `SettingSpec` to your feature's own
   spec module and call `app.core.settings_resolver.register_specs([...])`
   at import time (see `app/features/settings/spec.py` for the pattern). A
   spec with no real reader anywhere in the code fails the
   no-orphan-settings architecture test — wire the `resolve_value(...)`
   call before you ship it.
4. **Brand it.** Deployment-wide identity (name, tagline, colors, support
   email, app URL) is `brand.json` (repo root) + `BRAND_*` env var
   overrides — see `.env.example`. Per-tenant branding overrides on top of
   that live in the `settings/branding/ui_branding` setting, editable at
   `/admin/settings/branding`. Both layers are consumed by the admin portal
   (see "Admin portal" below).

To run the app locally, see **Quickstart (dev)** below. To run the
integration test suite, use `make test-db-up` to start a disposable
Postgres, `make test-integration` to run tests, then `make test-db-down` to
tear it down — that test DB is separate from the persistent dev database
used for running the app.

## What's in this template

- `Tenant` + `TenantDomain` for subdomain and custom-domain resolution.
- `Party` (`party_type` person|organization) — the fleet-wide identity
  model, with `PartyPerson`/`PartyOrganization` subtype profile tables. See
  `POST /parties/people`, `POST /parties/organizations`.
- Minimal JWT auth (`/auth/register`, `/auth/login`, `/auth/me`) with
  tenant-bound credentials and sessions.
- Minimal RBAC (`/rbac/roles`, `/rbac/role-grants`) plus a tenant audit-event
  log (`/rbac/audit-events`).
- **Settings-as-data**: a tenant admin API (`GET`/`PUT /settings/{domain}/{key}`)
  backed by a typed spec registry, tenant → platform-default → spec-default
  resolution, secret masking, and audit-on-write. See
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#settings-resolution-order--platform-row-rls-design).
- **Custom fields**: define a field on any registered entity at runtime —
  no migration, no deploy — and set/read its value through a generic
  values API. This is the template's signature capability; see the
  quickstart below.
- **Admin portal**: a server-rendered HTML/HTMX admin UI (Jinja2 + Tailwind
  v4 + Alpine) at `/admin/*` — cookie-based login, dashboard, and CRUD
  screens for parties, RBAC roles/grants/audit, settings (incl. a friendly
  branding editor), and custom fields. Deletable (`DISABLED_FEATURES=web`)
  without losing the JSON API. See "Admin portal quickstart" below and
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#admin-portal-web-ui) for the
  auth flow, branding pipeline, and cross-feature UI composition pattern.
- CSRF middleware, tenant-aware in-memory rate limiting, and request IDs.
- `TenantResolverMiddleware` that parses the host header → `request.state.tenant`.
- `get_db` dependency that runs `SET LOCAL app.current_tenant` for RLS.
- Alembic migrations that create the `app_user`/`platform_api`/`app_admin`
  Postgres roles, apply RLS policies, and seed platform-default settings.
- Cross-tenant isolation tests as canaries for every tenant-scoped table
  (parties, settings, custom fields, RBAC/audit, auth).

CI (lint/type-check/security/import-boundaries, unit, Postgres RLS
integration, Docker build + health gate — see `.github/workflows/ci.yml`)
and a production Dockerfile/compose (`Dockerfile`, `docker-compose.yml`,
`docker-compose.dev.yml`, `scripts/deploy.sh`) are already in place; see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#deploy) for how they fit
together.

## What's NOT here yet

This is intentionally minimal outside the tenancy/governance foundation. To
productionize further, port what your project needs:

- MFA, password reset, account lockout, and production auth hardening
  (phase 2c on this repo's own roadmap; fleet-specific rationale lives in
  `docs/adr/`, not here)
- Billing, file uploads, notifications, scheduler
- Security headers
- A self-service (non-admin) portal surface — today every `/admin/*` page
  requires the `admin` role; see `app.core.web_deps.require_web_auth`'s
  docstring for the phase-3 loosening plan

Each port follows the pattern already established: add `tenant_id`, write
the cross-tenant isolation test first, port the code, watch the test go
green.

## Quickstart (dev)

`docker-compose.yml` is prod-only (requires a published `APP_IMAGE`, no `db`
service) — for local dev, run just the Postgres service from the dev overlay
(`docker-compose.dev.yml`), migrate against it, then run the app directly
with `--reload`:

```bash
poetry install
docker compose -f docker-compose.dev.yml up -d postgres   # DEV_DB_PORT/DEV_POSTGRES_* overridable
# Point the app and alembic at the dev Postgres (matches the compose defaults above).
# The initial migration must run as a superuser — it creates the app_user/platform_api/app_admin roles.
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/starter
export MIGRATION_DATABASE_URL="$DATABASE_URL"
poetry run alembic upgrade head
make css-build   # builds static/css/main.css (Tailwind v4) — gitignored, build-only; re-run after editing static/css/src/main.css, or use `make css-watch` while iterating
make dev   # or: poetry run uvicorn app.main:app --reload --port 8000
```

In this quickstart flow, the app runs as the Postgres superuser, which
bypasses Row-Level Security — tenant isolation is not enforced. Use
`make test-db-up && make test-integration` to verify isolation in the
integration suite; production must use the three-role setup from
`.env.example`.

In dev, browsers resolve `*.localhost` automatically:

```bash
# Provision two tenants (as platform admin)
curl -X POST http://localhost:8000/platform/tenants \
    -H "Content-Type: application/json" \
    -d '{"slug":"acme","name":"ACME"}'
curl -X POST http://localhost:8000/platform/tenants \
    -H "Content-Type: application/json" \
    -d '{"slug":"widgets","name":"Widgets Inc"}'

# Same Parties endpoint, different tenants
curl -X POST http://acme.localhost:8000/parties/people \
    -H "Content-Type: application/json" \
    -d '{"email":"alice@acme.com","first_name":"Alice","last_name":"A"}'
curl http://acme.localhost:8000/parties     # sees Alice
curl http://widgets.localhost:8000/parties  # sees nothing
```

## Admin portal quickstart

The portal is server-rendered HTML (Jinja2 + HTMX + Alpine, Tailwind v4
CSS), served from the same FastAPI process as the JSON API — no separate
frontend build/deploy. Continuing from the Quickstart above (Postgres
migrated, at least one tenant provisioned):

```bash
npm ci                 # installs the Tailwind v4 CLI (devDependency, pinned via package-lock.json)
make css-build          # -> static/css/main.css (gitignored — rebuild after editing static/css/src/main.css)
make dev                # poetry run uvicorn app.main:app --reload --port 8000
```

Then, in a browser (dev resolves `*.localhost` automatically, so no
`/etc/hosts` edits needed):

1. Register your first user — `/auth/register` is a JSON endpoint (no
   portal signup form exists; every portal account starts as an API
   registration), the same shape as the Quickstart above's
   `/parties/people` example. The FIRST person registered in a tenant is
   auto-assigned the `admin` role, which the portal requires:
   ```bash
   curl -X POST http://acme.localhost:8000/auth/register \
       -H "Content-Type: application/json" \
       -d '{"email":"admin@acme.com","password":"correcthorsebatterystaple","first_name":"Admin","last_name":"User"}'
   ```
2. Visit `http://acme.localhost:8000/admin/login` and sign in with that
   email/password. The login form is HTMX (`hx-post`) with the CSRF
   header-bridge (`static/js/csrf.js`) wired automatically — nothing to
   configure.
3. Land on `http://acme.localhost:8000/admin` — the dashboard, showing
   party/role/active-session counts for this tenant.
4. From the sidebar: **Parties** (list/create/edit/detail, with a
   custom-fields values panel on the detail page — the cross-feature UI
   composition pattern, see ARCHITECTURE.md), **RBAC** (roles, role
   grants, audit log), **Settings** (per-key editor + a friendly branding
   editor at `/admin/settings/branding` that live-previews your
   `primary_color`/`accent_color`/`custom_css`), **Custom Fields**
   (definitions CRUD).
5. `http://widgets.localhost:8000/admin` (a second tenant, its own
   register+login) sees none of tenant A's data — same RLS isolation the
   JSON API gets, proven end-to-end by `tests/test_admin_portal_e2e.py`.

<!-- Screenshot: admin/dashboard.html — dashboard with stat cards -->
<!-- Screenshot: admin/parties/detail.html — party detail + custom-fields values panel -->
<!-- Screenshot: admin/settings/branding.html — branding editor with live preview -->

Screenshots deferred (no CI artifact pipeline for them yet) — the
placeholders above mark where they'd go; capture manually against a
`make dev` instance if you want them for a fork's own docs.

## Quick example: custom fields (zero migrations)

This is the template's signature capability — a tenant admin adds a new
field to an entity at runtime, no Alembic migration or deploy involved, and
uses it immediately. Requires a party to already exist and an admin-role
party bearing a bearer token (see Quickstart above / `/auth/login`) —
abbreviated here as `$TOKEN`:

```bash
# 1. Define a field on the "party" entity — a plain row insert, nothing else.
curl -X POST http://acme.localhost:8000/custom-fields/definitions \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{
      "entity_type": "party",
      "field_code": "eye_color",
      "field_name": "Eye Color",
      "field_type": "TEXT",
      "is_required": false
    }'

# 2. Use it immediately — set the value on an existing party.
curl -X PUT http://acme.localhost:8000/custom-fields/party/<party_id>/values \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"eye_color": "brown"}'

# 3. Read it back.
curl http://acme.localhost:8000/custom-fields/party/<party_id>/values \
    -H "Authorization: Bearer $TOKEN"
# {"eye_color": "brown"}
```

No migration ran between steps 1 and 3 — `eye_color` lives as a key in
`Party.custom_fields` (JSONB), validated against the definition's
`field_type`/`is_required`/etc. on every write. `party` is registered as a
custom-fields entity out of the box
(`app/features/custom_fields/registry.py::ENTITY_MODELS`); register your
own entities there the same way. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#custom-fields-value-flow) for
the full validate → merge → flag_modified write path.

## Disabling a feature

Every feature can be turned off without deleting code, via the
comma-separated `DISABLED_FEATURES` env var (see `.env.example`):

```bash
# Ship without custom fields and without the settings admin API:
export DISABLED_FEATURES="custom_fields,settings"
```

`app.core.features.mount_features` skips any feature whose name is in this
set — its routers are never mounted, so its endpoints simply don't exist
(404, not a guard failure). This is the fast path for "starting a project
from this template" step 1 above; delete the package under `app/features/`
only once you're sure you'll never want the feature back (`make test-unit`
will tell you if the registry and import-linter contract still agree with
what's on disk).

**`DISABLED_FEATURES=web` is special: it does NOT disable the portal's
login, and it does NOT remove any JSON API.** The `web` feature package
owns exactly one route — `GET /admin`, the dashboard shell — so disabling
it drops only that landing page. `GET`/`POST /admin/login` and
`GET /admin/logout` are owned by the `auth` feature (core, always mounted)
and stay up regardless. Every other feature's own `/admin/*` screens
(parties, RBAC, settings, custom fields) are mounted from THAT feature's
`FeatureManifest` alongside its JSON router — one manifest, two routers,
one on/off switch (`app/features/<name>/feature.py`'s `routers=[router,
web_router]`). There is no per-router granularity: disabling `parties` (say)
to drop its `/admin/parties/*` screens also drops `/parties/*` from the
JSON API, and vice versa — `DISABLED_FEATURES` is a per-FEATURE toggle, not
a per-surface one. If you want JSON-only with zero portal HTML, you cannot
reach that by disabling individual feature packages without losing their
API too; the practical "no HTML" deployment is simply "don't build/serve
`static/`, don't visit `/admin/*`" — every JSON route keeps working exactly
as it does today whether or not any portal template exists.

## Route map

| Prefix | Feature | Notes |
|---|---|---|
| `GET /health` | — | Liveness only, no DB touch, no guard (allowlisted). |
| `POST/GET /platform/tenants`, `GET /platform/tenants/{id}` | `tenants` | Platform-root-domain only, `require_platform`. |
| `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | `auth` | Tenant-scoped JWT flows. |
| `POST /parties/people`, `POST /parties/organizations`, `GET /parties`, `GET /parties/{id}`, `DELETE /parties/{id}` | `parties` | Identity CRUD for both party types. |
| `POST/GET /rbac/roles`, `POST /rbac/role-grants`, `GET /rbac/audit-events` | `rbac` | Roles, grants, audit read — `require_role("admin")`. |
| `GET /settings/{domain}`, `PUT /settings/{domain}/{key}` | `settings` | Tenant settings admin API — `require_role("admin")`. |
| `POST/GET /custom-fields/definitions`, `GET/PATCH/DELETE /custom-fields/definitions/{id}`, `GET/PUT /custom-fields/{entity_type}/{entity_id}/values` | `custom_fields` | Field definitions CRUD + values — `require_role("admin")`. |

Every route above (except `/health`) carries a `require_*` guard —
enforced by `tests/architecture/test_route_guards.py`.

### Admin portal routes (`/admin/*`)

| Prefix | Feature | Notes |
|---|---|---|
| `GET`/`POST /admin/login`, `GET /admin/logout` | `auth` | Cookie login/logout — `/login` is deliberately unguarded (pre-auth), allowlisted with a comment. |
| `GET /admin` | `web` | Dashboard shell — the one route this deletable feature owns. |
| `GET /admin/parties`, `/create`, `POST /admin/parties/{people,organizations}`, `GET /admin/parties/{id}`, `GET`/`POST /admin/parties/{id}/edit`, `POST /admin/parties/{id}/delete` | `parties` | List/detail/create/edit/delete screens. |
| `GET`/`POST /admin/roles`, `/admin/roles/create`, `GET`/`POST /admin/role-grants`, `GET /admin/audit` | `rbac` | Roles, grants, audit log screens. |
| `GET /admin/settings`, `GET`/`POST /admin/settings/{domain}/{key}/edit`, `GET`/`POST /admin/settings/branding` | `settings` | Generic per-key editor + friendly branding editor. |
| `GET`/`POST /admin/custom-fields`, `/create`, `GET`/`POST /admin/custom-fields/{id}/edit`, `POST /admin/custom-fields/{id}/deactivate`, `GET`/`POST /admin/custom-fields/party/{id}/values-panel` | `custom_fields` | Definitions CRUD + the values-panel fragment `parties/detail.html` embeds. |

Every `/admin/*` route (except the login and logout routes) carries
`require_web_auth`, which also requires the `admin` role — see
`app.core.web_deps`.

## Run the cross-tenant tests

```bash
poetry run pytest \
    tests/test_cross_tenant_isolation.py \
    tests/test_auth_tenant_claim.py \
    tests/test_rbac_audit_isolation.py \
    tests/test_security_middleware.py \
    tests/test_party_isolation.py \
    tests/test_settings_isolation.py \
    tests/test_custom_fields_isolation.py \
    tests/test_web_auth_isolation.py \
    tests/test_admin_portal_e2e.py \
    -v
```

These tests require a migrated disposable Postgres database because SQLite
cannot enforce RLS (`make test-db-up` first).

## DB roles

```
app_user      — Tenant request role. RLS-enforced. Sets app.current_tenant per request.
platform_api  — Online platform routes. Explicit grants, no RLS bypass. Also
                the only role permitted to write platform-default (NULL-tenant)
                settings rows.
app_admin     — Alembic migrations and offline maintenance only. Bypasses RLS.
```

The `DATABASE_URL` env var should use `app_user`. `PLATFORM_DATABASE_URL`
should use `platform_api`. Migrations use `MIGRATION_DATABASE_URL`
connecting as `app_admin`. Settings are loaded from the environment and from
a local `.env` file — see `.env.example` for every knob, including
`SEED_ON_STARTUP` (idempotent platform-setting-default seeding on boot; set
`false` on a read replica or when a separate deploy step seeds instead).

## Middleware notes

- Rate limiting is process-local in this template. It is keyed by
  `tenant_id/client_ip/path`, but it does not aggregate across Gunicorn
  workers and keys live for the process lifetime. Port the same key shape
  to Redis with TTLs for production.
- Inbound `X-Request-ID` is ignored by default to prevent log poisoning. Set
  `TRUST_INBOUND_REQUEST_ID=true` only behind a trusted proxy that
  normalizes that header.
- CSRF uses a double-submit cookie/header check. Origin/Referer validation
  is deferred; add it before relying on browser-cookie auth in production.

## License

TBD.
