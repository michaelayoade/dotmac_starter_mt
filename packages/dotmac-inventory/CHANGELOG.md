# Changelog

## 0.1.0a1 — UNRELEASED

- Adds the tenant-only `mod_inventory` lineage.
- Adds SKU, warehouse, stock-balance, movement, reservation, lot, serial, and
  valuation persistence.
- Adds flush-only receipt, issue, transfer, adjustment, reservation,
  reconciliation, and valuation writers plus pure
  WAC/FIFO/lower-of-cost-and-NRV calculations.
- Exposes `versions_dir()` as the public installed-lineage locator.
