# Changelog — dotmac-auth-oidc

All notable changes to the `dotmac-auth-oidc` distribution. This package follows
[Semantic Versioning](https://semver.org); see `COMPATIBILITY.md` for the
public-surface stability policy. Pre-1.0 (`0.x`, incl. this alpha) the surface is
still settling — a `0.MINOR` bump may carry breaking changes, each called out
here.

## 0.1.0a2 — 2026-08-27 (unreleased)

Additive server-side support for an ID token issued to a public native client.

### Added

- `PublicNativeClientConfig`, `NonceBinding` and `NativeIDTokenVerifier`. The
  backend supplies its pinned issuer, client id, assertion-age policy, ID token
  and a trusted plaintext-or-SHA-256 nonce binding. The result is the same
  `VerifiedSubject` boundary; raw nonce storage is not required.
- An RS256-only native policy with exact issuer and client-id-derived audience,
  multi-audience `azp`,
  `exp`/`iat`/optional `nbf`, maximum-age and constant-time nonce checks.
- A static HTTPS JWKS URI option for deployments that deliberately pin it,
  while discovery remains the default.

### Changed

- Confidential and public-native surfaces now share one private PyJWT security
  core and the existing `ProviderCache`; there is no second transport, cache or
  token decoder.
- Unknown-key refresh is bounded by the last attempt, not the last success. A
  failed refresh cannot be amplified once per hostile token and does not erase
  still-fresh working keys.

### Boundary

This verifier is backend-only. It adds no client secret, code exchange, cookie
or state store to the native flow, and it is never shipped to Flutter. Local
identity resolution, authorization, ceremony state and session issuance remain
with the adopting product.

## 0.1.0a1 — 2026-08-14

First release. An OIDC relying party that ends at a verified `(issuer, subject)`.

### Added

- `OIDCClient` — `start_login` and `complete_login`, returning `VerifiedSubject`.
- `RelyingPartyConfig` — one configured provider registration, carrying the
  local `provider_binding` name so a consumer with two providers cannot confuse
  which one completed a ceremony.
- `StateStore` / `InMemoryStateStore` / `claim_state` / `generate_state_id` /
  `generate_pkce` — the ceremony held server-side behind a random opaque state
  id, single-use by construction, and S256 PKCE. There is no state signer and no
  signing key: nothing is serialized onto the wire to sign.
- `PER_REQUEST_STATE_STORE` / `PerRequestStateStore`, and a `state_store`
  argument on `start_login`/`complete_login` — a store bound to one request's
  transaction can be supplied per ceremony operation, while the client (and so
  the `ProviderCache`) is still built once. The declaration keeps "no store at
  all" a construction-time error. See COMPATIBILITY.md § "Where the store
  lives".
- `ProviderCache` — discovery and JWKS with TTLs and a rate-limited forced
  refetch for an unknown `kid`.
- `Transport` / `HttpxTransport` — the one place the package touches a network,
  injectable so the security tests exercise real validation rather than
  monkeypatching it out.
- The `OIDCError` taxonomy, where the subclass name is the stable reason code.

### Boundary

No local table, no session, no cookie, no account creation, no external
authorization, no provider-specific branch. Stateless: no namespace allocation
(hard rule 14).

### Source

`greenfield-after-inventory`, not `product-first`. ERP's is the fleet's only OIDC
implementation and does not qualify as a source: its signature and claim
validation are monkeypatched out of every existing test, which fails rule 24's
"tested" half on repository evidence alone. Its production adoption is
separately UNVERIFIED — the repo cannot prove absence, and the host has not been
read. See
`EXTRACTION.toml` and `docs/inventories/external-identity-sources.md` § D4.

### Not released

Not on `.github/release-modules.json`, and not on `.github/release-adapters.json`
either. The second file is the lane this package's SHAPE needs — it has no
`db_schema`, `manifest_attr` or `kernel_floor` for the module lane to assert —
and it is deliberately EMPTY. The lane is built; the door is shut.

Absence is the safety mechanism until a consumer and a pilot exist (the
precedent ADR-0026 § 8 set), and nothing adopts this yet. Until then the pilot
runs against a local wheel built at an exact SHA — see "Testing against an
unpublished wheel" in `README.md`, which is how `dotmac_workspace` consumes this
code without a cross-repository path dependency and without relaxing a version
pin.
