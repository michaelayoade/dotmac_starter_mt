# Kernel / assembly migration split — design (pre-Task-1 gate)

> **Status:** design only — no migration edited, no code moved. Produced 2026-07-30 to satisfy the
> gate raised on the Task 0 audit: splitting migration 0004 is not a file move, and existing v0.8
> databases already at head `0007` have executed it. This doc specifies the revised lineage,
> adoption, and the rehearsals that must pass before Task 1 touches `alembic/`. Companion to
> `docs/superpowers/reviews/2026-07-18-kernel-surface-audit.md` § "Migration ownership".
>
> **Conditionally accepted with four amendments (Michael, 2026-07-30), incorporated below:**
> 1. **Independent assembly lineage.** `a001` is its own lineage root (`down_revision = None`,
>    branch-labelled) with `depends_on = "0007_platform_identity"` — NOT a linear continuation of
>    the kernel chain. The graph therefore has **two heads** (kernel + assembly); the one-head
>    assertion is replaced by an **expected-head-per-lineage** assertion.
> 2. **Runtime-only rollback procedure.** Once `a001` is recorded, the old v0.8 migrator cannot
>    locate it, so `deploy.sh <old-tag>` fails in Alembic. A rollback procedure un-records `a001`
>    (via `alembic stamp`, preserving the table) *before* the image rollback; the rehearsal runs the
>    **actual rollback command**, not just an old-app boot check.
> 3. **Full-contract adoption verification.** The adopt path verifies the COMPLETE table contract —
>    PK, FK + on-delete, indexes, per-column nullability/defaults, unique + check constraints, the
>    exact RLS policy expressions, the exact grants, and the absence of unsafe grants — fail-closed
>    on any deviation.
> 4. **`a001.downgrade()` is destructive → fail-closed by default.** It refuses to drop the adopted
>    table (and its tenant data) unless an approved operator runbook explicitly authorizes it. The
>    self-verifying adoption revision stays the normal path; a manual `alembic stamp` is only an
>    emergency fallback after equivalent verification and recorded operator evidence.

## The real shape of the problem

Migration `0004_custom_fields` (`down_revision = 0003_party_identity`) does **two things of
different ownership** in one `upgrade()`:

| step in 0004 | object | owner |
|---|---|---|
| `_create_custom_field_definitions_table()` + `_apply_rls()` + `_grant_roles()` | `custom_field_definitions` table, its RLS policy, its grants | **assembly** (the `custom_fields` feature) |
| `_add_party_custom_fields_column()` | `parties.custom_fields` JSONB NOT NULL DEFAULT `'{}'` | **kernel** — the column is declared on `app.core.models.Party`, a kernel model |

So the naive "move 0004 to the assembly" is wrong twice over: it would (a) drag a kernel column
into the assembly, and (b) reparent the middle of a chain that existing databases have already run.

Current lineage (single linear chain, single head `0007`):

```
0001 → 0002 → 0003 → 0004_custom_fields → 0005 → 0006 → 0007   (head)
```

Existing v0.8 databases record `alembic_version = 0007` and physically contain **both** the
`parties.custom_fields` column and the `custom_field_definitions` table (both from the old 0004).

## Design decision (approach A′ — no reparent, minimal edit, idempotent-adoptive assembly revision)

Two moves, chosen to leave the kernel lineage's **revision ids and parentage completely
unchanged** (the safest possible thing for databases already at `0007`):

1. **Reduce kernel `0004` to its kernel half only.** `0004_custom_fields` keeps its id, its
   `down_revision = 0003_party_identity`, and its position in the chain, but its body loses the
   assembly half — it adds *only* the `parties.custom_fields` column (rename the docstring/purpose
   to "party custom-fields value column"; the revision **id stays `0004_custom_fields`** so no
   ancestor edge in the graph changes). `downgrade()` drops only that column.
