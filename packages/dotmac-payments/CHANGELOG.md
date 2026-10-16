# Changelog — dotmac-payments

## 0.1.0a3 — proposed, unpublished

- Extract existing purchase checkout as `PURCHASE_FUNDING`, requiring an exact
  opaque destination before invoices exist; reuse opening, lookup and immutable
  confirmation contracts without granting financial authority.
- Add tenant-only `pm_0004_purchase_funding` to extend the published purpose
  CHECK and protect the full original purchase checkout binding from mutation
  or deletion. Preserve old
  rows, RLS, grants and confirmation immutability; refuse downgrade with funding
  intents. Published a2 migrations and release history remain unchanged.
- Successor identity is proposed source metadata, not release allocation or
  publication approval. Hosted runtime acceptance remains pending.

## 0.1.0a2 — 2026-10-13 (published)

- Enforce confirmed-cash UPDATE/DELETE refusal at the PostgreSQL boundary,
  including deletion cascades, while preserving intent/proof lifecycle writes.
  Bound migration locking and refuse downgrade while confirmation evidence exists.

- Persists exact Money currency scale in additive `pm_0002_money_precision`;
  legacy NULL precision remains unresolved without an inferred default/backfill.
- Exposes frozen tenant-scoped confirmed-cash observations from persisted scalar
  evidence with typed missing/unknown-precision refusals and caller-owned transactions.
- Refuses unsupported scales, nonfinite amounts and Numeric(20, 6) overflow.
- Refuses confirmation replay with a changed amount, currency, source or supplied
  observation instant. An omitted instant replays the original evidence.
- Serializes confirmation, cancellation, expiry and proof submission on the
  tenant-scoped intent row, refreshing stored state after acquiring the lock.
- Reviews proofs in intent-before-proof lock order and leaves review evidence
  untouched when confirmation is refused.
- Adds migrated PostgreSQL races for duplicate/different provider references,
  cancellation, expiry, proof acceptance and competing proof reviews. SQLite
  canaries cover immutable replay and failed-review evidence.
- Preserves the released `pm_0001_payment_intents` migration and existing public
  command fields. This version changes no financial allocation or cash owner.

## 0.1.0a1 — 2026-08-22

- Extracts Sub's top-up intent, transfer-proof and gateway-observation boundary
  (`app/models/billing.py` `topup_intents`/`payments`, `app/models/payment_proof.py`).
- Correlates a settlement fact to an intent addressed by its own reference;
  provider metadata never selects the destination.
- Closes the source's partial-index gap: Sub's active `external_id` uniqueness
  required `provider_id IS NOT NULL`, leaving CRM-origin payments outside it and
  needing a second index to stop a concurrent push double-recording cash. Here
  the uniqueness is unconditional per (tenant, provider type, external
  reference).
- Refuses a confirmation whose currency differs from the intent, and refuses a
  confirmation observed after the intent expired.
- Takes the OPENING and CANCELLATION times from the caller
  (`OpenPaymentIntent.opened_at`, `cancel_payment_intent(cancelled_at=...)`),
  falling back to the wall clock only when they are omitted. `opened_at` is an
  authoritative business fact — when the payer was asked to pay — not a clock
  seam, and a history backfill carries the real one. Expiry is validated
  against the opening time rather than against the moment the caller runs, so
  a migrated intent whose whole timeline is already past is admitted on the
  strength of its ordering.
- Preserves the stored opening time on an idempotent replay, and refuses a
  replay that names a different one: two callers disagreeing about when the
  payer was asked to pay is the same class of defect as disagreeing about the
  amount.
- Creates three directly tenant-scoped, forced-RLS tables.
