# Compatibility

`dotmac-connector-meta-social 0.1.0a1` implements dotmac-integration SPI
`>=1.3,<2.0`, connector key `meta_social`, capability
`messaging.receive.v1`, and INGRESS mode only.

It declares `webhook_signing_secret`, optional
`webhook_signing_previous_secret`, and `webhook_verify_token`. Its external
host set is empty, which means deny all provider egress for this slice.
