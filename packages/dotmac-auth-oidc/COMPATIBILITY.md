# dotmac-auth-oidc — compatibility and public surface

The authoritative, machine-readable manifest is
`src/dotmac_auth_oidc/__init__.py`: `__version__`, `SUPPORTED_MODULES`,
`INTERNAL_MODULES` and the curated `__all__`. This document is its prose
companion. The governance test is
`tests/architecture/test_auth_oidc_public_surface.py`.

## What is public

A name is public if it is **either** in the curated top-level `__all__`, **or**
in the `__all__` of a module listed in `SUPPORTED_MODULES`. Everything else is
private and may change or disappear without a deprecation cycle.

## Supported modules and their public names

| Module | Public names |
|---|---|
| `dotmac_auth_oidc.client` | `OIDCClient`, `RelyingPartyConfig`, `VerifiedSubject`, `AuthorizationRedirect`, `ALLOWED_ALGORITHMS`, `DEFAULT_LEEWAY_SECONDS`, `DEFAULT_SCOPES` |
| `dotmac_auth_oidc.state` | `StateStore`, `InMemoryStateStore`, `PerRequestStateStore`, `PER_REQUEST_STATE_STORE`, `LoginState`, `PKCEPair`, `generate_pkce`, `generate_state_id`, `claim_state`, `DEFAULT_STATE_TTL_SECONDS` |
| `dotmac_auth_oidc.discovery` | `ProviderCache`, `ProviderMetadata`, `fetch_metadata`, `discovery_url`, and the three TTL defaults |
| `dotmac_auth_oidc.transport` | `Transport`, `HttpxTransport`, `DEFAULT_TIMEOUT_SECONDS`, `MAX_RESPONSE_BYTES` |
| `dotmac_auth_oidc.errors` | `OIDCError` and every subclass |

## The contract boundary

This package returns a `VerifiedSubject` and nothing else. It never:

- queries a local identity table, or creates an account;
- issues a session, a token or a cookie;
- reads a provider `roles`, `groups`, `scope` or organization claim as an
  authorization input;
- branches on a provider's name.

Asserted by `test_the_package_holds_no_local_identity_or_session_concern` and
`test_no_provider_name_appears_in_the_package`.

## Wire and security constants that are NOT configurable

Deliberate. Each is a fleet-wide invariant, and making it a constructor argument
would turn it into a per-deployment mistake.

| Constant | Value | Why it is fixed |
|---|---|---|
| `ALLOWED_ALGORITHMS` | asymmetric only (RS/ES/PS) | a relying party that accepts `HS256` can be defeated by signing with the provider's published public key as the HMAC secret |
| PKCE method | `S256` | `plain` sends the verifier in the authorization request, which is the interception PKCE exists to defeat |
| the `state` parameter | a random opaque id | the ceremony (verifier, nonce, return path) is held server-side and never serialized, so the front channel has nothing to read and nothing to encrypt |
| endpoint scheme | `https` only | an `http` token endpoint carries the client secret in clear; an `http` JWKS means the key set that decides identity is whatever the network says |

## What the CONSUMER owns

Three things this package will not do for you, each because doing it would
require knowledge the package does not have:

1. **`return_to` validation** — it is carried opaquely through the ceremony.
   Only your application knows which paths are safe.
2. **A `StateStore`** — required, shared across every callback-serving
   process, with an ATOMIC `take` (Redis `GETDEL`, `DELETE ... RETURNING`).
   `InMemoryStateStore` is tests and single-worker development only.
   Hold it for the life of the client, or supply it per ceremony operation —
   see "Where the store lives" below.
3. **The client secret** — resolved by the product and held, never fetched on a
   request path (ADR-0009).

### Where the store lives

A store may be held by the client for the life of the process, or supplied per
ceremony operation:

```python
client = OIDCClient(config, state_store=redis_store)          # held
client.start_login(return_to="/")

client = OIDCClient(config, state_store=PER_REQUEST_STATE_STORE)   # per request
client.start_login(return_to="/", state_store=store_for_this_request)
```

The second form exists for stores backed by the consumer's own database. Such a
store is bound to one request's transaction, and the consumer's framework — not
this package — decides when that transaction opens and commits. A client that
held the session would be a second transaction authority: the ceremony would
commit at a different moment from everything else the request did, and a
rolled-back request would leave a live ceremony behind.

The client is still built ONCE either way, because it owns the `ProviderCache`.
Rebuilding it per request would refetch discovery and JWKS on every sign-in and
lose the `kid`-rotation refresh with them.

`PER_REQUEST_STATE_STORE` is a positive declaration rather than an absence: it
says the consumer supplies a store per call, so omitting one is a
`ConfigurationError` at the call rather than a login that silently loses its
PKCE verifier. It implements neither `put` nor `take`, so it cannot be mistaken
for a store that happens to hold nothing.

There is no way to run without a store. The store holds the PKCE verifier, so
`take` IS how the callback recovers it — replay protection is not a feature to
opt into, it is a consequence of where the ceremony lives. An earlier revision
made the store optional and signed the ceremony into the state parameter
instead; that put the verifier on the wire, readable by anything that saw the
URL, and was withdrawn.

## Dependencies

Two, and each is a concern that cannot be faked: `pyjwt[crypto]` for signature
verification, `httpx` for discovery and JWKS. The httpx range spans the fleet's
two existing pins (0.27 in ERP/CRM/Sub, 0.28 in Academy) so adopting this
package never forces an httpx bump; and because `Transport` is injectable, a
consumer supplying its own client does not pay for the default at all.

Deliberately NOT `python-jose`. The objection is not that the project is
abandoned — it still publishes releases — but that ERP, CRM and Sub all pin
`3.3.0` from 2021, which is what a drop-in port would have inherited, and which
pulls the pure-Python `ecdsa`.

**The `>=2.13` floor is a security floor, not a preference.** PyJWT's advisory
GHSA-jq35-7prp-9v3f reports an algorithm allow-list bypass affecting versions
through 2.12.1 — the exact control this package leans on hardest. It must not be
lowered to widen compatibility.

## What is machine-checked, and what is not

**Checked:** the public surface and version sync; that no forbidden import
reaches the package (no kernel, no assembly, no ORM, no web framework); that no
provider name appears; that no local-identity or session concern appears; the
algorithm allowlist rejecting `HS256`, `none` and an unknown `alg`; `kid`
handling; nonce mismatch; audience, issuer, expiry and `azp`; state single-use and
expiry; the state parameter carrying no ceremony data; discovery issuer
mismatch; and the https rule on both discovered endpoints and the override.

**Not claimed:** this package has no production deployment anywhere. Nothing has
run a real login through it against a real identity provider. `EXTRACTION.toml`
records `status = "audit-complete"` and `contract_consumers = []`, which is the
honest state — a passing test suite is not a pilot.

## Versioning and deprecation

Pre-1.0 the surface is settling; a `0.MINOR` bump may break, and every break is
named in `CHANGELOG.md`. After 1.0: MAJOR for a breaking change to a public name
or to a wire constant above, MINOR for additive surface, PATCH for fixes. A
private name carries no guarantee at any version.
