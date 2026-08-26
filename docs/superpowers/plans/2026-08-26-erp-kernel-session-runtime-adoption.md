# Dotmac ERP — adopting the kernel session/tenant runtime and ratcheting the organization GUC to zero

> **Status:** Proposed implementation plan, 2026-08-26. This is non-authoritative
> execution intent. Per this repository's docs hierarchy, `docs/ARCHITECTURE.md`
> is as-built truth and `docs/adr/` carries decisions with their status; in the
> target repository, `docs/SOT_RELATIONSHIP_MAP.md`, its executable registry,
> `docs/PLATFORM_ADOPTION_LEDGER.md` and the checked-in architecture tests
> govern. The plan authorizes no code, schema, pin, release or production change
> by itself.
>
> **Target repository:** `dotmac_erp` for steps S2b–S6. S1 and S2 are kernel
> work in `dotmac_starter_mt`; they are AUTHORED but not published, and they
> appear here as prerequisites with their own release gates rather than as
> finished context. No step from S2b onward may start before its kernel rung
> has a release oracle.
>
> **Decision source:** ADR-0066 (the database runtime is a facility products
> instantiate, not a singleton they share), extending ADR-0006's product-first
> extraction amendment (`AGENTS.md` rule 22) and ADR-0024 § 2.
>
> **Evidence basis:** kernel side, `dotmac_starter_mt` PR #451 branch
> `feat/kernel-session-runtime` (open, unmerged, unpublished) —
> `packages/dotmac-kernel/src/dotmac_kernel/session_runtime.py`, ADR-0066,
> `COMPATIBILITY.md` § "Owning your own database runtime", `CHANGELOG.md`
> 0.1.0a100. ERP side, seams verified 2026-08-26 on branch
> `fix/retire-external-ticket-runtime`, scope files read at commit
> `24dc218afae60cb6ec41fce641fe99512984767b`. Every ERP fact below is from that
> read; nothing here re-derives ERP behaviour from memory, and an execution
> session must re-verify against the revision it actually targets.
>
> **Relationship to earlier plans:** this is the database-runtime slice of
> `2026-08-02-dotmac-erp-kernel-improvements.md` § E8. It does not replace E8's
> Organization-to-Tenant ADR; it removes the half of the blockage that was the
> kernel's own doing, so that E8 is left arguing about tenancy identity rather
> than about whether the kernel's session discipline is reachable at all.

## Outcome

ERP ends this work with ONE transaction authority — an instance of
`dotmac_kernel.session_runtime.DatabaseRuntime` that ERP builds from its own
engine, its own credentials and its own `Organization` lookup — one writer of
the RLS tenant settings, and an empty `legacy_tenant_settings`. The organization
GUC is not renamed, wrapped or aliased on the way there; it is primed alongside
the canonical setting while tables move onto composed module lineages, and then
it stops being primed at all because nothing reads it.

The target flow is:

```text
ERP's own Settings (pool size, overflow, timeout, statement_timeout)
  -> ERP builds its Engine, exactly as it does today
  -> DatabaseRuntime(engine=..., tenant_lookup=<Organization resolver>,
                     legacy_tenant_settings=("app.current_organization_id",))
  -> ONE parameterized statement primes app.current_tenant AND the legacy name
  -> per-domain table moves onto module lineages shrink the legacy set
  -> legacy_tenant_settings == () and app.current_organization_id is gone
```

The value is not that ERP gains a feature. It is that ERP stops maintaining a
second copy of the scope discipline. `conflict_savepoint`'s F3 hazard,
`resolver_session`'s pooled-scope trap and the blind-not-loud failure that let a
`dotmac_academy_app` audit report a clean estate over 333 question banks are all
inherited rather than rediscovered. A second commit boundary a product wrote
itself is a second place each of those has to be found again.

## Decisions and non-negotiable boundaries

1. **ERP owns its engine.** `DatabaseRuntime.from_urls` deliberately exposes
   only DSNs and pool sizes; it does not expose ERP's `DB_POOL_TIMEOUT`,
   `DB_POOL_RECYCLE` or the `statement_timeout` in `connect_args`. ERP therefore
   uses the `__init__(engine=...)` path and keeps building its engine in
   `app/db/__init__.py` from `app/config.py`. That is the SUPPORTED shape, not a
   gap to be closed by growing knobs on `from_urls`: the classmethod is a
   convenience for deployments with no engine opinions, and ERP has several.
