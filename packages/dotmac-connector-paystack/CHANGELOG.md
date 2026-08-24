# Changelog

## 0.1.0a2 — unreleased

- Adds authenticated paged transaction reconciliation through the engine-owned
  POLL checkpoint and the existing settlement-observation capability.
- Keeps amount/fee subunits exact and provider status verbatim; allocation and
  every financial consequence remain product decisions.
- Adds OUTBOUND provider I/O in DELIVERY mode: nine provider-neutral commands
  (initialize payment, charge a saved authorization, refund, resolve a bank
  account, create a transfer recipient, initiate a transfer, and customer
  create/update/read) across four separately bindable capabilities —
  `payments.intent.v1`, `payments.refund.v1`, `payments.payout.v1` and
  `payments.customer.v1`. A binding may only issue the commands its own
  capability names.
- Raises the declared SPI floor to `>=1.4,<2.0` so each capability maps to its
  OWN mode. Without that mapping a settlement observer's binding would carry
  the authority to move money, and conformance would call the ingress factory
  for a delivery-only contract.
- Classifies every attempt as succeeded, declined, retryable, terminal or
  ambiguous, over a total table with an import-time totality guard. **A decline
  is TERMINAL, never retryable** — Paystack burns the reference on a decline,
  so an identical retry is refused and a fresh attempt is a product decision.
  **An inconclusive send is AMBIGUOUS** (`reconciliation_required`), never
  retried: a read timeout after a charge or a transfer may already have moved
  money, and reconciling it against `transaction/verify`, `transfer/verify` or
  the refund list is the only safe answer. Only a connect-phase failure, which
  demonstrably sent nothing, is retryable.
- Carries the engine's idempotency key through to Paystack's own duplicate
  protection. Paystack publishes NO `Idempotency-Key` header; its mechanism is
  the client-supplied unique `reference` on `transaction/initialize`,
  `transaction/charge_authorization` and `transfer`, which the connector
  derives deterministically from the key so every attempt of one delivery
  presents the same reference and the provider refuses the second. `/refund`
  accepts no reference, so the derived key is stamped into `merchant_note`
  where a reconciler can match it.
- Reports the provider reference and transaction id as evidence on every
  outcome, including the ones where no answer arrived.
- Keeps money exact end to end: amounts cross the boundary as decimal strings
  in major units with an explicit currency, are converted through the single
  wire-scale owner shared with the ingress path, and are refused rather than
  rounded when finer than the provider can carry. `float`, and a bare integer
  whose unit is unstated, are both refused.
- Refuses a success whose echoed amount or currency differs from what was sent,
  and an unrecognised provider status token, as inconclusive rather than
  guessing. A tolerance is a policy, and policy stays with the product.

## 0.1.0a1 — 2026-08-20

Published, installed back from the private index, conformance-checked and
tagged from exact main SHA `deedb35` by release run `32368987719`.

- Verify Paystack HMAC-SHA512 signatures over exact request bytes with a bounded
  previous-secret rotation slot.
- Translate `charge.success` into exact amount, fee, currency and raw provider
  status observations without product metadata or financial consequences.
- Keep unsupported and malformed verified events as record-only transport
  evidence.
- Declare ingress-only execution and deny all external egress.
