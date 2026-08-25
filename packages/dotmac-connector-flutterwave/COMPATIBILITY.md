# Compatibility

`dotmac-connector-flutterwave 0.1.0a2` targets Flutterwave API v4. The working
tree extends it with outbound commands and needs a new version before
publishing, because a published manifest digest must not move.

| Surface | Contract |
|---|---|
| Connector key | `flutterwave` |
| Integration floor | `dotmac-integration >=0.1.0a14` |
| SPI | `>=1.4,<2.0` |
| `payments.settlement.observation.v1` | INGRESS + POLL |
| `payments.intent.v1` | DELIVERY — initialize one payment |
| `payments.refund.v1` | DELIVERY — request one refund against one charge |
| Egress | `developersandbox-api.flutterwave.com`, `f4bexperience.flutterwave.com`, `idp.flutterwave.com` |
| Provider idempotency | v4 `X-Idempotency-Key`, carrying the engine key verbatim |

It requires `webhook_signing_secret` and optionally accepts
`webhook_signing_previous_secret` during rotation. It accepts only
`flutterwave-signature` HMAC-SHA256 over the exact request bytes; v3
`verif-hash` and v3 payload envelopes are unsupported. The v4 OAuth bindings
`api_client_id` / `api_client_secret` are required for reconciliation and for
either outbound capability, and reach exactly the documented identity host.

**Transfers and payouts are not implemented.** No product consumer exists, and
an outbound money-movement command whose first execution is also its first
review does not ship. Verification and refund status likewise stay
reconciliation concerns on the observation capability rather than becoming
commands in a queue that retries.

Both the a1 ingress-only and the a2 ingress+poll manifests stay adoptable.
