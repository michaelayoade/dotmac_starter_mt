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

`0.1.0a2` is an unpublished successor proposal, held pending review and
Michael's source/release authorization after acceptance of the Kernel a105
publication record. It preserves a1 runtime behavior, public API and migrations,
with the same Kernel floor (`>=0.1.0a89`), and requires a fresh build and modern
release provenance under the new version. The legacy a1 release is not reissued
or relabeled. Do not treat a2 as available for adoption; see the
[unreleased changelog](CHANGELOG.md).

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

## The per-account access hold and R10 holds (0.1.0a3, GAP-4)

An overdue postpaid invoice opens a case at its due date (the assembly routes
`collections.exposure.due.v1` back to `assess`), runs the notice ladder, and turns
the account's service off at `access_hold_from`, which the assembly's reader
computes (`due_at` plus the account's grace) and carries on
`ReceivableObservationV1`. Collections owns the decision and the per-account state;
it never touches service access. It emits `collections.subject.access_hold.v1` when
a subject enters or leaves `held`, and the product writes the access signal
(COLLECTIONS_HOLD) from it.

- A subject is `held` when some active case has `access_hold_from <= now` and no
  live collection hold; with a live hold it is `suppressed` (service on, reminders
  paused); otherwise `clear`. `get_collection_subject_state` reads it.
- `CollectionHoldService.place` / `.lift` write insert-only holds keyed by an opaque
  `external_ref` (`<kind>:<id>`, at most 255 characters). The same live reference on
  the same subject returns the existing hold; on another subject it is refused. A
  lift of a hold that is not live is a quiet `HoldNotLive`, because the caller's
  event can be redelivered. Pass the `CollectionsTimer` to `lift` so the ladder is
  re-armed.
- Subject convention: `subject_ref = "billing-account:{uuid}"` everywhere.
- Approval of staff-placed holds is optional and off by default; Collections stores
  only the `approval_ref` the caller obtained.

Duties of the caller (the assembly / Sub-thin):

1. Route `billing.receivable.exposure.v1/v2` and `collections.exposure.due.v1` to
   `assess` (the timer entity id is `{source_owner}:{exposure_ref}`), each in its own
   transaction, with one policy per `collection_timing` (reason `invoice_overdue`).
2. Route `collections.case.access_hold_due.v1`: read the case with
   `get_collection_case(case_id)`, run the money-flows balance pass
   (`apply_balance_on_invoice_issued` for its invoice) in its own earlier
   transaction, then call `process_access_hold_due`. Only that command engages the
   hold, so no other command can cut service before the balance pass. Pass the fired
   `collections.exposure.due.v1` trigger to `assess(exposure_wake=...)`. A technical failure raises and is
   redelivered; there is no staff queue.
3. Consume `collections.subject.access_hold.v1`: write COLLECTIONS_HOLD on every
   postpaid service of the account; on service activation or transfer call
   `republish_subject_state` and apply the event it emits (a fresh version, so SAP does
   not drop it as STALE),
   drop a STALE reply and park a CONFLICT with an ops alert.
4. Place and lift R10 holds for approved payment arrangements and service
   extensions.
