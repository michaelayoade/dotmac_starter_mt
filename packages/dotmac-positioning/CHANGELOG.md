# Changelog

## 0.2.0a2 — unreleased

- Add neutral observation retention holds (`po_0002_observation_holds`,
  additive; released `po_0001` bytes are unchanged). A hold pins one
  observation against the retention sweep on behalf of an opaque holder
  reference: `place_observation_hold`, `release_observation_hold` and
  `list_observation_holds`. A hold carries no subject, address, decision or
  business meaning; the holder owns what its reference means.
- `prune_observations` never deletes a held observation, and the restricting
  foreign key means no cascade or direct delete can either. Releasing the last
  hold on an observation makes it prunable again. Holds do not extend what may
  be collected: ingest still requires an active collection grant and the
  caller's `ObservationPolicy`.
- Backward compatible: no existing signature, column or constraint changes. A
  deployment on `0.2.0a1` upgrades by running `po_0002` and can roll the
  application back without rolling the schema back.

## 0.2.0a1 — unreleased

- Replace domain computation with a private batched Rust extension behind the existing Python API.
- Ship a platform-specific CPython wheel and a complete locked native source archive.
- Keep authorization, persistence, transactions and audit with their existing Python owners.


## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `75a26ab` by release run
`32460582571`. Publication is supply-chain evidence only; it composes no
product and moves no authority.

- Extract the product-neutral position-observation and current-projection
  contract from the corrected Dotmac Sub reference implementation.
- Add tenant-scoped tracked units, source assignments, bounded collection
  grants, immutable observations, current/trail reads, retention, and neutral
  geofence entry/exit facts.
- Keep transactions, subject links, policy defaults, business consequences,
  transports, and presentation in the adopting product.
- Require explicit tenant and operational inputs at the public surface, and
  make concurrent replay of identical collection-grant and geofence identities
  converge inside the caller's transaction without leaking integrity failures.
