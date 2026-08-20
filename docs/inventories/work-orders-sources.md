# Work orders / field execution — product-first source inventory

- **Status:** audit complete; package built, no product cut over
- **Audit date:** 2026-08-18

Source revisions inspected:

- `dotmac_starter_mt` `c6ef6cd7b13105bd95c3faf354ffee9032077625`
- `dotmac_sub` `3f8d74825bee47b98c3c532229b72f3a8a5b16aa`
- `dotmac_crm` `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`
- `dotmac_erp` `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`
- `dotmac_vendor_control_plane` `e6b2bbee815cf9fd3ce99ceed0ff3a1f5763f057`

This inventory answers one question: what is the smallest reusable owner of
**physical work execution**? It does not use the fleet matrix's broad
`field-workforce` label as a package boundary. That row also counts technician
profiles, skills, shifts, availability, dispatch scoring, ETA and live
location. Those are inputs and adapters around execution, not one lifecycle.

## Ruling

`dotmac_sub` is the qualifying source. Its checked-in
`docs/designs/WORK_ORDER_IDENTITY_SOT.md` names native `WorkOrder` as the
authoritative record, records the CRM mirror/importer retirement, and makes
remaining CRM identifiers provenance-only. The constructor and assignment
owner is guarded by
`tests/architecture/test_work_order_command_ownership.py`; its mobile execution
behavior is covered by `tests/test_field_transitions.py`, work logs by
`tests/test_field_worklogs.py`, notes by `tests/test_field_notes.py`, and
evidence by `tests/test_field_attachments.py`.

CRM is a retirement inventory. ERP's matching “maintenance work order” is a
fixed-asset maintenance domain and is not the same physical customer/service
execution contract. Vendor CP has no implementation.

The extracted unit is therefore:

> tenant-scoped work-order identity; guarded scheduled → dispatched → active →
> paused/completed/cancelled execution; assignment history; idempotent execution
> events; work logs; notes; and generic evidence references.

It excludes the reason the work exists and every product consequence of its
outcome.

## Fleet comparison

| Concern | Sub | CRM | ERP / Vendor CP | Extraction ruling |
|---|---|---|---|---|
| work-order root | authoritative `app/models/work_order.py`; native UUID + public id | older `app/models/workforce.py::WorkOrder`, still linked to CRM people/subscriber/project/ticket | ERP fixed-asset maintenance only; Vendor CP none | port Sub's execution fields; product subjects leave the shared table |
| create/update/assign | sole constructor in `app/services/work_order_commands.py` | `app/services/workforce.py::WorkOrders`, including direct sync back to Sub | no qualifying implementation | port behavior, replace HTTP errors/commit/rollback with typed errors + flush |
| execution lifecycle | `app/services/field/transitions.py`; nine field events and seven states | older workforce/field services and status writer | none | closed module vocabulary ported from Sub |
| assignment | native queue + technician projection; Sub owner test prevents parallel writers | assignment rows, queue, auto-assign | none | module owns history/current projection; product owns assignee roster and eligibility |
| availability/routing/ETA | Sub dispatch, but known availability/conflict gap | mature `is_technician_available`, scoring and ETA | none | excluded; a later dispatch/workforce owner consumes work orders |
| work logs | `FieldWorkLog`, transition-started timers and manual log service | work log implementation | none | port; correct one actor/two open timers with a database constraint |
| notes | `FieldWorkOrderNote` | `WorkOrderNote` | none | port narrative facts, no attachment bytes |
| evidence | attachment metadata plus product fiber/splice queries | attachment lists | none | module stores generic evidence references; product owns bytes and domain proof |
| movement/location | field movement, presence, pings and routing | field map/location | none | excluded; Positioning/dispatch observation concern |
| inventory/materials | field inventory, allocation, requests, ERP sync | inventory/material flows | ERP is stock owner | excluded; inventory owner links its facts to the work order |
| downstream consequences | Sub events, customer communications, team inbox, ticket timeline | direct notifications and Sub sync | none | excluded; adopter reacts after local commit through outbox/reconciliation |

The measured fleet matrix records 38 Sub, 26 CRM and 10 ERP table-name matches
under `field-workforce`, with 15 exact collisions. Those counts prove overlap;
they do not prove one package. Exact-name inspection shows ERP's rows are
skills/maintenance rather than the qualifying execution aggregate, and the
scope above is the coherent vertical slice supported by Sub's behavior tests.

## Sub source and mandatory parity

### Identity and creation

`app/models/work_order.py` currently carries native `id`, unique `public_id`,
status, work type, priority, schedule, current-assignment display projection,
access instructions, timers and evidence policy. It also carries direct
subscriber/project/task/ticket foreign keys and CRM identifiers. The first set
ports; the second stays in Sub-owned link tables or provenance during adoption.

`app/services/work_order_commands.py` is the sole create/assignment owner and
already has useful behavior:

