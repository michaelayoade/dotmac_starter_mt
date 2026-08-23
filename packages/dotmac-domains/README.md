# dotmac-domains

Provider-neutral, tenant-only owner of Dotmac's registered-domain lifecycle.

It owns registration, transfer, renewal, expiry/redemption interpretation,
desired contact/nameserver/DNS intent, registrar observations, reconciliation,
holds and guarded transfer-out. Generic release/allow-lapse is refused in V1;
provider deletion is observational after expiry/redemption. It does not own pricing, payment, dunning, hosting,
provider credentials, provider I/O, `TenantDomain`, TLS or ingress.

Provider effects leave through the kernel outbox and are translated by the
Dotmac Cloud assembly to Integrator capability `domains.registrar.v1` or
`dns.authoritative.v1`. Provider callbacks are immutable observations; only
the lifecycle reconciler may change Dotmac service state.

Registration and contact commands carry closed, immutable contact snapshots
with source provenance, plus actual nameservers. They never carry a Domains
row reference that the independently deployed Integrator would have to resolve.
The snapshot digest is computed by Domains from canonical content, not accepted
from a caller. Nameserver and DNS deliveries likewise carry their exact typed
values, never an intent/service row id. Contact snapshots contain necessary
personal data in Domains' immutable intent/command evidence and kernel outbox,
and in Integrator delivery attempts, so Domains and its consuming assembly need
an approved retention/redaction design before external-customer adoption.
Registrar renewals consume a named, recent POLL observation from the active
binding; caller-supplied expiry truth is refused. DNS observations persist the
actual canonical recordsets and nameservers, are binding-scoped and immutable,
and participate in drift reconciliation.

Transfer-in is deferred from V1 until the shared Integrator per-operation secret
channel exists. No auth code, arbitrary secret reference or Domains-local row
reference may enter command evidence or an outbox payload. Guarded transfer-out
approval and cancellation remain in V1.

Cloud adoption remains blocked on four assembly contracts, not local
workarounds: verified paid coverage for renewal, Fulfillment's published
participant contract, Integrator's per-operation secret channel before
transfer-in can return, and approved retention/redaction for contact PII across
Domains evidence/outbox and Integrator delivery attempts.
