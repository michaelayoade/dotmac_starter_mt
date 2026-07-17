# Starter Consolidation: dotmac_starter_mt as the single DotMac starter

**Date:** 2026-07-17
**Status:** Approved
**Decision owners:** Michael (user), Claude (design)

## Decision

Consolidate `dotmac_starter` and `dotmac_starter_mt` into **one repo: `dotmac_starter_mt`**, with multi-tenancy always on. A "single-tenant" deployment is simply a deployment with one tenant. `dotmac_starter` is retired (archived with a pointer README) once feature parity is reached.

Explicitly rejected alternatives:

- **One repo, two builds via a flag** — rejected because the single/multi divergence lives in the ORM schema, session layer (RLS `SET LOCAL`), DB roles, and test harness; a build flag would branch exactly where flags are most brittle.
- **Monorepo with shared core + two app packages** — rejected as permanent maintenance overhead for a distinction (single vs multi tenant) that MT dissolves.
- **Strip dotmac_sub down to a starter** — rejected because sub is single-tenant (tenancy is the hardest thing to retrofit and MT already solved it) and its ISP domain (PostGIS, RADIUS, OLT/GenieACS) is woven through ~848 service files; stripping is a de-tangling audit with high leftover-domain risk. Sub instead serves as the **infrastructure source of truth** (see below).

## Three sources of truth

| Source | Contributes | How |
|---|---|---|
| `dotmac_starter_mt` (this repo) | Tenancy foundation | Already built: RLS with per-request `SET LOCAL app.current_tenant`, `tenant_id` + composite unique constraints on all tenant-scoped models, `base.py` mixins (`uuid_pk`, `TimestampMixin`), three DB roles (`app_user` RLS-enforced / `platform_api` / `app_admin` BYPASSRLS for migrations), three DB URLs, `TenantResolverMiddleware` (custom domain → subdomain → 404), real-Postgres cross-tenant isolation canary tests, ADR docs. |
| `dotmac_sub` | Infrastructure & engineering discipline | Ported near-verbatim (adapted to tenancy where needed). |
| `dotmac_starter` | Features | Rewritten tenant-scoped as modular feature packages — not copied line-for-line. |

## Infrastructure ported from dotmac_sub

Cross-cutting modules (adapt imports/paths; keep patterns intact):

