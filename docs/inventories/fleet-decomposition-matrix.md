# Fleet decomposition matrix — ERP, CRM, Sub

**As of:** 2026-08-12
**ERP:** `0f4b1698` (`origin/main`) · **CRM:** `c64b5aa0` (`main`) · **Sub:** `9f6f9f36` (`feat/kernel-pin-a40`) · **Starter:** `10bd4a6`
**Measured by:** `scripts/fleet_decomposition_sweep.py`
**Frozen baseline:** [`fleet-decomposition-baseline.json`](fleet-decomposition-baseline.json)

This is the artifact [`docs/PRODUCT_VISION.md`](../PRODUCT_VISION.md) § "Make the
monoliths countable" requires: for each capability, its current owner and
writers, competing implementations, consumers, database/migration owner,
authority overlaps, target layer, and retirement condition — with the measured
duplication frozen so it can only shrink.

Read it under the same two cautions as every file in this directory
([README](README.md)): facts go stale, and **a row here is not permission to
extract anything**. ADR-0006 § "The extraction rule" and its 2026-08-08
product-first amendment still gate every move. A capability family below is a
*measurement bucket*, not an approved package boundary.

> **Placement, decided 2026-08-12.** `dotmac_governance` owns the programme
> matrix and approvals. **Starter owns the reusable implementation and the
> composition/conformance machinery** — and therefore the measurement, the
> frozen baselines and the ratchets, which have to execute somewhere that can
> run them. This document is Starter's measured evidence base; governance rows
> cite it rather than restating it. It is not a competing plan.

## The rule this document must not get wrong

**Duplication determines sequencing. It does not determine final scope.**

The end state is not "three monoliths with the overlaps removed":

```text
Starter
  ├─ dotmac-kernel
  ├─ dotmac-ui
  ├─ dotmac-<domain> modules
  └─ composition / conformance system
          ↓
Backoffice/ERP · Engagement/CRM · ISP/Sub
        thin composable assemblies
```

Three consequences that earlier revisions of this file got wrong:

1. **"ERP is not a de-duplication target" is a measurement finding, not a scope
   decision.** ERP's payroll, ledger, procurement, inventory and asset
   capabilities are unique *and* they still become Starter-owned modules.
2. **A second consumer proves reuse; it is not a prerequisite for modularizing a
   coherent domain.** "Zero duplication" is a reason to sequence a domain later,
   never a reason to leave it in a monolith permanently.
3. **Authority consolidation is intermediate, not terminal.** Retiring CRM's
   copy into Sub settles *who decides*; it does not settle *where the code
   lives*. Sub then consumes the Starter module like any other assembly.

So every row below resolves to kernel, a Starter module, or a contract. The
duplication counts choose the order.

## How the duplication is measured

`__tablename__` is the countable proxy for "somebody built this here": it is
persisted state, it has a migration lineage behind it, and it cannot be
hand-waved the way an overlapping service name can. The sweep AST-parses every
class body under each product's `app/models/` — grep over the same tree
over-reports (docstrings, migration comments) and under-reports (assignments
beside `__table_args__`), the same lesson `restatement_sweep.py` records.

Two counts, because one of them lies on its own:

| Count | Meaning |
|---|---|
| **exact** | The identical table name exists in two or more products. |
| **aliased** | The name matches only after a product namespace prefix (`crm_`, `inbox_`, `team_inbox_`, `support_`, `erp_`, `sub_`, `dotmac_`) is stripped. |

The aliased count exists because exact matching missed the single largest
duplication in the fleet. CRM's `crm_conversations`, `crm_messages`,
`crm_agent_presence` and Sub's `inbox_conversations`, `inbox_messages`,
`inbox_agent_presence` are the same capability built twice — a prefix is
packaging, not a different business fact. Counting only exact names scored that
entire subsystem as zero.

**A missing repository is UNMEASURED, never zero.** The sweep names absent repos
and the ratchet abstains rather than reporting the duplication as solved.

## Frozen duplication baseline

1,191 tables across the three source monoliths. **148 duplicated table names**
(125 exact + 23 aliased).

| | ERP | CRM | Sub |
|---|---:|---:|---:|
| Tables | 397 | 218 | 576 |

