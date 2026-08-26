# dotmac-managed-infrastructure-contracts compatibility

## Public surface

Only names in `dotmac_managed_infrastructure_contracts.__all__` are public.

| Name | Contract |
|---|---|
| `PRODUCT_MANIFEST` | exact owner, release and four versioned capability ids |
| `CAPABILITY_CONTRACTS` | immutable, canonically ordered lifecycle snapshots |
| `CAPABILITY_SCHEMAS` | immutable, canonically ordered exact schema documents |
| `CAPABILITY_COMPOSITIONS` | empty; the suite owner declares cross-owner dataflow |
| `COMPOSITION_DEPENDENCY_CONTRACTS` | empty external-owner verification input |
| `COMPOSITION_DEPENDENCY_SCHEMAS` | empty external-owner verification input |
| `INSTANCE_LIFECYCLE` | `infrastructure.instance.lifecycle.v1` |
| `NETWORK_LIFECYCLE` | `infrastructure.network.lifecycle.v1` |
| `VOLUME_LIFECYCLE` | `infrastructure.volume.lifecycle.v1` |
| `FIREWALL_LIFECYCLE` | `infrastructure.firewall.lifecycle.v1` |
| `__version__` | installed catalogue version |

`catalogue` and `schemas` are implementation modules and are not supported
import paths. The wheel has no connector entry point or `ModuleManifest`.

## Compatibility rule

The `CapabilityContractSnapshot.capability_code` is unversioned.
`schema_version` is appended only for the Product Manifest's public capability
id. Adding a version is additive. Changing an existing operation, resource
shape, field, check, endpoint, schema byte or classification requires a new
capability/schema version. Consumers pin the complete contract and exact schema
digests; they never select the newest implicitly.

## Fixed security and operational meaning

- Instance, network, volume and firewall are distinct bindings.
- All four implement exactly `plan`, `apply`, `observe`, and `cancel`.
- Installation endpoint and held credential reference exist only in typed
  configuration, never in signed operation inputs.
- Operation inputs carry desired state, pins and opaque references only.
- Successful outputs contain public operational evidence and no secret value.
- Provider identity and wire mapping remain connector-owned.

Weakening any commitment is a reviewed new contract version, never a
connector-local setting.
