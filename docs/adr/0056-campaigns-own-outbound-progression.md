# ADR-0056: Campaigns own outbound campaign progression

> **Renumbered 2026-08-23.** This record was accepted as ADR-0032 on
> 2026-08-18 and collided with ADR-0032 *Unobserved is UNKNOWN, never
> ABSENT*, accepted 2026-08-16. Resolved chronologically, the same rule
> Michael applied to the ADR-0010/0011 collision on 2026-08-15: the earlier
> record keeps the number. Every citation moved with this file in the same
> change, so no reference points at the wrong decision.

- Status: Accepted
- Date: 2026-08-18
- Deciders: Michael
- Supersedes: none
- Related: ADR-0006 (product-first extraction), ADR-0014 (at-most-once),
  ADR-0024 (application/module independence), ADR-0031 (sealed authority
  cutovers), `docs/inventories/campaigns-sources.md`

## Context

Sub and CRM each implement campaign rows, audience selection, scheduling and
delivery coupling. Their superficially similar tables hide different and
sometimes conflicting authority: both query product identities directly, both
carry provider/Inbox coupling, CRM creates Leads, and each owns a scheduler.
Sub nevertheless has the qualifying production behavior and parity suite:
suppression races, ordered nurture sequencing, send windows, sender selection,
financial-cohort boundaries and outbox delivery.

The broader marketing programme named Backoffice first. For this vertical that
would build a fresh shared implementation beside a qualifying source product,
contrary to ADR-0006's product-first amendment.

## Decision

### 1. One tenant-only campaigns owner

`dotmac-campaigns` owns provider-neutral outbound campaign identity and
progression: immutable revisions, audiences and recipient snapshots; one-time
and nurture steps; send-window decisions; recipient-step state; eligibility
receipts; delivery intents and normalized outcome/engagement observations;
unsubscribe requests; response/correlation facts; rebuildable counters, drift,
reconciliation and privacy repair.

V1 is tenant-only. The fleet audit found no named platform-plane campaign
consumer. A future platform plane requires a real current adopter and an ADR;
absence of `tenant_id` never infers one.

### 2. Campaigns consume facts; they do not discover another owner's state

Assemblies submit typed audience candidates with source owner, version,
fingerprint, opaque subject reference, destination snapshot and eligibility
reason. The module never queries Party, subscriber, customer, invoices, orders,
subscriptions, collections, Inbox, Sales or media tables and never imports a
sibling business module.

The kernel consent ledger decides eligibility. Campaigns records the receipt and
revalidates at audience creation and before every delayed step. The delivery
adapter performs the final consent check immediately before transport and
returns its receipt; a later denial always wins.

Campaigns may emit response and conversion-correlation facts. It does not
create/advance a Lead or decide attribution; an assembly asks Sales.

### 3. Mechanics retain their existing owners

- Durable Timers owns generation, due-work delivery, supersession,
  cancellation, acceptance, retry and dead-letter mechanics. Campaigns declares
  deterministic identities/purposes through an assembly port and ships no scan
  scheduler or competing ledger.
- Kernel outbox owns publication. The campaign delivery intent and outbox event
  commit together. Campaigns performs no provider I/O or retry.
- Integrator owns provider credentials, wire translation and normalized
  observations. Provider campaign ids remain provenance, never domain identity.
- Template Studio owns templates/rendering. An assembly adapter supplies an
  exact rendered revision/fingerprint; campaigns retains only the bounded
  snapshot required to make replay and repair deterministic.
- Inbox owns messages/conversations. Sales owns Leads/opportunities/conversion
  decisions. dotmac_mkt owns editorial/media campaigns and advertising
  hierarchy.

### 4. Immutable evidence and repairable projections

Once a campaign starts sending, its active revision, steps, audience and
recipient snapshots cannot be edited. A recipient/step identity is unique per
tenant. An unresolved predecessor blocks every successor. Cancellation prevents
new delivery intents but deletes no historical evidence.

Delivery/open/click/reply observations are append-only, source-deduplicated and
fingerprinted. A changed replay is a conflict. Engagement is orthogonal to the
delivery state, and a lower-precedence delivery observation cannot regress a
terminal state. Counters are cached projections rebuilt from recipient facts;
drift is reported before repair.

Retention is explicit. Campaign, recipient and rendered-content records carry
deadlines. Bounded privacy repair scrubs PII while retaining hashes, lifecycle
and aggregate evidence; there is no implicit indefinite-retention default.

### 5. Sub first, Backoffice second

Sub is cutover 1 because qualifying behavior and tests must become the shared
implementation's source and Sub must retire its local writer. Backoffice is
cutover 2 because its empty starting point is independent proof that the same
released contract composes without a Sub branch. CRM follows with its tracking
parity and local-writer retirement. dotmac_mkt remains independent and need not
install this module.

Sub's authority switch follows ADR-0031: final comparison and switch occur in
one transaction under legacy-table write locks. A pre-produced report is only a
rehearsal. No permanent shadow writer, mirrored recipient ledger, or fallback
scan survives.

The 2026-08-18 current-head adoption recheck found a prerequisite before that
switch. Sub pins kernel `a50`, does not yet compose the kernel lineage, and its
platform-adoption ledger classifies consent, idempotency and outbox as S7+
ownership collisions. Campaigns consumes those kernel owners directly, so
neither product adapters to the legacy stores nor parallel kernel tables are an
admissible bridge. Sub must complete those recorded cutovers and compose the
exact released kernel before campaigns can be allowlisted or released.

## Consequences

- The package can be built and validated before adoption, but its dossier stays
  `audit-complete` and it cannot claim authority until Sub seals the cutover.
- Sub product-specific audience/cohort/team/sender decisions move behind typed
  assembly adapters. Behavior ports; ORM/provider coupling does not.
- Backoffice cannot be the first campaigns authority merely because it is
  greenfield; it consumes the exact release proven by Sub.
- CRM's open/click behavior becomes normalized observation parity, while direct
  Lead, Inbox and provider writes are retired.
- Durable Timers is a release/adoption prerequisite for due-work activation.
  Campaigns remains independently installable because it depends on the port,
  not the sibling package.
- Sub's kernel S7 consent/idempotency/outbox disposition and real-lineage
  composition precede the campaigns release. This keeps the product-first order
  without installing a second execution or delivery owner beside Sub's current
  stores.

## Alternatives rejected

**Backoffice first.** Correct for parts of the wider suite with no qualifying
source, incorrect here. It would let a greenfield consumer shape a contract
beside Sub's live owner and defer the required retirement.

**Port Sub wholesale.** Its direct Subscriber/invoice/team/connector queries,
Inbox writes, scan task, session ownership and public-schema tables violate the
module and transaction boundaries.

**Make consent or delivery part of campaigns.** Both already have kernel/
Integrator owners. Reimplementing them would create competing policy, retry and
provider paths.

**Treat dotmac_mkt advertising ids as campaigns ids.** Provider identity is an
external observation. Promoting it would bind the module to today's advertising
hierarchy and make replay/repair depend on a remote system.
