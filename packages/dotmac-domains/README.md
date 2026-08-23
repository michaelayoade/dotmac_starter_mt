# dotmac-domains

Provider-neutral, tenant-only owner of Dotmac's registered-domain lifecycle.

It owns registration, transfer, renewal, expiry/redemption interpretation,
desired contact/nameserver/DNS intent, registrar observations, reconciliation,
holds and guarded release. It does not own pricing, payment, dunning, hosting,
provider credentials, provider I/O, `TenantDomain`, TLS or ingress.

Provider effects leave through the kernel outbox and are translated by the
Dotmac Cloud assembly to Integrator capability `domains.registrar.v1` or
`dns.authoritative.v1`. Provider callbacks are immutable observations; only
the lifecycle reconciler may change Dotmac service state.
