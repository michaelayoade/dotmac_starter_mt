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

`lastmile_rerun` is not a ticket state. It is a *Sub* ticket state. That
distinction is the whole design.

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

**ERP is a different domain** — internal helpdesk and project support, with
`REPLIED` (an email-helpdesk state nothing else has), `project_id`,
`contact_email`/`phone`/`address`, `resolution` text, ERPNext sync, and dates
rather than timestamps. **It also has zero ticket tests**, so it supplies no
behavioural proof to port.

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
| **status as a declared vocabulary** | see "the variant seam" below |
| **priority as a declared vocabulary** | ditto |
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
| status vocabulary | product manifest | Sub `lastmile_rerun`; ERP `REPLIED` |
| priority vocabulary | product manifest | Sub's 6 vs ERP's 4 |
| channel vocabulary | product manifest | Sub's 5; ERP declares none |
| category taxonomy | product | Sub's NCC regulatory categories; ERP's `category_id` |
| **subject linkage** | product | Sub → subscriber/lead; ERP → project/customer; vendor → licence |
| extra role-holders | product | Sub's technician, site coordinator, ticket manager |
| routing / automation policy | product | Sub's assignment + automation rules |
| additional fields | product, via `custom_fields` | ERP's `contact_address`, `resolution` |

## The mechanism that makes the vocabulary open

A declared status is not enough on its own: SLA clocks, workqueues and
"is this ticket still open?" need to reason about states they have never heard
of. So each declared status also declares its **lifecycle class**:

```
open · waiting · resolved · closed · cancelled
```

`lastmile_rerun` is class `waiting`. `REPLIED` is class `waiting`. `merged` is
class `closed`. The module's guards, clocks and projections key off the class;
the product owns the name, the label, and the transitions it permits between its
own states.

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

### 1. Subject linkage — the one that shapes every migration

A shared `tickets` table in `mod_tkt` cannot hold a foreign key to
`subscribers`, `projects` or `licences`. Three options:

- **(a) Polymorphic `(subject_type, subject_id)`** on the shared table. Matches
  ERP's proven `GeneratedDocument` shape and the starter's `custom_fields`
  `ENTITY_MODELS` registry. **No referential integrity** — a deleted subscriber
  leaves a dangling ticket.
- **(b) Product-owned link table** — `sub_ticket_subscriber(ticket_id,
  subscriber_id)` with real FKs, owned by the product's own lineage. Integrity
  preserved, one join per query, the module stays ignorant of product tables.
- **(c) `custom_fields` JSONB.** Weakest; no integrity, no index. Not
  recommended.

**Recommendation: (b).** A polymorphic id with no FK is precisely the "an
imported identifier becomes the only copy of truth" failure the Dotmac SOT
standard warns about, and ticket→subscriber is operational state, not a loose
annotation. (a) is cheaper and has fleet precedent, so it is a legitimate
choice — but it is a decision, not a default.

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
