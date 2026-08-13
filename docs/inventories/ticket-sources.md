# Ticket module source inventory — the contract, and the variant seam

**As of:** 2026-08-11
**Starter:** `58bb194` (`origin/main`)
**Sub:** `1f41538e2` · **ERP:** `1e6b3270` · **CRM:** `60ec2063` · **Vendor CP:** `f9ca367`

Evidence input to a reusable ticket module. It executes steps 1–3 of
[`module-extraction-sources.md`](module-extraction-sources.md)'s procedure: name
the contract, inventory every product, select the source implementation.

**Scope is set by ADR-0003, not by what ERP and Sub happen to have built**
(ADR-0006 § 5a). ERP, Sub, the vendor control plane and future products each
need a ticket; none of them needs an *ISP* ticket. So the question this document
answers is not "can these three implementations merge" — measurably they cannot
— but "what is the product-neutral core, and what does each product declare on
top of it".

## Headline

**The three implementations do not share a contract, and that is a statement
about their vocabularies, not about the capability.** Once status, priority and
channel become *declared* vocabularies rather than enums (ADR-0008), what is
left is a single coherent lifecycle that all four products need and none of them
should own.

`lastmile_rerun` is not a ticket state at all — it is a *reason* a ticket is
waiting. Sub's own code proves it: all 16 references lump it into a set with the
standard statuses, and every one of those sets is asking a lifecycle-class
question. Separating **class** from **status** from **reason** is the whole
design; the two amendments below are how it got there.

### Composition amendment — 2026-08-13

The shared lifecycle is reusable code, not shared application authority
(ADR-0024). Every adopter installs its own module lineage and owns only its
local tickets. Applications exchange versioned API/webhook observations; they
never query another adopter's module tables, and a remote status cannot write a
local lifecycle directly.

That distinction is material for ERP: its one table mixes internal
back-office/project support with ERPNext/CRM-synchronized records. The former
may cut over to ERP's local module installation after classification. The
latter are archived and retired from ERP's operational schema, or cause
creation of a separate ERP-owned work ticket when a named local workflow needs
one. Provider-specific payload mapping stays in an Integrator connector plugin
and does not enter ERP or this module.

## The four products

| | Sub | CRM | ERP | Vendor CP |
|---|---|---|---|---|
| model | `app/models/support.py` | `app/models/tickets.py` | `app/models/support/ticket.py` | — none — |
| table | `support_tickets` | `tickets` | `ticket` | — |
| statuses | 11 | 11 (identical) | 5 | — |
| priorities | 6 | 6 (identical) | 4 | — |
| channel | 5-value enum | 5-value enum | none | — |
| title field | `title` | `title` | `subject` | — |
| resolution dates | `resolved_at` datetime | `resolved_at` datetime | `resolution_date` **date** | — |
| tenancy | Sub tenancy | — | `organization_id` | — |
| test files | **29** | 22 | **0** | — |
| last touched | 2026-08-10 | 2026-07-02 | 2026-05-12 | — |

**Sub and CRM are one implementation forked once**, not two. Their status enums
agree on all 11 values *including* `lastmile_rerun` and
`site_under_construction` — fiber-ISP concepts CRM has no independent reason to
know. Same priorities, same channels, same `TicketAssignee`/`TicketComment`
companions, same `merged_into_ticket_id`.

**CRM's copy is already being retired**, so it is not a second consumer and must
not be counted as one: `docs/designs/CRM_WEB_RETIREMENT.md`,
`docs/runbooks/CRM_TICKET_CAPABILITY_CUTOVER.md`,
`tests/test_crm_ticket_capability_cutover.py`, and `app/tasks/crm_ticket_pull.py`
importing into Sub during the transition. Sub's SOT map already names the owner:
`support.ticket_lifecycle` is `authoritative_record` / `owner_managed` /
**`complete`**.

**ERP mixes two authority classes in one table** — locally owned internal
helpdesk/project support and ERPNext/CRM-synchronized records — with `REPLIED`
(an email-helpdesk state nothing else has), `project_id`, `contact_email`/
`phone`/`address`, `resolution` text, and dates rather than timestamps. It needs
a total authority classifier before migration. **It also has zero ticket
tests**, so it supplies no behavioural proof to port.

