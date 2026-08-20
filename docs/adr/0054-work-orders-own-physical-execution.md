# ADR-0054: Work orders own physical execution, not workforce or installation commercials

- Status: Accepted
- Date: 2026-08-18

## Context

Sub is authoritative for operational work orders. CRM retains a retiring
duplicate. The broad fleet inventory groups work orders with technician
profiles, skills, shifts, availability, dispatch, ETA and location, while Sub
also has a separate vendor `InstallationProject` path carrying bidding, quotes,
purchase orders, accounts payable and as-built review.

Treating that inventory row as one module would create a second workforce,
dispatch, inventory, topology and vendor-commercial system. Treating both Sub
execution stacks as already equivalent would promote an unresolved modelling
choice into code.

## Decision

Create the tenant-only `dotmac-work-orders` optional module. It owns:

- work-order identity, product-neutral header mutation and generic execution
  status;
- guarded execution events, with command replay delegated to the kernel's one
  idempotency ledger;
- assignment/unassignment history and the current assignment projection;
- work logs and generic evidence-gated completion;
- notes and evidence metadata with opaque artifact references.

The adopter owns subject link tables and every adjacent decision: why the work
exists, subscriber/project/task/ticket relationships, workforce roster and
eligibility, routing/ETA/location, inventory/material issue, topology/as-built
truth, file bytes, official timeline and customer/inbox/notification
consequences. Those owners call or react to the work-order owner; they do not
write its state directly.

Header mutation, assignment and unassignment are distinct typed commands.
Generic header updates cannot set status or assignment columns, so a product
adapter cannot bypass either owning transition path.

The first candidate adopter is Sub's internal-crew `WorkOrder` path. CRM is a
retirement target only. Vendor `InstallationProject` is not folded into the
module until a later decision explicitly chooses the generic execution root and
defines its commercial wrapper/cutover.

The module owns one tenant plane in `mod_workorders`, with forced RLS on every
table. It has no platform plane because no named control-plane assembly executes
physical work today.

## Consequences

- Sub's source behavior and tests are mandatory parity evidence.
- The port removes HTTP exceptions, untyped payloads, service commits/rollbacks
  and product model imports; the host remains transaction authority.
- Completion requirements are snapshotted on a work order. Product-specific
  prerequisites are validated by the product in the same transaction before
  completion.
- Evidence stores a reference, never file bytes. No sibling module import is
  introduced.
- One actor/open-timer and one active-assignment invariants are database-backed,
  not only in-memory checks.
- Client command ids remain domain evidence, but `dotmac_kernel.idempotency`
  owns the only replay ledger and request fingerprint; the module requires its
  `idempotency_ledger.v1` database effect.
- CRM availability/conflict behavior must move to the future dispatch/workforce
  owner before CRM retirement; it is not copied into this module.
- Cross-application work data synchronizes through versioned APIs/webhooks and
  local observations. No application reads another application's module schema.

## Evidence

- [`work-orders-sources.md`](../inventories/work-orders-sources.md)
- [`dotmac-work-orders/EXTRACTION.toml`](../../packages/dotmac-work-orders/EXTRACTION.toml)
- Sub `docs/designs/WORK_ORDER_IDENTITY_SOT.md`
- Sub `tests/architecture/test_work_order_command_ownership.py`
