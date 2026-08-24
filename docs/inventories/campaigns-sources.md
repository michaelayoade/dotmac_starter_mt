# Campaigns source audit

- **As of:** 2026-08-18
- **Starter:** `c6ef6cd7b13105bd95c3faf354ffee9032077625`
- **Starter release-gate recheck:** `cfa9df8ac8d4f6c2b4faa92d6724be7ae767bbe7`
- **Sub:** `510b80ca7fab4f54a57f261872f94b5e972c8eb6`
- **Sub adoption head recheck:** `0d27ab91181fbc2717731bef28e21578f3442cc4`
- **CRM:** `60daaa2dd305696636632f48505ab784110a55d2`
- **dotmac_mkt:** `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d`
- **Backoffice:** `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d`
- **ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`
- **Method:** fresh detached audit checkouts at the commits above, followed by
  model/service/task/migration/test call-graph inspection. The Mkt pin was
  re-read through the GitHub API on 2026-08-23; it supersedes a mistyped,
  nonexistent `1a185b9f...` coordinate in this document. ERP was inspected from
  its pinned Git tree because its checkout had unrelated local changes; no
  working-tree content was used as evidence.
- **Adoption recheck:** the current Sub `origin/dev` campaign model, service,
  task and parity-test paths have no changes from the qualifying Sub pin above.
  Its current `docs/PLATFORM_ADOPTION_LEDGER.md`, executable owner registry and
  kernel rehearsal were inspected separately because adoption readiness is a
  different claim from source qualification.
- **Decision:** product-first from Sub. CRM supplies parity and negative
  evidence. dotmac_mkt supplies media requirements only. ERP and Backoffice
  have no campaign owner; Backoffice is the independent reuse proof, not a
  source.

This inventory is characterization evidence, not authority. ADR-0056 owns the
campaign boundary and cutover sequence. `packages/dotmac-campaigns/EXTRACTION.toml`
is the executable extraction dossier.

## Sequencing conflict resolved

The earlier marketing-to-service-agent programme named Backoffice first for the
suite as a whole. That sequence cannot govern campaigns without violating the
product-first rule: Sub has a production-used campaign implementation and parity
tests, so a shared implementation beside it would create a second owner before
the qualifying owner adopted it.

Campaigns therefore cuts over in this order:

1. **Sub first.** Port and shadow the qualifying lifecycle, seal the authority
   switch, compose the module lineage, then retire Sub's local campaign writer.
2. **Backoffice second.** Compose the exact released module as an independent
   greenfield adopter. This proves reuse without treating absence as source
   evidence.
3. **CRM third.** Preserve its open/click and reply-correlation facts while
   retiring its coupled campaign scheduler, provider configuration, direct
   Inbox/Lead writes, and mutable audience document.
4. **dotmac_mkt does not adopt by default.** It remains the media/editorial
   owner. A future typed observation adapter may correlate an external media
   campaign to an opaque campaigns reference, but neither application imports
   or queries the other's state.

The broad programme can still introduce non-campaign modules to Backoffice
first. This is a campaign-specific precedence ruling, not a reversal of the
whole programme.

## Capability boundary

`dotmac-campaigns` owns provider-neutral outbound campaign progression:

- campaign identity, immutable revisions, schedule, pause, cancellation and
  completion;
- immutable audience and recipient snapshots once sending begins;
- one-time and ordered nurture steps, send-window evaluation and recipient-step
  progression;
- recorded eligibility/suppression receipts, unsubscribe requests and the
  consequences campaigns itself owns;
- delivery intents and deduplicated delivery/open/click/reply observations;
- response and conversion-correlation facts for a product-owned Sales adapter;
- rebuildable counters, drift evidence, reconciliation and bounded repair;
- domain idempotency keys/fingerprints while the kernel idempotency ledger owns
  at-most-once execution.

It owns none of the following:

| Concern | Owner / assembly seam |
|---|---|
| Party, customer, subscriber, contact, or financial identity | The product owner supplies typed `AudienceCandidate` facts. Campaigns stores the immutable delivery snapshot and opaque source identity only. |
| Leads, opportunities, quotes, conversion decisions | `dotmac-sales`. Campaigns emits a response/correlation fact; the assembly asks Sales whether to create or advance a Lead. |
| Orders, subscriptions, billing, collections, retention cases, cancellation | Their named product owners. Financial cohort membership is an input fact, never a campaigns query or rule. |
| Consent and suppression policy | `dotmac_kernel.consent`. Campaigns records the decision receipt and re-asks at audience build and every delayed step. The delivery adapter re-asks immediately before transport. |
| Conversations and messages | `dotmac-inbox`. A reply observation carries opaque correlation; campaigns never creates a conversation/message. |
| Provider delivery, retry, credentials, webhooks | Kernel outbox plus Integrator. Campaigns publishes one provider-neutral delivery intent and consumes normalized observations. |
| Template authoring, publication and rendering | Template Studio. The assembly renderer returns an exact immutable rendered revision; campaigns retains the bounded delivery snapshot required for replay/repair. |
| Due-work claim, lease, retry, dead-letter, generation | Durable Timers plus the kernel relay. Campaigns supplies deterministic timer identity/purpose and accepts current triggers through an adapter. |
| Advertising hierarchy, provider campaign ids, raw metrics | dotmac_mkt/Integrator observation provenance. A provider id never becomes campaign identity. |
| Attribution and funnel decisions | Product analytics/Sales. Campaigns emits facts but does not assign credit or advance a funnel. |

The package imports the kernel only. In particular it imports neither Template
Studio, Durable Timers, Inbox, Sales nor any product module. Assemblies bind the
published typed ports.

## Source 1 — Dotmac Sub (qualifying implementation)

### Inventory

| Surface | Source |
|---|---|
| Models and local vocabulary | `app/models/comms_campaign.py` |
| Lifecycle, audiences, sequencing, send windows, sender and reconciliation | `app/services/comms_campaigns.py` |
| HTTP adapter | `app/api/campaigns.py` |
| Scan-based task adapter | `app/tasks/campaigns.py` |
| Persistence history | `alembic/versions/259_campaigns.py`, `273_campaign_nurture.py`, `278_campaign_sender_profiles.py` |
| Primary parity suite | `tests/test_campaign_parity.py` |
| Canonical financial-cohort boundary | `tests/test_campaign_financial_segments.py` |
| Queue-time suppression race | `tests/test_notification_queue_suppression.py` |

### Behaviour that ports

- One-time and nurture campaign lifecycles, including draft/scheduled/sending,
  pause/cancel and terminal completion.
- Audience construction with immutable address/context snapshots and explicit
  skipped/suppressed outcomes.
- Marketing suppression at audience build, again before a delayed step, and at
  the final delivery gate. A later suppression wins over an earlier eligible
  result.
- Marketing-only unsubscribe: it affects later marketing delivery but does not
  suppress billing/transactional communication.
- Ordered nurture progression. Delays are cumulative from predecessor steps;
  a later step cannot materialize until its predecessor has a resolved outcome.
- Idempotent recipient/step materialization and refusal to remove a step that
  already has recipient evidence.
- Timezone-aware send windows, including windows that cross midnight and the
  source convention that equal start/end means the whole day.
- Explicit sender selection, product default selection and fail-closed handling
  of inactive/unconfigured senders. Only the resolved, provider-neutral sender
  snapshot ports; connector credentials do not.
- Delivery-intent, confirmation/failure projection and rebuildable counts.
- Financial cohort membership obtained from Sub's collectibility owner in
  batches. The tests port as a boundary proof: campaigns consumes an eligibility
  fact and never restates due/overdue invoice policy.

### Product coupling that does not port

- Direct ORM reads of `Subscriber`, `Reseller`, invoices, service teams or
  connector configuration.
- Foreign keys to subscribers, users, service teams, notifications,
  conversations, messages or connector rows.
- Direct notification/Inbox submission, provider configuration, or provider I/O.
- The Celery/beat campaign scan, `FOR UPDATE SKIP LOCKED` loop and
  `materialized_steps` metadata ledger. Durable Timers already owns that
  mechanic; keeping the loop would create a second scheduler.
- Service/task-created sessions, commits or rollbacks. The adopter's transaction
  boundary owns commit/rollback and module services only mutate/flush.
- Unsubscribe policy writes. The assembly calls the kernel consent owner and
  records the resulting request/receipt in campaigns.

## Source 2 — CRM (parity and negative evidence)

### Useful parity

- `tests/test_campaign_tracking.py`: signed open/click ingestion, deduplication,
  first-seen timestamps and click-implies-open projection. URL signing and
  redirect handling stay in an edge/assembly adapter; campaigns consumes the
  normalized observation.
- `tests/test_campaign_lead_attribution.py`: reply correlation and idempotent
  conversion-correlation facts. Direct Lead creation is inverted: campaigns
  emits the fact and the assembly asks Sales.
- `tests/test_api_campaigns.py`: manual audience selection and lifecycle/API
  expectations. Manual selections become the same immutable typed audience
  batch as every other source.

### Defects that are explicit negative canaries

- `CampaignSmtpConfig`, `ConnectorConfig`, sender-provider foreign keys and
  provider selection inside campaign services couple lifecycle to transport.
- `audience_snapshot` is mutable JSON on the campaign and is built by querying
  People, Subscribers and retention facts directly.
- The scheduler opens its own sessions and scans campaign rows; sequence delay
  is not cumulative and can skip an unresolved predecessor.
- Recipients become `sent` when queued rather than when delivery is observed.
- Reply handling writes `Lead` and funnel/attribution decisions directly.
- Reconciliation reads Inbox `Message` rows rather than normalized observations.
- The tables have no tenant RLS boundary.

The shared module has structural tests forbidding provider/product foreign keys,
product imports, scheduler loops and lead/conversation mutation vocabulary.

## Source 3 — dotmac_mkt (requirements only)

`app/models/campaign.py` and `app/services/campaign_service.py` own an editorial
campaign container: posts, assets, members and tasks with
`draft/active/paused/completed/archived`. That is not outbound recipient
progression and does not move.

`app/models/ad_campaign.py` owns Meta/Google/LinkedIn hierarchy and provider
`external_id`. Those identifiers are external observation provenance. They may
appear only in a normalized observation envelope (`source_owner`,
`source_event_id`, fingerprint and opaque correlation); they never become a
campaign primary key, provider enum or module branch. Raw impressions, spend
and advertising metrics remain outside campaigns.

## Source 4 — Backoffice (independent adopter)

The pinned Backoffice source contains no campaign, nurture, audience,
suppression, outbox, timer or template lifecycle. It therefore contributes no
implementation or parity test. Its value is stronger and narrower: after Sub's
cutover and the exact module release, Backoffice composes the tenant lineage and
implements its own audience/template/sender/timer adapters without a product
branch inside the package. That is the second-consumer proof.

## Required fleet negative evidence — ERP

The pinned ERP Git tree was searched repository-wide for campaign, nurture,
unsubscribe and engagement-tracking implementations. It contains no outbound
campaign model, lifecycle, scheduler or parity suite; the only matches were
unrelated prose and fiscal-period reopen tracking. ERP therefore contributes
neither a qualifying implementation nor behavior to port. This closes the
ADR-0006 product inventory requirement without promoting absence into a
greenfield source or adding ERP as an adopter for this slice.

## Relevant Starter facilities

| Facility | As-built ruling for campaigns |
|---|---|
| `dotmac_kernel.consent` | Sole policy/ledger owner. Campaigns persists immutable evaluation receipts containing decision, reason, scope and evidence identity; it cannot manufacture eligibility. |
| `dotmac_kernel.idempotency` | Sole at-most-once engine. Changed fingerprints conflict; campaign tables contain domain evidence, not a second execution ledger. |
| `dotmac_kernel.messaging.outbox` | Sole publication owner. A delivery-intent fact and its outbox row are written in one caller transaction. Reconciliation can restore a missing publication with the same dispatch identity. |
| `dotmac_kernel.delivery` | Sole raw/provider receipt and bounce/complaint suppression owner. Campaigns consumes normalized delivery observations and never parses provider status. |
| `dotmac-template-studio` | Exact published template revisions are immutable. A renderer adapter returns rendered content plus revision/fingerprint for a bounded delivery snapshot. No module import. |
| `dotmac-durable-timers 0.1.0a1` | Registry-verified release owning timer generation, supersession/cancellation, current-trigger acceptance, history retention and delayed outbox relay. Campaigns has no scan loop or due-work ledger and no sibling import. |

Kernel `0.1.0a73` is also registry-verified. Starter PR #268 merged at
`cfa9df8ac8d4f6c2b4faa92d6724be7ae767bbe7`; release workflow run
`32220542857` installed the published artifact from the private registry before
creating annotated tag `dotmac-kernel-v0.1.0a73` on that commit. This closes the
caller-session runtime and campaigns dependency-floor gates. It does not decide
Sub's separate S7 table-owner/lineage collisions.

## Sub adoption prerequisite discovered by the current-head recheck

Sub cannot install the module merely by adding a package pin. Its authoritative
platform-adoption ledger currently pins `dotmac-kernel==0.1.0a50`, deliberately
does not compose the kernel migration lineage, and proves that lineage still
stops at revision `0001` pending per-table collision disposition. The same
ledger classifies the three persisted owners campaigns consumes as unresolved
S7+ cutovers:

- `dotmac_kernel.consent` collides with Sub's mature
  `communication_suppressions` table and `communications.eligibility` writer;
- kernel idempotency would sit beside Sub's `IdempotencyKey`/`TaskExecution`
  owners; and
- kernel tenant/platform outbox tables and relays would sit beside Sub's
  `events.store`, communication-intent and integration owners.

Those are source-of-truth collisions, not dependency-resolution details. A Sub
adapter that redirected campaigns to the legacy stores would contradict the
required kernel owners; installing the kernel tables beside them would create
parallel writers. The module therefore remains buildable and independently
validated, but stays unallowlisted and unreleased until Sub completes the
existing S7 disposition/cutover work, composes the exact released kernel
lineage and proves the superseded local writers are retired. This gate precedes
Sub's adoption of the already-released Durable Timers package and the campaigns
authority cutover; it does not demote Sub as the qualifying source or permit
Backoffice to go first.

## Contract and invariant map

| Required property | Module evidence |
|---|---|
| Tenant-only V1 | Manifest declares tenant tables only; every model has `tenant_id NOT NULL`; the migration enables and forces RLS in the same revision. No platform consumer was found. |
| Snapshot immutability | Lifecycle service and database triggers refuse audience, recipient, revision and step mutation once sending begins. PII can only be privacy-scrubbed after its recorded deadline. |
| One recipient/step | Composite unique `(tenant_id, recipient_id, step_id)` plus conflict-safe service materialization. |
| Three consent gates | Audience receipt, delayed-step receipt and delivery-gate receipt are separately named phases. A later denied receipt suppresses the step and prevents publication. |
| Ordered sequence | The previous step must have a resolved recipient outcome before a later step can materialize; due-work never skips it. |
| One scheduler | Timer identities are emitted through a typed port; no campaign scan/claim/lease/dead-letter table or job exists. |
| One delivery owner | The module writes provider-neutral intents to the kernel outbox; no network/provider code is accepted. |
| Observation monotonicity | Immutable, deduplicated observations feed a precedence-checked projection. Terminal delivery state cannot regress; engagement facts remain orthogonal. |
| Repairable counters | Counts are recomputed from recipient/step facts and compared to the cached projection by drift reporting. |
| Replay conflict | Kernel idempotency plus domain fingerprints return the original result for an identical replay and reject a changed payload. |
| Cancellation evidence | Cancellation blocks new intents/timers and preserves recipients, observations and prior delivery facts. |
| Retention/privacy | Campaign/revision policies declare evidence and PII deadlines. Bounded purge scrubs addresses, context and rendered content while retaining non-PII hashes/status evidence. No implicit forever default. |

## Cutover, drift and retirement evidence

### Sub — cutover 1

1. Complete Sub's checked-in kernel S7 adoption gates for tenant scope,
   consent, idempotency and outbox, including per-table collision disposition,
   real-lineage rehearsal, exact kernel pin and local-owner retirement evidence.
2. Compose `mod_campaigns` and backfill campaigns, revisions, steps, immutable
   recipient snapshots and delivery facts with source ids/fingerprints.
3. Shadow every eligible audience and due step through the module while the
   legacy service remains the only writer. Compare campaign/recipient/step
   identity, eligibility/suppression decisions, sender snapshot, window result,
   delivery intent and terminal observation. Provider ids are excluded from
   domain equality and retained as provenance only.
4. Rehearse drift until zero unexplained mismatches, then seal authority using
   ADR-0031's same-transaction observation/verification/switch protocol while
   legacy tables are write-locked.
5. Route commands and reads to the module. Delete the scan task and local
   campaign writer; archive/drop legacy tables only after rollback expiry.
6. A two-directional caller/table ratchet must reach zero. No mirrored recipient
   ledger or dual writer survives the cutover.

### Backoffice — cutover 2

Install the exact released kernel, campaigns and timer versions; compose only
the tenant planes; bind product-owned audience/render/sender/response adapters;
prove the conformance kit and RLS canary. There is no data migration or legacy
writer. When this lands, the dossier becomes `reuse-proven` with Sub and
Backoffice as contract consumers.

### CRM — retirement/parity cutover

Backfill immutable audience/recipient facts; translate existing open/click and
reply evidence into deduplicated observations; compare first timestamps and
counts; then retire the local scheduler/provider configs/direct queue path and
direct Lead/Inbox writes. The edge redirect/signature adapter may remain, but it
only submits a typed fact.

### dotmac_mkt — preservation

No writer moves. Preserve editorial/media tables and advertising hierarchy.
Any future correlation is a versioned API/webhook observation with opaque
references; there is no shared database, import or campaign-id adoption.

## Audit conclusion

The extraction is authorized as a tenant-only optional module with Sub as the
mandatory code/test base. The module and its canaries are implemented and
validated; kernel a73 and Durable Timers a1 are released. Campaigns publication
remains blocked by authoritative adoption dependencies: complete Sub's kernel
S7 consent/idempotency/outbox cutovers and lineage composition first, compose
the released timer package before due-work, seal the Sub campaigns cutover, and
only then prove independent reuse in Backoffice. Until those owners and Sub's
local campaign writer are retired the package may be built and tested but must
not claim `adopted` or become a second authority.
