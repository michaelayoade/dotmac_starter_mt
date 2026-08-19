# Decomposed marketing suite source inventory

**Status:** Source audit complete; campaigns is merged and media observations is parked, with neither package released or adopted
**As of:** 2026-08-18
**Decision owner:** Michael
**Default first adopter:** Backoffice; campaigns is the Sub-first ADR-0032 exception
**Later adopter:** Sub, as an independent application
**Current Starter main:** `f7d69f7d3db6a36dcccaa847dff7a37e9c3cd685`

This is the ADR-0006 product-first evidence for a Starter-owned, product-neutral
marketing suite. It deliberately selects seven small owners rather than
reproducing `dotmac_mkt` as one installable monolith. Backoffice is the first
adopter. Sub is a later independent adopter: each application pins the released
packages, runs its own module lineages and owns its own rows. There is **no
shared database** and no cross-application ORM, table or filesystem access.

The audit covered the planning, publication, media, analytics, form and campaign
implementations at these exact revisions:

| Repository | Revision | Audit role |
| --- | --- | --- |
| `dotmac_starter_mt` | `c6ef6cd7b13105bd95c3faf354ffee9032077625` | Target contracts, kernel facilities and extraction rules |
| `dotmac_mkt` | `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d` | Candidate content, publishing, media-observation and web-analytics behavior |
| `dotmac_sub` | `510b80ca7fab4f54a57f261872f94b5e972c8eb6` | Mandatory campaign, consent and suppression behavior |
| `dotmac_erp` | `dd6416cd981ffdf48564e2770b87d3cd7201186c` | Generic versioned form engine and submission behavior |
| `dotmac_crm` | `60daaa2dd305696636632f48505ab784110a55d2` | Competing campaign implementation and negative site/analytics census |
| `dotmac_backoffice` | `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d` | First-adopter census; no existing marketing owner found |

The `dotmac_mkt` remote was verified directly on 2026-08-18. The current pin is
one commit after the original `1a185b47164e34601769c84976e95578996c4523`
audit coordinate. That delta changes delivery-backed external-reference
presentation and its web test only; the selected content models, schemas and
services are unchanged. It strengthens the publishing evidence without
changing the content boundary.

## Current implementation state

`dotmac-campaigns` is implemented and merged by PR #261 at Starter main
`300ebd7523e85dff7e94efcdf81d8c1f34b80de5`. All sixteen required GitHub
checks passed, including unit, PostgreSQL integration, Docker, consumer boot,
kernel floors and engineering standards. Kernel `0.1.0a71` is published and its
release tag is registry-backed, but it predates the campaigns merge.

That milestone is **not adoption**. Campaigns `0.1.0a1` remains unallowlisted,
unpublished and at dossier status `audit-complete` with no contract consumer.
ADR-0032 makes Sub cutover 1 for campaigns because Sub is its qualifying source;
Backoffice is cutover 2 and the independent reuse proof. Backoffice remains the
default first adopter for the other suite modules.

The tagged kernel `0.1.0a71` does not contain
`CAMPAIGNS_MIGRATION_OWNER`, although merged source and the campaigns package
comment claim that a71 allocated `mod_campaigns`/`ca`/`campaigns`. Campaigns
must not be released against a71. Durable Timers PR #263 is now merged at
Starter main `f7d69f7d3db6a36dcccaa847dff7a37e9c3cd685`; main declares kernel
`0.1.0a72` and contains both the campaigns and durable-timers allocations.
Kernel a72 and Durable Timers `0.1.0a1` are now tagged and registry-verified
from that exact main commit. The campaigns declared floor and lock still need
correction from a71 to a72, followed by fresh clean-wheel and consumer-floor
evidence.

