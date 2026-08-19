# dotmac-connector-whatsapp

Ingress-only Meta WhatsApp Cloud API connector for the independently deployed
Dotmac Integrator. It verifies subscription handshakes and webhook signatures
over the exact received bytes, then emits provider-neutral observations through
`dotmac-integration` SPI 1.3.

The package owns no persistence, retries, checkpoints, destination selection,
or product decisions. Its manifest declares the exact logical secret bindings
it reads: a required primary signing secret, an optional previous signing
secret for bounded rotation, and a required subscription verify token. It
declares explicit deny-all provider egress because ingress verification and
normalization perform no network I/O. Secret values are resolved and held by
the Integrator and never stored here.
