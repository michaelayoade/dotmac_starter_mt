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
- `docs/CONTROL_EXCEPTIONS.md` — controls that were bypassed, with cost and
  remediation. Append-only: an entry is never deleted, only its remediation
  state changes.

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
12. **Manifest declarations are unique, referenced, and consumed.** NINE
    vocabularies work this way — permissions, capabilities, audit actions,
    outbox event types, feature flags, setting domains, provisioning
    participants, charge models and obligation sources — and every later
    vocabulary whose members belong to modules must be another declaration
    registry, never an enum or a fixed list. ADR-0008 is a FLEET-WIDE standard:
    it binds every Dotmac
    repository, not only this one, and applies wherever one layer holds a
    vocabulary another layer owns members of. Each member is declared by
    exactly ONE module's manifest; a consumer may only reference a DECLARED
    member (`require_permission` refuses the boot, `require_capability`,
    `write_audit_event`, `resolve_flag`, the settings write path,
    Fulfillment's `ParticipantRegistry`, and Subscriptions'
    `SubscriptionVocabularyRegistry` refuse the operation); every declared
    member needs a real consumer outside its own `feature.py`; and the backing
    DB column stays a plain string, since
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
    own base and branch label, and `down_revision` never crosses lineages.
    **Cross-lineage ordering is LOGICAL, not physical** (ADR-0006 D1
    amendment, 2026-08-13): a module declares the database EFFECTS it needs
    (`ModuleManifest.requires`), the supplying lineage declares
    `MigrationOwner.provides`, and each ASSEMBLY binds requirement to provider
    revision (`app/migration_bindings.py`, installed from `alembic/env.py`).
    `resolve_depends_on` turns that binding back into a real `depends_on` edge
    at script load, so Alembic ordering is unchanged — but a module lineage
    may **never** name a foreign revision itself, because that edge is true
    only in the assembly that wrote it. Host owners (`kernel`, `assembly`)
    keep literal edges. A binding is a claim, so it is checked two ways:
    the composed gate (`make migration-gate`, also in `make check` and in CI
    *before* `docker-build`) rejects duplicate revisions, prefixes, branch
    labels, schema claims, table ownership, unbound requirements, bindings to
    a lineage that never declared the effect, bindings to an uncomposed
    revision, and migration/manifest drift; and `require_prerequisites`
    verifies the real catalog before any DDL, so a STAMPED provider fails.
    Ordering needs no third check — Alembic enforces the resolved edge, and
    `alembic_version` records branch HEADS, not applied history, so asserting
    a root revision appears there is simply wrong. `alembic stamp`, a blanket
    `IF EXISTS`, and a product conditional inside a kernel migration are not
    bindings and stay forbidden.
    **Allocation is SERIALIZED** (2026-08-21): a module's ledger row is merged
    to `main` as an allocation-only change BEFORE any of its source is
    written. Uniqueness is a property of the canonical ledger, and no check a
    branch runs on itself can see a sibling branch — three unmerged trains
    (`sales`, `support_access`, `service_access_policy`) each allocated
    `prefix="sa"` and every one of them was green. `make allocation-gate`
    (its own CI job, needing `fetch-depth: 0`; deliberately NOT in `make
    check`, which stays offline-runnable) fails a branch that changes
    `packages/<pkg>/src/**` for a module with no ledger row at the MERGE BASE,
    and exits 2 rather than passing when it cannot establish an answer.
    What is gated comes from each package's `EXTRACTION.toml`
    `classification` read AT THE BRANCH HEAD — `optional-module` is gated;
    `stateless-protocol-adapter`, `presentation-foundation` and
    `universal-facility` legitimately own no lineage — never from a directory
    name or from "does it have a manifest", both of which fail open. A missing,
    unreadable or unknown classification FAILS. A genuinely stateless manifest
    (no `short_code`/`migration_prefix`) needs no row. Deleting a module
    passes: its row is a PERMANENT reservation, never reclaimed, so a retired
    prefix can never be handed to a new owner and collide with rows still live
    in a deployed database — a rename is a deletion plus an addition, and the
    added side is gated normally. The in-tree companion,
    `test_every_module_packages_full_allocation_matches_the_ledger`, uses the
    same classification and the same AST parser to check the COMPLETE
    allocation — schema, prefix AND branch label — resolving a row by `code`
    or, if the code was renamed, by `migration_branch` against the immutable
    `branch_label`.
    (`tests/unit/test_namespaces.py`, `tests/unit/test_migration_gate.py`,
    `tests/unit/test_prerequisites.py`,
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
    which runs idempotently on every application-process start and never
    overwrites an existing row — so changing the variable later does NOT
    update or rotate the setting; the settings owner is the only way in after
    first creation. Precedence is
    `scope chain -> assembly profile default -> spec fallback` (the profile
    level is ADR-0013); the environment does not appear at any rank, because it
    is a loader that produces a row rather than a source that competes with
    one. A value in effect is therefore always one an operator can see and
    change (ADR-0011, amended 2026-08-20).
    (`tests/unit/test_settings_resolution_ignores_env.py`,
    `tests/architecture/test_settings_env_is_bootstrap_only.py`)

22. **A setting declares whether it inherits.** `SettingSpec.inherits`
    defaults to `True` (walk the scope chain, then the spec default) and is set
    `False` for a value that IDENTIFIES something owned by one scope — a ledger
    or bank account, a warehouse, an external system's id. A fallback claims a
    less-specific value answers the question; for an identifier it does not.
    Pair with `required_at` for "must be set here, no fallback, fail loudly".
    Honoured by single-key AND bulk reads (ADR-0012).
    (`tests/unit/test_setting_inherits.py`)

23. **At-most-once execution has ONE owner.** `dotmac_kernel.idempotency`
    owns the ledger, the engine, the conflict rule and the retention sweep;
    `messaging.process_once`/`process_once_platform` are adapters over it, not
    a second mechanism. A scope names the OPERATION, never an HTTP route. The
    effect and its ledger row commit in the SAME transaction — nothing is
    reserved before the effect, so a crashed attempt leaves no marker and the
    retry re-drives, and there is no "in progress" state to get stuck in. The
    request fingerprint is its own column and is never overloaded onto a result
    id; a key reused with a different fingerprint is a conflict, not a replay.
    Retention is the product's policy: `expires_at` is nullable and the kernel
    sets no default TTL. Non-transactional effects belong in the outbox
    instead (ADR-0014).
    (`tests/unit/test_idempotency.py`)

24. **Shared capabilities are extracted product-first, not rebuilt beside a
    mature product implementation.** Before adding kernel/module behaviour,
    inventory ERP, Sub, and every product named in the candidate scope. A
    qualifying production-used, tested implementation is the mandatory
    reference and initial code source; port its behaviour and parity tests,
    generalising only at typed product seams. Every distribution under
    `packages/` carries `EXTRACTION.toml` with its owner, contract, source
    paths/tests, consumers, first cutover, drift proof, and local-copy
    retirement gate. A greenfield shared implementation requires checked-in
    evidence that no qualifying product implementation exists. Copying is a
    one-time extraction: no permanent fork, parallel writer, or second owner.
    (`tests/architecture/test_product_first_extraction.py`; ADR-0006
    § "Decision amendment — 2026-08-08 (product-first extraction)")
25. **A guard exemption states an enforceable premise, or it is not an
    exemption.** Excluding a path from a lint, type, architecture or security
    check requires a premise that is machine-checkable in the same change; an
    unverifiable premise makes the region unmonitored, not exempt. Guards
    enumerate ENTRY-POINT FAMILIES (tasks, scripts, CLI, workers, cron), never
    a single directory — where a guard's docstring claims broader scope than
    its configuration, the configuration is the defect. An existing backlog is
    retired by a TWO-DIRECTIONAL ratchet that fails when the count rises OR
    falls without being lowered, kept distinct from any per-line
    "reviewed and correct" marker, and carrying a sensitivity proof that the
    detector still fires. (ADR-0018; `dotmac_erp` reference implementation:
    `scripts/check_session_context.py` + `session_context_legacy.txt`)

26. **Starter-owned templates author colour against `var(--dmui-*)`, never a
    literal palette.** A hardcoded Tailwind palette utility (`bg-slate-700`,
    `text-primary-600`, `text-white`) or a raw hex/`rgb()` literal in any
    template under `packages/*/src/*/templates` is frozen debt: the existing
    851 occurrences across 29 files are recorded per-file and per-token in
    `tests/architecture/palette_debt_baseline.json`, and the ratchet fails when
    that inventory rises OR falls. A slice that genuinely retires palette usage
    lowers the baseline in the SAME change (`make palette-baseline`) so the
    reduction is reviewable as a diff. The document canvas, tenant login,
    authenticated admin shell, `platform/**`, `layouts/platform.html` and
    Template Studio are token-native and are asserted at zero against the live
    scan, so a regenerated baseline cannot legalise a regression there. (Rule
    25's ratchet shape applied to the design system; `test_palette_ratchet.py`)

27. **A dual-plane module has ONE behaviour and TWO DECLARED persistence
    planes.** A capability that genuinely operates in both security contexts —
    a tenant data plane and the control plane — ships one lifecycle, vocabulary
    and transition engine, and two storage planes declared separately on its
    manifest: `tables` (tenant: `tenant_id NOT NULL`, RLS ENABLEd *and* FORCEd,
    composite uniques) and `platform_tables` (control plane: no tenant column,
    no RLS at all — not even ENABLEd-with-no-policy, which denies every row to
    the control plane while reading as protected — GRANTed to the platform roles
    and **REVOKEd from the tenant app role** across ALL SEVEN table privileges
    and their column-level forms; on that plane the revoke IS the isolation, and
    it is checked as strictly as a policy is on the other side). A platform
    table must also be REACHABLE by the online platform role: that role needs
    schema `USAGE` plus at least one row DML privilege (`SELECT`, `INSERT`,
    `UPDATE`, `DELETE`); `REFERENCES`, `TRIGGER` or `TRUNCATE` alone does not
    make a request path usable. Declared-and-unusable is a violation too. The
    plane is DECLARED, never inferred from a missing `tenant_id`, or a table
    that merely forgot the
    column would reclassify itself and lose isolation silently; a table may
    appear in exactly one plane. **No foreign key crosses the planes** — they
    share a lifecycle, never a row. The gate enforces that for FKs whose SOURCE
    is in the module schema; a product-owned link table in `public` is
    UNMONITORED rather than exempt, which is why a dual-plane module ships one
    link helper PER PLANE and each refuses an unusable configuration. Nullable
    `tenant_id`, sentinel/fake tenants and polymorphic scope columns are
    rejected and refused by the gate. Two
    planes require a real named assembly on each side TODAY; most modules are
    tenant-only and must stay that way. A module whose one lineage can install
    plane subsets declares every supported combination; its assembly MUST make
    one explicit per-module `ModulePlaneSelection`. Prerequisite bindings only
    name providers and NEVER select a plane — a provider may truthfully exist
    while the product intentionally excludes that plane. Missing, duplicate,
    unknown or unsupported selections fail the static composition; migrations
    and the live-catalog gate consume the same selection (ADR-0028). (ADR-0023;
    `tests/unit/test_live_catalog_contract.py`,
    `tests/unit/test_namespaces.py`,
    `tests/architecture/test_ticketing_module.py`)

28. **Applications are independent; they compose by synchronizing DATA.**
    Every application owns its runtime, database, migrations, sessions,
    authorization and domain decisions. Cross-application integration is only
    through versioned APIs/webhooks: an adapter records a typed, deduplicated
    observation, then a local resolver updates a rebuildable projection or asks
    the local owning service to act. An importer never assigns an authoritative
    status, permission, entitlement or lifecycle field directly, and no app
    reads another app's database, tables, ORM models or filesystem. An
    installable module is also independent, but is composed LOCALLY: each app
    pins its own copy, runs its own lineage and owns its own rows; the module
    imports neither its assembly nor a sibling module. Shared execution paths
    contain no product/provider branches or mode flags. Products declare only
    provider-neutral domain ports and capability versions; provider identity,
    wire mappings, endpoints and secret references stay in Integrator connector
    plugins. Outbound synchronization leaves through a durable outbox after the
    local transaction. Configuration binds a transport capability, never the
    business owner. The independently deployed Dotmac Integrator is the sole
    EXTERNAL connector control plane, but deployment is not code location:
    Starter's stateful `dotmac-integration` module owns the registry,
    installations, bindings, secret references, inbox/outbox, retries,
    checkpoints, health, repair and its `mod_*` lineage; the separate
    `dotmac_integrator` repository is a thin assembly that pins kernel, that
    module and connector distributions and runs them. It never implements a
    second engine. Products do not each compose the module, and they carry no
    provider clients,
    provider credentials, provider webhook verification, connector schedules,
    checkpoints or delivery retry engines. Products expose typed domain ports;
    Integrator owns transport evidence and never writes product domain tables.
    External systems are independently released connector PLUGINS discovered
    from package metadata through one versioned SPI; the `dotmac-integration`
    module and thin assembly have no provider enum/import list/conditional.
    Plugins declare versioned
    capabilities and config schemas, translate wire formats and perform I/O,
    while the Starter module exclusively owns bindings, secret materialization,
    inbox/outbox, idempotency, retry, checkpoints, health and repair evidence.
    One installation has exactly one active plugin per capability; duplicate or
    incompatible bindings fail closed.
    A remote projection requires a named local reader and reconciler; foreign-key
    compatibility alone is not a reason to keep one. Correlation-only needs use
    an opaque Integrator reference on the local owning record.
    (ADR-0024; import-linter contracts `Modules must not import the assembly`
    and `Modules are independent of each other`; ADR-0010/0014)

    **Outbound clauses added 2026-08-24 — REVIEW DISCIPLINE, not guards.**
    Stated here because rule 25 forbids implying enforcement that does not
    exist: no check in this repository catches any of the following today, and
    the missing machinery is named in ADR-0024's "Enforcement and evidence"
    additions.
    *(a)* **One capability, one contract, one payload.**
    `payments.payout.v1`, `messaging.send.v1` and every other id name a
    BUSINESS ACT, not a provider endpoint. No provider-named id
    (`payments.payout.paystack.v1`), no provider-shaped sibling for an act that
    already has a contract (`payments.transfer.v1`), and no per-connector
    command dialect behind a shared id — that last one is the branch relocated
    into whichever product builds the payload.
    *(b)* **A provider branch in a product is one of six concrete things:** an
    `if provider == …`/provider enum/provider-keyed behaviour dict; a provider
    SDK import or hand-written provider HTTP client; a provider credential in
    product config, env, settings rows or a path the product dereferences; a
    provider-named route/task/queue/column/setting/flag/table; a provider
    string inside a business decision (status mapping, currency scale, retry
    eligibility); or a "which provider is configured?" read on a request path.
    Each belongs in the connector distribution or the Integrator binding.
    *(c)* **Connector completeness is Dotmac capability parity** — every
    capability the ecosystem needs, with interchangeable providers behind the
    ones that matter — never coverage of a provider's published surface. A
    withheld surface is DECLARED in the connector's `EXTRACTION.toml`, not
    merely absent (LinkedIn outbound, Mono writes and Flutterwave transfers are
    the three in force).
    *(d)* **A payout is ERP's decision.** Whether it happens, to whom, for how
    much, and whether an ambiguous attempt may be retried are ERP's Treasury
    owner's calls; no connector, engine path, configuration or operator gesture
    decides them.
    *(e)* **Modules own metric DEFINITIONS; assemblies own EXPORTERS.** A shared
    module declares stable, namespaced metric names and derives values from its
    own facts at read time; it ships no metrics client, counter registry or
    `/metrics` route, and no second observability path.
    (ADR-0061; ADR-0062; ADR-0024 §§ 8–9)

29. **Poetry is an exact build input, not a workstation preference.**
    `[tool.poetry].requires-poetry` is the ONE version source; CI's hash-locked
    bootstrap, the lockfile generator stamp and the production Docker build
    must equal it exactly. A local command running another Poetry version fails
    before it may inspect or rewrite the lock. Validate the COMMITTED lock with
    `poetry check --lock`/`poetry install`; never run `poetry lock` inside a
    validation lane, because that proves repaired state rather than the commit.
    A dependency change and its lock change are one commit. Use ordinary
    `poetry lock` with the pinned tool and review the diff; `--regenerate` is
    reserved for an explicit dependency-upgrade slice. Root-lock validation is
    supplemented by the bidirectional path-package guard because Poetry does
    not re-read nested package metadata for `poetry check --lock`.
    (`scripts/check_poetry_toolchain.py`; `make poetry-lock-check`;
    `tests/architecture/test_poetry_toolchain_contract.py`;
    `tests/architecture/test_lockfile_path_packages.py`)

30. **A repository-local claim comes from repository-local facts; a release,
    registry or production-adoption claim needs an authoritative external
    oracle.** Accepted fleet-wide as `dotmac_governance` ADR 0013 (merge
    `2d711cd594979ba0bc368382b7f5ea69bf21eaa4`, effective 2026-08-22). A
    version existing in `pyproject.toml`, on `main`, or in a CHANGELOG is not
    evidence that it is published, installable or pinnable. Four typed oracles,
    each carrying immutable coordinates: `release_run` (the run that published →
    installed back from the private index → registered → tagged), `peeled_tag`
    (the tag's PEELED commit, never the annotated tag object's SHA),
    `deployment_run` (run, commit, image digest, explicitly named target) and
    `adoption_evidence` (dossier repository, exact commit, path, field). A
    branch name, "current `main`", "latest", an unpeeled tag, a run without its
    id, or an image by tag rather than digest are not coordinates. A positive
    oracle-backed claim is permanent; an ABSENCE is a moment, so record it
    either as an as-of observation with a NAMED refresh owner, or replace it
    with a repository-local positive fact — prefer the second.
    **Enforcement is deliberately narrow.** It is automated only where a
    machine-readable contract already carries a declared oracle: every declared
    distribution is measured against its real tag, and a declared-unpublished
    one must carry a recorded reason that is removed in the SAME change as its
    release. Everywhere else this is stated review discipline, NOT a guard —
    ADR 0013 rejects a generic prose scanner, which cannot separate a claim
    from a description of one and would flag its own incident recital. Saying
    so is the point: an unmonitored region is honest, an implied guard is not
    (ADR-0018). The known-bad case any future automation must fail on is
    `AWAITING_RELEASE_TAG` in `dotmac_vendor_control_plane`, which was NAMED
    for a release tag, read only `pyproject.toml`, and stayed green after the
    tag was published.
    (`scripts/declared_publication_sweep.py`;
    `tests/architecture/test_declared_publication.py`)

31. **Protected-environment approval is NON-DELEGABLE, and chat authorization
    does not transfer it.** A `registry-release` (or any protected-environment)
    approval is a control that exists to put a human between an authenticated
    credential and an irreversible publication. An agent holding a credential
    that CAN approve defeats it — the approval record then names a person who
    did not perform the action, which is the one fact the record exists to
    establish.

    "Michael said approve it in chat" is NOT equivalent. The gate's premise is
    that approval happens at the gate, by the reviewer, against the tree the
    gate is showing. Chat authorization is given earlier, against a different
    tree, and cannot be re-checked at the moment of publication — the failed
    a91 attempt (`32596599849`) is the proof: main moved between the
    authorization and the gate, and only the gate's own re-check caught it.

    **An agent stops at the gate.** It dispatches the run, waits, and hands
    over the approval URL. It does not call
    `POST /actions/runs/{id}/pending_deployments`, whatever it has been told
    in conversation. The technical backstop is that the agent-accessible
    credential must lack the permission to approve deployments; until that is
    in place this rule is discipline, and discipline is the weaker half.

    Exception on record: `docs/CONTROL_EXCEPTIONS.md`, 2026-08-22,
    kernel `0.1.0a91`.

32. **A release holds a short, named freeze.** `publish` re-asserts that the
    run's SHA is still the tip of protected `main`, and that check runs AFTER
    the approval wait — so any merge between dispatch and verification voids
    the run. One release captain holds merges from dispatch through
    verification and tagging, and announces the window opening and closing.

    This is a real control, not ceremony: the first a91 attempt was voided
    mid-flight by a merge landing during the approval wait, and it stopped
    BEFORE publication. Keep the check; the freeze is what stops it from
    costing a wasted build every time.
    (`scripts/assert_current_main.sh`)

33. **A writer claim is TYPED, and the prose channel only shrinks.**
    `[[product_writers]]` in a dossier states, per product, whether it is the
    `qualifying_source`, a `legacy_writer` that must stop, a `no_writer`, or
    `inventory_only` — with an immutable revision and evidence paths. Governance
    cites these across the repository boundary, and two rationales were once
    contradicted by the dossiers they described because the cited claim was
    prose.

    The block may be ABSENT, deliberately: silence must stay distinguishable
    from a claim of absence, so a consumer that cannot find its row fails as
    UNKNOWN. That makes #354 migration support, not enforcement — which is what
    this rule adds. `product-writer-baseline.json` freezes the prose-only
    dossier/product pairs (303 across 86 dossiers at the freeze; one dossier
    fully typed) as a TWO-DIRECTIONAL ratchet: it may not grow, and it may not
    shrink without being regenerated in the same change. A dossier absent from
    the baseline must be complete, which is what stops the debt growing with
    the package count.

    Retire a pair by READING the source product and recording what you found.
    Never by inferring a state from the prose already there — a scanner
    guessing `no_writer` from a sentence manufactures the false confidence this
    exists to remove.
    (`scripts/product_writer_sweep.py`; `make product-writer-check`;
    `tests/architecture/test_product_writer_ratchet.py`)

## Everything by config — no hardcoding

Env-specific values are overridable variables with documented defaults,
never literals: new knobs go in `Settings` + `.env.example`; Make vars use
`?=`; compose files use `${VAR:-default}`; `scripts/deploy.sh` falls back
via `: "${VAR:=default}"`. New secrets/knobs that must not keep a dev
default in production are added to `validate_settings`'s prod-fatal list.

## Validation before any commit

**Test host — hard rule.** Never run test commands or install test/development
dependencies on the local workstation. Run every focused, unit, architecture,
integration, migration, browser, and full-suite test on the Dotmac Observer
server (SSH alias `observe`) in a fresh isolated writable Git worktree pinned
to the exact branch commit under test; a shared checkout is not test evidence.
Use only disposable test databases and tear them down after the run. Never use
`pytest -n auto` on Observer; cap xdist at `-n 2` or `-n 3` to avoid exhausting
the host. Local work is limited to read-only inspection, editing, formatting,
and static checks that do not execute tests or install dependencies. Git-hosted
CI remains the merge acceptance owner.

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
- **New kernel facility or shared module:** complete the product inventory and
  `EXTRACTION.toml` dossier before implementation. Start from the qualifying
  ERP/Sub implementation and its tests when one exists; an unresolved audit is
  a stop condition, not permission to greenfield the shared version.
- Zero-consumer code is deleted, not kept. Every new concept gets its owner
  row in `docs/ARCHITECTURE.md`'s provenance/ownership tables.
- **Publishing writes a RECORD, not just a tag.** A tag makes that
  distribution's `declared-publication-baseline.json` row false immediately,
  and its released migrations become bytes that must not change, so five gates
  fail from the instant of the tag until both are recorded. The release
  workflows now open that record themselves
  (`scripts/open_release_record_pr.sh` calling
  `scripts/write_release_record.py`, straight after tagging) — merge that pull
  request as soon as it is green. Run the writer by hand only to repair an
  older gap; never edit a declared version down to make the gates agree.
