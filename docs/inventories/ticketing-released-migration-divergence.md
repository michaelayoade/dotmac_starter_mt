# Ticketing released-migration divergence — P1 migration-lineage incident

**As of:** 2026-09-01
**Repository:** `dotmac_starter_mt`
**Severity:** P1 migration-lineage incident (Michael, 2026-09-01)
**Disposition:** released bytes frozen; **a3 and a4 both REFUSED for new production adoption**
**Known installation set:** EMPTY — zero declarations, zero non-pipeline fetches; steps 3-5 of containment are `not_applicable`, not `passed`
**Repair:** OUTSTANDING — additive `tk_0002`, specified below, not written here

## Finding

`tk_0001_tickets` was published under two tags with two different byte sets.

| Release | SHA-256 | Behaviour |
|---|---|---|
| `0.1.0a3` | `4719bb00030931b9c18697ee66e6e183067728582040a90f5189e1fcef0ed7bb` | Builds the platform plane UNCONDITIONALLY; infers the tenant plane from a bound catalogue |
| `0.1.0a4` | `0e27d341b9b0a26577a55b2a973dcab7335b381c1dc4d5c936ed57a119993727` | Builds exactly the planes the assembly SELECTS (ADR-0028) |

`alembic_version` records that revision `tk_0001_tickets` ran, never which bytes
gave it its meaning. Two databases at the same revision id can hold different
schemas, and neither can tell you which.

## Exposure window

a3 was published **2026-08-14 14:16 UTC**; a4 **2026-08-15 03:45 UTC**. The
window is roughly **13.5 hours**, not the two and a half weeks an earlier draft
of this document asserted. Both wheels remain installable by explicit pin, so
the window bounds unattended adoption, not availability.

## The divergence is conditional EXECUTION, not a grant list

There is exactly ONE textual grant difference between the two files; everything
else in the DDL is byte-identical. The substantive change is which code runs:

* a3 calls `_upgrade_platform_plane()` unconditionally. a4 guards it with
  `ModulePlane.PLATFORM in selected_module_planes("ticketing")`.
* a3 issues `GRANT USAGE ON SCHEMA mod_tkt TO platform_api, app_admin`. a4
  grants `app_admin` always, and `platform_api` only where the platform plane
  was selected.

So on a **tenant-only** assembly, a3 additionally leaves
`mod_tkt.platform_tickets` and `mod_tkt.platform_ticket_comments` EXISTING,
with `platform_api` holding SELECT/INSERT/UPDATE/DELETE on both and schema
`USAGE` to reach them. That is the exposure: reachable online tables the
composition never asked for.

## a4 is not clean either

With `TENANT`-only selection, a4's `_upgrade_tenant_plane()` still issues

```
GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.tickets         TO platform_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tkt.ticket_comments TO platform_api;
```

while withholding schema `USAGE` from `platform_api`. The privilege is inert
today and becomes live the instant a platform plane is added to that database
and USAGE is granted. `tk_0002` must repair a4 databases too, not only a3 ones.

## Two roles, recorded and tested separately

`app_admin` receives schema `USAGE` unconditionally in BOTH releases and is the
schema owner. That is correct and uninteresting.

`platform_api` is the ONLINE role, and it is where the exposure lives. Any
observation, repair or test must evaluate the two separately: an assertion that
"a role can reach the schema" that is satisfied by `app_admin` proves nothing
about `platform_api`.

## Blast radius — measured, not assumed

### Declaration: zero products, one reference assembly

**No PRODUCT composes `dotmac-ticketing`.** ERP, Sub and the vendor control
plane each list it as a candidate, and none composes it. `contract_consumers`
is empty; the only lockfile hit is this repository's own path dependency on the
package it builds. There is no persistent database in the fleet holding
`mod_tkt`.

**The starter's own reference assembly is the exception, and it matters.**
`app/assembly.py:167-170` declares

```python
ModulePlaneSelection(
    module="ticketing",
    planes=(ModulePlane.TENANT, ModulePlane.PLATFORM),
)
```

and `docs/MODULE_CATALOG.md:134` records `platform+tenant` as installed here.
So the a4 tenant+platform path is **exercised, not merely declared** — the
starter's ephemeral CI Postgres containers are the only place `mod_tkt` has
ever existed with the platform plane selected.

