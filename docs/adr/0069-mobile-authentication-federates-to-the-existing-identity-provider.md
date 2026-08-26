# ADR 0069 — Mobile authentication federates to the existing identity provider

**Status:** Accepted
**Date:** 2026-08-26
**Catalogue correction — 2026-08-26:** This decision first reached `main`
through PR #459 with the already-claimed number 0066. It moved to 0069; the
decision itself is unchanged. The database-runtime decision that reached
`main` earlier through PR #452 keeps ADR-0066.
**Decision owner:** Michael
**Extends:** ADR-0065 (native mobile clients are composed applications — this
ADR closes the "choice of authorization server" that ADR-0065 explicitly did not
own), ADR-0024 (applications compose over versioned APIs), ADR-0009 (a secret is
held, never dereferenced)
**Owns:** which authorization server a Dotmac native mobile client authenticates
against, the client type and ceremony it uses, where the resulting assertion is
exchanged for a product session, the rollout order across the fleet's mobile
applications, and the prohibition on confidential-client material in a mobile
artifact
**Does not own:** the mobile client contracts themselves (ADR-0065 §§ 3–8); the
shape of Sub's own session, refresh and revocation model; Keycloak's operational
plan, backup regime or HA design; store release mechanics; any product
authorization decision, which stays with its owning module under the Dotmac
source-of-truth standard

---

## Context

ADR-0065 defined what a Dotmac native mobile client *is* and what contracts it
implements, and deliberately left one thing open: **which authorization server it
talks to.** That question could not be deferred past the first mobile
authentication change, because the answer determines whether a token exchange
seam is needed at all, whether a client secret has to exist on a device, and
whether the fleet acquires a second identity authority.

Three options were live: build an OpenID Provider inside `dotmac_sub`, adopt a
hosted identity SaaS, or federate to the Keycloak deployment the fleet already
runs. The evidence below was measured on 2026-08-26 against `dotmac_sub`
`1a3edf0eb`, `dotmac_crm` `a922decf` and this repository at the head of
`feat/mobile-contract-inventory`. It is recorded here because the decision is
only defensible against facts, and the facts have a short half-life.

### The identity provider already exists and is production-shaped

`https://idp.dotmac.io/realms/dotmac/.well-known/openid-configuration` returns
**HTTP 200** with a complete endpoint set — `authorization_endpoint`,
`token_endpoint`, `jwks_uri` at
`…/protocol/openid-connect/certs` — and advertises
`code_challenge_methods_supported: ["plain", "S256"]`, so **S256 is available**.
It runs on a dedicated hardened Keycloak host with hourly encrypted backups, and
an isolated restore drill has been performed and passed.

Two properties of that same document are hardening obligations rather than
reassurances, and § 4 of the decision turns them into prerequisites: the realm
advertises `plain` alongside `S256`, and its
`grant_types_supported` still includes `implicit` and `password`.

### `dotmac_sub` is not an OpenID Provider, and making it one is the expensive path

Measured at `1a3edf0eb`:

- There is **no `/authorize` endpoint, no discovery document, no JWKS endpoint
  and no client registry.** Nothing in Sub models a relying party.
- `POST /api/v1/auth/login` mints a **symmetric HS256 JWT**.
  `app/services/auth_flow.py:394-425` builds the payload from `sub`,
  `principal_id`, `principal_type`, `session_id`, `typ`, `iat`, `exp` and
  optional `roles`/`scopes`, and signs it with `_jwt_secret(db)` under
  `_jwt_algorithm(db)`, which defaults to `HS256` (`:195-196`). There is **no
  `iss`, no `aud` and no `kid`** — three claims an OP is required to emit and
  which every third-party verifier needs. A token with no `kid` cannot be
  key-rotated without a flag day; a token with no `iss`/`aud` cannot be safely
  accepted by any second service.
- Refresh is an **opaque `secrets.token_urlsafe(48)`**, stored SHA-256-hashed
  (`_hash_token`, `:336`), with rotation and reuse detection via
  `previous_token_hash` (`:1655-1725`). This is a sound *product session*
  design. It is not an OAuth refresh token and does not want to become one.
