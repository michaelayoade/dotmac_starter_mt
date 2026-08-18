# Changelog

## 0.1.0a1 — Unreleased

- Add the tenant-only `mod_orders` lineage.
- Add immutable accepted line snapshots with exact price, terms, and
  specification provenance.
- Add finite coverage-obligation receipts and replayable fulfillment request
  publication through the kernel outbox.
- Add exact derived-total and complete-FX checks plus deferred PostgreSQL
  guards that refuse unfrozen or internally inconsistent committed snapshots.
- Fingerprint the normalized full commercial snapshot, not only the line set;
  validate actor, instant, source, FX and coverage evidence before replay.
- Publish typed snapshot, finite-coverage, fulfillment-state and official
  timeline readers without exposing ORM rows.
- Make lifecycle and fulfillment acceptance evidence final once recorded, and
  remove online `DELETE` authority from every Orders table.
- Record cancellation refusal after fulfillment acceptance as an idempotent
  domain result, timeline event, and unsuccessful audit fact in the same
  transaction.
- Declare `tenant_audit_log.v1` alongside tenant scope, idempotency and outbox
  prerequisites; importing the package remains database-configuration-free.
