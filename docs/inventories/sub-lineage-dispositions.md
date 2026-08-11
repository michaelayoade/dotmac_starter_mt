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
| `party_roles` | 6 | 11 | 4 | union |
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
`user_credentials` (the kernel links one `party_id`; Sub links `subscriber_id`,
`system_user_id`, `reseller_user_id` and `radius_server_id` — an identity-model
question) and `audit_events` (Sub's `ip_address`/`user_agent`/`request_id`/
`status_code` versus the kernel's `details` JSON).

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

### Group A — already the kernel's shape: STAMP (2)

| table | why |
|---|---|
| `tenants` | Sub's migration 508 created it *from the kernel's shape*, and Sub imports `dotmac_kernel.models.Tenant` to use it |
| `tenant_domains` | same migration, same story |

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

### Group E — Sub's table is richer: KERNEL ADOPTS AND EXTENDS (5)

**This is the real work, and all five share one shape.**

| table | kernel | sub | shared | kernel-only | sub-only |
|---|---|---|---|---|---|
| `roles` | 6 | 6 | 4 | `slug`, `tenant_id` | `description`, `is_active` |
| `parties` | 9 | 10 | 5 | `custom_fields`, `email`, `is_active`, `tenant_id` | `data_classification`, `merge_reason`, `merged_into_party_id`, `metadata_`, `status` |
| `party_roles` | 6 | 11 | 4 | `role_id`, `tenant_id` | `role_key`, `role_type`, `source`, `status`, `valid_from`, `valid_until`, `metadata_` |
| `audit_events` | 8 | 15 | 4 | `actor_party_id`, `created_at`, `details`, `tenant_id` | `actor_id`, `actor_label`, `actor_type`, `ip_address`, `user_agent`, `request_id`, `status_code`, `is_success`, `occurred_at`, `metadata_`, `is_active` |
| `user_credentials` | 6 | 16 | 4 | `party_id`, `tenant_id` | `username`, `provider`, `subscriber_id`, `system_user_id`, `reseller_user_id`, `radius_server_id`, and six lockout/rotation columns |

The pattern in every row: **the kernel contributes `tenant_id` plus a small
number of columns; Sub contributes substantially more operational detail.** The
target is the union, and a40 already established which direction that resolves
in — *make the kernel adopt the product's stronger table, rather than levelling
the product down to the kernel.*

Two of the five are harder than a union suggests and should not be planned as
mechanical:

- **`user_credentials`** is not a superset in either direction. The kernel links
  a credential to one `party_id`; Sub links to four different principal kinds
  (`subscriber_id`, `system_user_id`, `reseller_user_id`, `radius_server_id`).
  That is a genuine identity-model difference, not extra columns, and it needs a
  decision about whether Sub's principals become parties before this table can
  converge.
- **`audit_events`** carries request-forensic columns the kernel folds into a
  `details` JSON. Flattening Sub's `ip_address`/`user_agent`/`request_id`/
  `status_code` into `details` loses queryability that Sub's audit surface may
  depend on; promoting them into the kernel is the safer direction and should be
  measured against real audit queries first.

## What this changes about the gate

`idempotency_records` still cannot be reached before these are dispositioned —
the lineage is a chain and every collision above sits at revisions `0001`–`0003`,
`0014` or `0019`. But the shape of the remaining work is now:

- **4 tables need no design** (Group A stamp, Group B adopt)
- **1 is a two-column reconciliation** (Group D)
- **5 need a union, of which 2 need a real decision first** (Group E)

`idempotency_records` is confirmed present at kernel head — `0018` renames
`inbox_records` into it — and confirmed NOT to collide, since Sub's table is
`idempotency_keys`.

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
