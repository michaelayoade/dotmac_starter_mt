# P11 — the ADR-0017 lineage gate: measured status

**As of:** 2026-08-14
**Starter:** `49f9ccf` (`origin/main`); working tree read at `b55c9a5`
(`docs/whatsapp-connector-extraction-dossier`, which is *behind* main and does
not carry `packages/dotmac-approvals`)
**Kernel:** `0.1.0a59` (`packages/dotmac-kernel/pyproject.toml` at `49f9ccf`)
**Sub:** `origin/dev` `27c76aaee` (2026-08-14 07:55 +0100) · `origin/main`
`73c35f49` (2026-08-14 08:50 +0100)
**ERP:** `origin/main` `4df1190d` (2026-08-14) — the local checkout `0f4b1698`
is three tenancy commits behind and materially different
**Vendor CP:** `origin/main` `63acff1` (2026-08-13) — the local checkout is on
`feat/adopt-kernel-a46-and-vendor-modules` `8984801`, which is **not an ancestor
of main**
**Measured by:** reading checked-in evidence and git history only. **No test,
migration, server, container or database was executed** — CI is this fleet's
only acceptance owner, and P11 is a claim about production, which no local run
could evidence either way.

This is an inventory: it records what is true today, not what should be true.
Read it under the two standing cautions in [`README.md`](README.md) — facts go
stale, and a row here is not permission to build anything.

---

## 0. The answer, in one paragraph

**P11 is not met, and no document in the fleet claims it is.** `dotmac_sub` —
the product ADR-0017 decision 4 nominates as the reference adopter — pins the
kernel as an ordinary registry dependency, composes **none** of its migration
lineage, and carries an executable gate whose current expectation is that the
kernel lineage *fails at its first revision*. Sub's own `docs/adr/0009` (the
operator-tenant decision ADR-0017 explicitly assumes is ratified) still reads
`Status: proposed`. There is no production deployment evidence anywhere: no
runbook, no changelog entry, no deploy-script reference, no release tag. Every
one of the nine allocated installable modules therefore stands at
`contract_consumers = []`, and every one of the four commercial modules is
blocked behind this single gate.

---

## 1. What P11 is, precisely

P11 is not a capability in the billing prerequisite table; it is ADR-0017's
external gate, named `P11` by
[`2026-08-11-billing-subscriptions-collections.md`](../superpowers/plans/2026-08-11-billing-subscriptions-collections.md)
§ "Tier 2 — the external gate, not another capability".

ADR-0017 decision 2, verbatim:

> No new facility from the gap list is started until **the kernel's migration
> lineage runs in a product database in production**.

And the ADR is explicit about why the exit is the LINEAGE rather than "consumes
persistence" (§ "Why the exit is the LINEAGE and not 'consumes persistence'"):

> `dotmac_sub` imports `dotmac_kernel.models.Tenant` today and runs it against a
> `tenants` table — so by one reading the gate was already met on the day it was
> written. It is not met, and the distinction is the whole point. Sub's own
> migration `508` created that table. A kernel MODEL on a product-owned table is
> code sharing: the product still owns the schema, the ordering, and the
> upgrade.

Three exclusions follow from that wording and are load-bearing for everything
below:

| Not P11 | Why |
|---|---|
| A kernel ORM model over a product-created table | Explicitly ruled out by the paragraph above. Sub does exactly this today. |
| A rehearsal, a scratch database, a disposable environment | "in production". Sub's rehearsals run in disposable databases and say so. |
| `alembic stamp`, a copied migration, a product conditional inside a kernel migration | AGENTS.md rule 14: "`alembic stamp`, a blanket `IF EXISTS`, and a product conditional inside a kernel migration are not bindings and stay forbidden." |
| The starter's own assembly running its own kernel lineage | ADR-0017 decision 4: the proof must be "on a real product, rather than in the starter's own assembly". |

---

## 2. Terminology hazard — "the gate is closed" means the opposite in two places

This must be fixed before it causes a wrong decision, because both readings are
already in checked-in text.

- Four of the commercial documents write **"ADR-0017's P11 gate is closed"**
  meaning *shut, therefore blocking*
  (`collections-extraction-dossier.md:12`, `collections-sources.md:17`,
  `subscriptions-extraction-dossier.md:15`,
  `2026-08-14-subscriptions-public-contracts.md:5`).
- The execution plans write **"G1 — Clear P11"** and **"when P11 is cleared"**
  meaning *satisfied, therefore unblocking*
  (`2026-08-14-collections-sub-vendor-cp-adoption.md:330`,
  `collections-extraction-dossier.md:14`).

A reader who meets "P11 is closed" first and "P11 must be cleared" second can
reasonably conclude the gate has already been resolved. **Recommended
vocabulary, used consistently in this document: P11 is `UNMET`. It becomes
`MET`.** "Open" and "closed" should not be used of this gate at all.

This is a documentation defect owned by the four teams' documents, not by this
one. It is recorded here rather than corrected, per this team's scope.

---

## 3. What Sub's platform-adoption ledger actually claims

`dotmac_sub:docs/PLATFORM_ADOPTION_LEDGER.md`, 877 lines. Last three commits
touching it: `0ce53058` (2026-08-13 22:17), `bde32103` (2026-08-13 20:28),
`8568fb0b` (2026-08-13 18:20).

**The string `P11` does not appear anywhere in `dotmac_sub`.** Neither does
ADR-0017's lineage gate by any other name; ADR-0017 lives only in this
repository. Sub's own name for the same gate is **S7**, defined in the ledger's
classification table:

