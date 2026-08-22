# dotmac-connector-flutterwave

Ingress and reconciliation **Flutterwave v4** plugin for the independently deployed Dotmac
Integrator. It verifies `flutterwave-signature` with HMAC-SHA256 over the exact
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
