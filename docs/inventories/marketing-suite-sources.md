# Decomposed marketing suite source inventory

**Status:** Source audit complete; campaigns is merged, while content, publishing, media observations, sites and web analytics are implemented and parked; none is adopted
**As of:** 2026-08-19
**Decision owner:** Michael
**Default first adopter:** Backoffice; campaigns is the Sub-first ADR-0032 exception
**Later adopter:** Sub, as an independent application
**Current Starter main:** `68939275fdb302b1f50ed92a8920ccea745e5d37`

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
| `dotmac_mkt` | `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d` | Candidate content, publishing and media-observation behavior; provider-aggregate web requirements only |
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
kernel floors and engineering standards.

That milestone is **not adoption**. Campaigns `0.1.0a1` remains unallowlisted,
unpublished and at dossier status `audit-complete` with no contract consumer.
ADR-0032 makes Sub cutover 1 for campaigns because Sub is its qualifying source;
Backoffice is cutover 2 and the independent reuse proof. Backoffice remains the
default first adopter for the other suite modules.

Kernel a72 and Durable Timers `0.1.0a1` are tagged and registry-verified; a72 is
the first immutable release containing the campaigns namespace allocation.
Kernel a73 is also published and registry-verified from current Starter main
`68939275fdb302b1f50ed92a8920ccea745e5d37`. It adds the caller-session
transaction mechanics required by Sub, so the campaigns package and lock now
correctly use a73 as the effective floor while retaining a72 as allocation
evidence. Campaigns release remains gated on Sub's kernel S7
consent/idempotency/outbox ownership cutovers, real kernel lineage composition,
and Durable Timers adoption in Sub; the timer and kernel publication gates are
closed.

`dotmac-media-observations` is complete on the pushed
`agent/dotmac-media-observations` branch. Commit
`c548ef02aca10b421d1ebf4158b9c4fdf72e6025` is the validated candidate
milestone; the branch then advanced through evidence-only commit
`abf1b9ad4c3889aa6c40ed2e01419e440452f565` and test-hardening commit
`56517abc7f05cb6e20f9b0e5fdb6a492dbf0fdd2`; that commit is not validation
or release evidence. The pushed branch is reconciled through
`2ade09d16c3e2d246ad361129c4700de6eff819b`. The candidate package is
`0.1.0a1` and now declares `dotmac-kernel >=0.1.0a74`: immutable published a73
lacks its allocation, so the first free identity is the unreleased local a74
candidate. The old rebase, namespace and floor-repin gates are closed. Observer
validation was green for the full checks, unit/architecture suite and
disposable PostgreSQL integration on the earlier code revision; c548's
documentation revision also passed checks plus focused architecture and
clean-wheel tests. The candidate is deliberately **parked**: it has not been
allowlisted, tagged, published, composed or adopted, and no authority has
moved. The combined marketing branch validation recorded below covers content
a75 and publishing a76 beside it.

### Combined reconciliation evidence

On 2026-08-19 a fresh detached writable Observer checkout at exact commit
`9c0068d3c675c955c46bd3391f9d46f6685cbfcb`, with all 85 tags and the committed
Poetry 2.4.1 lock, passed `make check`, all 3,944 collected unit/architecture
cases and all 504 collected disposable-PostgreSQL integration cases without
failure. The combined media/content/publishing live-catalog and forced-RLS
suite passed all 15 cases. Its isolated database container and network were
removed. This closes reconciliation only: kernel a74/a75/a76 and all three
module packages remain unpublished, unallowlisted, uncomposed and unadopted.

### Mkt source-coordinate status across sibling branches

The merged `docs/inventories/campaigns-sources.md` names Mkt revision
`1a185b9f9d3ee102255bd57ce4bc62a587c08552`. Direct object lookup after a
fresh fetch reports that object missing, while `git ls-remote` resolves Mkt
`refs/heads/main` to `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d`.
This is an evidence-coordinate defect to correct before campaigns release. It
does not change the campaigns source ruling because Sub—not Mkt—is the
qualifying implementation and Mkt supplies requirements only.

Media-observations commit `abf1b9ad4c3889aa6c40ed2e01419e440452f565`
records current Mkt `main`, its configured `master` predecessor and the
one-commit delivery/content-affinity delta. It explicitly rejects the local
Post deletion and content/publication associations while preserving the
missing-object observation. The stale media evidence-coordinate gate is
therefore closed. Rebase, namespace reconciliation and the package floor
correction are also complete; release and adoption remain separate gates.

## Source ruling

