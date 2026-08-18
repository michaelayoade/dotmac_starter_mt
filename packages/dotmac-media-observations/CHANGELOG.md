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
