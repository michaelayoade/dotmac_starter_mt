# Changelog — dotmac-media-observations

## 0.1.0a1 — UNRELEASED

### Added

- Tenant-only immutable node/metric declarations, observation envelopes,
  transport receipt provenance, entity/hierarchy facts, canonical metric
  periods and exactly typed metric facts.
- Exact replay, changed-fingerprint conflict and append-only provider
  restatement semantics.
- Deterministic current entity, hierarchy and period-metric projections with
  orphan/cycle and projection drift reporting.
- Exact money with currency, minor-unit scale and integer minor-unit
  provenance; integral counts; decimal duration/ratio separation.
- Reconciliation preview/apply evidence and normalized analytics facts that
  label provider conversion claims without assigning attribution.
- Complete read/emission provenance with fingerprint, correction ancestry and
  timestamped transport receipts; kind-matched analytics payloads preserve full
  normalized entity, hierarchy and metric content.
- Typed invalid/unsupported/conflict rejection reports, strict receipt replay
  conflicts and lossless exact-Decimal configuration-state round trips.
- Pre-persistence signed-64-bit and `NUMERIC(38,18)` bounds so normalized metric
  values are refused instead of being rounded or overflowing in storage.
- Provider-free normalized-observation conformance kit.

### Fixed

- Every public recording path now validates that a restatement retains its
  installation, source system and entity, hierarchy-child or metric-period
  subject; callers cannot bypass that boundary by setting the link directly.
- Reused observation identities report changed-fingerprint conflicts before
  changed content is interpreted against the declaration registry.
- Period-metric read windows refuse naive or reversed instants under the same
  aware `[start,end)` contract as stored metric periods.
- Aggregate entity properties refuse singular and plural person/audience keys,
  local Lead/opportunity/Party/customer/subscriber/Quote/Order identifiers,
  attribution claims and authoritative revenue labels at the typed boundary.
- The package architecture guard now allowlists domain imports and detects
  planted provider SDKs, network clients, embedded endpoints and
  provider-specific source conditionals.
