# Media-observations product-first source dossier

**Audit date:** 2026-08-18  
**Decision:** [ADR-0032](../adr/0032-media-observations-own-provider-reports-not-attribution.md)  
**Package dossier:**
[`packages/dotmac-media-observations/EXTRACTION.toml`](../../packages/dotmac-media-observations/EXTRACTION.toml)

## Outcome

`dotmac_mkt` is the qualifying code and test source for external advertising /
social hierarchy normalization and daily performance behavior. The audit does
**not** establish that its writer is production-used authority. Its default
branch is tested and deployment-shaped, but the available repository and GitHub
evidence contains no deployment record, named runtime or observed database.

Therefore:

- port Mkt's behavior and parity tests;
- do not claim a production authority transfer or retire its writer;
- keep Backoffice and Sub as candidates, not consumers; and
- if Michael later names an Mkt production target, inspect that exact runtime
  before any shadow/cutover plan is authorized.

The module is an immutable aggregate-fact owner, not an attribution engine or a
connector control plane.

## Candidate validation evidence

Implementation commit `8aed76cdbb677d5f286b701546b8d84130bbc5c6`
passed, from a fresh detached Observer worktree:

- `make check` with Poetry 2.4.1 from the committed hash-locked bootstrap;
- the full unit and architecture suite, including the clean-wheel installation,
  public-surface, import-independence and provider-free fake conformance proofs;
- the full disposable PostgreSQL suite, including tenant isolation, forced RLS,
  append-only grants/triggers, duplicate ingest, replay conflict, restatement,
  hierarchy and projection-repair canaries; and
- database container, network and volume teardown.

[GitHub CI run 32148037659](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32148037659)
passed on the same implementation commit: all quality jobs, unit coverage,
PostgreSQL integration and teardown, Python 3.11/3.12 floors, consumer boot and
Docker smoke. No release job or product adoption ran. Engineering Standards is
PR-only and remains pending because Michael did not authorize a PR.

## Exact audit pins

| Repository | Exact source inspected | Finding |
|---|---|---|
| `dotmac_starter_mt` | `12df64ad69ee76cc334948f9375748c98aa6338e` (`origin/main`) | Kernel/module contracts, tenant RLS, namespace, extraction and connector-control-plane rules. |
| `dotmac_mkt` | `1a185b47164e34601769c84976e95578996c4523` (`master`) | Qualifying hierarchy/metric behavior and tests; deployment-shaped, production use unproven. |
| `dotmac_crm` | `60daaa2dd305696636632f48505ab784110a55d2` (`origin/main`) | Campaign reply and provider metadata currently write Lead attribution/ROI projections: negative boundary evidence. |
| `dotmac_sub` | `510b80ca7fab4f54a57f261872f94b5e972c8eb6` (`origin/main`) | Authoritative immutable Lead origin and sales-to-service lifecycle boundary. |
| `dotmac_erp` | `dd6416cd981ffdf48564e2770b87d3cd7201186c` (`origin/main`) | Required fleet sweep found no advertising/social hierarchy or media-performance owner. |
| `dotmac_integrator` | `e7ec250be9c681883f3acab8a8a19614fec30d29` (`origin/main`) | Thin assembly of released `dotmac-integration`; transport authority, not media-domain authority. |
| `dotmac_backoffice` | `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d` (local clean branch) | Tenant-plane candidate assembly with no modules, remote, image, database or deployment. Adoption paused. |

The Mkt audit used a fresh clean clone at
`/private/tmp/dotmac_mkt_media_observations`. Dirty local product worktrees were
not evidence and were not modified.

## Mkt: qualifying behavior and mandatory corrections

### Behavior to preserve

| Source | Qualifying behavior | Parity disposition |
|---|---|---|
| `app/models/ad_campaign.py` | common campaign -> group -> advertisement hierarchy and daily metric identity | Port as provider-neutral versioned node declarations, entity facts, parent facts and explicit metric periods. |
| `app/services/ad_sync_service.py` | maps multiple provider shapes onto the common hierarchy; a repeated natural key does not duplicate rows | Preserve shape convergence and idempotent replay. Replace in-place overwrite with a new immutable restatement fact plus deterministic current projection. |
| `tests/test_ad_sync_service.py` | hierarchy mapping for three payload families and repeat-sync replacement | Port provider-free fixture equivalents. The repeat-sync test becomes replay plus restatement history proof. |
| `app/models/channel_metric.py` and `tests/test_analytics_daily_totals.py` | daily aggregate reporting over channel/post metrics | Port aggregate period reads and total parity without importing content/Post identity. |
| `app/tasks/analytics_sync.py` and `tests/test_analytics_sync.py` | missing remote objects are noticed | Preserve the signal as an archive/deletion observation. Explicitly reject the source's local `db.delete(post)` consequence. |

### Behavior that must not port

1. `AdPlatform` and fixed metric/provider enums.
2. `PLATFORM_FIELD_MAP` or provider names in package code.
3. Provider credentials, adapters, endpoints, polling tasks, commits, rollbacks
   and checkpoints in `app/tasks/ad_sync.py` / `analytics_sync.py`.