**Vendor CP is greenfield** — no ticket model at all. It does have
`src/vendor_cp/approvals/`, which is the same request-lifecycle shape wearing a
different word; worth reading before the core is fixed, so the module is not
accidentally shaped to exclude it.

## The contract

> **A ticket is a durable request for work, with one owner, a guarded lifecycle,
> and an auditable trail of what was said and done.**

The module owns that sentence and nothing else. Concretely:

### Core — the module owns

| Element | Why it is generic |
|---|---|
| identity + human-readable number | every product allocates one; ERP has **five** separate numbering implementations today |
| title, description | universal |
| **status — the closed standard vocabulary** | products extend the REASON layer, never the status layer |
| **lifecycle class** | fixed at five; the machine semantics everything else keys off |
| guarded transitions | "which transitions are legal" is a mechanism; *which states exist* is not |
| assignment (person + team) | universal; Sub's assignment-rule engine and round-robin cursor are the qualifying source |
| comments, internal vs public | all three have exactly this, with the same semantics |
| lifecycle timestamps | `created`/`updated`/`due`/`resolved`/`closed` |
| SLA clocks | keyed off the lifecycle CLASS (below), never off a status name |
| merge / duplicate resolution | Sub and CRM both have it; ERP will want it |
| tenant scoping + RLS from revision 1 | the starter's contribution — neither ERP nor CRM has RLS here |
| audit | every transition is an audit action |

### Variant — each product declares

| Element | Declared by | Example |
|---|---|---|
| **status reason** | product manifest | Sub `lastmile_rerun`; ERP `replied` — see the second amendment: these are reasons, not statuses |
| priority vocabulary | product manifest | Sub's extra `lower`/`normal` rungs |
| channel vocabulary | product manifest | Sub's 5; ERP declares none |
| category taxonomy | product | Sub's NCC regulatory categories; ERP's `category_id` |
| **subject linkage** | product | Sub → subscriber/lead; ERP → project/customer; vendor → licence |
| extra role-holders | product | Sub's technician, site coordinator, ticket manager |
| routing / automation policy | product | Sub's assignment + automation rules |
| additional fields | product, via `custom_fields` | ERP's `contact_address`, `resolution` |

## The mechanism that makes the vocabulary open

> **Superseded in part by the second amendment below.** This section's original
> claim was that products declare *statuses*. They declare *reasons*; the status
> vocabulary is closed. The class mechanism it describes is unchanged and is
> what the rest of the design rests on.

SLA clocks, workqueues and "is this ticket still open?" must not depend on
knowing every term a product invents. So every status carries a **lifecycle
class**:

```
open · waiting · resolved · closed · cancelled
```

The module's guards, clocks and projections key off the class. A product's
`lastmile_rerun` is a reason attached to a `waiting`-class status, so those
consumers need no knowledge of it whatsoever.

**This is the whole reason a shared ticket module is possible at all.** Merge the
enums and you get a 16-member union that means nothing in either product —
exactly the `kind ∈ {notification, document}` error the Template Studio audit
disqualified. Declare the vocabulary and classify it, and Sub's ISP
states and ERP's helpdesk states coexist without either product learning the
other's language. It is ADR-0008's rule applied to a lifecycle instead of a
settings domain.

## Amendment — 2026-08-11: the line is *standard helpdesk*, and Sub is not exempt

**Decision (Michael):** the module ships the **standard helpdesk vocabulary**.
Any term that is not standard helpdesk is composable — declared by the product
that needs it — **including Sub's own**. Being the source implementation buys
Sub no right to bake its domain terms into the shared core.

That settles open decision 2 below: ship a standard set, not "classes only".

### The standard core the module ships

Present across mainstream helpdesk systems (Zendesk, Jira Service Management,
Freshdesk, osTicket) and the ITIL incident lifecycle:

