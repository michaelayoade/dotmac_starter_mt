# Decomposed marketing suite source inventory

**Status:** Source audit complete; media observations, content, publishing and Sites are released and registry-verified; none is adopted
**As of:** 2026-08-20
**Decision owner:** Michael
**Default first adopter:** Backoffice; campaigns is the Sub-first ADR-0056 exception
**Later adopter:** Sub, as an independent application
**Release main:** `8f99413826e5adf3d35379ebc6deb79bcb5c8242`

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
ADR-0056 makes Sub cutover 1 for campaigns because Sub is its qualifying source;
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

`dotmac-media-observations` originated on the pushed
`agent/dotmac-media-observations` branch. Commit
`c548ef02aca10b421d1ebf4158b9c4fdf72e6025` is the validated candidate
milestone; the branch then advanced through evidence-only commit
`abf1b9ad4c3889aa6c40ed2e01419e440452f565` and test-hardening commit
`56517abc7f05cb6e20f9b0e5fdb6a492dbf0fdd2`; that commit is not validation
or release evidence. The pushed branch is reconciled through
`2ade09d16c3e2d246ad361129c4700de6eff819b`. The candidate package is
`0.1.0a1`. Published kernel a77 now owns the vendor cohort, so the marketing
allocations have moved to media a78, content a79, publishing a80 and Sites a81;
all four packages floor at the first installable cohort kernel, a81. Observer
validation was green for the full checks, unit/architecture suite and
disposable PostgreSQL integration on the earlier code revision; c548's
documentation revision also passed checks plus focused architecture and
clean-wheel tests. Michael authorized GitHub CI to validate the restacked
release candidate. PR #284 passed all sixteen required checks, merged as exact
main revision `8f99413826e5adf3d35379ebc6deb79bcb5c8242`, and the protected release
train published and registry-verified kernel a81 plus all four a1 modules.
Release did not resume adoption or move authority.

### Combined reconciliation evidence

On 2026-08-19 a fresh detached writable Observer checkout at exact commit
`9c0068d3c675c955c46bd3391f9d46f6685cbfcb`, with all 85 tags and the committed
Poetry 2.4.1 lock, passed `make check`, all 3,944 collected unit/architecture
cases and all 504 collected disposable-PostgreSQL integration cases without
failure. The combined media/content/publishing live-catalog and forced-RLS
suite passed all 15 cases. Its isolated database container and network were
removed. This is historical evidence for the pre-restack a74-a76 layout. The
published vendor a74-a77 cohort invalidated those local allocation numbers;
the release evidence for the a78-a81 restack is recorded below.

### 2026-08-20 release evidence

