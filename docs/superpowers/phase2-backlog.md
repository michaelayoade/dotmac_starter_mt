# Phase 2 backlog (from phase 1 final review, 2026-07-17)

Carried out of the phase-1 whole-branch review and per-task review cycles. Each item
was explicitly triaged "phase-2 ticket" — none blocks the phase-1 merge.

## Features (spec-scoped)

- **Core parity:** auth hardening (MFA/TOTP, refresh rotation, password reset, lockout,
  API keys), RBAC parity (incl. mounting `GET /rbac/roles` — `rbac/service.py::list_roles`
  exists, currently uncalled; add an explicit tenant filter when wiring), settings-as-data
  + branding.
- **Custom fields feature package** — port SoT: dotmac_erp `finance/automation` custom-field
  module, generalized (string entity_type registry, tenant_id + RLS, domain exceptions,
  settings-driven per-entity limit). HARD REQUIREMENT: fields are data, not schema —
  creating a field is a runtime API/admin insert, zero migrations per field
  (spec commits d66590d, 51d53c0).
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
- Service payload typing: replace `payload: Any` with concrete Pydantic schemas across
  the four feature services (pairs with mypy tightening).
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
- Settings cache (Redis) with invalidation on write — phase 3, alongside Celery/Redis infra.
- RBAC: consider `require_user_auth` (not admin) for `GET /rbac/roles` when 2b builds
  role-assignment dropdowns.
