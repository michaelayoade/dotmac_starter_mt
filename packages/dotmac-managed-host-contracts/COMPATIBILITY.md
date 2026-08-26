# dotmac-managed-host-contracts compatibility

## Public surface

Only names in `dotmac_managed_host_contracts.__all__` are public.

| Name | Contract |
|---|---|
| `PRODUCT_MANIFEST` | exact owner, release and three versioned capability ids |
| `CAPABILITY_CONTRACTS` | immutable, canonically ordered lifecycle snapshots |
| `CAPABILITY_SCHEMAS` | immutable, canonically ordered exact schema documents |
| `CAPABILITY_COMPOSITIONS` | empty; the suite owner declares cross-owner dataflow |
| `COMPOSITION_DEPENDENCY_CONTRACTS` | empty external-owner verification input |
| `COMPOSITION_DEPENDENCY_SCHEMAS` | empty external-owner verification input |
| `DEPLOYMENT_BUNDLE_LIFECYCLE` | `host.deployment-bundle.lifecycle.v1` |
| `BACKUP_RESTORE_LIFECYCLE` | `host.backup-restore.lifecycle.v1` |
| `HEALTH_PROBE_LIFECYCLE` | `host.health-probe.lifecycle.v1` |
| `__version__` | installed catalogue version |

`catalogue` and `schemas` are implementation modules and are not supported
import paths. The wheel has no connector entry point or `ModuleManifest`.

## Compatibility rule

The `CapabilityContractSnapshot.capability_code` is unversioned.
`schema_version` is appended only for the Product Manifest's public capability
id. Adding a version is additive. Changing an operation, typed bundle action,
field, check, endpoint, schema byte or classification requires a new
capability/schema version. Consumers pin the complete contract and exact schema
digests; they never select the newest implicitly.

## Fixed security and operational meaning

- Bundle deployment, backup/restore and health probes are distinct bindings.
- All three implement exactly `plan`, `apply`, `observe`, and `cancel`.
- Bundle operation version 1 admits only decommission, install, repair, resume,
  rollback, suspend and upgrade.
- Update semantics are only the typed `upgrade` bundle action. There is no
  generic execution request, transport or escape hatch.
- Agent endpoint, identity and held credential reference exist only in typed
  installation configuration, never in signed operation inputs.
- Successful outputs contain public operational evidence and no secret value.

Weakening any commitment is a reviewed new contract version, never an agent or
connector-local setting.
