# dotmac-connector-whatsapp

Meta WhatsApp Cloud API connector for the independently deployed Dotmac
Integrator. It verifies subscription handshakes and webhook signatures over the
exact received bytes, emits provider-neutral observations, and delivers
product-decided text, template and media commands through `dotmac-integration`
SPI 1.4.

The package owns no persistence, retries, checkpoints, destination selection,
or product decisions. Its manifest declares the exact logical secret bindings
it reads: a required primary signing secret, an optional previous signing
secret for bounded rotation, and a required subscription verify token. It
declares a conditionally required Graph access-token binding and exact egress to
`graph.facebook.com`; ingress verification and normalization themselves remain
network-free. Secret values are resolved and held by the Integrator and never
stored here. The connector does not decide customer-window eligibility,
template choice, conversation state or retry policy.
