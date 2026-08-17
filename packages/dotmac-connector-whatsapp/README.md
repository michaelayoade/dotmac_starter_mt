# dotmac-connector-whatsapp

Ingress-only Meta WhatsApp Cloud API connector for the independently deployed
Dotmac Integrator. It verifies subscription handshakes and webhook signatures
over the exact received bytes, then emits provider-neutral observations through
`dotmac-integration` SPI 1.2.

The package owns no persistence, retries, checkpoints, destination selection,
or product decisions. Configuration names ordered material slots; secret values
are resolved and held by the Integrator and never stored here.