2. **Add a new assembly revision** `a001_adopt_custom_field_definitions` in the **assembly's own
   version directory**, as the **root of an independent assembly lineage** (amendment 1):
   `down_revision = None`, `branch_labels = ("assembly",)`, and
   `depends_on = "0007_platform_identity"`. `depends_on` expresses "apply the kernel through 0007
   first" WITHOUT making the assembly a linear continuation of the kernel — the assembly is its own
   lineage that can evolve (a002, a003, …) on its own chain while pinning the kernel head it needs.
   Its id is **new** — 0004 is explicitly *not* reused. It creates `custom_field_definitions` + RLS
   + grants, written **idempotent-adoptive** so the same revision both *creates* on a fresh database
   and *adopts* (verifies, no-op) on an existing v0.8 database.

   Consequence: the revision graph has **two heads** — the kernel head (`0007_platform_identity`)
   and the assembly head (`a001`, advancing to the assembly lineage's latest). `alembic upgrade
   heads` applies both; `depends_on` guarantees `a001` runs only after `0007`. This is deliberate
   ownership separation, not an accident to be asserted away — see "Head detection" below.

**Why not reparent 0005→0003 and delete 0004** (the alternatives considered): reparenting rewrites
an ancestor edge that every existing database has already traversed, and deleting a revision from
the middle of a run chain invites `alembic` head/ancestry surprises for zero benefit. A′ changes no
edge and no id; it only shrinks one revision's body (removing DDL that existing databases will never
re-run anyway) and appends a new leaf. **Folding the column into 0003** was also rejected — editing
the party-creation migration is a larger blast radius than shrinking 0004, with no upside.

**`parties.custom_fields` stays kernel.** It is declared on the kernel `Party` model; the
`custom_fields` feature reads/writes it generically through that kernel model. Making the *column*
assembly-owned would require removing it from `app.core.models.Party`, which desynchronizes the
kernel model from the schema — out of scope. Flagged as a separate, non-blocking architecture
question: "should the kernel `Party` carry a feature-shaped column at all?" (An empty assembly gets
an unused `'{}'` column — harmless, but not free of smell.)

## Revised lineage — two independent lineages (amendment 1)

**Kernel base lineage** (shipped as kernel package data; ids + parentage unchanged), branch-labelled
`kernel`:

```
0001 → 0002 → 0003 → 0004_custom_fields(column-only) → 0005 → 0006 → 0007        [head: kernel = 0007_platform_identity]
```

**Assembly lineage** (shipped in the reference assembly / any product assembly; own version
directory), branch-labelled `assembly`, an independent chain rooted at `a001`:

```
a001_adopt_custom_field_definitions → (a002 → …)                                 [head: assembly = a001, then latest]
    down_revision = None
    branch_labels = ("assembly",)
    depends_on    = "0007_platform_identity"     ← cross-lineage pin, NOT a parent edge
```

The two lineages are **separate roots** joined only by `a001`'s `depends_on` pin. The assembly chain
extends on its own ids (`a002`, …) as the assembly grows; it never becomes a linear tail of the
kernel. `alembic` runs with `version_locations = [<kernel base dir>, <assembly dir>]`, so the two
directories compose into **one graph with two heads** — `kernel` (`0007`) and `assembly` (`a001`,
then latest). The kernel package alone exposes only the `kernel` head; a product assembly exposes
both. `env.py` becomes **assembly-owned** and builds `target_metadata` from the kernel `Base`
(identity/tenancy/settings/platform/audit models) **plus** the assembly's own models
(`custom_field_definitions`) — it already imports both today; only the ownership label, the branch
labels, and the `version_locations` composition are added.

## Behaviors specified

### Fresh database (empty assembly — kernel base only)

`alembic upgrade heads` over the kernel base runs `0001…0007`: creates the three roles, the RLS
machinery, all kernel tables, **and `parties.custom_fields`** (reduced-0004), but **no
`custom_field_definitions`**. Heads = `{kernel: 0007}`. This is exactly the empty-assembly-boot
target (Task 4): a kernel-only database has the kernel column but not the feature table.

### Fresh database (reference/product assembly)

`version_locations` includes the assembly dir → `alembic upgrade heads` runs the kernel chain to
`0007` then `a001` (ordered by its `depends_on`), which finds no `custom_field_definitions` and
**creates** it (+ RLS + grants). Heads = `{kernel: 0007, assembly: a001}`.

### Existing v0.8 database (already at `0007`; has column + table)

The database already contains both objects (old 0004). Adoption is automatic and self-verifying:

- Kernel side: nothing to do — already at `0007`, reduced-0004 is never re-run.
- Assembly side: `alembic upgrade heads` wants `a001`. `a001.upgrade()` is **idempotent-adoptive**:
  1. If `custom_field_definitions` is **absent** → create table + RLS + grants (the fresh path).
  2. If **present** → verify the **complete table contract** (amendment 3) before recording the
     revision, fail-closed (**raise**) on ANY deviation — never silently stamp over a divergent
     table. The verification asserts every element the fresh `create` path would have produced:
     - **Primary key** — `id` is the PK (uuid).
     - **Foreign key + on-delete** — `tenant_id → tenants(id)` with `ON DELETE CASCADE`.
     - **Indexes** — `ix_custom_field_definitions_tenant_id` on `(tenant_id)`.
     - **Columns: presence, type, nullability, and server defaults** — every column
       (`entity_type`/`field_code`/`field_name`/`field_type` NOT NULL; `is_required` default
       `false`; `display_order` default `0`; `show_in_form`/`show_in_detail` default `true`;
       `show_in_list`/`is_active` defaults; `created_at`/`updated_at` default `now()`; the nullable
       optionals) matches the model exactly — no missing, extra, retyped, or wrongly-defaulted column.
     - **Constraints** — the `uq_custom_field_definitions_tenant_entity_code` unique on
       `(tenant_id, entity_type, field_code)`, and the `ck_custom_field_definitions_field_type`
       CHECK whose expression is the exact 13-value `field_type IN (...)` list.
     - **Exact RLS expressions** — `relrowsecurity AND relforcerowsecurity` both true, and the
       `custom_field_definitions_tenant_isolation` policy present with **both** `USING` and
       `WITH CHECK` equal to `(tenant_id = app_current_tenant_id())` (compare the normalized
       `pg_policies.qual`/`with_check`, not just policy existence).
     - **Grants present** — `app_user` and `platform_api` each hold SELECT/INSERT/UPDATE/DELETE.
     - **Absence of unsafe grants** — no DML/DDL grant to `PUBLIC` or to any role outside the
       expected `{app_user, platform_api, app_admin}` set; the table is not owned by an unexpected
       role. (Reuses the same lens as `tests/test_rls_catalog.py`'s grant-boundary checks.)
  After `upgrade()` returns, `alembic` records `a001` as applied — the existing table is now owned by
  the assembly lineage with no re-creation and no data touched.

This makes `alembic upgrade heads` the single, safe command for both fresh and existing databases —
no separate manual `stamp` step, and the full-contract verification is the audit trail.

**Manual `alembic stamp` is an emergency fallback only** (not the normal path): permitted solely
after running the equivalent full-contract verification out-of-band AND recording operator evidence
(who, when, the verification output) — because a bare `stamp` records the revision without any
check and would happily stamp over a divergent or absent table. The self-verifying revision is
preferred precisely because it cannot do that.

### Head detection (amendment 1 — expected head per lineage)

The two-lineage graph has **two heads by design**; a global "exactly one head" assertion is wrong
and is replaced by an **expected-head-per-lineage** assertion keyed on branch labels:

- Kernel package in isolation: `alembic heads` → `{ kernel: 0007_platform_identity }` (one head).
- Any assembly: `alembic heads` → `{ kernel: 0007_platform_identity, assembly: a001 }` (the assembly
  head advances to `a002`, … as the assembly lineage grows). Exactly **two** heads, one per branch
  label.
- The deploy's `alembic upgrade heads` (plural) call is unchanged and correct — it applies both
  lineage heads. The guard test asserts the **set of branch-labelled heads equals the expected set**
  (`{kernel: <expected>, assembly: <expected>}`) — catching an *unexpected* head (an accidental
  fork within a lineage, or a lineage gone missing) without forbidding the intended second head.

### Downgrade (amendment 4 — `a001.downgrade()` is destructive, fail-closed by default)

- **`a001.downgrade()` drops the adopted `custom_field_definitions` table and ALL its tenant data.**
  It therefore **fails closed by default**: `downgrade()` raises
  `"refusing to drop adopted custom_field_definitions (destructive); set
  DOTMAC_ALLOW_DESTRUCTIVE_CF_DOWNGRADE=1 per the operator runbook to authorize"` unless that
  explicit authorization flag is set. Only with the flag (an approved operator runbook step) does it
  drop the policy/grants/table. This prevents an ordinary `alembic downgrade` from destroying tenant
  data. **Runtime rollback of a deploy does NOT use this path** — it uses the non-destructive
  procedure below.
- reduced-`0004.downgrade()` drops **only** `parties.custom_fields` (kernel). It is unrelated to the
  assembly table now. Because the assembly lineage is independent (`depends_on`, not a child edge),
  downgrading the kernel below `0004` while the assembly still records `a001` is itself a destructive
  cross-lineage operation and is out of scope for routine ops.

### Runtime-only rollback procedure (amendment 2)

**The problem:** once `a001` is recorded in `alembic_version`, the **old v0.8 migrator has no `a001`
script**, so `deploy.sh <old-v0.8-tag>` fails during its `alembic upgrade heads` step with
`Can't locate revision identified by 'a001_adopt_custom_field_definitions'`. Rolling the image back
is therefore NOT sufficient on its own, and "the old app boots" is the wrong thing to test.

**The procedure** (run with the NEW code still deployed, before the image rollback):

1. **Un-record `a001` without dropping the table** — `alembic stamp` the assembly lineage back to
   base so `alembic_version` no longer references `a001`, while the `custom_field_definitions` table
   and its data **remain in place** (this is exactly why `a001.downgrade()` is not used — that would
   drop the table). Concretely: `alembic stamp assembly@base` (or the explicit "remove `a001`"
   stamp), leaving the `kernel` head at `0007`.
2. **Verify** the table still exists and the recorded version is the kernel head only.
3. **Roll the image back** to the v0.8 tag (`deploy.sh <old-tag>`). Its `alembic upgrade heads` now
   sees only `0007` — which it recognizes — is a no-op, and the app boots against a database that
   still has the column and the table it expects.

The rehearsal (below) executes **this exact command sequence including the real `deploy.sh <old-tag>`
invocation** and asserts its Alembic step **succeeds** — not merely that the old app process starts.

### Required rehearsals (must pass before Task 1 edits `alembic/`)

Run against real Postgres (RLS), scripted and committed as the migration-split acceptance suite:

1. **Fresh empty-assembly:** kernel-base `upgrade heads` → assert `parties.custom_fields` exists,
   `custom_field_definitions` **absent**, heads `{kernel: 0007}` only.
2. **Fresh reference-assembly:** full `upgrade heads` → assert table present and the **full-contract
   verification passes** (PK/FK+on-delete/indexes/columns+defaults/constraints/exact-RLS/grants/
   no-unsafe-grants), heads `{kernel: 0007, assembly: a001}`; then the existing
   `tests/test_rls_catalog.py` audit passes unchanged.
3. **Existing-v0.8 adoption:** build a database at old-`0007` (run the *pre-split* migrations), then
   apply the split (reduced-0004 already applied there, so only `a001` runs) → assert `a001` **adopts**
   (no re-create, no error), the full-contract verification passes, assembly head `a001`, and row data
   in a seeded `custom_field_definitions` survives untouched.
4. **Adoption drift guard (per contract element):** on an existing database, independently break
   **each** verified element in turn — drop the unique constraint; drop/alter the check; drop the FK
   or change its on-delete; drop the index; null a NOT NULL / change a default; disable FORCE RLS;
   alter the policy `USING`/`WITH CHECK` expression; revoke a required grant; add a `PUBLIC` grant —
   and assert `a001` **raises** and does **not** record the revision for every case.
5. **Destructive-downgrade guard (amendment 4):** `a001.downgrade()` **without** the authorization
   flag → **raises**, table and data intact; **with** `DOTMAC_ALLOW_DESTRUCTIVE_CF_DOWNGRADE=1` →
   drops table+policy+grants. reduced-`0004.downgrade()` drops only `parties.custom_fields`.
6. **Runtime rollback rehearsal (amendment 2 — test the real command):** deploy the split image
   (records `a001`), then run the **actual** rollback: (a) `alembic stamp` the assembly lineage back
   to base (table preserved), (b) assert the table still exists and version = `{kernel: 0007}`, then
   (c) run the **real `scripts/deploy.sh <old-v0.8-tag>`** and assert its `alembic upgrade heads`
   **step completes successfully** (not merely that the process starts) and the v0.8 app serves.
   Negative control: skipping step (a) and running `deploy.sh <old-tag>` must reproduce the
   `Can't locate revision 'a001…'` Alembic failure — proving the stamp step is load-bearing.
7. **Expected-heads guard (amendment 1):** assert the branch-labelled head set equals
   `{kernel: 0007_platform_identity, assembly: a001}` (kernel-only package: `{kernel: 0007}`) —
   an unexpected or missing lineage head fails.

## Task-1 impact (recorded for the re-plan)

- Task 1 ships the **kernel base lineage `0001–0007`** (with reduced-0004, branch-labelled `kernel`)
  as kernel package data, plus a kernel `env.py` composition helper; it does **not** ship `a001`.
- The **assembly** owns the independent `assembly` lineage (`a001` + successors), its version
  directory, and the concrete `env.py` that sets `version_locations`, branch labels, and composes
  `target_metadata` from kernel `Base` + assembly models.
- The migration-split acceptance suite (rehearsals 1–7) is a Task-1 deliverable and a merge gate for
  the kernel-alpha PR — it, not a code review, is what proves existing-v0.8 compatibility. The
  runtime-rollback and per-element drift rehearsals are non-optional.
