# ADR-0052: Conversations own threads, not transport or workforce policy

- **Status:** Accepted
- **Date:** 2026-08-18
- **Decision owner:** Michael
- **Scope:** FLEET-WIDE. Applies to every Dotmac application that records staffed
  external conversations and to the reusable `dotmac-inbox` module.
- **Relates to:** ADR-0006 (product-first extraction), ADR-0008 (declaration
  registries), ADR-0014 (at-most-once execution), ADR-0017 (adoption is scarce),
  ADR-0024 (applications synchronize data), ADR-0031 (cutover evidence)
- **Evidence:** [`inbox-sources.md`](../inventories/inbox-sources.md)

## Context

Sub owns the fleet's active staffed inbox. CRM's similar implementation is a
retirement source under the CRM-to-Sub ledger, not an independent consumer.
ERP has no inbox, but its support ticket records email-correspondence fields
without a durable message record, making it a credible second candidate.

Most of the existing inbox surface is not a conversation aggregate. Provider
clients, webhook authentication, connector accounts, retries, contact
resolution, routing, teams, assignment, presence, media, realtime, AI intake,
ticket handoff and ISP consequences each have a different owner. Extracting the
whole surface would reproduce Sub behind a package name.

Both Sub and CRM also branch on channel names to decide threading and message
identity. CRM encodes one such list in a partial unique index and contradicts it
with another index; Sub assumes provider message ids are globally unique for
every channel. The reusable mechanism is a declared channel with fixed traits,
not a merged channel enum.

## Decision

### 1. `dotmac-inbox` owns the local conversation aggregate

Every adopter installs its own tenant-only `ib` lineage and owns its local
rows. The module owns:

- conversation identity and one canonical thread key;
- ordered inbound, outbound and internal message records;
- open, pending, snoozed and resolved lifecycle transitions;
- product-declared status reasons and free-form tags;
- channel-trait threading and message identity; and
- a monotonic per-operator read cursor using an opaque actor id.

The service validates, mutates and flushes inside the caller-owned transaction.
It never commits or rolls back. A new inbound message may reopen its resolved
thread; products decide all other consequences.

### 2. Channel names are open; behavior traits are fixed

Products declare channel codes. Conversation behavior reads only address form,
transport kind, thread-identity source and message-id scope. No channel name
may appear in a module conditional. Status remains a closed four-value
vocabulary; product-specific terms such as `resolved_to_ticket` are declared
reasons, not extra lifecycle states.

The initial registry remains inside `dotmac-inbox`. Consent and outbound
channel policy retain their existing kernel contracts. The repeated generic
declaration-registry mechanism remains an explicit fleet candidate rather than
being promoted into the kernel by this extraction.

### 3. Integrator owns transport; products own domain ingress

`dotmac-integration` and connector plugins own provider credentials, raw wire
evidence, signature verification, installations, bindings, transport inbox and
outbox receipts, retry, checkpoints, health and repair. They call a product's
authenticated, versioned domain port and never write `mod_inbox`.

An inbox message may retain opaque transport message/observation references for
correlation. It stores no raw provider payload, provider credential, connector
configuration, transport processing state or delivery retry state. The product
normalizes the accepted domain command and delegates the conversation decision
to the module.

### 4. Products own identity, workforce policy and consequences

The module has no subscriber, customer, person, lead, ticket, team, queue,
assignment, presence or attachment foreign key. An adopter owns local relations
from those subjects to its local conversation id. Contact resolution, routing,
assignment, reply-window policy, delivery, files, realtime, templates, AI,
audit consequences and product notifications stay with their named owners.

### 5. Sub is cutover 1; publication follows adoption proof

Sub is the qualifying source and first cutover. It shadows conversation,
message, lifecycle, threading and read-cursor commands in the same transaction,
classifies expected message-id-scope differences, proves forced RLS on
PostgreSQL and seals a one-writer switch. Its product policy remains outside the
module. CRM retires through that existing consolidation path.

ERP is cutover 2 only if its support correspondence adopts the released
contract. Until Sub's cutover is ready, Starter builds and proves the
audit-complete package but neither composes its lineage in the reference
assembly nor makes it publishable.

## Consequences

- The fleet matrix target for the conversation-record slice becomes
  `dotmac-inbox`; contact-centre workforce behavior does not move with it.
- Account-scoped message identities stop legitimate messages at different
  connected accounts from colliding.
- Tenant scoping, composite parent keys and forced RLS exist in revision 1 even
  though neither source schema currently provides them.
