# Sub's kernel-lineage dispositions — ten collisions, five that are real work

**As of:** 2026-08-11 · **Kernel:** `0.1.0a40` (released, `b37d25b`) · **Sub:** `8e8c2c658` (`origin/dev`)

The per-table dispositions ADR-0017's gate requires. It supersedes the a27
6/2/11 figures as the working baseline, and it exists because the previous
number was measured against a kernel two months of releases ago.

> **Verified against a real database, 2026-08-11.** Everything below was
> originally derived by parsing migration files in two repositories, which took
> four attempts to get right. It has now been checked against the **staging Sub
> database** (seabone, `dotmac_sub`, alembic head `519_fiber_cost_items`, 595
> live tables) — read-only, no test load. **The parse and the database agree**
> once the baseline is aligned, which is the validation the method needed. See
> "Measured against the database" below for what changed and what it settles.

> **Superseded in part, 2026-08-12.** ADR-0019 renamed the kernel's RBAC grant
> to `party_role_grants` (migration `0022`, kernel `0.1.0a41`), so `party_roles`
> **no longer collides at lineage head**: nine collisions, five unions. The
> title's "ten" is the measured baseline, kept because the corrections below
> are the point of the document. The chain still creates the old name in `0003`,
> which reduces that disposition rather than removing it — see "Update
> 2026-08-12". Two `user_credentials` corrections are noted in place.

> **Correction, 2026-08-11 (same day).** This document first said TWELVE
> collisions and proposed deleting `people`/`person_roles` from the kernel.
> Both were wrong: kernel revision `0003`'s `upgrade()` already drops those two
> tables, so they do not exist at lineage head and never collided. **The count
> is TEN**, former Group C is empty, and the rest of the analysis stands. What
> made the measurement finally trustworthy is described under "Method" below.

**The headline: ten name collisions, but only five need design work.** Two are
already the kernel's own shape, two are byte-identical, and one is a near-match.
Grouping them that way is most of the value here — "ten collisions" reads as a
wall; the actual work is five tables that share one pattern.

## Amendment — 2026-08-24: an eleventh collision, created deliberately

`machine_credentials` now collides. Unlike the ten measured here, it was not
discovered — Sub's migration `551` created it, transcribed from
`dotmac_kernel.machine_models.MachineCredential`, so that Sub can adopt the
kernel's `machine_auth` facility.

The counts above are left at their measured baseline on purpose. They record
what an audit found on a date; folding a later, deliberate addition into them
would make the historical measurement unreadable and is exactly the drift the
"nine at head, ten at baseline" note below exists to prevent. The new entry is
in Group A, where it belongs, and this amendment is how the total moves.

Its disposition is STAMP for the same reason `tenants` and `tenant_domains` are:
Sub is already running the kernel's model against its own table, so there is
nothing to migrate when the lineages compose. It differs from those two only in
being newer, and in having no pre-kernel history at all — the table has never
held a row written by anything but the kernel's ORM.

## Method, and the four ways it was wrong first

Kernel columns come from imported SQLAlchemy metadata (exact). Sub columns are
parsed statically from its model files, because importing Sub's app needs a
database environment this analysis has no business touching. Collisions come
from a static parse of `op.create_table` across both lineages.

Four corrections, all worth keeping for whoever remeasures, because each one
produced a confident wrong answer:

1. **Sub squashed 155 early migrations into `alembic/versions_archive/`.** A
   first pass read only `alembic/versions/` and reported six collisions, missing
   `roles`, `audit_events`, `user_credentials` and `domain_settings` — all
   created in the archive. The tables still exist in the database. Any Sub
   schema analysis must include the archive.
2. **Counting `create_table` and ignoring drops over-reports.** That is what put
   `people`/`person_roles` in this document as collisions. A lineage is a net
   effect, not an accumulation.
