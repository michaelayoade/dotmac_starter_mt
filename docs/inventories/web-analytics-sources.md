# Web-analytics source inventory

**As of:** 2026-08-18
**Starter:** `4b285cb3b0da82b9a2a3d5f39f4aca7da19105ff`
**Mkt:** `1a185b47164e34601769c84976e95578996c4523`
**Sub:** `3f8d74825bee47b98c3c532229b72f3a8a5b16aa`
**CRM:** `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`
**ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`
**Backoffice:** `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d`
**Integrator:** `783baf23cbf5129ef18763f97f646f684e6db3a3`
**Academy application:** `40423a` (full local commit)
**Integration client:** `4714d9411e7512cc00944fd44583bc38812e9839`

This is the mandatory product-first audit for `dotmac-web-analytics`. It was
completed before behaviour implementation. The ruling is
**greenfield-after-inventory**: no audited implementation owns a first-party,
privacy-minimising, append-only web-observation ledger with deterministic
sessionisation, deletion-safe rebuildable projections and drift repair.

## Implementation provenance

The reusable implementation already exists in committed Starter history. The
initial cohort landed at `b89df1f` and its corrected committed head is
`abef05ac5fd121ca254bb74eafcc7c9970e90dfd`. The marketing-suite branch ports
that committed package, tests and evidence and reconciles its immutable
namespace onto the suite allocation train; it does not rebuild the engine from
the dirty `agent/dotmac-web-analytics` prototype worktree.

This does not change the source ruling above. `greenfield-after-inventory`
describes the absence of a qualifying PRODUCT implementation before the module
was built. It does not mean the already-committed Starter module may be ignored
or replaced.

The audit did find adjacent implementations. They supply requirements, adopter
evidence and explicit retirement boundaries; none supplies behaviour that may
be ported as the module's starting point.

## Classification

| Repository or surface | Classification | Evidence and disposition |
|---|---|---|
| `dotmac_mkt` | External/provider observation only | `app/adapters/google_analytics.py` asks GA4 for daily `sessions`, `screenPageViews`, `totalUsers` and `bounceRate`; `app/tasks/analytics_sync.py` upserts those provider aggregates into mutable `ChannelMetric` rows. There is no raw first-party collection protocol, visitor evidence, event registry, session reconstruction, retention deletion or rebuild. The metric names are migration requirements only. Provider credentials, APIs and aggregates stay outside this module. |
| `dotmac_mkt` analytics views/services | Presentation/reporting only | `app/services/analytics_service.py` aggregates mutable daily metrics for display. It is not an observational source and must not be relabelled as one. |
| `dotmac_sub` KPI analytics | Presentation/reporting only | `app/api/analytics.py` and `app/services/analytics.py` expose business KPI configuration and reporting, not website observations. |
| `dotmac_crm` KPI analytics | Presentation/reporting only | `app/models/analytics.py` and `app/services/analytics.py` are business reporting surfaces. |
| `dotmac_erp` analytics/reporting | Presentation/reporting only | The searched reporting and KPI engines read business-domain state. They do not collect first-party website observations. |
| CRM chat widget | Negative evidence; retirement source for identity-bearing visitor tracking | `static/js/chat-widget.js` constructs a browser fingerprint from user agent, language, screen dimensions, colour depth, timezone and canvas; persists a token in local storage; and sends the full current URL and referrer. `app/models/crm/chat_widget.py` stores fingerprint hash, raw IP, user agent, page/referrer URLs, metadata and later customer identity. Those are explicit anti-requirements for web analytics. The chat/lead workflow remains CRM-owned; its visitor-tracking overlap must be retired rather than ported. |
| CRM campaign open/click tracking | Retirement source; campaign-owned | `app/services/crm/campaign_tracking.py` implements recipient-linked email pixels and signed redirect clicks with mutable counters. It is useful proof that redirect authentication matters, but it is outbound-campaign evidence tied to a person and remains outside web analytics. |
| Sub campaign open/click state | Retirement source; campaign-owned | `app/models/comms_campaign.py` stores campaign delivery/open/click state. Campaigns owns that lifecycle. |
| Sub Fiber chat collector | Negative evidence and adopter requirement | `app/api/chat_widget.py` proves a public-site adapter can enforce exact origin and rate limits before dispatching a typed command. `app/services/team_inbox_widget.py` then collects name, email, phone, message, page URL and referrer and creates sales/support state. The adapter topology is relevant; the payload and business consequences are not analytics and cannot be ported. |
| Sub UTM fields on sales records | Negative boundary evidence | `app/models/sales.py` keeps lead-origin markers with sales state. Sales owns the lead and official acquisition decisions. Web analytics may retain sanitised anonymous campaign-marker observations but must not write or infer this sales state. |
| Backoffice | Negative evidence; first adopter assembly | No first-party tracker, visitor/session store or analytics writer exists at the audited revision. This is a clean adopter, not a source. It must compose the generic contract with property-specific configuration outside the module. |
| Academy application | Negative evidence; candidate site | A repository search found no GA/GTM, Plausible, Matomo, Mixpanel, Segment, Facebook Pixel or first-party page-view collector. Learning progress and authentication sessions are domain state, not web analytics. |
| `dotmac_integrator` | Negative evidence; transport assembly | The repository is a thin assembly over `dotmac-integration`. It contains no website connector and no analytics decision engine. |
| `dotmac-integration` and `dotmac-integration-client` | Supporting transport, not source | The platform module owns connector installation, ingress/outbox delivery, replay evidence and provider-neutral transport. The stateless client carries idempotency/request identifiers. Web analytics reuses those transport semantics; it does not copy the control plane or make transport authoritative for event meaning. |
| `dotmac-connector-whatsapp` | Negative evidence | The only audited connector plugin is messaging-specific. No website analytics connector exists. |
| Public `dotmac.ng` and `fiber.dotmac.ng` sites, observed 2026-08-18 | Negative evidence; real remote adopters | Both public WordPress pages load CRM's chat widget. Raw HTML contained no GA/GTM, Plausible, Matomo, Mixpanel, Segment, Amplitude or Facebook Pixel tag. The pages prove real property/origin demand and an overlapping privacy-risk tracker; they do not define module behaviour. |
| Public `academy.dotmac.io`, observed 2026-08-18 | Negative evidence; real application adopter candidate | The rendered application loads local design-system/public assets and no recognised external analytics tag. Its repository likewise has no first-party tracker. |
| Unversioned `academy-website-update` fragments | Presentation only | The local HTML fragments contain page content and links, no tracking implementation, and are not a Git source. They cannot qualify as behavioural evidence. |

## Search method and paths

The audit searched committed revisions rather than dirty working trees. It used
filename and content searches for `analytics`, `tracking`, `pageview`,
`visitor`, `fingerprint`, `session`, `referrer`, `utm_*`, `gtag`, GTM,
provider SDKs, connector ingress/outbox and campaign redirect/pixel code, then
read the models, services, adapters, migrations and tests behind relevant hits.

Principal paths read:

- Mkt: `app/models/channel_metric.py`, `app/adapters/google_analytics.py`,
  `app/tasks/analytics_sync.py`, `app/services/analytics_service.py`,
  `tests/test_analytics_sync.py`, `tests/test_analytics_daily_totals.py`,
  `tests/test_web_analytics.py`.
- Sub: `app/api/analytics.py`, `app/services/analytics.py`,
  `app/api/chat_widget.py`, `app/services/team_inbox_widget.py`,
  `app/models/sales.py`, `app/models/comms_campaign.py`, and the native widget
  tests.
- CRM: `app/models/analytics.py`, `app/services/analytics.py`,
  `app/models/crm/chat_widget.py`, `app/services/crm/chat_widget.py`,
  `static/js/chat-widget.js`, `app/services/crm/campaign_tracking.py` and its
  tests/migration.
- ERP: reporting/analytics service and test matches plus the repository-wide
  external tracker search.
- Backoffice and Integrator: complete repository searches for web collection,
  analytics, visitor/session, referrer, campaign-marker and connector code.
- Starter: `packages/dotmac-integration`,
  `packages/dotmac-connector-whatsapp`, kernel idempotency/outbox contracts,
  architecture tests and module manifests.
- Academy: committed `app/`, `static/`, templates and tests plus the deployed
  public HTML.

Mkt was available in two clean local audit clones at the exact revision above.
An attempted remote refresh was refused by the configured GitHub credentials,
so the ruling is explicitly limited to that revision. That limitation does not
turn provider aggregates into first-party events, and no missing remote commit
is assumed.

## Why no source qualifies

A qualifying source needed behavioural proof for at least the central
invariants: append-only first-party observations; property-scoped pseudonyms;
typed, declared event attributes; deterministic late-event session rebuilding;
consent/filter evidence; explicit expiry and privacy deletion; and projections
that rebuild after deletion without count leakage.

No audited implementation proves even that combined core:

- Mkt starts after the provider has already aggregated and classified the data.
- CRM's browser tracker persists the exact fingerprint, raw network and
  identity-bearing inputs this module forbids.
- Sub's public-site collector is a typed lead/chat workflow containing PII, not
  anonymous measurement.
- Integrator can deliver and deduplicate a message but deliberately does not
  decide what the message means.
- Backoffice and the public sites have no local analytics owner to extract.

There are therefore no parity tests to preserve. The package's initial tests
are greenfield canaries derived from this inventory and the ownership ADR.

## Greenfield ruling and constraints

The permitted source mode is **greenfield-after-inventory**, with these hard
consequences:

1. Tenant-only V1. No audited platform-plane adopter exists.
2. One provider-neutral contract serves local and remote collection. Transport
   provenance is recorded, but transport does not select behaviour.
3. No adopter hostname, route, property, event code, consent mode or retention
   period exists in package constants. Assemblies configure properties and
   supply typed event declarations and explicit policies.
4. The package accepts no PII, raw IP, unrestricted query string, request body,
   form value, raw user agent, browser fingerprint or free-form metadata.
5. GA4/provider aggregates are not imported as first-party events. A separate
   provider-observation owner may consume them without crossing this boundary.
6. The CRM chat widget and campaign trackers are retirement surfaces, not code
   sources. Their business workflows remain with CRM/campaigns.

## Adoption and retirement order

1. Compose the exact released package in Backoffice using configuration for a
   real first-party website. A remote website enters through an Integrator
   connector and the same typed collection adapter used locally.
2. Adopt the same release independently in Sub. Sub may emit a declared,
   anonymous `form_completed`-class event only after its form/chat owner has
   accepted the submission; analytics receives no form values or person ids.
3. After shadow comparison shows no unexplained event, session, filter or
   aggregate drift, remove overlapping visitor/session/URL tracking from the
   CRM widget. Campaign open/click writers retire through the campaigns owner,
   not through this module.

No production cutover target is inferred by this inventory.
