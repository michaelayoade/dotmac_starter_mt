# Changelog — dotmac-service-changes

## 0.1.0a1 — 2026-08-22

- Extracts Sub's `SubscriptionChangeRequest` boundary
  (`app/models/subscription_change.py`, `app/services/subscription_change_execution.py`).
- Keeps Sub's confirmation-key idempotency so a double-submitted change opens
  one request.
- Replaces the per-collaborator nullable FK columns with typed append-only
  checkpoints carrying domain, evidence reference, facts and observation time.
- Enforces the execution chain in one place: `advance_execution` accepts only
  the next declared state, or an explicit failure.
- Creates two directly tenant-scoped, forced-RLS tables.
