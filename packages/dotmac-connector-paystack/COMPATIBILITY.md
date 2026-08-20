# Compatibility

`dotmac-connector-paystack 0.1.0a1` implements dotmac-integration SPI
`>=1.3,<2.0`, connector key `paystack`, capability
`payments.settlement.observation.v1`, and INGRESS mode only.

It declares required `webhook_signing_secret` and optional
`webhook_signing_previous_secret` bindings. Its external host set is empty,
which means deny all provider egress for this slice.

