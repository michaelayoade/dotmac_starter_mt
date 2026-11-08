# Changelog — dotmac-service-access-policy

## 0.1.0a2 — 2026-11-03

Fresh allocation (pyproject, `__version__` and manifest agree); published
`0.1.0a1` and `sap_0001` remain immutable. Kernel floor unchanged (`>=0.1.0a91`).

- **`RecordAccessInputV2`** = V1 fields plus `source_version` (non-negative
  integer, source-assigned, per tenant + service + signal) and `policy_version`
  (non-empty, at most 60 characters). `observed_at` must be timezone-aware.
- **`record_access_input_v2`** refuses stale input instead of overwriting.
  Ordering rule: strictly larger `source_version` wins; timestamps never order.
  Equal version with an identical payload `(active, source_reference,
  observed_at, policy_version)` is an idempotent no-op; equal version with a
  different payload raises `AccessInputRefused("ACCESS_INPUT_VERSION_CONFLICT")`;
  a lower version raises `AccessInputRefused("ACCESS_INPUT_STALE")`. A row
  written by V1 (NULL versions) is older than any versioned input.
- **Pure public helper** `decide_access_input` / `decide_unversioned_write` /
  `StoredAccessStamp` (module `versioning`, no database import) so a non-SAP
  consumer applies the identical rule.
- Concurrent writers for one key are serialized (`INSERT .. ON CONFLICT DO
  NOTHING` then `SELECT .. FOR UPDATE`); the rule always runs against the latest
  committed row. No `db.rollback()`, no savepoint, flush-only as before.
- **V1 behaviour change (fail closed):** `record_access_input` (V1) still
  overwrites an unversioned key, but now raises
  `AccessInputRefused("ACCESS_INPUT_UNVERSIONED_WRITE")` for a key that has been
  written through V2, so an unversioned writer cannot bypass the stale rule.
- Migration `sap_0002_input_versions`: adds nullable `source_version` (bigint)
  and `policy_version` (varchar 60) to `service_access_inputs` plus a check that
  the pair is set together and non-negative. No backfill; existing rows stay
  NULL. Downgrade drops the columns and the check.
- No change to `resolve_desired_access`, `desired_access_decisions` or RLS.

## 0.1.0a1 — 2026-08-20

- Extracts Sub's per-service access-policy decision boundary.
- Separates source observations from the desired enforcement state.
- Creates two directly tenant-scoped, forced-RLS tables.