That is consistent with the registry forensics below and with the empty known
installation set: an ephemeral CI database is not an installation. But it means
retirement is a change **in this repository**, not only a deletion elsewhere,
and it makes the reference assembly the natural first proving ground for
`tk_0002`'s converge-and-refuse behaviour — it is the one composition that
actually builds both planes.

### Distribution: zero non-pipeline fetches

A read-only Forgejo audit of `registry.dotmac.io` (authorized 2026-09-01)
counted every fetch of every ticketing artifact:

| artifact | downloads |
|---|---|
| `dotmac_ticketing-0.1.0a3-py3-none-any.whl` | 1 |
| `dotmac_ticketing-0.1.0a3.tar.gz` | 0 |
| `dotmac_ticketing-0.1.0a4-py3-none-any.whl` | 1 |
| `dotmac_ticketing-0.1.0a4.tar.gz` | 0 |

Both wheel fetches are the release pipeline verifying its own upload, ~26
seconds after it, from a GitHub Actions runner: a3 uploaded 14:18:30Z and
fetched 14:18:56Z; a4 uploaded 03:50:52Z, fetched 03:51:17Z. The a3 fetch then
404s through `alembic`, `sqlalchemy`, `fastapi` and `pydantic` on the private
index — the unmistakable shape of a pip resolve inside a verify step.

Neither sdist was fetched at all.

The admissible conclusion, in full:

> No repository declared either release, and no distribution-path consumer
> outside the publishing pipeline fetched either release. The MinIO backend
> remains an explicitly unaccounted non-distribution path.

That establishes an **empty known installation set** — nothing stronger. It is
a measurement rather than a failure to find something, and it is deliberately
not written as "never downloaded", which is false.

### Why the measurement is trusted

Log retention is proven continuous and unrotated across the whole interval
(json-file driver with empty log options, no rotation configured, the container
never recreated, and the registry is *younger* than the package, so no pre-log
history can exist). `package_cleanup_rule` holds zero rows. The grep covers the
whole file with its denominator reported. All four filenames were enumerated
from `package_file ⋈ package_blob` rather than guessed. Authenticated and
anonymous paths are both proven to log (1,093 × 401 and 79 × 403 appear). There
is no CDN and no cache handler in Caddy's chain.

The positive control is the load-bearing part. The identical query shape finds
6,873 `dotmac-kernel` downloads, 357 for `dotmac-deployment-control` a6, and
**14 `.tar.gz` downloads across other packages** — so the query is not
wheel-blind, which is the specific way "sdist: 0" could have been a lie. The
Forgejo `download_count` column and the router log cross-validate exactly on
every version checked, ticketing included.

### One declared blind spot

MinIO holds the package blobs, is published on `0.0.0.0:9000`, and has no
request logging. A holder of the MinIO service credential could read blob
objects invisibly, and that path is **unaccounted for** — the set is not closed.
Mitigations verified: anonymous `GET` to both the bucket and the exact a3 wheel
blob return 403; the object key is the sha256, obtainable only from Forgejo or
the database; and no package manager installs through that path. It is a
storage-backend path, not a distribution path.

### Containment steps, with honest statuses

| # | Step | Status |
|---|---|---|
| 1 | Declaration census across every repository | `passed` — zero product declarations; the starter's own assembly composes `platform+tenant` |
| 2 | Registry download census | `passed` — zero non-pipeline fetches |
| 3 | Production authorization review | `not_applicable` |
| 4 | Per-database observation | `not_applicable` |
| 5 | Containment window reconstruction | `not_applicable` |

Steps 3–5 are `not_applicable`, **not `passed` and not "absence proven"**. A
step with no subject is not a step that succeeded. Recording them as passed
would let a future reader believe an audit ran over installations, when what is
actually true is that there were none to audit.

The empty installation set bounds WHO was affected. It does not excuse the
repair: `tk_0002` must still converge both historical catalog shapes before a
new release, and a3 and a4 both remain refused for new production adoption.

### The other bound

Across all **46** releasable-but-unmonitored distributions, `tk_0001_tickets.py`
is the **only** released migration with divergent bytes. Four of those 46 are in
real production databases, and all four are byte-stable.

