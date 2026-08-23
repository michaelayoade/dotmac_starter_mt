# Changelog

## 0.1.0a1 — Unreleased

- Add the tenant-only domain-service aggregate, immutable command/outcome and
  registrar-observation evidence, desired-state revisions, holds and durable
  attention conditions.
- Publish provider-neutral registrar/DNS capability contracts and a conformance
  fake without naming a provider.
- Keep provider callbacks observational; lifecycle transitions occur only in
  reconciliation, with replay, out-of-order and callback-loss handling.
- Guard release and transfer-out with an exact-content approval receipt and
  active-hold refusal.
