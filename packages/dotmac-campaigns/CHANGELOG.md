# dotmac-campaigns changelog

## 0.1.0a1+dev — source preparation, unpublished

- Typed dated occurrences for one-time greetings with an explicit IANA zone.
- Optional revision-owned delivery deadlines, distinct from retention.
- Deadline checks before intent publication and immediately before transport;
  expiry cannot overwrite earlier terminal delivery observations.
- Nullable ca_0002 expansion preserves existing unbounded campaigns.
- ca_0002 also repairs the shared snapshot trigger's table-specific field
  access while preserving frozen snapshots and recipient privacy scrubbing.

This is not a bare successor allocation, package release, product adoption or
Selfcare deployment. Git-hosted CI and product cutover evidence remain required.

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `ae5369c0` by release run
`32484270372`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

Initial tenant-only extraction from Dotmac Sub, with CRM tracking/correlation
parity and explicit retirement evidence.

- Immutable campaign revisions, steps, audiences and recipient snapshots.
- One-time/nurture progression through a typed Durable Timers seam.
- Kernel-consent receipts at audience, delayed-step and final-delivery gates.
- Kernel-outbox delivery intents with missing-publication reconciliation.
- Deduplicated delivery/open/click/reply observations and monotonic projections.
- Explicit PII/evidence retention, privacy scrubbing and rebuildable counters.
- Typed renderer, sender and timer ports plus fakes/conformance checks.