- The default password hash is `pbkdf2_sha256` (`:67-68`, with `bcrypt` and
  `sha512_crypt` accepted as deprecated). **Keycloak imports `pbkdf2_sha256`
  natively**, so an eventual credential migration is an import, not a forced
  reset for every user.

Turning that into an OP means adding a discovery document, an asymmetric signing
key with rotation, a JWKS endpoint, a client registry, consent, and an
authorization endpoint with its own UI — reimplementing, against a product
deadline, the component the fleet already runs and already backs up.

### The starter's OIDC relying party is confidential by construction

`packages/dotmac-auth-oidc` `0.1.0a1` is the fleet's audited relying-party
implementation, and it is a **confidential** one. Three of its properties are
load-bearing and **all three are impossible in a public mobile client**:

| Property | Where | Why a Flutter artifact cannot have it |
|---|---|---|
| `client_secret` is a **required** field of the relying-party config | `client.py:120` (`client_secret: str`, no default) | A secret shipped in an app bundle is readable by anyone who downloads the app. There is no device-side store that survives inspection of the artifact itself. |
| the secret is sent as **HTTP Basic** on the token exchange | `client.py:375` — `auth=(self._config.client_id, self._config.client_secret)` on the `post_form` to `token_endpoint` | The exchange happens from the device. Basic auth from a public client is client-secret transmission from an untrusted party. |
| security rests on a **server-side single-use `StateStore`** plus an **`HttpOnly` browser cookie** | `state.py` — "the state travels in an `HttpOnly` cookie, which is a property of the CONSUMER's" deployment; `recover_and_consume` makes the ceremony single-use | A device has no `HttpOnly` cookie and no server-side session to pin the ceremony to. PKCE is what replaces both, and it replaces them only if the code verifier never leaves the device. |

This is the **concrete** reason for the prohibition in § 4 — not a style
preference and not a generic "secrets are bad" rule. The package's own security
model assumes a server. Putting it, or its client, on a phone removes the three
things that make it safe and leaves the appearance of the same integration.

### The exchange seam already exists in the kernel Sub already pins

`dotmac_sub` pins `dotmac-kernel==0.1.0a91` (`pyproject.toml:51,68,84,332`).
That kernel ships `dotmac_kernel.external_identity`, whose
`finalize_external_login(...)` resolves a **verified** external subject to a
local `Party` on a tenant-scoped
`(tenant_id, provider_binding, issuer, subject)` match against an ACTIVE
binding. Its documented rules are exactly the ones this decision needs:
`(issuer, subject)` alone is refused because "any provider that can mint a token
for a known subject string" would otherwise authenticate as that party;
`provider_binding` records *which* configured provider completed the ceremony;
an unbound subject resolves to `None` and **never provisions**.

The "identity-provider roles never grant product permissions" invariant is
already enforced, not merely written down:
`tests/unit/test_external_identity.py::test_the_module_reaches_no_network_and_reads_no_external_roles`
asserts at source level that the binding seam contains no `httpx`/`requests`/
`urllib`/`jwt`/`jwks`/`socket` **and** no `roles`/`groups`/`scopes`/
`entitlement`; its own docstring names its sibling, ERP's
`test_identity_protocol_boundary`, as the other half of the pair.

---

## Decision

### 1. The existing Keycloak realm is the authorization server

Dotmac native mobile clients authenticate against
`https://idp.dotmac.io/realms/dotmac`. The fleet acquires **no second identity
authority**. `dotmac_sub` does not become an OpenID Provider, and no hosted
identity SaaS is introduced.

The realm is the authority for *authentication* only — for the assertion "this
subject proved possession of this credential at this issuer". It is never the
authority for what that subject may do (§ 6).

### 2. Each mobile application registers its own PUBLIC native client

One Keycloak client per application artifact, registered as **public** (no
client authenticator), with:

- **Authorization Code flow with PKCE, `code_challenge_method=S256`.** No other
  flow. `implicit` and `password` are disabled on these clients regardless of
  what the realm advertises (§ 4).
