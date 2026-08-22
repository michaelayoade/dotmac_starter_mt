# dotmac-sales

The sales authority from a qualified Lead through an immutable accepted Quote.
It owns Pipelines, Stages, Leads, append-only origin evidence, Quote authoring,
discount history, Quote lifecycle and exactly-once acceptance.

Acceptance publishes `sales.accepted-quote.v1` with an
`AcceptedQuoteHandoffV1`. The handoff contains a stable subject reference, exact
lines and totals, and a canonical snapshot digest. It never contains or creates
a subscriber, SalesOrder, project, work order, invoice or service. The consuming
product owns every consequence beyond that boundary.

Services mutate and flush a caller-owned session. Acceptance reuses the kernel
idempotency ledger and default transactional outbox adapter. Source authority
and port deltas are in `EXTRACTION.toml`, the sales inventories and ADR-0033.

Status: audit-complete and implementation-candidate. Sub remains authoritative
until backfill, shadow comparison, reconciliation and writer cutover pass.