3. **Scanning the body of `upgrade()` alone finds nothing.** Kernel `0001` and
   `0003` do all their work in helper functions (`_create_people_table()`,
   `_drop_old_person_shaped_tables()`). A per-function scan reported zero
   creates and zero drops, which briefly looked like confirmation.
4. **Order matters within one migration.** Applying all creates then all drops
   removed `user_credentials` and `auth_sessions`, which `0003` drops and then
   RECREATES with `party_id`. Operations have to be replayed in source order,
   inlining each helper at its call site. `0018` additionally renames via raw
   `op.execute("ALTER TABLE ... RENAME TO ...")` rather than `op.rename_table`,
   which a parser looking only for the latter misses.

**What finally made it trustworthy was a self-check, not more care.** The
measurement now asserts four things it already knows: `people`/`person_roles`
absent (dropped, never recreated), `user_credentials`/`auth_sessions` present
(dropped and recreated), and `idempotency_records` present (renamed from
`inbox_records`). Every earlier version fails at least one. A schema measurement
without such assertions is a guess with a table around it.

## Measured against the database

Read-only queries against staging Sub (seabone, container `dotmac_sub_db`,
database `dotmac_sub`, 4.4 GB, alembic head `519_fiber_cost_items`, **595 live
tables**). No test load ran there; staging was the source of the schema, not the
compute.

### Collision count: 9 at staging, 10 at dev head — and that is not a discrepancy

The database reports **nine** collisions. The parse reported ten. The single
difference is `domain_setting_history`, created by Sub's migration `520`, and
**staging is at `519`** — two revisions behind `dev`. Both numbers are right for
their baseline.

That agreement is the point. The parse-based method produced four different
answers before it was correct; being able to reconcile it exactly against a real
database is what makes the tenth answer trustworthy rather than merely the
latest.

### Three dispositions now settled by evidence, not inference

Comparing kernel model columns against `information_schema.columns` on staging:

