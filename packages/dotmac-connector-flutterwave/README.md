# dotmac-connector-flutterwave

Ingress, reconciliation and outbound-command **Flutterwave v4** plugin for the
independently deployed Dotmac Integrator. It verifies `flutterwave-signature` with HMAC-SHA256 over the exact
request bytes and translates v4 `charge.completed` events into
`payments.settlement.observation.v1` without owning any financial consequence.

There is no API-version switch and no v3 `verif-hash` fallback. Sub's existing
v3 receiver remains legacy product transport until cutover replaces its
callback with this v4 contract.

Version 0.1.0a2 adds OAuth-authenticated paged charge reconciliation against
the exact documented v4 identity/sandbox/live hosts. It owns no database, product identity, allocation,
coverage, receivable state, retry policy or checkpoint. Provider metadata is
retained only in the Integrator's transport receipt; it is never copied into a
product observation. The a1 ingress manifest remains accepted during adoption.

The v4 protocol evidence is Flutterwave's official
[webhook documentation](https://developer.flutterwave.com/docs/webhooks/) and
[charge-list API](https://developer.flutterwave.com/reference/charges_list).
Flutterwave's v4 webhook does not report a provider fee, so the connector does
not invent one; products may decide only from provider evidence they actually receive.

## Outbound commands

`payments.intent.v1` initializes one payment; `payments.refund.v1` requests one
refund against one v4 charge. Both are DELIVERY capabilities: the engine owns
the queue, the claim, the retries and the persistence, and this package performs
provider I/O and classifies the answer.

**Money is exact.** Amounts are `Decimal` from the payload to the wire bytes,
written as a JSON number by a hand-written encoder — `json.dumps` cannot
serialize a `Decimal` without routing it through binary floating point. A
floating-point amount is refused, and an amount finer than the currency's
exponent is refused rather than rounded: a transport that rounds money has made
a financial decision. The currency and its minor-unit exponent are required on
every command, with no default chain and no ISO-4217 table inside the transport
— the product supplies the exponent from the kernel's `Money`/`Currency`.

**Idempotency** rides Flutterwave v4's own `X-Idempotency-Key` header, carrying
the engine's key verbatim. The connector mints nothing and stores nothing.

**Outcomes.** A decline — a 4xx refusal or a 2xx carrying a declined status — is
terminal and never retried; repeating the identical request cannot change the
answer, and a retry on a payment command is how a customer is charged twice. A
timeout after the request was sent is `RECONCILIATION_REQUIRED`, because the
charge may exist. Only a connect-phase failure, where nothing was ever sent, is
retryable. The provider reference is captured as evidence on every outcome,
declines included.

**Not implemented, deliberately:** transfers and payouts. No product consumer
exists for them.