2. **`app.current_tenant` is not configurable and this plan never makes it so.**
   Every composed module lineage writes its policies as
   `tenant_id = public.app_current_tenant_id()`, which reads that exact setting,
   and `dotmac_kernel/migrations/verify.py` pins the semantics against the live
   catalog. A runtime priming some other name yields a database where every
   composed policy matches nothing — and RLS fails CLOSED, so the symptom is
   zero rows, not an error. Nothing raises; the operator reads an empty
   organization as an empty organization.
3. **`legacy_tenant_settings` ADDS, never replaces**, in one statement with one
   value. Two statements could fail between them and leave the canonical setting
   armed with a stale legacy one — a scope that reads as working, over the wrong
   rows.
4. **`Organization` remains ERP's tenancy key.** `app/tenancy.py`'s
   `OrganizationTenantContext` already holds `tenant_id == organization_id`,
   persistence-free and with no mapping table; it becomes the `tenant_lookup`
   callable unchanged. No column is renamed and no identity is migrated by this
   plan.
5. **`dotmac_kernel.db` stays prohibited in ERP's ledger, permanently and
   correctly.** It is the starter's instance, still eager, still built from the
   kernel's own `DATABASE_URL` at import. What becomes adoptable is
   `dotmac_kernel.session_runtime`, which is a different module with a different
   answer. Two ledger rows, not one row changing its mind.
6. **Async is out of scope.** `DatabaseRuntime` is sync-only. ERP's async
   engine, `async_sessionmaker` and `app/web/deps.py::get_async_db_for_org` stay
   ERP-owned through all six steps. This is stated so it is a named follow-up
   rather than a silent omission — see "Named follow-ups" below.
7. **Rule 26 gates the pin.** A version in `pyproject.toml` or on `main` is not
   evidence it is published or pinnable. ERP must not pin `0.1.0a100` until a
   protected release run has published and tagged it and an authoritative
   external oracle carries the immutable coordinates. The kernel's own
   `pyproject.toml` reading `0.1.0a100` on this branch is exactly the kind of
   repository-local fact rule 26 refuses as release evidence.
8. **Rule 22 is why this is repatriation, not import.** `tenant_scope` is ERP's
   own production implementation (`app/db/session_context.py::tenant_scope_for_session`
   over `app/rls.py`), ported at ERP commit `24dc218a…` and recorded with its
   sources in `packages/dotmac-kernel/EXTRACTION.toml`. ERP is adopting the
   mechanic it wrote, generalized. Deleting its local copy is the second half of
   the extraction, not a loss of authorship.

## Delivery sequence

Each step is a separate, reviewable change in the repository named.

**S1 and S2 are AUTHORED, not published.** Both are open starter PRs whose
kernel versions are declared and unreleased, and rule 26 refuses a declared
version as evidence of anything. They are ordered as one release train, and
each rung must be merged, published through the protected workflow and recorded
before the next is rebased onto it:

| Rung | PR | Kernel | State |
|---|---|---|---|
| S1 | #445 | `0.1.0a98` | open, declared-unpublished |
| — | #450 | `0.1.0a99` | open, declared-unpublished (facet-admission repair; positional, not a dependency of this plan) |
| S2 | #451 | `0.1.0a100` | open, declared-unpublished |

The ordering is release mechanics, not a dependency graph: a99 touches neither
a98 nor a100, and a100's `DatabaseRuntime` needs nothing from a98. What the
train buys is that no two branches claim one version and every rung has an
oracle before the next is cut.

S3 cannot start until a100 exists as a published, tagged artifact. Publication,
pinning, merging, staging and production work remain separately authorized.

### S1 — The public engine-free transaction and fingerprint surface — AUTHORED (kernel a98, PR #445)

**State:** open, unmerged, declared-unpublished. Nothing below is available to
ERP yet.

A caller-session kernel service must be able to open a SAVEPOINT without
entering the eager database owner. On `origin/main` it cannot: the mechanic
lives at the private `dotmac_kernel._transactions`, listed under
COMPATIBILITY.md's "Internal modules and names (do not import)", and the only
supported spelling is `dotmac_kernel.db.conflict_savepoint` — the module ERP's
ledger prohibits and whose import costs a `DATABASE_URL`. a98 publishes
`dotmac_kernel.transactions` as the supported engine-free spelling.

