# dotmac-collections

Scope-explicit owner of delinquency policy, collection cases, arrangements,
grace, consequence requests and their immutable receipts. It rereads a typed
receivables owner before every decision and asks product owners to apply
consequences. It owns neither receivable money nor product state, delivery,
timers, provider transport, or accounting.

The first release supports independently selectable tenant and platform planes
through one lifecycle and one service surface. `dotmac_sub` remains the
candidate first adopter on the tenant plane and the Vendor Control Plane is the
candidate second adopter on the platform plane; no production authority switch
is part of this package change.

## Canonical persistence path

`dotmac_collections.service` provides the only module writers for policy
publication, case assessment and lifecycle, arrangements and their settlement
evidence, grace evidence, notice/action requests and owner receipts, and
reconciliation observations. The public commands and results are frozen,
fully typed contracts. Every writer receives the caller's `Session` and an
explicit `Scope`, selects the matching persistence and kernel idempotency plane,
and mutates and flushes without committing or rolling back.

Adopters consume the supported names re-exported from `dotmac_collections`:
typed observations/readers, policy and case commands, request/receipt values,
service owners, arrangement/grace contracts and the timer port. ORM models are
not part of that public surface and must not become an assembly integration
seam.

An accepted notice or action request writes its Collections evidence and one
`collections.notice.requested.v1` or `collections.action.requested.v1` kernel
outbox row in that same caller-owned transaction. Replaying the immutable
request creates neither a second request row nor a second delivery intent. The
tenant path uses the tenant outbox and the platform path uses the platform
outbox; the relay owns delivery, leases, retry, backoff and dead letters.

Case assessment and every neutral timer wake-up reread `ReceivablesReader` at
decision time. Opening or replanning an active case schedules its exact
`collections.case.step_due.v1` timer generation; resolution, cancellation or
pause cancels that identity in the same caller-owned transaction. The timer
payload carries only case identity, due time, generation and expected source
version. `CollectionCaseService.process_step_due` accepts the current
generation, rereads the position, resolves the pinned policy step and writes
the action/notice evidence plus kernel outbox intent atomically. A stale,
canceled or duplicate generation cannot authorize a second effect.

Each immutable policy step owns the notice purpose or action/effect scope it
may request, whether an owner receipt is required, and the ordered retry
offsets for retryable owner failures. An accepted receipt advances to the next
step and schedules its exact declared anchor; a retryable failure schedules
only the next policy offset. A transient receivable-source outage emits no
consequence and replaces the case timer at the source-provided retry instant.

The input to both paths is the
peer-owned `ReceivableObservationV1`, never a second
`ReceivablePositionV1`. The observation contains only the already-funded
collectible amount plus the financial state and provenance Collections needs;
available credit and prepaid funding remain Billing facts. Unavailable,
incomplete, shadow, unknown-due-date and not-yet-due reads block action; stale
source versions and changed fingerprints conflict. Collections persists the
exact typed observation as decision evidence, but owns no mutable balance and
performs no invoice, settlement, allocation or financial-resolution arithmetic.

The migration and live PostgreSQL canaries prove tenant FORCE RLS and
cross-tenant refusal, platform reachability with complete `app_user` revocation,
no cross-plane foreign keys, concurrent first-case convergence and immutable
policy publication with fresh reopen after resolution. Adoption is still
pending: Sub must supply the typed Billing, Durable Timers and
product-consequence adapters, backfill and shadow-compare its live cohorts, and
pass the retirement ratchets before any authority switch. Vendor Control Plane
must compose and prove its platform adapters separately.

The `CollectionsTimer` port requires the caller-owned SQLAlchemy session on
schedule, cancellation, trigger acceptance and current-generation reads. This
lets the assembly bind Durable Timers without a second transaction. Its one
neutral wake-up vocabulary is `collections.case.step_due.v1`. Assemblies may
omit the port only for explicit command-driven operation; a production Cloud,
Sub or Vendor Control Plane binding must adapt this port to
`dotmac-durable-timers` without moving the policy decision into the adapter.