## Enforced disposition

The tree retains the a4 digest as the canonical released bytes.
`tests/architecture/test_released_migrations.py`:

* cross-checks both digests against their Git tags, so the map cannot be
  brought into line with a further edit without moving a tag;
* requires the exact two-variant census, so a third byte set fails
  `test_two_tags_agree_unless_the_divergence_is_exactly_grandfathered`;
* requires the working tree to hold the canonical a4 bytes and nothing else;
* requires this document to exist and to quote every digest above.

This is a grandfathered FACT, not a grandfathered permission. `tk_0001` is
never edited again; repairs are additive.

## Adoption refusal

**a3 and a4 are both refused for new production adoption** until a new release
supplies the additive reconciliation migration below AND that release is
independently verified. The refusal covers a4 because of the stray tenant-plane
`platform_api` DML grants above, not only a3.

The empty known installation set does not lift the refusal and does not excuse
the repair. `tk_0002` still has to converge BOTH historical catalog shapes — an
empty set bounds who was affected, not what the released bytes do to a database
that runs them tomorrow.

## The platform plane is being RETIRED, not adopted

Michael, 2026-09-01: *"Three releases with no real consumer is not an adoption
queue; it is speculative surface area. Do not invent a consumer to preserve
it."* And, once the census above landed: *"Ticketing's platform plane is now
confirmed speculative: three releases, zero declarations and zero non-pipeline
distribution fetches. Retire it. The next release should carry the additive
catalog reconciliation and tenant-only supported composition."*

**The supported composition going forward is TENANT-ONLY.** The platform plane
is retired rather than preserved, and the next release carries the additive
catalog reconciliation.

The obligation therefore points at retirement, in this order:

1. Change this repository's own composed selection to tenant-only —
   `app/assembly.py:167-170`, with `docs/MODULE_CATALOG.md:134` moving with it.
   **Not done in this change:** Michael sequenced the retirement behind
   `tk_0002`, and the currently composed `platform+tenant` selection is exactly
   the state `tk_0002` must converge FROM. Whoever writes it should know the
   starter is an affected installation of the a4 tenant+platform shape.
2. Revoke online `platform_api` reach wherever the platform plane was not
   selected — schema `USAGE` and per-table DML, evaluated separately.
3. Preserve or export any unexpected data before removing anything.
4. Remove the platform plane from supported compositions, leaving tenant-only
   as the supported shape.
5. Add an additive migration converging legacy catalogs.
6. Prove tenant-plane behaviour unchanged.
7. Retire the platform-plane declarations, tests and grants **only after** the
   migration path above is proven.

## `tk_0002` must be catalog-state-driven, not version-string-driven

Not written in this change. Its required shape:

* The explicitly SELECTED plane is the DESIRED state.
* Actual tables, ownership and effective privileges are OBSERVATIONS, read from
  the catalog — never inferred from a version string, a tag, or
  `alembic_version`, none of which distinguish a3 from a4.
* Known a3 and a4 states CONVERGE.
* Unknown mixed states **REFUSE**. A migration that guesses at an unrecognised
  state is how one divergence becomes two.
* Online `platform_api` access and offline migration-role access are evaluated
  SEPARATELY.
* Destructive removal requires a prior zero-data or preservation proof.

## The upgrade matrix comes after `tk_0002`, and a straight port would not work

Approvals carries `tests/test_approvals_released_migration_upgrades.py`, which
reconstructs each tagged source from its blob, verifies the historical digest
before executing it, and proves on PostgreSQL that every historical variant
upgrades into the current lineage.

Ticketing has no equivalent, and the matrix is sequenced AFTER `tk_0002` exists,
because the matrix's job is to prove it.

A straight port would not do that job. **The approvals matrix asserts tables and
rows and never asserts a grant** — there is no `has_schema_privilege` or
`has_table_privilege` call anywhere in it. Ported unchanged onto ticketing it
would go GREEN on a database still carrying the stray `platform_api`
privileges, which are the entire security content of this incident. The
ticketing matrix must assert effective privilege for `platform_api` and
`app_admin` separately, or it is not evidence.
