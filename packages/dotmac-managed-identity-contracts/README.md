# dotmac-managed-identity-contracts

Immutable, provider-neutral contracts for the managed identity service. The
wheel describes three independently bindable lifecycle families:

- `identity.realm.lifecycle.v1`; and
- `identity.oidc-client.lifecycle.v1`; and
- `identity.user.lifecycle.v1`.

Both expose the Integrator SPI 1.2 operation vocabulary: `plan`, `apply`,
`observe`, and `cancel`. Every operation is bound to exact canonical JSON
Schema bytes and their SHA-256 digests.

This package does not administer an identity provider. It has no network
client, connector entry point, persistence, migration, secret value, retry
engine, provider branch, or runtime configuration. A provider connector
implements these contracts; Vendor CP selects exact released contract and
schema evidence; Integrator holds configuration and executes an approved plan.

## Security contract

The realm contract requires a private administrative endpoint and makes the
public issuer, discovery document, JWKS endpoint, and RS256 signing policy
observable. The confidential-client contract fixes Authorization Code flow,
PKCE S256, RS256 ID tokens, exact HTTPS redirect URIs, and audience/authorized-
party validation. Those controls are declarations and schema constants rather
than connector defaults that a provider implementation may silently weaken.
The user contract locates an identity only through a stable Dotmac-owned
reference, treats email and names as mutable attributes, and returns the exact
public issuer plus provider subject. Disabling an identity requires provider
session revocation; it never assigns product roles or links by email.
Enrollment is an explicit stable revision with an exact HTTPS return URI,
client id and bounded lifetime. A connector may deliver a provider-owned
one-time enrollment action for that revision, but no password or action token
is part of the contract.
Mutable email, login and display-name observations are validated against the
owner schema but are deliberately not classified as public evidence, so they
cannot be projected into fleet receipts or suite composition mappings.

Administrative and client credential inputs are `secret_reference` values.
They name material already held by Integrator; they are never the secret bytes.
No result schema can return a password, private key, client secret, or secret
reference. `client_secret_configured` is a boolean observation only.

## Published data

The top-level package exports:

- `PRODUCT_MANIFEST` — one `ProductManifestSnapshot` owned by
  `dotmac-managed-identity`;
- `CAPABILITY_CONTRACTS` — the canonically ordered contract snapshots;
- `CAPABILITY_SCHEMAS` — exact, self-contained JSON Schema documents; and
- `CAPABILITY_COMPOSITIONS` — the standard catalogue surface, empty because
  cross-capability dataflow is owned by the managed-suite catalogue; and
- `COMPOSITION_DEPENDENCY_CONTRACTS` and
  `COMPOSITION_DEPENDENCY_SCHEMAS` — empty because this owner catalogue does
  not publish a composition that depends on another owner; and
- named aliases for each lifecycle snapshot.

See `COMPATIBILITY.md` for the supported surface and `EXTRACTION.toml` for the
Rule-24 inventory ruling.
