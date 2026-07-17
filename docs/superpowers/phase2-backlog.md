# Phase 2 backlog (from phase 1 final review, 2026-07-17)

Carried out of the phase-1 whole-branch review and per-task review cycles. Each item
was explicitly triaged "phase-2 ticket" — none blocks the phase-1 merge.

## Features (spec-scoped)

- **Core parity:** auth hardening (MFA/TOTP, refresh rotation, password reset, lockout,
  API keys) — still open. ~~RBAC parity (incl. mounting `GET /rbac/roles` —
  `rbac/service.py::list_roles` exists, currently uncalled; add an explicit tenant filter
  when wiring)~~ — **delivered 2a-T2**: `GET /rbac/roles` mounted with explicit tenant
  filter + pagination. ~~settings-as-data~~ — **delivered 2a-T3..T5**: spec registry +
  resolver + tenant admin API (`app/core/settings_models.py`/`settings_resolver.py` +
  `app/features/settings/`). **Branding** (`ui_branding` setting spec) remains open —
  it's declared and the sole no-orphan-settings allowlist entry, pending plan 2b's
  branding UI as its consumer.
- ~~**Custom fields feature package**~~ — **delivered 2a-T8..T10**: port SoT dotmac_erp
  `finance/automation` custom-field module, generalized (string entity_type registry,
  tenant_id + RLS, domain exceptions, settings-driven per-entity limit) in
  `app/features/custom_fields/`. Runtime-field requirement demonstrated by the
  `eye_color` e2e canary (`tests/unit/test_custom_fields_api.py`) — zero migrations
  between defining and using a field.
- After core parity lands: archive `dotmac_starter` with a pointer README.

## Architecture / correctness follow-ups

- **Lazy engine construction in `app/core/db.py`** — engines currently build at import
  time from DATABASE_URL; blocks importing the app without env, makes
  `validate_settings`' "DATABASE_URL is required" branch unreachable, and forced the
  unit-test env pin.
- **`get_uow` tenant context** — `app/core/unit_of_work.py::get_uow` yields sessions
  without RLS `set_config`; either take `Request` and apply the same context as `get_db`,
  or mark it loudly platform/maintenance-only. Zero callers today.
- **Feature fault isolation vs. reality** — `load_manifests` imports every feature module
  (including disabled ones) before `mount_features` filters; either skip imports for
  disabled features and wrap non-core imports in try/except, or correct the docs
  (`app/core/features.py` docstring, `.env.example`, ARCHITECTURE.md) to say
  "mount-time only".
- **Auth→RBAC coupling** — `auth/service.py::_assign_first_user_admin` writes Role/
  PersonRole rows directly; belongs behind an rbac-owned function. Invisible to
  import-linter since the six identity models moved to core (ADR-0002).
- **Engine hardening port delta** — spec lists sub's `statement_timeout`/`lock_timeout`/
  `idle_in_transaction_session_timeout` connect args; not ported in phase 1
  (documented deviation).
- **Governance additions:** static check that every new tenant-scoped table ships an RLS
  policy in its migration; test that `alembic/env.py` + `tests/unit/conftest.py` model
  imports cover all feature model modules (a forgotten import makes autogenerate propose
  dropping tables).

## Smaller tickets

- `LOG_LEVEL` setting for `setup_logging()` (currently fixed INFO default).
- Share one health-path constant between tenant middleware (`_HEALTH_PATHS`) and the
  rate-limit bypass (currently only literal `/health`) before mounting `/health/ready`.
- ~~Service payload typing: replace `payload: Any` with concrete Pydantic schemas across
  the four feature services (pairs with mypy tightening).~~ — **delivered 2a-T1/T2**,
  now a standing hard rule enforced by `tests/unit/test_service_typing.py`.
- Test harness: replace private `trans._parent` savepoint-restart idiom with SQLAlchemy
  2.0 `join_transaction_mode="create_savepoint"`.
- deploy.sh: generic ERR trap should also `up -d` the previous image for mid-`up` failures
  (today only the health-gate path restores); qualify `IMAGE_NAME` and rename CI job's
  `IMAGE_TAG` → `IMAGE_REF` when the GHCR publish job is added.
- Service rollback convention: document that after `db.rollback()` (which discards the
  transaction-scoped RLS context) the request must end, never continue.
- Scoping style convention: services relying on RLS alone should say so in a comment
  (persons service style); pick one convention for explicit-vs-RLS-only tenant filters.
- Dangling doc pointers to untracked task reports (Dockerfile, query.py, bump_version.py,
  deploy.sh headers) — commit the reports or strip the references.

## Added during phase 2a execution

- Settings: add `sqlite_where` mirrors to the domain_settings partial unique indexes so the
  resolver precedence test can run unstubbed on SQLite.
- Settings: `_normalize_for_db` None-handling for json/boolean types → clean BadRequestError
  at the settings API boundary (owned by T5's validation; verify it landed there).
- Settings cache (Redis) with invalidation on write — phase 3, alongside Celery/Redis
  infra (noted in `app/core/settings_resolver.py`'s module docstring; no caching exists
  yet, every `resolve_value` call hits Postgres).
- RBAC: consider `require_user_auth` (not admin) for `GET /rbac/roles` when 2b builds
  role-assignment dropdowns.
- Custom-fields definitions list paginates in-router via Python slice (bounded by
  max_per_entity, default 20); if the bound ever rises materially, push limit/offset into
  list_for_entity via apply_pagination.

## SOT-complete gaps (criteria added to spec 2026-07-17)

- `Party.display_name`: stored projection of subtype fields, write-once, no drift
  detection/repair — when 2b adds update endpoints: single write-owner + idempotent repair,
  or compute-at-read. Still open; explicitly named as a known gap in
  `docs/ARCHITECTURE.md`'s "Known dual-writer: Parties" section.
- ~~Ownership table: T11's provenance table must name an owner for every mutable resource
  and state transition (not just models) — routes/service functions per resource.~~ —
  **delivered 2a-T11**: `docs/ARCHITECTURE.md` carries both the model provenance table
  (owner + port SoT for all 12 ORM model classes) and the mutable-resource ownership list
  (resource → owning service function, including the parties dual-writer named with its
  shared invariants). Going forward this becomes maintenance, not a one-off: **extend the
  ownership list to new routes/tasks/event handlers as they arrive** — every future task
  that adds a mutable resource or a new writer of an existing one must update the table in
  the same commit, not leave it to a later doc pass.
- External-system contracts: none in the starter yet; when OpenBao/webhooks arrive (2c),
  each must be declared transport vs contracted authority in ARCHITECTURE.md.