| status | class | why standard |
|---|---|---|
| `new` | open | raised, not yet triaged |
| `open` | open | being worked |
| `pending` | waiting | blocked, cause unspecified |
| `waiting_on_customer` | waiting | awaiting requester reply |
| `on_hold` | waiting | blocked on internal party or third party |
| `resolved` | resolved | fixed, awaiting confirmation or auto-close |
| `closed` | closed | terminal |
| `cancelled` | cancelled | withdrawn or void |
| `merged` | closed | folded into another ticket |

Priorities: `low`, `medium`, `high`, `urgent`.
Channels: `web`, `email`, `phone`, `chat`, `api`.
Comment authors: `customer`, `staff`, `system`.

### What each product must now declare

| term | product | why it is not standard |
|---|---|---|
| `lastmile_rerun` | **Sub** | fiber last-mile re-run — an ISP field operation |
| `site_under_construction` | **Sub** | ISP build-phase state |
| `pending_confirmation` | **Sub** | Sub's own resolution-confirmation flow; the standard expresses this as `resolved` awaiting close |
| `lower`, `normal` | **Sub** | extra priority rungs beyond the standard four |
| `replied` | **ERP** | or map onto `waiting_on_customer`, which is the standard role it fills |
| NCC categories | **Sub** | Nigerian regulatory taxonomy |

### Two things this exposes in Sub

1. **Sub has no `resolved` state.** It goes `pending_confirmation → closed`, so
   it is missing a standard rung and carries a bespoke one in its place.
   Adopting the standard core gives Sub `resolved` and makes
   `pending_confirmation` either a declared Sub state layered on top of it or a
   retirement.
2. **Sub declares both `medium` and `normal`** as separate priorities — two
   names for one rung, which no consumer can order meaningfully.

### Second amendment — they are not statuses at all

Testing the previous amendment against Sub's code changes the answer again, and
in the better direction.

**All 16 references to `lastmile_rerun` and `site_under_construction` put them
in a SET alongside the standard statuses**, and every one of those sets is
answering a *class* question:

| site | set | the question it asks |
|---|---|---|
| `services/sla_assignment.py` | `SLA_APPLICABLE_STATUSES` | does the SLA clock run? |
| `services/ticket_validation.py` | `_OPEN_STATUSES` | is this ticket open? |
| `services/customer_service_state.py` | `OPEN_INFRASTRUCTURE_TICKET_STATUSES` | is this ticket open? |
| `services/ticket_assignment/selectors.py` | routing set | who should work it? |
| `services/notification_template_conditions.py` | condition list | should this notification fire? |
| `services/status_presentation.py` | label/colour map | how is it displayed? |

**Not one site branches on `lastmile_rerun` to do something no other waiting
status does.** As a *status* it carries no behaviour its class does not already
carry. Its actual job is to record **why** the ticket is waiting, and to be
filtered and searched on.

That is a **reason**, not a state. So the model gains a layer and the status
vocabulary gets to be *closed*:

| layer | who owns it | extensible | what it decides |
|---|---|---|---|
| **lifecycle class** — `open`/`waiting`/`resolved`/`closed`/`cancelled` | module | **no**, fixed at 5 | machine semantics: does the SLA clock run, does it count as open |
| **status** — the 9 standard terms | module | **no** | what the UI calls it; which transitions are guarded |
| **status reason** — `lastmile_rerun`, `site_under_construction`, `awaiting_parts` | **product declares** | yes | *why* it is in this status; filterable, searchable, drives routing and notification conditions |
| **tag** — free-form | operator creates | yes | searchable only; no behaviour |
| **comment** | anyone | n/a | narrative; never queried for behaviour |

**The test for which layer a term belongs in: does any code branch on it?**
If code branches → a declared reason, with an owner and a consumer CI can check.
If only humans search → a tag, needing no declaration.
If it is prose → a comment.

Applied to Sub: nothing branches on its ISP terms individually, but
`notification_template_conditions` and the assignment selectors *do* consume
them as filters — so they are **reasons**, not free-form tags.

### What this buys

- **The status vocabulary closes.** No product ever adds a status, so no
  product can quietly redefine what "open" means for the fleet. Composability
  moves to the reason layer, where it belongs and where it is cheap.
