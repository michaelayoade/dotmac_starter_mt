# `dotmac-ticketing` adoption: ERP, then the vendor control plane

**Status:** execution plan; not evidence that either product has adopted the
module
**Decision:** ADR-0017, amendment of 2026-08-13 ("owner-directed exception for
`dotmac-ticketing`, and ERP goes first")
**Dossier:** `packages/dotmac-ticketing/EXTRACTION.toml`
**Order selected by Michael:** ERP cutover 1, vendor control plane cutover 2

## Exit condition

The work is complete only when both products independently pin the same exact
released `dotmac-ticketing` version, compose their own installation of its `tk`
lineage, declare their own status *reasons* rather than statuses, and own their
subject link tables through the link helper for **their** plane. ERP must first
classify every existing row by authority: only ERP-owned internal work enters
its `mod_tkt` installation; remotely owned records remain provenance-bearing
observations or rebuildable projections. ERP then retires
`app.models.support.ticket.TicketStatus` and every sync-side direct status write
as decision authorities. Until then `contract_consumers = []` remains correct.

No product may use a relative path dependency, copy the module migration, add a
second session factory, extend the status layer, keep a permanent dual writer,
or query another application's persistence. Sharing the module means sharing a
contract and implementation, never sharing rows (ADR-0024).

## Not in scope: Academy

Academy was considered and dropped. It has no ticket capability — zero source
references — so installing the module there would retire no local owner and
prove no contract, which ADR-0017 decision 1 says is not adoption. It also pins
`dotmac-kernel 0.1.0a32`, below this module's effective `0.1.0a53` floor after
ADR-0023. If Academy later wants tickets that is a product decision on its own
merits; it does not become a contract consumer of this programme.

## The ordering risk, stated once

ERP-first inverts both the dossier's original `first_cutover` and ADR-0017
decision 4. The cost is concrete and belongs at the top of the plan rather than
in a footnote:

- **E8 is a hard prerequisite.** `mod_tkt.tickets` and `mod_tkt.ticket_comments`
  are tenant-scoped with RLS. ERP has 303 tables carrying `organization_id` and
  none carrying `tenant_id`. No Organization-to-Tenant decision means no
  lineage, means no cutover.
- **ERP has zero ticket tests.** That is the same fact that disqualified it as
  an extraction source. Its parity evidence must be *built* before the cutover,
  not ported.
- **Re-ordering is available again.** It was not when this plan was first
  written: cutover 2 was blocked on a contract mismatch of its own. ADR-0023
  resolved that by splitting the module into two persistence planes, so
  promoting the vendor control plane — greenfield, with a required kernel
  upgrade from `0.1.0a46` to `0.1.0a53` — is a real fallback if E8 stalls.
  Take it rather than reporting progress that is not happening.

## Release gate

1. `dotmac-ticketing` is in `.github/release-modules.json` as of this change,
   with `db_schema = mod_tkt` and `kernel_floor = 0.1.0a53`. Before this entry
   the module was unreleasable and `first_cutover` named a cutover no product
   could begin. The floor is **not** the `a39` that allocated `mod_tkt`:
   ADR-0023 made this module dual-plane, so its manifest passes
   `platform_tables`, which `a53` added — an earlier kernel raises `TypeError`
   at import, before the allocation check runs.
2. Publish kernel `0.1.0a53` **first**, then `0.1.0a1`, through the normal
   branch/review/release workflow. Registry verification installs the module
   against its floor, so it cannot pass until that kernel exists.
3. Prove the wheel contains the `tk` migrations and is importable in a clean
   consumer environment. `alembic` is an expected runtime requirement here and
   only here — the link helpers emit DDL into a consuming product's migration.
4. Keep routing policy, category taxonomies, work-order handoff and the agent
   workqueue OUT of the module. They stay product-owned.

### The surface gap is real

`0.1.0a1` ships the lifecycle, the reason registry, four tables across two
planes and both link helpers — and **no routers, schemas or screens**. Both
adopters build their own surface. The `ticketing.use` capability and the
read/work/administer permission split must be declared in the *same change* as
the guards that reference them; the manifest deliberately declares none today
because a declared code with no consumer is dead vocabulary.

## Cutover 1 — ERP

### E0. Partition authority before writing migration code

This is the step that decides whether the cutover is possible at all, and it is
not a migration question.

ERP's table is not one ownership boundary. It mixes internal helpdesk/project
work that ERP may own with records described as "synced from ERPNext Issue or
HD Ticket DocTypes". It carries `ERPNextSyncMixin` and `ERPNEXT_STATUS_MAP`, and
there are **three independent writers** of ticket state:

| writer | file |
|---|---|
| local support service | `app/services/support/ticket.py` (`.status` written at 3 sites) |
| CRM ticket sync | `app/services/crm/sync/tickets.py` |
| CRM project sync | `app/services/sync/crm/projects.py` (`.status` written at 2 sites) |

A guarded local lifecycle and a remote status feed cannot both own one row.
ADR-0024 resolves the architecture without appointing one application owner of
every record named `ticket`:

- **ERP-owned internal work** becomes a local `TenantTicket` in ERP's own
  `mod_tkt` installation. Only ERP's ticket service may transition it.
- **Sub/CRM/ERPNext-owned work** is archived and retired from ERP's operational
  ticket schema. A named local reader may justify a separately designed,
  rebuildable observation projection later; foreign-key compatibility does
  not. The importer never writes `TenantTicket.status`.
- If remote work requires separate ERP action, the reconciler creates a new
  ERP-owned ticket and records provenance between the two identities. It does
  not turn the remote app into a writer of the ERP ticket.

The row classifier and its measured counts are E0 acceptance evidence. An
unclassified row is a stop condition, not a default to ERP ownership. The
integration provider codes and wire mappings live in independently released
Integrator connector plugins; ERP exposes only provider-neutral domain ports,
and `dotmac-ticketing` gains no ERPNext/CRM/Sub branches.

### E8. The tenancy gate

Unchanged from the `dotmac-files` plan, and mandatory here for the same reason:

- approve one Organization-to-Tenant mapping with no parallel tenant writer;
- establish one transaction authority and one request-scoping GUC contract;
- compose independent migration lineages without copying their revisions; and
- prove ERP's existing organization isolation is not weakened.

### E1–E7. The cutover

1. Add exact kernel/ticketing pins and compose the `tk` lineage. Run the
   composed migration gate before any live migration. ERP's migration declares a
   cross-lineage `depends_on`, never a `down_revision` chained to `tk`.
2. **Characterize and classify before mapping.** ERP has no ticket tests, so
   measure the live estate first: authority/source distribution, status
   distribution, every transition actually performed, and every place a status
   is read for behaviour. Prove the authority classifier total before any row
   moves. This parity baseline does not exist yet.
3. **Map local status to status + reason.** For rows proven ERP-owned, ERP's
   five statuses map onto the standard nine as below. The mapping must be total
   and reversible on those real rows. A remote status maps to an
   `observed_status` in the projection, not to this table.

   | ERP `TicketStatus` | module `Status` | note |
   |---|---|---|
   | `OPEN` | `open` | direct |
   | `REPLIED` | `waiting_on_customer` | **behaviour change — see below** |
   | `ON_HOLD` | `on_hold` | direct; ERP's blocking causes become *reasons* |
   | `RESOLVED` | `resolved` | direct |
   | `CLOSED` | `closed` | direct |

   ERP has no `new`, `pending`, `cancelled` or `merged` today. Do not backfill
   them; let them arrive with the surface that needs them.

4. **Measure the `REPLIED` divergence before cutover.** The module classes
   `waiting_on_customer` as `WAITING`, which **pauses the SLA clock**. ERP's
   `REPLIED` does not pause anything today. Migrating will change ERP's SLA
   numbers. Measure the delta in shadow, in report-only mode, and get it
   accepted — this is the same class of divergence the dossier already flags for
   Sub, and it must not be discovered in a breach report.
5. **Declare ERP's reasons, extend no statuses.** Blocking causes currently
   implied by ERP prose or category become declared `ReasonSpec` rows scoped to
   the statuses they may accompany, owned by ERP's own module code. If you
   cannot name the code that branches on a term, it is a tag, not a reason.
6. **Local subjects move to link tables.** ERP-owned tickets' references to
   Customer, Organization, Project, Employee and expense claims become
   product-owned link tables generated by `link_tenant_subject()` — ERP is on
   the tenant plane — with an explicit
   `on_delete_subject` — there is no safe default — in ERP's own migration,
   which must run after the `tk` lineage. The retired remote ticket rows create
   no link table and no cross-application FK; correlation-only expense claims
   retain their opaque Integrator reference.
7. **Ratchet the old owner down.** Add a two-directional ratchet over imports of
   `app.models.support.ticket.TicketStatus`, direct `.status` assignment and
   cross-application ticket-model access, with a sensitivity proof (ADR-0018).
   The baseline may fall only when the recorded count is lowered in the same
   change. The pre-E8 retirement slice already reduces
   `app/services/support/ticket.py` to the one local owner and removes external
   ticket adapters. Future external work arrives through an Integrator
   capability and may submit an explicit local create command; it does not
   recreate a compatibility projection. `ticket_category`, `support_team` and
   `ticket_attachment` stay
   ERP-owned — the module does not absorb them.

ERP acceptance evidence: fresh-database and upgrade composed migrations,
cross-organization/tenant RLS isolation on `mod_tkt` and every link table, a
total authority classifier with no default bucket, the total-and-reversible
local status mapping, the measured SLA delta for local `REPLIED`, a guard canary
per surface, remote-observation-cannot-write-local-lifecycle canaries for both
CRM syncs and the ERPNext feed, idempotent projection repair, and a sensitivity
proof for the retirement ratchet.

## Cutover 2 — Vendor control plane

Greenfield: no rows to migrate and no writer to retire. It currently pins
`dotmac-kernel 0.1.0a46`, so it must upgrade to the module's `0.1.0a53` floor.
Its value here is proving a separate installation of the same contract composes
cleanly on a vocabulary ERP has already exercised, which is the whole reason it
moved to second.

### V0. RESOLVED — the module now has a platform persistence plane

**Status: closed 2026-08-13 by ADR-0023.** Recorded here because the shape of
the fix is what cutover 2 is built on, and because the table below is still the
clearest statement of why a platform-only assembly cannot use tenant-scoped
tables.

The mismatch was:

| the module requires | the vendor control plane has |
|---|---|
| `tickets.tenant_id UUID NOT NULL`, forced RLS | platform catalog tables, no `tenant_id`, no RLS |
| `ticket_comments` composite `(tenant_id, ticket_id)` FK | — |
| ticket numbers unique **per tenant** | control-plane-wide identity |
| `link_subject()` always emits a tenant column + tenant RLS policy | — |
| a tenant-scoped session | `get_platform_db` at 15 sites; zero `require_tenant` |

Installing the lineage anyway, or minting a tenant row so the constraint
passes, would have been an installation rather than an adoption.

**What shipped instead.** One shared lifecycle, vocabulary and transition
engine; two persistence planes:

- `TenantTicket`/`TenantTicketComment` + `link_tenant_subject()` — unchanged:
  mandatory tenant, composite isolation, forced RLS;
- `PlatformTicket`/`PlatformTicketComment` + `link_platform_subject()` — no
  `tenant_id`, no RLS, platform-role grants with `app_user` REVOKEd, exactly the
  treatment `dotmac-release-catalog` gets under hard rule 11's platform-catalog
  case;
- platform ticket numbers unique within the control plane;
- no foreign key across the planes, refused by the kernel gate.

Nullable `tenant_id`, a sentinel tenant and a polymorphic scope column were
rejected and are now refused by the gate rather than by review. Authorization,
routing, SLA policy, reason declarations and subject relationships stay with the
consuming assembly on either plane.

**What this cost.** A kernel change — `ModuleManifest.platform_tables` and the
platform half of the live-catalog contract, released as `0.1.0a53`. Before it,
`audit_snapshot` required RLS on every table in a module schema, so a dual-plane
module could not compose at all. Ticketing's kernel floor is therefore set by a
capability rather than by the `a39` that allocated `mod_tkt` — `a53` at the time
of writing, and `a61` since ADR-0028 gave plane selection its own declaration.

**Still owed — corrected.** An earlier revision of this plan said the platform
plane had no live-catalog proof because the starter composes only the tenant
plane. That was wrong. `app/assembly.py` registers `dotmac_ticketing.module`,
`alembic.ini` carries the `tk` lineage, `make test-integration` upgrades all
heads, and `tests/test_module_schema_catalog.py` builds its registry from
`assembly.modules` — so a fresh integration run creates and audits **all four**
`mod_tkt` tables against the new contract, with
`test_the_ticketing_module_schema_holds_both_planes` asserting the four tables
and the platform facts explicitly.

So the evidence is **pending the next CI run on a real database**, not deferred
to the vendor CP cutover. It must be green before `0.1.0a1` is published.

### V1–V5. The cutover

1. Add the exact pin alongside `dotmac-release-catalog` and
   `dotmac-entitlement-allocation`, and compose the `tk` lineage into the vendor
   lineage. It is the third module in that assembly, so the composed gate is
   already the established path.
2. Build the surface here, since ERP's cutover is a migration rather than a new
   screen: routers, schemas, and the `ticketing.use` capability plus the
   read/work/administer permission split — each declared in the same change as
   the guard that references it. `core=False` marks an optional installed
   capability; the vendor control plane resolves its own platform availability
   and permissions without inventing a product tenant.
3. Declare vendor-side reasons only where code branches on them. A vendor
   support desk is likely to need very few; resist the urge to pre-declare.
4. Link subjects to vendor-side entities (vendor account, deployment, licence
   delivery, support grant) through `link_platform_subject()` — never the tenant
   helper — with `on_delete_subject` chosen per subject. A ticket about a remote
   deployment stores a contracted external reference and never queries that
   product's database.
5. Prove it live: `platform_tickets` and every platform link table carry no
   `tenant_id`, no RLS, and are REVOKEd from `app_user`; no FK crosses the
   planes; guard coverage on every new route; a fresh + upgraded composed
   migration. The starter's own integration run already audits the `mod_tkt`
   platform tables, so what this adds is the first exercise of the plane by a
   real control-plane assembly under real platform sessions — not the first
   catalog proof.

Reaching this point with ERP already cut over is what moves the dossier to
`reuse-proven`. One consumer alone moves it to `adopted`, not further.
