# dotmac-publishing changelog

## 0.1.0a1 — UNRELEASED

Initial product-first tenant publication owner extracted from Mkt scheduling,
per-target delivery and partial-success behavior.

### Added

- immutable digest-addressed publication snapshots and distinct opaque targets;
- retry-safe per-target attempt history with transactional outbox commands;
- deduplicated normalized delivery observations and one aggregate reconciler;
- typed timer scheduling/acceptance/cancellation seam with stale-generation
  refusal;
- independent `pb` migration lineage with four forced-RLS tenant tables; and
- source dossier, retirement ledger, lifecycle/service parity and PostgreSQL
  isolation canaries.

### Corrected from source

- explicit `partial` rather than reporting any-success as global publication;
- retained all-failed evidence rather than losing it through caller rollback;
- provider transport after commit rather than direct API calls in a transaction;
- immutable attempts/observations instead of overwriting one delivery row; and
- cancellation/evidence retention rather than hard-deleting publication facts.