| table | kernel | sub | shared | verdict |
|---|---:|---:|---:|---|
| `tenants` | 8 | 8 | **8** | IDENTICAL — stamp |
| `tenant_domains` | 6 | 6 | **6** | IDENTICAL — stamp |
| `domain_settings` | 13 | 13 | **13** | IDENTICAL — adopt in place |
| `communication_suppressions` | 11 | 10 | 9 | union (kernel `party_id`+`tenant_id`; sub `subscriber_id`) |
| `roles` | 6 | 6 | 4 | union (kernel `slug`+`tenant_id`; sub `description`+`is_active`) |
| `parties` | 9 | 10 | 5 | union |
| `party_roles` | 6 | 11 | 4 | ~~union~~ — **no longer collides** (ADR-0019 renamed the kernel's to `party_role_grants`) |
| `audit_events` | 8 | 15 | 4 | union |
| `user_credentials` | 6 | 16 | 4 | union |

Group A's "stamp" and Group B's "adopt in place" were previously argued from
migration 508's provenance and from model files. **They are now measured:** the
column sets are byte-identical in the live database, so those three tables carry
no schema risk at all.

`domain_setting_history` will join them once staging runs `520` — its kernel and
Sub column sets already match at 15/15.

### What this leaves

**Six unions**, of which two still need a decision before a migration:
`user_credentials` (the kernel links one `party_id`; Sub links **three** —
`subscriber_id`, `system_user_id`, `reseller_user_id` — an identity-model
question) and `audit_events` (Sub's `ip_address`/`user_agent`/`request_id`/
`status_code` versus the kernel's `details` JSON).

> **Two corrections since this was measured (2026-08-12).**
> **`party_roles` is no longer a union.** ADR-0019 renamed the kernel's RBAC
> grant to `party_role_grants`, so at lineage head there is no collision on that
> name — leaving **five** unions here, not six, and **nine** collisions at dev
> head rather than ten. The chain still passes through the old name in `0003`;
> see "Update 2026-08-12" below for why that reduces the disposition rather than
> removing it.
> **`user_credentials` has three principal kinds, not four.**
> `ck_user_credentials_exactly_one_principal` sums only `subscriber_id`,
> `system_user_id` and `reseller_user_id`. `radius_server_id` sits outside that
> constraint and is read only under `provider == radius`, to select which RADIUS
> server verifies the password — a provider qualifier, not an identity kind. Full
> analysis in `docs/superpowers/reviews/2026-08-12-user-credentials-principal-decision.md`.

### The method to use from here

Stop parsing. The remaining question — *does the kernel lineage actually apply
to this schema* — is one a database answers definitively and a parser cannot:
column types, constraint conflicts, rows that violate a new CHECK, RLS
interactions. Restore this schema into a scratch database and run the lineage
against it. The rehearsal harness in `tests/test_migration_split_rehearsals.py`
already provisions scratch databases and runs lineages as `app_admin`; pointing
it at a restored Sub schema turns the six remaining dispositions from an
analysis into failing assertions that get fixed one at a time.

## The ten, grouped by what they actually need

### Group A — already the kernel's shape: STAMP (2, plus one added 2026-08-24)

| table | why |
|---|---|
| `tenants` | Sub's migration 508 created it *from the kernel's shape*, and Sub imports `dotmac_kernel.models.Tenant` to use it |
| `tenant_domains` | same migration, same story |
| `machine_credentials` | Sub's migration `551` (2026-08-24) created it from `dotmac_kernel.machine_models.MachineCredential`, for the `machine_auth` adoption. Identical by construction: the constraints, the composite uniques, the `hmac-sha256:` CHECK and the FORCEd RLS policy are the kernel's, transcribed |

Sub is already running the kernel's model against its own table. The disposition
is to stamp the revision, not to migrate anything. **These are not really
collisions; they are adoption that already happened without the lineage.**

### Group B — byte-identical column sets: ADOPT IN PLACE (2)

| table | kernel | sub | shared |
|---|---|---|---|
| `domain_settings` | 13 | 13 | **13** |
| `domain_setting_history` | 15 | 15 | **15** |

`domain_settings` is the reference case and is already solved: kernel a40's
migration `0021` detects Sub's existing `ck_domain_settings_scope_alignment`,
verifies the constraint *and* the platform default genuinely match, adopts them,
and records `dotmac-kernel:0021:adopted-existing` so a downgrade restores Sub's
predecessor rather than deleting it.

`domain_setting_history` needs the same treatment and nothing more.

### Group C — WITHDRAWN

This group claimed `people` and `person_roles` were dead-but-present in both
repositories and proposed a kernel migration to drop them.

**The kernel already drops them.** Revision `0003`'s `upgrade()` calls
`_drop_old_person_shaped_tables()`, which removes `people`, `person_roles`,
`user_credentials` and `auth_sessions`; the last two are then recreated with
`party_id`. The docstring says so plainly — *"this is a destructive replace
(template repo), not a dual-write migration"* — and the measurement error was
mine, not the lineage's.

So there is no work here and never was. The general point the group was reaching
for still holds and is worth keeping: **a superseded model can leave its table
behind forever unless the same change removes it.** `0003` did exactly that,
which is why this group is empty rather than why it exists.

### Group D — near-match: ONE COLUMN EACH WAY (1)

`communication_suppressions` — kernel 11 columns, Sub 10, **9 shared**.

- kernel adds `tenant_id` and `party_id`
- Sub has `subscriber_id`

This is the extraction round-tripping. **The kernel gained this table by
extracting Sub's consent ledger** (kernel `0019`, released a34; Sub's is
`app/models/notification.py:479`, the qualifying source the consent dossier
names). Generalising `subscriber_id` → `party_id` and adding tenancy is exactly
what made it product-neutral — and it is also what turned Sub's own donation
into a collision on Sub's adoption path.

Disposition: Sub gains `tenant_id` (it has one operator tenant, so the backfill
is a constant) and `party_id`; `subscriber_id` either becomes a Sub-local column
or moves to a link table.

### Group E — Sub's table is richer: KERNEL ADOPTS AND EXTENDS (4, was 5)

**This is the real work, and all four share one shape.**

| table | kernel | sub | shared | kernel-only | sub-only |
|---|---|---|---|---|---|
| `roles` | 6 | 6 | 4 | `slug`, `tenant_id` | `description`, `is_active` |
| `parties` | 9 | 10 | 5 | `custom_fields`, `email`, `is_active`, `tenant_id` | `data_classification`, `merge_reason`, `merged_into_party_id`, `metadata_`, `status` |
| `audit_events` | 8 | 15 | 4 | `actor_party_id`, `created_at`, `details`, `tenant_id` | `actor_id`, `actor_label`, `actor_type`, `ip_address`, `user_agent`, `request_id`, `status_code`, `is_success`, `occurred_at`, `metadata_`, `is_active` |
| `user_credentials` | 6 | 16 | 4 | `party_id`, `tenant_id` | `username`, `provider`, `subscriber_id`, `system_user_id`, `reseller_user_id`, `radius_server_id`, and six lockout/rotation columns |

> **`party_roles` left this group on 2026-08-12.** It was
> `| party_roles | 6 | 11 | 4 | role_id, tenant_id | role_key, role_type, source, status, valid_from, valid_until, metadata_ |`.
> ADR-0019 renamed the kernel's grant to `party_role_grants`, so there is no
> union to design — the two tables were never one contract. What remains is a
> transient chain disposition, not a Group E row: `0003` still creates the old
> name before `0022` renames it. See "Update 2026-08-12" below.

The pattern in every row: **the kernel contributes `tenant_id` plus a small
number of columns; Sub contributes substantially more operational detail.** The
target is the union, and a40 already established which direction that resolves
in — *make the kernel adopt the product's stronger table, rather than levelling
the product down to the kernel.*

Two of the five are harder than a union suggests and should not be planned as
mechanical:

- **`user_credentials`** is not a superset in either direction. The kernel links
  a credential to one `party_id`; Sub links to **three** different principal
  kinds (`subscriber_id`, `system_user_id`, `reseller_user_id`), enforced as
  exactly-one by `ck_user_credentials_exactly_one_principal`. That is a genuine
  identity-model difference, not extra columns, and it needs a decision about
  whether Sub's principals become parties before this table can converge.
  (`radius_server_id` is on the table but outside that constraint — it is a
  provider qualifier, not a fourth principal. This document said four until
  2026-08-12.) ADR-0019 fixes the target — authenticate the Party, authorize the
  PartyRole — and the recommendation is dossiered in
  `docs/superpowers/reviews/2026-08-12-user-credentials-principal-decision.md`.
- **`audit_events`** carries request-forensic columns the kernel folds into a
  `details` JSON. Flattening Sub's `ip_address`/`user_agent`/`request_id`/
  `status_code` into `details` loses queryability that Sub's audit surface may
  depend on; promoting them into the kernel is the safer direction and should be
  measured against real audit queries first.

  **Measured 2026-08-12 —
  [`audit-events-disposition.md`](audit-events-disposition.md).** The query
  surface settles the forensic columns, but the slice is blocked on two policy
  questions the measurement cannot answer: Sub's audit rows are *mutable* (a
  guarded `DELETE` sets `is_active = false`, and records nothing about the
  redaction) while the kernel's are immutable by design; and the kernel's
  `actor_party_id` cannot replace Sub's polymorphic actor, because three of the
  four `AuditActorType` members are not parties. `occurred_at` and `created_at`
  also turn out not to be aliases — Sub's is caller-supplyable domain time.