- Transport cutover evidence and module cutover evidence remain separate and
  both are required.

## Alternatives rejected

### Extract Sub's whole Team Inbox

Rejected because it would transfer ISP identity, workforce, transport and
product policy into the shared owner.

### Put conversations in `dotmac-integration`

Rejected because Integrator owns transport evidence across applications, while
each application owns its local domain conversation and consequences.

### Keep independent Sub and ERP message records

Rejected because ERP's missing correspondence record is the candidate demand,
and the product-neutral thread/message/lifecycle mechanism has one qualifying
source already.

## Amendment — 2026-08-23: adoption preserves identity through an owner seam

The first Sub cutover established that a runtime `create_*` command cannot also
be the historical adoption contract. Runtime creation correctly mints a new
identity and may apply live consequences; adoption must keep the product's
existing UUID because its relations, URLs and saved views already name it.

`dotmac-inbox` therefore owns typed, flush-only history commands for
conversation, message and read-cursor rows. `dotmac-inbox-operations` owns the
equivalent commands for the operational rows Sub already identifies. These
commands preserve source UUIDs and timestamps, validate the module's canonical
identities and structural invariants, replay only an exact existing row and
fail closed on same-id/different-fact or natural-identity collisions. They do
not apply live transition, routing, promotion, release or reopen consequences.

The Sub backfill already in flight is the sole temporary exception while these
exact package versions are unpublished. It stays bounded to one file, checks
exact replay and has a version-sensitive gate that forces retirement as soon as
the released seams are pinned. It is not precedent. Every later adopter uses
these module-owned seams. Whether the same rule binds every other Dotmac module
remains a fleet-governance decision; this ADR does not silently widen its own
scope.

## Amendment — 2026-09-05: explicit indefinite snooze

The lifecycle contract distinguishes a finite snooze from “until reply”. A
caller must pass the typed `SnoozeUntilReply` value (exported as `UNTIL_REPLY`)
to request an indefinite snooze; an omitted `snoozed_until` remains invalid.
The existing nullable column persists that explicit form as
`status=snoozed, snoozed_until=NULL`, while a finite snooze keeps its
timezone-aware deadline. Only inbound message activity owned by
`dotmac-inbox` wakes an indefinite snooze; timed wake scheduling remains a
product responsibility. The same validation, normalization, exact replay and
conflict rules apply to the history import seam. No migration or scheduler is
introduced by this amendment.

## Amendment — 2026-09-05: late transport-observation correlation

An Integrator observation may be correlated after a local message is admitted.
The Inbox owner therefore exposes `bind_message_observation_ref`, which locks
the tenant-scoped message identified by its local UUID and flushes only
`transport_observation_ref`. A missing or cross-tenant message is not visible
and raises `ConversationNotFound`; an empty reference is rejected; an exact
existing reference replays; and a different existing reference raises
`ConversationConflict`. `transport_message_ref` remains part of admission
identity and is never late-bindable. The command does not alter message
identity, content, direction, conversation activity, lifecycle, or delivery
state, and never commits or rolls back.

## Amendment — 2026-09-05: products may supply stable local identities

Some product events have no provider identity at all, yet have a stable local
thread and message reference. `dotmac-inbox` accepts these as declared
`SUPPLIED` thread and message identity traits. The supplied values are opaque,
bounded product-owned references; the module persists them separately from
transport references and derives canonical scoped keys without treating either
as provider evidence.

This does not introduce a subscriber, person, ticket, or principal relation:
the product retains those relationships and maps its own work records to a
conversation. A product using supplied message identity must retain and reuse
the same logical message reference for redelivery. This closes the repeated
identical internal-message mechanism, but does not prove any product mapping or
cutover, and does not transfer provider or delivery authority to the module.

## Amendment — 2026-09-05: typed tenant read owner

The module also owns the read contract for its aggregate. Products use frozen
typed DTOs for tenant-scoped conversation get/list and ordered message timeline
reads; they do not receive ORM rows or provide SQL predicates. Conversation
lists expose only status, channel, and account-scope filters. Both list reads
use bounded opaque keyset cursors with a stable UUID tie-break; cursor scope
includes the tenant and query identity (including conversation id or filters),
so a cursor cannot be reused across tenants, conversations, or changed filters.
Conversation ordering keeps NULL activity timestamps after non-NULL timestamps,
and timeline ordering is ascending by occurrence time. Cursor timestamps are
validated as timezone-aware. Search, unread counts, assignment, operations,
and workforce policy remain outside this owner.
