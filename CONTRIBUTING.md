# Contributing

Human developer rules. Agents follow the same canon via `AGENTS.md`
(the canonical hard-rules list — read it first; nothing here overrides it).

## Gates before every commit

Tests run on the dedicated test server (85.190.246.211) in a fresh isolated
worktree; do not install test dependencies or run test commands on a
workstation, and never on Dotmac Observer — a capped run there once OOM-killed
Prometheus (`AGENTS.md`, "Test host"). Local static checks are
allowed. GitHub CI remains the merge owner.

```bash
make check        # exact Poetry/lock, ruff, import-linter, mypy, bandit, format
# On the dedicated test server only:
make test-unit
make test-db-up && make test-integration && make test-db-down
                  # Postgres RLS canaries — required for anything touching
                  # models, migrations, guards, or tenancy
```

All relevant gates must be green before push. `TEST_DB_PORT` is
`?=`-overridable if the disposable test-server port is taken.

## Test-first expectations

- Write the test before the behavior. For anything tenancy-affecting, the
  cross-tenant isolation canary comes FIRST (two tenants; B must get
  404/empty for A's rows even with the exact UUID) — copy the pattern in
  `tests/test_cross_tenant_isolation.py`.
- A new governance/architecture test needs a sensitivity proof: temporarily
  introduce the violation, watch the test go RED, revert.
- Unit tests (SQLite) never prove tenancy. RLS correctness only counts on
  Postgres (`make test-integration`).

## Migration discipline

- The SAME migration that creates a table creates its RLS
  (`ENABLE` + `FORCE` + policy) and its grants. No "add tenant_id later".
- Platform catalog tables (no `tenant_id`): GRANT `platform_api`/`app_admin`
  only, REVOKE from `app_user` — the catalog audit
  (`tests/test_rls_catalog.py`) fails otherwise.
- Migrations run as `app_admin` (`MIGRATION_DATABASE_URL`), never on
  container boot; `scripts/deploy.sh` is the only production migration path.
  New revisions stay backward-compatible with the previous release
  (deploy rolls the app back, not the schema).

## Adding things (pointers, not duplicates)

- **A feature package** — README § "Starting a project from this template"
  and `CLAUDE.md` § Extension points: package + `feature.py`, register in
  `FEATURE_MODULES`, add to the import-linter independence contract,
  isolation test first.
- **A setting** — declare a `SettingSpec` in your feature's spec module and
  wire a real `resolve_value(...)` reader before shipping
  (`tests/architecture/test_no_orphan_settings.py` fails otherwise).
- **A custom-fields entity** — register the model in
  `app/features/custom_fields/registry.py::ENTITY_MODELS` and give it a
  `custom_fields` JSONB column (see `Party.custom_fields`).
- **A model** — placement rule and provenance table:
  `CLAUDE.md` § Model placement rule and `docs/ARCHITECTURE.md`'s model
  provenance + ownership tables (every model/resource names its owner).
- **A config knob** — overridable with a documented default
  (`Settings` + `.env.example`); prod-unsafe defaults go in
  `validate_settings`'s prod-fatal list. Never hardcode ports, hosts,
  image names, or paths.

## Pull requests

- Small and reviewable — one coherent slice per PR, not a grab-bag.
- CI must be green before merge (quality matrix, unit, Postgres
  integration, docker-build health gate — `.github/workflows/ci.yml`).
- Breaking changes are called out in the PR body and CHANGELOG entry.
- Docs move with the code: update `docs/ARCHITECTURE.md`'s tables and any
  affected ADR (dated amendment, never rewritten history) in the same PR.