> | **defer-db** | Touches kernel persistence (tables, engine, migrations) **or
> is gated on the S7 operator-tenant/migration ADR; forbidden until that gate is
> green** |

### 3.1 What it claims

| Claim | Evidence |
|---|---|
| The kernel is **pinned** as an installed registry dependency | `dotmac-kernel==0.1.0a50` at `pyproject.toml:50,65,78,326`, `poetry.lock:1130`, `tests/architecture/test_kernel_compatibility.py:59` |
| The product assembly **declares composition** (S3) | ledger status line: "slice S3 (composition declared in `app/composition.py`)" |
| A set of kernel facilities are **consume-pure** or **adapt** | `money`, `capabilities`, `profiles`, `assembly`, `testing`, `features`, `providers`, and the whole `settings_*` family |
| `dotmac_kernel.models` is **partial (S7a)** | "`Tenant`/`TenantDomain` ONLY, per ADR-0009" |
| Package compatibility was **rehearsed** on a disposable environment | ledger L109-117, 103 tests |

### 3.2 What it explicitly does NOT claim — verbatim

These are the load-bearing lines. Every one is a disclaimer the ledger volunteers
about itself:

> L66-70: "This does not import `dotmac_kernel.db`, activate FORCE RLS, **compose
> the kernel migration lineage**, move the revision-0001 ratchet, backfill Party
> projections, **or authorise deployment**."

> L88-90: "Sub does **not compose a new kernel migration lineage**, import a
> kernel authority into `app/`, or transfer any business owner."

> L116-117: "This is package-compatibility evidence, **not a lineage, authority,
> merge, or deployment claim**."

> L133-137: "This pin makes the immutable contract available for compatibility
> and rehearsal; **it does not claim an authority or lineage cutover**."

> L166-168: "This is the contract pin and lineage rehearsal the gate needs, **not
> migration composition or identity adoption**."

> L209-213: "…the full-lineage ratchet, **which currently stops at the expected
> 0001 collision**…"

> L855-857 (S1 acceptance): "**runs no kernel migrations** — Sub's `alembic.ini`
> keeps `script_location = alembic` with no `version_locations`; kernel revisions
> are inert package data".

**There is no row in the ledger classified `adopted` for any DB-bearing kernel
facility.** `dotmac_kernel.db` and `dotmac_kernel.migrations` are both
`defer-db S7`.

### 3.3 The four commercial dossiers point at a field that does not exist

Three of the four teams instruct a future reader to verify P11 "recorded in
Sub's `PLATFORM_ADOPTION_LEDGER.md`" — e.g.
`collections-extraction-dossier.md:140`:

> "G1 clear ADR-0017 P11 — the kernel migration lineage composed and RUNNING in
> Sub's production database, recorded in Sub's PLATFORM_ADOPTION_LEDGER.md (a
> prepared branch, a copied migration or a stamped revision does not satisfy
> it)"

The instruction is correct about the *standard*. It is wrong about the
*artifact*: the ledger has no P11 row, no lineage-status field, and no
production-deployment field to read. **Closing P11 requires adding that field to
Sub's ledger, not merely finding it.** This is listed as step 7 in § 6 below.

---

## 4. Prepared vs merged vs deployed

The three states must not be collapsed. Measured on `dotmac_sub` at
`origin/dev` `27c76aaee` / `origin/main` `73c35f49`.

| State | Status | Evidence |
|---|---|---|
| **Prepared** | ✅ substantial | Kernel pin `0.1.0a50`; facility classification ledger; `tests/integration/test_kernel_lineage_rehearsal.py` (an executable gate); a transactional `0021` adoption canary; collision ratchets; per-table dispositions in [`sub-lineage-dispositions.md`](sub-lineage-dispositions.md) |
| **Merged** | ⚠️ **only the predecessor** | `feat/kernel-lineage-r1-disposition` tip `5bc69a51` is an ancestor of **both** `origin/dev` and `origin/main`. Its diff (`git show --stat 5bc69a5`) is `app/db.py`, `app/services/operator_tenant.py`, `app/services/session_hooks.py`, `app/services/sot_registry/domains/tenancy.py`, 4 docs, 2 tests. **No migration. No alembic change.** The branch is named for the lineage; its content is the transaction-scope GUC predecessor. |
| **Merged (lineage proper)** | ❌ none | The branches actually carrying lineage work — `agent/sub-kernel-a40-lineage` (`0cd71fa3`), `agent/sub-kernel-a40-adoption-proof` (`9f636c77`), `feat/kernel-pin-a40` (`9f6f9f36`), `integration/kernel-adoption` (`4ca42f3c`) — are ancestors of **neither** `origin/dev` nor `origin/main`. |
| **Deployed to production** | ❌ **none, and the absence is the finding** | `CHANGELOG.md`: zero hits for "kernel" or "lineage". `scripts/deploy.sh`, `deploy_production.sh`, `deploy_staging.sh`, `scripts/ci/`, `scripts/ops/`: zero hits for "kernel". `docs/runbooks/PRODUCTION_DEPLOYMENT.md`: zero hits for "kernel" or "lineage" — and Sub has 20+ runbooks for other cutovers, so the omission is not a repository-wide habit. No release tag names a kernel-lineage cutover. |

**The single production measurement that does exist** (Sub `docs/adr/0009`
L40-54, read-only against `selfcare.dotmac.io`, 2026-08-11) records `tenants` =
1 row and `domain_settings` = 577 rows. That proves **Sub's own migrations
507/508/509 ran in production** — which ADR-0017 pre-emptively rules out as
satisfying the gate (§ 1 above).

---

