# Commercial composition and conformance

**Date:** 2026-08-14
**Owner:** Adoption / conformance workstream (Starter)
**Status:** non-authoritative intent, per the docs hierarchy in `CLAUDE.md`.
`docs/ARCHITECTURE.md` is as-built truth; `docs/adr/` holds decisions. This
document describes **the machinery that turns four accepted specifications into
adopted modules**. It decides nothing about what billing, subscriptions,
collections or document rendering should *do*.
**Creates nothing.** No package, module, model, migration or namespace
allocation. It states what `namespaces.py`, `migration_bindings.py`,
`alembic.ini`, `app/assembly.py` and `.github/release-modules.json` must
eventually contain; it does not contain it.
**Gate:** ADR-0017 P11 is UNMET. See
[`docs/inventories/p11-adoption-status.md`](../../inventories/p11-adoption-status.md).
Nothing in this document may begin before that gate, or an owner-directed
exception in the shape ADR-0026 granted `dotmac-approvals`.

**Governing rules:** AGENTS.md 11 (tenant-scoped tables), 13 (migration
discipline), 14 (one namespace + one lineage per stateful module), 22
(product-first extraction), 25 (an exemption must be enforceable), 27 (dual
planes), 28 (apps compose by synchronizing data). ADR-0006 D1 and its
2026-08-13 amendment; ADR-0017; ADR-0018; ADR-0020 A1/A2/A3/A5/A6; ADR-0022;
ADR-0023; ADR-0024.

---

## 1. Namespace and migration bindings

### 1.1 The ledger as it stands

`dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` at starter `49f9ccf`
(`origin/main`), kernel `0.1.0a59`. Nine installable allocations plus two
grandfathered host owners.

| Owner | Prefix | Branch label | Schema | Allocated in | Planes declared |
|---|---|---|---|---|---|
| `kernel` | `k` | `kernel` | *(none — `public`)* | pre-D1, legacy id pattern | host |
| `assembly` | `a` | `assembly` | *(none — `public`)* | pre-D1, legacy id pattern | host |
| `template_studio` | `ts` | `template_studio` | `mod_tstudio` | `0.1.0a13` | tenant |
| `ticketing` | `tk` | `ticketing` | `mod_tkt` | `0.1.0a39` | **both** |
| `release_catalog` | `rl` | `release_catalog` | `mod_rel` | `0.1.0a44` | tenant *(misdeclared — § 5.4)* |
| `entitlement_allocation` | `ea` | `entitlement_allocation` | `mod_ealloc` | `0.1.0a45` | tenant *(misdeclared — § 5.4)* |
| `application_directory` | `ad` | `application_directory` | `mod_appdir` | `0.1.0a46` | tenant |
| `files` | `fi` | `files` | `mod_files` | `0.1.0a54` | **both** |
| `imports` | `im` | `imports` | `mod_imports` | `0.1.0a55` | tenant |
| `integration` | `ig` | `integration` | `mod_intg` | `0.1.0a58` | **platform only** (empty `tables`) |
| `approvals` | `ap` | `approvals` | `mod_approvals` | `0.1.0a59` | **both** |

**Reserved and unavailable, fleet-wide:** prefixes `k a ts tk rl ea ad fi im ig
ap`; schemas `mod_tstudio mod_tkt mod_rel mod_ealloc mod_appdir mod_files
mod_imports mod_intg mod_approvals`; branch labels as listed; and `public`,
`information_schema`, `pg_catalog`, `pg_toast`, `pg_temp`.

A prefix is **never reused after retirement** (allocation rule, `namespaces.py`
§ THE ALLOCATION LEDGER).

### 1.2 What each commercial module needs

| Module | Stateful? | Needs an allocation? | Planes |
|---|---|---|---|
| `dotmac-billing` | yes | **one** short code, prefix, branch label | tenant **and** platform (ADR-0020 A2) |
| `dotmac-subscriptions` | yes | **one** | tenant **and** platform |
| `dotmac-collections` | yes | **one** | tenant **and** platform |
| document rendering | **no** — its dossier proposes a *stateless* module | **none** | n/a; scope is data, one code path, contract suite runs every case under both `TenantScope(uuid4())` and `PlatformScope()` and asserts an identical `projection_digest` |

The rendering row is the important one. `document-rendering-extraction-dossier.md`
§ 3 states it plainly: *"No `mod_*` short code, no lineage, no namespace entry. A
stateless module allocates nothing in the kernel's immutable namespace ledger…
That ledger is immutable, so an allocation made 'just in case' is permanent debt
for a module that may never have a table."* **The conformance requirement is
therefore that the rendering package declare neither `short_code` nor
`migration_prefix`** — `NamespaceRegistry.from_manifests` treats a manifest with
no `migration_owner()` as stateless and contributes nothing, which is the
behaviour to rely on rather than an empty allocation.

### 1.3 The allocation constraints a proposal must satisfy

Mechanically checked by `namespaces.py`, so a proposal that fails these fails at
import, not at review:

- **short code** matches `^[a-z][a-z0-9_]{1,20}$`; the schema is the *derived*
  read-only `mod_<short_code>`, never settable, never inferred from `code`,
  `name`, a brand or any display string;
- **migration prefix** matches `^[a-z][a-z0-9]{0,5}$` — **6 characters
  maximum**, because a revision id is `<prefix>_<sequence>_<slug>` and must fit
  `alembic_version.version_num`'s `VARCHAR(32)`. Six spent on prefix plus one
  underscore plus four sequence digits plus one underscore leaves **20
  characters for the slug**;
- **branch label** matches `^[a-z][a-z0-9_]{0,31}$`;
- all three, plus the schema, are **unique fleet-wide** against the whole ledger
  — not merely against the modules a given deployment installs.
  `NamespaceRegistry.from_manifests` validates the entire shipped ledger before
  looking at any manifest, precisely so a dormant collision cannot lie in wait
  until two modules are first composed together;
- a schema length over 63 bytes is rejected: *"Postgres truncates at 63, and a
  truncated schema collides."*

Two naming conventions the existing rows establish and a proposal should follow:
the short code is chosen for **how it reads in a catalog dump** (`tkt` not
`ticketing`, `intg` not `integration`, `ealloc` not `alloc`), and the prefix is
chosen to be **as short as the reader can still attribute** (`tk`, `ig`, `ea`)
so the slug keeps its budget.

### 1.4 Allocation happens in the diff that creates the package — never before

This is settled and must not be relitigated per module. ADR-0017's 2026-08-14
`dotmac-approvals` amendment:

> **The exception authorises the module, and the module carries its own
> allocation.** Nothing is reserved in advance: the change that writes
> `dotmac-approvals` allocates `mod_approvals` against the then-current kernel
> alpha and opens its `EXTRACTION.toml` in the same diff. Reserving a namespace
> earlier would have meant renumbering it at every rebase while the alpha train
> is contended, and holding a manifest-less ledger row honest with a
> package-specific gate. ADR-0026 § 8 records that reasoning; if "allocated but
> unbuilt" is ever worth having as a state, it needs a generic ledger mechanism
> and a generic gate rather than a one-off.

