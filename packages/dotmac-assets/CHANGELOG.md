# Changelog — dotmac-assets

## 0.1.0a1 — UNRELEASED

- Adds the tenant-only `mod_assets` lineage for durable asset identity,
  assignments, maintenance, disposal, and append-only lifecycle evidence.
- Ports ERP's fixed-asset and vehicle lifecycle behavior behind typed,
  product-neutral commands with expected-state and relationship guards.
- Separates physical lifecycle from depreciation/revaluation/GL, inventory
  parts, positioning observations, and vehicle-specific operations.
- Prevents concurrent active custody, self-approval of disposal, disposal with
  unresolved custody or maintenance, and mutation of lifecycle history.