The parked media-observations branch diverged before campaigns and Durable
Timers merged and separately adds its allocation to an alternate a72 source
line. Published a72 is immutable and does not contain the media-observations
allocation. Reconciliation onto main must preserve the existing campaigns and
durable-timers owners, allocate media observations in a new kernel alpha, and
repin that package before it can be released. Campaigns release remains gated
on Sub's kernel S7 consent/idempotency/outbox ownership cutovers, real kernel
lineage composition, and Durable Timers adoption in Sub; the timer publication
gate itself is now closed.

`dotmac-media-observations` is complete on the pushed
`agent/dotmac-media-observations` branch. Commit
`c548ef02aca10b421d1ebf4158b9c4fdf72e6025` is the validated candidate
milestone; the branch has since advanced by one evidence-only commit to
remote head `abf1b9ad4c3889aa6c40ed2e01419e440452f565`. The clean local
worktree is one unpushed test-hardening commit further at
`56517abc7f05cb6e20f9b0e5fdb6a492dbf0fdd2`; that commit is not validation
or release evidence. The candidate package is `0.1.0a1` and still declares
`dotmac-kernel >=0.1.0a72`, which must move because published a72 lacks its
allocation. Observer validation is green for the full checks,
unit/architecture suite and disposable PostgreSQL integration on the earlier
code revision; c548's documentation revision also passed checks plus focused
architecture and clean-wheel tests. The candidate is deliberately **parked**:
it has not been allowlisted, tagged, published, composed or adopted, and no
authority has moved.

### Mkt source-coordinate status across sibling branches

The merged `docs/inventories/campaigns-sources.md` names Mkt revision
`1a185b9f9d3ee102255bd57ce4bc62a587c08552`. Direct object lookup after a
fresh fetch reports that object missing, while `git ls-remote` resolves Mkt
`refs/heads/main` to `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d`.
This is an evidence-coordinate defect to correct before campaigns release. It
does not change the campaigns source ruling because Sub—not Mkt—is the
qualifying implementation and Mkt supplies requirements only.

Media-observations commit `abf1b9ad4c3889aa6c40ed2e01419e440452f565`
now records current Mkt `main`, its configured `master` predecessor and the
one-commit delivery/content-affinity delta. It explicitly rejects the local
Post deletion and content/publication associations while preserving the
missing-object observation. The stale media evidence-coordinate gate is
therefore closed; rebase, namespace reconciliation, release and adoption remain
separate gates.

## Source ruling

| Starter module | Source mode | Qualifying source | Evidence and initial boundary |
| --- | --- | --- | --- |
| `dotmac-content` | `product-first` | `dotmac_mkt` | Preserve campaign-calendar planning, canonical post copy, asset references and approval-ready content state from `Campaign`, `Post`, `Asset` and their services. Stored bytes remain with `dotmac-files`; generic work management is outside this owner. |
| `dotmac-sites` | `greenfield-after-inventory` | `none` | No qualifying site-builder implementation was found in Starter, `dotmac_mkt`, ERP, CRM, Sub or Backoffice. The first slice therefore needs checked-in greenfield proof and a Backoffice canary before code. It owns immutable page/site revisions and release intent, not hosting transport. |
| `dotmac-publishing` | `product-first` | `dotmac_mkt` | Gate 2 is Observer-green at `5a1892c3aac30b607cc28baa52217870e97bc63c`. The candidate owns immutable releases, ordered opaque-target deliveries, monotonic attempts, normalized observations and explicit partial/all-failed reconciliation. It preserves qualifying `PostDelivery` scheduling meaning while replacing direct provider adapters with kernel outbox intents and an assembly-supplied typed timer port. Kernel a74 and publishing a1 remain local, unpublished, unallowlisted, uncomposed and unadopted. |
| `dotmac-media-observations` | `product-first` | `dotmac_mkt` | Candidate implementation is complete and parked at validated milestone `c548ef02aca10b421d1ebf4158b9c4fdf72e6025`; evidence-only remote head `abf1b9ad4c3889aa6c40ed2e01419e440452f565` refreshes the Mkt ruling, while local `56517abc7f05cb6e20f9b0e5fdb6a492dbf0fdd2` is unpushed test hardening. Published kernel a72 lacks the media allocation, so rebase and a new kernel floor are mandatory; the module is not released or adopted. Preserve normalized remote post/ad hierarchy and idempotent metric upserts from `AdSyncService`, `ChannelMetric`, `AdCampaign`, `AdGroup`, `Ad` and `AdMetric`. These are observations; they never assign another module's authoritative lifecycle. |
| `dotmac-web-analytics` | `product-first` | `dotmac_mkt` | Preserve the provider-neutral daily web metric vocabulary and aggregation behavior for sessions, pageviews, users and bounce rate. GA4 HTTP/OAuth code is rejected; Integrator records typed observations that this local owner projects and reconciles. |
| `dotmac-forms` | `product-first` | `dotmac_erp` | Preserve organization-scoped definitions, immutable versions, sections, typed fields/options, validation, submissions and answer snapshots from ERP's `forms` models and `FormEngineService`. Replace Organization and domain entity coupling with Tenant scope and opaque subject references. |
| `dotmac-campaigns` | `product-first` | `dotmac_sub` | Implemented on Starter main, but deliberately unreleased and unadopted. Published kernel a71 lacks its namespace allocation; registry-verified a72 contains it and Durable Timers a1 is also published. Correct the package floor/lock, then satisfy Sub's owner, lineage and timer-adoption gates. Preserve audience building, sequences, send windows, canonical senders, attempt/outcome state, unsubscribe and pre-send suppression rechecks. Sub is the mandatory campaign source and cutover 1; Backoffice is cutover 2. CRM and `dotmac_mkt` are parity/retirement inputs, not competing owners. |

