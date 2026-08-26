# dotmac-managed-collaboration-contracts

Immutable, provider-neutral contracts for a managed collaboration product. The
wheel describes four independently bindable capability families:

- `collaboration.application.lifecycle.v1` — ensure-active, backup, restore,
  upgrade, suspend, resume and decommission transitions with health evidence;
- `collaboration.user-oidc.configuration.lifecycle.v1` — an exact relying-party
  configuration that permits only immutable issuer/subject mapping,
  preprovisioned accounts, S256 PKCE, audience/azp validation, provenance-bound
  sessions and revocation;
- `collaboration.user-group-quota.lifecycle.v1` — user, group membership and
  quota desired state by stable identifiers, never email matching; and
- `collaboration.file-roundtrip.lifecycle.v1` — write, read, digest comparison
  and mandatory cleanup of a bounded non-secret probe through one exact user.

Every family exposes Integration SPI 1.2's `plan`, `apply`, `observe`, and
`cancel` operations with exact canonical JSON Schema bytes. Contract
`capability_code` values are unversioned; each separate `schema_version`
produces the public `.vN` capability id in the Product Manifest.

The application family owns product lifecycle obligations, not host execution.
It states the approved action and the backup, restore, installed-version,
rollback and health evidence a connector must return. It contains no command,
argv, script, executable bytes or provider method. A constrained agent or
administrative API may implement a binding, but cannot change the contract.

The OIDC configuration contract does not perform login or own local sessions.
It makes the required managed-product configuration observable: exact issuer
and subject binding, no just-in-time account creation, no email linking,
backchannel logout, direct-login break glass, PKCE S256 and audience/azp
validation. Local relying-party verification, identity binding and session
revocation remain with their application owners.

Installation endpoints and credential references appear only in typed
`config_fields`. Integration validates and supplies that held configuration
separately; operation request schemas never repeat a config key or carry a
secret reference. Plan and apply validate the desired step target. Observe and
cancel targets are derived from that immutable target and restricted to their
declared input properties; outer command, operation and plan pins remain in the
Integration envelope. Every successful result is validated before only
schema-classified public operational evidence is projected; secret material is
never an output.

Message and share transports are intentionally not smuggled into the file
roundtrip schema. They require separately owned provider-neutral operation
schemas before a later contract version can claim them.

## Published data

- `PRODUCT_MANIFEST` — owner `dotmac-managed-collaboration` and four versioned
  public capability ids.
- `CAPABILITY_CONTRACTS` — immutable, canonically ordered snapshots.
- `CAPABILITY_SCHEMAS` — exact self-contained Draft 2020-12 documents.
- `CAPABILITY_COMPOSITIONS` — empty; cross-owner evidence flow belongs to the
  managed-suite catalogue.
- `COMPOSITION_DEPENDENCY_CONTRACTS` and
  `COMPOSITION_DEPENDENCY_SCHEMAS` — empty for this owner catalogue.
- `APPLICATION_LIFECYCLE`, `USER_OIDC_CONFIGURATION_LIFECYCLE`,
  `USER_GROUP_QUOTA_LIFECYCLE`, and `FILE_ROUNDTRIP_LIFECYCLE` — named aliases
  for the four snapshots.

See `COMPATIBILITY.md` for supported identifiers and `EXTRACTION.toml` for the
product-first inventory ruling.
