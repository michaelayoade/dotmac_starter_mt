# External identity: source audit

**Date:** 2026-08-14; current-tree amendment 2026-08-23
**Scope:** every Dotmac repository, for two capabilities that are usually
confused: the OIDC/OAuth2 **protocol client**, and the **local binding** from a
verified external subject to a local identity.
**Reason:** hard rule 24 — a shared implementation may not be written before the
product implementations are inventoried, and ERP and Sub must both be covered.
**Repositories swept:** `dotmac_erp`, `dotmac_sub`, `dotmac_crm`,
`dotmac_vendor_control_plane`, `dotmac_academy_app`, `dotmac_starter_mt`.

## The headline

The two capabilities have **different owners in different products, and neither
product has both halves.**

| | protocol client | local subject→identity binding | provider registration |
|---|---|---|---|
| ERP | none — deleted in ERP PR #302 | retained empty `federated_identities`, with no reader or writer | none |
| Sub | none | none | `authentication_bindings` |
| CRM | Meta OAuth2 only (not SSO) | none | `ConnectorConfig` (integration, not auth) |
| Vendor CP | none | none | none |
| Academy | none | none | none |
| starter | none | **`external_identity_bindings` (this change)** | none |

That asymmetry was the original finding. The current ERP tree at
`0dc07e4b6dd36260c9510a7115dbdc656e2a19a5` no longer accepts an external
identity assertion: the production inspection found OIDC disabled, no provider
configured and zero binding rows, and ERP PR #302 deleted the unshipped client.
Sections A and D4 preserve the source audit that shaped the shared contracts;
they are historical extraction evidence, not a claim that ERP still runs an
OIDC protocol writer. The retained empty table is a separate binding-retirement
concern and does not make ERP a protocol source.

## A. ERP — the external-subject half

`app/models/auth.py:98-140`, migration
`alembic/versions/20260720_add_federated_identity.py`.

Eight columns: `id`, `person_id` (FK `people.id` CASCADE), `issuer`
varchar(512), `subject` varchar(255), `is_active`, `last_authenticated_at`,
`created_at`, `updated_at`. Two uniques —
`uq_federated_identities_issuer_subject` and
`uq_federated_identities_person_issuer`.

**What ERP got right, and what is ported:**

- The boundary itself. `docs/oidc_identity_contract.md` is a written contract
  with an authority table, and it is enforced:
  `tests/architecture/test_identity_protocol_boundary.py` asserts the exact
  `(issuer, subject)` predicate appears in the adapter and that the token
  `roles` appears nowhere in the login path.
- Explicit no-auto-provision. An unbound subject is refused
  (`oidc.py:330-334`), and the contract states provider *"role, group, scope,
  organization, and employee claims are not accepted as ERP authorization.
  Email is display evidence only and is never used for automatic account
  linking."*
- Re-checking local person state on every login, not trusting bind time
  (`oidc.py:336-337`).
- Disable-not-delete, so the evidence survives (`oidc.py:396-406`).

**What is NOT ported, and why:**

- **No tenant column and no RLS.** `federated_identities` appears in neither ERP
  RLS migration, while the `Person` it points at *is* org-scoped
  (`app/models/person.py:57-59`). So `(issuer, subject)` uniqueness is global
  across every ERP organization and the boundary is only transitive through the
  FK. For a table that decides who a login is, that is the defect the kernel
  version must not inherit.
- **Single-issuer by construction.** Every call site re-reads one configured
  `OIDC_ISSUER`; `FederatedIdentityCreate` has no issuer field
  (`app/schemas/auth_flow.py:91-93`), and `disable_binding` 404s on a foreign
  issuer. The schema cannot express two providers.
- **No bind evidence and no audit event.** Neither who bound an identity nor
  why is recorded anywhere; "auditability" means row retention only.
- **No locking on the bind path.**

## B. Sub — the provider-registration half

`app/models/auth.py:51-117`, migration
`alembic/versions/527_credential_party_binding_additive.py`.

`authentication_bindings` is *"One installed, configured way of proving you are
a Party"* — `binding_key` (immutable, deployment-global), `mechanism_code`
(open declared string), `name`, `is_active`. Identity immutability is enforced
twice: an ORM `before_update` event and a Postgres trigger raising `23514`.

**The design note that decided this change's shape** (`app/models/auth.py:54-58`):

> the discriminator is the **binding**, not the mechanism code: two OIDC issuers
> or two RADIUS verifiers are two bindings of one code, and a code-keyed
> constraint would forbid a party holding a credential against each.

**What is ported:** the binding-as-discriminator idea (kernel column
`provider_binding`), and the CHECK-forced evidence pair — Sub requires
`party_binding_source` and `party_binding_reason` non-blank whenever the
projection exists, which becomes `bound_by` / `bind_reason`.

**What is NOT ported:**

- **No RLS.** Migration 527 says `- no NOT NULL, no RLS, no legacy-column
  removal` and `tests/test_credential_party_binding_migration.py:31-32` asserts
  the absence. Deferred there to a later GUC cutover; not deferrable here.
- **No issuer or subject column** exists anywhere in Sub. Grep returns only TOTP
  `totp_issuer` and an X.509 cert issuer.
- The mechanism registry (`app/services/authentication_mechanism_registry.py`)
  is a genuine ADR-0008 vocabulary with one SOT owner per code. It is **not**
  ported: see the ruling below on why `provider_binding` is data rather than a
  declaration.
