# ADR-0017: Adoption is the scarce resource, and Sub goes first

**Status:** Accepted
**Date:** 2026-08-11
**Decision owner:** Michael
**Amends:** ADR-0003's *sequencing*, not its decisions. The composition model
(`product = pinned kernel + product assembly + domain modules`) is unchanged.
**Amends:** the 2026-07-18 adoption plan's treatment of E8 and S7 as one
parallel workstream.

## Amendment, 2026-08-13: owner-directed exception for `dotmac-ticketing`, and ERP goes first

Michael directed the `dotmac-ticketing` optional module into adoption, with two
adopters in a fixed order: **ERP is cutover 1, the vendor control plane is
cutover 2.** This lifts decision 2's moratorium for that module only, on the
same terms as the `dotmac-files` amendment below.

### Amendment, 2026-08-13: adopters share the module, not ticket authority

Michael's fleet-wide composition rule is now explicit in ADR-0024: applications
are independent and integrate only by synchronizing data through versioned
APIs/webhooks. `dotmac-ticketing` is therefore installed separately in each
adopter. They share a package contract, not a database or ticket rows.

The ERP cutover applies only to internal back-office/project/employee-support
work for which ERP is the local decision owner. ERP's current table also carries
ERPNext/CRM-synchronized records; those must first be classified and remain
typed observations or rebuildable projections while their source application
owns them. A sync adapter may not assign the ERP installation's authoritative
ticket status. If a remote request creates ERP work, ERP creates a separate
local ticket with explicit provenance and owns that lifecycle from then on.

Sub remains authoritative for operational customer, subscriber and service
tickets. The vendor control plane owns only its vendor-support tickets. A change
to those boundaries needs its own accepted authority-migration decision; a
configuration flag or provider mapping cannot change the owner.

This narrows “ERP retires its local ticket owner”: it retires the legacy
`TicketStatus` decision path for classified ERP-owned tickets and removes every
sync-side direct status write. It does not import remote authority into
`mod_tkt`. The effective kernel floor is `0.1.0a53` after ADR-0023, so both
adopters must pin that floor or later even where earlier text records `a39`.

### Academy is explicitly NOT an adopter

Academy was considered and dropped. It has **no ticket capability of any kind** —
zero source references — so composing the module there would not retire a local
owner and would not exercise a contract against an existing implementation. It
would be a new product capability wearing the word "adoption", which is exactly
the substitution decision 1 exists to prevent: adoption is measured by contracts
consumed *in place of* a product's own writer, not by installations counted.
Academy also pins `dotmac-kernel 0.1.0a32`, below the `0.1.0a39` allocation
floor in effect when this section was written; ADR-0023 later raised the
effective floor to `0.1.0a53`, as the amendment above records.

Building tickets in Academy may still be a good product decision later. It is
not evidence for this module, and it must not be recorded as a contract consumer
if it happens.

### ERP first inverts this ADR's own ordering, knowingly

Decision 4 argues Sub goes first and rejects "ERP first" for the pilot, and
`packages/dotmac-ticketing/EXTRACTION.toml` independently selected the vendor
control plane as `first_cutover` because it is greenfield on tickets — no rows to
migrate, no writer to retire, so the module lineage reaches production with no
cutover risk. Michael's direction overrides both, and the dossier has been
rewritten to match rather than left contradicting the decision.

What that choice buys and costs is recorded here so it is not rediscovered:

- **Buys.** ERP is where the duplication actually hurts: 125 files reference a
  ticket today, across a support module with its own status enum, categories,
  teams, comments and attachments. Proving the contract against the hardest real
  implementation first means the vendor control plane's greenfield surface is
  built on a vocabulary that has already survived contact with a live product,
  instead of one that ERP later has to bend.
- **Costs.** The programme now inherits ERP's E8 gate. `mod_tkt`'s tables are
  tenant-scoped with RLS, so running the lineage in ERP requires the
  Organization-to-Tenant decision, one transaction authority, and a composed
  migration gate — the same prerequisites the `dotmac-files` amendment made hard
  for ERP. **E8 is a hard prerequisite, not a parallel track.** If E8 does not
  move, ERP does not move — and the honest response is to say so and re-order to
  the vendor control plane, which ADR-0023 unblocked (see below), rather than to
  wait quietly.
- **Risk accepted.** A contract defect is discovered on ERP's support estate
  rather than on an empty table. Decision 4's "discover the contract defects on
  the simpler shape first" is being traded away deliberately.

### ERP-first is also forced, not only chosen

Measured while writing this amendment, and it removes the fallback: **the vendor
control plane cannot adopt `0.1.0a1` as shipped.** `mod_tkt.tickets` and
`mod_tkt.ticket_comments` require `tenant_id NOT NULL` with forced RLS, and
`link_subject()` always emits a tenant column, a composite `(tenant_id,
ticket_id)` FK and a tenant RLS policy. The vendor control plane is a
platform-only assembly — `get_platform_db` at 15 sites, zero tenant-session or
`require_tenant` uses, platform catalog tables carrying no `tenant_id`. Its
session cannot truthfully operate a tenant-scoped table.

