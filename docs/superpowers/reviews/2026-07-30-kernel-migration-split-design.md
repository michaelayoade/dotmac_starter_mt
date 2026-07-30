# Kernel / assembly migration split — design (pre-Task-1 gate)

> **Status:** design only — no migration edited, no code moved. Produced 2026-07-30 to satisfy the
> gate raised on the Task 0 audit: splitting migration 0004 is not a file move, and existing v0.8
> databases already at head `0007` have executed it. This doc specifies the revised lineage,
> adoption, and the rehearsals that must pass before Task 1 touches `alembic/`. Companion to
> `docs/superpowers/reviews/2026-07-18-kernel-surface-audit.md` § "Migration ownership".

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
   version directory**, with `down_revision = 0007_platform_identity` (it extends the kernel head).
   Its id is **new** — 0004 is explicitly *not* reused. It creates `custom_field_definitions` + RLS
   + grants, written **idempotent-adoptive** so the same revision both *creates* on a fresh database
   and *adopts* (verifies, no-op) on an existing v0.8 database.

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

## Revised lineage

**Kernel base** (shipped as kernel package data; ids + parentage unchanged):

```
0001 → 0002 → 0003 → 0004_custom_fields(column-only) → 0005 → 0006 → 0007   (kernel head)
```

**Assembly** (shipped in the reference assembly / any product assembly; own version directory):

```
… 0007_platform_identity → a001_adopt_custom_field_definitions   (assembly head)
```

`alembic` runs with `version_locations = [<kernel base dir>, <assembly dir>]`, so the two directories
compose into one graph with a single head `a001`. The kernel package alone has head `0007`; a product
assembly's head is its own last revision (here `a001`). `env.py` becomes **assembly-owned** and builds
`target_metadata` from the kernel `Base` (identity/tenancy/settings/platform/audit models) **plus** the
assembly's own models (`custom_field_definitions`) — it already imports both today; only the ownership
label and the `version_locations` composition are added.

## Behaviors specified

### Fresh database (empty assembly — kernel base only)

`alembic upgrade head` over the kernel base runs `0001…0007`: creates the three roles, the RLS
machinery, all kernel tables, **and `parties.custom_fields`** (reduced-0004), but **no
`custom_field_definitions`**. Head = `0007`. This is exactly the empty-assembly-boot target (Task 4):
a kernel-only database has the kernel column but not the feature table.

### Fresh database (reference/product assembly)

`version_locations` includes the assembly dir → `alembic upgrade head` runs `0001…0007` then `a001`,
which finds no `custom_field_definitions` and **creates** it (+ RLS + grants). Head = `a001`.

### Existing v0.8 database (already at `0007`; has column + table)

The database already contains both objects (old 0004). Adoption is automatic and self-verifying:

- Kernel side: nothing to do — already at `0007`, reduced-0004 is never re-run.
- Assembly side: `alembic upgrade head` wants `a001`. `a001.upgrade()` is **idempotent-adoptive**:
  1. If `custom_field_definitions` is **absent** → create table + RLS + grants (the fresh path).
  2. If **present** → **verify** it matches the target shape before recording the revision:
     the expected columns/types, the `uq_custom_field_definitions_tenant_entity_code` unique
     constraint, the `ck_custom_field_definitions_field_type` check, `relrowsecurity AND
     relforcerowsecurity`, the `custom_field_definitions_tenant_isolation` policy in `pg_policies`,
     and `app_user`/`platform_api` DML grants. On match → no-op (adopt). On drift → **raise**
     (fail closed; do not silently stamp over a divergent table).
  After `upgrade()` returns, `alembic` records `a001` as applied — the existing table is now owned by
  the assembly lineage with no re-creation and no data touched.

This makes `alembic upgrade head` the single, safe command for both fresh and existing databases —
no separate manual `stamp` step, and the verification is the audit trail. (A plain
create-then-`alembic stamp a001` adoption is the documented fallback if idempotent DDL is rejected in
review; A′ prefers the self-verifying revision because it needs no out-of-band operator action and
cannot stamp over drift.)

### Head detection

- Kernel package in isolation: `alembic heads` → `0007`.
- Any assembly: `alembic heads` → its own leaf (`a001` for the reference assembly). **Exactly one
  head** — `a001.down_revision = 0007` keeps the graph linear; no branch/merge revision is introduced.
- The deploy's existing `alembic upgrade heads` (plural) call is unchanged and correct: one head, so
  `heads` == `head`. A guard test asserts `len(script.get_heads()) == 1` to catch an accidental branch.

### Downgrade

- `a001.downgrade()` drops `custom_field_definitions` (+ its policy/grants). On a database adopted
  from v0.8 this drops a table that predated the assembly lineage — **intended**; the assembly now
  owns it. The `parties.custom_fields` column is untouched (kernel).
- reduced-`0004.downgrade()` drops **only** `parties.custom_fields` (its former table-drop half is
  gone — that object is now `a001`'s). Downgrading below `0004` therefore requires `a001` to have been
  downgraded first (linear order guarantees this).

### Required rehearsals (must pass before Task 1 edits `alembic/`)

Run against real Postgres (RLS), scripted and committed as the migration-split acceptance suite:

1. **Fresh empty-assembly:** kernel-base `upgrade head` → assert `parties.custom_fields` exists,
   `custom_field_definitions` **absent**, head `0007`.
2. **Fresh reference-assembly:** full `upgrade head` → assert table present + RLS/grants/constraints
   correct, head `a001`; then the existing `tests/test_rls_catalog.py` audit passes unchanged.
3. **Existing-v0.8 adoption:** build a database at old-`0007` (run the *pre-split* migrations), then
   apply the split (reduced-0004 already applied there, so only `a001` runs) → assert `a001` **adopts**
   (no re-create, no error), verification passes, head `a001`, and row data in a seeded
   `custom_field_definitions` survives untouched.
4. **Adoption drift guard:** on an existing database, deliberately drop the unique constraint (or the
   RLS policy) before running `a001` → assert `a001` **raises** and does not record the revision.
5. **Downgrade:** from `a001`, `downgrade 0007` → table gone, column remains; `downgrade base` clean.
6. **Rollback rehearsal:** immutable-image deploy of the split, then roll back to the v0.8 image →
   assert the v0.8 app still boots against the adopted database (the table/column it expects are all
   present; `a001` being recorded is inert to the v0.8 app, which never queries `alembic_version`).
7. **Head-count guard:** `len(get_heads()) == 1` after the split.

## Task-1 impact (recorded for the re-plan)

- Task 1 ships the **kernel base migrations `0001–0007`** (with reduced-0004) as kernel package data,
  plus a kernel `env.py` composition helper; it does **not** ship `a001`.
- The **assembly** owns `a001`, its version directory, and the concrete `env.py` that sets
  `version_locations` and composes `target_metadata` from kernel `Base` + assembly models.
- The migration-split acceptance suite (rehearsals 1–7) is a Task-1 deliverable and a merge gate for
  the kernel-alpha PR — it, not a code review, is what proves existing-v0.8 compatibility.
