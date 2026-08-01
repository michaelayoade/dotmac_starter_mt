# WS8 — signed/versioned licence delivery: security + contract design

> **Status:** Implementation brief (2026-08-01). Fixes the canonical signed
> document, key rotation/revocation, deployment binding, offline verification,
> expiry/grace, replay/rollback protection, and version/digest acknowledgement
> for the kernel slice of WS8 (→ 0.1.0a7). Boundaries follow the ruled C4 /
> entitlement-boundary decisions (2026-07-31): the kernel owns product-neutral
> verification contracts only — **no private signing key ever enters the kernel
> or a customer deployment**; issuance/custody stays in the vendor control
> plane; each product data plane verifies locally and writes its OWN WS2 grants.

## Problem

The vendor control plane now carries the full Contract → Allocation path
(ContractService emits `contract.activated`; AllocationService stages an
immutable allocation). What is missing is the **cross-plane hand-off**: how an
allocation becomes capabilities inside a product deployment **without** the
control plane ever writing the product database, and **without** the product
trusting the network at request time.

The ruled shape (C4): delivery is a **signed, versioned licence document** the
product data plane verifies offline, projects into its local WS2
`tenant_entitlement_grants` via `grant_entitlement`, and **acknowledges by
applied version + digest**. WS2's evaluator already guarantees request-time
decisions are purely local; WS8 supplies the verified input to that projection.

Getting this wrong has two failure classes: a forgery/replay path (a tampered,
stale, or re-targeted licence grants capabilities) and a coupling path (a
deployment that must phone home to decide access). Both are designed out below;
everything **fails closed**.

## Division of labour (plane boundaries — ruled, not new)

| Concern | Owner |
|---|---|
| Licence schema, envelope format, digest rule, verifier, keyring/rotation semantics, replay guard, ack **types**, signing **fake for tests** | **Kernel** (`dotmac_kernel.licensing`, `dotmac_kernel.testing.licensing`) |
| Issuance, private-key custody (OpenBao), versioned delivery, renewal, revocation-list distribution, acknowledgement **tracking** | **Vendor control plane** |
| Verify → project into local WS2 grants (`grant_entitlement`, capability codes validated against ITS catalogue) → explainable decisions → emit ack | **Product data plane** (reference receiver proves it) |

The kernel ships **verification only**. The one place a private key exists in
this repo is the **ephemeral, per-test key** inside the testing kit's fake
signer — generated in memory, never persisted, never a real issuer key.

## Canonical signed document

### Envelope — the signed bytes are the truth

DSSE-style two-layer format. The **outer envelope** is plain JSON:

```json
{
  "schema": "dotmac-licence-envelope/1",
  "payload_b64": "<base64url of the payload bytes, no padding>",
  "signatures": [
    {"key_id": "vendor-2026-a", "algorithm": "ed25519", "signature_b64": "<base64url>"}
  ]
}
```

- Signatures are computed **over the exact payload bytes** (the base64url-decoded
  `payload_b64`), never over a re-serialization. There is **no canonical-JSON
  algorithm to agree on** — the bytes the issuer signed are the bytes the
  verifier checks, and the payload JSON is parsed only **after** a signature
  verifies. This removes the entire canonicalization-mismatch bug class.
- **Digest** = `"sha256:" + hex(SHA-256(payload bytes))` — the identity used by
  replay protection and the acknowledgement. Two envelopes carrying the same
  payload bytes have the same digest regardless of signature set.
- `signatures` is a list so a **rotation window** can double-sign (old + new
  key). Verification succeeds when **at least one** signature verifies with an
  acceptable key (see keyring rules); a revoked key never contributes.
- Unknown `schema` or `algorithm` values **fail closed** (no fallback parsing,
  no algorithm negotiation). Algorithm agility is a future schema/major bump,
  not a runtime choice an attacker can steer.

### Payload — the licence document

The payload bytes decode to JSON (`dotmac-licence/1`):

