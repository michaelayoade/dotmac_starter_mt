# dotmac-managed-collaboration-contracts compatibility

## Public surface

Only names in `dotmac_managed_collaboration_contracts.__all__` are public.

| Name | Contract |
|---|---|
| `PRODUCT_MANIFEST` | exact owner, release and versioned public capability ids |
| `CAPABILITY_CONTRACTS` | immutable, canonically ordered lifecycle snapshots |
| `CAPABILITY_SCHEMAS` | immutable, canonically ordered exact schema documents |
| `CAPABILITY_COMPOSITIONS` | empty; the suite owner declares cross-owner dataflow |
| `COMPOSITION_DEPENDENCY_CONTRACTS` | empty external-owner verification input |
| `COMPOSITION_DEPENDENCY_SCHEMAS` | empty external-owner verification input |
| `APPLICATION_LIFECYCLE` | `collaboration.application.lifecycle` at schema version 1 |
| `USER_OIDC_CONFIGURATION_LIFECYCLE` | `collaboration.user-oidc.configuration.lifecycle` at schema version 1 |
| `USER_GROUP_QUOTA_LIFECYCLE` | `collaboration.user-group-quota.lifecycle` at schema version 1 |
| `FILE_ROUNDTRIP_LIFECYCLE` | `collaboration.file-roundtrip.lifecycle` at schema version 1 |
| `__version__` | installed catalogue version |

`catalogue` and `schemas` are implementation modules and are not supported
import paths. The wheel has no connector entry point or `ModuleManifest`.

## Compatibility rule

`CapabilityContractSnapshot.capability_code` is unversioned. Its
`schema_version` is appended only for the public id declared by
`ProductManifestSnapshot`. Adding a capability version is additive. Changing an
operation, action or resource vocabulary, schema field, data classification,
check, endpoint, fixed security value or schema byte requires a new
capability/schema version. Consumers pin complete contract and schema digests
and never select the newest implicitly.

## Fixed security and operational meaning

- Application actions are exactly backup, decommission, ensure-active, restore,
  resume, suspend and upgrade; results expose health and immutable
  backup/version/configuration facts without executing host commands here.
- OIDC maps identity only by immutable issuer plus subject, refuses JIT account
  creation and email linking, requires S256 and audience/azp checks, and keeps
  direct login as break glass.
- Backchannel logout, local session provenance and revocation are required
  observable behavior; this catalogue stores no session.
- User, group membership and quota operations address stable ids and contain no
  email identity field.
- A file roundtrip must write, read, compare the exact digest and clean up its
  bounded public probe.
- Endpoint and secret-reference values exist only in held installation config,
  never in operation request or result schemas.
- All four schema pairs are runtime contracts: plan/apply validate the desired
  step target and observe/cancel receive only schema-declared fields derived
  from that durable target. Outer command/operation/plan pins are not owner
  payload fields.
- Every successful output is public operational evidence, never secret
  material.

Weakening any commitment is a reviewed new contract version, never a
connector-local setting.
