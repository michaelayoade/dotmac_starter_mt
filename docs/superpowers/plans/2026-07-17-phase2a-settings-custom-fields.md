# Phase 2a: API Typing, Settings-as-Data, Custom Fields — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Concrete Pydantic typing across all feature services, a tenant-scoped settings-as-data registry, the Party identity remodel (one identity SoT: party = person | organization), and a custom-fields feature package where fields are runtime data (create "eye color" on a party via API — zero migrations per field).

**Architecture:** Two new feature packages (`settings`, `custom_fields`) built on the phase-1 foundation (CRUDManager, domain exceptions → envelope, RLS, feature registry, architecture governance). Settings rows are tenant-scoped with platform-level defaults (`tenant_id NULL`); resolution is tenant row → platform row → spec default. Custom-field definitions are rows; values live in a `custom_fields` JSONB column on the target entity, validated at the service layer.

**Tech Stack:** as phase 1 (FastAPI, SQLAlchemy 2.0, Alembic, Postgres 16 RLS, Poetry; SQLite unit suite + Postgres canaries).

**Spec:** `docs/superpowers/specs/2026-07-17-starter-consolidation-design.md` (phase 2 + amendments d66590d, 51d53c0, bb9700f)

## Global Constraints

- Repo `/home/dotmac/projects/dotmac_starter_mt`, branch `phase2a` off `main`.
- Port sources: `ST:<path>` = `/home/dotmac/projects/dotmac_starter/<path>` (settings/branding — trimmed clone of sub's design, preferred over sub here); `ERP:<path>` = `/home/dotmac/projects/dotmac_erp/<path>` (custom fields). Port faithfully; adaptations only where a task enumerates them. Every port task's report carries a PORT-DELTA section.
- USER RULES: (1) everything by config, no hardcoding; (2) fields are data — creating a custom field must never require code or migration.
- All phase-1 governance stays green at every commit: `make check` (ruff, import-linter incl. contract-sync test, mypy, bandit), architecture tests (thin wrappers incl. db.get/scalars/scalar, route guards, manifests), unit suite.
- Tenancy invariants: every new tenant-scoped table gets `tenant_id` + composite uniques + `ENABLE/FORCE ROW LEVEL SECURITY` + policies + role grants in its migration; **cross-tenant isolation canary written FIRST** for each new feature (CLAUDE.md process rule).
- New feature packages must be added to `FEATURE_MODULES` AND the import-linter independence contract in `pyproject.toml` (the sync test fails otherwise), AND their model modules imported in `alembic/env.py` + `tests/unit/conftest.py` (known governance gap — do it by hand, it's on the phase-2 backlog to automate).
- Integration runs: `TEST_DB_PORT=5437` (host ports 5433/5434 are production containers — never touch). Baseline at branch start: 14 integration, 38 unit+architecture.
- Error bodies: the single JSON envelope everywhere; services raise domain exceptions only.
- Features may import `app.core.*` freely (identity models live in `app/core/models.py`; from Task 6 onward the identity SoT is `Party`/`PartyPerson`/`PartyOrganization`/`PartyRole` — the Party remodel amendment) but never other features.
- Model provenance rule (spec amendment): every model has one declared SoT and owner; Task 11's ARCHITECTURE.md update adds the provenance table.
- New API routes follow existing prefix style (`/settings`, `/custom-fields`) — no `/api/v1` (deferred per spec amendment).
- All list endpoints added here take `limit`/`offset` query params with `ge=0, le=<cap>` router-level bounds using `apply_pagination`.

---

### Task 1: Typed schemas — tenants + persons

**Files:**
- Create: `app/features/tenants/schemas.py`, `app/features/persons/schemas.py`
- Modify: `app/features/tenants/{router,service}.py`, `app/features/persons/{router,service}.py`
- Test: extend `tests/unit/test_crud.py` typing indirectly; new `tests/unit/test_service_typing.py`

**Interfaces:**
- Consumes: existing service functions (`list_tenants`, `create_tenant`, `get_tenant`, `Persons`, `list_persons`).
- Produces: `TenantCreate`, `TenantOut`, `PersonCreate`, `PersonUpdate`, `PersonOut` Pydantic models in each feature's `schemas.py`; service signatures change from `payload: Any` to the concrete schema (e.g. `create_tenant(db: Session, payload: TenantCreate) -> Tenant`). Task 2 mirrors this for auth/rbac.

- [ ] **Step 1: Inventory current inline schemas**

Read both routers. Any Pydantic models defined inline move to `schemas.py` (keep names); response models referenced by decorators update imports. Service `payload: Any` parameters become the concrete create/update schema type. NO behavior change — field names, validation, and response shapes stay byte-identical.

- [ ] **Step 2: Failing typing test**

`tests/unit/test_service_typing.py`:
```python
"""Service payloads must be typed — `Any` payloads are banned in feature services."""

from __future__ import annotations

import re
from pathlib import Path

FEATURES = Path(__file__).resolve().parents[2] / "app" / "features"


def test_no_any_typed_payloads_in_services() -> None:
    offenders: list[str] = []
    for service in sorted(FEATURES.glob("*/service.py")):
        text = service.read_text(encoding="utf-8")
        for match in re.finditer(r"payload:\s*Any\b", text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{service.relative_to(FEATURES.parents[1])}:{line}")
    assert not offenders, "Any-typed payloads:\n" + "\n".join(offenders)
```
Run: `poetry run pytest tests/unit/test_service_typing.py -v` — Expected: FAIL listing all four current services (this test goes green only after Task 2; that is intended — run it, record the RED, and leave it failing until Task 2 finishes. Do not mark it xfail).

Correction to keep CI green mid-plan: since Task 1 only fixes two services, scope the test to pass incrementally — assert offenders only for `tenants` and `persons` in this task:
```python
    fixed = {"tenants", "persons"}
    offenders = [o for o in offenders if o.split("/")[2] in fixed]
```
Task 2 removes the `fixed` filter so the test covers all services permanently.

- [ ] **Step 3: Implement, verify, commit**

```bash
poetry run pytest tests/unit tests/architecture -q   # all green incl. new test
make check
TEST_DB_PORT=5437 make test-db-up && TEST_DB_PORT=5437 make test-integration && TEST_DB_PORT=5437 make test-db-down   # 14/14 — response shapes unchanged
git add -A && git commit -m "refactor: concrete Pydantic schemas for tenants and persons services"
```

---

### Task 2: Typed schemas — auth + rbac; wire GET /rbac/roles

**Files:**
- Create: `app/features/auth/schemas.py`, `app/features/rbac/schemas.py`
- Modify: `app/features/auth/{router,service}.py`, `app/features/rbac/{router,service}.py`, `tests/unit/test_service_typing.py` (drop the `fixed` filter)
- Test: extend integration `tests/test_rbac_audit_isolation.py` with a roles-list isolation assertion

**Interfaces:**
- Consumes: Task 1's pattern; `app.services... (n/a)`; `list_roles(db)` exists in `app/features/rbac/service.py` with zero callers.
- Produces: `LoginRequest`, `TokenResponse`, `RegisterRequest` (auth — match existing names if already defined inline), `RoleCreate`, `RoleOut`, `RoleAssign`, `AuditEventOut` (rbac); **new route `GET /rbac/roles`** (router-level `require_tenant` guard inherited; add `require_user_auth` if the sibling routes use it — match the existing rbac router's guard pattern exactly), calling `list_roles` **with an explicit tenant filter added** (`.where(Role.tenant_id == tenant.id)` — currently RLS-only; make it explicit per the scoping-convention triage).

- [ ] **Step 1: Same mechanical pattern as Task 1** (inline schemas → schemas.py; `Any` → concrete types; drop the typing test's `fixed` filter and watch it go green).

- [ ] **Step 2: TDD the roles endpoint via the canary**

Add to `tests/test_rbac_audit_isolation.py`: tenant A creates role "editor"; `GET /rbac/roles` as tenant A lists it; as tenant B does not. Run against the disposable Postgres — RED (404: route missing) → implement route → GREEN.

- [ ] **Step 3: Verify + commit**

```bash
poetry run pytest tests/unit tests/architecture -q && make check
TEST_DB_PORT=5437 make test-db-up && TEST_DB_PORT=5437 make test-integration && TEST_DB_PORT=5437 make test-db-down   # 15 now (new canary)
git add -A && git commit -m "refactor: typed auth/rbac schemas; wire GET /rbac/roles with explicit tenant scope"
```

---

### Task 3: Settings model (in core) + migration + isolation canary (canary FIRST)

**PLACEMENT RULE for Tasks 3–5:** the settings MODEL and the runtime RESOLVER live in `app/core/` (mirroring the six-identity-models and audit precedents) because the `custom_fields` feature must consume `resolve_value` and features may never import each other. The settings FEATURE package owns only what nothing else imports: spec declarations, seed, router, schemas, manifest.

**Files:**
- Create: `app/core/settings_models.py` (model + enums), `app/features/settings/{__init__.py,feature.py}` (scaffold; router in Task 5), `alembic/versions/<rev>_settings_table.py`
- Modify: `app/features/__init__.py` (FEATURE_MODULES), `pyproject.toml` (independence contract), `alembic/env.py`, `tests/unit/conftest.py` (model imports — import `app.core.settings_models`)
- Test: `tests/test_settings_isolation.py` (Postgres canary)

**Interfaces:**
- Consumes: `app.core.models.Base/TimestampMixin/uuid_pk`, existing RLS migration patterns (`alembic/versions/20260504_0001_...py` `_apply_rls()` is the reference).
- Produces: model `DomainSetting` (table `domain_settings`) and enums `SettingDomain(str, Enum)` = `{auth, audit, branding, custom_fields}`, `SettingValueType(str, Enum)` = `{string, integer, boolean, json}` — all in `app/core/settings_models.py`. Tasks 4–6 build on these exact names.

Port source: `ST:app/models/domain_settings.py`, adapted for tenancy:

1. Columns as ST (id uuid PK, domain, key String(120), value_type, value_text Text, value_json JSON-with-JSONB-variant, is_secret, is_active, timestamps) **plus** `tenant_id: UUID NULL REFERENCES tenants(id) ON DELETE CASCADE` — **NULL means platform-level default row**.
2. Uniqueness via two partial unique indexes (composite unique can't span NULLs): `uq_domain_settings_platform (domain, key) WHERE tenant_id IS NULL` and `uq_domain_settings_tenant (tenant_id, domain, key) WHERE tenant_id IS NOT NULL` (create in the migration with `op.create_index(..., unique=True, postgresql_where=...)`; on SQLite the unit suite doesn't exercise uniqueness across NULL semantics — acceptable).
3. Keep ST's value-alignment CheckConstraint style from sub: json type ⇒ value_json set & value_text null; else value_text set (copy the constraint from `dotmac_sub/app/models/domain_settings.py` — ST dropped it; we keep it).
4. `SettingDomain` trimmed to the four domains above (ST has 5; we drop `scheduler`/`billing`, add `custom_fields`).

Migration RLS (this table is special — tenants must READ platform rows but only write their own):
```sql
ALTER TABLE domain_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE domain_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY domain_settings_read ON domain_settings FOR SELECT
  USING (tenant_id = app_current_tenant_id() OR tenant_id IS NULL);
CREATE POLICY domain_settings_write_ins ON domain_settings FOR INSERT
  WITH CHECK (tenant_id = app_current_tenant_id());
CREATE POLICY domain_settings_write_upd ON domain_settings FOR UPDATE
  USING (tenant_id = app_current_tenant_id()) WITH CHECK (tenant_id = app_current_tenant_id());
CREATE POLICY domain_settings_write_del ON domain_settings FOR DELETE
  USING (tenant_id = app_current_tenant_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON domain_settings TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON domain_settings TO platform_api;
```
(platform_api has no RLS bypass but also no `app.current_tenant` set → policies give it only NULL-tenant SELECT; INSERT of NULL-tenant rows fails the write policy... **therefore add**: `CREATE POLICY domain_settings_platform_all ON domain_settings TO platform_api USING (tenant_id IS NULL) WITH CHECK (tenant_id IS NULL);` — platform role manages platform rows only. Verify with the canary.)

- [ ] **Step 1: Write the canary FIRST** — `tests/test_settings_isolation.py` (follow `tests/test_cross_tenant_isolation.py` fixtures): raw-SQL/ORM assertions that (a) tenant A's setting row is invisible to tenant B's session, (b) both tenants see a platform row (tenant_id NULL) inserted via the admin engine, (c) tenant A cannot INSERT a row with tenant_id NULL or tenant B's id (RLS write policy rejects). Run: FAIL (table missing).
- [ ] **Step 2: Model + feature scaffold** (`feature.py` exports `FeatureManifest(name="settings", routers=[])` for now — empty routers list is valid), FEATURE_MODULES + import-linter contract + env.py/conftest imports. `make check` green (contract-sync test forces the pyproject edit).
- [ ] **Step 3: Migration** per above. `TEST_DB_PORT=5437 make test-db-up` (migrates) → canary GREEN.
- [ ] **Step 4: Full gate + commit** — `git commit -m "feat(settings): tenant-scoped domain_settings with platform defaults (RLS canary first)"`

---

### Task 4: Settings spec registry + resolver (in core) + seed

**Files:**
- Create: `app/core/settings_resolver.py` (SettingSpec, registry API, resolver, upsert/ensure), `app/features/settings/{spec.py,seed.py}` (spec DECLARATIONS + seed live in the feature)
- Modify: `app/main.py` (seed platform defaults in lifespan)
- Test: `tests/unit/test_settings_service.py`

**Interfaces:**
- Consumes: Task 3 model/enums (`app.core.settings_models`); `app.core.db.PlatformSessionLocal` (seed uses the platform role — app_user cannot write NULL-tenant rows).
- Produces (exact contracts Tasks 5–7 use — resolver names live in `app.core.settings_resolver`):
  - `SettingSpec` frozen dataclass: `domain: SettingDomain, key: str, value_type: SettingValueType, default: object | None, label: str | None = None, required: bool = False, allowed: set[str] | None = None, min_value: int | None = None, max_value: int | None = None, is_secret: bool = False` (ST/sub shape minus `env_var` — env seeding comes via seed.py explicitly).
  - `register_specs(specs: list[SettingSpec]) -> None` + `all_specs() -> list[SettingSpec]` — core holds the registry mechanism; `app/features/settings/spec.py` DECLARES the initial entries and calls `register_specs` at import: `custom_fields/max_per_entity` (integer, default 20, min 1, max 100 — ported from ERP's orphan spec, now actually consumed), `branding/ui_branding` (json, default {}), `audit/retention_days` (integer, default 365, min 1). The feature's `__init__.py` imports `spec` so registration happens when the feature loads.
  - `get_spec(domain, key) -> SettingSpec` (KeyError → NotFoundError at API layer).
  - `resolve_value(db, domain: SettingDomain, key: str, *, tenant_id: UUID | None) -> Any` — tenant row → platform row → spec default; coerce by value_type; enforce allowed/min/max (out-of-range → default, per sub's behavior). No cache (phase-1 has no Redis; note in docstring; backlog: settings cache with Redis in phase 3). If the key isn't registered (feature disabled), fall back to a caller-supplied `default=` kwarg — `resolve_value(..., default=20)` — so core consumers never hard-depend on the settings feature being enabled.
  - `upsert_by_key(db, domain, key, value, *, tenant_id) -> DomainSetting` and `ensure_by_key(...)` (idempotent, race-safe via IntegrityError-catch-reselect — port the mechanics from `SUB:app/services/domain_settings.py::ensure_by_key`, adapted to the partial-unique pair).
  - `app/features/settings/seed.py::seed_platform_defaults()` — for every registered spec, `ensure_by_key(platform_db, ..., tenant_id=None)` with the spec default; called from lifespan (idempotent, never overwrites operator values — sub's contract).

- [ ] **Step 1: TDD on SQLite** — `tests/unit/test_settings_service.py`: resolution precedence (tenant row wins over platform row wins over default), coercion per type, out-of-range int falls back to default, unknown key raises, ensure_by_key idempotency (call twice, one row). Write RED → implement → GREEN.
- [ ] **Step 2: Wire seed into lifespan** — after `validate_settings`, `seed_platform_defaults()` guarded by `settings.seed_on_startup: bool = True` config knob (env-overridable, USER RULE). Unit test with the flag off.
- [ ] **Step 3: Full gate + commit** — `git commit -m "feat(settings): spec registry, tenant->platform->default resolution, idempotent platform seed"`

---

### Task 5: Settings API + no-orphan-settings governance

**Files:**
- Create: `app/features/settings/router.py`, `tests/architecture/test_no_orphan_settings.py`
- Modify: `app/features/settings/feature.py` (router mounted), `app/features/settings/schemas.py` (create)
- Test: `tests/unit/test_settings_api.py` + extend `tests/test_settings_isolation.py` (API-level isolation)

**Interfaces:**
- Consumes: Tasks 3–4.
- Produces: routes under `/settings` (router-level `Depends(require_tenant)` + per-route `require_user_auth` matching rbac's pattern): `GET /settings/{domain}` (list specs merged with effective values for the tenant; secrets masked), `PUT /settings/{domain}/{key}` (validate against spec: unknown key → 404 not_found; type/allowed/range violation → 400 bad_request; writes tenant row via upsert_by_key). Schemas: `SettingOut {domain,key,value,value_type,label,is_secret,source: "tenant"|"platform"|"default"}`, `SettingUpdate {value: Any}`.
- Architecture test (adapted from `SUB:tests/architecture/test_no_orphan_settings.py`, simplified): every `SETTINGS_SPECS` key must appear as a quoted literal somewhere under `app/` outside the settings feature — orphan allowlist starts EMPTY and may only shrink.

- [ ] **Step 1: TDD API on SQLite** (unit tests with overridden `get_db` + fake tenant state, following `tests/unit/test_errors.py` app-building pattern) — list shows default source; PUT then list shows tenant source; secret masking; validation errors map to envelope codes.
- [ ] **Step 2: API-level canary** — tenant A PUTs `custom_fields/max_per_entity=5`; tenant B still resolves 20.
- [ ] **Step 3: Orphan test** — RED if any spec key is unused (all three initial keys must have consumers by end of Task 7 — `ui_branding` gets a consumer in plan 2b; add it to the allowlist with a `# consumed in plan 2b (branding UI)` comment, the only allowed entry).
- [ ] **Step 4: Full gate + commit** — `git commit -m "feat(settings): admin API with effective-value resolution + no-orphan-settings governance"`

---

### Task 6: Party identity remodel — core models + migration + canary (canary FIRST)

**Rationale (spec amendment 2026-07-17):** the identity SoT is the Party pattern. `Person`
is replaced by `Party` (person | organization) with subtype tables; auth credentials, RBAC
grants, and audit actors rebind to `party_id`. The starter has no production data — replace,
don't dual-write.

**Files:**
- Modify: `app/core/models.py` — REPLACE `Person` with: `Party` (table `parties`: `id` uuid_pk, `tenant_id` UUID NOT NULL FK tenants ON DELETE CASCADE, `party_type: Mapped[PartyType]` enum {person, organization}, `display_name: String(200) NOT NULL`, `email: String(320) NULL` with partial unique index `(tenant_id, lower(email)) WHERE email IS NOT NULL`, `is_active` default True, TimestampMixin); `PartyPerson` (table `party_persons`: `party_id` UUID PK + FK parties ON DELETE CASCADE, `first_name String(100)`, `last_name String(100)` — copy any other profile columns the current `Person` carries); `PartyOrganization` (table `party_organizations`: `party_id` UUID PK/FK as above, `legal_name String(200) NOT NULL`). Rebind: `AuthSession.person_id` → `party_id`; `PersonRole` → `PartyRole` (table `party_roles`, columns/composites renamed accordingly); `UserCredential.person_id` (in features/auth/models.py) → `party_id`. `app/core/audit.py`: actor column renamed to `actor_party_id` if it exists as person-named.
- Create: `alembic/versions/<rev>_party_identity.py` — DROP the old `people`/`person_roles`-shaped tables and CREATE the new ones (destructive is correct: template, no data; downgrade recreates the old shape), with RLS ENABLE/FORCE + tenant policies + grants on `parties`, `party_persons`, `party_organizations`, `party_roles` (subtype tables carry no tenant_id — they inherit isolation through the FK to `parties`; document this in the migration docstring; RLS on them uses `EXISTS (SELECT 1 FROM parties p WHERE p.id = party_id AND p.tenant_id = app_current_tenant_id())`).
- Modify: `app/core/deps.py` + `app/core/middleware/*` wherever `Person` was queried (require_user_auth → `db.get(Party, ...)` + assert `party_type == PartyType.person` for login-capable actors), `alembic/env.py`, `tests/unit/conftest.py` (imports + `tenant_row`-style factories: add `party_row` fixture creating a person-type party with subtype row).
- Test: `tests/test_party_isolation.py` (canary FIRST) — replaces/extends the person isolation assertions: tenant A's parties invisible to B; subtype rows unreachable cross-tenant (query via join under B's context returns zero); org-type party creatable and isolated.

**Interfaces:**
- Consumes: existing RLS pattern; every phase-1 canary that referenced `Person` (they will need updating — that's Task 7's job for API-level ones; model-level references in `tests/unit/conftest.py` update here).
- Produces (Tasks 7–10 and plan 2b bind to these EXACT names): `app.core.models.{Party, PartyType, PartyPerson, PartyOrganization, PartyRole}`; fixture `party_row`. Provenance rule: these five are core-owned, canonical for the whole fleet.

- [ ] **Step 1: Canary FIRST** (`tests/test_party_isolation.py`) — RED (tables missing).
- [ ] **Step 2: Core model replacement + dependent import fixes until `import app.main` works and unit suite compiles** (unit tests referencing Person adapt mechanically — same fields, new names; auth/rbac feature service/router compile fixes are ALLOWED here where purely mechanical renames, but behavioral rework belongs to Task 7 — if a rename cascades into logic changes, note it and defer).
- [ ] **Step 3: Migration (destructive replace + RLS incl. subtype EXISTS policies) → canary GREEN on the disposable Postgres.**
- [ ] **Step 4: Full gate** — unit+architecture green; integration: the pre-existing person/auth/rbac canaries WILL need mechanical renames (person→party) to pass — update them (assert same isolation semantics; do not weaken any assertion); 16+ integration green. Commit: `feat(core)!: Party identity model replaces Person (person/organization subtypes, RLS canary first)`

---

### Task 7: Parties feature + auth/rbac rebinding

**Files:**
- Rename/rework: `app/features/persons/` → `app/features/parties/` (models.py stays absent — Party is core; `service.py` reworked: `Parties(CRUDManager[Party])`, `create_person_party(db, payload) -> Party` (creates party + PartyPerson atomically), `create_organization_party(db, payload) -> Party`, `list_parties(db, *, party_type: PartyType | None)`; `router.py`: routes move from `/people` to `/parties` — `POST /parties/people`, `POST /parties/organizations`, `GET /parties?party_type=&limit=&offset=` (paginated, ge=0/le=200), `GET /parties/{id}`, `DELETE /parties/{id}` (204, response_model=None); `schemas.py`: `PersonPartyCreate`, `OrganizationPartyCreate`, `PartyRead` (includes party_type + subtype fields flattened); `feature.py` name="parties").
- Modify: `app/features/auth/{service,router,schemas}.py` — register creates a person-type Party (+ PartyPerson + UserCredential bound to party_id); login/me flow returns party-shaped identity; `app/features/rbac/{service,router}.py` — grants/list operate on PartyRole/party_id (route payload field renames person_id → party_id).
- Modify: `app/features/__init__.py` (persons → parties), pyproject independence contract (sync test enforces), integration canaries' API calls (person endpoints → party endpoints).
- Test: unit service/API tests for the two create paths + type filter; all integration canaries green post-rename.

**Interfaces:**
- Consumes: Task 6 core models.
- Produces: the `parties` feature package (Tasks 8–10's registry binds `{"party": Party}`); auth/rbac fully party-bound. Plan 2b screens build against `/parties`.

- [ ] **Step 1: TDD service (SQLite)** — create_person_party makes both rows atomically (flush, no commit — get_db owns commit); org path; type filter; RED→GREEN.
- [ ] **Step 2: Rework routers/schemas; update auth+rbac bindings; rename feature package (git mv), FEATURE_MODULES + contract.**
- [ ] **Step 3: Update integration canaries to the new endpoints (same isolation semantics, zero weakened assertions); full gate: unit+architecture, make check, integration all green on the disposable Postgres.**
- [ ] **Step 4: Commit:** `feat(parties)!: parties feature replaces persons; auth/rbac bind to party_id`

---

### Task 8: Custom fields — models + migration + isolation canary (canary FIRST)

**Files:**
- Create: `app/features/custom_fields/{__init__.py,models.py,feature.py,registry.py}`, `alembic/versions/<rev>_custom_fields.py`
- Modify: `app/features/__init__.py`, `pyproject.toml` (contract), `alembic/env.py`, `tests/unit/conftest.py`, `app/core/models.py` (**Party** gains `custom_fields` JSONB column)
- Test: `tests/test_custom_fields_isolation.py`

**Interfaces:**
- Consumes: core models (`Party` — post Task 6), RLS migration pattern, and `app.core.settings_resolver.resolve_value` (settings model + resolver live in core per Tasks 3–4's placement rule, so this feature never imports the settings feature).
- Produces:
  - `CustomFieldDefinition` model, table `custom_field_definitions`: port of `ERP:app/models/finance/automation/custom_field.py` with adaptations: `tenant_id UUID NOT NULL REFERENCES tenants(id)` replaces `organization_id`; `entity_type: String(50)` replaces the enum (validated against the registry at service layer); PK `id` via `uuid_pk()` (repo convention) instead of `field_id`; drop `created_by/updated_by` (no actor plumbing yet — phase 2c); keep all field-shape columns (field_code String(50), field_name, description, field_type Enum(CustomFieldType) — KEEP this enum, ported verbatim 13 members —, field_options JSON-variant, is_required, default_value, validation_regex, validation_message, min_value, max_value, max_length, display_order, section_name, placeholder, help_text, show_in_list/form/detail — drop css_class and show_in_print (YAGNI for the starter)), `is_active`, timestamps via TimestampMixin. `UniqueConstraint(tenant_id, entity_type, field_code)`.
  - `registry.py`: `ENTITY_MODELS: dict[str, type] = {"party": Party}` + `def resolve_entity(entity_type: str) -> type` raising `BadRequestError` on unknown. All current registrable entities live in `app.core.models`, so no cross-feature imports; the dict is the extension point features use later.
  - Core `Party` model gains `custom_fields: Mapped[dict] = mapped_column(sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False, server_default=text("'{}'"))`.
  - Migration: definitions table + standard tenant RLS (copy `_apply_rls` single-table pattern: `USING (tenant_id = app_current_tenant_id())` all commands) + grants to app_user/platform_api + `ALTER TABLE parties ADD COLUMN custom_fields JSONB NOT NULL DEFAULT '{}'`.

- [ ] **Step 1: Canary FIRST** — `tests/test_custom_fields_isolation.py`: tenant A inserts an `eye_color` definition for `party`; tenant B's session sees zero definitions; tenant B creates its own `eye_color` (same code — allowed, per-tenant unique); A's party row's `custom_fields` value invisible to B (via existing party isolation). RED (table missing).
- [ ] **Step 2: Models + registry + scaffold feature** (empty routers), FEATURE_MODULES + contract + env/conftest imports. Unit suite green (SQLite JSON variant works).
- [ ] **Step 3: Migration → canary GREEN** on the disposable Postgres. Confirm the parties ADD COLUMN backfills `'{}'` for existing rows.
- [ ] **Step 4: Full gate + commit** — `git commit -m "feat(custom-fields): definitions model + party JSONB values column (RLS canary first)"`

---

### Task 9: Custom fields service — port, generalize, enforce the limit

**Files:**
- Create: `app/features/custom_fields/service.py`
- Test: `tests/unit/test_custom_fields_service.py`

**Interfaces:**
- Consumes: Task 6 models/registry; `app.core.settings_resolver.resolve_value`; `app.core.exceptions.{BadRequestError,ConflictError,NotFoundError}`.
- Produces (Task 8 + plan 2b consume these exact signatures):
  - `create_field(db, tenant_id: UUID, payload: CustomFieldCreate) -> CustomFieldDefinition` — port of `ERP:...custom_fields.py::create_field` with: HTTPException→domain exceptions (dup → ConflictError, bad code → BadRequestError); `field_code.isidentifier()` check kept; **NEW limit enforcement**: `count = <active defs for (tenant, entity_type)>; limit = resolve_value(db, SettingDomain.custom_fields, "max_per_entity", tenant_id=tenant_id); if count >= limit: raise BadRequestError(f"Custom field limit reached ({limit}) for {entity_type}")` — ERP defined this setting but never enforced it (verified); we close that gap.
  - `get_field`, `get_by_code`, `list_for_entity(db, tenant_id, entity_type, *, is_active=True)` (ordered section_name→display_order→field_name, per ERP), `update_field` (entity_type/field_code immutable — port ERP's forbid), `deactivate_field` (soft, ERP's `delete`).
  - `validate_values(db, tenant_id, entity_type, values: dict) -> None` — port ERP's `validate_custom_fields` + `CustomFieldDefinition.validate_value` (model method, ported onto our model) with adaptations: raise `BadRequestError("\n".join(errors))` instead of returning tuples; **unknown field codes are ERRORS here** (ERP silently ignored them — a starter should fail loudly; PORT-DELTA documented); ADD min_value/max_value enforcement for NUMBER/DECIMAL (ERP declared but never checked them — close the gap; compare as Decimal).
  - `set_values(db, tenant_id, entity_type, entity_id: UUID, values: dict) -> dict` — resolve entity via registry, `db.get` the row (NotFoundError if missing — RLS makes cross-tenant reads invisible), `validate_values`, merge into the row's `custom_fields` dict (partial update semantics: provided keys overwrite; `None` deletes a key), flag `flag_modified(row, "custom_fields")`, flush, return merged dict.
  - `get_values(db, tenant_id, entity_type, entity_id) -> dict`.

- [ ] **Step 1: TDD on SQLite** — RED tests for: create + duplicate-code conflict; non-identifier code; the limit (seed a platform `max_per_entity` row or monkeypatch resolve_value default to 2 → third create fails); validate: required missing, NUMBER non-numeric, SELECT non-member, regex mismatch, min/max out of range, unknown code errors; set_values merge semantics incl. `None`-deletes; get_values on missing entity → NotFoundError.
- [ ] **Step 2: Implement (port + adaptations), GREEN.**
- [ ] **Step 3: Full gate + commit** — `git commit -m "feat(custom-fields): service port from dotmac_erp with limit enforcement and strict validation"`

---

### Task 10: Custom fields API + end-to-end runtime-field canary

**Files:**
- Create: `app/features/custom_fields/{router.py,schemas.py}`
- Modify: `app/features/custom_fields/feature.py`
- Test: `tests/unit/test_custom_fields_api.py`, extend `tests/test_custom_fields_isolation.py`

**Interfaces:**
- Consumes: Task 7 service.
- Produces routes (router-level `require_tenant`, per-route `require_user_auth` — match rbac's guard pattern):
  - `POST /custom-fields/definitions` → create_field (201)
  - `GET /custom-fields/definitions?entity_type=party&limit=&offset=` → list (paginated, ge=0 bounds)
  - `GET /custom-fields/definitions/{id}` / `PATCH .../{id}` / `DELETE .../{id}` (soft deactivate, 204 with `response_model=None` — remember the FastAPI 0.115 gotcha)
  - `GET /custom-fields/{entity_type}/{entity_id}/values` and `PUT .../values` → get_values/set_values
  - Schemas: `CustomFieldCreate/Update/Out`, `CustomFieldValues(root: dict[str, Any])`.

- [ ] **Step 1: TDD API on SQLite** (app-builder pattern) — definition CRUD flows, values round-trip, envelope codes on each error path.
- [ ] **Step 2: The spec's acceptance canary (END-TO-END, Postgres)** — extend the isolation test with the user's literal scenario: tenant A admin `POST /custom-fields/definitions {"entity_type":"party","field_code":"eye_color","field_name":"Eye color","field_type":"SELECT","field_options":{"options":[{"value":"brown","label":"Brown"},{"value":"blue","label":"Blue"}]}}` → `PUT /custom-fields/party/<party_id>/values {"eye_color":"brown"}` (the party is a person-type party) → `GET .../values` returns it → tenant B sees neither the definition nor the value → invalid option "green" → 400 envelope. **Zero migrations were run between definition and use** — that's the requirement proven.
- [ ] **Step 3: Full gate + commit** — `git commit -m "feat(custom-fields): definitions + values API — fields are runtime data, no migrations"`

---

### Task 11: Docs + changelog + version bump

**Files:**
- Modify: `CLAUDE.md` (feature list + settings/custom-fields rules), `docs/ARCHITECTURE.md` (two new features, settings resolution order, values-in-JSONB design + registry extension point), `README.md` (custom-fields quick example), `CHANGELOG.md`, `VERSION`/`pyproject.toml` via `make bump-version part=minor` (→ 0.5.0), `docs/superpowers/phase2-backlog.md` (strike delivered items: typed payloads, list_roles, settings cache noted for phase 3)

**Interfaces:** none new; docs must describe as-built (read the code, not this plan, where they diverge).

- [ ] **Step 1: Write docs; verify every documented route/command/var exists (grep-driven, as Task 13 phase 1 did).** ARCHITECTURE.md additionally gains the **model provenance table** (spec amendment): every model in the repo listed with its owner (core | feature) and its port SoT (dotmac_sub / dotmac_starter / dotmac_erp / native), including the Party family as the fleet-canonical identity models.
- [ ] **Step 2: Final full gate incl. one complete integration cycle; `make bump-version part=minor`; commit** — `git commit -m "docs: settings + custom-fields; v0.5.0"`

---

## Completion criteria (phase-2a gate)

- `make check` + full unit/architecture suite green; integration canaries green including the three new files (settings isolation, custom-fields isolation, end-to-end eye_color scenario).
- Runtime-field requirement demonstrably true: the Task 8 canary creates and uses a field with no migration between.
- No-orphan-settings test green with an allowlist of exactly one entry (`ui_branding`, consumed in plan 2b).
- Governance intact: new features in FEATURE_MODULES + independence contract (sync test), guards on every route, thin wrappers.
- Merge `phase2a` → `main` per superpowers:finishing-a-development-branch.

Plan 2b (working admin portal) follows; plan 2c (auth hardening) after.