4. `_safe_decimal` converting malformed provider values to zero.
5. Mutable overwrite of campaign/group/ad state and one wide metric row.
6. Counts stored through `Numeric(18,6)` or Python `float`.
7. Spend without exact currency and minor-unit provenance.
8. Foreign keys to local Campaign, Channel, Post, Lead, Party or customer rows.
9. Deleting a local Post because a remote listing no longer returns it.
10. Treating provider conversion claims as Dotmac acquisition or revenue.

### Production-use conclusion

The Mkt default-branch pin has a successful GitHub Actions CI run. The
repository contains Compose/Nginx deployment assets and the hostname
`marketing.dotmac.io`, which shows deployment intent. GitHub's deployment list
for the repository is empty, and no checked-in ledger identifies a production
database, image, run or current writer. Those facts do not prove that the
implementation is unused; they prove only that production use is **unverified**.

The module extraction may proceed under Michael's owner direction, but an
authority cutover may not be inferred from source quality.

## CRM: reporting requirement and negative evidence

At `origin/main`, CRM's `Lead` carries `lead_source`, `campaign_id` and
`campaign_recipient_id`. Migration
`ca2026062800_add_lead_campaign_attribution.py` calls the result campaign ROI.
`app/services/crm/campaigns.py::attribute_lead_from_reply` creates or updates a
Lead with `lead_source="Campaign"`, while `campaign_attribution_report` combines
recipient, Lead and won/open values. `tests/test_campaign_lead_attribution.py`
proves that behavior.

`app/services/meta_webhooks.py` additionally copies provider attribution fields
into Person, Lead and Conversation state and creates provider-named Lead origin.
This is evidence of what the reusable module must **not** do. CRM's needs belong
to a later attribution resolver that consumes media observations alongside
first-party and commercial evidence.

## Sub: authoritative acquisition boundary

Sub's approved `docs/designs/SALES_TO_SERVICE_LIFECYCLE_SOT.md` names:

- verified provider receipt -> `integration.inbox`;
- Party-first capture/source replay -> `sales.capture`;
- immutable origin -> `sales.lead_lifecycle`; and
- downstream Quote, acceptance, Order, provisioning, Subscription and support
  owners separately.

`app/models/sales.py::LeadOriginCapture` stores structured immutable evidence,
and `app/services/sales/lifecycle.py` refuses a changed origin.
`tests/test_customer_lifecycle.py` proves origin fields and their legacy Lead
source projection are immutable. External media ids in that record are Lead
origin evidence owned by Sub, not entity identity the media module may update.

The media module can publish aggregate external facts for a future resolver. It
cannot create a Party/Lead, choose Lead origin, attach a customer, accept a
Quote, or assign official acquisition.

## ERP: required negative sweep

Starter hard rule 24 requires ERP and Sub to be inventoried for every shared
package. An exact `origin/main` search across `app`, `tests` and `docs` found no
advertising/social entity hierarchy, provider media metric ingestion or media
attribution owner. ERP's analytics metric store is organizational reporting,
not external media observations. The only LinkedIn occurrence is a recruitment
source example. ERP therefore supplies no implementation or parity test to this
extraction.

## Integrator and connector boundary

`dotmac-integration` SPI 1.2 is the generic connector contract. Its capability
registry states the division explicitly: the business module declares what a
capability means, Integration validates and binds it, and a connector plugin
implements it. `dotmac_integrator` is the independent thin assembly that pins
that module.

Media observations publish normalized domain contracts without importing the
Integration package. A later assembly can declare a media-observation
capability, and a connector can conform independently to both surfaces.

The repository sweep found no authorized Meta, Google, TikTok, LinkedIn or other
media connector plugin. The only connector package currently present is the
WhatsApp ingress adapter, which is unrelated. The present scope therefore
includes provider-free fake conformance only; real connector certification is a
named later action.

## Backoffice and adoption state

Backoffice is the recommended first tenant-plane operating surface because its
assembly is deliberately empty and its tenancy/migration gates are explicit.
It is not evidence of adoption today: its README says nothing runs anywhere,
the repository has no remote, and there is no image, database or deployment.

Michael paused adoption on 2026-08-18. No Backoffice, Sub, Mkt, Integrator or
connector file is changed by this extraction. The future sequence remains:

1. give Backoffice a real remote/CI and pin the exact released kernel/module;
2. compose the tenant lineage explicitly;
3. shadow the qualifying Mkt behavior without changing authority;
4. reconcile every divergence;
5. if Mkt production authority is proven, cut over and retire its local writer;
6. only then count Backoffice as a consumer; and
7. adopt the same exact release independently in Sub.

None of those steps is authorized by the present change.

## Frozen V1 contract checklist

- Tenant plane only; aggregate media facts only.
- Aware provider/source and receipt instants remain distinct.
- Opaque installation and transport receipt references are never dereferenced.
- Domain identity and fingerprint exclude transport identity.
- Same identity/same fingerprint replays; changed fingerprint conflicts.
- Corrections append a linked restatement.
- Versioned node and metric declarations are data, not provider enums.
- Count, decimal, money, duration and ratio values are structurally distinct.
- Conversion count/value remains labelled `provider_reported`.
- Periods are half-open, valid and non-overlapping.
- Missing parents and cycles remain visible drift, never silent roots.
- Archive/deletion is observed state, never local destructive deletion.
- Current projections and drift reports rebuild from immutable facts.
- No raw payload, imported audience, person profile or attribution writer.
