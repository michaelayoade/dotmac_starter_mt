# Contabo provider inventory

Inventory date: 2026-08-17. Authority: the current official
[Contabo API reference](https://api.contabo.com/) and the accepted local
ADR-0033/ADR-0034 owner boundary. No product contract was changed by this
inventory.

## Authentication and transport

The official introduction requires four credential fields (client ID, client
secret, API user and API password), exchanges them with `grant_type=password`
at the fixed Contabo realm token endpoint, then calls
`https://api.contabo.com/v1/**` with bearer authorization and a required UUID
`x-request-id`. The connector holds the four fields as one exact, non-rendered
secret document. It accepts only the official API origin and its firewall path
family; redirects, environment proxies, URL userinfo/query/fragment/path, large
responses and unbounded time are refused.

## Exact owner-contract reconciliation

| Owner capability | Official provider evidence | Decision / activation gate |
| --- | --- | --- |
| `infrastructure.firewall.lifecycle.v1` | `POST/GET/PATCH/PUT/DELETE /v1/firewalls`; rules PUT explicitly supports only inbound rules; provider rules have `tcp`/`udp`/`icmp`, ports and source CIDRs | Declared. Accept only the exact intersection: inbound accept rules with those three protocols. Refuse egress, `any`, ICMP ports and foreign provider shapes. |
| `infrastructure.instance.lifecycle.v1` | Create requires provider `imageId`, `productId`, region and optional provider secret IDs; reads expose provider image/product/status/IPs | Not declared: `instance_product_mapping_unavailable`. The owner has artifact/instance-type identities but no approved connector-owned mapping from those identities to provider IDs, and no secret ID is an owner operation field. |
| `infrastructure.network.lifecycle.v1` | Private-network create accepts region, name and description; CIDR is allocated and returned by Contabo | Not declared: `network_cidr_control_unavailable`. The owner desired document requires caller-selected CIDR and resolver addresses, neither accepted by the provider create contract. |
| `infrastructure.volume.lifecycle.v1` | The reference exposes object-storage purchase/resize/cancel and instance-local extra-storage add-ons; no attachable block-volume family is documented | Not declared: `block_volume_api_unavailable`. Object storage is not an attachable volume and is not substituted. |
| `dns.authoritative.v1` | Zone CRUD and record CRUD exist for A/AAAA/CAA/CNAME/MX/SRV/TXT; PTR is a separate IP-address API. Zone payloads contain zone/customer/tenant data, not assigned nameservers or TLS observations. Provider records retain no Dotmac `resource_ref` or owner `requirement_kind`. | Not declared: `dns_observation_state_unavailable`. Apply-time record data cannot reconstruct the exact later OBSERVE output from only the stateless observe target, and the provider API cannot prove TLS/DNS observation fields. |

The DNS refusal is not caused by missing CRUD. It is caused by the exact
stateless observe boundary. Encoding owner metadata as synthetic TXT records,
putting desired state into the provider operation reference, returning empty
TLS proof, or adding Contabo fields to the owner schemas would each invent a
second contract and is forbidden.

## Activation work that may clear a gate

- Instance: publish an independently reviewed connector-owned mapping from
  stable owner artifact/type identities to current Contabo image/product IDs,
  with drift and retirement evidence. Secret IDs remain held provider config.
- Network: only a provider API that accepts the owner CIDR/resolver desired
  state, or a new owner version explicitly modeling provider allocation, can
  clear the gate. The connector cannot reinterpret v1.
- Volume: a documented attach/detach block-volume API matching the owner
  lifecycle is required.
- DNS: a safe, bounded public DNS/TLS observation seam plus durable,
  provider-visible ownership metadata that does not alter DNS semantics is
  required. Until then no DNS binding may activate through this distribution.
