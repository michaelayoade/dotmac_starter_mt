# dotmac-managed-suite-contracts compatibility

## Public surface

Only top-level names in `dotmac_managed_suite_contracts.__all__` are public:

| Name | Contract |
|---|---|
| `PRODUCT_MANIFEST` | exact suite owner and package version; declares no product capability |
| `CAPABILITY_CONTRACTS` | empty; component owners publish operation contracts |
| `CAPABILITY_SCHEMAS` | empty; component owners publish exact schema bytes |
| `CAPABILITY_COMPOSITIONS` | canonical tuple of suite-owned compositions |
| `COMPOSITION_DEPENDENCY_CONTRACTS` | exact externally owned contracts needed to verify the compositions |
| `COMPOSITION_DEPENDENCY_SCHEMAS` | exact externally owned schemas needed to verify the compositions |
| `COLLABORATION_FEDERATION` | identity client evidence into collaboration OIDC configuration |
| `EMAIL_APPLICATION_DEPENDENCIES` | application-success evidence into explicitly selected email resource instances |
| `EMAIL_DNS` | managed-email public DNS requirements into authoritative DNS |
| `EMAIL_FEDERATION` | identity client evidence into managed-email OIDC configuration |
| `IDENTITY_FEDERATION` | `managed-suite.identity-federation.v1` |
| `IDENTITY_ACCOUNT_FEDERATION` | identity user issuer/subject evidence into selected collaboration users |
| `__version__` | installed catalogue version |

## Compatibility rule

Changing either endpoint of an evidence binding, a schema reference/digest, a
pointer, instance selector, coverage axis, classification, operation,
requirement flag, or composition meaning
requires a new composition version. Consumers bind the exact composition
identity and digest; they do not select the newest document implicitly.

The identity, email, collaboration and domains catalogues are exact dependencies
rather than compatible ranges because this artifact pins their exact schema
bytes. Any dependency schema change requires a reviewed suite release even if
its product contract change is otherwise additive.

Future cross-owner mappings are additive only when all referenced owner
catalogues are already released and the composition cross-check passes against
their exact held contract and schema documents.