- **Sub's six hardcoded membership lists collapse into class predicates.** All
  16 sites become `status.class in {open, waiting}`. The ISP terms stop being
  something every new call site must remember to include — which is exactly the
  bug shape those lists invite: add a tenth status, forget one of six lists,
  and the SLA clock silently stops for it.
- **`pending_confirmation` resolves cleanly**: it becomes the standard
  `resolved` (class `resolved`) plus reason `awaiting_customer_confirmation` —
  which also fixes Sub's missing standard rung noted above.
- Reasons are filterable and searchable **as data**, so "show me every ticket
  waiting on a last-mile re-run" is a query rather than a status filter that
  only exists because someone widened an enum.

### Retirement surface

`lastmile_rerun` and `site_under_construction` appear in **16 Python locations**
in `dotmac_sub/app/` and **0 templates**. So Sub's cutover is a bounded code
change, not a UI rewrite — the terms never leaked into markup. Sub's
`TicketStatus` Python enum is itself the thing being retired: after this, no
repository holds an ISP term in an enum, and every non-standard term is a row in
a declaration registry that CI can check for an owner and a consumer, exactly as
ADR-0008 requires of `SettingDomain`.

## Source selection

Per product-first: prefer the production-used implementation with the strongest
behavioural proof.

| Element | Source | Proof |
|---|---|---|
| lifecycle, guarded transitions, timestamps | **Sub** `support.ticket_lifecycle` | `test_ticket_status_transition.py`, `test_support_services.py`; SOT status `complete` |
| assignment rules + round-robin cursor | **Sub** `support.ticket_assignment_*` | `test_ticket_assignment_engine.py` |
| automation rules | **Sub** `support.ticket_automation_*` | `test_support_automation.py` |
| SLA clocks | **Sub** `support.ticket_sla_clock` | via workqueue suite |
| number allocation | **Sub**, then reconcile with ERP's five | `test_support_services.py` |
| duplicate detection | **Sub/CRM** `find_duplicate_ticket_candidates` | CRM suite |
| comments, internal/public | **Sub** | `test_support_services.py` |
| tenant RLS from revision 1 | **starter** | neither product has it |
| polymorphic subject + provenance | **ERP** `GeneratedDocument` pattern | 10 integration tests (different entity, proven shape) |

Sub is the source for almost all of it, and its 29 test files are the reason.
**Nothing is sourced from ERP's ticket** — zero tests means no proof, and
product-first sources implementations *with* their tests.

## Open decisions

### 1. ~~Subject linkage~~ — SETTLED 2026-08-11: (b), via the module-shipped helper

A shared `tickets` table in `mod_tkt` cannot hold a foreign key to
`subscribers`, `projects` or `licences`. Three options:

**Measured 2026-08-11, and it mostly settles the question: a ticket does not
have ONE subject.** Sub's ticket carries **six** subject links (`subscriber_id`,
`customer_account_id`, `lead_id`, `customer_person_id`,
`origin_conversation_id`, `service_team_id`); ERP's carries **five**
(`raised_by_id`, `project_id`, `customer_id`, `category_id`, `team_id`).

A single `(subject_type, subject_id)` pair holds exactly one of those. So the
polymorphic option does not merely weaken integrity here — **it cannot
represent what both products already do**, and the remainder would fall into
`custom_fields` JSONB: no integrity *and* no queryability, worse than either
option on its own.

- **(a) Polymorphic `(subject_type, subject_id)`** on the shared table. Matches
  ERP's proven `GeneratedDocument` shape and the starter's `custom_fields`
  `ENTITY_MODELS` registry. **No referential integrity** — a deleted subscriber
  leaves a dangling ticket, and Postgres cannot help because it does not know
  `subject_id` means anything. **Holds one subject only.**
- **(b) Product-owned link table** — `sub_ticket_subscriber(ticket_id,
  subscriber_id)` with real FKs, owned by the product's own lineage. Integrity
  preserved, one join per query, the module stays ignorant of product tables.
- **(c) `custom_fields` JSONB.** Weakest; no integrity, no index. Not
  recommended.

