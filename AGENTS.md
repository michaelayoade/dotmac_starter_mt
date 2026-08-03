# Agent rules — dotmac_starter_mt

Tool-neutral rules for ANY coding agent working in this repo. This file is
the canonical hard-rules reference; `CLAUDE.md` (repo map + web-portal
specifics) points here and must never fork these rules.

## Documentation hierarchy

- `docs/ARCHITECTURE.md` — as-built truth (model provenance, ownership,
  transaction authority, settings, portal). If code and a doc disagree,
  reconcile toward reality and fix the doc.
- `docs/adr/` — decisions with status; amendments are dated notes, never
  rewritten history.
- `docs/superpowers/plans/` and `specs/` — non-authoritative intent; never
  cite a plan as proof of current behavior.
- `README.md` — onboarding. `CONTRIBUTING.md` — human dev rules.
  `docs/SECURITY.md` — security posture (ASVS mapping).

## Hard rules (enforced — test/contract named per rule)

1. **Routers are thin.** `router.py`/`web.py` never issue direct DB queries
   (no `db.query(`, `db.execute(`, `select(`) — logic lives in `service.py`.
   (`tests/architecture/test_thin_wrappers.py`)
2. **Timestamps render only through the `local_datetime`/`local_date` Jinja
   filters** — never a raw `*_at` attribute in a template.
   (`tests/architecture/test_web_conventions.py::test_timestamp_renders_go_through_local_filters`)
3. **Every mounted route carries a `require_*` guard** (route- or
   router-level), or sits in the commented `ALLOWLIST`. Mutating routes
   need a guard from the explicit `AUTH_GUARD_NAMES` set
   (`require_user_auth`, `require_role`, `require_web_auth`,
   `require_platform_admin` — `require_tenant` alone does NOT count) or a
   commented `MUTATING_ALLOWLIST` entry.
   (`tests/architecture/test_route_guards.py`; non-admin sweep in
   `tests/unit/test_admin_route_sweep.py`)
4. **Every `app/features/<name>` package is registered** in
   `FEATURE_MODULES` and exports a `feature.py` manifest named after its
   package. (`tests/architecture/test_feature_manifests.py`)
5. **Features never import each other; core never imports features.**
   (`pyproject.toml` `[tool.importlinter]` contracts, `make lint-imports`)
6. **The import-linter independence contract's `modules` list stays
   byte-for-byte in sync with `FEATURE_MODULES`.**
   (`tests/architecture/test_feature_manifests.py::test_importlinter_independence_contract_matches_feature_modules`)
7. **No `payload: Any` in feature services** — every payload parameter is a
   concrete Pydantic schema.
   (`tests/unit/test_service_typing.py::test_no_any_typed_payloads_in_services`)
8. **`dotmac_kernel/db.py` is the ONE transaction authority.** No module outside
   it calls `SessionLocal()`/`PlatformSessionLocal()`/`sessionmaker(...)` or
   constructs `Session(...)`; boundaries (`get_db`/`get_platform_db`/
   `platform_session`) own commit/rollback, services only mutate and flush.
   See `docs/ARCHITECTURE.md` § "Transaction authority".
   (`tests/architecture/test_session_authority.py`)
9. **Feature services never call `db.rollback()`.** Expected conflicts use
   `dotmac_kernel.db.conflict_savepoint`, with the mutation INSIDE the `with`
   block — a bare rollback wipes the transaction's `SET LOCAL` tenant
   context. Full rationale: `docs/ARCHITECTURE.md` § "Conflict handling".
   (`tests/architecture/test_no_feature_rollback.py`; canaries in
   `tests/test_conflict_rls_context.py`)
10. **Every registered `SettingSpec` has a real reader** under `app/`
    outside the settings feature/resolver. The intentionally-unwired
    allowlist is EMPTY and may only shrink, never grow, without a task/plan
    reference. (`tests/architecture/test_no_orphan_settings.py`)
11. **Every tenant-scoped table:** `tenant_id UUID NOT NULL`, composite
    unique for anything unique-per-tenant, and `ENABLE`+`FORCE ROW LEVEL
    SECURITY` + policy in the SAME migration that creates the table
    (`domain_settings` is the one documented exception). Platform catalog
    tables instead get NO RLS but are REVOKEd from `app_user`. Enforced
    dynamically by `tests/test_rls_catalog.py` plus the per-feature
    isolation canaries — Postgres only (`make test-db-up &&
    make test-integration`); SQLite cannot enforce RLS.
12. **Migration discipline.** Migrations run as `app_admin`
    (`MIGRATION_DATABASE_URL`), never on container boot — the Dockerfile
    `CMD` only runs `uvicorn`; `scripts/deploy.sh` is the only place
    migrations run in production. The same migration that creates a table
    creates its RLS and grants.
13. **Cross-repository engineering governance is pinned and required.**
    `.dotmac/standards-profile.json` names this repository's declared authority
    and fully typed contract surfaces, and pins the accepted Governance source
    by exact commit. The `Dotmac engineering standards` CI job must execute the
    Governance action at that same commit; a mutable tag/branch, copied policy,
    candidate mode, missing job, or source/profile revision mismatch is not an
    admissible substitute. Product CI is evidence only for the product revision
    it evaluated. (`.github/workflows/engineering-standards.yml`; Governance
    ADR 0006 in `michaelayoade/dotmac_governance`)

## Everything by config — no hardcoding

Env-specific values are overridable variables with documented defaults,
never literals: new knobs go in `Settings` + `.env.example`; Make vars use
`?=`; compose files use `${VAR:-default}`; `scripts/deploy.sh` falls back
via `: "${VAR:=default}"`. New secrets/knobs that must not keep a dev
default in production are added to `validate_settings`'s prod-fatal list.

## Validation before any commit

- `make check` — ruff lint, import-linter, mypy, bandit, format check.
- `make test-unit` — SQLite-fast: `tests/unit` + `tests/architecture`.
- `make test-db-up && make test-integration && make test-db-down` —
  Postgres RLS canaries (`TEST_DB_PORT` overridable).

## Process

- **TDD / canary-first.** New behavior lands with its test written first;
  tenancy-affecting work starts with the cross-tenant isolation canary (see
  `tests/test_cross_tenant_isolation.py` for the pattern). New governance
  tests must include a sensitivity proof (shown RED against a temporary
  violation).
- **New feature:** create the package + `feature.py`, register in
  `FEATURE_MODULES`, add to the import-linter independence contract, write
  the isolation test first. New settings/custom-field entities: follow the
  Extension points in `CLAUDE.md`.
- Zero-consumer code is deleted, not kept. Every new concept gets its owner
  row in `docs/ARCHITECTURE.md`'s provenance/ownership tables.