- `app/services/crud.py` — generic `CRUDManager[TModel]` (create/get/update/delete, soft-delete, `_get_or_404`, commit-vs-flush control) + `app/services/common.py` (`apply_ordering`, `apply_pagination`, `validate_enum`) and query builders. Tenant-scope filtering baked into the MT port of the base manager.
- Transaction discipline: `UnitOfWork` + `get_uow` (savepoints, `ConcurrencyConflict`), `task_session()` for Celery, `form_write()` guaranteed-rollback context for web form handlers.
- `app/db.py` engine hardening: `statement_timeout` / `lock_timeout` / `idle_in_transaction_session_timeout` connect args, `pool_pre_ping`, documented conservative pool sizing.
- `app/logging.py` JSON structured logging (request_id/actor_id/path/method/status/duration_ms) with pytest-safe lazy stderr handler.
- `app/observability.py` ASGI middleware: x-request-id generation/propagation, actor extraction, Prometheus request count/latency/error metrics. OTel instrumentation (`telemetry.py`) as an optional, default-off feature.
- `app/errors.py` content-negotiated error handling: branded HTML error templates for browsers, structured JSON `{code, message, details, request_id}` for API; `DomainError` hierarchy (already shared by all three repos).
- CSRF double-submit middleware (HTMX-aware), security-headers middleware, login rate limiting (MT's tenant-aware rate limiter keyed by tenant/ip/path is kept).
- Settings-as-data: `settings_spec.py` registry + seed, generic admin settings UI, guarded by the no-orphan-settings architecture test.
- Startup discipline: fail-fast preflight vs deferred idempotent seeding off the serving path; deferred router mounting with per-router fault isolation.

Tooling & delivery:

- `pyproject.toml` baseline: ruff (line 88, rules E/W/F/I/B/C4/UP/S, ruff-format), mypy with per-module override pattern, bandit.
- **import-linter contracts** enforcing: features → core only, never feature → feature.
- `tests/architecture/` executable governance: thin-wrapper test (no `db.query`/`db.execute`/`select(` in `app/api` or `app/web`), route-guard-coverage test (every `/api/v1` route has a permission/role dependency), no-orphan-settings test.
- `.pre-commit-config.yaml`: ruff + ruff-format, standard hooks, bandit, detect-secrets with baseline.
- Self-documenting Makefile (`## ` help pattern): lint/format/type-check/security/check, test/test-cov/test-fast, migrate targets, dev/worker/beat, docker dev-vs-prod split, deploy.
- Multi-job CI: lint, type-check, import-boundaries, unit tests (xdist, coverage), security, pre-commit, docker-build with migration + health gate against real Postgres, integration tests (real Postgres for RLS canaries), GHCR publish on main.
- Deploy: immutable-image prod compose (`APP_IMAGE` required, no bind mounts) vs explicit dev overlay; `deploy.sh` (verify image → DB backup → pin tag → pull → `alembic upgrade heads` as one-off container → recreate → health gate → auto-rollback); **migrations never run on container boot**; image retention script.
- Versioning: single `VERSION` file synced to `pyproject.toml`, `bump_version.py`, label-driven version-bump-PR workflow, `CHANGELOG.md`.

## Model source-of-truth and the Party identity model

**Amendment 2026-07-17 (Michael):** every model has exactly one source of truth — declared
provenance (which repo/module its canonical definition lives in) and one owner (core vs a
feature). Apps built from this starter inherit models; they never re-declare person-ish or
identity-ish tables. `docs/ARCHITECTURE.md` carries the provenance table.

**Identity SoT is the Party pattern**, replacing the bare `Person` model (phase 2a):
`parties` (id, tenant_id, `party_type` person|organization, display_name, email, is_active,
RLS) with subtype tables `party_persons` (party_id PK/FK, first_name, last_name, …profile)
and `party_organizations` (party_id PK/FK, legal_name). Auth credentials, RBAC grants,
audit actors, and custom-field values all bind to `party_id`. Fleet rationale: ERP
customers/suppliers, CRM contacts/companies, and sub subscribers are all party roles —
future features attach role tables to parties instead of inventing new identity tables.

Generalizes dotmac_sub's declarative router-spec + deferred-mount pattern into self-contained feature packages:

```
app/
  core/                  # tenancy, db, config, logging, errors, observability,
                         # csrf, security headers, auth dependencies, crud base, uow
  features/
    auth/                # JWT + refresh rotation, MFA/TOTP, password reset,
                         # lockout, API keys, sessions — tenant-scoped
    rbac/
    audit/
    settings/            # settings-as-data + branding/white-label
    billing/
    files/
    notifications/
    websockets/          # Redis pub/sub
```

Each feature package contains `models.py`, `schemas.py`, `service.py`, `router.py` (API), `web.py` (HTML routes, if any), `tasks.py` (Celery, if any), `templates/`, and a **`feature.py` manifest** declaring: name, routers with mount kind (`api`/`web`) and dependency mode (`user` / `admin` / `perm:<domain>` / `readperm:<key>`), settings specs, default-enabled flag.

A central registry in `app/main.py`:

- Mounts enabled features; core features at startup, heavier ones deferred in the lifespan with per-feature fault isolation (a broken feature module cannot take the app down).
- Feature enablement via settings (env-driven), so a new app disables or deletes what it doesn't need.
- Import-linter contracts enforce the boundary: `features/* → core` allowed; `features/x → features/y` forbidden (cross-feature needs go through core interfaces or events).

Alembic: single migration lineage (features contribute migrations to one history) — simplest correct option for a template; per-feature branches were considered and rejected as ceremony.

## Features ported from dotmac_starter (rewritten tenant-scoped)

Phase-ordered:

1. **Core parity:** auth (JWT + refresh rotation, MFA/TOTP, password reset, lockout, API keys, sessions), RBAC, audit logging, settings/branding. All models on `base.py` mixins with `tenant_id` + composite uniques; all queries tenant-scoped via the CRUD base.
   Also in phase 2: **custom fields** as a feature package (`app/features/custom_fields/`), ported from dotmac_erp's `app/models/finance/automation/custom_field.py` + `app/services/finance/automation/custom_fields.py` and generalized: `entity_type` as a string registry (features declare their own entities, not a hardcoded finance enum), `tenant_id` + RLS like all tenant-scoped models, domain exceptions instead of in-service `HTTPException`, per-entity field limit via settings. The ERP module is the port SoT for field types (text/textarea/number/decimal/date/datetime/boolean/select/multiselect) and the unique-field-code-per-entity constraint.
   **Hard requirement — fields are data, not schema:** creating a field (e.g. "eye color" on customer) is a runtime operation — an insert of a `CustomFieldDefinition` row via API/admin UI. No model change, no Alembic migration, no deploy. Values are stored schema-lessly (JSONB on the entity or a typed value table — phase 2 design decides) and validated at the service layer against the definition's type/options. The feature ships its own (one-time) migrations for the definitions/values tables only.
2. **Async infra:** Celery + Redis (worker, beat), `task_session` pattern, tenant-aware task conventions (task payloads carry tenant_id; sessions set RLS context).
3. **Web UI shell:** Jinja2 + HTMX + Alpine.js + Tailwind v4 (npm build), per-portal `templates/{admin,customer,public,auth}` with shared `layouts/` and `components/`, `brand.json` white-label global, branded error pages.
   **Amendment 2026-07-17:** the ADMIN portal is pulled forward into phase 2 as a *working* admin UI (shell + functional screens for login, tenants, people, roles, audit log, settings, custom fields). Phase 3 retains the customer/public portal structure and remaining shell generalization. Phase 2 additionally includes API cleanup: concrete Pydantic payload/response schemas replacing `payload: Any` across services, and wiring the orphaned `list_roles` endpoint. (/api/v1 prefixing, pagination conventions, and OpenAPI polish were considered and deferred.)
4. **Heavier modules:** billing, file uploads, notifications, WebSockets — each as a disable-able feature package.

## Config

Upgrade to **pydantic-settings** (`BaseSettings`): typed, env-driven, documented fields; feature flags default sensibly; the three DB URLs (`DATABASE_URL`, `PLATFORM_DATABASE_URL`, `MIGRATION_DATABASE_URL`) and `PLATFORM_ROOT_DOMAIN` remain first-class. `.env.example` fully commented with `change-me` placeholders (sub's style).

## Testing strategy

- **Unit suite on SQLite** (fast, default): port sub's `conftest.py` patterns — env stomping before app import, UUID/JSONB SQLite shims, connection + outer-transaction + rollback isolation, service-layer-built fixtures, autouse singleton/cache resets.
- **RLS integration canaries on real Postgres** (MT's existing approach): cross-tenant isolation tests requiring migrated Postgres with the three roles; run in CI's integration job. SQLite cannot enforce RLS, so these are the guardrail that tenancy actually works.
- **Architecture tests** (from sub) run in the unit suite.

## Docs & agent conventions

- **`CLAUDE.md`** (new — none of the three repos has one): codifies the layered layout, feature-package rules, thin-wrapper rule, tenancy invariants (every tenant-scoped model gets `tenant_id` + composite unique; never query without tenant scope; migrations run as `app_admin`), testing strategy, and Make targets.
- Keep MT's ADR habit (`docs/adr/`); ADR-0002 records this consolidation decision referencing this spec.
- Port sub's docs scaffold: `docs/ARCHITECTURE.md`, `docs/DEVELOPER_GUIDE.md`, `docs/testing/TEST_ENV.md`.
- README rewritten: what the template gives you, how to start a new app from it (incl. running as a one-tenant deployment), production checklist.

## Endgame for dotmac_starter

When phase 2 (core parity) is complete and CI is green: archive `dotmac_starter` — README replaced with a pointer to `dotmac_starter_mt`, repo archived on GitHub. Until then it stays frozen (no new features).

## Success criteria

- One repo (`dotmac_starter_mt`) with CI green: lint, type-check, import-boundaries, architecture tests, unit suite (SQLite), RLS canaries (Postgres), docker health-gate.
- A new app can be started by cloning, deleting/disabling unwanted feature packages, and deploying — single-tenant deployments work as one-tenant instances with no code changes.
- Feature-boundary violations and unguarded routes fail the build, not review.
- `dotmac_starter` archived with pointer.

## Non-goals

- Porting any ISP/domain functionality from dotmac_sub (RADIUS, PostGIS, OLT, ISP billing).
- Mobile (Flutter) scaffolding — revisit later if a starter needs it.
- Per-feature Alembic branches, plugin-style dynamic feature loading, or multi-repo shared libraries.
