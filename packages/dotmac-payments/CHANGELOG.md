# Changelog — dotmac-payments

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