**`fingerprints` is in the same position, and the distinction matters.** Main
DOCUMENTS `dotmac_kernel.fingerprints` in COMPATIBILITY.md but does not list it
in `SUPPORTED_MODULES`, so by the kernel's own rule — a name is public only if
it is in a supported module's `__all__`, and the module is in
`SUPPORTED_MODULES` — it is not actually public. a98 registers it and
reconciles the two. Do not read the prose contract as evidence of the machine
one; that gap is the defect a98 closes, and a plan that treated `fingerprints`
as already released would be reasoning from the wrong artifact.

**The ERP consequence, which is a gate on S3 rather than a detail.** Four files
on ERP's `fix/retire-external-ticket-runtime` working tree
(`app/services/sync/dotmac_sub_sync_service.py`, `sub/base.py`,
`sub/expenses.py`, `sub/procurement.py`) already import
`dotmac_kernel.transactions`. No released kernel ships it, so those imports
cannot collect against the installed kernel and ERP's exact-pin test should be
failing there — as expected, since the work is uncommitted and anticipates a98.
That work cannot merge until a98 is published and ERP repins to the exact
tagged version. It must not be resolved by importing the private
`_transactions` name.

That branch also carries an internal inconsistency worth clearing first: ERP
`main` is consistently `0.1.0a94`, while the working tree has package and lock
at a94 and `KERNEL_PIN`/the adoption ledger at a96. Reconcile before S3.

**Why a98 and not a96.** The branch originally declared `0.1.0a96`, a version
already published from a different commit (the machine-attribution renumber).
Leaving it there would document a new public module under an already-released
changelog heading, so the entries were moved into their own a98 section on the
way in.

### S2 — The configurable kernel session/tenant runtime — AUTHORED (kernel a100, PR #451)

**State:** open, unmerged, declared-unpublished. ADR-0066 is accepted as a
decision; the code it describes is not yet an installable artifact, and this
plan must not be read as though it were.

`DatabaseRuntime` holds the engines, the session factories, the tenant scope and
every boundary (`request_session`, `platform_request_session`,
`platform_session`, `tenant_session`, `tenant_session_by_slug`,
`resolver_session`, `tenant_scope`). It constructs no engine at import, reads no
settings and imports no web framework. `dotmac_kernel.db` became one instance of
it, with every public name unchanged and still bound once, and stayed eager on
purpose — two package-root import guards can only distinguish a module-level
import of the owner from a deferred one because entering the owner costs a DSN.

The seam is framework-free deliberately: `request_session` takes a tenant id,
not a `Request`. The four lines that read tenancy off request state and carry
whatever annotation the framework needs stay in the product — which is precisely
what lets ERP's dependency-primed organization context adopt the transaction
discipline without adopting a router stack.

### S2b — ERP pin-only prerequisite — PIN ONLY, NO BEHAVIOUR CHANGE

**Purpose:** move ERP onto the published a100 kernel as a change that adopts
nothing, so that the adoption in S3 is reviewed on its own merits against a
pin that already works.

**Precondition:** a100 exists as a published, tagged artifact with an
authoritative external oracle carrying its immutable coordinates. Not a merged
PR, not a version in the starter's `pyproject.toml` — rule 26 refuses both.

Six artifacts move TOGETHER, in one commit, because any subset leaves a green
check over an untested combination:

| Artifact | Why it is in this set |
|---|---|
| `pyproject.toml` | what the resolver installs |
| `poetry.lock` | what is actually installed |
| `KERNEL_PIN` (`tests/architecture/test_kernel_compatibility.py`) | what the compatibility canaries assert against |
| `app/bill_of_materials.py` | what ERP reports it is running |
| `docs/PLATFORM_ADOPTION_LEDGER.md` | what the import allowlist is checked against |
| the exact-pin tests | the guard that the first three agree |

ERP `main` is consistently `0.1.0a94`. The
`fix/retire-external-ticket-runtime` working tree is NOT: package and lock at
a94, `KERNEL_PIN` and the ledger at a96. Its four
`dotmac_kernel.transactions` imports therefore cannot collect against the
installed kernel, and the exact-pin test should be failing there. That is the
expected signal, not a mystery to route around — the branch anticipates a98 and
is waiting for it.

**Consequence for the in-flight ticket/CRM-retirement work:** it rebases onto
the published a100 pin rather than carrying its own kernel expectations. Doing
it the other way — landing that work first and repinning after — means the pin
change arrives on top of an unrelated diff and nobody can tell which of the two
moved a failing test.

