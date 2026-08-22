# Compatibility

`dotmac-connector-paystack 0.1.0a2` implements dotmac-integration SPI
`>=1.3,<2.0`, connector key `paystack`, capability
`payments.settlement.observation.v1`, in INGRESS and POLL modes.

It declares required `webhook_signing_secret` and optional
`webhook_signing_previous_secret` bindings plus optional `api_secret_key` for
polling. Egress is exactly `api.paystack.co`; the a1 manifest stays adoptable.
