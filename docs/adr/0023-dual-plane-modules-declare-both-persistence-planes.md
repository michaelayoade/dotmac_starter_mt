# ADR-0023: A dual-plane module has one behaviour and two declared persistence planes

**Status:** Accepted
**Date:** 2026-08-13
**Decision owner:** Michael
**Scope:** FLEET-WIDE. Applies to every installable Dotmac module.
**Relates to:** ADR-0006 (module extraction, D1 namespaces), ADR-0017 (adoption
is the scarce resource), ADR-0022 (`dotmac-files`), hard rule 11 (tenant-scoped
tables), hard rule 12 (declaration registries, never enums).

## Amendment, 2026-08-13: `dotmac-files` now implements both planes

The original consequence below truthfully recorded `dotmac-files` as not yet
split when this ADR was accepted. It was completed later the same day:
`TenantStoredFile` and `PlatformStoredFile` now use one persistence-free
physical engine through required `TenantScope`/`PlatformScope` values, the `fi`
manifest declares both tables, and the module floor is kernel `0.1.0a53`.
ADR-0022 carries the as-built detail. The historical consequence remains to
show the implementation order; it is no longer an open action.

## Context

`dotmac-ticketing 0.1.0a1` was built tenant-only: `tickets` and
`ticket_comments` carry `tenant_id NOT NULL` with forced RLS, ticket numbers are
unique per tenant, and `link_subject()` always emits a tenant column, a
composite `(tenant_id, ticket_id)` reference and a tenant isolation policy.

Its named cutover-2 adopter, the vendor control plane, is a platform-only
assembly: `get_platform_db` at 15 call sites, **zero** tenant-session or
`require_tenant` uses, and platform catalog tables that carry no `tenant_id` at
all. A platform session cannot truthfully operate a tenant-scoped table — the
RLS predicate tests a tenant GUC it never sets, so every row is invisible, and
every insert has no legitimate value for the column.

This is not a scheduling problem to be worked around. It is a **current adoption
blocker**, and it was found the same way the fleet finds most of them: a module
was designed against the security context of the product that happened to be
audited first.

The same shape is already visible in `dotmac-files`: stored bytes are a tenant
concern for ERP and Academy, and a control-plane concern for vendor-side
artifacts and licence deliveries. Two modules meeting the test is what makes
this a standard rather than a fix.

### Why the obvious workarounds are rejected

Each of these has been proposed somewhere in the fleet, and each fails in a way
that is worse than the problem:

- **Nullable `tenant_id`.** The column stops being an isolation key: an RLS
  predicate on a nullable column either denies the platform rows to everyone or
  requires a second, wider policy, and the kernel already has one documented
  exception of this shape (`domain_settings`) whose cost is a split read/write
  policy pair nobody enjoys maintaining. Worse, ADR-0017's own amendment records
  a kernel defect where exactly this nullability let a row persist that the
  resolver could not reach.
- **A sentinel or "fake" tenant.** Every query and every report then has to know
  which tenant id means "not a tenant". That knowledge is unwritten, spreads by
  copy-paste, and is wrong the first time someone forgets it.
- **A polymorphic scope column** (`scope_kind` + nullable `scope_id`). This is
  the shape the linking module's own docstring already rejects for subjects: a
  UUID PostgreSQL does not know means anything, so referential integrity is
  gone, and the isolation predicate becomes a conditional on data rather than a
  structural property.
- **A second module.** Two modules would duplicate the lifecycle, the status
  vocabulary, the transition guards and the reason registry — the entire reason
  the module exists — to avoid duplicating four `CREATE TABLE` statements.

## Decision

### 1. One behaviour, two declared persistence planes

A capability that genuinely operates in both security contexts ships **one**
lifecycle, status vocabulary, transition engine and behaviour suite, and **two**
storage planes:

| | tenant plane | platform plane |
|---|---|---|
| `tenant_id` | `NOT NULL` | **absent** |
| isolation | RLS ENABLEd **and** FORCEd, tenant policy | no RLS; schema `USAGE` + row DML granted to the online platform role; **REVOKE ALL** from the tenant app role |
| uniqueness | composite, includes `tenant_id` | control-plane-wide |
| link helper | tenant-scoped, composite FK, RLS | no tenant column, single-column FK, revoke |

The shared engine must not import persistence. If it does, the "one behaviour"
claim is false and a product cannot reuse the guards on the other plane.

### 2. The plane is DECLARED, never inferred

A module declares `ModuleManifest.platform_tables` alongside `tables`. The
live-catalog gate holds each set to its own contract.

Inferring the plane from the absence of a `tenant_id` was considered and
rejected, and this is the load-bearing half of the decision: a tenant table that
merely **forgot** its column would reclassify itself as platform and lose its
isolation silently. Declaration makes adding a platform table a reviewed diff —
the same reasoning hard rule 12 and ADR-0008 apply to every other vocabulary.

A table may appear in exactly one plane. Both is rejected at manifest
construction and again in the registry, because the two contracts are opposite
and whichever check ran second would decide.

### 3. On the platform plane, the REVOKE is the isolation

It is therefore checked as strictly as an RLS policy is on the tenant side. An
un-revoked platform table is exactly as exposed as an unpolicied tenant table
and reads just as safe.

Without this, "declare it platform" would be a supported way to switch isolation
off — a guard exemption whose premise is unenforceable, which ADR-0018 forbids.

