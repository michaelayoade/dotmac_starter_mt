# Changelog

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