## 5. The executable gate, and where it currently stops

`dotmac_sub:tests/integration/test_kernel_lineage_rehearsal.py` is the most
honest artifact in the fleet on this subject. Its docstring:

> "This is the ADR-0017 gate, executed rather than described… The kernel lineage
> is **expected to fail today**, and the point is to pin WHERE… when it stops
> failing, the gate is closed and the kernel lineage runs in a product database."

```
L67:  EXPECTED_FIRST_FAILURE = "0001_initial_tenant_schema"
      test_the_kernel_lineage_fails_exactly_where_expected  →  pytest.raises(KernelLineageFailure)
```

**The lineage fails at revision one.** Not at revision fourteen with a residue
of hard cases — at the root. That is consistent with
[`sub-lineage-dispositions.md`](sub-lineage-dispositions.md) § "Where the gate
actually sits: revision `0001` is the atomic unit", which measured that **five
of the ten name collisions are created inside revision `0001` alone**, that
`0001` applies atomically, and that `user_credentials` is the hardest of the
five and gates the rest:

> "The kernel binds a credential to one `party_id`; Sub binds to **three**
> principal kinds (`subscriber_id`, `system_user_id`, `reseller_user_id`) under
> an exactly-one CHECK. Until it is decided whether Sub's principals become
> parties, `0001` cannot be dispositioned, and therefore **no** collision after
> it can be reached."

**Consequence for planning: there is no partial-credit path.** Revisions are the
packaging unit, not difficulty. The four easy dispositions cannot land first and
move the ratchet forward; `0001` moves as one change or not at all.

### 5.1 Which lineage creates each contested table in Sub today

Every one is Sub's. **Zero tables in `dotmac_sub` are created by a kernel
migration.**

