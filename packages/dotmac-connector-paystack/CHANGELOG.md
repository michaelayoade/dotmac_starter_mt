# Changelog

## 0.1.0a3 — unreleased

Narrowed to REFUNDS (Michael's decision D5, 2026-11-04). The date is filled in
by the release record, not before it.

Declared as a3, not folded into a2. a2 IS published (peeled tag
`dotmac-connector-paystack-v0.1.0a2` points at commit
`656ecebb05f24c11acda69a069d6fbe60d319f56`), and the refund slice below adds a
DELIVERY capability, per-capability modes and an SPI 1.4 floor — each inside the
manifest digest an installation adopts by. Editing the a2 manifest in place
would make one version name two contracts and leave every binding enabled
against the a2 digest unidentifiable.

- The exact a2 manifest is preserved as `SETTLEMENT_MANIFEST`, so an
  installation pinned to digest
  `73ffecb65880c6b8bf5dec78ae33839f08f44bd4adcab061915727716a906c7a` resolves to
  a known contract. `docs/inventories/released-manifest-digests.json` records
  that digest and `make manifest-digest-check` fails if it ever moves.
- Raises the `dotmac-integration` dependency floor from `>=0.1.0a11` to
  `>=0.1.0a14` (the first release with SPI 1.4; it matches
  `integration_floor` in `.github/release-connectors.json` and the sibling
  connectors).
- Raises the declared SPI floor to `>=1.4,<2.0` so each capability maps to its
  OWN mode: `payments.settlement.observation.v1` in INGRESS and POLL,
  `payments.refund.v1` in DELIVERY only. Without that mapping a settlement
  observer's binding would carry the authority to move money.
- **Release scope, verified against tag `dotmac-connector-paystack-v0.1.0a2`.**
  The outbound slice that was unreleased since a2 (payment intent, saved
  authorization charge, payout and customer commands, nine operations across
  four capabilities) is NOT in this release. It was removed rather than
  disabled: the capability allocation guard forbids an operation no capability
  can reach. Nothing is deprecated, because a2 never carried any of it.
  `payments.intent.v1`, `payments.payout.v1` and `payments.customer.v1` are not
  declared; the public surface keeps `ACTIONS_BY_CAPABILITY`,
  `OUTBOUND_CAPABILITY_IDS`, `OPERATIONS` and `PaystackDeliveryHandler`, now
  holding refund only.
- **Adds `payments.refund.v1`** (DELIVERY) with two commands. `refund` stamps the
  engine's idempotency key VERBATIM into `merchant_note` (Paystack publishes no
  `Idempotency-Key` header and `/refund` accepts no client reference). The key
  must match `^[a-z0-9][a-z0-9-]{15,63}$` and is otherwise refused before
  anything is sent (`idempotency_key_shape`); Payments' `rf1-` plus 32 hex
  instruction reference satisfies it, so one identity runs end to end. The shape
  is fixed once a3 is tagged. **`find_refund`** is new: a read-only
  `GET /refund?transaction=` that takes a required `params.refund_reference`
  (the refund's reference; a lookup is its own delivery with its own key) and
  matches it exactly against `merchant_note`. Zero matches means NOT FOUND AS OF
  THIS READ, one means found (refund id and status as evidence), more than one
  needs a human, and a list that is unreadable or reports another page decides
  nothing. A successful lookup carries `{matches, refund_id?, refund_status?}` in
  `Outcome.result`; `refund` carries none. An AMBIGUOUS refund is decided with
  it and is never re-sent. The `dmi`+SHA-256 derivation is dropped.
- **Adds refund-outcome ingress.** `refund.processed` and `refund.failed` are
  normalized to `payments.settlement.observation.v1` observations with
  `observation_kind` `refund` / `refund_failed`, exact amount and currency,
  verbatim provider status, `merchant_reference` (the transaction reference),
  `provider_refund_reference`, and `refund_reference` when `merchant_note` has
  exactly the reference shape above. This is the provider-sourced path for Payments'
  refund-completed and refund-failed outcomes; an observation with no
  `refund_reference` is still emitted and parks unmatched at the consumer.
  `refund.pending` and `refund.processing` stay record-only. Malformed refund
  events are record-only evidence (`refund_shape_invalid`).
- **Dispute ingress is deferred.** Dispute events remain record-only
  (`event_not_in_ingress_slice`); chargebacks enter through staff confirmation
  until volume says otherwise.
- Classifies every refund attempt as succeeded, declined, retryable, terminal or
  ambiguous, over a total table with an import-time totality guard. **A decline
  is TERMINAL, never retryable. An inconclusive send is AMBIGUOUS**
  (`reconciliation_required`), never retried: a read timeout after a refund may
  already have moved money, and `find_refund` is the only safe answer. Only a
  connect-phase failure, which demonstrably sent nothing, is retryable.
- Reports the provider reference and refund id as evidence on every outcome,
  including the ones where no answer arrived.
- Keeps money exact end to end: amounts cross the boundary as decimal strings
  in major units with an explicit currency, are converted through the single
  wire-scale owner shared with the ingress path, and are refused rather than
  rounded when finer than the provider can carry. `float`, and a bare integer
  whose unit is unstated, are both refused.
- Refuses a refund whose echoed amount or currency differs from what was sent,
  and one with no provider id, as inconclusive rather than guessing. A tolerance
  is a policy, and policy stays with the product.

## 0.1.0a2 — released

Peeled tag `dotmac-connector-paystack-v0.1.0a2` points at commit
`656ecebb05f24c11acda69a069d6fbe60d319f56`. The heading read "unreleased" after
the tag was cut, and the outbound work above was written under it; both are
corrected here rather than left as a claim that the artifact does not exist.

- Adds authenticated paged transaction reconciliation through the engine-owned
  POLL checkpoint and the existing settlement-observation capability.
- Keeps amount/fee subunits exact and provider status verbatim; allocation and
  every financial consequence remain product decisions.

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
