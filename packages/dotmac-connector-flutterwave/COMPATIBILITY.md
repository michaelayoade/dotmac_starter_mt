# Compatibility

`dotmac-connector-flutterwave 0.1.0a1` targets Flutterwave API v4 and implements
dotmac-integration SPI `>=1.3,<2.0`, connector key `flutterwave`, capability
`payments.settlement.observation.v1`, and INGRESS mode only.

It requires `webhook_signing_secret` and optionally accepts
`webhook_signing_previous_secret` during rotation. It accepts only
`flutterwave-signature` HMAC-SHA256 over the exact request bytes; v3
`verif-hash` and v3 payload envelopes are unsupported. The external host set is
empty, which means deny all provider egress for this slice.
