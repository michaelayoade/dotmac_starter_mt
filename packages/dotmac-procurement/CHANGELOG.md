# dotmac-procurement changelog

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32479852085`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Tenant-only requisition, sourcing, bid, evaluation/award and purchase-order
  lifecycle extracted product-first from ERP.
- Exact Money totals, aware sourcing windows, immutable submitted offers and
  complete weighted-criteria evaluation.
- Digest-bound budget and approval facts without importing the Approvals owner.
- Deduplicated receipt observations, bounded cumulative quantities and a
  rebuildable, batch-atomic purchase-order fulfilment projection without
  Inventory writes.
- Exact requisition-to-sourcing-to-award-to-order line binding, one purchase
  commitment per requisition/award source, and bid-free sourcing cancellation
  that safely releases the requisition.
- Append-only transition evidence, caller-owned transactions and forced RLS.