- **OS-browser authentication** — `ASWebAuthenticationSession` on iOS,
  Custom Tabs on Android. **Never an embedded `WebView`**: an in-app web view is
  a credential-entry surface the application can read, which defeats the reason
  for federating in the first place, and it forfeits the platform's own
  passkey/password-manager integration.
- **An exact, registered redirect URI** — an App Link / Universal Link, per § 7.
  Wildcard redirect registration is refused.

A client is per **artifact identity**, matching ADR-0065 § 1's rule that a mobile
audience is an artifact identity rather than a path prefix. `field_mobile` and
the customer self-care app never share a client, because they never share an
audience.

### 3. The ID token is exchanged inside Sub for a Sub-owned session

The device completes the ceremony with Keycloak, then presents the **ID token**
to `dotmac_sub`. Sub verifies it — signature against the realm JWKS, `iss`,
`aud` equal to that application's client id, `exp`/`nbf`, and the `nonce` bound
to the ceremony — and, on success, **exchanges it for a Sub-owned session**: the
same access/refresh pair Sub issues today, under Sub's own rotation and reuse
detection (`auth_flow.py:1655-1725`).

**The exchange delegates its identity resolution to
`dotmac_kernel.external_identity.finalize_external_login`.** Sub does not write a
second subject→party mapping. The tenant-scoped
`(tenant_id, provider_binding, issuer, subject)` uniqueness is the seam, and the
"no provisioning" rule holds: an unbound subject is a refused login, never an
implicitly created party.

Three properties follow, and each is a reason the exchange exists rather than
having the device carry the IdP's tokens directly:

1. **Sub keeps one session model.** Every existing consumer of a Sub session —
   revocation, reuse detection, impersonation, staff projection — keeps working
   unchanged, because the credential the device holds afterwards is still a Sub
   credential.
2. **The IdP is on the login path only.** An issued Sub session survives an IdP
   outage; only *fresh* logins fail. This is the property that makes § 7's
   staged rollout safe, and it is why the exchange is not merely a convenience.
3. **The ID token is consumed, not stored.** It is an assertion about a moment,
   presented once. A mobile client does not persist it and does not present it
   to any other Dotmac service.

### 4. No client secret, and never the confidential relying party, in a Flutter artifact

**A Dotmac mobile artifact contains no OAuth client secret, and never the
confidential `dotmac-auth-oidc` client or its credentials.** The reason is the
three properties measured above — a required `client_secret` (`client.py:120`),
HTTP Basic on the token exchange (`client.py:375`), and a server-side single-use
`StateStore` plus an `HttpOnly` cookie (`state.py`). A device has none of the
three. Shipping that client to a phone keeps the integration's shape and
discards its security model.

Two per-client hardening pins are **prerequisites**, not follow-ups, because the
realm's own document shows why:

- **Pin `S256` per client.** The realm advertises
  `code_challenge_methods_supported: ["plain", "S256"]`. `plain` is not a proof
  of possession — an attacker who intercepts the authorization code also has the
  verifier. Each native client sets its PKCE challenge method to `S256` in
  Keycloak so the server refuses `plain` for that client whatever the realm
  advertises. A client-side preference for `S256` is not a control; the *server*
  must refuse.
- **Disable `implicit` and `password` per client.** The realm's
  `grant_types_supported` includes both. Neither may be enabled on a native
  client: `implicit` returns tokens in a redirect the OS may log, and `password`
  reintroduces the credential-entry surface § 2 exists to remove.

### 5. The backend remains the owner of authorization

**Identity-provider roles, groups and scopes never grant product permissions.**
Keycloak says *who* authenticated. Every decision about what that party may do
is taken by the owning Dotmac module against local grants, exactly as it is
today.

This is not a new invariant and it is not aspirational: the kernel seam this
decision delegates to already refuses to read external roles at source level
(`test_the_module_reaches_no_network_and_reads_no_external_roles`), and ERP's
`test_identity_protocol_boundary` asserts the same boundary from the other side.
An IdP claim that looks like a role is data about the assertion, never an input
to an authorization check.

The practical consequence: mapping a Keycloak group onto a Dotmac role in the
IdP is **forbidden**, because it would move an authorization decision into a
system that is not its owner and that no Dotmac test can constrain.

