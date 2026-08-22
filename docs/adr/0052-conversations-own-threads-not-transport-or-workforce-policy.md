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