[PR #284](https://github.com/michaelayoade/dotmac_starter_mt/pull/284) passed
all sixteen required checks on its final candidate, including unit,
PostgreSQL integration, migration, Python-floor, consumer-boot, Docker,
security, type and engineering-standards gates. It squash-merged as protected
main revision `8f99413826e5adf3d35379ebc6deb79bcb5c8242`. Main remained at that exact
revision for the complete release train:

| Distribution | Version | Release run | Registry composition verified | Tag |
| --- | --- | --- | --- | --- |
| `dotmac-kernel` | `0.1.0a81` | [32346291258](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32346291258) | kernel artifact | `dotmac-kernel-v0.1.0a81` |
| `dotmac-media-observations` | `0.1.0a1` | [32346834449](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32346834449) | kernel a81 + media observations | `dotmac-media-observations-v0.1.0a1` |
| `dotmac-content` | `0.1.0a1` | [32348113583](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32348113583) | kernel a81 + media observations + content | `dotmac-content-v0.1.0a1` |
| `dotmac-publishing` | `0.1.0a1` | [32348989950](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32348989950) | kernel a81 + media observations + content + publishing | `dotmac-publishing-v0.1.0a1` |
| `dotmac-sites` | `0.1.0a1` | [32350030557](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32350030557) | full kernel/media/content/publishing/Sites composition | `dotmac-sites-v0.1.0a1` |

Every workflow installed the published artifact from the registry, registered
its manifest, and then wrote its release tag. All five tags point and peel to
the exact main revision above. This cumulative release-workflow composition is
artifact and manifest compatibility evidence; it is not product composition,
writer cutover or adoption. Backoffice and Sub remain candidate consumers, all
writer-retirement rows remain `not-started`, and the media-observations
adoption pause remains active.

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
| `dotmac-content` | `product-first` | `dotmac_mkt` | Preserve campaign-calendar planning, canonical post copy, asset references and approval-ready content state from `Campaign`, `Post`, `Asset` and their services. Stored bytes remain with `dotmac-files`; generic work management is outside this owner. Allocation a79 is installable through released kernel a81; Content a1 is registry-verified but remains uncomposed and unadopted. |
| `dotmac-sites` | `greenfield-after-inventory` | `none` | No qualifying site-builder implementation was found in Starter, `dotmac_mkt`, ERP, CRM, Sub or Backoffice. Gate 2 is historically green at `8bf12ddc5cb938714d090fc0b0e69b83fa78f2d2`, including all five Sites isolation/immutability canaries. Allocation and floor a81 plus Sites a1 are released and registry-verified; the module remains uncomposed and unadopted. |
| `dotmac-publishing` | `product-first` | `dotmac_mkt` | The owner keeps immutable releases, ordered opaque-target deliveries, monotonic attempts, normalized observations and explicit partial/all-failed reconciliation; Integrator retains transport. Allocation a80 is installable through released kernel a81; Publishing a1 is registry-verified but remains uncomposed and unadopted. |
| `dotmac-media-observations` | `product-first` | `dotmac_mkt` | Preserve normalized remote post/ad hierarchy and idempotent metric upserts from the qualifying Mkt implementation without provider transport or authoritative business consequences. Allocation a78 is installable through released kernel a81; Media Observations a1 is registry-verified but remains uncomposed and unadopted under the active adoption pause. |
| `dotmac-web-analytics` | `greenfield-after-inventory` | `none` | The focused privacy-first audit found no qualifying first-party observation owner. The module owns privacy-minimised events, property-scoped visitor/session evidence and deterministic rebuildable projections. Mkt's GA4 aggregate reader remains external/provider evidence for `dotmac-media-observations`; its HTTP/OAuth code is rejected. Integrator transports remote typed observations but never classifies, sessionises or attributes them. Allocation and floor a84 are the authorized release candidate; publication remains distinct from Backoffice/Sub adoption. |
| `dotmac-forms` | `product-first` | `dotmac_erp` | Preserve organization-scoped definitions, immutable versions, sections, typed fields/options, validation, submissions and answer snapshots from ERP's `forms` models and `FormEngineService`. Replace Organization and domain entity coupling with Tenant scope and opaque subject references. |
| `dotmac-campaigns` | `product-first` | `dotmac_sub` | Implemented on Starter main, but deliberately unreleased and unadopted. Registry-verified a72 contains its namespace allocation and Durable Timers a1; published a73 supplies the caller-session mechanics required by Sub and is therefore the effective package floor. Satisfy Sub's owner, lineage and timer-adoption gates before publication. Preserve audience building, sequences, send windows, canonical senders, attempt/outcome state, unsubscribe and pre-send suppression rechecks. Sub is the mandatory campaign source and cutover 1; Backoffice is cutover 2. CRM and `dotmac_mkt` are parity/retirement inputs, not competing owners. |

Every package needs its own `EXTRACTION.toml`, manifest, owner, namespace,
lineage, tenant-isolation canary, preserved parity tests, first cutover and
local-copy retirement gate. This inventory selects sources and records release
state; it does not claim that publication completed any product adoption.

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
   `dotmac-sites` and first-party Web Analytics start with their focused
   greenfield-after-inventory proofs and adopter canaries.
3. Implement one module slice at a time with its manifest, `mod_*` namespace,
   independent migration lineage, RLS canary, typed ports and owner row in
   `docs/ARCHITECTURE.md`.
4. Publish exact package releases and compose Backoffice without path or
   editable dependencies. Backoffice is the default first adopter and owns its
   rows; campaigns follows ADR-0056's source-first sequence of Sub then
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