Every eventual package still needs its own `EXTRACTION.toml`, manifest, owner,
namespace, lineage, tenant-isolation canary, preserved parity tests, first
cutover and local-copy retirement gate. This inventory selects sources; it does
not claim that any package or adoption already exists.

## Why the suite is decomposed

Each business decision has one owner and each module is independently useful:

- `dotmac-content` owns editorial content and its planning lifecycle.
- `dotmac-sites` owns immutable site/page revisions and which revision is ready
  for release.
- `dotmac-publishing` owns local publication intent and reconciled publication
  state.
- `dotmac-media-observations` owns normalized social/ad observations and
  rebuildable rollups.
- `dotmac-web-analytics` owns normalized website observations and rebuildable
  web measurement rollups.
- `dotmac-forms` owns form definitions, versions, submissions and answer
  snapshots.
- `dotmac-campaigns` owns campaign/audience/recipient lifecycle. Consent and
  suppression are inputs owned outside campaigns; delivery is requested after
  the local transaction commits.

Modules never import sibling modules. Assemblies connect them through typed
ports, declared events, opaque identifiers and a typed outbox. A site may retain
an opaque form identifier, a campaign may retain an opaque content identifier,
and a publisher may consume an immutable content snapshot; none imports the
other module's ORM model or service.

## Integrator and hosting boundary

**Integrator owns provider transport.** Credentials, OAuth, provider SDKs,
webhook verification, API clients, rate limits, retries, checkpoints and remote
delivery evidence remain in Integrator and its connector plugins. Marketing
modules hold no provider enum, provider client, provider credential or
product-specific switch. They emit provider-neutral commands only after local
state commits and consume typed, deduplicated observations.

Website releases use the same boundary. `dotmac-sites` always stores a **local
immutable website snapshot** before requesting remote publication. A
`SiteRelease` command leaves through the typed outbox; Integrator selects and
runs the bound hosting connector. A remote URL, deployment identifier or
successful webhook is evidence of transport, never the only copy of the site.
The reconciler can re-drive a release from the local snapshot and repair drift.

## Preserved source evidence

### `dotmac_mkt`

Preserved behavior comes from:

- `app/models/campaign.py`, `post.py`, `post_delivery.py`, `asset.py`,
  `channel_metric.py` and `ad_campaign.py`;