```json
{
  "schema": "dotmac-licence/1",
  "licence_id": "9f4c…",
  "licence_version": 3,
  "issuer": "dotmac-vendor",
  "product": "dotmac-sub",
  "edition": "standard",
  "subject": {"customer": "acme-isp", "deployment_id": "dep-7f2e…"},
  "capabilities": [
    {"code": "inventory.use", "limits": {"seats": 25}}
  ],
  "issued_at": "2026-08-01T00:00:00+00:00",
  "not_before": null,
  "expires_at": "2027-08-01T00:00:00+00:00",
  "grace_days": 14,
  "constraints": {}
}
```

Field semantics:

- **`licence_id`** — stable identifier of the licence **lineage** (one
  commercial licence for one subject+product). A renewal/amendment re-issues the
  SAME `licence_id` with a **higher `licence_version`**; a genuinely new
  contract gets a new `licence_id`. Revocation lists name `licence_id`s.
- **`licence_version`** — positive integer, **strictly monotonic within the
  lineage**. The replay/rollback guard and the acknowledgement are keyed on
  `(licence_id, licence_version, digest)`.
- **`subject.customer`** — required opaque commercial identity (vendor-plane
  vocabulary; the kernel never interprets it).
- **`subject.deployment_id`** — **optional deployment binding** ("where
  contracted", per the plan). When present, the verifier requires the caller's
  `expected_deployment_id` to match exactly; a licence bound to deployment A
  verifies nowhere else. When absent the licence is deliberately portable
  (e.g. pre-provisioning issuance) — a *contract* choice, not a verifier
  default: the verifier takes an explicit `require_binding` flag so a receiver
  contracted for bound licences fails closed on an unbound document.
- **`capabilities`** — the grant set to project. Codes are WS1 capability codes
  **declared by the receiving product's installed modules**; the verifier does
  NOT resolve them (it has no catalogue) — projection does, because
  `grant_entitlement` already refuses undeclared codes. A licence naming a code
  the product doesn't declare is a projection error surfaced in the ack, not a
  silent grant. `limits` flows through to the grant's `limits` (explainable
  quota), uninterpreted by the kernel.
- **Validity** — `issued_at` required; `not_before` optional (embargoed start);
  `expires_at` optional (**absent = perpetual**, per the plan's perpetual
  on-prem case); `grace_days ≥ 0` extends `expires_at` into an explicit
  degraded-but-valid window.
- **`constraints`** — opaque mapping for contracted operational semantics
  (e.g. HA/node counts). Carried and returned verified; interpreted by the
  product/vendor contract, never by the kernel verifier. Explicit non-goal for
  this slice: the kernel defines no node-count enforcement.

Parsing is **strict**: missing required fields, wrong types, negative versions,
naive (non-UTC-aware) timestamps, or `not_before`/`expires_at` inversions are
`MalformedLicenceError` — before any validity logic runs.

## Keys — rotation and revocation

`LicenceKeyRing` is the verifier's trust store: immutable value objects,
distributed to deployments as **public material only** (config/file — its
distribution mechanism is vendor/ops-owned, out of kernel scope).

- `LicenceKey(key_id, algorithm="ed25519", public_key_b64, status)` — unique
  `key_id` per ring (duplicate fails closed at construction).
- **`status` drives rotation semantics:**
  - `active` — verifies signatures; the issuer signs new documents with it.
  - `retired` — **still verifies** (rotation overlap: documents signed before
    rotation stay valid) but the issuer must no longer sign with it. Retiring is
    how a key ages out without invalidating the installed base.
  - `revoked` — **never verifies anything**, even a signature that is
    cryptographically valid. Compromise response: revoke the key, re-issue
    affected licences (higher `licence_version`) under a new key, ship the
    updated keyring + revocation list.
- A signature by an **unknown `key_id` fails closed** — the keyring is a closed
  world; there is no fetch-the-key-from-the-document mechanism (that would let
  an attacker bring their own key).

**Algorithm:** Ed25519 only (RFC 8032) — small keys, deterministic signatures,
no parameter/padding pitfalls, the modern default for exactly this use. The
envelope's `algorithm` field exists so a future schema revision can migrate;
`dotmac-licence-envelope/1` accepts nothing else.