- `AuthProvider.sso` is a tombstone in both ERP and Sub — a persisted enum member
  no owner declares. `tests/architecture/test_authentication_mechanism_registry.py`
  proves `owner_of("sso") is None` and that any write naming it is refused. **No
  product implements SSO today.**

## C. The other repositories

- **CRM** — no federated identity table. `ExternalReference`
  (`app/models/external.py:28`) is structurally similar (connector-config FK +
  opaque external id + local entity + `is_active`) but is an ERPNext/integration
  sync reference that nothing in the auth path reads. `OAuthToken` is outbound
  stored tokens. CRM and Sub also hold **two forked copies** of a Meta OAuth2
  client (1257 lines of difference) — OAuth2 for API access, not SSO: no OIDC,
  no PKCE, no ID tokens, no JWKS.
- **Vendor CP** — none. `issuer` hits are licence signing; `subject_id` hits are
  approval subjects.
- **Academy** — none, in either capability. It is the future consumer, not a
  source.

## D. Ruling

### D1. The binding table is `module ← ERP`, with named Sub deltas

Same shape ADR-0026 used. ERP supplies the contract, the refusal posture and the
column set; Sub supplies three mandatory deltas it demonstrably lacks
(provider-binding discriminator, evidence pair, immutability intent), and the
kernel adds the tenant isolation **neither** has. Merging them as co-equal
sources would produce a union of two data models rather than one contract.

### D2. `provider_binding` is operator data, not a declared vocabulary

Sub declares `mechanism_code` through its SOT registry, and the symmetry is
tempting. It is wrong for the same reason ADR-0026 §4 gives for `policy_code`: a
*mechanism* can only appear through a code change, but a *provider registration*
appears when an operator configures an IdP. Declaring it would put a software
release between an operator and their own identity provider. Fail-closed
resolution protects a typo instead — an unknown binding resolves to nothing.

### D3. There is no provider-registration TABLE yet, and that is a real gap

Sub has one; this kernel does not. A first-class row carrying discovery URL,
client id, key material and rotation state is a second contract with its own
lifecycle, and ADR-0009 governs where its secret half may live. Until it exists
`provider_binding` is a string whose trust the CALLER asserts — the product's
identity facet, which owns the provider configuration.

This is recorded as a limitation rather than hidden. The invariant that survives
without the table is the trust DIRECTION: the resolver keys on the whole
`(tenant, provider_binding, issuer, subject)` tuple, and the trusted component is
the one that did not arrive inside the credential being verified.

### D4. The protocol client is a SEPARATE decision, and ERP was not a qualifying
production source

The paragraphs below record the 2026-08-14 pre-inspection decision. It has now
resolved more strongly: ERP's implementation was never enabled, was deleted in
PR #302, and the current exact tree has no protocol reader or writer. The
historical finding still explains why its code was not used as trusted source.

At the audit revision, ERP's `oidc.py` was the fleet's only real OIDC
implementation, and every repository-side artifact a deployment would touch
pointed the same way — **production adoption was then unverified, not
disproven**:
`.env.example` default `OIDC_ENABLED=false`, no OIDC key in `docker-compose.yml`,
none in `deploy/systemd/`, a single commit (`bded8aa9`, 2026-07-20), and its own
contract doc written as a future cutover gate. That is evidence of absence in
git; it is NOT a reading of the running environment, which lives on the ERP host
and requires Michael to name it (the open gate
[`oidc-sources.md`](oidc-sources.md) recorded on 2026-08-12, still unperformed). Worse for extraction purposes, `_validate_id_token` and
`_exchange_code` — the security-critical core — are **monkeypatched out in every
existing test**, so signature verification, the algorithm allowlist, `kid`
handling, nonce mismatch and every claim-validation failure path have zero real
coverage.

Rule 24 makes a *"qualifying production-used, tested implementation"* the
mandatory source — and the TESTED half fails on repository evidence alone,
independent of the host. That is what makes this ruling safe to draw now: even
if a production ERP were authenticating through this code today, its security
core would still be untested, and an untested crypto path is not a qualifying
source to copy.
So any shared OIDC package is `source_mode = "greenfield-after-inventory"`, not
`"product-first"` — the design and the contract document port with confidence,
the crypto and HTTP internals do not port as trusted code.

Specific gaps a shared implementation must close, all measured in ERP:

- **no JWKS or discovery caching** — both refetched on every login, discovery
  twice per callback;
- **no clock skew leeway** — python-jose defaults to zero, so a slightly fast IdP
  is rejected;
- **no `azp` check** when `aud` is an array; no `at_hash`;
- **no one-time-use state** — the signed state cookie has a 600s window and no
  replay nullifier, where CRM's Redis store does an atomic get-and-delete;
- **`python-jose 3.3.0`** — released 2021, effectively unmaintained, pulls the
  pure-Python `ecdsa`. Pinned identically in ERP, CRM and Sub, so a port is
  drop-in today; a new shared security library should not start there.

Contamination that must not travel, all in `oidc.py`: the `FederatedIdentity` and
`Person` queries (`:322-337`), the binding CRUD (`:346-406`), the `db: Session`
threaded through every protocol method, `fastapi.HTTPException` as the error
type, the `settings` module global — and above all
`from app.services.auth_flow import _jwt_secret` (`:38`), which signs OIDC state
with the host's session-JWT secret. That single import makes OIDC state forgeable
by anything that can mint an ERP session token, and vice versa; key separation is
mandatory in any extraction.

## E. What this change does

Adds `external_identity_bindings` to the kernel (migration `0024`) and
`dotmac_kernel.external_identity`. It adds **no protocol client** — that is D4's
separate decision and remains unbuilt.
