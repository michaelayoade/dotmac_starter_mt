# Changelog — dotmac-billing

All notable changes follow Semantic Versioning.

## 0.1.0a1 — UNRELEASED

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