## Verification — one function, fail-closed, offline

`verify_licence(envelope, *, keyring, now, expected_deployment_id=None,
require_binding=False, applied=None, revoked_licence_ids=frozenset())`
→ `VerifiedLicence` or raises a `LicenceError` subclass. Order matters and is
part of the contract (cheapest/structural first, and **no validity information
leaks from an unverified document** — signature is checked before the payload
is even parsed):

1. **Envelope shape** — schema/fields/base64 valid, else `MalformedLicenceError`.
2. **Signature** — ≥1 signature verifies under an `active`/`retired` key;
   unknown key → `UnknownKeyError`; only-revoked-key coverage →
   `RevokedKeyError`; no valid signature → `BadSignatureError`.
3. **Payload parse** (strict, above) → `MalformedLicenceError`.
4. **Licence revocation** — `licence_id ∈ revoked_licence_ids` →
   `RevokedLicenceError`.
5. **Deployment binding** — document bound and mismatched →
   `DeploymentMismatchError`; unbound but `require_binding=True` → same error.
6. **Validity vs injected `now`** — before `not_before` →
   `LicenceNotYetValidError`; past `expires_at + grace_days` →
   `LicenceExpiredError`; past `expires_at` but inside grace → **verifies** with
   `validity="in_grace"` (explicit degraded state the receiver can surface);
   otherwise `validity="valid"`. Perpetual (no `expires_at`) is always `valid`.
7. **Replay/rollback** — against the caller-supplied `applied`
   (`AppliedLicence(licence_id, licence_version, digest)`, the receiver's
   durable record of what it last applied, same lineage):
   - lower `licence_version` → `StaleLicenceError` (an old document cannot roll
     a deployment back after a revocation-by-reissue);
   - same version, same digest → verifies with `reapplied=True` (idempotent
     redelivery — safe, at-least-once transports are expected);
   - same version, **different digest** → `LicenceConflictError` (two distinct
     documents claiming one version is an issuer-side integrity failure — never
     pick one).

`VerifiedLicence` is frozen: the parsed `LicenceDocument`, the `digest`, the
`validity` state, and `reapplied`.

**Offline/air-gapped posture:** every input is local (envelope bytes, keyring,
clock, applied-record, revocation set). There is **no network I/O anywhere in
the module**, no phone-home, and no wall-clock read — `now` is always injected
(receivers use their trusted clock; tests use `FakeClock`). Clock-tamper
resistance beyond that is receiver policy (e.g. persisting a
high-water-mark clock) and is documented as such, not smuggled into the
verifier.

## Revocation list — same envelope, monotonic

Connected AND air-gapped deployments import revocations the same way: a
**signed revocation list** using the identical envelope mechanics, payload
schema `dotmac-licence-revocation/1`:
`{schema, list_version, issued_at, revoked_licence_ids: [...]}`.
`verify_revocation_list(envelope, *, keyring, applied_list_version=None)`
verifies the signature and enforces **monotonic `list_version`** (a stale list
cannot un-revoke; equal version is an idempotent re-import). The verified set
is what the receiver stores and passes into step 4 above. Key revocation is
keyring `status` (ops-distributed), licence revocation is this list — two
mechanisms, deliberately not conflated.

## Acknowledgement — version/digest, both planes speak it

`LicenceAcknowledgement(licence_id, licence_version, digest, status,
reason=None, deployment_id=None)` (frozen) is the shared vocabulary:

- `status="applied"` — the receiver verified the document AND committed its
  local WS2 projection; `digest` is the **exact payload digest applied**, so
  the vendor plane can distinguish "applied v3" from "applied a v3 it never
  issued".
- `status="rejected"` — verification or projection failed; `reason` carries the
  stable error code (the `LicenceError` subclass name — machine-readable, in
  the same spirit as `EntitlementDecision.reason`).