**Recommendation: (b)** — on multiplicity first, integrity second. The
one-subject limit is not a trade-off that can be accepted and moved past; it
breaks on day one for both existing products. Integrity then compounds it: a
polymorphic id with no FK is precisely the "an imported identifier becomes the
only copy of truth" failure the Dotmac SOT standard warns about, and
ticket→subscriber is operational state read by billing and field dispatch, not
a loose annotation.

**Where polymorphic IS right, so this is not read as a blanket rule:** ERP's
`GeneratedDocument` uses `(entity_type, entity_id)` correctly — a generated
document genuinely can attach to anything, the relationship is annotation, and
it is one-of by nature. Open-ended, one-of, low integrity stakes: polymorphic.
Closed set, known at design time, operational: link table.

**Honest cost of (b):** one indexed join per subject-bearing query; ~10 lines of
migration plus an RLS policy per link table per product; and a **migration
ordering constraint** — `public.sub_ticket_subscriber` referencing
`mod_tkt.tickets` means the module's lineage must run before the product's. That
ordering requirement is the one thing (b) costs that (a) does not.

**DECIDED (Michael, 2026-08-11): option (b), delivered through the helper.**
The module ships a declarative `link_subject("subscriber", Subscriber)` that
generates the table, both foreign keys, the index and the RLS policy *in the
product's own lineage*. Products get one line per subject, the schema still gets
real constraints, and the module never learns what a subscriber is.

Consequences to carry into the build:

- The helper emits migration operations into the PRODUCT's lineage, never the
  module's. It is a code generator for the product's own `mod_`/`public`
  migration, not a table the module creates on the product's behalf — otherwise
  the module would own a table outside `mod_tkt`, which rule 14 forbids.
- Ordering is now a first-class cutover step: the module's lineage must run
  before any product link-table migration, because the FK targets
  `mod_tkt.tickets`. The composed migration gate should assert this rather than
  leaving it to deploy order.
- Every generated link table is tenant-scoped with its own RLS policy, per the
  tenancy rule. The helper emitting that policy is the point — a hand-written
  link table is exactly where an RLS policy gets forgotten.
- `ON DELETE` is an explicit argument with no default. Whether deleting a
  subscriber cascades the link or is restricted by it is a product policy
  decision, and a silent default would make it an accident.

### 2. ~~Does the module ship any statuses at all?~~ — SETTLED 2026-08-11

The module ships the **standard helpdesk vocabulary**; everything else is
declared by the product that needs it, Sub included. See the amendment above.

### 3. ADR-0017 interaction

A ticket module is a **module**, not a kernel facility, so it is not literally
under the moratorium — Template Studio shipped on the same footing. But its
lesson binds: it would carry its own `mod_tkt` lineage, and no product runs a
non-kernel module lineage in production today. **Name the first adopter before
writing the first migration**, or this lands as another released package with
zero consumers.

### 4. CRM sequencing

Building this while the CRM ticket cutover is in flight risks a third parallel
implementation. Either finish the cutover first, or make CRM's retirement land
*into* the shared module rather than into Sub's local one — the latter is more
work but retires two owners instead of one.

## Product defects found (report regardless of extraction)

1. **ERP** — `app/models/support/ticket.py` has **zero test files**. A support
   ticket system with no behavioural proof.
2. **ERP** — `TicketStatus`/`TicketPriority` are native enums, the same ADR-0008
   non-conformance as `SettingDomain` and `document_template_type`, needing the
   same `ALTER TYPE`-avoiding repair.
3. **ERP** — `opening_date`/`resolution_date` are `date`, not timestamptz, so
   SLA duration cannot be computed to better than a day.
4. **CRM/Sub** — the forked enum means a status added to one silently diverges
   from the other while the pull importer is still running.

## Not covered

Sub's work-order handoff (`TICKET_WORK_ORDER_HANDOFF_SOT.md`), which is
adjacent and product-owned; the agent workqueue, which is a projection over
tickets rather than part of them; vendor CP's `approvals`, which should be read
before the core is fixed.
