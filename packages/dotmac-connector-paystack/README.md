# dotmac-connector-paystack

Paystack ingress, reconciliation and refund plugin for the
independently deployed Dotmac Integrator. It verifies `x-paystack-signature`
over the exact request bytes, translates `charge.success` and the refund
outcomes `refund.processed` / `refund.failed` into provider-neutral
`payments.settlement.observation.v1` facts, pages the authenticated
transaction list from an engine-owned checkpoint, and performs the refund
command a product tells it to perform.

It owns no database, product identity, allocation, coverage, receivable state,
retry policy or checkpoint. Provider metadata is retained only in the
Integrator's transport receipt; it is never copied into the product
observation. The published a1 ingress manifest remains accepted during
adoption.

## Refund path

0.1.0a3 is narrowed to refunds. One separately bindable DELIVERY capability,
`payments.refund.v1`, with two commands:

| command | provider call | notes |
|---|---|---|
| `refund` | `POST /refund` | amount optional (omitted = the transaction's own); engine key stamped verbatim into `merchant_note` |
| `find_refund` | `GET /refund?transaction=` | read-only; takes `params.refund_reference`; decides an ambiguous refund by exact match on `merchant_note`; verdict in `Outcome.result` |

Payment intent, saved-authorization charge, payout and customer commands are
not part of this release and their capabilities (`payments.intent.v1`,
`payments.payout.v1`, `payments.customer.v1`) are not declared.

The connector performs the call it is commanded to perform. Whether a refund is
warranted and every ledger consequence stay with the product.

### Refund outcomes (ingress)

`refund.processed` and `refund.failed` become observations with
`observation_kind` `refund` / `refund_failed`, exact `amount` and `currency`,
`provider_status` verbatim, `merchant_reference` (the transaction reference) and
`provider_refund_reference`. When the event carries a `merchant_note` with
exactly the reference shape `^[a-z0-9][a-z0-9-]{15,63}$` (Payments' `rf1-` plus 32 hex qualifies) it is emitted as
`refund_reference`, which is how Payments ties the outcome to its instruction;
without it the observation is still emitted and the consumer parks it unmatched.
`refund.pending` / `refund.processing` and every dispute event stay
record-only. Dispute ingress is deferred.

### Outcomes

| outcome | engine status | meaning |
|---|---|---|
| succeeded | `succeeded` | performed and conclusively confirmed |
| declined | `terminal` | the provider performed it and refused |
| retryable | `retryable` | it demonstrably never reached the provider |
| terminal | `terminal` | rejected before the provider acted |
| ambiguous | `reconciliation_required` | it may have landed; nobody may retry |

**A decline is not a retryable error.** The provider performed the refund and
refused it; repeating the question does not change the answer.

**A timeout after the request was sent is ambiguous, never retried.** The money
may already be on its way back. It is resolved with `find_refund`
(`GET /refund?transaction=...`) and never by sending the refund again.

### Duplicate protection

Paystack publishes **no `Idempotency-Key` header**, and `/refund` accepts no
client reference either, so nothing at the provider refuses a second refund.
The connector stamps the ENGINE's idempotency key (stable across attempts of
one delivery) verbatim into `merchant_note`; a key that is not a reference
(`^[a-z0-9][a-z0-9-]{15,63}$`) is refused before anything is sent. The lookup
is a separate delivery with its own key, so it is given the refund's reference
as `params.refund_reference`. The classification never lets an ambiguous send be retried, and
`find_refund` reads the provider's refund list for the transaction and matches
the note exactly: zero matches = not found as of this read, one = found (id and status as
evidence), more than one = a human decides, and a list that is unreadable or
reports another page decides nothing. A refund's reference is never read from
the payload; only the lookup's subject is.

### Money

An amount crosses the boundary as an exact decimal string in the currency's
major units with an explicit currency code (`{"amount": "1500.00", "currency":
"NGN"}`), which is the shape the ingress side already emits. It is converted to
provider minor units through the single wire-scale owner both directions share,
and refused rather than rounded if it is finer than the provider can carry.
`float` never appears, and a bare integer is refused because `1000` does not say
whether it means naira or kobo.

Paystack's protocol evidence is its official
[webhook documentation](https://paystack.com/docs/payments/webhooks/) and
[API reference](https://paystack.com/docs/api/): the signature is HMAC-SHA512
over the event payload, and amounts use the provider's ×100 wire scale for every
supported currency.