The inverse is enforced too: the online platform role must have `USAGE` on the
module schema and at least one of `SELECT`, `INSERT`, `UPDATE` or `DELETE` on
each platform table. A table grant without schema `USAGE` is ineffective, and
holding only `REFERENCES`, `TRIGGER` or `TRUNCATE` does not make an ordinary
row-access path usable. Declared-and-unreachable is a contract violation, not a
secure deployment.

### 4. The planes share a lifecycle, never a row

**No foreign key may cross them**, in either direction. An FK is the one
crossing the database itself would enforce and therefore permit: a tenant-scoped
delete cascading into control-plane data, or a platform row whose visibility
depends on a tenant predicate it has no column to satisfy.

**What is actually enforced, stated precisely.** The kernel gate refuses a
crossing FK whose SOURCE table is inside the module schema — the crossings the
module itself could author. It reads `pg_constraint` for the audited schema, so
a **product-owned link table** in `public` or a product schema referencing the
wrong plane's ticket is **unmonitored by the gate, not exempt from the rule**
(ADR-0018's distinction, applied to this ADR's own claim). The mitigation is
that a dual-plane module must ship ONE LINK HELPER PER PLANE, so the correct FK
is generated rather than hand-typed, and each helper refuses a configuration
that would produce an unusable table.

Making the external case monitored needs an inbound-FK sweep over every schema
into the module's. That is tracked follow-up work, not something this ADR claims
is done.

A ticket about a remote deployment stores a **contracted external reference** —
an identifier plus its contract — and never queries that product's database.
This is the existing app-independence standard, restated where it bites.

### 5. Naming: the bare name is the tenant plane

`tickets` is tenant-scoped; `platform_tickets` is the prefixed exception.
Multi-tenancy is this fleet's default, so prefixing both would imply a third,
unprefixed thing exists.

Python classes are explicit on **both** sides (`TenantTicket`,
`PlatformTicket`). A bare `Ticket` in a product's imports is exactly the
ambiguity this split removes, and the table name is not what a developer reads
when writing a query — the class is.

Link helpers are two functions, not one with a `platform=` flag. A flag has a
default, and whichever value the default takes is the plane a caller gets by
**forgetting to think** — on one side a missing RLS policy, on the other a
control-plane table the product data plane can read.

### 6. When this applies — and when it does not

Two planes are justified only when the same capability genuinely operates in
both security contexts. The test is whether a real, named assembly on each side
needs it *today*, not whether one is imaginable.

- **Meets the test today:** `dotmac-ticketing` (ERP/Sub tenant support desks;
  the vendor control plane's own desk), `dotmac-files` (tenant attachments;
  vendor-side artifacts and licence deliveries).
- **Does not:** `dotmac-release-catalog` and `dotmac-entitlement-allocation` are
  platform-only and correctly have no tenant plane. Most modules are tenant-only
  and must stay that way — a speculative second plane is the ADR-0006 § 5
  speculative extraction wearing different clothes.

A module declaring no `platform_tables` is audited exactly as before this ADR.

## Consequences

- **A kernel change was required, and it is demand-pulled.** Before this,
  `audit_snapshot` required RLS on every table in a module schema, so a
  dual-plane module could not compose at all. Under ADR-0017 decision 2 this
  qualifies as the narrow exception — a live adoption is blocked on it today —
  and it is an improvement to already-adopted surface rather than a new
  gap-list facility.
- **A latent hole is now closed.** `dotmac-release-catalog` has platform tables
  in `mod_rel` and was never caught by this gate, only because it is not
  composed into the starter's own assembly. The gate had no platform concept at
  all; it now has one, declared.
- **`dotmac-ticketing 0.1.0a1` was amended before release rather than after.**
  It has no consumers (`contract_consumers = []`), so the rename from
  `Ticket`/`TicketComment` to the explicit plane classes costs nothing now and
  would have been a breaking change in a month.
- **`dotmac-files` should be reviewed against this standard before its ERP
  cutover.** It is named above as meeting the test; it has not yet been split.
  That is tracked work, not a claim about its current state.
- **The live-catalog proof is pending CI, not deferred to a future adopter.**
  An earlier draft of this ADR said the starter composes only the tenant plane
  and that platform-plane evidence would arrive with the vendor control plane.
  That was wrong: `app/assembly.py` registers `dotmac_ticketing.module`,
  `alembic.ini` carries its `tk` lineage, `make test-integration` upgrades all
  heads, and `tests/test_module_schema_catalog.py` builds its registry from
  `assembly.modules` — so a fresh integration run creates and audits **all four**
  `mod_tkt` tables, both planes, against the new contract. The evidence is owed
  from the next CI run on a real database; `test_the_ticketing_module_schema_holds_both_planes`
  asserts the four tables explicitly so the audit cannot pass vacuously.

## Alternatives rejected

**Keep ticketing tenant-only and let the vendor CP build its own.** This is the
duplication the module was extracted to remove, and it would put a second ticket
lifecycle in the fleet while the first is still unadopted — precisely the
"extraction produces a third implementation" failure ADR-0006's extraction rule
and ADR-0017 both name.

**Make the vendor control plane tenant-scoped.** Inventing a tenant row for a
vendor is the sentinel-tenant dodge above, and it would contradict ADR-0021's
finding that the control plane is a distinct plane.

**Defer until a second module needs it.** `dotmac-files` is already the second,
and deferring means ticketing ships a shape its own next adopter cannot use.
