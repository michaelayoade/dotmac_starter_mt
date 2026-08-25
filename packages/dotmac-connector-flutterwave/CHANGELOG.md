# Changelog

## 0.1.0a3 — unreleased

Declared as a3, not folded into a2. a2 IS published (peeled tag
`dotmac-connector-flutterwave-v0.1.0a2` points at commit
`656ecebb05f24c11acda69a069d6fbe60d319f56`), and the outbound slice below adds
two capability ids, per-capability modes and an SPI 1.4 floor — every one of
them inside the manifest digest an installation adopts by. Until this bump the
plugin carried TWO manifests both named `0.1.0a2`, which is worse than an
in-place edit: `accepts_manifest_digest` accepted both, so nothing could see the
collision.

- Adds two API-v4 DELIVERY capabilities: `payments.intent.v1` (initialize one
  payment) and `payments.refund.v1` (request one refund against one v4 charge).
  Verification and refund status stay reconciliation concerns on the existing
  observation capability and are deliberately not converted into commands.
- **Transfers and payouts are deliberately absent.** No product consumer exists,
  and an outbound money-movement command whose first execution is also its first
  review does not ship. A test, not a comment, keeps adding one visible.
- Still v4 only, on both legs: v4 OAuth `client_credentials`, the same three
  already-declared v4 hosts, and no v3 fallback anywhere.
- Money is exact `Decimal` from the payload to the wire bytes. A hand-written
  encoder writes it as a JSON number, because `json.dumps` cannot serialize a
  `Decimal` without routing it through binary floating point. A binary
  floating-point amount is refused, and an amount finer than the currency's
  exponent is refused rather than rounded.
- The currency and its minor-unit exponent are required on every command, with
  no default chain and no ISO-4217 table inside the transport.
- The engine's idempotency key is carried to the provider's own
  `X-Idempotency-Key` header verbatim; the connector mints and stores nothing.
- Five typed outcomes over the engine's four statuses: a decline is TERMINAL and
  never retryable; a timeout after the request was sent is
  RECONCILIATION_REQUIRED, never a retry; only a connect-phase failure is
  retryable. The provider reference is captured as evidence on every outcome,
  declines included.
- The published SPI-1.3 ingress+poll manifest is retained as
  `INGRESS_POLL_MANIFEST`, so an installation pinned to digest
  `4933c0f1782875d625daac6f704f99ea3a4fe4d1ccffa234512c2da0bc1ea1b6` resolves to
  a known contract. `docs/inventories/released-manifest-digests.json` records
  that digest and `make manifest-digest-check` fails if it ever moves.

## 0.1.0a2 — released

Peeled tag `dotmac-connector-flutterwave-v0.1.0a2` points at commit
`656ecebb05f24c11acda69a069d6fbe60d319f56`.

- Adds API-v4-only OAuth authentication and paged charge reconciliation.
- Uses only the documented sandbox/live v4 hosts and retains the a1 ingress
  manifest as the bounded adoption window; there is no v3 fallback.

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
