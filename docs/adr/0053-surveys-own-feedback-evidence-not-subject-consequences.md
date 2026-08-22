# ADR-0053: Surveys own feedback evidence, not subject consequences

- **Status:** Accepted
- **Date:** 2026-08-18
- **Decision owner:** Michael
- **Scope:** FLEET-WIDE. Applies to every Dotmac application that requests,
  records or aggregates customer/employee feedback and to the reusable
  `dotmac-surveys` module.
- **Relates to:** ADR-0006 (product-first extraction), ADR-0008 (declaration
  registries), ADR-0010 (thin adapters), ADR-0014 (at-most-once execution),
  ADR-0017 (adoption is scarce), ADR-0023 (persistence planes), ADR-0024
  (applications synchronize data), ADR-0031 (authority cutover evidence)
- **Evidence:** [`surveys-sources.md`](../inventories/surveys-sources.md)

## Context

Sub and CRM each implement generic survey definitions, invitations, responses
and aggregates. Sub also stores ticket CSAT independently inside Ticket
metadata, while CRM couples conversation CSAT to channel settings, message
delivery and retry. ERP has a separate HR survey system.

Keeping these copies preserves parallel validation, response and aggregate
writers. Combining ticket, work-order, conversation, service and employee
policy into a generic package would move lifecycle and workforce decisions away
from their owners. The reusable unit is the feedback mechanism between those
extremes.

Sub's `communications.surveys` implementation is the qualifying source. It has
a checked-in SOT, typed content/lifecycle commands, focused behavior tests,
architecture guards, tracked-response uniqueness and rebuildable aggregate
projections. CRM is its retiring ancestor and supplies retry requirements. ERP
supplies second-adopter requirements but has no focused survey proof.

## Decision

### 1. `dotmac-surveys` owns product-neutral feedback mechanics

`dotmac-surveys` is a stateful, independently versioned, tenant-plane module.
Every adopting application installs its own `sv` lineage in its own database
and owns its local rows. No application reads another application's survey
schema.

The module owns:

- survey definition identity, ordered typed questions and public reference;
- the `draft`, `active`, `paused`, `closed` lifecycle and answer eligibility;
- invitation identity, bearer token, semantic source-event deduplication and
  completion state;
- authoritative answer, rating and NPS validation;
- one response per tracked invitation; and
- rebuildable invitation count, response count, mean rating and NPS
projections.

Definition content is editable only in `draft`. Once activated, changing the
questionnaire creates a new Survey identity; one lifetime aggregate never mixes
answers captured under different question meaning. While rating and NPS are
top-level aggregates, a definition may declare at most one of each question
type.

Services mutate and flush inside the caller-owned transaction. They never
commit, roll back, deliver notifications or call another application.

### 2. Subject owners decide eligibility and consequences

Ticketing, work orders, services, conversations and HR remain authoritative for
the lifecycle state that makes feedback eligible, the intended recipient,
whether rerating is permitted and every business consequence.

The module stores only opaque, bounded `recipient_ref`, `source_owner`,
`source_event_id` and optional `subject_ref` values. It has no subscriber,
customer, person, employee, ticket, work-order, service, conversation,
notification, team, agent or technician foreign key. It never polls a subject
or infers completion from a timestamp.

An assembly/product adapter consumes an owner-produced event or command,
selects the survey under product policy and asks the module to issue an
invitation. After response capture it passes the immutable response fact to the
subject owner. The subject owner may project or act on it, but it does not keep
a parallel rating writer.

### 3. Delivery is a transport consequence

The module returns a durable invitation identity and bearer token. The adopting
product requests delivery through its outbox; Integrator/delivery owners retain
channel selection, provider credentials, message rendering, delivery attempts,
retry and provider evidence.

A failed delivery does not delete the invitation or create a new response
opportunity. A retry addresses the same invitation under delivery policy. The
module neither records provider payloads nor treats `sent` as feedback
lifecycle state.

### 4. Ticket CSAT migrates as a composition, not as ticket state

`support.ticket_lifecycle` continues to decide that a resolved ticket is
eligible and what a rating means for support operations. The mutable
`Ticket.metadata.csat` writer retires during Sub adoption. The module records
the invitation/response fact; the Sub composition layer projects that fact for
ticket reads and asks support owners to apply any consequence.

Work-order and service CSAT follow the same shape. A field outcome or active
service is an owner-produced input. Surveys cannot complete, reopen, dispute or
escalate the subject and cannot assign agent/technician performance.

### 5. The initial module is tenant-only and identity-minimizing

All current operational candidates are tenant data-plane applications. No
named control-plane application needs Surveys today, so the manifest declares
only tenant tables. A platform plane requires a real adopter and the full
ADR-0023 declaration/isolation contract; nullable or sentinel tenants are not
alternatives.

The module stores opaque references rather than contact or workforce records.
Answers and tokens never enter audit/event metadata or logs. Expiry controls
answer eligibility only. A purge/anonymization API is not invented without an
adopter-owned retention contract; physical deletion mechanics and aggregate
repair must land together when that contract exists.

### 6. Sub is cutover 1; ERP is the second candidate

Sub first composes the module, shadows the existing survey owner and ticket
CSAT path, then seals one writer and retires the displaced persistence. CRM
retires through that cutover and is not a consumer.

ERP follows only after focused characterization of anonymity, response
eligibility, date windows, local question types and aggregates. HR targeting,
employee identity and HR consequences remain ERP-owned. Reuse is proven only
when ERP runs the same released mechanism contract, not when CRM's fork is
deleted.

## Consequences

- Survey/CSAT mechanics receive one named reusable owner.
- Subject lifecycles and business consequences stay with their current owning
  services.
- Delivery failure and retry cannot mutate survey definitions or create a
  second decision engine.
- Each adopter owns a local tenant-isolated installation; applications
  synchronize facts through versioned ports, never shared database access.
- Sub's generic survey suite is the parity base; CRM retry tests and ERP HR
  behavior are explicit adoption inputs rather than copied coupling.

## Alternatives rejected

### Put CSAT inside `dotmac-ticketing`

Rejected because surveys also apply to work orders, services, conversations and
employee experience. Ticketing owns eligibility and consequences for tickets,
not the generic question/invitation/response mechanism.

### Put ticket/work-order trigger enums in Surveys

Rejected because a reusable module would then own product vocabulary and infer
foreign lifecycles. Opaque source evidence plus a product adapter preserves the
boundary.

### Treat CRM as a second consumer

Rejected because CRM and Sub are one consolidating lineage. A fork proves
duplication, not reuse.

### Use ERP's broader question vocabulary as the initial contract

Rejected because ERP has no focused proof for that service. The first version
ports Sub's tested four types; widening requires ERP characterization and an
adopter-driven contract change.
