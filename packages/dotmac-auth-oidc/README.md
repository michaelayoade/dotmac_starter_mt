# dotmac-auth-oidc

An OIDC relying-party client. It runs the Authorization Code flow with PKCE and
returns a **verified external subject** — an `(issuer, subject)` pair — and
stops there.

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

## Using it

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

## Testing against an unpublished wheel

This package is **not published**. It is absent from
`.github/release-adapters.json`, and absence is the safety mechanism: the entry
lands with the proof, never ahead of it, and the proof owed is a real pilot
consumer. `dotmac_workspace` is that pilot, which leaves a chicken-and-egg
problem — it must run against this code before there is anything to pin.

The answer is a **local wheel built at an exact SHA**, installed as an ordinary
version-pinned distribution. Not a path dependency: a path dependency across
repositories is forbidden (see the comment above `dotmac-release-catalog` in the
root `pyproject.toml`, and `dotmac_workspace/AGENTS.md`), because it makes one
repository's working tree part of another's build, and nothing then records
WHICH bytes the pilot proved.

### 1. Build the wheel, in this repository, at the SHA under test

```sh
git -C dotmac_starter_mt rev-parse HEAD          # record this; it IS the pin
cd dotmac_starter_mt/packages/dotmac-auth-oidc
poetry build -f wheel
```

The artifact lands in `packages/dotmac-auth-oidc/dist/`:

```
dotmac_auth_oidc-0.1.0a1-py3-none-any.whl
```

Poetry builds a wheel that is **reproducible in content but not named by SHA** —
the filename carries only `0.1.0a1`. So record the commit and the file's
`sha256` together, because the version alone does not distinguish two builds of
an unreleased alpha:

```sh
shasum -a 256 dist/dotmac_auth_oidc-0.1.0a1-py3-none-any.whl
```

Both values — commit and digest — go in the pilot's own notes and in its PR
description. That pair is what makes "the pilot was green" a statement about
specific bytes rather than about a version number that has not stabilised.

### 2. Consume it WITHOUT relaxing the pin

Copy the wheel into a directory the consuming repository controls (a
gitignored `vendor/wheels/`, or a CI artifact — do **not** commit the binary),
and point the resolver at it as an extra source. The version constraint stays
EXACT; only where the artifact comes from changes.

Poetry, in `dotmac_workspace/pyproject.toml`:

```toml
[[tool.poetry.source]]
name = "local-wheels"
url = "file:///abs/path/to/vendor/wheels"   # a simple-index directory
priority = "supplemental"

[tool.poetry.dependencies]
dotmac-auth-oidc = "0.1.0a1"                # EXACT, not ^ and not >=
```

pip, equivalently — `--find-links` supplies the artifact while the requirement
stays exact and the public index still resolves `pyjwt`/`httpx`:

```sh
pip install --find-links vendor/wheels 'dotmac-auth-oidc==0.1.0a1'
```

Three properties this preserves, and each is the point:

- **the version pin is untouched.** `==0.1.0a1` resolves the same way before and
  after publication; only the source moves. Nothing in the consuming repository
  has to be relaxed and then remembered back;
- **no path dependency crosses a repository boundary.** The consumer installs a
  built distribution, exactly as it will from the index;
- **the cutover is a one-line diff.** When the adapter is finally listed and
  published, delete the `[[tool.poetry.source]]` block (or the `--find-links`)
  and the identical `==0.1.0a1` requirement resolves from
  `registry.dotmac.io`. If the pilot's behaviour changes at that moment,
  something other than the source changed — which is precisely the signal the
  recorded `sha256` lets you check.

### 3. What the pilot still owes before an allowlist entry

- a **shared, atomic `StateStore`** — `InMemoryStateStore` cannot complete a
  login started on another worker (see "The ceremony never travels" above);
- the client secret **resolved by the product and installed at startup**, never
  dereferenced on a request path (ADR-0009);
- the verified subject resolved through `dotmac_kernel.external_identity`, with
  the pilot issuing its **own** session.

Only then does `.github/release-adapters.json` gain an entry. Publication is
still not adoption: `EXTRACTION.toml`'s `status` and `contract_consumers` count
CONSUMERS, and a release moves neither.

## Documents

- `COMPATIBILITY.md` — the public surface and the stability policy
- `CHANGELOG.md`
- `EXTRACTION.toml` — the product-first dossier
- `docs/inventories/external-identity-sources.md` — the source audit
