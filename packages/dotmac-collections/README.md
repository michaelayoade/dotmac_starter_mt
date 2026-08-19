# dotmac-collections

Tenant-scoped owner of delinquency policy, collection cases, arrangements,
grace, consequence requests and their immutable receipts. It rereads a typed
receivables owner before every decision and asks product owners to apply
consequences. It owns neither receivable money nor product state, delivery,
timers, provider transport, or accounting.

The first release is tenant-only. `dotmac_sub` is the candidate first adopter;
no production authority switch is part of this package change.

## Canonical persistence path

`dotmac_collections.service` provides the only module writers for policy
publication, case assessment and lifecycle, arrangements and their settlement
evidence, grace evidence, notice/action requests and owner receipts, and
reconciliation observations. The public commands and results are frozen,
fully typed contracts. Every writer receives the caller's `Session` and an
explicit `TenantScope`, uses the kernel idempotency ledger, and mutates and
flushes without committing or rolling back.

Case assessment rereads `ReceivablesReader` at decision time. Unavailable,
incomplete or non-authoritative reads block the case; stale source versions and
changed fingerprints conflict. Collections persists the exact typed position
snapshot as decision evidence, but owns no mutable balance and performs no
invoice, settlement or allocation arithmetic.

The tenant migration and live PostgreSQL canaries prove FORCE RLS,
cross-tenant refusal, concurrent first-case convergence and immutable policy
publication with fresh reopen after resolution. Adoption is still pending:
Sub must supply the typed Billing, Durable Timers and product-consequence
adapters, backfill and shadow-compare its live cohorts, and pass the retirement
ratchets before any authority switch.
