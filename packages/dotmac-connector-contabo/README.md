# dotmac-connector-contabo

Stateless Contabo provisioning connector for the independently deployed Dotmac
Integrator. Version `0.1.0a1` declares only
`infrastructure.firewall.lifecycle.v1`: the current Contabo API can create,
read, update and delete firewalls and replace their inbound rules, so that is
the only exact owner contract this distribution can presently prove.

The supported subset is explicit. Contabo accepts inbound `tcp`, `udp` and
`icmp` accept rules. An owner-valid egress rule, `any` protocol, or ICMP rule
with destination ports is refused with a stable activation code rather than
translated approximately. The connector uses a hash-derived provider name and
marker to make create retries discoverable without owning a database. Every
mutation uncertainty is `ambiguous`; read-side timeouts are `retryable`.

The connector intentionally does not declare instance, private-network,
block-volume, or authoritative-DNS lifecycle. `activation_gate_for()` exposes a
named gate tied to each exact local owner contract:

- instance creation needs an approved provider `imageId`/`productId` mapping;
- Contabo allocates private-network CIDRs and has no desired resolver input;
- the current API has object storage and instance disk add-ons, not the
  attachable block-volume lifecycle;
- Contabo DNS records do not retain the owner `requirement_kind`/`resource_ref`,
  while the exact observe result also requires DNS/TLS evidence absent from the
  provider API. A stateless connector therefore cannot reconstruct it later.

Those are activation gates, not an invitation to add provider fields to owner
schemas. A later provider-specific mapping or safe public-observation seam must
remain connector-owned and must clear the named gate with conformance evidence.

`api_secret_ref` resolves to an already-held JSON object with exactly
`client_id`, `client_secret`, `username`, and `password`. The connector uses the
official OAuth password-grant endpoint but never logs or returns those values.
Its real transport accepts only `https://api.contabo.com`, refuses redirects and
environment proxies, bounds connect/read/write/pool time and response size, and
permits only the firewall path family.

This first release depends on unpublished `dotmac-kernel 0.1.0a69`,
`dotmac-integration 0.1.0a6`, `dotmac-managed-infrastructure-contracts
0.1.0a1`, and `dotmac-domains-contracts 0.1.0a1`. It must remain outside the
connector release lane until that dependency train is published and Observer
CI has run the package's RED sensitivities and installed-wheel conformance.