### 6. `field_mobile` first; customer self-care is deferred until the IdP has HA

`field_mobile` adopts first. Customer self-care is **deferred until Keycloak has
HA** — the identity provider is currently a **single host with a 4-hour RTO**,
and the two audiences carry very different failure costs: a field technician
population is bounded, reachable and can be told to hold an existing session,
while the self-care audience is the subscriber base and its first fresh login
after an IdP outage is a support event at customer scale.

The property that makes the staged rollout safe is § 3's second point, restated
because it is the whole argument: **issued Sub sessions survive an IdP outage;
only fresh logins fail.** During a 4-hour IdP outage every already-signed-in
technician keeps working, keeps syncing and keeps completing jobs. That is an
acceptable exposure for a bounded workforce and an unacceptable one for a
subscriber base, which is why the order is what it is rather than a preference
for doing the smaller app first.

The gate on self-care is **Keycloak HA**, not a date and not "field went fine".

### 7. Deep-link registration is a prerequisite, and neither app has it today

A native OIDC redirect needs a registered, verified redirect target. **Neither
application has one.** Measured at `1a3edf0eb`:

| Application | Android | iOS |
|---|---|---|
| `field_mobile` | **no intent-filter at all** beyond `MAIN`/`LAUNCHER` (`android/app/src/main/AndroidManifest.xml:35-38`) | **no `CFBundleURLTypes` key at all** in `ios/Runner/Info.plist` |
| `mobile` (self-care) | one payment intent-filter with **`android:autoVerify="false"`** on a `${paymentScheme}` Gradle placeholder (`AndroidManifest.xml:44-49`) | **hardcoded `dotmacpay`** in `CFBundleURLTypes` (`ios/Runner/Info.plist:46-56`) |

Two defects are visible in that table and both must be fixed before an OIDC
redirect is registered:

1. **`autoVerify="false"` is a custom scheme, not an App Link.** Any application
   on the device may claim the same scheme and receive the authorization code.
   Mobile OIDC redirects use **verified App Links / Universal Links** backed by
   `assetlinks.json` and `apple-app-site-association`, or a loopback redirect —
   never an unverified custom scheme.
2. **The self-care scheme is configured twice, differently.** Android reads a
   `${paymentScheme}` Gradle property while iOS hardcodes `dotmacpay`. Two
   sources of truth for one identity is exactly the drift ADR-0065 was written
   about, and it must not be the pattern the auth redirect copies. One declared
   value, both platforms.

### 8. The existing password/MFA login gets a BOUNDED deprecation, and the bound cannot be set yet

Sub's password and MFA login is not removed by this decision, and it is not left
open-ended either. It gets a **bounded** deprecation plan: a stated end date, an
announced migration window and a removal change.

**The bound cannot be set today, and this ADR does not invent one.** Setting it
requires production counts that were not obtainable in this session:

| Required input | Why the bound depends on it |
|---|---|
| count of active credentials by principal type | determines whether migration is an import or a re-enrolment, and for how many people |
| count of active sessions and their age distribution | determines how long a dual-path window must stay open before removal strands anyone |
| count of enrolled MFA factors, by factor type | Keycloak must be able to carry each factor type, or its holders need re-enrolment before, not after, the cutover |

Until those three numbers exist, the deprecation is **bounded in principle with
an unset bound**, and that is what is recorded. A date written without them
would be a guess presented as a plan. Producing the counts is the named next
action; the bound is set in the same change that records them.

---

## Named risks

Recorded honestly, including the ones that are measured defects rather than
hypotheticals.

1. **The realm advertises `plain` for PKCE.** Mitigated only by the per-client
   pin in § 4. Until that pin exists in the realm configuration, the S256
   requirement is a client-side intention, and a client-side intention is not a
   control.
2. **The realm advertises `implicit` and `password` grants.** Same shape, same
   mitigation, same gap until the per-client configuration lands.
3. **No verified deep link exists on either application** (§ 7). This is the
   largest single piece of prerequisite work and it is platform work, not
   backend work.