## What this changes about the gate

`idempotency_records` still cannot be reached before these are dispositioned —
the lineage is a chain and every collision above sits at revisions `0001`–`0003`,
`0014` or `0019`. But the shape of the remaining work is now:

- **4 tables need no design** (Group A stamp, Group B adopt)
- **1 is a two-column reconciliation** (Group D)
- **4 need a union, of which 2 need a real decision first** (Group E) — was 5
  until ADR-0019 took `party_roles` out of the group
- **1 transient chain disposition**: `party_roles`, created in `0003` under the
  old name and renamed by `0022`. Not a union; see "Update 2026-08-12"

> **Read the next section before planning from this list.** Grouping by
> difficulty is not the order the work can land in: five of the ten collisions —
> including both tables that need a decision — are inside revision `0001`, which
> applies atomically and is the lineage root. See "Where the gate actually sits".

`idempotency_records` is confirmed present at kernel head — `0018` renames
`inbox_records` into it — and confirmed NOT to collide, since Sub's table is
`idempotency_keys`.

## Where the gate actually sits: revision `0001` is the atomic unit

**Added 2026-08-12.** The grouping above is right about the *tables* and
misleading about the *order*. It reads as though the four no-design tables could
land first and move `EXPECTED_FIRST_FAILURE` forward while the five unions are
designed. They cannot. Grouping by difficulty hid the fact that difficulty is
not how the lineage is packaged — revisions are.

