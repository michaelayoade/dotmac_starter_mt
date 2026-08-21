# dotmac-connector-flutterwave

Ingress-only **Flutterwave v4** plugin for the independently deployed Dotmac
Integrator. It verifies `flutterwave-signature` with HMAC-SHA256 over the exact
request bytes and translates v4 `charge.completed` events into
`payments.settlement.observation.v1` without owning any financial consequence.

There is no API-version switch and no v3 `verif-hash` fallback. Sub's existing
v3 receiver remains legacy product transport until cutover replaces its
callback with this v4 contract.

The first release makes no provider HTTP call and declares an empty external
host set: deny all egress. It owns no database, product identity, allocation,
coverage, receivable state, retry policy or checkpoint. Provider metadata is
retained only in the Integrator's transport receipt; it is never copied into a
product observation.

The v4 protocol evidence is Flutterwave's official
[webhook documentation](https://developer.flutterwave.com/docs/webhooks/).
Flutterwave's v4 webhook does not report a provider fee, so the connector does
not invent one; fee confirmation belongs to the later reconciliation slice.
