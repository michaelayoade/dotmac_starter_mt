# dotmac_starter_mt

Multi-tenant FastAPI starter. Tenant isolation enforced at three layers:

1. **Routing** — subdomain resolves to a tenant before any request handler runs.
2. **Application** — every service receives `tenant_id` via request state.
3. **Database** — PostgreSQL Row-Level Security policies fail closed if app code forgets to filter.

This repo is **the** DotMac starter: per
[ADR-0002](docs/adr/0002-starter-consolidation.md) it supersedes the older
single-tenant `dotmac_starter` repo, which is frozen and will be archived
once this repo reaches feature parity. A single-tenant product is simply a
deployment of this app with one tenant row — not a different codebase.

See [`docs/adr/0001-multi-tenant-architecture.md`](docs/adr/0001-multi-tenant-architecture.md)
for the full tenancy design, [`docs/adr/0002-starter-consolidation.md`](docs/adr/0002-starter-consolidation.md)
for the consolidation decision, [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the expanded architecture reference, and [`CLAUDE.md`](CLAUDE.md) for the
agent-facing rules summary.

## What's in this skeleton

- `Tenant` model + `tenant_domains` for custom domain support.
- `Person` model with `tenant_id` and per-tenant unique email.
- Minimal JWT auth with tenant-bound credentials and sessions.
- Minimal RBAC with tenant-scoped roles, role grants, and audit events.
- CSRF middleware, tenant-aware in-memory rate limiting, and request IDs.
- `TenantResolverMiddleware` that parses host header → `request.state.tenant`.
- `get_db` dependency that runs `SET LOCAL app.current_tenant` for RLS.
- Initial Alembic migration that creates `app_user`, `platform_api`, and `app_admin`
  Postgres roles, applies RLS policies, and seeds the schema.
- Cross-tenant isolation tests as canaries.

## What's NOT here yet

This is intentionally minimal. To productionize, port from `dotmac_starter`:

- MFA, password reset, account lockout, and production auth hardening
- Billing, file uploads, notifications, scheduler
- Security headers
- Frontend (Tailwind, Alpine CSP build, templates)

Each port follows the same pattern: add `tenant_id`, write the cross-tenant isolation test
first, port the code, watch the test go green.

CI (lint/type-check/security/import-boundaries, unit, Postgres RLS
integration, Docker build + health gate — see `.github/workflows/ci.yml`)
and a production Dockerfile/compose (`Dockerfile`, `docker-compose.yml`,
`docker-compose.dev.yml`, `scripts/deploy.sh`) are already in place; see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#deploy) for how they fit
together.

## Starting a new app from this template

```bash
git clone <this-repo> my-app && cd my-app
# Rename the project: pyproject.toml [tool.poetry].name, IMAGE_NAME defaults
# in Makefile/scripts/deploy.sh, and this README's title.

# Disable features you don't need via DISABLED_FEATURES (fast), or delete
# the package under app/features/ and remove it from FEATURE_MODULES in
# app/features/__init__.py plus the import-linter independence contract in
# pyproject.toml (permanent). Either way, run `make test-unit` afterward —
# tests/architecture/test_feature_manifests.py fails if the registry and
# app/features/ directory drift apart.

cp .env.example .env   # fill in change-me placeholders

make test-db-up   # disposable Postgres + migrations (TEST_DB_PORT if the
                  # default port is taken)
make migrate      # apply migrations to your real DATABASE_URL/MIGRATION_DATABASE_URL
make dev          # run the app
```

A single-tenant deployment is this same app provisioned with exactly one
tenant (`POST /platform/tenants`) — no code changes required.

## Quickstart (dev)

`docker-compose.yml` is prod-only now (requires a published `APP_IMAGE`, no
`db` service) — for local dev, run just the Postgres service from the dev
overlay (`docker-compose.dev.yml`), migrate against it, then run the app
directly with `--reload`:

```bash
poetry install
docker compose -f docker-compose.dev.yml up -d postgres   # DEV_DB_PORT/DEV_POSTGRES_* overridable
MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/starter \
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/starter \
    poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --port 8000 \
    --forwarded-allow-ips "127.0.0.1"
# equivalently, once DATABASE_URL is in .env: make dev
```

In dev, browsers resolve `*.localhost` automatically:

```bash
# Provision two tenants (as platform admin)
curl -X POST http://localhost:8000/platform/tenants \
    -H "Content-Type: application/json" \
    -d '{"slug":"acme","name":"ACME"}'
curl -X POST http://localhost:8000/platform/tenants \
    -H "Content-Type: application/json" \
    -d '{"slug":"widgets","name":"Widgets Inc"}'

# Same Person endpoint, different tenants
curl -X POST http://acme.localhost:8000/people \
    -H "Content-Type: application/json" \
    -d '{"email":"alice@acme.com","first_name":"Alice","last_name":"A"}'
curl http://acme.localhost:8000/people     # sees Alice
curl http://widgets.localhost:8000/people  # sees nothing
```

## Run the cross-tenant tests

```bash
poetry run pytest \
    tests/test_cross_tenant_isolation.py \
    tests/test_auth_tenant_claim.py \
    tests/test_rbac_audit_isolation.py \
    tests/test_security_middleware.py \
    -v
```

These tests require a migrated disposable Postgres database because SQLite cannot enforce
RLS.

## DB roles

```
app_user      — Tenant request role. RLS-enforced. Sets app.current_tenant per request.
platform_api  — Online platform routes. Explicit grants, no RLS bypass.
app_admin     — Alembic migrations and offline maintenance only. Bypasses RLS.
```

The `DATABASE_URL` env var should use `app_user`. `PLATFORM_DATABASE_URL` should use
`platform_api`. Migrations use `MIGRATION_DATABASE_URL` connecting as `app_admin`.
Settings are loaded from the environment and from a local `.env` file.

## Middleware Notes

- Rate limiting is process-local in this skeleton. It is keyed by
  `tenant_id/client_ip/path`, but it does not aggregate across Gunicorn workers and keys live for the
  process lifetime. Port the same key shape to Redis with TTLs for production.
- Inbound `X-Request-ID` is ignored by default to prevent log poisoning. Set
  `TRUST_INBOUND_REQUEST_ID=true` only behind a trusted proxy that normalizes that header.
- CSRF uses a double-submit cookie/header check. Origin/Referer validation is deferred; add it before
  relying on browser-cookie auth in production.

## License

TBD.