**This step adopts nothing.** No `DatabaseRuntime` is constructed, no ledger
row is reclassified, no session factory changes. If it needs a behaviour change
to go green, that change belongs in S3 and the pin is not ready.

### S3 — Adopt the runtime in ERP with dual-GUC compatibility

**Purpose:** replace ERP's duplicated scope plumbing with one constructed
runtime, without changing a single RLS policy or moving a single table.

This is a replacement rather than a redesign, and the reason is worth stating
plainly: `app/rls.py`'s `_SET_CURRENT_SCOPE_SQL` ALREADY sets
`app.current_organization_id` and `app.current_tenant` in one parameterized,
transaction-local `set_config` statement. That is exactly what `DatabaseRuntime`
composes from `legacy_tenant_settings`. ERP is not being asked to change what
its database sees; it is being asked to stop being the code that decides it.

**Seams to change in `dotmac_erp`:**

- `app/db/__init__.py` — keep `get_engine()` and its fork-safe, PID-keyed,
  dispose-on-PID-change behaviour; that behaviour is ERP's and the runtime does
  not supersede it. Construct the `DatabaseRuntime` around the returned engine.
- `app/db/session_context.py::tenant_scope_for_session` — delete, and call
  `runtime.tenant_scope(session, organization_id)`. This is the ported mechanic
  coming home; the `after_begin` re-arm behaves identically because it IS the
  same code.
- `app/tenancy.py::OrganizationTenantContext` — supply as `tenant_lookup`. It
  must raise rather than return `None` on an unknown organization: a CLI handed
  a `None` carries on and prints an empty report, which is the exact failure the
  runtime exists to stop being quiet.
- `app/api/deps.py::get_db_with_org` and `app/web/deps.py::get_db_for_org` —
  become thin adapters over the runtime's boundaries, keeping their current
  FastAPI signatures so no route changes.
- `app/web/deps.py::get_async_db_for_org` — UNTOUCHED (boundary 6).

**Ledger amendment, in the same change:** `docs/PLATFORM_ADOPTION_LEDGER.md`
classifies `dotmac_kernel.db` as PERMANENTLY PROHIBITED, with the stated reason
that it "constructs TWO engines + SessionLocal/PlatformSessionLocal from env at
import time and primes only `app.current_tenant`". a100 removes both halves of
that premise, so the row's REASON is now false even though its VERDICT is still
right. Rewrite the reason (`dotmac_kernel.db` is the starter's instance, still
eager, and is not adoptable by a deployment with its own configuration) and add
a separate row classifying `dotmac_kernel.session_runtime` as consume-pure and
adopted. `tests/architecture/test_kernel_import_boundary.py`'s
`ALLOWED_KERNEL_MODULES` allowlist and its
`test_allowlist_matches_ledger_classification` move in the SAME commit — an
allowlist and a ledger that drift apart mean the guard is asserting against a
document nobody is reading.

**The pin is already done when this step starts.** S2b moved all six pin
artifacts together against a published a100. If this step finds itself editing
`pyproject.toml`, `poetry.lock` or `KERNEL_PIN`, S2b was skipped or was
incomplete — go back rather than folding a pin change into an adoption diff,
because the two failing together is indistinguishable from either failing
alone.

