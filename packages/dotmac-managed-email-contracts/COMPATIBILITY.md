# dotmac-managed-email-contracts compatibility

## Public surface

Only names in `dotmac_managed_email_contracts.__all__` are public.

| Name | Contract |
|---|---|
| `PRODUCT_MANIFEST` | exact owner, release and versioned public capability ids |
| `CAPABILITY_CONTRACTS` | immutable, canonically ordered lifecycle snapshots |
| `CAPABILITY_SCHEMAS` | immutable, canonically ordered exact schema documents |
| `CAPABILITY_COMPOSITIONS` | empty; the suite owner declares cross-owner dataflow |
| `COMPOSITION_DEPENDENCY_CONTRACTS` | empty external-owner verification input |
| `COMPOSITION_DEPENDENCY_SCHEMAS` | empty external-owner verification input |
| `EMAIL_LIFECYCLE` | `email.lifecycle` at schema version 1; public id `email.lifecycle.v1` |
| `__version__` | installed catalogue version |

`catalogue` and `schemas` are implementation modules and are not supported
import paths. The wheel has no connector entry point or `ModuleManifest`.

## Compatibility rule

The `CapabilityContractSnapshot.capability_code` is unversioned. Its
`schema_version` is appended only when constructing the public capability id
declared by `ProductManifestSnapshot`. A schema or meaning change therefore
increments `schema_version`; it never embeds a second version in the contract
code.

Adding a new capability version is additive. Changing an existing operation,
resource kind, field, check, endpoint, schema byte, data classification or
fixed safety value requires a new capability/schema version. Consumers pin the
complete contract and schema digests and never select the newest implicitly.

## Fixed security and operational meaning

- Application/domain/mailbox/alias/quota/delivery/app-password/DKIM form one
  lifecycle binding.
- Backup/restore and update remain capabilities of the managed-host owner and
  are not duplicated here.
- Only provider-administration material is supplied through held installation
  configuration; operation inputs contain no config field or secret-shaped key.
- Mailbox/app-password operations express disable/revoke state without carrying
  their material, and DKIM exposes only provider-generated public DNS evidence.
- Successful output is public operational evidence, never secret material.

Weakening any commitment is a reviewed new contract version, never a
connector-local setting.
