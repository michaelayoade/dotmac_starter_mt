# Changelog — dotmac-licensing

All notable changes to the `dotmac-licensing` distribution. This package follows
[Semantic Versioning](https://semver.org). Pre-1.0 (`0.x`, incl. this alpha) the
surface is still settling — a `0.MINOR` bump may carry breaking changes, each
called out here.

## 0.1.0a1 — 2026-08-19

First release. Product-first extraction of the vendor control plane's issuer-side
`licensing/` service (ADR-0033 § 2).

### Added

- `mod_licensing` — `signing_keys`, `licences`, `licence_issuances`,
  `licence_acknowledgements`, `revocations`, `revocation_lists` on the platform
  plane. Lineage root `li_0001_licensing`, which verifies
  `idempotency_ledger.v1` and `platform_audit_log.v1` before any DDL of its own.
- Issuance that signs the payload **once**, registers every signer's public half
  first, and round-trips the envelope through the pinned kernel verifier before
  recording anything.
- Rotation overlap as a **sequence** of signers rather than a mode flag.
- The full version lifecycle — issued, active, suspended, reinstated, revoked,
  expired, replaced — with optimistic concurrency on every transition.
- Acknowledgements checked against an issuance this module actually produced, and
  refused otherwise.
- Lineage revocation with the `generation` recovery path, and immutable published
  revocation lists enforcing the cumulative superset rule.
- `inspect_issued_envelope` — a typed inspection API that returns a verification
  failure as an answer rather than raising.

### Deliberately NOT included

- **Any `LicenceSigner` implementation.** A signer in a shared library is a
  default that ships. Custody belongs to the product (ADR-0009).
- **A `cryptography` dependency.** This package signs nothing.
- **The delivery half** — `transport.py`, `delivery_models.py` and the delivery
  portion of `projection.py` (roughly 1,600 LOC of the source). Transport,
  connection refs, attempt counters, retry outcomes and error codes are the
  Integrator's (ADR-0024, hard rule 28).
- **The deployment-credential/admission half** (`credentials.py`,
  `admission.py`). That went to `dotmac-deployment-control` (ADR-0033 § 3): a
  licence *names* a deployment, it does not *enrol* one.

### Changed from the source implementation

- `customer_ref` becomes an opaque `subject_ref`; `allocation_id` and the
  contract linkage become opaque `allocation_ref`/`agreement_ref` with no foreign
  key. ADR-0006 D1 forbids the cross-lineage FK, and a licence must stay
  verifiable after the allocation's retention has passed.
- `IssuanceStatus` gains the five states beyond `issued` the source deferred to
  its projection slice, so a licence's standing is answerable from the issuance
  itself rather than from a delivery record.
- Acknowledgements are validated against the issuance before being stored. The
  source recorded them and matched afterwards.
- The revocation-list superset check reads the previous **published** list's own
  payload rather than recomputing from the table — recomputing compares a set
  with itself and makes the guard vacuous.
- Append-only is enforced by trigger on all three evidence tables against every
  role including `app_admin`, not by withholding grants from the online role
  alone.
