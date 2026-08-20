# Surveys and CSAT product-first source inventory

- **Audited:** 2026-08-18
- **Starter:** `92ae7a6f9c8307797704deb615a24e59420a73c4`
- **Sub:** `3f8d74825bee47b98c3c532229b72f3a8a5b16aa`
- **ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`
- **CRM:** `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`

The Sub, ERP and CRM worktrees had unrelated local changes. Every cited survey
path was clean, so the revisions above identify the behavior reviewed. Vendor
Control Plane and Backoffice were also searched and contained no Surveys/CSAT
implementation. Academy matches were training-content prose and assessment
banks, not an operational feedback owner.

This inventory resolves the open Surveys/CSAT candidate before shared
implementation, as required by ADR-0006's product-first rule. It distinguishes
survey mechanics from the ticket, conversation, work-order or service decision
that makes feedback eligible.

## Verdict

`dotmac-surveys` is a reusable tenant module extracted **from Sub's
`communications.surveys` owner**. CRM is the retiring fork and supplies
delivery-failure requirements. ERP's employee survey system is a concrete
second-adopter candidate and exclusion source, but it has no focused behavior
tests and therefore does not displace Sub as the base.

| Repository | Existing behavior | Evidence quality | Verdict |
|---|---|---|---|
| `dotmac_sub` | Generic survey definitions, typed questions, lifecycle, invitations, public/tracked responses, rating/NPS projections and ticket/work-order triggers; separate ticket-metadata CSAT | Implemented SOT; one named owner; 11 focused behavior tests plus five architecture canaries; adapters sealed from writes | **Qualifying source and first cutover** |
| `dotmac_crm` | Earlier generic survey fork plus conversation-resolution CSAT, channel delivery and retry | Four survey-service/API tests and nine delivery/retry tests; direct commits/rollbacks and product/provider coupling remain | Mandatory ancestry and retry requirements; not a consumer |
| `dotmac_erp` | HR engagement/pulse/exit/onboarding surveys with questions, responses, anonymous mode and aggregates | Broad production-shaped models/service; no focused tests naming the HR survey owner were found | Requirements source and concrete second candidate, not the base |
| Vendor CP / Backoffice | No survey or CSAT owner | Zero matching implementation paths | Not candidates |

The reusable boundary is definition, question validation, lifecycle, invitation
identity, response evidence and rebuildable aggregates. Subject eligibility,
audience selection and business consequences remain with the product owner.

## Sub — qualifying implementation

Authoritative paths:

- `dotmac_sub:docs/designs/SURVEY_LIFECYCLE_AND_CREATION.md`
- `dotmac_sub:docs/designs/SUPPORT_TICKET_LIFECYCLE_SOT.md`
- `dotmac_sub:docs/designs/CUSTOMER_EXPERIENCE_LIFECYCLE_SOT.md`
- `dotmac_sub:docs/SOT_RELATIONSHIP_MAP.md`
- `dotmac_sub:app/models/comms.py`
- `dotmac_sub:app/schemas/comms.py`
- `dotmac_sub:app/services/surveys.py`
- `dotmac_sub:app/services/events/handlers/surveys.py`
- `dotmac_sub:alembic/versions/464_survey_lifecycle_and_creation.py`

Proof to port or preserve:

- `dotmac_sub:tests/test_surveys.py`
- `dotmac_sub:tests/architecture/test_survey_boundary.py`
- `dotmac_sub:tests/test_ticket_csat.py`

The checked-in design names `communications.surveys` as the owner of content,
lifecycle, invitation identity, response validation, metrics, idempotency and
public-answer eligibility. Its strongest reusable behavior is:

- the exact `rating`, `nps`, `multiple_choice`, `free_text` question vocabulary;
- normalized unique question keys and bounded, unique choice options;
- `draft -> active -> paused/closed`, with paused reactivation and terminal
  closure;
- refusal to activate, publish or invite from an empty definition;
- public and tracked response reads that fail closed for inactive, expired or
  malformed definitions;
- one response per tracked invitation;
- authoritative answer/rating/NPS validation;
- invitation/response counts, mean rating and NPS as rebuildable projections;
  and
- thin admin/public/event adapters around one flush-oriented owner.

Sub's automatic trigger adapter consumes existing authoritative events. That
adapter shape is retained, but the `ticket_closed` and `work_order_completed`
terms do not move into the module: they belong to the subject owners that emit
the events and decide eligibility.

## Ticket CSAT is an adoption path, not a second mechanism

`support.ticket_lifecycle` currently stores a mutable `metadata.csat` object on
the Ticket after checking only that the ticket is closed. It is separate from
`communications.surveys`; a second rating overwrites the first and there is no
dedicated immutable response identity, invitation identity or aggregate repair
path.

The cutover does not move ticket closure into Surveys. Ticketing continues to
decide:

- whether the ticket is eligible for feedback;
- which resolution event and customer may be invited;
- whether a later rating is a correction, a new response or forbidden; and
- what support, agent-performance or service-quality consequence follows.

The Surveys adapter records the invitation and immutable response fact. Ticket
projections reference that fact through the adopting product's composition
layer; they do not maintain a second rating writer in Ticket metadata.

The same rule applies to work orders and services. Field completion, customer
acceptance, rework and technician consequences remain with their respective
owners. A completed work order is evidence an adapter may consume, not a
lifecycle transition the Surveys module may infer.

## CRM — fork and delivery requirements

Relevant paths:

- `dotmac_crm:app/models/comms.py`
- `dotmac_crm:app/services/surveys.py`
- `dotmac_crm:app/services/crm/inbox/csat.py`
- `dotmac_crm:app/tasks/surveys.py`
- `dotmac_crm:app/tasks/crm_inbox.py`
- `dotmac_crm:tests/test_surveys_service.py`
- `dotmac_crm:tests/test_api_surveys.py`
- `dotmac_crm:tests/test_csat_send_failure_isolation.py`
- `dotmac_crm:tests/test_csat_retry_task.py`

CRM's generic models are the weaker ancestor of Sub's current implementation.
They foreign-key directly to people, tickets, work orders and notifications;
the manager and task paths commit internally; and the conversation CSAT helper
mixes target settings, channel policy, message construction, provider delivery,
retry and survey state.

The reusable requirement is narrower: an invitation must remain durable if
delivery fails, and delivery retry must not create another response opportunity.
The module therefore returns invitation identity and the bearer token to the
caller transaction. Delivery status, retry, provider payloads and channel
selection remain with the delivery/Integrator owners. CRM retires through Sub
and is not counted as an independent consumer.

## ERP — second-candidate requirements

Relevant paths:

- `dotmac_erp:app/models/people/hr/survey.py`
- `dotmac_erp:app/services/people/hr/survey_service.py`
- `dotmac_erp:alembic/versions/20260411_add_survey_succession.py`

ERP proves the generic mechanism has a credible adopter outside post-service
feedback: employee surveys also need definitions, ordered questions, response
sessions, answers and aggregates. Its engagement/pulse/exit/onboarding types,
date window, department/designation targeting, employee identity and
anonymity policy remain HR-owned. They do not become shared enums or foreign
keys.

No focused tests for `SurveyService`, `hr.survey` or its lifecycle were found
under ERP's test tree. ERP is therefore requirements evidence, not parity proof.
Before cutover it must characterize anonymity, one-response rules, date-window
eligibility, all six local question types and aggregate behavior. The first
module version does not widen Sub's tested four-question vocabulary merely to
claim ERP compatibility.

## Mandatory corrections and canaries

### D1 — subject policy is embedded in the source schema

Sub stores closed trigger types plus subscriber, ticket and work-order ids in
the generic tables. CRM additionally stores person, conversation and
notification foreign keys. Those columns make the mechanism ISP/CRM-specific.

The module instead records bounded opaque `recipient_ref`, `source_owner`,
`source_event_id` and optional `subject_ref` strings with no product foreign
key. The product adapter selects an eligible survey and issues the invitation.
The module neither polls a subject nor interprets these references.

### D2 — no source provides tenant isolation

Sub/CRM survey tables have no `tenant_id`; ERP uses `organization_id` inside
the HR schema but provides no Starter `TenantScope`/RLS contract. Every module
table carries `tenant_id UUID NOT NULL`, every internal foreign key carries the
tenant, and the creating migration ENABLEs and FORCEs RLS. A PostgreSQL canary
proves visibility and rejects a cross-tenant invitation/response reference; a
sensitivity mutation disables RLS and proves the detector sees both tenants.

### D3 — delivery is fused into survey state

CRM commits an invitation before provider send, then commits again after send.
Sub requests notification delivery inside the product owner command. The
module performs no I/O, chooses no channel, renders no provider payload and
never commits or rolls back. It returns a durable invitation/token fact inside
the caller's transaction; the adopting product requests delivery through its
outbox and retries there.

### D4 — ticket CSAT has two current writers and mutable evidence

Sub's generic response path and Ticket `metadata.csat` are independent. The
module permits one response per tracked invitation and keeps subject policy
outside. Sub cutover must retire the metadata writer and make the product
projection read the module response fact after the ticket owner approves the
command.

### D5 — aggregate columns are projections

Counts, mean rating and NPS are derived from invitation/response rows. The
module has one projection writer and an idempotent rebuild. Callers cannot set
aggregate values. Rating uses exact integer/decimal arithmetic; the module does
not copy CRM's float projection.

### D6 — privacy and retention are incomplete in every source

The initial module minimizes identity: no name, email, phone, subscriber,
employee, ticket, work-order or conversation FK is stored. Answers never enter
audit or event metadata, and the module logs no bearer token or response body.
The adopting product owns the lawful audience and retention policy; a later
purge/anonymization surface requires an adopter, a retention contract and its
own source/behavior proof. `expires_at` controls answer eligibility, not data
retention, and must not be described as one.

### D7 — source definitions can change underneath one aggregate

Sub and CRM permit question edits after activation while response rows retain
only answer keys and the Survey keeps one lifetime aggregate. Changing a label,
type or scale can therefore make old and new answers look like one series. The
initial module permits content edits only in `draft`; after activation a changed
questionnaire is a new Survey identity. It also allows at most one rating and
one NPS question while those two values have one top-level aggregate each. A
future versioned-definition contract must migrate responses to an explicit
definition revision rather than relax these guards.

## Initial package contract

The audit-complete `0.1.0a1` package owns three tenant tables in
`mod_surveys`: survey definitions, invitations and responses. Questions and
answers are validated typed JSON matching Sub's source contract. The `sv`
lineage requires the tenant-scope catalogue and module database roles.

The module deliberately excludes routers, templates, notification delivery,
audience queries, product trigger vocabularies, ticket/work-order/service
lifecycles, employee anonymity policy, agent/technician performance and
retention schedules. Those are owned by adopters or require a separate audited
contract.

## Cutover and retirement

1. Validate the package and its unit/architecture/PostgreSQL canaries on a fresh
   Observer worktree.
2. In Sub, compose the `sv` lineage and add a thin adapter from the existing
   authoritative ticket-resolution and work-order-outcome events.
3. Backfill definitions, invitations and responses with explicit tenant and
   opaque subject/recipient mappings; do not infer lifecycle from timestamps.
4. Shadow validation, invitation deduplication, response outcomes and aggregate
   rebuilds in the same transaction until unexplained drift is zero.
5. Seal one writer, retire `app.services.surveys` persistence/models and the
   Ticket `metadata.csat` writer, and retain only Sub-owned eligibility and
   consequence adapters.
6. Route CRM retirement through the Sub cutover; do not migrate its provider
   delivery engine into Surveys.
7. Characterize ERP HR behavior, then adopt on an exact release while keeping
   targeting, employee identity, anonymity and HR consequences outside.

Until step 5, the package is `audit-complete`, not adopted. Until ERP completes
step 7, it is not reuse-proven.