| Table | Created by | Owner |
|---|---|---|
| `domain_settings` | `alembic/versions_archive/799a0ecebdd4_initial_schema.py:249` | Sub |
| `audit_events` | `alembic/versions_archive/799a0ecebdd4_initial_schema.py:52` | Sub (additively expanded by `526_audit_events_kernel_r1.py`) |
| `roles` | `alembic/versions_archive/799a0ecebdd4_initial_schema.py:923` | Sub (expanded by `528_roles_kernel_r1_additive.py`) |
| `user_credentials` | `alembic/versions_archive/799a0ecebdd4_initial_schema.py:2194` | Sub (touched by `527_credential_party_binding_additive.py`) |
| `parties` | `alembic/versions/349_party_role_foundation.py:26` | Sub |
| `party_roles` | `alembic/versions/349_party_role_foundation.py:91` | Sub (business capacities; the kernel's same-named table became `party_role_grants` at kernel `0022`, ADR-0019) |
| `tenants` | `alembic/versions/508_operator_tenant_tables.py:48` | **Sub**, hand-written to mirror `dotmac_kernel.models.Tenant`. Its own docstring: "**Sub writes this migration itself rather than composing the kernel's Alembic lineage.**" |
| `tenant_domains` | `alembic/versions/508_operator_tenant_tables.py:65` | Sub |
| `idempotency_records` | **absent from Sub** | kernel-only; Sub's own owner is `idempotency_keys` (`120_add_idempotency_keys.py:31`) |

`dotmac_kernel.models.Tenant` is imported at exactly one site —
`app/services/operator_tenant.py:29` — over a Sub-created table. That is the
"kernel MODEL on a product-owned table" ADR-0017 names as *not* the gate.

---

## 6. What would close P11, in order, with the evidence each step produces

Ordered because the dependencies are real, not because a phase plan looks tidy.
Steps 1-3 are Sub's; step 4 is a joint kernel/Sub change; steps 5-7 are
mechanical once 4 lands.

| # | Step | Owner | Evidence it produces | Status today |
|---|---|---|---|---|
| **1** | **Ratify Sub's ADR-0009.** ADR-0017 § "Open decisions this ADR does not make" #1 says in terms: "This ADR assumes it is ratified; if it is rejected, decision 4 must be revisited." | Sub | `dotmac_sub:docs/adr/0009-operator-tenant-bridge.md` `Status:` changes from `proposed` to `Accepted` | ❌ still `Status: proposed`, five days after ADR-0017 assumed otherwise |
| **2** | **Decide the `user_credentials` principal question** — whether Sub's `subscriber_id` / `system_user_id` / `reseller_user_id` principals become parties. This is the same decision `PARTY_PRINCIPAL_CONTEXT_BINDING` needs; the two tracks share the gate. | Sub | An accepted Sub ADR naming the principal mapping and the shadow/verification phase | ❌ not started as a decision record |
| **3** | **Write Sub's S7 migration ADR** — the operator-tenant/migration decision the ledger's `defer-db` class is gated on. It must disposition all five revision-`0001` collisions (`tenants`, `tenant_domains`, `roles`, `user_credentials`, `audit_events`) as ONE change, plus the RLS/GUC contract, the tenant function, and the three role grants. | Sub | An accepted ADR in `dotmac_sub:docs/adr/` (today that directory holds only 0000-0010, and no S7 ADR exists) | ❌ absent |
| **4** | **Land the `0001` disposition** in the kernel and Sub together, and **remeasure against the current kernel**. The dispositions in [`sub-lineage-dispositions.md`](sub-lineage-dispositions.md) were measured against kernel `0.1.0a40`; the kernel is now `0.1.0a59` and Sub pins `0.1.0a50`. | Kernel + Sub | A kernel release; a remeasured disposition table with its as-of commit | ❌ measurement is 19 alpha releases stale |
| **5** | **Compose the lineage in Sub.** Add the kernel version location to `dotmac_sub:alembic.ini` (`version_locations`, absent today) and add `app/migration_bindings.py` with Sub's `PrerequisiteBinding`s for `tenant_scope_catalog.v1` and `module_database_roles.v1`, installed from `alembic/env.py`. | Sub | `alembic.ini` diff; `make migration-gate`-equivalent green in Sub CI; the rehearsal test's `EXPECTED_FIRST_FAILURE` **deleted**, not advanced | ❌ `alembic.ini` has no `version_locations` key at all |
| **6** | **Run it in Sub production**, through Sub's normal deploy path, as `app_admin`, never on container boot (AGENTS.md rule 13). | Sub | A production deploy record: changelog entry, release tag, and a runbook entry in `docs/runbooks/` — none of which exist today for this cutover | ❌ |
| **7** | **Record the fact where the fleet is told to look.** Add an explicit lineage/production row to `dotmac_sub:docs/PLATFORM_ADOPTION_LEDGER.md` naming the deployed revision, the date, the database, and the alembic head — because three commercial dossiers already instruct readers to verify P11 there and today there is no such field. | Sub | The ledger row; `dotmac_kernel.db` / `dotmac_kernel.migrations` move off `defer-db` | ❌ |

**Step 5a, easy to miss:** Sub pins kernel `0.1.0a50`. Every module released
since `0.1.0a56` has a floor above that pin (see § 7.2). Composing the kernel
lineage does not by itself make Sub able to compose a *module* lineage; that
needs a repin as well. The two are separate changes and the second is currently
invisible in every plan.

---

## 7. The honest adoption board

### 7.1 Installation is not adoption

ADR-0017 decision 1 is the measure: *"contracts consumed **in a product**, not
contracts shipped"*, and the ticketing amendment sharpens it: *"adoption is
measured by contracts consumed **in place of** a product's own writer, not by
installations counted."*

The machine-checked form of this lives in
`tests/architecture/test_product_first_extraction.py`: a dossier's `status` is
**derived from the length of `contract_consumers`** — zero forces
`audit-complete`, one forces `adopted`, two force `reuse-proven`. A package
cannot self-describe as adopted.

### 7.2 Every package, measured

At starter `49f9ccf`, kernel `0.1.0a59`.

| Distribution | Dossier `status` | `contract_consumers` | Kernel floor | Composed in the reference assembly? | Real production consumer? |
|---|---|---|---|---|---|
| `dotmac-kernel` | `historical-pre-rule` | `["dotmac_starter_mt"]` | n/a | yes (it *is* the assembly's kernel) | the starter only; **no product runs its lineage** |
| `dotmac-ui` | **`reuse-proven`** | `["dotmac_sub","dotmac_academy_app","dotmac_erp"]` | n/a | yes | ✅ **yes** — Sub `main` `73c35f4` and ERP `main` `462b6fa` both pin released `0.1.0a7` and resolve the package-owned `empty_state` template through their real Jinja loaders |
| `dotmac-template-studio` | `audit-required` | `[]` | `0.1.0a56` (capability-raised from `a13`) | yes — `assembly.py` + `alembic.ini` `ts` lineage | ❌ |
| `dotmac-ticketing` | `audit-complete` | `[]` | `0.1.0a56` (capability-raised from `a39`) | yes — `assembly.py` + `alembic.ini` `tk` lineage | ❌ ERP is cutover 1 behind E8 |
| `dotmac-release-catalog` | `audit-complete` | `[]` | `0.1.0a44` (own allocation) | **no** in the starter — but **Vendor CP `origin/main` composes its `rl` lineage** | ⚠️ **contested — see § 7.5** |
| `dotmac-entitlement-allocation` | `audit-complete` | `[]` | `0.1.0a45` (own allocation) | **no** in the starter — Vendor CP composes its `ea` lineage in **shadow** | ❌ explicitly *"a shadow installation, not adoption"*; four cutover gates unmet |
| `dotmac-application-directory` | `audit-complete` | `[]` | `0.1.0a56` (capability-raised from `a46`) | **no** — deliberately; its consumer is the separate `dotmac_workspace` assembly | ❌ intended adopter is "a local scaffold with no remote, no lock, no CI and no authentication path" |
| `dotmac-files` | `audit-complete` | `[]` | `0.1.0a56` (capability-raised from `a54`) | **no** — scratch-DB canary only | ❌ ERP cutover 1 behind E8 |
| `dotmac-imports` | `audit-complete` | `[]` | `0.1.0a56` (capability-raised from `a55`) | **no** — scratch-DB canary only | ❌ |
| `dotmac-integration` | `audit-complete` | `[]` | `0.1.0a58` (own allocation) | **no** — deliberately; consumer is `dotmac_integrator` | ❌ that assembly does not exist yet |
| `dotmac-approvals` | `audit-complete` | `[]` | `0.1.0a59` (own allocation) | **no** — scratch-DB canary only | ❌ |

**Exactly one distribution in the fleet has a real production consumer, and it
is the one with no database at all.** `dotmac-ui` is dependency-free — no
kernel, no ORM, no web framework — which is precisely why it could be adopted
while every stateful module waits. That is not a coincidence to be celebrated;
it is the measurement ADR-0017 decision 1 predicted.

### 7.3 Installed-but-not-adopted, and rehearsed-but-not-installed

Three distinct populations, kept separate because collapsing them is how a
dashboard starts lying:

- **Composed in the starter, zero product consumers:** `dotmac-template-studio`,
  `dotmac-ticketing`. Their lineages run in the starter's own database, which
  ADR-0017 explicitly excludes from counting.
- **Not composed anywhere, proven only by a scratch-database canary:**
  `dotmac-files`, `dotmac-imports`, `dotmac-integration`, `dotmac-approvals`,
  `dotmac-application-directory`.
  Each has a `tests/test_*_isolation.py` that builds a throwaway database,
  composes the kernel plus its own lineage in a temporary Alembic config, and
  audits the planes. This is real evidence that the migration *applies* — and
  `test_integration_isolation.py` says why it exists: *"Without this file a
  normal CI push exercises neither `ig_0001` nor `ig_0002`… The migrations would
  first run in production."* It is not evidence of adoption.
- **Allocated, released, and composed nowhere at all:**
  `dotmac-release-catalog`, `dotmac-entitlement-allocation`. Both carry a known
  latent conformance defect — see the conformance spec § "the
  `tables=`/`platform_tables=` misdeclaration", recorded by ADR-0023's own
  Consequences.

### 7.4 The kernel floor gap nobody has costed

| Repo | Kernel pin (`origin/main`) | Source |
|---|---|---|
| Starter (owns it) | **`0.1.0a59`** | `packages/dotmac-kernel/pyproject.toml` |
| **ERP** | **`0.1.0a56`**, exact, no range | `pyproject.toml:53`, landed by PR #289 (`e4b84387`, 2026-08-14 09:55) |
| **Sub** | **`0.1.0a50`** | `pyproject.toml:50,65,78,326` + `poetry.lock:1130` |
| **Vendor CP** | **`0.1.0a45`** (extras `testing`, `licensing`) | `pyproject.toml` |
| Floor of every module released since ADR-0006's D1 amendment | **`0.1.0a56`** or higher | `.github/release-modules.json` |

Three consequences, none of which appears in any plan:

1. **Sub cannot compose any current module lineage at its present pin**,
   whatever happens to P11. Six alpha releases separate `a50` from the lowest
   current module floor. Every commercial plan that says "after P11, pin the
   module" is missing an intermediate kernel repin.
2. **Vendor CP is eleven alphas below** — and it is cutover 1 for billing and
   subscriptions. At `a45` it predates `platform_tables` (`a53`) entirely, so it
   cannot even *declare* a dual-plane module, and it predates the logical
   prerequisite contract (`a56`), so it still uses physical `depends_on` edges
   and has **no `migration_bindings.py` at all**.
3. **ERP is the only product at the module floor** — and ADR-0020 A6 says ERP
   installs none of the three commercial modules.

**The product ordered first for two of the three commercial modules is the
product furthest from being able to compose one.**

### 7.5 Vendor CP already composes four lineages — and this changes the shape of the board

Measured on Vendor CP `origin/main` `63acff1`. This is the single most
significant fact the P11 discussion has been missing.

`alembic.ini` deliberately carries **no** `version_locations`; composition is
programmatic in `src/vendor_cp/migrations.py`:

```python
def composed_version_locations() -> str:
    """Kernel, two independent modules and vendor migration lineages."""
    return (
        f"{kernel_versions_dir()} "
        f"{release_catalog_versions_dir()} "
        f"{entitlement_allocation_versions_dir()} "
        f"{VENDOR_VERSIONS}"
    )
```

Its docstring: *"one revision graph, four separately-owned lineages."*
`src/vendor_cp/assembly.py` registers `dotmac_release_catalog.module` and
`dotmac_entitlement_allocation.module` on the `ProductAssemblySpec`.

**So the kernel migration lineage IS composed in a product assembly today — in
Vendor CP, not Sub.** What is missing is the other half of P11's sentence:

| P11 clause | Vendor CP |
|---|---|
| "the kernel's migration lineage" | ✅ composed, `kernel_versions_dir()` |
| "runs in a product database" | ✅ in CI, against a disposable `docker-compose.test.yml` Postgres |
| "**in production**" | ❌ **no `CHANGELOG.md`, no runbooks, no `deploy.sh`, no container build, no environment manifest.** The only deploy artifact is a 40-line `scripts/migrate.py` and `make migrate`. |

Vendor CP's own `docs/ARCHITECTURE.md` disclaims it: *"This change composes the
owner; it does **not yet** add a publish HTTP adapter or **claim a production
cutover**."*

**Two things follow, and both are decisions this document does not make:**

- **P11's reference adopter may be the wrong one.** ADR-0017 decision 4 chose
  Sub on cost, proof and risk-order grounds, all measured in August against a
  Sub whose gate was "one decision". Sub's gate has since resolved into three
  unratified decisions and an executable canary that fails at revision one.
  Vendor CP has the composition already built and needs only a deploy path.
  ADR-0017's own § "A stop rule needs a start rule" is the relevant test: *"If
  the gate is not being worked, the moratorium is not disciplined restraint — it
  is the kernel idling while products pay the cost it was meant to remove, and
  it should be lifted or re-argued rather than left running."*
- **Vendor CP has no adoption ledger at all.** There is no
  `PLATFORM_ADOPTION_LEDGER.md` or equivalent; `docs/ARCHITECTURE.md` carries
  the role informally. If Vendor CP ever became P11's evidence, there is no
  artifact to record it in — the same missing-field problem § 3.3 records for
  Sub, one step worse.

### 7.6 ERP has the D1 prerequisite contract implemented — the first real one outside the starter

`dotmac_erp:app/migration_bindings.py` exists on `origin/main`:

```python
ASSEMBLY_PREREQUISITE_BINDINGS = (
    PrerequisiteBinding(TENANT_SCOPE_CATALOG_V1.name,
                        "20260813_tenant_projection", "assembly"),
    PrerequisiteBinding(MODULE_DATABASE_ROLES_V1.name,
                        "20260814_database_roles",   "assembly"),
)
```

installed from `alembic/env.py:37`. Its docstring is the D1 amendment's
motivating case, proven rather than argued:

> "Kernel `0001_initial_tenant_schema` creates `public.tenants` unconditionally
> as its FIRST table. ERP hosts that same table in its own lineage, so kernel
> `0001` can never run here — `tests/integration/test_kernel_lineage_rehearsal.py`
> is a **permanent negative canary** proving exactly that, and **it can never go
> green**."

**Note the asymmetry, because it is easy to misread:** Sub's
`test_kernel_lineage_rehearsal.py` is expected to fail **today** and to go green
when P11 is met. ERP's file of the same name is expected to fail **forever** —
it proves a structural impossibility, not a backlog. Two files, one name,
opposite meanings. A reader auditing by filename would draw the wrong conclusion
about either.

**ERP's E8 decision exists and is partially implemented**, contrary to every
document in this repository that treats it as absent:
`dotmac_erp:docs/architecture/organization-tenant-boundary.md`, *"Status:
implemented context, projection and lineage-ratchet slices (E8 slices 3–5)"*,
deciding `tenant_id = organization_id` with *"no allocated second identifier,
nullable mapping, sentinel tenant, or mapping table"*, and
`20260813_tenant_projection.py` (469 lines) hosting `public.tenants`,
`public.tenant_domains` and `public.app_current_tenant_id()`.

What E8 still lacks, per ERP's own `kernel-0001-dispositions.md`: *"**Until this
is done for Seabone, `dotmac-files` cannot be composed into ERP** —
`fi_0001_stored_files` runs as `app_admin` and hits the same wall. The kernel
repin and the prerequisite bindings are necessary but not sufficient."* And
`alembic.ini` still has **no `version_locations`**, so ERP composes no foreign
lineage of any kind. Every tenancy change sits under `## [Unreleased]` in its
changelog; the last released version is `1.1.9` (2026-05-22).

---

## 8. Downstream blocked on P11

Consolidated from all four commercial teams' checked-in documents, plus the
non-commercial modules that share the gate.

### 8.1 The four commercial modules

| Module | Blocked on | Additionally blocked on |
|---|---|---|
| `dotmac-billing` | P11 — no package, namespace, prefix, lineage, or `EXTRACTION.toml` may be created (`billing-extraction-dossier.md:24,193`) | P4 document numbering (**no owner**), P3 durable timers (**no owner**), P8a rendering |
| `dotmac-subscriptions` | P11 (`subscriptions-extraction-dossier.md:15,88,94`) | P3 durable timers; **a released, assembly-wired `AcceptRatedObligationV1` billing input** — which is itself behind P11. Also blocked on the contract gaps G1/G2 below: its S7 row repoints collections onto billing's receivable, and billing's `ReceivablePositionV1` carries no service period, which is the field `prepaid_policy.py:57` uses today |
| `dotmac-collections` | P11 — G1 of its own plan (`2026-08-14-collections-sub-vendor-cp-adoption.md:330`) | P3 durable timers (G2, demand-pulled only when the Sub cutover is *actually* blocked); Vendor CP additionally has no platform-plane consent ledger or delivery receipt loop |
| document rendering (`dotmac-document-rendering`, proposed **stateless**) | P11 by ADR-0020 § 6 (`document-rendering-extraction-dossier.md:260`) | P4 numbering; a `DocumentStorageProvider` binding onto `dotmac-files`, which is itself `audit-complete` with zero consumers |

Note the chain in the subscriptions row: it is blocked on billing, billing is
blocked on P11, and billing's own first cutover (Vendor CP) is a *different*
product from P11's reference adopter (Sub). **P11 clearing in Sub does not by
itself unblock the billing cutover in Vendor CP** — it lifts the moratorium on
starting the package; the Vendor CP composition is a separate proof.

### 8.2 The non-commercial modules on the same gate

| Module | Gate |
|---|---|
| `dotmac-files` | ERP's E8 — **partially met** (see § 7.6): the boundary decision is accepted and `20260813_tenant_projection` is implemented, but `alembic.ini` still composes no `fi` lineage and ERP's own dispositions doc says *"the kernel repin and the prerequisite bindings are necessary but not sufficient"*. ADR-0022's amendment additionally says the file sequence "does not bypass the broader decision that Sub goes first for kernel persistence" |
| `dotmac-imports` | `EXTRACTION.toml` `next_action`: "Finish Sub's reference kernel-lineage production gate, **then** ERP's E8" — P11 named as the first prerequisite in the module's own dossier |
| `dotmac-ticketing` | ERP's E8, described in ADR-0017's ticketing amendment as "a HARD prerequisite, not a parallel track" |
| `dotmac-approvals` | Vendor CP is cutover 1 and, per its own amendment, is *not* blocked on E8 — but the module is not release-registered until a live Postgres migration and catalog gate has passed |

### 8.3 Two shared facilities with NO named owner

Recorded as unassigned dependencies with named dependents. **This document does
not assign them; that requires a decision from Michael.**

| Facility | Named dependents | Where the source would come from | Current state |
|---|---|---|---|
| **P3 — durable timers** | `dotmac-subscriptions` (recurring renewals), `dotmac-collections` (grace expiry, dunning offsets, retry ladders, arrangement due dates), `dotmac-billing` (retry ladders) | `dotmac_sub:app/services/runtime_durable_timers.py`, `app/models/durable_timer.py`, `tests/test_durable_timers.py` | Gap-listed and blocked. Three dependents, no owner, no dossier. The collections plan's G2 correctly says it must be extracted **only when a cutover is actually blocked on it** — demand-pulled, not anticipated. |
| **P4 — document numbering** | `dotmac-billing` (invoice, credit-note and receipt series), the document-rendering workstream | Unclear — and the billing dossier records that **Sub has no test for `next_invoice_number` at all**, so the qualifying source is both gap-listed *and* untested | Gap-listed and blocked. No owner, no dossier, no parity test to port. ADR-0017's Context notes ERP already has five numbering implementations. |

Neither may be built inside a commercial module's schema. The collections plan
is explicit: "Do not put `durable_timers` inside the collections schema, and do
not bundle numbering, rendering, or unrelated scheduling use cases into this
slice."

### 8.4 Open gates this document does not treat as resolved

- **Team 1's A2 / recurring-occurrence classification.** ADR-0020 A4 resolves
  A2b's *ownership*; the occurrence-vs-obligation classification of live rows is
  cutover work, is not done, and its executable half — the category-2
  forbidden-name guard over 13 occurrence fields and 2 contract fields — does
  not exist.
- **Team 2/4's official-artifact relation.** Both teams agree billing should own
  it and then specify **incompatible** relations: different uniqueness (partial
  vs composite), different repair (append-and-supersede vs update-in-place),
  different idempotency keys (checksum in vs out), different digest field names.
  Recorded as gap **G3** in
  [`commercial-retirement-ledger.md`](commercial-retirement-ledger.md).
- **The named reconciler.** `InvoiceArtifactReconciler` is *required before the
  Vendor CP billing cutover*, both teams place it on the assembly, and the
  rendering spec § 6.6 rejects the assembly as an owner: *"An assembly-owned
  relation table is assembly-local state with no module owning its tests, its
  migration or its drift repair."* **It is a blocking, unassigned owner.**
- **P11 itself**, per everything above.

---

## 9. Contradictions found

Each of these is a place where one checked-in document is contradicted by
another, or by the code. They are the finding, not a to-do list this team may
act on.

1. **ADR-0017 assumes a ratification that has not happened.** ADR-0017 § "Open
   decisions" #1: "This ADR assumes [Sub's ADR-0009] is ratified; if it is
   rejected, decision 4 must be revisited." Sub's ADR-0009 reads
   `Status: proposed` at `origin/dev` today, with two amendments layered on a
   still-unratified decision. **ADR-0017 decision 4 — "Sub goes first" — rests
   on an unmet assumption, and the ADR itself named the consequence.**

2. **"P11 is closed" vs "clear P11".** § 2 above. Two opposite meanings of
   "closed" across six documents.

3. **The named evidence artifact has no such field.** Three dossiers instruct a
   reader to verify P11 in Sub's `PLATFORM_ADOPTION_LEDGER.md`. The string `P11`
   does not occur in `dotmac_sub`, and the ledger has no lineage-status or
   production-deployment field. § 3.3.

4. **A merged branch is named for work it does not contain.** Sub's
   `feat/kernel-lineage-r1-disposition` (`5bc69a51`, an ancestor of both
   `origin/dev` and `origin/main`) contains no migration and no alembic change.
   A reader auditing by branch name would conclude lineage work is merged. § 4.

5. **The disposition measurement is two kernels stale.**
   [`sub-lineage-dispositions.md`](sub-lineage-dispositions.md) is measured
   against kernel `0.1.0a40`; ADR-0017's own § "The gate, measured" table is
   against `0.1.0a27`; Sub pins `0.1.0a50`; the kernel is at `0.1.0a59`. Three
   different baselines are in circulation and the ADR's own table is the oldest
   of them. The document itself anticipates this: "it exists because the
   previous number was measured against a kernel two months of releases ago."

6. **ADR-0023 § 6 says two modules are "correctly" platform-only; their
   manifests say otherwise.** `dotmac-release-catalog` declares
   `tables=("release_artifacts","artifact_attestations")` and
   `dotmac-entitlement-allocation` declares
   `tables=("allocations","allocation_entries")` — the **tenant**-plane field —
   though both hold platform catalog tables. ADR-0023's own Consequences
   records the hole ("was never caught by this gate, only because it is not
   composed into the starter's own assembly") without recording that the
   manifests were never corrected. Composing either today would fail the live
   catalog gate against the *wrong* contract. Detail and remedy shape are in the
   conformance spec.

7. **The kernel floor gap is absent from every plan.** § 7.4. Sub's pin
   (`0.1.0a50`) is below the floor of every module released since `0.1.0a56`.
   No commercial plan, and no module `EXTRACTION.toml` `next_action`, names the
   intermediate repin.

8. **A module the starter calls `audit-complete` is called "the permanent
   owner" by its adopter.** `packages/dotmac-release-catalog/EXTRACTION.toml`
   says `status = "audit-complete"`, `contract_consumers = []`, and lists the
   remaining gate as *"an exact vendor-CP pin to the module release exposing its
   installed lineage locator, composition of that lineage, and a successful
   migration rehearsal."* **All three have happened.** Vendor CP `origin/main`
   pins `dotmac-release-catalog = "0.1.0a2"`, composes its `rl` lineage through
   `versions_dir()`, registers its manifest on the assembly spec, and its
   `docs/ARCHITECTURE.md` calls it *"the **permanent owner** of immutable
   release artifacts and attestations."* The starter's dossier is **stale**, and
   `test_product_first_extraction.py` derives `status` from
   `contract_consumers`, so correcting one requires correcting both. *(Whether
   this reaches `adopted` also turns on the production question — Vendor CP has
   never deployed. Both readings are defensible; what is not defensible is the
   two repositories disagreeing silently.)* **Owner: the vendor-module owner.**

9. **`dotmac-entitlement-allocation` is correctly labelled in both places, and
   is the model for how row 8 should read.** Vendor CP composes its `ea`
   lineage but says in terms: *"This is deliberately a **shadow installation**,
   not adoption: `vendor_cp.allocations` remains the sole authoritative
   writer and there is no dual-write… Until all four gates pass, the module
   tables are empty and non-authoritative. A partial switch would either invent
   product identity or create two writers."* Recorded as a contradiction only by
   contrast: two sibling modules in one assembly, one described accurately and
   one not.

10. **Two files named `test_kernel_lineage_rehearsal.py` mean opposite things.**
    Sub's is expected to fail today and go green at P11. ERP's is a *permanent
    negative canary* that *"can never go green"*. § 7.6.

11. **ERP's ledger contradicts ERP's own `pyproject.toml` on the same commit.**
    `docs/PLATFORM_ADOPTION_LEDGER.md` says *"Current lock:
    `dotmac-kernel==0.1.0a24`"* at lines 16, 87 and 145 (*"**Current E8
    measurement — `0.1.0a24`.**"*), while `pyproject.toml:53` on the same commit
    (`e4b84387`, PR #289) says `0.1.0a56` — and line 476 of the ledger itself
    says *"E8 (kernel `0.1.0a56`)"*. The evidence-pin header was not updated by
    the change that moved the pin.

12. **ERP does not consume `AccountingFactV1`, and its checked-in contract
    forbids the mechanism.** `AccountingFact` has **zero occurrences** in ERP.
    Its live integration with Sub is a document-level HTTP **pull**
    (`app/tasks/dotmac_sub.py` → `sync_invoices`/`sync_payments` →
    `post_unposted_*`, watermarked by
    `models/finance/ar/dotmac_sub_sync_watermark.py`), and
    `docs/dotmac_sub_tax_accounting_contract.md` states: *"ERP **pulls**
    immutable or versioned source facts from Sub… **No second push/outbox path
    is permitted for the same accounting decisions.**"* Billing's
    `AccountingFactV1` is specified as push-after-commit through the kernel
    outbox. **This is an authority/transport contradiction between two
    repositories' accepted contracts, not an integration detail**, and it is
    recorded in the retirement ledger as gap G8.

13. **Two incompatible `ReceivablePositionV1`s share one version name.**
    Billing's spec § 2.3: identity `(scope, billing_account_id, currency)` +
    `as_of_version`, third field `prepaid_funding`, delivered as a published
    fact. Collections' spec § 2.3: identity
    `(source_owner, exposure_ref, source_version)`, third field
    `funding_available`, delivered by a synchronous `ReceivablesReader` port.
    Neither document acknowledges the other. A `V1` that means two things is
    the exact failure `dotmac_kernel.prerequisites`' versioning rule exists to
    prevent — *"a prerequisite whose verified contract changes is a NEW
    prerequisite"* — applied here to a message contract instead.

14. **`dotmac-approvals` is on `origin/main` but absent from the working tree
   this programme is being written against.** `origin/main` is `49f9ccf`, which
   carries `packages/dotmac-approvals` and a ninth `MIGRATION_OWNER_LEDGER` row
   (`ap` / `mod_approvals`, kernel floor `0.1.0a59`). The branch the four teams'
   documents are being drafted on (`docs/whatsapp-connector-extraction-dossier`,
   `b55c9a5`) is behind main and does not. **Any short code, migration prefix or
   branch-label proposal validated against the working tree is validated against
   a stale ledger.**

---

## 10. How to re-measure this document

Nothing here required a running database, and a re-measurement must not either.

```
# P11 itself — the only three questions that matter
grep -rn "version_locations" <sub>/alembic.ini            # must exist and name the kernel path
grep -rn "EXPECTED_FIRST_FAILURE" <sub>/tests/            # must be GONE, not advanced
grep -rn "Status:" <sub>/docs/adr/0009-*.md               # must read Accepted

# then, and only then, the production question, which is not a grep:
#   a deploy record in <sub>/CHANGELOG.md, a release tag, and a runbook entry
#   naming the deployed kernel revision, the date and the alembic head.

# adoption board
for f in packages/*/EXTRACTION.toml; do grep -H "^status\|^contract_consumers" $f; done
python3 -c "import json;d=json.load(open('.github/release-modules.json'));\
[print(k,v['kernel_floor'],v['db_schema']) for k,v in d['modules'].items()]"

# floor gap — read origin/main, NOT the local checkout: all three sibling
# repos were measurably behind their remotes on 2026-08-14
git -C <sub> show origin/main:pyproject.toml | grep -n "dotmac-"
git -C <erp> show origin/main:pyproject.toml | grep -n "dotmac-"
git -C <vcp> show origin/main:pyproject.toml | grep -n "dotmac-"

# which assemblies actually compose a foreign lineage
git -C <erp> show origin/main:alembic.ini | grep -n version_locations   # none
git -C <vcp> show origin/main:src/vendor_cp/migrations.py               # four
git -C <sub> show origin/main:alembic.ini | grep -n version_locations   # none

# the accounting-fact contradiction (contradiction 12)
grep -ril "accountingfact" <erp>/                                       # zero
```

If any grep above disagrees with this document, this document is wrong and
should be re-run — not trusted.
