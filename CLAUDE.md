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
  `service.py`, `router.py`, `feature.py` (exports `feature: FeatureManifest`).
  Features never import each other (import-linter contract "Features are
  independent of each other"); cross-feature references use FK strings /
  UUID columns, never a Python import. Six registered today: `tenants`,
  `auth`, `parties`, `rbac`, `settings` (tenant-scoped settings-as-data admin
  API — spec/seed/router/schemas only; the registry/resolver mechanics it
  depends on live in core, see below), `custom_fields` (definitions CRUD +
  values on a registered entity's `custom_fields` JSONB column — 13 field
  types, zero-migration field creation).

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

Three points let a project built from this template add its own surface
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
  `tests/test_custom_fields_isolation.py`), which fail if isolation is
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
- `make dev` — run the dev server. `make docker-build` / `make docker-dev` —
  build/run the container locally. `make migrate` / `make migrate-new` —
  Alembic. `make deploy TAG=...` — production deploy via `scripts/deploy.sh`.

## Testing model

- Unit tests (`tests/unit`, `tests/architecture`): in-memory SQLite, no RLS —
  do not test tenancy correctness there, only logic and static structure.
- Tenancy correctness: Postgres RLS canaries in `tests/` (top-level, not
  under `tests/unit`) — require a real, migrated database
  (`make test-db-up`).
