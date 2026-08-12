# Entitlement allocation — source audit

**As of:** 2026-08-12
**Source:** `dotmac_vendor_control_plane` @ `eb667fa` — `src/vendor_cp/allocations/`
(404 LOC, 7 files, migration `v005_allocations`, tables `allocations` +
`allocation_entries`)
**Also inventoried:** `dotmac_erp` @ `0f4b1698`, `dotmac_crm` @ `c64b5aa0`,
`dotmac_sub` @ `9f6f9f36`, `dotmac-kernel` @ `0.1.0a45`

Hard rule 24 requires inventorying the products before writing shared behaviour.
This records what that inventory found and, more importantly, the three
couplings the extraction has to cut.

## Qualifying source

The vendor control plane is the only implementation. ERP, CRM and Sub have no
entitlement-allocation family; the 2026-08-12 fleet sweep measured
`entitlement-allocation` at 0/0/0/2. The kernel owns the *data-plane* half —
`entitlements`, `tenant_entitlement_grants`, `EntitlementDecision` — which is
deliberately a different thing: ruling C4 makes the control plane the ALLOCATOR
and the product data plane the only WRITER of its own grants.

So this is a product-first extraction with exactly one candidate source, and no
parity risk from a competing implementation.

## What the unit is

An `Allocation` is an **immutable projection of what an activated contract
version entitles**. `(contract_id, content_hash)` is unique — one allocation per
activated contract version — and re-delivery of the same activation is a no-op.
It is not a grant, not a licence, and not a delivery record.

## The three couplings the extraction must cut

This is the audit's real output. The source is coupled to `vendor_cp.contracts`
in three distinct ways, and each needs a different cut.

| # | Coupling | Where | Why it cannot survive extraction |
|---|---|---|---|
| **C-a** | **Foreign key** `allocations.contract_id → contracts.id` | `models.py`, `v005` | Contracts land in a *different* Starter module (A2, unadjudicated). A cross-module FK couples two independently released migration lineages — ADR-0006 D1 forbids it, and it would make either module un-releasable without the other. |
| **C-b** | **Direct model read** — `session.get(Contract, …)`, `ContractStatus`, `contract.content_hash` | `service.py:100-113` | "Modules are independent of each other" (import-linter). A module may not import another module's models, and this is a read of authoritative state belonging to another owner. |
| **C-c** | **Event-type literal** `"contract.activated"` | `consumer.py:23` | The event vocabulary belongs to whoever owns contracts. A module hard-coding another owner's event type silently makes itself un-reusable by any product whose activation event is spelled differently. |

## The cut: a typed `ContractSnapshot` port

All three resolve the same way — **invert the dependency**. The module stops
reading contract state and instead accepts a frozen, typed snapshot the caller
constructs:

```text
    caller (vendor CP assembly today; the contracts module later)
        │  builds a ContractSnapshot from its own authoritative state
        ▼
    dotmac-entitlement-allocation
        stage_allocation(db, snapshot) -> Allocation
```

- **C-a** becomes `contract_ref: UUID` with **no FK** — provenance, not
  referential integrity. That is what an immutable snapshot already is: it
  freezes what the contract entitled at activation, and it must survive the
  contract row being archived, corrected or moved to another module. The
  existing `content_hash` and `source_event_id` already carry the provenance a
  live FK was standing in for.
- **C-b** becomes the caller's job. The `status == ACTIVE` and
  `content_hash` checks are *contract* invariants, and the source service is
  reading another owner's state to re-verify them. Under the snapshot they are
  proven where the authority lives, and the module validates only what it owns:
  that entries are non-empty, capability codes are declared, quantities are
  positive.
- **C-c** becomes a caller-supplied `source_event_id`, which the source already
  records. The module keeps idempotency; it stops knowing what the event was
  called.

## The finding that changes the sequencing

The module queue put entitlement-allocation *after* commercial-contracts,
because the source "consumes activated contract snapshots and is not actually
independent of contracts". That is true of the **implementation** and false of
the **extraction**:

> Cutting the contract coupling is not a consequence of extracting this module —
> it is the first required step, and it is **A2-neutral**.

Whichever module contracts eventually land in, it is not this one, so the FK and
the model import must go regardless. Once they are a typed port, the module no
longer has an opinion about where contracts live. So this extraction is
**unblocked by A2**, and doing it first has a useful side effect: it produces
the `ContractSnapshot` contract that the A2 adjudication then has to satisfy,
rather than inheriting whatever shape the contracts module happens to ship.

## What stays in the vendor CP

The consumer (`consumer.py`) — a `PlatformDeliveryTransport` binding
`contract.activated` to the module's entry point. That is assembly wiring: it
names one product's event vocabulary and one transport, and it is exactly the
"thin adapter around the owner" ADR-0010 requires. It shrinks rather than moves.

## Open before the module creates a table

- **Capability-code validation.** The source stores `capability_code` as a free
  string. The domain design says allocation lines must be validated against the
  product's manifest-declared capability catalogue, so that "a place where new
  capability codes are invented" cannot be this module. The kernel has
  `capabilities`; whether validation is the module's job or the caller's is the
  one design question this audit does not answer.
- **A2 remains open**, but does not block: see above.
