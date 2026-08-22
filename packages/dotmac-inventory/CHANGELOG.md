# Changelog

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `bfc112fc` by release run
`32481134625`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Adds the tenant-only `mod_inventory` lineage.
- Adds SKU, warehouse, stock-balance, movement, reservation, lot, serial, and
  valuation persistence.
- Adds flush-only receipt, issue, transfer, adjustment, reservation,
  reconciliation, and valuation writers plus pure
  WAC/FIFO/lower-of-cost-and-NRV calculations.
- Exposes `versions_dir()` as the public installed-lineage locator.