**Deliverable unique to this step:** the shrink-only ratchet test (see "The
ratchet" below). It lands here, with the legacy set at one, so that the count it
guards has a starting value the reviewer can see.

**Canaries:** an HTTP request, a Celery task, a CLI command and a reconciler
each prove BOTH settings are primed together and with the same value; a commit
inside a scoped block leaves the next statement still scoped (the `after_begin`
re-arm); a scoped block leaves nothing on the connection after exit (borrow the
same pooled connection and assert the settings are absent); a cross-organization
read through each entry point returns nothing; and — the test that documents the
danger rather than the fix — a bare unscoped session is proven SILENT, not loud.

### S4 — Move the remaining legacy tables onto module lineages

**Purpose:** make the legacy GUC unnecessary, one domain at a time. This is the
long step and the plan does not pretend otherwise.

The measured inventory is `tests/integration/tenant_table_inventory.tsv` (450
rows), which is the per-table disposition and the thing to work from rather than
a fresh survey. Today only 21 tables are on the module-lineage
`app.current_tenant` family — `mod_accounting` 12, `mod_numbering` 4,
`mod_imports` 3, `mod_files` 2, each with `policy_count=1` and
`policy_uses_settable_guc=false`. The other ~429 tables across ~41 schemas are
legacy on `app.current_organization_id`, and 105 of them still consult the
`app.bypass_rls` settable GUC.

Two related ratchets move with this work and must not be allowed to drift in the
opposite direction while it happens: `docs/rls-coverage-baseline.json` records
223 known gaps (157 tables with RLS off, 66 not FORCED), and
`docs/inventories/rls-cross-org-callers.tsv` records 147 rows. Under ADR-0018
these are two-directional — a count that FALLS without the baseline being lowered
is as much a failure as one that rises, because a silently-improving number is
usually a detector that stopped seeing.

**This step has no schedule and this plan does not invent one.** It is
per-domain, each domain is its own reviewable change with its own expand/backfill
/verify/cutover/contract shape, and sequencing belongs to whoever owns the
domains — not to a document written from outside the repository. What this plan
fixes is the ORDER CONSTRAINT: no name leaves `legacy_tenant_settings` until the
tables that depend on it are gone, and S5 cannot begin until this step reaches
zero readers.

### S5 — Remove ERP's duplicate session factory and the organization-GUC path

**Purpose:** end the transition. One factory, one writer, an empty legacy set.

- Retire the local sessionmaker, `_get_session_local()` and the
  `_SessionLocalProxy` in favour of `runtime.session_factory` /
  `runtime.platform_session_factory`. The proxy exists to defer construction;
  the runtime is constructed in a composition root, so the deferral has no
  remaining job.
- Drop `app.current_organization_id` from `legacy_tenant_settings` once S4 has
  proven no policy reads it, and lower the ratchet in the same commit. Empty is
  the finished state, and the ratchet test then guards zero.
- **`app/services/audit_listener.py` is a correctness item, not cleanup.** It
  writes the GUC itself with an f-string `SET LOCAL`, in a probe/pin/restore
  sequence around lines 296, 335 and 391–396. Two things are wrong with it
  independently. First, the value is INTERPOLATED rather than bound, in a file
  whose whole job is to observe values from elsewhere. Second, it is a SECOND
  writer of the scope: the runtime's one-statement guarantee only holds if
  nothing else writes those settings, and a probe/pin/restore additionally
  restores a value the runtime never set — so a listener firing inside a scoped
  block can hand the rest of that block a scope the boundary did not choose.
  This must die with the legacy GUC, and it is the one place in S5 where
  "delete the duplicate" is also a bug fix.

### S6 — Prove a fresh composed ERP database boots on kernel/module migrations only

**Purpose:** prove that the tables are actually where S4 claims they are, on a
database nobody has hand-repaired.

**Be precise about what this claim IS**, because getting it wrong is the exact
confusion ERP already has a permanent negative canary to prevent. ERP composes
four module lineages through `version_locations` in `alembic.ini` and deploys
with `alembic upgrade heads` (`scripts/deploy.sh`).
`dotmac_kernel.migrations:versions` is DELIBERATELY ABSENT from that list, and
`tests/integration/test_kernel_lineage_rehearsal.py` is a permanent negative
canary asserting it stays absent — ERP hosts `public.tenants` itself and can
therefore never run kernel revision `0001_initial_tenant_schema`.

So "kernel/module migrations only" here means exactly two things, and neither is
"ERP starts running the kernel lineage":

1. ERP's own assembly lineage supplies the prerequisite EFFECTS that composed
   modules require — `tenant_scope_catalog.v1` bound to `20260813_tenant_projection`
   and `module_database_roles.v1` bound to `20260814_database_roles`, per
   `app/migration_bindings.py`. A module declares the effect it needs; the
   assembly binds effect to revision; the binding is proven against the live
   catalog rather than trusted. A module never names a foreign revision.
2. Every DOMAIN table comes from a module lineage, so a fresh database has no
   table whose only origin is an ERP-local legacy migration.

**Acceptance:** a fresh composed database, migrated only by
`alembic upgrade heads` with the bindings above, serves the full ERP surface
with `legacy_tenant_settings == ()`; the kernel-lineage negative canary is still
green and was not weakened to get there; and the live-catalog contract confirms
revision, namespace and table ownership for every composed lineage.

## The ratchet

`DatabaseRuntime.legacy_tenant_settings` is exposed as a property for one
reason: so the adopting product can assert against it. The kernel cannot assert
a count it does not own — a starter-side test over ERP's legacy set would be a
guard in the wrong repository, asserting a number it cannot see change.

**ERP's S3 deliverable is therefore an architecture test that reads
`runtime.legacy_tenant_settings` and asserts the count only ever falls**, with a
declared current value that a change must lower explicitly, never raise, and
never quietly leave alone while adding a name. Empty is the finished state, and
the test should say so in its own failure message.

The failure this prevents is specific and unglamorous: `legacy_tenant_settings`
becoming a general "extra GUCs" bag. It has room for exactly the names somebody
is actively retiring. A name that sits there for two quarters with nothing moving
is not compatibility, it is a second tenancy scheme with a friendlier label — and
the runtime refuses the canonical name as a legacy entry precisely so that scheme
can never be spelled as though `app.current_tenant` were optional.

## Named follow-ups

Recorded so they are not read as done, and not read as forgotten.

- **The async path.** `AsyncSessionLocal` and `get_async_db_for_org` remain
  ERP-owned duplicate scope plumbing after S6. Either the kernel grows an async
  runtime with a product-first source behind it (rule 22 — port a production
  implementation, do not invent one), or ERP's async path is explicitly declared
  ERP's forever and gets its own guard. It must not simply stay unmentioned.
- **A public engine-free savepoint spelling**, if and only if S3 finds a real
  ERP consumer for `conflict_savepoint` (S1). A declaration with no consumer is
  the shape this repository deletes.
- **The kernel-lineage question is closed, not deferred.** ERP hosting
  `public.tenants` is a permanent fact with a permanent canary; nothing in S6
  reopens it.

## What this plan does not do

- **No `ProductAssemblySpec` database field was added, and none is proposed.**
  The spec has no database slot, and adding one would be a declaration with zero
  consumers — the shape this repository deletes rather than keeps. A product
  constructs its runtime in its own composition root and passes the bound
  methods where its framework wants callables; those methods are built once at
  construction, so their identity is stable and `dependency_overrides` keyed on
  them works.
- **`create_app` is untouched.** Application construction, module registry
  order, startup checks and lifespan hooks are exactly as they were.
- **ERP does not adopt `create_app`, kernel middleware, kernel routers or kernel
  identity** as any part of this. ERP's request pipeline, its RBAC, its OIDC
  binding and its sessions remain local. The whole point of a framework-free
  seam is that the transaction discipline arrives without the router stack.
- **The canonical `app.current_tenant` name is not configurable and this plan
  never makes it so.** Boundary 2 is not a preference to revisit later.
- **No new setting, no new env knob, no schema change in the kernel**, and no
  ERP identity migration: `organization_id` is not renamed anywhere.
- **No pin, publication, merge, deploy or SSH action is authorized here.**

## Required validation

Each step runs the TARGET repository's authoritative commands, read from its
current `AGENTS.md`, `Makefile` and CI workflows at execution time rather than
from this file. For `dotmac_erp` at the plan's evidence revision that is at
minimum:

```bash
poetry run ruff check app tests alembic scripts
poetry run ruff format --check app tests alembic scripts
poetry run mypy app
poetry run pytest tests/ --ignore=tests/e2e/
```

plus the repository's migration, tenant-context, RLS and PostgreSQL integration
gates, which are the ones that actually matter here — RLS is the whole point and
SQLite cannot enforce it, so a unit-test-only green is not evidence for any step
in this plan.

**Git-hosted CI is the acceptance owner.** Local static checks are not test
evidence and must not be reported as such. Push the branch and let CI decide.

## Completion criteria

This plan is complete only when, in `dotmac_erp`:

- one `DatabaseRuntime` instance, built from ERP's own engine and
  `Organization` lookup, is the only transaction authority, and no second
  session factory, commit boundary or scope-priming path survives;
- `legacy_tenant_settings` is empty, the shrink-only architecture test guards
  zero, and `app.current_organization_id` is not written by any code path
  including `audit_listener`;
- every domain table is owned by a composed module lineage, the legacy-table
  inventory is exhausted, and the RLS coverage and cross-org-caller ratchets
  were lowered explicitly rather than drifting;
- a fresh composed database migrated by `alembic upgrade heads` alone serves the
  full surface, with the kernel-lineage negative canary still green and
  unweakened;
- the adoption ledger, the kernel import allowlist and the pinned kernel version
  agree with each other and with an authoritative external release oracle; and
- the async path is either adopted or explicitly declared ERP-owned with its own
  guard, rather than left unmentioned.
