# ADR 0007 — Deployment-authenticated applied state (WS8 production-readiness)

**Status:** Accepted
**Date:** 2026-08-03
**Extends:** ADR-0004 (platform control plane), ADR-0003 (composable deployment
profiles)
**Owns:** the cross-plane contract by which a deployment PROVES its identity when
it reports what licence it has applied — the credential lifecycle, the signed
applied-state envelope, the idempotency key, and the ownership split across the
fleet, kernel, vendor and product planes
**Does not own:** licence issuance or signing (WS8 vendor plane), the licence
envelope itself (`dotmac-licence-envelope/1`), delivery transports, or the
authoritative Deployment entity (`FleetDesiredStateService`)

## Context

WS8 delivers signed, versioned licences to deployments and expects an
acknowledgement back. Today the vendor control plane can verify everything about
the *document* — signature, key status, binding, version, digest — and nothing
about the *caller*.

`EntitlementProjectionService` already fails closed on this. Every inbound
acknowledgement is recorded as `unverified_identity` evidence and activates
nothing, because `active` is defined to mean "the data plane committed this
exact version", and a caller that has proven no identity cannot establish that
for any licence, bound or unbound. The gate is correct, but it means **no
delivery can ever reach `active`** — the pipeline's terminal state is currently
unreachable in production.

Three failure modes have to be designed out before that gate can open:

1. **A claim mistaken for a proof.** The acknowledgement body already carries a
   `deployment_id`. If the ingestion path is allowed to read identity from the
   body, deployment binding becomes decorative: anyone who can reach the
   endpoint can activate any deployment's licence by naming it.
2. **A credential the vendor could use to impersonate a deployment.** A shared
   secret — even hashed at rest — is a verifier held by the party being reported
   to. A control-plane database compromise then yields the ability to
   manufacture "the fleet applied it" evidence for licences that were never
   applied.
3. **Replay presented as fresh application.** Delivery is at-least-once and a
   signed request can be captured and re-sent verbatim; the signature stays
   valid because the bytes are unchanged. Without an idempotency contract, a
   replayed report is indistinguishable from a new one.

## Decision

### 1. Identity is an Ed25519 signature; the vendor holds only public keys

Each deployment holds a unique Ed25519 private key. Its **public** key is
registered against the authoritative deployment. The control plane never holds
material capable of impersonating a deployment, and no secret crosses the wire,
so none can be logged, echoed into a stored error code, or captured at a proxy.

Private keys are read **once** from a configured file materialised from OpenBao,
exactly as licence signing keys already are (`ConfiguredLicenceSigner`). They are
never stored in application tables, never in logs, and never in an exception
message. The host and path are deployment configuration, not code.

### 2. Registration is `pending` until possession is proven

A newly registered public key is **`pending`**, not `active`. Registration
proves only that someone submitted a key; it does not prove the deployment holds
the matching private half. A key that authenticated reports from the moment it
was pasted in would let a typo — or an attacker with control-plane write
access — bind an identity the real deployment never had.

Activation requires a **one-time, expiry-bound challenge**: the vendor issues a
nonce, the deployment signs it, and only a correct signature moves the key
`pending → active`. The challenge is single-use and expires, so a captured
challenge response cannot be replayed to activate a key later.

### 3. Applied state travels in its own signed envelope

`ReceiverAppliedState` is carried in a **distinct signed envelope** that names
the `key_id`. The signature covers the **exact payload bytes** — never a
re-serialisation. Two JSON encodings of the same object differ in key order and
whitespace, so verifying against a re-encode either rejects honest callers or
gets quietly relaxed until it passes.

The signature additionally covers a **WS8-specific domain separator**, so a
signature produced for one purpose can never be replayed as another. Without it,
any other protocol that has the deployment sign caller-influenced bytes becomes
an oracle for forging applied-state reports.

This is deliberately a **separate envelope from the licence envelope**. They
travel in opposite directions, are signed by different parties with different
key custody, and conflating them would put the vendor's issuing key and the
deployment's identity key in one trust structure.

