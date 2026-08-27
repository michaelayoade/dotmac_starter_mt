# dotmac-auth-oidc

An OIDC protocol adapter with two server-side entry points. `OIDCClient` runs a
confidential web Authorization Code flow with PKCE; `NativeIDTokenVerifier`
verifies the ID token a public native client obtained with its own PKCE flow.
Both return a **verified external subject** — an `(issuer, subject)` pair — and
stop there.

```
dotmac-auth-oidc                 →  verified (issuer, subject)
dotmac_kernel.external_identity  →  which local Party is that, here?
the product's identity facet     →  issue ITS OWN session
```

Those three steps have three owners on purpose. This package is the first one.

## What it does not do

It queries no local table, creates no account, mints no session, sets no cookie,
and never reads a provider's role, group or scope claim as authorization. There
is no provider-specific branch — no Keycloak, Entra, Google or Auth0 code path —
and adding one is the wrong fix for a provider quirk (ADR-0024).

It is **stateless**: no rows, so no `short_code`, no `migration_prefix` and no
namespace allocation (hard rule 14). Its only state is a rebuildable in-process
discovery/JWKS cache.

## Why it is not a port of ERP's client

ERP has the fleet's only real OIDC implementation, and the 2026-08-14 audit
(`docs/inventories/external-identity-sources.md` § D4) found it does not qualify
as an extraction source: its two security-critical functions are monkeypatched
out of every one of its tests, so rule 24's "tested" half fails on repository
evidence alone. Its production adoption is separately **unverified** —
`OIDC_ENABLED` defaults false and no deployment artifact sets it, but the live
value lives on the ERP host, which has not been read. The design and the written
contract port with confidence; the crypto and HTTP internals do not port as
trusted code.

Six measured defects in that source are fixed here, each with a test that fails
if the behaviour regresses:

| ERP | Here |
|---|---|
| discovery + JWKS refetched every login | cached with TTLs, plus a rate-limited forced refetch for an unknown `kid` |
| no clock-skew tolerance | `leeway`, default 60s |
| no `azp` check when `aud` is a list | refused unless `azp` is this client |
| state valid for its whole TTL, replayable | single use is structural: claiming the state IS how the verifier is recovered |
| state signed with the host's **session-JWT secret** | no key at all — the ceremony never leaves the server |
| `python-jose` pinned at 3.3.0 since 2021 (pulls `ecdsa`) | `pyjwt[crypto] >=2.13` (2.13 is a security floor — GHSA-jq35-7prp-9v3f) |

## Confidential web relying party

```python
from dotmac_auth_oidc import OIDCClient, RelyingPartyConfig

client = OIDCClient(
    RelyingPartyConfig(
        provider_binding="corp-idp",      # YOUR local name for this registration
        issuer="https://idp.example.com",
        client_id=...,
        client_secret=...,                # resolved by YOU at startup (ADR-0009)
        redirect_uri="https://app.example.com/auth/callback",
    ),
    state_store=...,                      # REQUIRED, shared, atomic — see below
)

redirect = client.start_login(return_to="/dashboard")
# store `redirect.state` in an HttpOnly cookie, then 302 to `redirect.url`

subject = client.complete_login(
    code=..., state_parameter=..., stored_state=...
)
# subject.issuer, subject.subject → dotmac_kernel.external_identity
# subject.return_to came from YOUR stored state — still validate it
```

## Public-native backend verification

The mobile application performs Authorization Code + PKCE in the OS browser.
Its backend receives the resulting ID token plus the backend-owned binding to
its ceremony nonce and verifies them without a client secret or a second code
exchange:

```python
from dotmac_auth_oidc import (
    NativeIDTokenVerifier,
    NonceBinding,
    PublicNativeClientConfig,
)

verifier = NativeIDTokenVerifier(
    PublicNativeClientConfig(
        issuer="https://idp.example.com/realms/mobile",
        client_id="io.example.field",
        max_token_age_seconds=300,
    )
)

subject = verifier.verify(
    id_token,
    nonce_binding=NonceBinding.from_sha256_hex(ceremony_nonce_hash),
)
# Resolve subject.issuer + subject.subject through YOUR local identity owner,
# then issue YOUR product session. Provider claims grant no local permission.
```

Construct one verifier per registration and retain it for the process lifetime;
that is what retains the bounded discovery/JWKS cache. `RS256` is the fixed
native-client policy. `aud` is derived from and must contain the exact client
id; `exp`, `iat`, optional `nbf`, exact issuer, multi-audience `azp`, nonce and
maximum assertion age are all enforced.

`NonceBinding.from_sha256_hex(...)` lets a ceremony persist only
`sha256(raw_nonce)`: the package hashes the verified claim and constant-time
compares it to that binding. A caller that still holds plaintext may use
`NonceBinding.from_plaintext(...)`; the object immediately hashes it and never
retains the raw value.

This verifier runs on the backend. It is not a Flutter library and carries no
OAuth client credential, token exchange, cookie or state store. The device
still owns its PKCE verifier, and the product backend still owns its ceremony,
subject-to-local-identity mapping and session.

### The ceremony never travels

The `state` parameter is a random opaque id. The PKCE verifier, nonce and return
path live server-side in the `StateStore` and are removed the first time the
callback claims them — so the front channel carries nothing to read, and single
use is structural rather than an added check.

An earlier revision of this package signed the ceremony INTO the state
parameter. That made it tamper-evident but still readable by anything that saw
the URL (referrer, proxy log, browser history), with its confidentiality resting
on the consumer setting an `HttpOnly` cookie — a property of somebody else's
integration, which is not a guarantee a library gets to make.

Two things a consumer owns and this package deliberately does not:

- **`return_to` validation.** It is carried opaquely. This package does not know
  which paths are safe in your application, and a library that guessed would
  either block legitimate targets or wave through an open redirect.
- **A `StateStore`.** Not optional: the store holds the PKCE verifier, so
  there is no login without one. It must be SHARED across every process serving
  the callback and its `take` must be ATOMIC — Redis `GETDEL`, or
  `DELETE ... RETURNING` on a row. `InMemoryStateStore` is tests and
  single-worker development only; behind a load balancer a login started on one
  worker cannot be completed on another, and it fails loudly rather than
  degrading.

## Releases and exact pins

`0.1.0a1` is published and adopted by Workspace for the confidential flow.
`0.1.0a2` is the declared, unreleased native-verifier supply version. A product
must not pin `0.1.0a2` until the protected adapter workflow installs the exact
artifact back from the private registry and writes
`dotmac-auth-oidc-v0.1.0a2`.

Consumers pin an exact released version—never a branch, range or
cross-repository path dependency. Sub becomes the second adopter only after it
pins the released artifact, replaces its local JWKS/verifier copy with the
surface above, and proves the real exchange path against that pin.

## Documents

- `COMPATIBILITY.md` — the public surface and the stability policy
- `CHANGELOG.md`
- `EXTRACTION.toml` — the product-first dossier
- `docs/inventories/external-identity-sources.md` — the source audit
