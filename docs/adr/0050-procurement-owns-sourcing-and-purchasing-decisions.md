# ADR-0050: Procurement owns sourcing and purchasing decisions

- **Status:** Accepted
- **Date:** 2026-08-19
- **Decision owners:** Procurement domain; product assemblies own adapters and
  consequences
- **Evidence:** [`procurement-sources.md`](../inventories/procurement-sources.md)

## Context

The fleet decomposition historically grouped Inventory and Procurement because
ERP stores both.  That measurement bucket is not an ownership boundary.
Inventory decides stock evidence and valuation; Accounts Payable decides
supplier liabilities and matching; product owners decide why work is needed.
None of those answers which supplier offer was accepted or what the buyer
committed to purchase.

ERP has the only broad production implementation of requisitions, RFQs,
supplier quotations, evaluations and purchase orders.  Sub has a mature but
product-specific installation-project bidding lifecycle.  CRM contains its
predecessor copy.  Academy has educational content only, Backoffice has
composition intent only, and Vendor CP's contracts are seller-side platform
contracts.  The revision-pinned inventory distinguishes these facts.

The extraction also meets two existing decisions:

- ADR-0026: generic Approvals decides approval, never the subject transition;
- ADR-0031: authority moves only in one sealed cutover with replayable evidence.

## Decision

Create `dotmac-procurement` as an optional, tenant-only module with schema
`mod_procurement`, revision prefix `pc` and branch label `procurement`.

V1 is the sole writer of these local decisions:

- purchase-requisition content and lifecycle after product demand enters;
- sourcing method, event lines/criteria, publication window and invitations;
- immutable formally received supplier offers;
- evaluation scores, selected offer and approved award; and
- purchase-order commitment plus a rebuildable line receipt projection.

Every actor, supplier, item, department, project, budget, approval, Inventory
receipt and external source is an opaque bounded reference.  No module table has
a foreign key to a product or sibling module.

### Decision inputs

Budget and approval are facts from their named owners.  Each fact binds an
opaque decision reference to the exact subject id and SHA-256 content digest.
Procurement verifies the binding and performs its own transition; it does not
re-run budget availability or actor-eligibility policy.

Sourcing criteria are fixed when an event is published.  A completed evaluation
must cover the declared criteria exactly, use weights totalling 100, score only
offers received for that event and select one of those offers.  The approved
evaluation is the award decision.

When sourcing names a requisition, its currency and line identities, quantities,
units and item references must cover that approved snapshot exactly once.  Only
one non-cancelled sourcing event may consume a requisition at a time; cancelling
a published event before any bid restores the requisition for a fresh decision.

Purchase-order content is editable only in draft.  Submission freezes its
digest; approval must bind that digest.  Fulfilment arrives as immutable,
deduplicated line-quantity observations from the local receipt owner.
Procurement refuses cumulative quantities above the ordered amount and derives
partial/received state.  It neither creates nor edits stock.

A purchase order repeats the exact requisition line identity and, when awarded,
the selected bid's quantities, sourcing units/items and unit prices.  An
evaluation sourced from a requisition must carry both references, and a tenant
may create only one commitment from either source.  This prevents a valid total
or approval digest from authorizing reshaped or duplicate purchases.

### Explicit exclusions

The module owns no annual budget, threshold/statutory policy, approval policy,
supplier identity or compliance regime, contract administration, Inventory,
Assets, AP invoice/three-way match/payment/journal, product work, numbering,
file/rendering, notification or connector transport state.

Long-form procurement contracts and supplier prequalification remain audited
ERP-adjacent capabilities for a later coherent slice.  They are not smuggled
into V1 as nullable columns or untyped JSON.

### Transaction and delivery boundary

All services receive a caller-owned SQLAlchemy `Session`, lock affected rows,
mutate/flush and never commit or roll back.  Append-only evidence is written in
the same transaction as each state change.  Supplier communication and finance
consequences leave through assembly-owned outbox adapters after the local
transaction; the package imports no template, provider, product or sibling.

## Adoption

ERP is the qualifying source and first cutover.  Organization-to-Tenant mapping,
exact pins, composed migration/RLS evidence, complete backfill, shadow equality,
rollback rehearsal and the ADR-0031 sealed switch are required before any ERP
writer retires.  Publication of an unused package is not extraction.

Backoffice is the next greenfield candidate.  Sub may adopt only through a
separate adapter/cutover that preserves its installation-project lifecycle as
the product owner; CRM's predecessor copy consolidates to Sub and is not an
independent reuse proof.

## Consequences

- Inventory accepts or reports receipt facts without deciding the purchase.
- AP consumes an approved purchase commitment and owns invoices, matching,
  payments, tax and journals; it never advances Procurement state directly.
- Approvals can change eligibility/quorum policy without becoming a purchasing
  writer, while a Procurement state never changes on an unbound approval.
- Sub's routes, build eligibility and project status do not enter the reusable
  schema.
- The source's mutable offers, date-only RFQs, incomplete criteria checks,
  direct invoice writes and inline email/PDF delivery are retired rather than
  ported.
