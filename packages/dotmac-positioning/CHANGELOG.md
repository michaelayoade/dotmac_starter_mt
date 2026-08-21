# Changelog

## 0.1.0a1 — 2026-08-21

Published, registry-verified and tagged from exact protected-main revision
`75a26ab` by release run `32460582571`.

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