All four commercial dossiers already comply — none proposes a short code, and
the collections dossier says so in terms: *"Do not reserve a namespace, create an
empty package, or predeclare a guessed Vendor action code ahead of these gates."*

**Practical consequence for whoever eventually writes the diff:** validate the
proposed prefix and short code against `MIGRATION_OWNER_LEDGER` on
`origin/main`, not against a feature branch. `origin/main` at `49f9ccf` already
carries an allocation (`ap`/`mod_approvals`) that the branch this programme is
being drafted on does not.

### 1.5 The edits a stateful module costs, and where each lands

Five files, in three different blast radii. Getting this wrong is how a module
becomes composable in the starter but not releasable, or releasable but not
pinnable.

| # | Edit | Repo/scope | Effect if omitted |
|---|---|---|---|
| 1 | A `MigrationOwner` row in `dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER`, **plus a kernel release** | kernel package — fleet-wide | `UnallocatedNamespaceError` at registry construction. The releasing kernel version becomes the module's floor. |
| 2 | The distribution's import name added to the import-linter contract *Modules are independent of each other* (`pyproject.toml`) | starter | The module could import a sibling module and nothing would fail. This is the executable half of ADR-0020's A1 and ADR-0024 § 2. |
| 3 | An entry in `.github/release-modules.json`, with `kernel_floor`, `db_schema`, `tag_prefix` and `wheel_contents` (`required` + `forbidden_prefixes` + `allowed_requires`), and the matching workflow choice lists | starter | **Unpublishable.** `release-module.yml` resolves its target there and fails closed. A module nobody can publish is a module nobody can pin, which is the state `dotmac-ticketing` and `dotmac-integration` each sat in until their entries landed. |
| 4 | A `version_locations` entry in `alembic.ini` | **the composing assembly only** | The lineage never runs there. |
| 5 | The manifest added to `ProductAssemblySpec.modules` in `app/assembly.py` | **the composing assembly only** | The module is not registered, its schema is not audited by the live-catalog gate, and its nav/routes do not mount. |

Plus, not a file edit but required by ADR-0006's product-first amendment and
`tests/architecture/test_product_first_extraction.py`: a
`packages/<dist>/EXTRACTION.toml` with `status`, `contract`,
`local_copy_retirement`, `source_paths`, `preserved_tests`, `first_cutover`,
`shadow_and_drift` and `next_action`. **Its `status` is derived from
`len(contract_consumers)`** — a package cannot describe itself as adopted.

Edits 4 and 5 are **deliberately not made in the starter** for six of the nine
current modules. `test_integration_isolation.py` states the rule as built:

> "Adding it to `app/assembly.py` or the shipped `alembic.ini` merely to get CI
> coverage would contradict the deployment boundary the module exists to
> establish — every starter deployment would grow a `mod_intg` schema it never
> uses."

**The same reasoning applies to all four commercial modules.** ADR-0020 A6's
matrix says the starter *"owns the packages, contracts, conformance tests, and
the reference assembly. It holds no commercial rows of its own."* So the starter
must **not** add `mod_bill`/`mod_sub…`/`mod_coll…` to `app/assembly.py` or
`alembic.ini`. Their CI coverage comes from the scratch-database canary pattern
in § 5.5.

### 1.6 `requires` / `provides` / binding — the three parts, and why no module names a foreign revision

ADR-0006 D1's 2026-08-13 amendment. `dotmac_kernel/prerequisites.py` states the
problem it solves:

> That edge is a lie in every assembly that does not run the named lineage. It
> says "files needs kernel revision 0001", when what files actually needs is a
> tenant catalogue to point a foreign key at and three database roles to grant
> to. Kernel 0001 is one *provider* of those effects. It is not the requirement.

| Part | Where it lives | Shape |
|---|---|---|
| **Vocabulary** | `dotmac_kernel.prerequisites` — an open declaration registry, never an enum (ADR-0008). Two shipped today: `tenant_scope_catalog.v1`, `module_database_roles.v1`. Name form `<name>.v<major>`; a changed verified contract is a **new** prerequisite, never a re-pointed one. | `PrerequisiteSpec` |
| **Declaration** | the module's `ModuleManifest.requires`, and the answering `MigrationOwner.provides` | literals in the migration root, so the composed gate can read them statically and diff them against the manifest |
| **Binding** | **the assembly**, checked in — `app/migration_bindings.py`, installed from `alembic/env.py`, discoverable by entry points that never run `env.py` through `DOTMAC_MIGRATION_BINDINGS` | `PrerequisiteBinding(prerequisite, provider_revision, provider_owner)` |

A binding is a claim, so it is checked three independent ways and never
believed:

1. **statically**, by `make migration-gate` (in `make check` and in CI *before*
   `docker-build`): duplicate revisions/prefixes/branch labels/schema
   claims/table ownership, unbound requirements, a binding to a lineage that
   never declared the effect, a binding to an uncomposed revision, and
   migration/manifest drift;
2. **against the live catalog**, by `require_prerequisites` before any DDL — the
   table shape, the function semantics, the roles. **A stamped provider fails
   here**, which is what makes `alembic stamp` unusable as a shortcut;
3. **by an order canary** asserting the provider revision is recorded in
   `alembic_version`.

`resolve_depends_on` then turns the binding back into a real Alembic
`depends_on` edge at script load, so ordering is exactly as correct as a
hand-authored edge — and a module lineage may **never** name a foreign revision
itself.

### 1.7 The binding each assembly must write for these modules

Given a dual-plane commercial module declaring
`requires = ("tenant_scope_catalog.v1", "module_database_roles.v1")` — the
declaration every dual-plane module ships today (`dotmac-ticketing`,
`dotmac-files`, `dotmac-approvals`):

| Assembly | Binding it must write | Current state |
|---|---|---|
| **Starter** (reference) | Both effects → kernel `0001_initial_tenant_schema`, owner `kernel`. Already present in `app/migration_bindings.py`. | ✅ exists — but the starter must not compose these modules at all (§ 1.5) |
| **Sub** | Both effects → whichever Sub revision supplies the tenant catalogue and the three roles. Sub's `tenants`/`tenant_domains` come from its own `508_operator_tenant_tables.py`, so the binding names **508**, not kernel `0001` — *unless* P11's cutover replaces 508 with the kernel lineage, in which case it names kernel `0001`. **Which of those two is true is exactly the P11 decision.** | ❌ `dotmac_sub:alembic.ini` has no `version_locations`; there is no `migration_bindings.py` |
| **Vendor CP** | See § 2.3 — this is the open conformance question, not a filled-in row. | ❌ **no `migration_bindings.py` at all**, and none is possible: Vendor CP pins kernel `0.1.0a45`, eleven alphas below the `a56` that introduced the contract. It composes four lineages today through **physical `depends_on` edges** |
| **ERP** | Both effects → ERP's own revisions. **This is already implemented** on `origin/main`. | ✅ **the first real implementation outside the starter** — see § 2.4a |

### 1.8 A dual-plane module still requires a TENANT catalogue — the unresolved case

**This is an open conformance question and is recorded, not answered.**

