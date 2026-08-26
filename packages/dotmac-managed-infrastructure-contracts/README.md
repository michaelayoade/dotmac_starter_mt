# dotmac-managed-infrastructure-contracts

Immutable, provider-neutral desired-state contracts for managed infrastructure.
The wheel publishes four independently bindable capability families:

- `infrastructure.instance.lifecycle.v1`
- `infrastructure.network.lifecycle.v1`
- `infrastructure.volume.lifecycle.v1`
- `infrastructure.firewall.lifecycle.v1`

Every family exposes Integration SPI 1.2's `plan`, `apply`, `observe`, and
`cancel` operations with exact canonical Draft 2020-12 JSON Schema bytes. The
contract code inside each `CapabilityContractSnapshot` is unversioned;
`schema_version` produces the public `.v1` capability id declared by the
Product Manifest.

Each resource has its own binding because compute, networks, storage and
firewall policy have independent replacement and failure boundaries. Their
schemas exchange typed desired state, exact artifact/configuration digests,
and opaque stable references. Provider resource identifiers and
observations are public operational evidence, never authority for a product's
business lifecycle.

Installation account, region, administrative endpoint and held credential
reference are typed `config_fields`. They are not repeated in signed operation
inputs. This package contains no connector, provider branch, network client,
persistence, migration, retry engine or secret material.

## Published data

- `PRODUCT_MANIFEST` — owner `dotmac-managed-infrastructure` and four public
  capability ids.
- `CAPABILITY_CONTRACTS` — immutable, canonically ordered snapshots.
- `CAPABILITY_SCHEMAS` — exact self-contained schema documents.
- `CAPABILITY_COMPOSITIONS` — empty; suite composition belongs to its owner.
- `COMPOSITION_DEPENDENCY_CONTRACTS` and
  `COMPOSITION_DEPENDENCY_SCHEMAS` — empty for this owner catalogue.
- `INSTANCE_LIFECYCLE`, `NETWORK_LIFECYCLE`, `VOLUME_LIFECYCLE`, and
  `FIREWALL_LIFECYCLE` — named lifecycle snapshots.

See `COMPATIBILITY.md` for the fixed meanings and `EXTRACTION.toml` for the
product-first inventory ruling.
