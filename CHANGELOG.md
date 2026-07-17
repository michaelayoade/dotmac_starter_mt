# Changelog

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