Every dual-plane module shipped so far declares the tenant prerequisites
unconditionally:

```
# dotmac-ticketing tk_0001_tickets.py
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
```

`dotmac-files` and `dotmac-approvals` declare the same pair. The only module
that declares **no** `requires` is `dotmac-integration`, which is
**platform-plane only** — its manifest carries an empty tenant `tables` tuple.

The consequence, stated plainly: **a dual-plane module installed into a
platform-only assembly still demands a tenant catalogue and the tenant
application role**, because its tenant-plane tables are created by the same
lineage that creates its platform-plane tables. ADR-0020 A6 places
`dotmac-billing`, `dotmac-subscriptions` and `dotmac-collections` in Vendor CP,
which ADR-0023 measured as having *"`get_platform_db` at 15 call sites, **zero**
tenant-session or `require_tenant` uses, and platform catalog tables that carry
no `tenant_id` at all."*

Three shapes could resolve it. **Naming them is not choosing one** — that is an
architecture decision this workstream does not own:

1. Vendor CP supplies the tenant-scope catalogue from its own lineage as an
   inert prerequisite (a `tenants` table with no rows, plus
   `app_current_tenant_id()` and the three roles) and the module's tenant plane
   is created-but-empty. Cheap; but it installs a tenant estate into an assembly
   whose whole identity is not having one, and an empty tenant table is the
   sentinel-tenant dodge's cousin.
2. The lineage becomes plane-conditional — a per-plane migration and a
   per-plane `requires`. This is the honest shape and is also the largest change
   to the kernel's prerequisite contract; it is a new capability, therefore
   squarely under ADR-0017's moratorium unless demand-pulled.
3. The commercial modules are **not** dual-plane, and Vendor CP's commercial
   surface is a different module. ADR-0023 § "Alternatives rejected" rejects the
   two-module shape for ticketing on duplication grounds; whether the same
   reasoning holds for money is a decision for the owning teams.

**Recommendation to the owning teams and to Michael:** answer this before the
package-creation diff, not during Vendor CP's cutover. It is the same class of
defect ADR-0023 was written to fix — *"a module was designed against the
security context of the product that happened to be audited first"* — and this
time the fleet has advance notice.

---

## 2. Assembly composition, plane by plane

### 2.1 What the reference assembly does today, and what it teaches

`app/assembly.py` at `49f9ccf` composes `load_manifests(FEATURE_MODULES)` plus
exactly two module manifests (`dotmac_template_studio.module`,
`dotmac_ticketing.module`); `alembic.ini` carries four version locations
(kernel, assembly, `ts`, `tk`). Six other allocated modules are **not** composed.

The pattern the assembly demonstrates, and the one each commercial adopter must
repeat:

- the assembly imports the module (legal direction: `assembly → module →
  dotmac-ui → dotmac-kernel`); the module imports neither the assembly nor a
  sibling;
- the assembly owns every **relation** between module rows and product data —
  the module ships a link helper **per plane** and the assembly's own migration
  calls it, rather than the module reaching into product tables;
- the assembly owns the **binding** (`migration_bindings.py`);
- the assembly owns **product declarations** the module's vocabulary needs
  (the `register_contexts` pattern Template Studio uses is the shape: the module
  owns the checking, the product owns the vocabulary — ADR-0008).

### 2.2 Sub — tenant-plane composition

**Registers:** the tenant plane of `dotmac-subscriptions`, `dotmac-billing` and
`dotmac-collections` (ADR-0020 A6), on exact pins.