### 4. `key_id` resolves to the identity; the body's claim is only a claim

Verification resolves `key_id → deployment_ref`, and **that** result becomes
`authenticated_deployment_ref`. Identity is derived from the material that
verified, never from a field the caller chose. `key_id` is therefore globally
unique across deployments.

A body-claimed `deployment_ref` that differs from the proven one is
**quarantined** as `deployment_mismatch` — recorded as evidence, activating
nothing. It is a contradiction, not a mistake to be resolved in the caller's
favour.

### 5. `(authenticated_deployment_ref, report_id)` is the idempotency key

Scoped to the proven identity, so one deployment's `report_id` can never
collide with another's. Three cases, each distinct:

| Case | Verdict |
|---|---|
| Identical signed replay | **Idempotent** — original verdict returned, nothing changes |
| Same `report_id`, different payload | **Conflict → quarantine** — one of the two is forged or a receiver bug |
| An older but valid report | **Retained as evidence**, may never regress active state |

Replay safety lives here rather than in the signature layer. A freshness window
was considered and rejected: applied state is legitimately delayed — a
deployment that was offline reports late — so a timestamp window would reject
exactly the reports the pipeline most needs.

### 6. Rotation overlaps; revocation is immediate and backdate-proof

Rotation registers a new key while the old one is still `active`, so a
deployment cuts over at its own pace. Overlapping active keys are expected.

**Revocation blocks every report RECEIVED after it, even one whose payload is
backdated.** The test is when the vendor received the report, not what the
report says about itself — otherwise a compromised key's holder could evade
revocation simply by writing an earlier timestamp.

Retirement and revocation are different: a `retired` key stops authenticating
but stays attributable for what it already signed. Revocation is terminal; a
revoked `key_id` is never reinstated, because reinstating it would retroactively
re-trust everything it can sign.

### 7. Admin-submitted acknowledgements remain evidence only

A platform admin is not a deployment. Admin-submitted acknowledgements stay
`unverified_identity` and activate nothing. This is not a limitation to be
lifted later — it is the same rule as everywhere else in this ADR: `active`
means the data plane committed, and no third party can attest to that.

### 8. Ownership

| Plane | Owns |
|---|---|
| **Fleet / deployment owner** | The deployment entity, and credential binding + lifecycle against it |
| **Kernel** | The applied-state envelope contract, its serialization, and conformance vectors — **no production key custody** |
| **Starter / product receiver** | Private-key loading, signing, and the transactional outbox that publishes applied state |
| **`EntitlementProjectionService`** (vendor) | Consumes the proven identity and applies its existing digest/version/binding rules |

The kernel ships the contract and the vectors that prove both planes agree; it
ships no signer for production use, exactly as it ships no licence signer. The
vendor consumes a proven identity — it does not invent one.

## Consequences

- The `active` state becomes reachable in production for the first time, and
  only through a cryptographically proven identity.
- The keyring-uptake-lag and revocation-application-lag alerts become
  measurable, because a signed report carries `keyring_generation` and
  `revocation_list_version` — facts the vendor cannot otherwise infer, since
  "we published it" says nothing about what a deployment holds.
- Onboarding a deployment gains a step (challenge/response) that cannot be
  skipped. This is intended friction: it is the only moment possession is
  proven.
- A deployment that loses its private key cannot report applied state until a
  new key is registered and challenged. It keeps running on its existing
  licence — identity loss must not become a service outage.
- Production activation additionally waits on the issuance host and signing-key
  path being named. The implementation proceeds behind configured knobs; the
  deployment decision is separate and explicit.

## References

- `docs/superpowers/specs/2026-07-17-starter-consolidation-design.md` — WS8
- `dotmac_kernel.licensing` — `ReceiverAppliedState`, `LicenceAcknowledgement`
- `dotmac_vendor_control_plane/docs/design/licence-service.md` — the fail-closed
  ingestion rules this ADR unblocks
- ADR-0004 — platform control plane and platform-actor identity
