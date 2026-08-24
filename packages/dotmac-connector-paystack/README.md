# dotmac-connector-paystack

Paystack ingress, reconciliation and outbound-command plugin for the
independently deployed Dotmac Integrator. It verifies `x-paystack-signature`
over the exact request bytes, translates `charge.success` into a
provider-neutral `payments.settlement.observation.v1` fact, pages the
authenticated transaction list from an engine-owned checkpoint, and performs
the outbound commands a product tells it to perform.

It owns no database, product identity, allocation, coverage, receivable state,
retry policy or checkpoint. Provider metadata is retained only in the
Integrator's transport receipt; it is never copied into the product
observation. The published a1 ingress manifest remains accepted during
adoption.

## Outbound commands

Nine provider-neutral operations across four separately bindable DELIVERY
capabilities. The split is a security property: an installation bound for
payment intents cannot be made to issue a refund or a payout by a crafted
payload.

| capability | commands |
|---|---|
| `payments.intent.v1` | `initialize_payment`, `charge_authorization` |
| `payments.refund.v1` | `refund` |
| `payments.payout.v1` | `resolve_bank_account`, `create_transfer_recipient`, `initiate_transfer` |
| `payments.customer.v1` | `create_customer`, `update_customer`, `read_customer` |

The connector performs the call it is commanded to perform. Whether a refund is
warranted, which invoice a payment covers, how much to allocate, and every
ledger consequence stay with the product.

### Outcomes

| outcome | engine status | meaning |
|---|---|---|
| succeeded | `succeeded` | performed and conclusively confirmed |
| declined | `terminal` | the provider performed it and refused |
| retryable | `retryable` | it demonstrably never reached the provider |
| terminal | `terminal` | rejected before the provider acted |
| ambiguous | `reconciliation_required` | it may have landed; nobody may retry |

**A decline is not a retryable error.** Paystack burns the reference on a
decline, so an identical retry is refused; a fresh attempt is a product
decision, which is exactly how `dotmac_sub`'s autopay tracks it.

**A timeout after the request was sent is ambiguous, never retried.** The money
may already have moved. It is resolved against provider state —
`GET /transaction/verify/{reference}`, `GET /transfer/verify/{reference}`, or
`GET /refund?transaction=...` — which is what `dotmac_erp`'s
`_recover_transfer_initiation` and `dotmac_sub`'s `_recover_charge` do in
production today.

### Duplicate protection

Paystack publishes **no `Idempotency-Key` header**. Its mechanism is the
client-supplied unique `reference` on `transaction/initialize`,
`transaction/charge_authorization` and `transfer`; reusing one is refused
server-side ("Duplicate Transaction Reference", `duplicate_transfer_reference`).

The connector derives that reference deterministically from the ENGINE's
idempotency key, which is stable across attempts of one delivery. Two
independent protections therefore stack: the classification never lets an
ambiguous send be retried, and if something retries anyway the provider refuses
the duplicate. The reference is never read from the payload — a guard whose
value arrives in a mutable command is a guard a caller defeats by accident.

`/refund` accepts no client reference. The derived key is stamped into
`merchant_note` instead, so an ambiguous refund is decidable by listing the
provider's refunds for that transaction rather than by guessing — the mechanism
`dotmac_sub` already runs.

### Money

An amount crosses the boundary as an exact decimal string in the currency's
major units with an explicit currency code (`{"amount": "5000.00", "currency":
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
