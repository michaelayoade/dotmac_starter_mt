# dotmac-managed-identity-contracts compatibility

## Public surface

Only names in `dotmac_managed_identity_contracts.__all__` are public:

| Name | Contract |
|---|---|
| `PRODUCT_MANIFEST` | exact owner, version and declared capability codes |
| `CAPABILITY_CONTRACTS` | immutable, canonically ordered lifecycle snapshots |
| `CAPABILITY_SCHEMAS` | immutable, canonically ordered exact schema documents |
| `CAPABILITY_COMPOSITIONS` | empty; the suite owner declares cross-capability dataflow |
| `COMPOSITION_DEPENDENCY_CONTRACTS` | empty external-owner verification input |
| `COMPOSITION_DEPENDENCY_SCHEMAS` | empty external-owner verification input |
| `REALM_LIFECYCLE` | `identity.realm.lifecycle.v1`, schema version 1 |
| `OIDC_CLIENT_LIFECYCLE` | `identity.oidc-client.lifecycle.v1`, schema version 1 |
| `USER_LIFECYCLE` | `identity.user.lifecycle.v1`, schema version 1 |
| `__version__` | installed catalogue version |

`catalogue` and `schemas` are implementation modules and are not supported
import paths. This wheel has no connector entry point and no installable
`ModuleManifest`.

## Compatibility rule

An additive package release may add a separately versioned capability or schema
document. Changing an existing operation, field, check, endpoint requirement,
schema byte, data classification, or fixed security value requires a new
capability/schema version. Consumers bind the complete identity and digest;
they never accept the newest document implicitly.

The kernel dependency is a grammar floor only. This catalogue does not use the
kernel database, identity tables, sessions, web framework, or authorization
logic.

## Fixed security meaning

The following are compatibility commitments, not deployment settings:

- administrative access is private and HTTPS;
- issuer, discovery and JWKS endpoints are HTTPS;
- confidential clients use Authorization Code and PKCE S256;
- ID tokens use RS256 and consumers validate audience plus authorized party;
- redirect URIs are exact HTTPS values;
- users are correlated only by a stable owner reference and expose exact
  public issuer/subject evidence; email is never an identity key;
- disabling a user revokes its provider sessions;
- credential inputs are held secret references; and
- output documents never carry secret values or secret references.

Weakening one of these commitments is a new contract version and a reviewed
migration, never a connector-local compatibility choice.
