# Changelog

## 0.1.0a1 — Unreleased

- Add the tenant-only domain-service aggregate, immutable command/outcome and
  registrar-observation evidence, desired-state revisions, holds and durable
  attention conditions.
- Publish provider-neutral registrar/DNS capability contracts and a conformance
  fake without naming a provider.
- Keep provider callbacks observational; lifecycle transitions occur only in
  reconciliation, with replay, out-of-order and callback-loss handling.
- Guard transfer-out with an exact-content approval receipt and active-hold
  refusal; reject generic release/allow-lapse consequences in V1.
- Make registration and contact delivery self-contained with closed contact and
  postal-address snapshots, source provenance, an owner-computed digest and
  actual nameservers; make nameserver/DNS delivery exact typed requests too, and
  refuse owner-local intent references and literal secrets.
- Persist immutable, binding-scoped DNS observations with canonical recordset
  digests and DNS drift reconciliation.
- Require renewal to cite the latest recent registrar POLL fact from the active
  binding; remove caller-supplied registrar expiry.
- Defer transfer-in until the shared operation-secret channel exists; no auth
  code or arbitrary secret reference enters Domains evidence or delivery.
- Model terminal registration/transfer failures as typed lifecycle, attention
  and repair evidence; bind holds to owner plus source reference and protect
  service identity with controlled updates.