4. **The IdP is a single host with a 4-hour RTO.** Accepted for `field_mobile`
   on the reasoning in § 6; it is the explicit gate on self-care.
5. **Offline cold-restart discards a valid refresh token, and OIDC makes it
   worse.** `field_mobile/lib/features/auth/auth_state.dart:82-86`:
   `_restoreSession` calls `ensureFreshToken()` and, when it returns `null`,
   calls `currentStore.clear()` — and the surrounding `catch (_)` does the same.
   A **pure network failure** therefore wipes the token store and discards a
   valid 30-day refresh token, signing out a technician who was merely out of
   coverage. This directly violates ADR-0065 § 8's "network failure and 5xx do
   not sign out" rule, which that application's own
   `auth_flow_test.dart` asserts for the *refresh* path but not for the
   *restore* path. **Under OIDC it gets worse**: recovery today is a password
   re-entry that works against a reachable backend, whereas recovery after
   federation needs the OS browser to reach the *identity provider*. A
   technician in a coverage hole is then locked out of an application that holds
   perfectly valid offline data. **This defect is a blocker for § 6's
   `field_mobile` adoption, not a parallel cleanup.**
6. **Two identity surfaces coexist during the deprecation window.** Both the
   exchange path and the password path issue Sub sessions. Any session-affecting
   change — revocation, reuse detection, MFA policy — must be applied to both or
   it is applied to neither. The window is what § 8 exists to bound.
7. **A per-artifact client multiplies the configuration that must stay correct.**
   Three applications become three clients, each with its own redirect URIs,
   PKCE pin and disabled grants. There is no test in any Dotmac repository that
   reads realm configuration, so this is review discipline (see below), and
   saying so is the point rather than implying a guard.

---

## Enforcement status

Honest as of 2026-08-26. **Where no check exists, the row says `none yet`.** No
row names a guard that has not been written.

| Rule | Enforced by | Status |
|---|---|---|
| §1 one authorization server; Sub does not become an OP | — | **none yet.** Review discipline. Nothing measures the absence of an `/authorize` endpoint. |
| §2 public native client, code+PKCE S256, OS browser, no embedded `WebView` | — | **none yet.** No Dart is governed by this repository, and no repository reads realm configuration. |
| §3 exchange delegates to `finalize_external_login`; tenant-scoped binding tuple | `tests/unit/test_external_identity.py`, `tests/test_external_identity_isolation.py`, `tests/test_external_identity_login_race.py` — **for the kernel seam**, not for a Sub exchange endpoint that does not exist yet | partial: the seam is tested; nothing yet asserts that Sub's future exchange uses it |
| §4 no client secret in a mobile artifact | — | **none yet.** A secret-scanning gate over the Flutter trees would have to live in `dotmac_sub`; none exists. |
| §4 per-client `S256` pin; `implicit`/`password` disabled | — | **none yet.** Realm configuration is not under test in any repository. |
| §5 IdP roles never grant product permissions | `tests/unit/test_external_identity.py::test_the_module_reaches_no_network_and_reads_no_external_roles`; ERP's `test_identity_protocol_boundary` | **ENFORCED**, at source level, in the kernel seam and its ERP counterpart |
| §6 rollout order; self-care gated on IdP HA | — | **none yet.** A sequencing decision; no artifact can assert it. |
| §7 verified App Link redirect; one declared scheme per platform | — | **none yet.** Both defects are measured in this ADR and open. |
| §8 bounded deprecation of password/MFA login | — | **none yet.** The bound is unset pending the three production counts. |

**Most of these will stay `none yet` for the structural reason ADR-0065 already
records:** this repository is Python and holds no Dart, so it cannot run a
Flutter analyzer or an application test; and no Dotmac repository currently reads
Keycloak realm configuration, so realm-side pins are review discipline until
something does. Writing an aspirational guard name here would be the
"enforceable premise" failure ADR-0018 forbids.

---

## Consequences

- **The fleet gains no second identity authority.** The existing hardened
  Keycloak host, with its hourly encrypted backups and its passed restore drill,
  becomes the authentication authority for mobile. Nothing else changes owner.
