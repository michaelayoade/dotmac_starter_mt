# dotmac-connector-paystack

Paystack ingress and reconciliation plugin for the independently deployed Dotmac Integrator.
It verifies `x-paystack-signature` over the exact request bytes and translates
`charge.success` into a provider-neutral
`payments.settlement.observation.v1` fact.

Version 0.1.0a2 adds read-only paged transaction reconciliation against the
exact `api.paystack.co` host. It owns no database, product identity,
allocation, coverage, receivable state, retry policy or checkpoint. Provider
metadata is retained only in the Integrator's transport receipt; it is never
copied into the product observation. The published a1 ingress manifest remains
accepted during adoption; money-moving intent and refund commands stay separate.

Paystack's protocol evidence is its official
[webhook documentation](https://paystack.com/docs/payments/webhooks/) and
[API currency contract](https://paystack.com/docs/api/): the signature is
HMAC-SHA512 over the event payload, and amounts use the provider's ×100 wire
scale for every supported currency.
