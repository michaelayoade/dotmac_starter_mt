# Changelog — dotmac-billing

All notable changes follow Semantic Versioning.

## 0.1.0a2 — UNRELEASED

- Add a typed, atomic cash-backed available-credit draw across confirmed
  settlement sources, preserving allocation provenance and existing financial
  events. Non-cash grants and opening-balance funding remain separate gaps.
- Refuse allocation from reversed settlement funding while preserving exact
  replay of a previously accepted command.
- Rebuild position attestations from persisted numeric values, independent of
  ORM cache scale, preserving the hash algorithm and strict drift refusal.
- Reconcile Sub-first, pay-then-record and open-items-only cutover preparation.

## 0.1.0a1 — historical publication

Publication tag: `dotmac-billing-v0.1.0a1`, peeled commit
`92a1626b16d7e068f92536d8cfcb2ef9b6f270c2`. The historical record is in
`docs/inventories/module-release-legacy-unverified.json`; it does not constitute
a modern wheel-verification receipt or evidence of product adoption.

- First complete Billing V1: frozen obligation, settlement, receivable,
  accounting and invoice-document contracts; one pure financial engine; two
  declared persistence planes; immutable source effects and rebuild hashes.
- Every published command/fact is a closed immutable type, including nested
  party/address/payment/line/discount/accounting-effect values; architecture
  canaries reject `Any`, `object` and unshaped dictionaries at the boundary.
- Make Billing the sole owner of the account/currency `ReceivablePositionV1`
  and invoice-grained `ReceivableExposureV1`, use kernel `Money` on every public
  amount, own financial state without a `reversed` steady state, and preserve
  each exposure's subject/service, service-period and due-date provenance.
- Record exact renderer/file provenance for the Billing-owned official
  artifact relation, with a structural record key, a separate current-row-bound
  repair key, strict semantic mismatch, append-only repair supersession and
  cancellation withdrawal. Artifact creation serializes with cancellation and
  refuses a new medium after a void while preserving historical replay.
- Keep `RatedObligationOutputV1` exclusively on Subscriptions' public surface;
  Billing owns and names only its distinct `AcceptRatedObligationV1` input.
- Emits complete typed accounting and receivable-position payloads through the
  transactional outbox, including applied tax/FX and immutable service-period
  and due-date-basis evidence.
- Ships the `bi_0001_billing` migration with exact tenant RLS and platform
  revokes/grants, including column-only account lock permission, plus live
  concurrency, immutability, isolation and rebuild/hash canaries.
- Declares the exact `dotmac-kernel>=0.1.0a89` and `alembic>=1.13` floors so a
  clean wheel install can import the published migration/linking surface.
- Includes no provider connector, rendering, byte storage, cadence, collections
  policy, product consequence, numbering engine, GL or ERP posting path.
