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

1. **Adapters are thin.** `router.py`/`web.py` never issue direct DB queries
   (no `db.query(`, `db.execute(`, `select(`) — logic lives in `service.py`.
   Adapters validate, authorize, delegate and render; a DECISION belongs to one
   service. This repo identifies an adapter by FILENAME, which is why the check
   is a three-line `rglob` — that naming convention is load-bearing, not
   cosmetic. ADR-0010 makes the rule fleet-wide and says why a repository
   without such a convention cannot enforce it: `dotmac_erp` has 1223 direct
   queries in 83 web modules living inside `app/services/`, where a
   directory-scoped check passes while missing 96% of them.
   (`tests/architecture/test_thin_wrappers.py`; ADR-0010)
2. **Timestamps render only through the `local_datetime`/`local_date` Jinja
   filters** — never a raw `*_at` attribute in a template.
   (`tests/architecture/test_web_conventions.py::test_timestamp_renders_go_through_local_filters`)
3. **Every mounted route carries a `require_*` guard** (route- or
   router-level), or sits in the commented `ALLOWLIST`. Mutating routes
   need a guard from the explicit `AUTH_GUARD_NAMES` set
   (`require_user_auth`, `require_role`, `require_permission`,
   `require_web_auth`, `require_platform_admin` — `require_tenant` alone does
   NOT count) or a
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
12. **Manifest declarations are unique, referenced, and consumed.** FIVE
    vocabularies work this way — permissions, capabilities, audit actions,
    feature flags, setting domains — and a vocabulary whose members belong to
    modules must be a SIXTH declaration registry, never an enum or a fixed
    list. ADR-0008 is a FLEET-WIDE standard: it binds every Dotmac
    repository, not only this one, and applies wherever one layer holds a
    vocabulary another layer owns members of. Each member is declared by
    exactly ONE module's manifest; a consumer may only reference a DECLARED
    member (`require_permission` refuses the boot, `require_capability`,
    `write_audit_event`, `resolve_flag` and the settings write path refuse
    the operation); every declared member needs a real consumer outside its
    own `feature.py`; and the backing DB column stays a plain string, since
    a CHECK constraint would re-close the list and cost a kernel migration
    per consuming product. Orphan allowlists are EMPTY and may only shrink.
    (`tests/architecture/test_manifest_declarations.py`;
    `tests/unit/test_permissions.py`, `tests/unit/test_audit_actions.py`)
13. **Migration discipline.** Migrations run as `app_admin`
    (`MIGRATION_DATABASE_URL`), never on container boot — the Dockerfile
    `CMD` only runs `uvicorn`; `scripts/deploy.sh` is the only place
    migrations run in production. The same migration that creates a table
    creates its RLS and grants.
14. **One namespace and one migration lineage per stateful module**
    (ADR-0006 D1). A STATEFUL module declares `short_code` +
    `migration_prefix` on its `ModuleManifest`, both allocated in
    `dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER`; its schema is the
    derived `mod_<short_code>`, never inferred from a display name and never
    re-pointed. A STATELESS module declares neither. `public` stays the
    compatibility namespace of the kernel + this one host assembly and is
    NOT available to installable modules. Module models, migrations, FKs,
    policies and raw SQL fully qualify their schema — never `search_path`.
    Revision ids are `<prefix>_<sequence>_<slug>` and must fit
    `alembic_version.version_num`'s VARCHAR(32); each module lineage has its
    own base and branch label, and cross-lineage ordering uses `depends_on`,
    never `down_revision`. The composed gate (`make migration-gate`, also in
    `make check` and in CI *before* `docker-build`) rejects duplicate
    revisions, prefixes, branch labels, schema claims and table ownership.
    (`tests/unit/test_namespaces.py`, `tests/unit/test_migration_gate.py`,
    `tests/unit/test_live_catalog_contract.py`;
    `tests/test_module_schema_catalog.py` is the post-migration live-catalog
    gate on Postgres.)
15. **Cross-repository engineering governance is pinned and required.**
    `.dotmac/standards-profile.json` names this repository's declared authority
    and fully typed contract surfaces, and pins the accepted Governance source
    by exact commit. The `Dotmac engineering standards` CI job must execute the
    Governance action at that same commit; a mutable tag/branch, copied policy,
    candidate mode, missing job, or source/profile revision mismatch is not an
    admissible substitute. Product CI is evidence only for the product revision
    it evaluated. (`.github/workflows/engineering-standards.yml`; Governance
    ADR 0006 in `michaelayoade/dotmac_governance`)
