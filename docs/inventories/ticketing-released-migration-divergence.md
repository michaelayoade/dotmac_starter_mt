# Ticketing released-migration divergence

**As of:** 2026-09-01
**Repository:** `dotmac_starter_mt`
**Disposition:** confirmed released history; guarded, not repaired in place
**Upgrade evidence:** OUTSTANDING — see the last section

## How it was found

Not by a gate. `dotmac-ticketing` was one of the 43 rows in
`tests/architecture/released_lineage_coverage_baseline.json` — a lineage whose
bytes are in a published wheel with nothing freezing them — and the divergence
surfaced the moment the lineage was enrolled in
`tests/architecture/test_released_migrations.py`.

That is the finding underneath the finding. The identical edit was made to
`dotmac-approvals`' `ap_0001_approvals` in the same week, and it was caught,
written up and given a PostgreSQL upgrade matrix, because approvals was
enrolled. Ticketing was not, so the same act passed every gate in the
repository for two and a half weeks. The gate did not miss the edit; it was
never pointed at the file — the `dotmac-numbering` sentence, one distribution
later.

## Finding

`tk_0001_tickets` has been published under two tags with two different byte
sets.

| Release | SHA-256 | Meaning |
|---|---|---|
| `0.1.0a3` | `4719bb00030931b9c18697ee66e6e183067728582040a90f5189e1fcef0ed7bb` | Always builds the platform plane; builds the tenant plane when a tenant catalogue is bound |
| `0.1.0a4` | `0e27d341b9b0a26577a55b2a973dcab7335b381c1dc4d5c936ed57a119993727` | Builds exactly the planes the assembly SELECTS (ADR-0028) |

The change is not cosmetic and the emitted DDL is not equivalent:

* a3 calls `all_bound(TENANT_REQUIRES)` and treats a bound tenant catalogue as
  intent to build tenant tickets. a4 calls `selected_module_planes("ticketing")`
  and treats a missing selection as a hard error. Vendor CP is the case that
  separates them — it composes kernel `0001`, so the catalogue is genuinely
  there, while only platform tickets are wanted.
* a3 builds the platform plane unconditionally. a4 builds it only when that
  plane is selected.
* a3 issues `GRANT USAGE ON SCHEMA mod_tkt TO platform_api, app_admin`. a4
  grants `app_admin` always and `platform_api` only where the platform plane is
  selected.

`alembic_version` records that revision `tk_0001_tickets` ran, never which bytes
gave it its meaning. A database that ran a3 therefore holds the platform plane
whether or not anything selected it, while a fresh a4 installation of the same
revision id may hold no platform plane at all. Nothing in either database can
tell the two apart.

The module's own `CHANGELOG.md` records the consequence for an assembly —
"**a3 was published**, so an assembly already on it must add a
`ModulePlaneSelection`" — which is a migration note for consumers. It is not a
record of released-byte divergence, and it is not enforcement.

## Enforced disposition

The tree retains the a4 digest as the canonical released bytes. The guard in
`tests/architecture/test_released_migrations.py`:

* cross-checks both digests against their Git tags, so the map cannot be
  brought into line with a further edit without moving a tag;
* requires the exact two-variant census, so a third byte set fails
  `test_two_tags_agree_unless_the_divergence_is_exactly_grandfathered`;
* requires the working tree to hold the canonical a4 bytes and nothing else;
* requires this document to exist and to quote every digest above.

This is a grandfathered fact, not a grandfathered permission. Repairs are
additive: a defect in a3's shipped DDL is fixed by a new `tk_0002` revision that
alters the result, never by editing `tk_0001` again.

## What is still owed

Approvals carries `tests/test_approvals_released_migration_upgrades.py`, which
reconstructs each tagged source from its blob, verifies the historical digest
before executing it, and proves on PostgreSQL that every historical variant
upgrades into the current lineage with its rows and selected table set intact.

Ticketing has no equivalent. Until it does, the following is guarded but not
demonstrated:

1. that an assembly which ran a3 with a bound tenant catalogue and no explicit
   selection reaches the same state as an a4 assembly selecting both planes;
2. that an a3 installation which acquired an unwanted platform plane can be
   reconciled by an additive revision rather than by re-running `tk_0001`;
3. that `platform_api`'s schema `USAGE` grant, issued unconditionally by a3, is
   revoked where a4 would not have issued it.

Item 3 is the one with a security shape: an a3 database granted `platform_api`
reach into `mod_tkt` regardless of whether the platform plane was wanted, and
nothing has yet withdrawn it. Michael owns the ruling on whether that needs a
`tk_0002` repair, a fleet audit of a3 installations, or neither because no
adopter ran a3 with an unselected platform plane.
