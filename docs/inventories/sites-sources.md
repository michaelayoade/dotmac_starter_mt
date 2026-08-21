# Site-builder source audit

**As of:** 2026-08-19
**Ruling:** `greenfield-after-inventory`
**Qualifying source:** none
**First candidate adopter:** Backoffice

This is the product-first absence proof for `dotmac-sites`. It narrows the
suite-level ruling in [`marketing-suite-sources.md`](marketing-suite-sources.md)
to one owner and records why landing pages and application templates are not a
site builder.

## Exact audit coordinates

| Repository | Revision | Finding |
| --- | --- | --- |
| `dotmac_starter_mt` | `c6ef6cd7b13105bd95c3faf354ffee9032077625` | No site/page/revision owner; the reference assembly explicitly has no marketing landing/public-root capability. |
| `dotmac_mkt` | `7f14ee598ceefed7ac3ba0963e5a36f5c4c5082d` | Campaign, post, delivery, advertising and analytics behavior exists; no site builder or versioned website aggregate exists. |
| `dotmac_sub` | `510b80ca7fab4f54a57f261872f94b5e972c8eb6` | Public landing and lead-capture routes are product adapters; no reusable site/page revision owner exists. |
| `dotmac_erp` | `dd6416cd981ffdf48564e2770b87d3cd7201186c` | Configurable landing/app-page content is application presentation; no versioned site composition or release owner exists. |
| `dotmac_crm` | `60daaa2dd305696636632f48505ab784110a55d2` | Admin landing routes exist; no site/page/revision owner exists. |
| `dotmac_backoffice` | `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d` | No site-builder term or writer was found; this is the greenfield first-adopter premise. |

The audit searched the pinned trees for site builder, website builder, CMS,
landing page, page revision, site revision, navigation, redirect, SEO and
publish/deploy vocabulary, then inspected the matches rather than treating
their names as proof. The positive matches were ordinary route/template
builders, configurable welcome text or existing marketing publication code.
None jointly owns stable sites, pages, immutable revisions and a releasable
snapshot. The Mkt coordinate and absence ruling were already checked into the
suite inventory; the other five exact objects were re-inspected locally on
2026-08-19.

## Nearest surfaces and disposition

| Observed surface | Why it does not qualify | Owner after this decision |
| --- | --- | --- |
| Product landing/public routes | They render one application's experience and carry no editable site aggregate or revision history. | Product adapter consumes a ready site release if it elects to do so. |
| ERP/Sub configurable welcome text | A setting is neither a page graph nor an immutable release. | Product setting remains until a deliberate adopter migration. |
| Starter and product templates | Files in an application release are code-owned presentation, not tenant-authored website state. | The application/design system remains owner. |
| Mkt `Post` and `PostDelivery` | These are editorial/publication facts, with no website navigation, redirects or site revision. | `dotmac-content` and `dotmac-publishing`. |
| Files and forms | Stored bytes and submitted answers are independent reusable authorities. | `dotmac-files` and `dotmac-forms`; sites holds opaque references only. |
| Hosting/DNS/provider state | External execution and evidence are transport concerns. | Integrator and connector plugins; domains/hosting owners where applicable. |

## Greenfield gate

Greenfield means no historical rows are synthesized and no fictional parity
suite is created. It does not weaken the cutover proof. Before Backoffice can
claim adoption it must repeat this census, refuse a newly appeared local
site/page/revision writer, compose the released module in its own database and
prove its local renderer serves exactly the module-owned ready digest.

The first implementation is local and provider-free. Remote deployment stays
closed until an assembly can map `SiteReleaseV1` to a typed publication artifact
without importing siblings into either module. The remote URL, deployment id or
webhook receipt is evidence, never the source of the website.