**Its alembic lineage must carry:** the kernel lineage (P11's own subject), the
Sub assembly lineage, and one version location per composed module lineage.
Note the ordering dependency — a module lineage cannot be composed before the
kernel lineage it binds to is composed, so **P11 is not merely a policy gate for
Sub; it is a structural prerequisite for any module lineage there.**

**Plane contract the live-catalog gate enforces on every table the manifest puts
in `tables`:**

| Requirement | Detail |
|---|---|
| `tenant_id` | `UUID NOT NULL` |
| RLS | ENABLEd **and** FORCEd — without FORCE the table owner, which migrations run as, bypasses its own policy |
| policy | a tenant-isolation policy, created in the **same migration** as the table (hard rule 11) |
| uniqueness | composite, including `tenant_id` |
| grants | the online tenant role reachable; schema `USAGE` present |

**Shadow / cutover mechanics.** ADR-0017 decision 5 — *measure, freeze, then
improve* — is the required sequence, and Sub is the qualifying product-first
source for subscriptions (cadence, contract versioning, proration, recurrence)
and for most of billing. The mechanics each commercial slice must carry, drawn
from the shape the teams' own plans already use:

1. **Characterize before mapping.** A read-only census of the production estate
   on the explicitly named host, per tenant/account/reason/currency. The
   classifier must be **total** — an ambiguous source row becomes an explicit
   entity-scoped work item with a deadline, and does not stop unaffected rows.
2. **Port behaviour and its tests first**, before models or routes (ADR-0006's
   product-first procedure step 3). The named parity suites travel with the
   port; a re-derivation is not a port.
3. **Shadow**: the module computes alongside the local writer, writing to its
   own schema, deciding nothing. Compare on a declared key set per slice.
4. **Switch reads**, then **switch writes**, then **delete the local writer**.
   A slice is done at the delete, not at the switch — § 3 of the retirement
   ledger.
5. **No permanent compatibility projection.** The `dotmac-approvals` amendment's
   wording is the standard: *"shadow, compare, switch and delete per adopter,
   with no permanent compatibility projection."*

### 2.3 Vendor CP — platform-plane composition

**Measured today** (`origin/main` `63acff1`), because this assembly is further
along than any document in this repository assumes:

| | |
|---|---|
| Pins | `dotmac-kernel 0.1.0a45` (extras `testing`, `licensing`), `dotmac-release-catalog 0.1.0a2`, `dotmac-entitlement-allocation 0.1.0a3`, all exact, all from the private index with `priority = "explicit"` |
| Lineages composed | **four** — kernel + `mod_rel` + `mod_ealloc` + vendor. `alembic.ini` deliberately carries no `version_locations`; `src/vendor_cp/migrations.py::composed_version_locations()` builds it, and `alembic/env.py` re-composes as belt-and-braces |
| Assembly | `src/vendor_cp/assembly.py::build_spec()` registers `dotmac_release_catalog.module` and `dotmac_entitlement_allocation.module` on the `ProductAssemblySpec` |
| Prerequisite bindings | **none, and none possible at `a45`** |
| Platform-only | confirmed: **17** `get_platform_db` sites (7 routers), **0** `require_tenant`, **0** `tenant_id` columns — all 24 textual `tenant_id` occurrences are comments asserting the absence |
| Roles | online `platform_api`; migrator `app_admin`; tenant app role `app_user`. Grants are declared inline in every vendor migration as three uniform statements: `GRANT … TO platform_api;` `GRANT … TO app_admin;` `REVOKE ALL ON {table} FROM app_user;` |
| Deployed | **never.** No `CHANGELOG.md`, no runbooks, no `deploy.sh`, no container build. The only deploy artifact is `scripts/migrate.py` |

**This is the fleet's only real multi-lineage composition, and it has the wrong
kernel for the modules it is ordered to adopt.** Repinning from `a45` to at
least `a56` is a prerequisite nobody has costed: it crosses `platform_tables`
(`a53`) and the prerequisite contract (`a56`), and it converts four physical
`depends_on` edges into logical bindings.

**Registers (target):** the platform plane of the three commercial modules
(ADR-0020 A6),
retaining its commercial accounts, approval, allocation/licensing and
consequence execution. **No fake tenant** — ADR-0023's rejected-workarounds list
applies with force, and ADR-0020 A2 repeats it: *"a nullable `tenant_id` on an
invoice, a sentinel vendor tenant, and a polymorphic scope column on a
receivable are all refused by the gate."*

**Plane contract the live-catalog gate enforces on every table the manifest puts
in `platform_tables`:**

| Requirement | Detail |
|---|---|
| `tenant_id` | **absent** — not nullable, absent |
| RLS | **none at all** — *not even ENABLEd-with-no-policy*, which denies every row to the control plane while reading as protected |
| tenant app role | `REVOKE ALL` across **all seven table privileges and their column-level forms**. On this plane the revoke **is** the isolation and is checked as strictly as a policy is on the other side |
| online platform role | schema `USAGE` **plus** at least one of `SELECT`/`INSERT`/`UPDATE`/`DELETE`. `REFERENCES`, `TRIGGER` or `TRUNCATE` alone does not make a request path usable — **declared-and-unreachable is a violation** |
| uniqueness | control-plane-wide |
| naming | the bare name is the tenant plane; the platform table is prefixed (`platform_invoices`), and the Python classes are explicit on **both** sides (`TenantInvoice` / `PlatformInvoice`) |

**No foreign key crosses the planes, in either direction.** What is actually
enforced, stated as ADR-0023 states it: the gate refuses a crossing FK whose
**source** table is inside the module schema. A **product-owned link table** in
`public` or a product schema referencing the wrong plane is **unmonitored by the
gate, not exempt from the rule** — ADR-0018's distinction applied to ADR-0023's
own claim. The mitigation is that a dual-plane module ships **one link helper
per plane**, so the correct FK is generated rather than hand-typed, and each
helper refuses a configuration that would produce an unusable table. **Two
functions, never one with a `platform=` flag** — a flag has a default, and
whichever value the default takes is the plane a caller gets by forgetting to
think.

**Two gaps recorded by the collections team that Vendor CP composition must
answer first:** the platform plane has **no consent ledger and no delivery
receipt loop**, because `dotmac_kernel.consent` and `dotmac_kernel.delivery` are
tenant-plane only. A dunning notice on the platform plane therefore has no
contactability check and no receipt — which is a decision for the collections
owner, not a detail for the cutover.

**And § 1.8's unresolved question applies here specifically:** a dual-plane
module declaring `tenant_scope_catalog.v1` cannot bind that prerequisite in a
platform-only assembly today.

### 2.4 ERP — measured state, and the accounting-fact boundary

#### 2.4a As-built — ERP is the D1 amendment's proof

`origin/main` `4df1190d`. ERP pins **`dotmac-kernel 0.1.0a56`** exactly — *"no
range — the dependency-update gate in `tests/architecture/test_kernel_compatibility.py`
rejects any `^`/`~`/`>=`/`*` drift"* — and is the **only product at the current
module floor**.

`app/migration_bindings.py` exists and is the first real implementation of the
D1 amendment outside the starter:

```python
ASSEMBLY_PREREQUISITE_BINDINGS = (
    PrerequisiteBinding(TENANT_SCOPE_CATALOG_V1.name,
                        "20260813_tenant_projection", "assembly"),
    PrerequisiteBinding(MODULE_DATABASE_ROLES_V1.name,
                        "20260814_database_roles",   "assembly"),
)
```

installed from `alembic/env.py:37`, with `DOTMAC_MIGRATION_BINDINGS` documented
for graph commands that never run `env.py`. Its docstring proves the case
`prerequisites.py` was written for: *"Kernel `0001_initial_tenant_schema`
creates `public.tenants` unconditionally as its FIRST table. ERP hosts that same
table in its own lineage, so kernel `0001` can never run here."*

E8 is **accepted and partially implemented**, contrary to how every document in
this repository treats it: `docs/architecture/organization-tenant-boundary.md`
decides `tenant_id = organization_id` with *"no allocated second identifier,
nullable mapping, sentinel tenant, or mapping table"*, and
`20260813_tenant_projection.py` (469 lines) hosts `public.tenants`,
`public.tenant_domains` and `public.app_current_tenant_id()` *"without composing
or stamping the kernel lineage"*, forward-fix only.

What is still missing, in ERP's own words: `alembic.ini` has **no
`version_locations`**, so no foreign lineage composes; and
`kernel-0001-dispositions.md` records *"**Until this is done for Seabone,
`dotmac-files` cannot be composed into ERP** — `fi_0001_stored_files` runs as
`app_admin` and hits the same wall. The kernel repin and the prerequisite
bindings are necessary but not sufficient."*

#### 2.4b The accounting-fact contract, and why ERP does not implement it

ADR-0020 A6: **ERP installs none of the three modules.** The row is load-bearing
— *"ERP installing billing would recreate the shadow-GL this ADR rejects."*

ERP's entire relationship to this programme is one inbound contract.

```
billing.AccountingFactV1  ──assembly outbox──>  ERP accounting intake
```

> **⚠ Blocking contradiction, measured 2026-08-14.** The contract below is the
> billing team's specification. **ERP does not implement it, has no type of that
> name, and its checked-in contract forbids the transport.**
>
> - `AccountingFact` / `accounting_fact` have **zero occurrences** in
>   `dotmac_erp`, on the local tree and on `origin/main`.
> - ERP's live integration with Sub is a **document-level HTTP pull**:
>   `app/tasks/dotmac_sub.py::run_dotmac_sub_incremental_sync` calls
>   `sync_invoices(...)`, `sync_payments(...)`, then `post_unposted_invoices(...)`
>   / `post_unposted_payments(...)`, with a nightly tier-2 reconciliation and
>   watermarks in `app/models/finance/ar/dotmac_sub_sync_watermark.py`. Routes
>   are `app/api/sync/dotmac_sub.py` plus webhook events
>   `invoice.created|sent|paid|overdue`, `payment.received|refunded`.
> - `docs/dotmac_sub_tax_accounting_contract.md` states the boundary and the
>   transport: *"Dotmac Sub owns ISP billing facts. Dotmac ERP owns accounting…
>   ERP **pulls** immutable or versioned source facts from Sub, maps them
>   through ERP configuration, and creates the canonical accounting
>   projection."* And: *"ERP uses the existing Dotmac Sub pull integration. **No
>   second push/outbox path is permitted for the same accounting decisions.**"*
>
> Billing's `AccountingFactV1` is push-after-commit through
> `dotmac_kernel.messaging`'s outbox. **A push contract and a
> no-second-push-path contract cannot both hold.**
>
> Note what is *not* in conflict: the **ownership** boundary is identical on
> both sides — Sub/billing owns operational facts, ERP owns the chart of
> accounts, journals, periods and statutory posting, and
> `docs/gl_source_of_truth.md` says *"**External billing systems never supply GL
> account IDs.**"* ADR-0020 § 2 and ERP's contract agree completely on **who
> decides**. They disagree on **how the fact travels**, and on whether the
> current pull path is the one the commercial module keeps or the one it
> replaces.
>
> **This is an authority-migration question, not an integration detail**, and
> ADR-0024 § 5's rule applies: *"Any transfer of one of those ownership
> boundaries needs an accepted ADR with a shadow, cutover, repair and
> legacy-writer retirement plan."* It is recorded in the retirement ledger as
> gap **G8** and is listed in § 6 as an open question with a named owner. The
> contract table below is what billing specified; it is not a description of
> ERP.

| Facet | Requirement |
|---|---|
| **Identity** | `billing.accounting.fact.v1`; identity is `(source_system, fact_id, fact_version)`, where `source_system` names the billing *installation*. Immutable once emitted. |
| **Idempotency** | ERP-side scope `erp.accounting.intake`, key `f"{source_system}:{fact_id}:{fact_version}"`, `fingerprint = None`. Per ADR-0014 the owner is `dotmac_kernel.idempotency`; key identity is `(tenant_id, scope, key)`; the fingerprint is its own nullable column and never a reused id; **nothing is reserved before the effect**. ERP may replay the full stream. |
| **Transaction boundary** | emitted **after billing's own transaction commits**, through `dotmac_kernel.messaging`'s outbox. **No synchronous cross-database transaction**, and a delivery failure never rolls back a billing decision (ADR-0024 § 3). |
| **Money** | exact `Money`; where an FX observation was used, the **immutable snapshot** travels with the fact — source/target currency, exact rate, rate type, observation identity and version, observed-at, effective-at, rounding policy, provenance. |
| **Correction** | **append-only.** A wrong fact is offset by a reversing fact referencing it. `fact_version` increments only for re-emission of the *same* fact after a producer-side defect; ERP treats a higher version as superseding. A policy change never rewrites history. |
| **Refusals** | ERP's `UnmappedEffect` and `NoOpenFiscalPeriod` are **ERP's** refusals, reported back as reconciliation results — **not** as billing retries. Billing has no fallback journal and no compensating write; an unmapped fact waits in ERP's exception queue for a human. |
| **Compatibility** | the typed effect vocabulary is **closed within a version**. A new effect kind is a `V2`, because a consumer that silently ignores an unrecognized accounting effect **under-posts**. |

**Why billing keeps no ERP-shaped shadow journal**, and how that is made
checkable rather than promised: an architecture test over billing's model,
service and schema names must refuse *chart of accounts, account code, account
mapping, journal, journal entry, journal line, fiscal period, period close,
statutory return, tax return, trial balance, treasury, GL reconciliation*. Its
sensitivity proof is a fixture module declaring one of those names, asserted to
fail — an empty forbidden-name check passes for the wrong reason, exactly as the
`| safe`-filter guard's `test_the_safe_filter_guard_still_bites` exists to
prevent.

The reverse boundary is equally load-bearing and equally cheap to state: **ERP
never writes billing allocations or operational customer positions.** ADR-0024
§ 1 forbids the mechanism (no foreign key into another application's database,
no shared model import), so the only enforcement needed is that ERP's intake
adapter records a typed observation and submits a command — it does not assign
an authoritative billing field.

### 2.5 Assembly wiring is the only legal arrow

ADR-0020 A1 corrects the original § 4 graph in place. The three modules are
**peers over `dotmac-kernel`**, and every arrow is a versioned contract carried
by the consuming assembly — an outbox event or a typed command, **never a Python
import**:

```
subscriptions.RatedObligationOutputV1  ──assembly──>  billing.AcceptRatedObligationV1
integrator.SettlementObservationV1     ──assembly──>  billing.AcceptSettlementV1
billing.ReceivablePositionV1           ──assembly──>  collections.ReceivablesReader
collections.ConsequenceRequestV1       ──assembly──>  the owning service (Sub / Vendor CP)
billing.AccountingFactV1               ──assembly──>  ERP accounting intake
billing.InvoiceDocumentFactV1          ──assembly──>  rendering owner
rendering.RenderedDocumentV1           ──assembly──>  dotmac-files
```

Enforced by the existing import-linter contract *Modules are independent of each
other* plus *Modules must not import the assembly*. **Adding a module to the
first contract is edit #2 of § 1.5 and is not optional**; a module absent from
that list could import a sibling and nothing would fail.

### 2.6 The arrows do not currently agree with each other

Four of the seven arrows above are specified twice, by two teams, incompatibly.
Conformance machinery cannot reconcile a contract disagreement — it can only
refuse to pretend one does not exist. Each is recorded in the retirement ledger
with the rows it blocks.

| Arrow | Disagreement | Ledger gap |
|---|---|---|
| `billing.ReceivablePositionV1 → collections` | **Two different contracts share one version name.** Billing: identity `(scope, billing_account_id, currency)` + `as_of_version`, third field `prepaid_funding`, delivered as a **published fact**. Collections: identity `(source_owner, exposure_ref, source_version)`, third field `funding_available`, delivered by a **synchronous `ReceivablesReader` port** returning `Ok`/`Unavailable(retryable)`/`Unknown`/`AuthorityMismatch`, plus `state_fingerprint`, `authority`, `completeness`. Neither document acknowledges the other. | **G2** |
| the same arrow, again | **Billing's position carries no service period.** `dotmac_sub:app/services/collections/prepaid_policy.py:57` reads `period_start` on the obligation row to enforce *"the service period has not started"*. After the split that field is subscriptions' and the money is billing's — and billing's `ReceivablePositionV1` field list has no home for it. Without it prepaid collections manufactures cases for future periods. | **G1** |
| `billing.InvoiceDocumentFactV1 → rendering` and the artifact relation back | **Two incompatible relation designs.** Billing Part 5: partial unique `… WHERE superseded_at IS NULL`, repair by **appending** a row with a `supersession_reason` from an open registry, idempotency key **includes the checksum**, digest `presentation_model_digest`, plus `withdrawn_at`. Rendering § 6.4: composite unique `(scope, invoice_id, fact_version, media_type)`, *"the unique constraint refuses a second row"*, repair updates only `file_id`/`checksum`/`byte_length`, key **excludes** the checksum, digest `projection_digest`, no withdrawal column. Both are PROPOSED; both defer to Michael; **the key compositions cannot both ship.** | **G3** |
| every arrow | **Name collisions.** `document_profile_code` vs `template_profile_code` (identical substance). ~~`external_finance` vs `manual_erp`~~ as the third `source_authority` member — **RESOLVED 2026-08-14 by ADR-0020 § A7: `external_finance`; `manual_erp` retires** (recorded here as history, not as a live collision). `RatedObligationOutputV1` vs `RecurringObligationDueV1` vs `subscriptions.recurring_obligation_due.v1` — **three names for one output**, still open. `ConsequenceRequestV1` vs `CollectionActionRequested` — still open; the collections spec itself says *"One name must win before any code."* | **G5** |

**`InvoiceArtifactReconciler` has no module owner.** Both teams place it on the
assembly; rendering § 6.6 then rejects the assembly as a resting place — *"An
assembly-owned relation table is assembly-local state with no module owning its
tests, its migration or its drift repair."* Billing's plan makes it **required
before the Vendor CP cutover**. It is therefore a blocking, unassigned owner,
recorded alongside P3 and P4 in § 6.

---

## 3. `dotmac-files` adoption — real status per adopter

`packages/dotmac-files/EXTRACTION.toml`: `status = "audit-complete"`,
`contract_consumers = []`, `candidate_consumers = ["dotmac_erp",
"dotmac_academy_app", "dotmac_vendor_control_plane"]`, kernel floor `0.1.0a56`.

| Adopter | Ordered | State today | Blocked on |
|---|---|---|---|
| **ERP** | cutover 1 | not a consumer | **E8** — the Organization-to-Tenant decision, one transaction authority, one request-scoping GUC contract, composed lineages without copied revisions, and proof that ERP's existing organization isolation is not weakened. `dotmac_kernel.db` and stateful lineages are `defer-db` in ERP's ledger. |
| **Academy** | cutover 2 | not a consumer | ERP retiring its local owner first (product-first order), then the same exact pin |
| **Vendor CP** | candidate cutover 3 | not a consumer | the platform plane; must use `PlatformScope()` and a real durable artifact relation and **must not manufacture a product tenant merely to claim adoption** |

**The module is dual-plane and complete.** ADR-0023's 2026-08-13 amendment
records it: `TenantStoredFile` and `PlatformStoredFile` over one
persistence-free physical engine, both tables declared on the `fi` manifest.
ADR-0020 A5 therefore marks **P8b (object storage) as MET** — a correction to
the implementation plan's stale "gap-listed and blocked".

### 3.1 What the document workstream needs from it, and what it must not take

The chain is three owners, and the middle one does not exist yet:

```
billing (invoice meaning) ──InvoiceDocumentFactV1──> rendering owner (P8a, UNOWNED)
rendering (bytes)         ──RenderedDocumentV1─────> dotmac-files (storage)
```

- **Rendering binds a `DocumentStorageProvider`; it does not import
  `dotmac-files`.** ADR-0020 A1/A5: *"billing does not import `dotmac-files` —
  the assembly wires rendering to files."* The same rule binds rendering: the
  **assembly** wires it, and the port is a provider-neutral seam with a fake, so
  the rendering contract suite runs with no object store.
- **`dotmac-files` owns bytes, never meaning.** ADR-0022's boundary is explicit
  and the rendering owner must not push across it: no domain attachment meaning,
  no visibility, no retention policy, no authorization, no document generation.
  The **relation** between a rendered invoice artifact and the invoice is the
  *domain's*, referencing an opaque file id — exactly the shape Vendor CP's
  licensing keeps (`LicenceDelivery` remains the business authority; its
  domain-owned relation references `platform_stored_files.id`).
- **Rendering inherits files' adoption debt, not its schedule.** Because
  `dotmac-files` has zero contract consumers, the *first* production write
  through `RenderedDocumentV1` would also be the first production use of
  `mod_files` in its adopting assembly. That is two unproven things landing in
  one change and should be sequenced apart.
- **P8a remains genuinely unowned.** ADR-0020 A5: *"P3 durable timers, P4
  document numbering, and P8a rendering remain real, gap-listed
  prerequisites."*

---

## 4. Cross-module end-to-end acceptance tests

These are the tests that prove the four modules **compose** — which is a
different claim from each module being correct, and is the claim ADR-0020's
"Enforcement required with implementation" section demands. Every one of them
must live in the **starter**, because ADR-0020 A6 makes the starter the owner of
"the contracts, conformance tests, and the reference assembly", and because a
conformance test living in an adopter would prove one composition rather than
the contract.

Each carries a **sensitivity proof**, per ADR-0018 decision 5: *"A newly-covered
region that passes must be shown to FAIL without its ratchet. Otherwise a clean
run is indistinguishable from the guard having stopped looking."* An assertion
with no demonstrated failure mode is not evidence.

### E1 — obligation → invoice → receivable → dunning consequence

**Proves:** the four-hop chain reaches a consequence request with **no module
importing another**, through assembly-wired contracts only.

**Shape:** a test assembly (a fixture composition root, not `app/assembly.py`)
constructs the four owners over fakes, then drives:
`RatedObligationOutputV1` → `AcceptRatedObligationV1` → invoice issued →
`ReceivablePositionV1` → `ReceivablesReader` → `ConsequenceRequestV1`, and
asserts the consequence names the owning service and carries the exact `Money`
and currency the obligation started with.

**Sensitivity proof, three of them, because this test can pass vacuously three
ways:**
1. delete the assembly's wiring for one hop → the chain must **stop**, not
   silently produce a default consequence;
2. introduce a direct `import dotmac_billing` inside the collections fixture →
   the import-linter contract must fail (this is the proof the *contract* is
   live, not just that the test is);
3. change the currency mid-chain → `MixedCurrencyObligation` must be raised, not
   coerced.

### E2 — invoice fact → render → stored artifact

**Proves:** billing's obligation ends at an immutable fact; rendering produces
bytes; files stores them; and the artifact relation belongs to the domain.

**Shape:** `InvoiceDocumentFactV1` → `RenderRequestV1` → `RenderedDocumentV1` →
`DocumentStorageProvider` (fake) → a domain-owned relation holding an opaque
file id. Assert: the renderer performed **no arithmetic** (every amount on the
rendered output is byte-identical to a `Money` on the fact); a re-render
produces a new `RenderedDocumentV1` revision and **does not** increment the
fact's `fact_version`; and the stored artifact's digest matches what the
reconciler recomputes.

**Sensitivity proof:** a `SilentlyTruncatingRenderer` fixture that drops a line
item must fail the test — the digest/roundtrip assertion has to bite. A renderer
that branches on `TenantScope` vs `PlatformScope` must fail the
identical-`projection_digest` assertion (this is the rendering dossier's own
stated sameness proof, and it belongs in the cross-module suite too, because
scope sameness is what lets one renderer serve both assemblies).

### E3 — no module imports another, and none imports an assembly

**Proves:** ADR-0024 § 2 and ADR-0020 A1 mechanically.
**Shape:** the existing import-linter contracts, extended with the new
distributions.
**Sensitivity proof:** a fixture module importing a sibling must fail.
`test_feature_manifests.py`'s byte-for-byte sync check between `FEATURE_MODULES`
and the features contract is the precedent for keeping the module list from
drifting.

### E4 — one deployment binds exactly one commercial authority

**Proves:** ADR-0020 § 3 — *"The assembly refuses to boot with two
authorities."*
**Shape:** construct a composition root binding both `INTERNAL` and
`PROVIDER_OWNED`; assert `TwoFinancialWriters` (or the named refusal) at
**construction**, not at first request. Assert `external_finance` mode cannot
reach the local invoice/subledger writer at all — the writer object must not be
constructed, per the spec's "construction, not comparison" rule.
**Sensitivity proof:** removing the refusal must make the double-bind succeed.

### E5 — both planes, one behaviour

**Proves:** ADR-0023 § 1 for each stateful commercial module.
**Shape:** the scratch-database canary pattern (§ 5.5), asserting the live
catalog holds **exactly** the tables the manifest declares, read from
`module.tables` / `module.platform_tables` rather than a second hand-written
list that can drift; RLS ENABLEd and FORCEd on every tenant table; `app_user`
REVOKEd across all seven privileges and their column-level forms on every
platform table; the online platform role holding schema `USAGE` plus a DML
privilege; and no FK crossing the planes.
**Sensitivity proof:** the existing precedent is exact — build a `mod_` schema
with a deliberately broken table inside a rolled-back transaction and assert the
audit flags it (`test_module_schema_catalog.py`'s `_PROBE_OWNER`,
`test_rls_catalog.py::test_audit_flags_a_broken_table`).

### E6 — no PSP client, credential, verifier or provider name anywhere

**Proves:** ADR-0020 A3 and ADR-0024 § 6/§ 7 — the modules hold money decisions
and never transport.
**Shape:** a forbidden-name sweep over the three packages for provider
identifiers, credential shapes, webhook signature verification, retry/checkpoint
engines, and currency names as identifiers or defaults.
**Sensitivity proof:** a fixture declaring `PAYSTACK_SECRET` or a `NGN` default
must fail. Note the existing `external-connector-baseline.json` ratchet is the
*fleet-wide* version of this measurement; E6 is the per-package version and the
two must not be conflated.

### E7 — no general-ledger concept in billing

Described in § 2.4. Listed here because it is a cross-module test in substance:
it is the executable half of the billing/ERP boundary.

### Two measurement gaps these tests cannot close

Recorded so they are not mistaken for coverage:

1. **An existing ratchet these cutovers depend on has no sensitivity proof.**
   `dotmac_sub:tests/architecture/billing_scheduled_sweep_baseline.txt`, enforced
   by `test_billing_target_architecture.py::test_no_new_scheduled_financial_sweep`
   (`:117-132`), is two-directional but, as the collections team measured, *"**Sensitivity
   proof: NOT PRESENT.** No test in that file proves the detector still fires. A
   clean run is currently indistinguishable from `scheduled_sweep_names()`
   returning an empty set."* Two collections retirement rows depend on that file
   to prove they happened. **Fix it before either row is evidenced**; it costs a
   planted sweep and one assertion.
2. **The fleet baseline has no bucket for two of the four modules.**
   `scripts/fleet_decomposition_sweep.py` has no `collections` and no
   `documents` family — `dunning` folds into `billing-revenue` (`:154`) and
   `document_sequences`/`generated_document`/`legal_documents` into
   `sales-agreements` (`:201-203`). So
   `docs/inventories/fleet-decomposition-baseline.json` **cannot show
   collections or document-rendering duplication shrinking**, and neither
   module's retirement is visible at fleet level. Whether that is worth
   correcting is the matrix owner's call, not this workstream's — but a
   programme that reports progress against a baseline with no row for half its
   work is reporting on the wrong number.

---

## 5. Pins, and what breaks them

### 5.1 The discipline

| Rule | Mechanism |
|---|---|
| An adopter pins an **exact** module release, never a range and never a path dependency | the files adoption plan states it: *"No product may use a relative path dependency, copy the module migration, add a second session factory, or keep a permanent dual writer."* `tests/architecture/test_lockfile_path_packages.py` is the starter-side guard. Two adopter-side precedents already exist and should be copied rather than reinvented: ERP's `tests/architecture/test_kernel_compatibility.py` *"rejects any `^`/`~`/`>=`/`*` drift"*, and Vendor CP's private index is declared `priority = "explicit"` so *"ONLY the three names that declare `source = "forgejo"` are pulled from here — no dependency-confusion aggregation of public names"* |
| A module publishes a **public** `versions_dir()` so a consuming assembly can compose its lineage without deriving a path | Vendor CP found this gap first and recorded it in code: `0.1.0a1` of both vendor modules shipped without one, and *"Deriving the path from `__file__` is a workaround, not the pattern… The fix belongs upstream — a `versions_dir()` on each module, shipped in 0.1.0a2."* It landed. **Every commercial module must expose it from day one** — billing's dossier already says *"exposes `versions_dir()` from day one"* |
| A module declares a `kernel_floor` = **the highest of everything it needs** | `.github/release-modules.json`; `tests/architecture/test_kernel_version_sync.py` keeps two *separate, reasoned* maps — `LEDGER_ALLOCATION_RELEASES` and `CAPABILITY_RAISED_FLOORS` — so "this one is special" has to say why |
| The kernel's runtime `__version__` equals its distribution version | `test_runtime_version_matches_the_distribution_version`, compared against the **pyproject source**, not `importlib.metadata` — in an editable install the installed metadata can itself be stale, and asserting against it would let both values be wrong together |
| The kernel is at least every module floor | `test_the_kernel_is_at_least_every_module_floor` |
| A declared dependency floor is **proved**, not asserted | `scripts/kernel_floor_check.sh` builds the wheel, installs it into a clean venv with floor versions pinned exactly, and exercises the supported surface — because widening a floor is a *support claim* |

### 5.2 The two things that set a floor

1. **The kernel release that allocated the module's schema.** An earlier kernel
   raises `UnallocatedNamespaceError` and the module cannot register at all.
2. **The kernel release that added a capability the manifest consumes.** An
   earlier kernel raises `TypeError` at import, *before* the allocation check is
   reached.

The floor is the **higher** of the two. Current examples:
`dotmac-ticketing` = `0.1.0a56` (capability: the `requires` contract) though its
allocation was `a39`; `dotmac-integration` = `0.1.0a58` (its own allocation,
which is higher than the `a53`/`a56` capabilities it consumes).

For the commercial modules, both inputs will be at least `0.1.0a56` — they
consume `platform_tables` (`a53`) and the prerequisite contract (`a56`) — so the
floor will be **their own allocation release**, whatever the kernel is at when
the package-creation diff lands.

### 5.3 What breaks the pinning discipline

1. **An adopter pinned below a module's floor.** Measured on `origin/main`,
   2026-08-14:

   | Repo | Kernel pin | vs the `0.1.0a56` module floor |
   |---|---|---|
   | Starter | `0.1.0a59` | — (owns it) |
   | **ERP** | `0.1.0a56` | ✅ at floor — and installs none of the three |
   | **Sub** | `0.1.0a50` | ❌ six behind; cutover 2 for billing/subscriptions, cutover 1 for collections |
   | **Vendor CP** | `0.1.0a45` | ❌ **eleven behind**; cutover 1 for billing and subscriptions |

   **Neither commercial cutover-1 product can compose a current module lineage
   at its present pin**, and no commercial plan names the intermediate repin.
   Vendor CP's is the harder of the two: `a45 → a56` crosses `platform_tables`
   (`a53`) and the prerequisite contract (`a56`), converting four physical
   `depends_on` edges into logical bindings and exposing the plane
   misdeclaration in § 5.4. This is the single most likely thing to surprise the
   first cutover.
2. **A path dependency instead of a release.** Silently makes the adopter track
   an unreleased tree, and makes "which version is in production" unanswerable.
3. **A copied migration.** The ADR-0014 failure verbatim — *"an idempotency
   facility whose table each product hand-creates is a library, not a kernel."*
4. **A module naming a foreign revision.** True only in the assembly that wrote
   it (§ 1.6).
5. **`kernel_floor` recorded but not enforced.** The release allowlist's own
   comment records this happening: *"An unenforced floor is how the same
   allocation drifted across a56 twice before landing on a58."*
6. **Two branches minting one kernel alpha.** The allowlist records the fix —
   `a42`/`a43` belonged to the upstream train, so the vendor modules renumbered
   to `a44`/`a45` rather than the foundations renumbering around them. *"Two
   branches minting one kernel version collide in the changelog and leave a
   consumer unable to say which a42 it pinned."*
7. **A wheel that ships the manifest but drops a migration.** Caught by
   `wheel_contents.required` — and `dotmac-integration`'s entry explains why
   both its migrations are listed: the starter does not compose it, so *"a wheel
   that shipped the manifest but dropped a migration would therefore fail first
   in the Integrator's deployment, not here."* **Every commercial module is in
   that same position and must list every migration in `required`.**

### 5.4 A live conformance defect the commercial modules must not copy

`dotmac-release-catalog` declares `tables=("release_artifacts",
"artifact_attestations")` and `dotmac-entitlement-allocation` declares
`tables=("allocations","allocation_entries")` — the **tenant**-plane field —
although both hold platform catalog tables (no `tenant_id`, `app_user` REVOKEd).
ADR-0023 § 6 calls them *"platform-only and correctly have no tenant plane"*,
and its Consequences records the hole: *"`dotmac-release-catalog` has platform
tables in `mod_rel` and was never caught by this gate, only because it is not
composed into the starter's own assembly."*

`NamespaceRegistry.declared_platform_tables` returns empty for both, and
`catalog.py` states the fallback: *"A module that declares no `platform_tables`
— every module shipped before this — is audited exactly as before this ADR"*,
i.e. under the **tenant** contract. Composing either today would fail the live
catalog gate against the wrong contract, or — worse, if the gate were relaxed —
pass while unprotected.

**The hole is no longer latent — it is composed in production-bound code.**
Vendor CP `origin/main` runs both lineages (§ 2.3) and its `ARCHITECTURE.md`
correctly describes `mod_rel`'s tables as *"platform catalogues: `platform_api`
may use the published grants and `app_user` is denied."* The behaviour is right;
the **declaration** is wrong; and Vendor CP pins kernel `0.1.0a45`, which
predates `platform_tables` (`a53`) entirely, so its gate has no platform concept
with which to notice. **The misdeclaration will surface the moment Vendor CP
repins to `a56` for the commercial modules** — which is a prerequisite for
adopting any of them.

A related coverage narrowing already exists there and must not be inherited:
`REVOKE ALL … FROM app_user` is emitted at
`alembic/versions/v002_offer_versions.py:60` and `v004_contracts.py:31`, but
`tests/migration/test_vendor_migration_rehearsals.py::test_platform_role_access_and_tenant_role_denial`
**asserts denial only for `vendor_accounts` and ten licence tables**. The guard's
scope is narrower than its name — ADR-0018 decision 1's exact failure shape:
*"When a guard's docstring claims broader scope than its configuration
implements, the configuration is the defect."*

**The requirement for the commercial modules is therefore explicit:** each
stateful commercial module declares `platform_tables` from **revision 1**, and
its plane declarations are asserted against the live catalog by an E5 canary in
the same change, over **every** declared table rather than a hand-picked subset.
The correction of `mod_rel` and `mod_ealloc` is not this workstream's to make;
it is recorded as a finding.

### 5.5 The composition-coverage pattern for a module the starter must not install

Established by `test_files_isolation.py`, `test_imports_isolation.py`,
`test_integration_isolation.py` and `test_approvals_isolation.py`, and the only
correct shape for all four commercial modules:

> create a throwaway database; compose the kernel lineage plus the module's own
> lineage in a **temporary** Alembic configuration; migrate as `app_admin`;
> drive assertions as the online `app_user` (and `platform_api`); audit both
> planes against `module.tables` / `module.platform_tables`.

Why it matters, in the integration canary's own words: *"Without this file a
normal CI push exercises neither `ig_0001` nor `ig_0002`… **The migrations would
first run in production.**"*

**These canaries require real Postgres and run on Git-hosted CI only.** They are
not a substitute for adoption evidence, and this document does not treat them as
one.

---

## 6. What this document does not do

It does not lift ADR-0017's moratorium, does not claim P11, does not grant any
of the four modules the owner-directed exception `dotmac-approvals` received
under ADR-0026, does not create a package, namespace, prefix, branch label,
lineage, model, migration or dossier, does not name a short code, and does not
edit `namespaces.py`, `migration_bindings.py`, `alembic.ini`, `app/assembly.py`
or `.github/release-modules.json`.

It leaves four questions open and names their owners:

| Open | Owner |
|---|---|
| A dual-plane module's tenant prerequisite in a platform-only assembly (§ 1.8) | an architecture decision — Michael, with the three commercial teams |
| **P3 durable timers** — three named dependents (subscriptions, collections, billing), **no owner**. Source would be `dotmac_sub:app/services/runtime_durable_timers.py` + `app/models/durable_timer.py` + `tests/test_durable_timers.py` | **unassigned** |
| **P4 document numbering** — two named dependents (billing, rendering), **no owner**, and the qualifying source has **no test** (`billing-extraction-dossier`: *"Sub has NO test for `next_invoice_number` at all"*). ERP already has five implementations; Sub adds a sixth inside a renderer | **unassigned** |
| **`InvoiceArtifactReconciler`** — required before the Vendor CP billing cutover; both teams place it on the assembly and rendering § 6.6 rejects the assembly as an owner (§ 2.6) | **unassigned** |
| The `AccountingFactV1` push vs ERP's pull contract (§ 2.4) — an authority-migration question needing an accepted ADR under ADR-0024 § 5 | the billing owner **and** ERP's finance-integration owner, jointly |
| The four contract disagreements in § 2.6 (G1, G2, G3, G5) | the four commercial teams, jointly; the naming ones need a single ruling |
| `mod_rel` / `mod_ealloc` plane misdeclaration and the Vendor CP guard narrowing (§ 5.4) | the vendor-module owner |
| `dotmac-release-catalog`'s status: `audit-complete` in the starter, *"the permanent owner"* in Vendor CP (P11 dashboard § 9 contradiction 8) | the vendor-module owner |

Two of these — P3 and P4 — are **unassigned shared facilities with named
dependents**, and this document deliberately does not assign them. Under
ADR-0017 decision 2 each may be started only when a live adoption is blocked on
it today, which is why the collections plan's G2 is worded as *"demand-pulled
only when the Sub collections cutover is actually blocked on scheduling"*.
Naming a future consumer is not demand.