Transport of the ack (API call, outbox event, exported file from an air-gapped
site) is vendor/product-owned. The kernel defines the value object so neither
plane invents a local variant — the same reason `CommandEnvelope` lives here.

## Dependency decision

Ed25519 needs `cryptography` (the kernel is stdlib-HMAC/argon2 only today).
Ruling for this slice: **optional extra** —
`pip install dotmac-kernel[licensing]` — matching the `testing`/`httpx`
precedent. `dotmac_kernel.licensing` stays importable without it (types,
parsing, digest are stdlib); the signature step imports `cryptography` lazily
and raises `VerificationUnavailableError` (fail closed, actionable message) if
the extra is missing. WS8 is an optional workstream (plan: "Signed licensing
(optional)"); SaaS-profile assemblies shouldn't carry a compiled crypto
dependency they never call. The testing-kit signer needs the same package, so
the `testing` extra grows `cryptography` too.

## Explicit non-goals / must-nots (this slice)

- **No issuance in the kernel.** No signing API outside the test fake; no key
  generation helper for production use; private-key custody is vendor-plane
  (OpenBao pointer, never a value in any repo).
- **No storage.** No new tables/migrations — the receiver owns durable
  `applied` state and revocation imports in ITS schema (the reference receiver
  slice designs that; kernel migration head stays `0012`).
- **No delivery transport.** How envelopes reach a deployment (API, outbox
  relay, file import) is not this module; it verifies bytes it is handed.
- **No entitlement writes.** Projection into WS2 grants is the receiver calling
  `grant_entitlement` itself — this module never touches a `Session`.
- **No enforcement of `constraints`** (HA/node semantics) — carried, not
  interpreted.

## Acceptance tests (canary-first — written before the implementation)

1. **Round-trip:** fake-signer-issued envelope verifies; `VerifiedLicence`
   carries the parsed document, stable digest, `validity="valid"`.
2. **Tamper:** any payload byte change → `BadSignatureError`; digest never
   computed from a re-serialization (byte-identical payloads ⇒ equal digests).
3. **Keyring:** unknown `key_id` fails; `retired` key still verifies; `revoked`
   key fails even with a valid signature; double-signed rotation envelope
   verifies via either acceptable key; duplicate `key_id` fails ring
   construction.
4. **Validity clock:** injected `now` drives not-yet-valid, valid, in-grace,
   expired (grace boundary exact); perpetual licence never expires; naive
   datetime rejected.
5. **Binding:** bound licence verifies only with matching
   `expected_deployment_id`; unbound verifies portably but fails under
   `require_binding=True`.
6. **Replay/rollback:** lower version → `StaleLicenceError`; same
   version+digest → `reapplied=True`; same version, different digest →
   `LicenceConflictError`.
7. **Revocation:** revoked `licence_id` fails; revocation list verifies via the
   same envelope; stale `list_version` rejected; idempotent re-import allowed.
8. **Fail-closed order:** signature is checked before payload parse (a tampered
   document reveals nothing, not even "expired"); unknown schema/algorithm
   rejected.
9. **Offline:** module performs no network/DB/wall-clock access (no
   `datetime.now` outside injected parameters — structural check), and imports
   without `cryptography` installed; only signature verification raises
   `VerificationUnavailableError`.

## Decisions and remaining knobs

**Decided here:** DSSE-style bytes-are-truth envelope; Ed25519 only; digest =
sha256 of payload bytes; keyring statuses `active`/`retired`/`revoked`;
lineage = `licence_id`, monotonic `licence_version`; grace as explicit
`in_grace` state; optional deployment binding + `require_binding`; ack by
`(licence_id, licence_version, digest)`; `cryptography` via `licensing` extra.

**Deferred to later slices:** vendor issuance service + key ceremony/custody
(OpenBao), delivery/renewal distribution + ack tracking (vendor CP), the
reference product receiver (verify → local WS2 grant → explainable decision →
ack), keyring distribution/refresh mechanics, receiver clock-tamper policy,
OEM delegated issuance (WS9 — the envelope leaves room for a chained/delegated
issuer model but none is specified here).
