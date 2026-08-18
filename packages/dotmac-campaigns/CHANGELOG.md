# dotmac-campaigns changelog

## 0.1.0a1 — UNRELEASED

Initial tenant-only extraction from Dotmac Sub, with CRM tracking/correlation
parity and explicit retirement evidence.

- Immutable campaign revisions, steps, audiences and recipient snapshots.
- One-time/nurture progression through a typed Durable Timers seam.
- Kernel-consent receipts at audience, delayed-step and final-delivery gates.
- Kernel-outbox delivery intents with missing-publication reconciliation.
- Deduplicated delivery/open/click/reply observations and monotonic projections.
- Explicit PII/evidence retention, privacy scrubbing and rebuildable counters.
- Typed renderer, sender and timer ports plus fakes/conformance checks.