So the greenfield-first argument this amendment overrode was already unavailable
on other grounds. Installing the lineage there, or inventing a tenant row to
satisfy the constraint, would be an installation rather than an adoption.

**Resolved the same day.** Michael directed the module to grow an explicit
platform persistence plane rather than defer, and ADR-0023 makes "one behaviour,
explicit tenant/platform persistence planes" the fleet-wide standard for modules
that genuinely operate in both security contexts. `0.1.0a1` was amended in place
before release — it had no consumers, so the rename cost nothing then and would
have been a breaking change for two products later.

Cutover 2 is therefore unblocked at the contract level, which also restores the
re-ordering option this section said did not exist: if E8 stalls, promoting the
vendor control plane is now real rather than a dead end.

Nullable `tenant_id`, a sentinel tenant, and a polymorphic scope column remain
rejected — and are now refused by the kernel's live-catalog gate rather than by
review.

### What this amendment does not do

It does not declare the lineage-adoption exit gate met, does not lift the
moratorium for any other gap-list facility, and does not turn either candidate
into a contract consumer. The dossier stays `audit-complete` until ERP retires
its local ticket owner. Routing policy, category taxonomies, work-order handoff
and the agent workqueue stay product-owned and are not absorbed by the module.

`dotmac-ticketing` was also unreleasable when this was directed — absent from
`.github/release-modules.json`, so no product could pin it and `first_cutover`
named a cutover nobody could begin. That entry lands with this amendment.



## Amendment, 2026-08-13: Vendor pulls a product-manifest publication seam

Michael directed the build-once product-manifest boundary to proceed after the
Vendor Control Plane's Entitlement Allocation adoption reached a concrete stop:
the control plane cannot prove that its configured capability lists came from
the named target product release. This satisfies decision 2's narrow test — a
real consumer is blocked today — rather than claiming that a future product may
eventually need the facility.

The exception is limited to a pure, import-safe contract derived from facilities
the kernel already owns: `ProductAssemblySpec` and
`CapabilityCatalogue.from_manifests`. It canonically records one product code,
one product release version and that assembly's manifest-declared capability
codes, with a content digest. It adds no persistence, product registry, release
selection, network client, entitlement decision or list of Dotmac products.
Concrete product declarations remain in each product assembly; release artifact
identity and attestation association remain in `dotmac-release-catalog`.

The product-first audit is `docs/inventories/product-manifest-sources.md`. Sub's
`app/composition.py` is the qualifying source: it already declares the stable
product name and builds the capability catalogue from four manifests, with
determinism and ownership tests. ERP has only a compatibility probe and CRM has
no kernel assembly, so neither is allowed to supply guessed product data.

This amendment does not lift the broader moratorium. The Vendor allocation
writer remains authoritative until Sub publishes the snapshot, Vendor consumes
and verifies it, the historical preflight passes, and the legacy allocation
path is retired in one writer cutover.

## Amendment, 2026-08-12: `webhooks` names the facility family

Decision 2's gap-list entry `webhooks` covers reusable **inbound and outbound**
webhook facilities. The Consequences section's phrase `outbound webhooks` was
an incomplete example, not a narrowing of the list. An inbound receiver for
payment-provider events therefore remains under the same moratorium unless a
live adopter is blocked on it today.

This clarification governs shared facilities. It does not prohibit a product
from maintaining its existing product-owned adapter while adoption is blocked,
and it does not turn a proposed future consumer into the demand-pulled
exception.

## Amendment, 2026-08-11: the first adopter found a kernel invariant defect

Sub's S7 PostgreSQL gate supplied the demand-pulled exception contemplated by
decision 2. Kernel a27-a36 made `tenant_id` nullable and derived `scope_kind`
from it in Python, but set the database default to `tenant` and enforced no
alignment CHECK. A raw write omitting both columns could persist tenant/NULL: a
row present in the table and unreachable by the resolver.

Sub migration 514 already carried the stronger contract: platform server
default plus `ck_domain_settings_scope_alignment`. Michael approved preserving
that invariant rather than weakening the adopter to resemble a27.

Kernel 0.1.0a40 is therefore adoption work, not a new supply-pushed facility.
Migration `0021_setting_scope_alignment` repairs the exact legacy default
shape, refuses ambiguous rows, installs the CHECK, and can verify/adopt Sub's
existing constraint. The accepted a27 burn-down remains historical evidence;
the executable adoption baseline must be remeasured against a40 before the
kernel lineage is run in Sub.

## Context

