# ADR-0032: Collections assesses an identity and requests actions

**Status:** Accepted
**Date:** 2026-08-18
**Decision owner:** Michael
**Scope:** `dotmac-collections`, its composing applications, and the Sub-first
authority migration.
**Amends:** ADR-0020's Collections contract and initial plane profile; ADR-0030
build-order step 11. It does not alter Billing's receivable ownership or the
durable-timer owner's boundary.
**Evidence:**
[`collections-sources.md`](../inventories/collections-sources.md),
[`collections-extraction-dossier.md`](../inventories/collections-extraction-dossier.md),
and Sub at `d1a1a913e287ffadaf21b7da7be448f2c28b5483`.

## Context

The 2026-08-14 Collections spec and adoption plan were written in parallel.
They left two incompatible public-contract shapes and one stale composition
assumption:

- one inbound command carried money while the other carried identity and read
  the current amount through a port;
- the outbound request was named both `ConsequenceRequestV1` and
  `CollectionActionRequested`;
- both persistence planes were required from revision 1 even though Sub is the
  only real Collections adopter and Vendor CP has no overdue case, receivables
  reader or action consumer.

Those are ownership decisions, not naming tidy-ups. They are resolved here
before a stateful package or migration lineage exists.

## Decision

### 1. `AssessCollectionExposureV1` carries no money

The versioned inbound command carries identity, explicit scope and trigger
provenance only. At minimum that means:

- command, idempotency, correlation and causal source-event identities;
- `TenantScope` or `PlatformScope`, never a nullable or sentinel tenant;
- source owner and opaque exposure reference;
- opaque subject and narrowest product-effect scope references;
- collection timing and typed reason; and
- trigger kind, trigger identity and aware trigger time.

It never carries a receivable, credit, funding, balance, installment-paid or
other money amount. It also carries no authoritative position version,
position fingerprint, anchor, resolved state or policy choice; the reader and
Collections' own policy resolution supply those decision facts. A delayed
command containing money is stale by design and would turn Collections into a
second receivables authority.

### 2. `ReceivablesReader` is reread at every decision point

Collections declares a provider-neutral `ReceivablesReader` protocol, an
in-memory fake and one reusable conformance suite. The composing application
binds its current authoritative receivables owner to that protocol. Collections
imports no Billing, Subscriptions or application package and reads no sibling
table.

Every assessment, timer delivery, policy-step advance, arrangement/grace
decision, action request, closure and reopening decision rereads the current
authoritative position. `Unavailable` blocks only that case and is retryable;
`Unknown` and `AuthorityMismatch` are typed terminal evidence. There is no
"assume zero" path. Exact money uses `Decimal`-backed `Money`; floats and
epsilon comparisons are unrepresentable.

A stored money snapshot is decision evidence only. It is never exposed as the
current receivable and never substitutes for a later reader call.

### 3. The outbound name is `CollectionActionRequestedV1`

`CollectionActionRequestedV1` is an immutable, idempotent request for a
consequence from the product service that owns the affected state. It includes
the exact case, policy, step, exposure and source-version cause; declared action
code and effect scope; request fingerprint and idempotency identity; decision
instant; and any exact position snapshot used as evidence.

The request contains no provider credential, product model or transport
instruction. The application adapter maps it to a locally owned command. That
owner locks and revalidates its state, applies, refuses or defers the request,
and returns a typed receipt. Collections records that receipt as evidence. A
queue or delivery acknowledgement is not an action receipt.

`ConsequenceRequestV1` and unversioned `CollectionActionRequested` do not ship
as public aliases. One concept has one contract name.

Notice requests follow the same boundary: Collections requests a purpose and
records delivery evidence; the product communication owner decides consent,
channel, template, locale and delivery.

### 4. Collections owns policy and cases, not product or finance state

`dotmac-collections` owns:

- immutable policy versions and arbitrary ordered ladders;
- cases and exact exposure membership;
- exposure-scoped arrangements and installment schedules;
- grace with an explicit anchor and no default anchor;
- notice and action requests, typed receipts and append-only evidence; and
- reconciliation, closure and reopening.

It owns no invoice, payment, allocation, receivable balance, subscription,
service/access state, notification delivery, provider integration, statutory
accounting or durable-timer infrastructure. One named service writes every
decision and transition.

### 5. The initial package is tenant-plane only

Sub is the qualifying product-first source and first real adopter. The first
released manifest therefore declares only tenant tables. That declaration is
the explicit plane contract; `platform_tables` is empty. Because revision one
has one atomic plane rather than selectable subsets, ADR-0028 and the current
kernel correctly reject an assembly `ModulePlaneSelection` for it. Every tenant
table has `tenant_id UUID NOT NULL`, tenant-composite uniqueness and foreign
keys, and `ENABLE` plus `FORCE` RLS, policy and exact grants in its creating
migration.

No platform table ships merely because Vendor CP might adopt later. Vendor's
platform plane is an additive module release only after the checked-in demand
gate is met by a real authoritative receivables reader, a real overdue
exposure, and named notice/action consumers. That release declares platform
tables, their grant/revocation contract and supported plane selections in the
same change. No foreign key ever crosses the planes.

This amends ADR-0020's statement that Collections declares both planes from
revision 1. It applies hard rule 27: two planes require two real adopters today.

### 6. Durable timers remain a separate owner

Collections declares only the timer port, fake and conformance suite it needs.
Applications bind that port through local assembly to the released
`dotmac-durable-timers` contract. Collections never imports that sibling module
and contains no due-row scanner, claim loop, lease, retry engine or scheduler.

Pure schemas, ports, fakes, policy evaluation and persistence can be developed
without inventing timer infrastructure. Timer-backed behavior, shadow parity
for due steps and any live cutover remain gated on the separate timer module's
release and adoption.

### 7. Sub migrates by evidence, never by dual authority

Sub shadows local and module decisions for a classified cohort and compares
cases, steps, due instants/generations, notices, arrangements, grace, action
requests/receipts, closure and reopening. A bounded cohort cuts over only after
clean reconciliation and stale timer generations are proved unable to execute
product effects.

Retirement is complete only when two-directional ratchets with sensitivity
proofs reach zero for `dunning_runner`, `prepaid_balance_sweep`, direct invoice,
credential, Subscription and access writers, and every displaced Collections
model, service, job and table. Historical retention is not authority.

## Consequences

- Current money can never arrive through an assessment command.
- A delayed action cannot proceed without rereading the current authority.
- Product consequences remain requests with owner receipts.
- The initial lineage pays only for the plane with a real adopter.
- Collections can resume contract and tenant-first implementation work, while
  timer-backed cutover remains correctly gated on `dotmac-durable-timers`.

## Rejected alternatives

**Carry money on `AssessCollectionExposureV1`.** It becomes stale before a
delayed decision and creates a second balance authority.

**Import Billing or the timer module.** Sibling imports make the module
non-composable and move assembly authority into a package.

**Ship an unused platform plane.** Empty platform tables prove neither behavior
nor adoption and violate the real-adopter requirement.

**Let Collections execute consequences.** It cannot revalidate product-owned
state and would create parallel Subscription, access and licence writers.