Where the duplication actually is:

| Pairing | Duplicated names |
|---|---:|
| CRM ↔ Sub only | **133** (110 exact + 23 aliased) |
| All three | 11 |
| ERP ↔ CRM only | 2 |
| ERP ↔ Sub only | 2 |

**Nine tenths of the fleet's countable duplication is CRM ↔ Sub.** ERP shares
15 table names with anything at all, and 11 of those are the platform tables the
kernel already owns (`sessions`, `user_credentials`, `mfa_methods`, `api_keys`,
`roles`, `permissions`, `role_permissions`, `audit_events`, `domain_settings`,
`scheduled_tasks`). The *only* non-platform table implemented in all three
products is `project_template_task_dependency`.

That **orders** the programme; it does not scope it. ERP is not a
de-duplication target — it is a kernel/UI adoption target with a large body of
genuinely single-owner back-office state, all of which still becomes Starter
modules. The nearest countable win is CRM ↔ Sub authority consolidation, which
needs no new package to *start*, and which every affected domain then follows
into a module.

### Per family

Counts are per product; `exact`/`alias` are duplicated *names*, not per-product
tables. Disposition vocabulary is defined under [Dispositions](#dispositions).

| Capability family | ERP | CRM | Sub | exact | alias | Disposition |
|---|---:|---:|---:|---:|---:|---|
| identity-access | 5 | 10 | 17 | 8 | 0 | kernel |
| authorization | 4 | 5 | 8 | 4 | 0 | kernel |
| party-identity | 5 | 7 | 12 | 4 | 0 | kernel |
| audit-events | 6 | 2 | 7 | 2 | 0 | kernel |
| settings | 6 | 7 | 9 | 4 | 1 | kernel (cutover in flight) |
| scheduling-runtime | 9 | 1 | 6 | 1 | 0 | kernel |
| integration-external | 6 | 10 | 27 | 4 | 0 | kernel primitives + module ← Sub |
| branding-templates | 1 | 0 | 1 | 0 | 0 | dotmac-ui + template studio |
| ticketing-sla | 6 | 17 | 22 | 10 | 6 | module ← Sub (package exists, adopter unnamed) |
| projects-tasks | 10 | 11 | 11 | 10 | 0 | consolidate CRM → Sub; module source **unassigned** |
| notifications-comms | 7 | 18 | 21 | 11 | 5 | kernel (consent + outbox) + module ← Sub |
| engagement-inbox | 0 | 28 | 29 | 0 | 9 | consolidate → Sub, then module ← Sub |
| sales-agreements | 7 | 13 | 24 | 8 | 2 | consolidate → Sub, then module ← Sub |
| billing-revenue | 12 | 3 | 74 | 2 | 0 | module ← Sub + contract with ERP |
| outside-plant | 0 | 33 | 94 | 30 | 0 | consolidate → Sub, then module ← Sub |
| field-workforce | 10 | 26 | 38 | 15 | 0 | consolidate → Sub, then module ← Sub |
| geospatial-qualification | 2 | 6 | 11 | 5 | 0 | consolidate → Sub, then module ← Sub |
| subscriber-service | 2 | 9 | 76 | 3 | 0 | consolidate → Sub, then module ← Sub |
| network-operations | 3 | 2 | 72 | 1 | 0 | module ← Sub |
| finance-ledger | 80 | 0 | 7 | 1 | 0 | module ← ERP + contract with Sub |
| people-payroll | 97 | 0 | 0 | 0 | 0 | module ← ERP |
| inventory-procurement | 39 | 3 | 4 | 0 | 0 | module ← ERP; CRM/Sub copies consolidate → ERP |
| assets-fleet | 23 | 0 | 0 | 0 | 0 | module ← ERP |
| expenses | 14 | 5 | 0 | 0 | 0 | module ← ERP; CRM copies consolidate → ERP |
| governance-workflow | 20 | 0 | 2 | 0 | 0 | module ← ERP |
| analytics-reporting | 16 | 2 | 3 | 2 | 0 | module source **unassigned** (ERP or Sub) |
| content-help | 7 | 0 | 1 | 0 | 0 | module ← ERP |

### Dispositions

Every row resolves to kernel, a Starter module, or a contract. Nothing stays in
a monolith as its permanent home.

| Disposition | Means |
|---|---|
| `kernel` | A universal application invariant. Target `dotmac-kernel`; every product's local implementation retires on lineage adoption. |
| `module ← <product>` | A coherent business domain that becomes an independently versioned Starter `dotmac-<domain>`, sourced product-first from the named qualifying implementation and its tests. **Measured duplication sets the wave, not the eligibility** — a domain with one implementation is still modularized; the second consumer proves reuse afterwards. |
| `consolidate → <product>, then module` | Duplicated operational state. Authority consolidates to the named owner first — an **intermediate** step that settles who decides, not where the code lives — after which that owner consumes the Starter module like any other assembly. |
| `contract` | An annotation, not a location: two legitimate owners whose state must agree. Needs a versioned contract, drift detection and reconciliation regardless of which modules the two sides end up in. Never a merged module. |
| `unassigned` | The target layer is settled but the qualifying *source* is not, or no owner has been adjudicated. Blocked on that decision before extraction, not before sequencing. |

## Authority, ownership, and retirement

### Kernel layer

| Capability | Owner today (writers) | Competing implementations | DB / migration owner | Authority overlap | Target layer | Retirement condition |
|---|---|---|---|---|---|---|
| identity-access | `dotmac_kernel.models` + `deps.authenticate_request`; ERP `app/services/auth*.py`; CRM `app/services/auth*.py`, `portal_auth.py`; Sub `app/services/auth*.py`, `access_*` | 3 local session/credential stores; 8 exact collisions | kernel `public` lineage vs each product's Alembic | none — same facts, three writers | `dotmac-kernel` | Product boots on the kernel lineage and deletes its local `sessions`/`user_credentials`/`mfa_methods`/`api_keys` writers. |
| authorization | kernel `rbac`; ERP `services/rbac.py`; CRM `services/rbac.py`; Sub `services/rbac.py` | 3 role/permission stores | as above | none | `dotmac-kernel` | Permission checks resolve through the kernel registry; local `roles`/`permissions` tables dropped. |
| party-identity | kernel `Party` (+ subtype tables); ERP `people`/`person`; CRM `person*`, `people`; Sub `parties`, `party_*` | 3 identity models, 4 exact collisions | as above | **Guard rail:** a CRM lead, a Sub subscriber and an ERP financial account must not collapse into one aggregate (PRODUCT_VISION § "Source-of-truth boundaries survive decomposition"). | `dotmac-kernel` | Each product's local person table is a view over kernel `Party`; subtype/profile data stays product-owned. |
| audit-events | kernel `audit.write_audit_event`; 3 local `audit_events` | 3 | as above | none | `dotmac-kernel` | Local `write_audit_*` helpers deleted; ADR-0008 declaration registry is the only vocabulary. |
| settings | kernel `settings_resolver` (`0.1.0a40`); ERP + Sub `app/models/domain_settings.py` native enums | 3 stores + native `SettingDomain` enums | kernel `0014`/`0021` vs product enums | ERP's 21-member and Sub's 28-member enums are the live non-conformance | `dotmac-kernel` | Task #22: `ALTER TYPE`-avoiding migration to the kernel shape, Governance repin, then `0.1.0a40` adoption. Tracked in [`module-extraction-sources.md`](module-extraction-sources.md). |
| scheduling-runtime | kernel `idempotency` (ADR-0014), `messaging`; ERP `saga_*`, `event_outbox`, `scheduled_tasks`; Sub `system_jobs`, `durable_timer`, `task_executions` | 3 at-most-once mechanisms + 2 outboxes | product lineages | ADR-0014 gives at-most-once ONE owner; today it holds no product row | `dotmac-kernel` | A product runs a real workload through `dotmac_kernel.idempotency` and retires its local reservation table. |
| integration-external | Sub `services/integration_*`, `connector.py`; CRM `services/connector.py`, `integration_http.py`; ERP `services/sync/` | 3 connector/webhook/retry stacks | product lineages | none on the primitives; the *payloads* are domain-owned | kernel primitives (webhook delivery, retry, dead-letter) + product adapters | Connector transport moves behind a kernel facility; per-integration mapping stays in the product that owns the domain. |

### Shared-module candidates

| Capability | Owner today (writers) | Competing implementations | Consumers | DB / migration owner | Authority overlap | Target layer | Retirement condition |
|---|---|---|---|---|---|---|---|
| ticketing-sla | Sub `support_ticket*` + SLA services; CRM `tickets`, `ticket_*`; ERP `app/models/support/ticket.py` | **3**, 10 exact + 6 aliased collisions | admin portals, field app, CRM agent inbox, NCC complaints return | 3 lineages; package would own `mod_tkt` | Vocabularies provably cannot merge; the product-neutral core plus per-product lifecycle classes can ([`ticket-sources.md`](ticket-sources.md)) | `dotmac-ticketing` module | **Blocked on naming the first adopter** — no product runs a non-kernel module lineage today. CRM's cutover must land into the shared module, not into Sub's local one, or it retires one owner instead of two. |
| projects-tasks | ERP `pm/`; CRM `projects.py`; Sub `installation_projects`, `project_*` | **3**, 10 exact collisions incl. the only three-way non-platform table `project_template_task_dependency` | ERP delivery, CRM buildout, Sub installation | 3 lineages | CRM's copy is settled (consolidate → Sub); ERP↔Sub is not. Project *templates* and task DAGs look identical; project *subjects* (a buildout, an install, an internal delivery) do not | unassigned pending audit | Needs the same audit ticketing got: one contract for template/task/dependency, product-owned subject linkage. Do not start before ticketing has an adopter. See [Contested](#contested--genuinely-unassigned). |
| notifications-comms | Sub `notification*`, `comms_*`; CRM `notification.py`, `comms.py`, campaigns; ERP `notification.py` | 3, 11 exact + 5 aliased | every surface | 3 lineages | Consent/suppression exists only in Sub; delivery/outbox is the kernel outbox built twice ([`consent-suppression-sources.md`](consent-suppression-sources.md), [`delivery-outbox-sources.md`](delivery-outbox-sources.md)) | consent + outbox → kernel; template rendering → template studio; channel policy → settings; campaigns → module | Four open dossiers, **consent before delivery**. A campaign module that ships before the consent owner will send to suppressed recipients. |

### Authority consolidation — CRM's operational duplicates

**Intermediate, not terminal.** Consolidating to Sub settles which product
decides; each of these domains then follows its `module ← Sub` row and Sub
consumes the Starter module. Two standing decisions already assign the
consolidation:

- *Sub owns the complete person lifecycle; CRM is excluded* (approved, decision
  owner Michael, system of record `dotmac_sub`): interaction/conversation →
  party/contact → lead → quote → sales order → installation/project/service
  order → subscription → CX handoff → support → retention. CRM "cannot create,
  mirror, enrich, resolve, or decide person, lead, quote, order, project,
  work-order, subscription, ticket, attribution, support, or official-timeline
  state." Residual CRM identifiers are provenance only.
- *Complete every CRM web capability in Sub and retire CRM* (approved), whose
  executable control is Sub's `crm_web_retirement` ledger over 73 web modules
  and 813 routes — **~62 of the 73 are retirement work; ~11 justify a module
  row.**

So CRM is a **retirement target, not a decomposition source for operational
state**. Its 133 duplicated table names are the schema-level view of that
retirement ledger. None of them is an extraction source — the qualifying
implementation for the eventual module is Sub's, not CRM's.

| Capability | CRM copy | Sub authority | Duplicated names | Retirement condition |
|---|---:|---:|---:|---|
| outside-plant | 33 | 94 | 30 exact | CRM reads fibre/OLT/ONT/splice state through Sub's API; local `fiber_*`, `olt_*`, `ont_*`, `splitter*`, `splice*` dropped after shadow/reconcile proves parity. Already recorded as a parallel-authority violation of Sub's checked-in `FIBER_TOPOLOGY_SOT.md`, with the resolution direction (load OSP into Sub's staging→review→apply pipeline, then demote CRM's copy) agreed and unexecuted. |
| engagement-inbox | 28 | 29 | 9 aliased | The largest duplication in the fleet, and the one exact-name counting scored as zero: `crm_conversations`/`inbox_conversations`, `crm_messages`/`inbox_messages`, `crm_agent_presence`/`inbox_agent_presence`, `crm_pipelines`/`pipelines`, plus the routing/macro/assignment tables. Sub already owns `conversation_ticket_handoff` and `conversation_lead_relationships`, so the seam is load-bearing today. Retires when Sub's team inbox closes CRM's agent-facing capability gaps. |
| field-workforce | 26 | 38 | 15 exact | Work orders, technician profiles, shifts, skills, dispatch and ETA are Sub-owned; CRM keeps only agent-side projections, provenance-stamped and repairable. **Known gap:** Sub's dispatch assigns without availability/conflict checks, and CRM's `is_technician_available` dies at exit — close it before, not after. |
| sales-agreements | 13 | 24 | 8 exact + 2 aliased | `crm_quotes`/`quotes`, `quote_line_items`, `crm_leads`/`leads`, `sales_orders`, `contracts`, `referrals`. Sub's PR #1508 implements the lead→quote→order→project→subscription chain; the remaining work is adoption, capture population and reconciliation, not a new owner decision. |
| geospatial-qualification | 6 | 11 | 5 exact | Coverage areas, service buildings and qualifications resolve from Sub; CRM's copies retire. |
| subscriber-service | 9 | 76 | 3 exact | CRM already treats subscribers correctly as Sub projections; the remaining tables become read-through projections with a named source, never writers. |

Each row still needs the standard transfer dossier — old owner, new owner,
shadow phase, cutover gate, fallback retirement, and a boundary test that fails
if a retired writer comes back.

### Contested — genuinely unassigned

| Capability | The collision | Why it is not yet assignable |
|---|---|---|
| projects-tasks, ERP ↔ Sub (ERP 10 / Sub 11) | `project_templates`, `project_template_tasks`, `project_template_task_dependency` — the only non-platform table in all three products — plus `project_tasks`, `project_comments`, `project_task_assignees` | CRM's copy retires under the decisions above, but ERP's delivery projects and Sub's installation projects are two legitimate owners with an identical template/task/dependency mechanism. Whether that mechanism is a shared module or two independent implementations of a common shape has never been adjudicated. Needs the audit `dotmac-ticketing` got. |
| billing-revenue, ERP ↔ Sub (ERP 12 / Sub 74, plus finance-ledger's `bank_accounts`) | Only 2 exact collisions — the duplication is *semantic*, not structural: Sub owns subscriber billing, ERP owns the ledger, and the ERP↔Sub sync reconciles them | Explicitly **not** a module. The known money-correctness defects in that sync are contract and reconciliation bugs; merging the two into a shared package converts a fixable contract into an unfixable one. Deliverable is a versioned contract with drift detection and repair. |

### Single-owner domains — modules, sequenced later

ERP's back office — people-payroll (97), inventory-procurement (39),
assets-fleet (23), governance-workflow (20), expenses (14) — has **zero measured
duplication with CRM or Sub**.

That is a statement about *when*, not *whether*. Each of these is a coherent
domain and becomes a Starter `dotmac-<domain>` module sourced from ERP. Zero
duplication means no other product is currently paying the cost of the second
implementation, so the work sequences behind the domains that are, and the
second consumer that proves reuse arrives after the module exists rather than
before it is allowed to.

The same reading applies to Sub's network-operations (72) and to
analytics/content rows: single-owner today, module-bound tomorrow.

Two counter-flows consolidate toward ERP: CRM's 5 expense/material-request
tables plus its 3 `inventory_*` tables, and Sub's 4
`vendor_material_release*`/`vendor_advances` tables, duplicate ERP-authoritative
expense and procurement facts.

finance-ledger is the one row that looks single-owner and is not. ERP holds 80
tables; Sub holds 7 — `bank_accounts` (the only ERP↔Sub non-platform collision),
`bank_reconciliation_runs`/`_items`, `ledger_entries`, `tax_rates`, and the
withholding-tax pair. That is the ERP↔Sub billing contract seam showing up as
schema. It is `module ← ERP` **and** `contract`: modularizing the ledger does
not remove the reconciliation obligation, it gives each side a versioned owner
to reconcile between.

## Sequencing

Every family above ends as kernel, a Starter module, or a contract. This table
is the **order**, chosen by duplication cost and adoption risk under ADR-0017's
constraint that adoption — not scope — is the scarce resource.

| Wave | Work | Why here |
|---|---|---|
| **0** | Finish kernel lineage adoption: Sub on `0.1.0a40`, settings task #22, ERP's E8 tenancy gate. CRM has **no kernel pin and zero `dotmac_kernel` imports** — it needs an entry plan, not a module. | ADR-0017. No product runs a non-kernel module lineage; every later wave is blocked behind this. |
| **1** | CRM → Sub authority consolidation, driven by Sub's existing `crm_web_retirement` ledger: outside-plant, engagement-inbox, field-workforce, sales-agreements, geospatial, subscriber projections. **Entry gate:** declare an owner in Sub's registry for the 28 duplicates that have none — see [`fleet-fact-level-decomposition.md`](fleet-fact-level-decomposition.md). | 72 of the 133 CRM↔Sub duplicated names, and it needs no new package to start. The authority questions are already adjudicated; this is capability closure and reconciliation against a named owner. Intermediate — these domains follow into modules in wave 4. |
| **2** | `dotmac-ticketing` cutover — name the adopter, adopt in Sub, then land CRM's ticket retirement into the module. | The package and its source audit already exist; the only open gate is an adopter. The **first proof that a non-kernel module lineage runs in production**, which every later module wave depends on. |
| **3** | Consent → delivery/outbox → channel policy → campaigns, in that order. | Consent is a kernel owner and a legal boundary; shipping delivery first ships a suppression bypass. |
| **4** | Sub-sourced modules: network-operations, subscriber-service, outside-plant, field-workforce, engagement-inbox, sales-agreements, billing. | The domains consolidated in wave 1, now extracted product-first from Sub and consumed back by Sub as an assembly. Depends on wave 2's lineage proof. |
| **5** | ERP-sourced modules: finance-ledger, people-payroll, inventory-procurement, assets-fleet, expenses, governance-workflow, content-help. | Zero duplication, so nobody is paying for a second implementation today — that sequences it late, and does not exempt it. ERP's kernel/UI adoption must land first. |
| **6** | Source adjudication for projects-tasks (ERP vs Sub) and analytics-reporting, then extraction. | Target layer settled, qualifying source unsettled. Needs the audit `dotmac-ticketing` got. |

Running in parallel, on the contract track: the ERP↔Sub billing contract repair
and its drift detection. It has known money-correctness defects. Modularizing
both sides does not merge them, and must not be allowed to look like it does.

## How progress is measured

**Not by duplicate table counts.** Those measure the starting problem, and they
stop moving long before the programme is done — a domain with zero duplication
can still be entirely unmodularized.

The measures that count, in order:

1. **Capabilities implemented in Starter** — kernel facilities and
   `dotmac-<domain>` modules with a released, versioned contract.
2. **Source apps consuming those packages** — an exact pin, exercised in
   production, not a vendored copy.
3. **Old writers retired** — the displaced local implementation deleted or
   hard-gated, proven by a boundary test that fails if it returns.

Duplicate counts and the fact-level coverage numbers are *inputs to sequencing*
and a regression guard. A module with no consumer, or with its source
implementation still active, is work in progress whatever its version number
says.

## Keeping this honest

```sh
python scripts/fleet_decomposition_sweep.py            # report
python scripts/fleet_decomposition_sweep.py --check    # ratchet against the baseline
python scripts/fleet_decomposition_sweep.py --write-baseline
```

The baseline is a **two-directional ratchet** (ADR-0018). A count that rises
fails: duplication may only shrink. A count that falls *also* fails, until the
baseline is lowered to record the win — a frozen figure that never follows
reality down stops being evidence.

`tests/architecture/test_fleet_decomposition_matrix.py` keeps this document and
the baseline JSON in sync. It does not re-measure: the source monoliths are not
present in this repository's CI, and a test that silently passes when they are
absent would be the exact failure the sweep refuses to make. Re-run the sweep
from a checkout that has the fleet beside it, and update both files together.
