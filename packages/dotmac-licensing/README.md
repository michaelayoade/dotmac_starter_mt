# dotmac-licensing

The **issuer** half of WS8: signed, versioned, revocable software-use authority
derived from an active agreement's allocated entitlement.

Extracted product-first from the vendor control plane's `licensing/` service
under [ADR-0057](../../docs/adr/0057-the-vendor-control-plane-composes-existing-owners.md) § 2.
Source inventory: [`vendor-cp-gap-sources.md`](../../docs/inventories/vendor-cp-gap-sources.md) § 2.
Ownership record: [`EXTRACTION.toml`](EXTRACTION.toml).

## Two rules that shape everything else

**1. The module names signing material; it never holds it.**

`LicenceSigner` is a protocol and this distribution ships **no implementation of
it** — not an ephemeral one for tests, not a file reader, not a null object. The
`signing_keys` table has no private-key column. A wheel, a source checkout, a
database dump, a replica and a stack trace are all *structurally* incapable of
leaking a key, rather than prevented from it by policy. Custody is the product's,
through `dotmac_kernel.secret_sources` (ADR-0009, hard rule 20).

The source implementation shipped an `ephemeral` in-memory keypair as the default
mode. That is correct for a *product* — its own docstring says the default exists
"because a missing configuration must not silently become a real issuer" — and
wrong for a *library*, where a default ships to every consumer.

**2. Every envelope is round-tripped through the kernel's own verifier before it
is recorded, and a failure is fatal.**

The kernel is the receiver. If the receiver would reject the document, the issuer
must not record it. One verification per issuance guarantees something no amount
of shared test data can: that the two halves of one protocol have not drifted.

## Lifecycle

```
                        ┌──── reinstate ────┐
                        ▼                   │
  (grant) ── issue ─► issued ── ack ──► active ── suspend ──► suspended
                        │                   │                     │
                        └──── suspend ──────┘                     │
                        │                   │                     │
             revoke / expire  ◄─────────────┴─────────────────────┘
                        │
      revoked | expired ▼          issue(next) ──► previous becomes replaced
```

`revoke` acts on the whole **lineage**, not one version — revocation is by
`licence_id` in the wire protocol, so a receiver cannot act on "version 3 is
revoked". It is permanent; recovery is re-issuance under a new **generation**,
which `issue_licence` mints automatically.

## Usage

```python
from dotmac_licensing import (
    IssueCommand, LicensableGrant, LicensedCapability, issue_licence,
)

view = issue_licence(
    db,
    IssueCommand(
        command_id="cmd-1",
        grant=LicensableGrant(
            subject_ref="acme-operator",              # opaque
            product_code="dotmac_sub",
            capabilities=(
                LicensedCapability("subscriber.manage", {"quantity": 500}),
            ),
            agreement_ref=str(agreement.id),          # opaque; no FK
            allocation_ref=str(allocation.id),        # opaque; unique per issuance
            valid_until=datetime(2027, 8, 31, tzinfo=UTC),
            grace_days=14,
            deployment_ref="acme-lagos-1",            # omit for a portable licence
        ),
    ),
    signers=(primary_signer,),                        # you implement LicenceSigner
)
integrator.deliver(view.envelope)                     # this module does not deliver
```

## Diagnosing a licence

```python
result = inspect_issued_envelope(db, envelope, now=datetime.now(UTC))
# InspectionResult(valid=False, reason="LicenceExpiredError", detail="…")
```

Runs the caller's envelope through the *same* kernel verifier a receiver runs,
against the live keyring and the live revoked set. It never raises for an invalid
licence — a verification failure **is** the answer, so raising would move the
diagnosis into the caller's exception handler.

## Composition

- **Platform plane only**, and here the plane is a security boundary rather than
  an absent consumer: issuance must not live inside the deployment it authorises.
  The receiving half already verifies **offline** through
  `dotmac_kernel.licensing`.
- **Imports no sibling module.** Agreement and allocation facts arrive as a
  `LicensableGrant` value from the assembly (ADR-0024).
- **No delivery.** Transport, connection refs, attempt counters, retry outcomes
  and error codes are the Integrator's (hard rule 28). This module ends at a
  signed envelope and resumes at an acknowledgement.
- **Transaction authority is the caller's** — `add` and `flush` only (hard rule 8).

## The cumulative revocation rule

Every published revocation list must be a **superset** of the one before it.
Version monotonicity alone does not prevent un-revocation: a higher version that
quietly omits an earlier id restores access while looking perfectly well-ordered
to every receiver, and no deployment would report anything wrong. The check
compares against the previous **published** list's own signed payload — comparing
against the current table would be comparing a set with itself and would make the
guard vacuous.

## Published facts

`licence.{issued,activated,suspended,reinstated,revoked,expired,acknowledged}.v1`
and `licence.revocation_list.published.v1` — read `PUBLISHED_EVENT_TYPES` rather
than keeping a hand-written list.

**No fact carries a signed envelope.** Payloads name `licence_id`,
`licence_version` and `digest` and stop; the envelope is fetched from the issuance
by whatever will deliver it. Putting a signed document in an outbox row copies it
into every relay log, dead-letter dump and consumer's storage — and a signed
licence is exactly the artifact that grants authority wherever it lands.

## Status

**Built and validated, not adopted.** No product runs it yet. See
`EXTRACTION.toml` for what the vendor cutover owes — including that envelopes
must migrate **byte-for-byte** (re-serialising changes the digest and invalidates
the signature) and that the revocation-list lineage's cumulative rule spans the
cutover.
