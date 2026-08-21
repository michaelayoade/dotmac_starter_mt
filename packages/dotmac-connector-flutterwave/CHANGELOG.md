# Changelog

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `401f0006` by release run
`32473143050`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Add an ingress-only Flutterwave connector for
  `payments.settlement.observation.v1`.
- Verify the Flutterwave v4 `flutterwave-signature` with HMAC-SHA256 over the
  exact request bytes; v3 authentication and payloads are deliberately absent.
- Translate successful and failed v4 `charge.completed` events without
  importing product identity, financial policy, persistence, retry or provider
  I/O; do not invent a provider fee absent from the v4 webhook.