**Five of the ten collisions are created inside revision `0001` alone:**

| collision | created in | group above |
|---|---|---|
| `tenants` | `0001` | A — stamp |
| `tenant_domains` | `0001` | A — stamp |
| `roles` | `0001` | E — union |
| `user_credentials` | `0001` (dropped + recreated with `party_id` in `0003`) | E — union, **needs a decision** |
| `audit_events` | `0001` | E — union, **needs a decision** |

The remaining five sit later: `domain_settings` (`0002`), `parties` and
`party_roles` (`0003`), `domain_setting_history` (`0017`), and
`communication_suppressions` (`0019`).

Alembic applies a revision atomically, and `0001` is the lineage root — nothing
can be inserted before it. So the first movement of `EXPECTED_FIRST_FAILURE`
requires all five above to be dispositioned in one change, and that set contains
**both** of the two tables this document already flagged as needing a real
decision before a union can be planned. There is no cheap first slice on this
workstream. `auth_sessions` does not collide: Sub's table is `sessions`.

### `0001` does four things beyond creating tables

`upgrade()` is `_ensure_roles()`, seven `create_table` calls, then
`_create_current_tenant_function()`, `_apply_rls()`, `_grant_roles()`. Every one
of the last four has to be adopt-aware too, and the third is the dangerous one.

**`_apply_rls()` is the real blocker, and it is a fail-silent one.** It runs, for
`people`, `user_credentials`, `auth_sessions`, `roles`, `person_roles` and
`audit_events`:

```
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY <t>_tenant_isolation ON <t>
    USING (tenant_id = app_current_tenant_id()) ...
```

Sub has three of those six tables (`roles`, `user_credentials`, `audit_events`),
all populated, and **none of them has a `tenant_id` column** — confirmed in
source: `app/models/rbac.py:20-41` and `app/models/audit.py` contain no
`tenant_id` at all. Two consequences, in order:

1. **Before the union lands**, `CREATE POLICY` fails on a missing column. That is
   the benign outcome — it fails loudly and the rehearsal pins it.
2. **After the union lands** — once `tenant_id` exists — the policy succeeds, and
   `FORCE ROW LEVEL SECURITY` then applies to the table owner as well as to
   `app_user`. Sub's application never calls `app_current_tenant_id()`, so the
   GUC is unset, the comparison yields NULL, and **every read of `roles`,
   `user_credentials` and `audit_events` returns zero rows.** Authentication and
   RBAC stop working, and nothing raises. This is exactly the fail-silent class
   recorded in the 2026-08-10 assembly RLS audit, where ERP's policy helper
   returns NULL when unset and unscoped queries return nothing.

