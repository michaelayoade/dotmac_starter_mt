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
- Creates three directly tenant-scoped, forced-RLS tables.