16. **The design system is consumed through its published surface only.**
    The assembly imports only names in `dotmac_ui.__all__` or in a
    `SUPPORTED_MODULES` module's `__all__`; `dotmac_ui` itself imports no
    kernel, no assembly, no web framework, no ORM, and no templating engine,
    and declares no runtime dependency beyond `python`. Design tokens are
    named by ROLE, never by value (`--dmui-action-destructive-hover`, never
    `--teal`), and no `.dmui-*` component class ships without being declared
    in `PUBLISHED_COMPONENT_CLASSES` + the package's `COMPATIBILITY.md`
    (ADR-0006 § 5 — a component is extracted only with the same contract, a
    named owner, and a migration path, never because two templates look
    alike). (`tests/architecture/test_ui_public_surface.py`; `pyproject.toml`
    contracts "Kernel must not import the UI package" and "UI package must
    not import the kernel or the assembly")
17. **Every cache key carries its scope, built by `dotmac_kernel.cache`.**
    Scope is a type (`TenantScope`/`PlatformScope`), never a nullable
    `tenant_id`: a `None` tenant is indistinguishable from a forgotten one, and
    the platform entry becomes the bucket every unscoped read lands in. No
    module builds a key by interpolation, and no `@lru_cache` decorates a
    function taking tenant-bearing input — a process-wide memo over tenant data
    serves one tenant's value to every other.
    (`tests/architecture/test_cache_scope.py`)
18. **A feature flag is not a permission and not an entitlement.** Flag codes
    are disjoint from permission and capability codes; every declared flag has
    an owning module and a real consumer; an expired flag fails the build, never
    production. (`tests/architecture/test_feature_flags.py`)
19. **`dotmac-ui`'s compiled assets are COMMITTED and match their token
    source.** `packages/dotmac-ui/src/dotmac_ui/static/**` is the published
    contract, not a build artifact — never gitignore it, never hand-edit it;
    regenerate with `make ui-build` and commit the diff. The stylesheet stays
    self-contained (no `@import`, CDN, remote origin, or `@font-face`) and
    preprocessor-free (no `@tailwind`/`@apply`/`@theme`/`@layer`), so a
    consumer on any Tailwind major — or none — links it as-is (ADR-0006 D3).
    (`make ui-check`, wired into `make check`;
    `tests/unit/test_dotmac_ui_tokens.py`)
20. **A secret is HELD, never dereferenced.** Nothing on the settings
    resolution path reaches a network — not for a value, not for a key. A
    value that cannot be held is not a setting: if it must live in a secret
    store, the product reads it and installs it via
    `dotmac_kernel.secret_sources.SecretSource` (named material) or
    `dotmac_kernel.settings_crypto.KeyProvider` (encryption keys), or seeds it
    as a real setting. A row whose value merely LOOKS like a reference
    (`bao://...`) resolves to that string; the kernel does not recognise the
    scheme. Both seams load once at install, rotate on an explicit refresh,
    keep the working set when a refresh fails, raise rather than start
    degraded, and never log, repr or quote a value — only names (ADR-0009).
    (`tests/unit/test_secret_sources_no_network.py`,
    `tests/architecture/test_secrets_are_held.py`)

21. **Settings resolution reads rows and defaults — never the environment.**
    `env_var` is a declaration whose only consumer is `seed_settings_from_env`,
    which runs once at startup and never overwrites an existing row. Precedence
    is `scope chain -> spec default`; the environment does not appear, because
    it is a loader that produces a row rather than a source that competes with
    one. A value in effect is therefore always one an operator can see and
    change (ADR-0011).
    (`tests/unit/test_settings_resolution_ignores_env.py`,
    `tests/architecture/test_settings_env_is_bootstrap_only.py`)

## Everything by config — no hardcoding

Env-specific values are overridable variables with documented defaults,
never literals: new knobs go in `Settings` + `.env.example`; Make vars use
`?=`; compose files use `${VAR:-default}`; `scripts/deploy.sh` falls back
via `: "${VAR:=default}"`. New secrets/knobs that must not keep a dev
default in production are added to `validate_settings`'s prod-fatal list.

## Validation before any commit

- `make check` — ruff lint, import-linter, mypy, bandit, the composed
  migration gate (ADR-0006 D1), format check.
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