| Starter module | Source mode | Qualifying source | Evidence and initial boundary |
| --- | --- | --- | --- |
| `dotmac-content` | `product-first` | `dotmac_mkt` | Preserve campaign-calendar planning, canonical post copy, asset references and approval-ready content state from `Campaign`, `Post`, `Asset` and their services. Stored bytes remain with `dotmac-files`; generic work management is outside this owner. Its a75 allocation and combined train are Observer-green at `9c0068d3c675c955c46bd3391f9d46f6685cbfcb`; content a1 remains unpublished, unallowlisted, uncomposed and unadopted. |
| `dotmac-sites` | `greenfield-after-inventory` | `none` | No qualifying site-builder implementation was found in Starter, `dotmac_mkt`, ERP, CRM, Sub or Backoffice. The checked-in greenfield proof freezes its ownership of immutable page/site revisions and release readiness, not publication intent or hosting transport. Gate 1 produced 13 passing evidence cases and 40 intended missing-package failures at `9f735b4`. Gate 2 is complete at exact revision `8bf12ddc5cb938714d090fc0b0e69b83fa78f2d2`: 124 focused cases, `make check`, 4,028 unit/architecture cases and 509 disposable-PostgreSQL cases passed, including all five Sites isolation/immutability canaries. The a77/a1 candidate remains unpublished, unallowlisted, uncomposed and unadopted. |
| `dotmac-publishing` | `product-first` | `dotmac_mkt` | Gate 2 was Observer-green at `5a1892c3aac30b607cc28baa52217870e97bc63c`. The candidate owns immutable releases, ordered opaque-target deliveries, monotonic attempts, normalized observations and explicit partial/all-failed reconciliation. It preserves qualifying `PostDelivery` scheduling meaning while replacing direct provider adapters with kernel outbox intents and an assembly-supplied typed timer port. Its a76 allocation after immutable a73, media a74 and content a75 is combined-train green at `9c0068d3c675c955c46bd3391f9d46f6685cbfcb`. Publishing a1 remains local, unpublished, unallowlisted, uncomposed and unadopted. |
| `dotmac-media-observations` | `product-first` | `dotmac_mkt` | The complete parked candidate is reconciled through branch head `2ade09d16c3e2d246ad361129c4700de6eff819b`; immutable published kernel a73 lacks the media allocation, so the module keeps the next free kernel floor, a74. Its combined train is Observer-green at `9c0068d3c675c955c46bd3391f9d46f6685cbfcb`; the package remains unreleased, unallowlisted, uncomposed and unadopted. Preserve normalized remote post/ad hierarchy and idempotent metric upserts from `AdSyncService`, `ChannelMetric`, `AdCampaign`, `AdGroup`, `Ad` and `AdMetric`. These are observations; they never assign another module's authoritative lifecycle. |
| `dotmac-web-analytics` | `greenfield-after-inventory` | `none` | No audited product owns the complete first-party, privacy-minimising observation and deterministic-projection contract. Mkt's normalized daily provider metrics remain requirements for a separate observation boundary, not source code for this ledger. Reconcile the already-committed Starter candidate at `abef05ac5fd121ca254bb74eafcc7c9970e90dfd` onto kernel a78; do not rebuild it from the dirty prototype worktree. Release, composition and Backoffice/Sub adoption remain gated. |
| `dotmac-forms` | `product-first` | `dotmac_erp` | Preserve organization-scoped definitions, immutable versions, sections, typed fields/options, validation, submissions and answer snapshots from ERP's `forms` models and `FormEngineService`. Replace Organization and domain entity coupling with Tenant scope and opaque subject references. |
| `dotmac-campaigns` | `product-first` | `dotmac_sub` | Implemented on Starter main, but deliberately unreleased and unadopted. Registry-verified a72 contains its namespace allocation and Durable Timers a1; published a73 supplies the caller-session mechanics required by Sub and is therefore the effective package floor. Satisfy Sub's owner, lineage and timer-adoption gates before publication. Preserve audience building, sequences, send windows, canonical senders, attempt/outcome state, unsubscribe and pre-send suppression rechecks. Sub is the mandatory campaign source and cutover 1; Backoffice is cutover 2. CRM and `dotmac_mkt` are parity/retirement inputs, not competing owners. |

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
immutable website snapshot** and exports a typed `SiteReleaseV1` before remote
publication can be requested. A thin assembly adapter passes that value to
`dotmac-publishing`, which owns intent, outcome and the typed outbox; Integrator
selects and runs the bound hosting connector. A remote URL, deployment
identifier or successful webhook is evidence of transport, never the only copy
of the site. Reconciliation can re-drive delivery from the local snapshot.

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
   publishing from `dotmac_mkt`, campaigns from Sub, and forms from ERP.
   `dotmac-sites` and web analytics start from their greenfield proofs and
   adopter canaries; web analytics then reconciles its committed Starter
   implementation rather than creating a second engine.
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
