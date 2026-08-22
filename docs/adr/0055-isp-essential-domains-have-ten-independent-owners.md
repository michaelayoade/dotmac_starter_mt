# ADR-0055: ISP essential domains have ten independent owners

- Status: accepted
- Date: 2026-08-20
- Decision owners: Dotmac architecture / product ownership

## Context

The thin ISP replacement needs ten capabilities that are not supplied by a
released Starter domain owner. Sub, CRM and ERP currently mix these decisions
inside product services and shared tables. Copying those tables into one generic
ISP package would preserve the monolith boundary and create parallel writers.

The exact current-source audit is
[`isp-essential-domain-sources.md`](../inventories/isp-essential-domain-sources.md).
It pins Starter `828bc0968bb68bd8401c7227ae1334366cdc4b41`, Sub
`552d0fdfce7ede36d430fe52ac1eaa4f06ee10d1`, CRM
`60daaa2dd305696636632f48505ab784110a55d2`, and ERP
`4aab56812d6fb243c814ada15c13fabea6234da8`.

## Decision

Starter supplies ten independent, tenant-plane packages:

1. `dotmac-customers` owns customer accounts, account profiles and explicit
   payer/service/contact Party references. Kernel Party owns identity;
   `dotmac-party` owns reachability, roles and Party relationships.
2. `dotmac-service-catalog` owns technical service specifications, plan
   families, characteristic definitions and eligibility-input definitions.
   Subscriptions owns commercial offers, prices, contracts, cadence and fixed
   recurring rating.
3. `dotmac-qualification` owns time-bounded serviceability evidence and the
   qualification decision. Positioning and network owners publish observations.
4. `dotmac-services` owns a customer's service instance and its activation,
   suspension, restoration and termination lifecycle. Subscriptions,
   Fulfillment, Network Access and Service Access Policy remain separate owners.
5. `dotmac-usage` owns normalized immutable metering observations, append-only
   corrections and rebuildable aggregates. Network Access retains raw AAA,
   accounting and session evidence.
6. `dotmac-usage-rating` owns metered/event rating and emits immutable typed
   pre-tax obligations. It owns no fixed recurrence, invoice, tax, FX or
   receivable state.
7. `dotmac-service-access-policy` combines typed FUP, prepaid-coverage,
   collections and administrative inputs into one desired per-service access
   decision. Network Access alone projects and enforces that decision.
8. `dotmac-inbox-operations` owns queues, routing, channel-agent presence,
   assignment and operator workflow around an opaque conversation reference.
   Core Inbox owns the conversation, message, lifecycle and read cursor.
9. `dotmac-workforce` owns field/service teams, skills, shifts, availability,
   capacity, scheduling and dispatch policy around opaque work references. It
   owns neither Inbox presence nor Work Order lifecycle.
10. `dotmac-fx-policy` owns effective-dated rate observations, source
    provenance, source-selection policy and determination evidence. Kernel owns
    immutable Money/ExchangeRate values; Billing stores applied snapshots;
    Accounting owns GL consequences.

Every package has one `mod_*` schema, one independent lineage, direct
`tenant_id UUID NOT NULL` on every table, tenant-composite identities and
internal foreign keys, same-migration ENABLE+FORCE RLS and grants, a flush-only
service, typed public contracts, and no imports from a sibling module or
product assembly. Assemblies translate immutable contracts between owners.

## Cohort allocation exception

Kernel `0.1.0a85` allocates all ten namespace identities before the ten focused
package commits. This ordering is explicitly required for this programme so
concurrent work cannot choose colliding prefixes while the packages are built.
The original isolated branch proposed Services prefix `sv`; current main had
already allocated it permanently to Surveys, so integration resolved Services
to the unused `se` prefix before publication or composition.
The allocation is not a facility, release, composition, adoption or authority
cutover. Each following package commit must declare the exact allocated identity
and provide its own migration, canaries and dossier; an unused allocation may
never be repointed or reused.

## Consequences and cutover

Package existence is supply evidence only. No package enters the reference
assembly or release allowlist in this programme. A source product adopts a
package only through an exact released pin, adjudicated backfill/quarantine,
complete shadow fingerprints, a sealed one-writer switch, rollback evidence and
retirement of every displaced local writer. Cross-application synchronization
uses versioned APIs/webhooks and durable outbox delivery; no application reads
another application's database.

## Amendment — 2026-08-22: Inbox Operations executes the decision

Inbox Operations owns a routing rule only if it can execute that rule and
retain the selected rule/queue evidence. A caller may supply provider-neutral
work attributes and a Workforce/product-derived set of opaque eligible agent
references, but it may not select the queue-promotion winner. The module
combines that eligibility with fresh Inbox presence, current assignment
capacity and its durable round-robin cursor in one locked promotion path.

Queue admission serializes position allocation on the queue row. Promotion
locks the queue/front entry and eligible presence rows before it creates the
assignment and advances the cursor. A multi-queue sweep attempts one item from
every declared queue cohort; it must not take a global oldest window in which a
saturated queue can hide another queue with capacity.

Assignments and queue entries are lifecycle evidence, not one-row-per-thread
entities. Only `ASSIGNED` assignments and `QUEUED` entries are unique by
conversation. Release, promotion and cancellation settle the active row, and
a later assignment or admission creates a new history row. Migration
`io_0003_operational_safety` and package `0.1.0a3` implement this amendment.
Workforce continues to own teams, skills, shifts and field availability; this
module stores none of them.