- **`dotmac_sub` keeps its session model and gains one endpoint.** The exchange
  is an adapter over `finalize_external_login`, not a new authority. Sub's
  HS256 token stays what it is — a **product session credential**, never
  presented to a third party — and its lack of `iss`/`aud`/`kid` stops being a
  latent problem precisely because it stops being on a path where anyone else
  would need to verify it.
- **The prohibition on confidential-client material is now grounded rather than
  asserted.** `client.py:120`, `client.py:375` and `state.py` are the three
  reasons, and a future reviewer can check each one.
- **Two blockers are named and both are open.** No verified deep link exists on
  either application (§ 7), and `field_mobile` discards a valid refresh token on
  a pure network failure (risk 5). Neither is a parallel cleanup; the first
  makes the redirect unregisterable and the second turns a coverage hole into a
  lockout that only the IdP can end.
- **The realm needs per-client hardening before the first client ships.** `plain`
  PKCE, `implicit` and `password` are all advertised at the realm level today.
  The pins are per client and they are prerequisites.
- **The password login's removal has a plan but not a date**, and the missing
  input is named rather than estimated. This ADR is amended, not superseded,
  when the three production counts exist.
- **Nothing here is implemented by this ADR.** It changes no runtime behaviour in
  this repository, which contains no mobile code and no Sub endpoint. It
  constrains what the mobile authentication work may build.

---

## Alternatives rejected

**Build an OpenID Provider inside `dotmac_sub`.** Rejected on measured cost and
duplicated authority. Sub has no `/authorize`, no discovery document, no JWKS and
no client registry, and its access token is a symmetric HS256 JWT with no `iss`,
no `aud` and no `kid` — none of which is a defect for a product session and all
of which are prerequisites for an OP. Building them means reimplementing, under
a product deadline, the component the fleet already runs, backs up hourly and has
restore-tested. It would also give the fleet two identity authorities, which is
the outcome ADR-0024's independence rule exists to avoid.

**Adopt a hosted identity SaaS.** Rejected: it introduces a third-party
dependency on the login path of an ISP's field operations, in exchange for
capabilities the existing Keycloak already has, and it would strand the
`pbkdf2_sha256` credentials Keycloak can import natively.

**Ship the confidential `dotmac-auth-oidc` client inside the app.** Rejected on
the three concrete grounds in § 4 — a required `client_secret`, HTTP Basic on the
token exchange, and a security model resting on a server-side single-use state
store and an `HttpOnly` cookie. Every one of those is absent on a device. This is
the option that looks cheapest and is the only one that is unsafe by
construction.

**Let the device hold the IdP's own tokens and skip the exchange.** Rejected:
Sub would then have two session models, an IdP outage would break *every*
request rather than only fresh logins (removing the property § 6's staged
rollout depends on), and every existing consumer of a Sub session — revocation,
reuse detection, impersonation, staff projection — would need a second
implementation.

---

## References

- `docs/adr/0065-mobile-clients-are-composed-applications.md` — the mobile client
  contracts; § 8's authentication state machine is what the exchanged Sub session
  drives, and § 1's rule that no browser concept enters a mobile contract is why
  § 4 above refuses the cookie/state-store model rather than emulating it
- `docs/adr/0024-apps-compose-by-synchronizing-data.md` — applications are
  independent authority boundaries; the IdP is a transport for an authentication
  assertion, never an authorization system
- `docs/adr/0009-secrets-are-held-not-dereferenced.md` — a secret is held, never
  dereferenced; on a device there is no trusted store to dereference *to*, which
  is why § 4 is a prohibition rather than a storage requirement
- `docs/adr/0018-an-exemption-must-be-enforceable.md` — why the enforcement table
  says `none yet` instead of naming an intended guard
- `docs/inventories/mobile-application-sources.md` — the measured evidence base
  for the three applications, including the deep-link and cold-restart defects
- `packages/dotmac-auth-oidc/src/dotmac_auth_oidc/` — `client.py:120`,
  `client.py:375`, `state.py`: the three properties that make it a confidential
  relying party
- `packages/dotmac-kernel/src/dotmac_kernel/external_identity.py` — the exchange
  seam, and the "no external roles" boundary it enforces at source level