- create is idempotent and changed content conflicts;
- only draft/scheduled are legal initial states;
- project/task binding is validated and immutable;
- assignment is one atomic command and preserves in-progress/paused status;
- unassignment clears only the current projection, retains assignment history,
  and rewinds `dispatched` to `scheduled` without rewinding active execution;
- product-neutral header updates are separate from lifecycle and assignment
  commands, so no generic edit can bypass those owners;
- construction and assignment are covered by owner tests.

Port deltas are required, not optional cleanup. The source imports FastAPI,
accepts `Any`, commits and rolls back from the service, and couples validation
to Sub models. The module accepts concrete commands, raises typed domain errors,
uses the kernel conflict savepoint, and only flushes. Subject validation happens
in the product before the owner is called.

### Execution lifecycle

The parity vocabulary from `app/services/field/transitions.py` is:

```text
states: draft, scheduled, dispatched, in_progress, paused, completed, canceled
events: accept, en_route, arrived, start, pause, hold, resume,
        complete, unable_to_complete
```

The event→state behavior is ported exactly, including the non-obvious rule that
`en_route` from `paused` records evidence but does not resume the job. Start and
resume open a timer; pause, hold, completion and unable-to-complete stop it.

Two source defects are corrected and shadow-measured:

1. a repeated client event currently returns the first event without proving
   the new payload matches; the module delegates at-most-once execution and its
   request fingerprint to the kernel's one idempotency ledger, treating a
   changed replay as conflict rather than adding a second work-order ledger;
2. the source searches for any actor's open timer, so one job's event can close
   time opened against another job; the module refuses the crossing and carries
   a partial unique database index for one open timer per tenant/actor.

### Completion evidence

Sub's generic policy (photo + signature or reason) belongs with execution. Its
fiber/as-built and issued-splice-plan queries do not: those read topology models
and make a Sub product decision. The module snapshots generic requirements on
the work order and stores evidence kind + opaque artifact reference. Sub must
validate its additional topology prerequisites in the same host transaction
before calling the completion owner.

This is also why the package imports neither `dotmac-files` nor an outside-plant
module. Modules are independent. The assembly joins owners through explicit
commands and product-owned link tables.

## CRM duplicate and retirement evidence

CRM's `app/models/workforce.py` has the older `work_orders`,
`work_order_assignments` and `work_order_notes` aggregate. Its
`app/models/dispatch.py` adds skills, technician profiles, shifts, availability,
rules and the assignment queue. `app/services/workforce.py` owns local CRUD,
notifications, project-task mutation and direct pushes into Sub;
`app/services/dispatch.py` owns availability, scoring, ETA and auto-assignment.

That is a parallel operational writer, not a reusable source. Sub's CRM web
retirement ledger still lists work-order list/create/edit/detail/status/assign,
technician and dispatch routes, and states that data/caller cutover, parity,
zero traffic and source deletion remain. Adoption therefore never dual-writes
the new module and CRM. CRM reconciles to Sub's authoritative rows, traffic
moves, and the local tables and writers are deleted after zero-traffic proof.

One CRM behavior must not disappear: `is_technician_available` prevents shift,
availability-block and overlapping-work conflicts. It belongs in the future
dispatch/workforce owner, not in physical execution. Sub must close that known
gap before CRM exits, as the fleet matrix already records.

## Internal crew versus vendor execution — unresolved by design

Sub has two real stacks:

- internal crews: `WorkOrder`, technician profile, assignment queue, field
  events/work logs/materials;
- external vendors: `InstallationProject`, bidding/direct award, quote, proposed
  route, as-built, PO and AP flow — with **no WorkOrder**.

This package ports the first because it is the qualifying tested execution
owner. It does not decide whether `InstallationProject` becomes a generic
execution satellite or whether vendor work later creates a work order while
the project remains a commercial wrapper. The first Sub cutover is therefore
internal-crew work only. Adding vendor columns, vendor conditionals, quote/PO/AP
tables or an InstallationProject alias is forbidden until the owning boundary
is explicitly decided and evidenced.

## First cutover and drift proof

Before authority moves:

1. migrate copied rows into `mod_workorders` in a disposable rehearsal and
   prove exact identity/count/subject-link mapping;
2. replay captured create, assignment and mobile event commands into an
   isolated shadow database while the current Sub owner stays the only writer;
3. compare public id, status, assignment, timestamps, kernel-ledger command fingerprints,
   timer totals and completion verdicts exactly;
4. classify every difference, with the intentional corrections above named in
   advance;
5. cut constructors and transitions together so there is one writer;
6. reconcile, prove zero old-writer traffic, then delete the local execution
   machine. Product adapters for topology, movement, inventory, notifications,
   inbox and ticket timeline remain thin around the new owner.

No production adopter is claimed in this change. The package is
`audit-complete`, not `adopted`.