So the union is not the end of the `0001` disposition; it is the point at which
the disposition becomes hazardous. Landing `tenant_id` without simultaneously
deciding Sub's GUC/session contract converts a loud failure into a silent one.

`_ensure_roles()` and `_grant_roles()` are smaller but not free: they create and
grant to `app_user`/`platform_api`, which requires role-creation privilege on
Sub's database and grants against roles Sub's application does not connect as.
`_create_current_tenant_function()` defines `app_current_tenant_id()` in
`public`, which Sub does not have.

### Update 2026-08-12 — `party_roles` is reduced, not removed

ADR-0019 renamed the kernel's RBAC grant to `party_role_grants` (migration
`0022`, kernel `0.1.0a41`), because Sub holds the archetype-correct meaning of
`party_roles` and the kernel had the right name on the wrong table.

**At lineage head the `party_roles` collision is gone.** But the lineage is a
chain, and revision `0003` still calls `op.create_table("party_roles")` before
`0022` renames it — so a product running the kernel chain from base still passes
through the colliding name. This is the same "a lineage is a net effect, not an
accumulation" lesson as correction 2 under "Method", arriving from the other
direction: measuring the *head* would now under-report, exactly as counting
*creates* previously over-reported.

What changes is the disposition's difficulty, not its existence. `party_roles`
moves out of Group E: it is no longer a semantic union of two different concepts
under one name, but a question of when the kernel's grant table takes its final
name. Two ways to close it, both cheaper than a union:

- have `0003` create `party_role_grants` directly and make `0022` a no-op for
  fresh databases — amends a released migration, safe for recorded installs
  (they never re-run it) but a change to shipped content that needs recording;
- make `0003`'s create of `party_roles` adopt-aware, as `0021` is for
  `domain_settings`.

**Revised count for revision `0001`:** unchanged at five, because `party_roles`
is created in `0003`, not `0001`. The `0001` gate is untouched by ADR-0019.

### What this means for sequencing

- The `0001` disposition must be planned as **one change covering five tables,
  the RLS/GUC contract, the tenant function, and the grants** — not as five
  independent table unions.
- `user_credentials` is the hardest of the five and gates the rest of `0001`. The
  kernel binds a credential to one `party_id`; Sub binds to **three** principal
  kinds (`subscriber_id`, `system_user_id`, `reseller_user_id`) under an
  exactly-one CHECK. Until it is decided whether Sub's principals become parties,
  `0001` cannot be dispositioned, and therefore **no** collision after it can be
  reached. *(This bullet said four principal kinds when first written on
  2026-08-12; `radius_server_id` is outside the CHECK and is a provider
  qualifier. Corrected the same day.)*
- That decision is the same one the Party cutover needs for
  `PARTY_PRINCIPAL_CONTEXT_BINDING`. The lineage workstream and the principal
  slice of the Party cutover are not independent tracks; they share this gate.
- The measurement here is from source reading plus the existing staging
  measurement, **not from a rehearsal run**. `test_kernel_lineage_rehearsal.py`
  needs Postgres and `TEST_DATABASE_URL`; per the standing rule it runs on the
  Git-hosted CI/Observe runners, not locally. Consequence 2 above is a read of
  `_apply_rls()` and Sub's models, and should be confirmed by that rehearsal
  before it is relied on.

## The rule this argues for

`communication_suppressions` is the case to generalise from: **extracting a
capability from a product adds a collision to that product's own adoption
path**, unless the disposition is decided at extraction time. The kernel took
Sub's consent ledger in a34 and thereby made Sub's cutover one table more
expensive, and nobody recorded that at the time.

That should be a required field in `EXTRACTION.toml` — when a dossier names a
source product, it must state how that product's table is dispositioned when it
later adopts the lineage. Otherwise every product-first extraction quietly
raises the cost of the adoption it was meant to serve, and the bill only arrives
when someone remeasures.