ADR-0003 made this repository the strategic foundation for new deployments and
the convergence target for existing products. Roughly a month of kernel work
has followed. This ADR records what that month measured.

### The kernel's capability has outrun its adoption

| | |
|---|---|
| Kernel released | `0.1.0a33` |
| ERP pins | `0.1.0a24`, and imports exactly one module: `money` |
| Sub pins | `0.1.0a27`, and imports ~6 — none of them persistence |
| Vendor CP | the only real consumer of anything stateful |

`docs/inventories/` recorded this once already: *"the binding constraint is
adoption, not scope."* Idempotency then demonstrated it end to end. ADR-0014's
facility was designed from product evidence, released as `0.1.0a33`, and was
`defer-db` in **both** products on the day it shipped. Outside the starter's own
assembly it has never held a row.

That is not a criticism of the facility. It is a measurement of the gap, and the
gap is the thing this ADR is about.

### The blockage is singular

Every kernel persistence facility — `idempotency`, `audit`, `messaging` storage,
`models`, `db` — is classified `defer-db` in both products' adoption ledgers,
behind **the same single gate**: the tenancy boundary. ERP's is the E8
Organization→Tenant decision; Sub's is the S7 operator-tenant decision.

Not a dozen independent problems. One, worth an entire category. Nothing else in
the gap list (numbering, webhooks, scheduling, object storage, import/export)
has comparable leverage; each is worth one facility.

### The two products are not at the same stage

`docs/inventories/tenancy-characterization.md` (2026-08-10) measured both.

**Sub** provisions one operator tenant, and ADR-0009 in that repository is not
merely written but wired: `app/main.py` provisions at boot, and
`domain_settings` already stamps `tenant_id` from it. Its gate is a
ratification.

