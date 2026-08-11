# ADR-0017: Adoption is the scarce resource, and Sub goes first

**Status:** Proposed
**Date:** 2026-08-11
**Decision owner:** Michael
**Amends:** ADR-0003's *sequencing*, not its decisions. The composition model
(`product = pinned kernel + product assembly + domain modules`) is unchanged.
**Amends:** the 2026-07-18 adoption plan's treatment of E8 and S7 as one
parallel workstream.

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
  outbound webhooks, job scheduling, object storage, import/export, the SOT
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