- `app/services/campaign_service.py`, `post_service.py`, `asset_service.py`,
  `publishing_service.py`, `analytics_service.py` and `ad_sync_service.py`; and
- `tests/test_marketing_models.py`, `test_marketing_services.py`,
  `test_ad_sync_service.py`, `test_analytics_sync.py` and
  `test_analytics_daily_totals.py`.

The source has useful lifecycle and parity behavior but is not module-ready: its
marketing tables lack Starter tenant/RLS and lineage contracts, service code is
coupled to application models, and tasks open their own sessions. Those shapes
are rejected rather than ported.

### Sub campaigns

The load-bearing sources are `app/models/comms_campaign.py`,
`app/services/comms_campaigns.py`, `app/services/notification_suppression.py`,
`app/tasks/campaigns.py`, migrations `259`, `273` and `278`, plus
`tests/test_campaign_parity.py` and
`tests/test_notification_queue_suppression.py`. The tests prove suppression at
audience build and again immediately before send, global marketing unsubscribe,
ordered cumulative sequence delays, time-zone send windows, canonical sender
failure, durable attempts and delivery confirmation. A campaign implementation
without those behaviors is not parity.

Audience membership changes at the product seam: the shared module accepts
typed candidate-recipient observations and an eligibility port. It does not
query Sub `Subscriber`, CRM `Person` or any product segment table. Consent and
suppression remain the separate communication-policy authority.

### ERP forms

ERP's `app/models/forms/form.py` and
`app/services/forms/form_engine.py` implement seven persisted definition and
submission tables, published version snapshots, field validation, choice/file
handling, subject linkage and answer snapshots. This is a qualifying source,
but its `organization_id`, `core_org` foreign keys, recruitment-specific helper
and raw mapping payloads are product seams to replace, not shared contracts.
`tests/integration/services/test_careers_service.py` proves a real consumer can
publish a dynamic form, submit typed answers, reject invalid choice values and
query snapshot-backed answers; those behaviors are the minimum parity source,
not the recruitment aggregate around them.

## Explicitly rejected scope

The extraction does not copy `dotmac_mkt`'s inherited platform or product code:

- people and identity;
- billing;
- RBAC and sessions;
- settings;
- generic tasks;
- file-byte storage; or
- provider clients, credentials, OAuth and SDK adapters.

Starter kernel facilities and released modules supply authentication,
authorization, settings, audit, idempotency, durable timers, files, imports,
approvals and template-studio/UI contracts. Generic tasks stay outside the
marketing suite and follow their independently selected project/work owner.

## Execution and cutover order

1. Keep the verified `dotmac_mkt` pin current and freeze every source revision
   in its package dossier before the first code port.
2. Extract source parity canaries before implementation: content/media/
   publishing/web analytics from `dotmac_mkt`, campaigns from Sub, and forms
   from ERP. `dotmac-sites` starts with its greenfield proof and adopter canary.
3. Implement one module slice at a time with its manifest, `mod_*` namespace,
   independent migration lineage, RLS canary, typed ports and owner row in
   `docs/ARCHITECTURE.md`.
4. Publish exact package releases and compose Backoffice without path or
   editable dependencies. Backoffice is the default first adopter and owns its
   rows; campaigns follows ADR-0032's source-first sequence of Sub then
   Backoffice.
5. Backfill each selected source, shadow-read and reconcile, switch one writer
   at a time, prove parity and drift repair, then remove the old writer and
   provider code. A copied source that still writes is not a cutover.
6. Let Sub adopt selected released modules later as a separate application with
   its own rows, lineages and product adapters. Synchronize data only through
   versioned APIs/webhooks and local typed observations.
7. Add attribution and experiments only after the observation, campaign and
   publishing boundaries are live; they are not hidden inside the first slice.

All behavioral validation runs on Observer in a fresh isolated writable
worktree pinned to the exact branch commit under test, using disposable
databases and at most three test workers. Git-hosted CI remains merge acceptance
evidence.