**ERP** has 398 tables across 37 schemas, 303 carrying `organization_id`, none
carrying `tenant_id`. Its isolation is already two independent layers — a
SQLAlchemy ORM listener and PostgreSQL RLS — and the first live-catalog
measurement (ERP #255, #256) put RLS coverage at **85 of 309 scoped tables, 27.5
per cent**, with 158 unprotected and 66 enabled-but-not-`FORCE`d. It also has an
`app.bypass_rls` escape the kernel has no equivalent of — transaction-scoped
(`SET LOCAL`, context-managed, 22 call sites across 6 files), so narrower than
its name suggests, but an escape expressed in POLICY rather than in ROLE.

Sub is weeks of ratification and cleanup. ERP is a staged migration program.
Treating them as one workstream has been hiding how close Sub is, and
understating what ERP costs.

## Decision

### 1. Adoption is the metric

The kernel's progress is measured by **contracts consumed in a product**, not
contracts shipped. A released facility with no product consumer counts as work
in progress, not as delivered.

This is a restatement of the lesson already recorded against a15–a21 — built
with no product attached — promoted here from a retrospective observation to the
standing measure.

### 2. A moratorium on new kernel facilities

No new facility from the gap list is started until **the kernel's migration
lineage runs in a product database in production**.

One exception, deliberately narrow: a facility a live adoption *asks for*.
Demand-pulled, not supply-pushed. "A product will need this" is not demand; "a
product is blocked on this today" is.

This does not stop bug fixes, security fixes, or improvements to already-adopted
surface.

#### Why the exit is the LINEAGE and not "consumes persistence"

An earlier wording said "at least one product consumes kernel persistence in
production", and it was ambiguous in a way that would have decided itself.
`dotmac_sub` imports `dotmac_kernel.models.Tenant` today and runs it against a
`tenants` table — so by one reading the gate was already met on the day it was
written.

It is not met, and the distinction is the whole point. Sub's own migration
`508` created that table. A kernel MODEL on a product-owned table is code
sharing: the product still owns the schema, the ordering, and the upgrade. The
kernel owning the LINEAGE is where those transfer, and it is what makes a
facility shared rather than its source copied — an idempotency facility whose
table each product hand-creates is a library, not a kernel. That is precisely
the failure ADR-0014 recorded.

#### The gate, measured

Per decision 5, applied to this ADR's own gate rather than only to the work it
sequences. Counted 2026-08-11 against kernel `0.1.0a27` and `dotmac_sub`
`origin/dev`:

| | |
|---|---|
| Kernel lineage | 18 revisions, 19 tables |
| Sub tables | 574 |
| **Name collisions** | **6** — `parties`, `party_roles`, `roles`, `user_credentials`, `audit_events`, `domain_settings` |
| **Already created by Sub's own lineage** | **2** — `tenants`, `tenant_domains` (migration `508`) |
| Genuinely new to Sub | 11 |

So the gate is 8 tables needing a decision and 11 needing only a run — not 19
unknowns, and not the "224-table remediation" shape that ERP faces. That is the
cost figure decision 4 rests on, and it is now countable rather than asserted.

The moratorium burns this down; it does not wait on a binary. Each collision
resolved or each table brought under the kernel lineage is progress against a
number, which is the same treatment settings, migrations and RLS coverage
already get.

#### A stop rule needs a start rule

A moratorium is a WIP limit, and a WIP limit only pays if the freed capacity
moves to the constraint. Stopping gap-list work does not by itself make the gate
arrive sooner — and while it holds, the duplication the gap list exists to
retire keeps accruing in the products. ERP has five numbering implementations
today; nothing here stops a sixth.

So the moratorium is not self-executing. It holds only while the gate is an
owned, reported workstream with the burn-down above as its measure. If the gate
is not being worked, the moratorium is not disciplined restraint — it is the
kernel idling while products pay the cost it was meant to remove, and it should
be lifted or re-argued rather than left running.

### 3. E8 and S7 are separate workstreams

They are sequenced, resourced and reported separately. The 2026-07-18 plan's
parallel framing is retired.

### 4. Sub goes first, as the reference adoption

Sub is the first product to run kernel persistence in production, and does so
explicitly as the reference: the path it walks is the one ERP follows.

Three reasons, any one sufficient:

- **Cost.** Sub's gate is an ADR ratification against work already wired. ERP's
  is a 224-table remediation with an unresolved security question underneath it.
- **Proof.** It is the cheapest available demonstration that ADR-0003's thesis
  holds in production, on a real product, rather than in the starter's own
  assembly.
- **Risk order.** Every kernel contract Sub exercises is one ERP does not
  discover the hard way. ERP's data plane contains `banking.bank_accounts`;
  Sub's tenancy topology is one row. Discover the contract defects on the
  simpler shape first.

ERP's E8 continues in parallel as a *measured* program — it is not paused — but
it is the second adopter, not the pilot.

### 5. Measure, freeze, then improve

The standard opening move for any convergence area is to make the current state
countable, stop it worsening, and only then improve it.

This is not new; it is three independent successes generalised. Settings used
shadow verification before cutover; migrations use the sequence gate; RLS
coverage now uses a baseline ratchet. The pattern works because an invisible
problem cannot be managed, and because a gate that fails hundreds of times on
its first run gets switched off rather than acted on.

A convergence area that cannot state its current number is not ready to start.

### 6. The acid test

Adoption is complete when **a kernel security fix reaches both products through
one tested dependency-update pull request each, and neither requires a bespoke
migration to receive it.**

Everything else — contract counts, coverage percentages, ledger rows — is
instrumentation for that sentence.

## Consequences

- Several genuine gaps stay open on purpose: numbering/gapless sequences,
  inbound/outbound webhooks, job scheduling, object storage, import/export, the SOT
  registry mechanism. Each is real. Each would land unadopted today.
- The kernel's persistence contracts remain, for now, **designed against zero
  production consumers**. `idempotency_records` is theory until Sub runs it.
  Naming this is the point: it is the live strategic risk, and it is a risk of
  untested contracts, not of missing features.
- Sub's roadmap acquires a dependency it did not ask for. That is a real cost
  and belongs in Sub's planning, not only in the platform's.
- ERP gains permission to go slower and be measured, rather than being expected
  to keep pace with a product whose gate is one decision.

## Alternatives rejected

**Keep building facilities and adopt later.** This is the status quo, and it is
what produced a kernel at `a33` whose persistence layer has never held a
production row. Each facility added while adoption is blocked increases the
untested surface a future adopter must swallow at once.

**ERP first, because it is the larger prize.** ERP is the larger prize and the
worse pilot. Its tenancy question is unresolved at the security level
(`app.bypass_rls` is a policy-level escape where the kernel uses role privilege), its RLS estate is 72.5 per cent incomplete, and a contract
defect discovered there is discovered on ledgers and bank accounts.

**Both at once.** This is the 2026-07-18 framing being retired. It produced a
plan in which Sub's readiness went unnoticed for weeks because the pair moved at
ERP's pace.

**Extract to a shared module and let each product adopt when ready.** ADR-0006's
extraction rule already forbids the failure this creates: an extraction is not
complete until the source product retires its local owner, or the result is a
third implementation rather than a shared one. Idempotency currently sits in
exactly that incomplete state and should not be joined by more.

## Open decisions this ADR does not make

1. **Ratifying Sub's ADR-0009.** That decision belongs in Sub's repository.
   This ADR assumes it is ratified; if it is rejected, decision 4 must be
   revisited.
2. **ERP's `app.bypass_rls`.** Whether a session may switch isolation off is a
   security posture decision, not a naming reconciliation, and it gates every
   ERP table-family migration. The question is not whether the capability
   should exist — it should — but whether it belongs in the policy predicate or
   in a role privilege.
3. **ERP's `continue-on-error: true`** on the integration CI job, which
   currently prevents the RLS ratchet from failing a build.
