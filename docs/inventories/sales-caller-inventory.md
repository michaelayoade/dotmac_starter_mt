# Sales caller and writer inventory

**Audit pins:** Starter `7828697ef11fb1ae765a5397dfa7dc221ae6207a` ·
Sub `f64946fc451ba94a1d4c8f0a61b7831367d5b598` ·
CRM `57e112f0757edcee6b9ad625ee3e13ebff5c7d71` ·
ERP `2749ec5396cbbd7a1132b394e85855a1d133a7cd`

This inventory defines what must move or retire when `dotmac-sales` is adopted.
It records callers; it does not authorize changes to Inbox, campaigns,
connectors, consent, retention, Orders or ERP back-office domains.

## Sub writers — implementation source and first adopter

### Canonical behavior to port

| Current writer | Rows/decisions | Adoption disposition |
| --- | --- | --- |
| `app/services/sales/service.py::Pipelines.create/update/delete` | Pipeline configuration | Port to module commands; leave the web/API adapter thin |
| `app/services/sales/service.py::PipelineStages.create/update/delete` | ordered opportunity Stages | Port with composite tenant identity and Stage/Pipeline consistency |
| `app/services/sales/lead_authoring.py::author_lead/edit_lead` | staff Lead/Party authoring and maintenance | Port sales fields; Party mutation becomes a typed product participant/adapter |
| `app/services/sales/lifecycle.py::create_party_lead`, `bind_lead_party`, `capture_lead_origin`, `stage_quote_acceptance` | Lead identity/origin/Won transition | Port Lead/origin rules; replace product Party/Subscriber relations with typed references |
| `app/services/sales/service.py::Leads.create/update/delete/update_stage/bulk_assign_pipeline` | legacy Lead mutation paths | Migrate callers to one module owner, then delete or make projections; do not preserve parallel commands |
| `app/services/sales/customer_quote_linkage.py::resolve_customer_quote_lead` | customer-to-Lead linkage | Keep as Sub adapter feeding a typed sales-subject reference; no customer table in module |
| `app/services/sales/quote_authoring.py::author_quote/change_quote_discount` | Quote/line authoring and append-only discount evidence | Primary module code/test source |
| `app/services/sales/service.py::Quotes.create/update/delete` and `QuoteLineItems.create/update/delete` | legacy Quote/line lifecycle paths | Fold legal transitions into the module owner and retire duplicate paths |
| `app/services/sales/quote_acceptance.py::accept_quote` | locked acceptance plus current downstream conversion | Port only locking, expiry, immutable snapshot, idempotency and output; downstream account/order/project/work stays in Sub consumers |
| `app/services/sales/quote_delivery.py` | delivery request plus direct Draft→Sent mutation | Delivery remains external; replace direct assignment with a module lifecycle command |

### Direct or projection writers that must not survive cutover

| Path | Current write | Required treatment |
| --- | --- | --- |
| `app/services/team_inbox_commands.py::_record_lead_merge` | mutates `Lead.metadata_` with Inbox merge evidence | Do not edit Inbox in this extraction. Define a typed sales owner command and migrate this caller during Sub adoption |
| `app/services/quotes_mirror.py` | writes Quote mirror status | Retire the parallel projection or rebuild it strictly from owner outputs; never treat it as authority |
| `app/services/sales/lead_intake.py` and `app/services/sales/capture.py` | create Party-first Leads/origin from typed intake | Keep intake decisions in Sub; invoke module Lead/origin commands after adoption |
| `app/services/inbox_lead_actions.py` | selects Pipeline/Stage and invokes Lead intake/actions | Adapter only after adoption; no direct module-row writes |
| `app/services/referrals.py` and `app/services/referral_account_conversion.py` | referral-to-Lead/account consequences | Referrals remain their own owner; hand a typed Lead command to sales |
| `app/services/quote_deposits.py` and `app/services/sales/selfserve.py` | read accepted Quote and drive billing/order behavior | Downstream consumers only; consume handoff/accepted snapshot, never write sales state |

The Inbox, referral, billing and self-serve files are migration obligations,
not permission for `dotmac-sales` to import those domains.

### Sub adapter/read surface

The following families import sales models/services and must be checked during
the caller flip:

- API: `app/api/sales.py`, `app/api/crm_sales.py`, `app/api/me.py`,
  `app/api/reseller.py`, `app/api/lead_capture_webhooks.py`;
- web: `app/services/web_sales.py`, `app/web/admin/sales.py`,
  `app/web/customer/quotes.py`, `app/web/public/lead_intake.py`,
  `app/web/admin/lead_intake.py`;
- reports/projections: `app/services/sales/reports.py`,
  `app/services/web_sales_dashboard.py`,
  `app/services/sales/quote_discount_reporting.py`,
  `app/services/reseller_crm_views.py`, status presentation and lifecycle audit;
- documents/delivery: `quote_documents`, `quote_activity`, `quote_delivery`,
  communication attachments and customer portal views; and
- downstream sales-to-service: `sales_orders`, `sales_fulfillment`,
  `sales_lifecycle_reconciliation`, customer-experience and project services.

Reads may temporarily shadow both stores. Writes may target only one owner at a
time. The flip inventory records each adapter as `legacy`, `shadow-read`,
`module-write`, or `retired`; a nullable feature flag is not an authority plan.

## CRM writers — retirement inventory

### Primary sales service/API

| Path | Current behavior | Retirement obligation |
| --- | --- | --- |
| `app/services/crm/sales/service.py` | constructs all five CRM sales row types; CRUD/search/Kanban/stage moves; generic Quote acceptance | Freeze after backfill, redirect commands to the accepted product API/contract, then delete local writers |
| `app/api/crm/sales.py` | 21 Pipeline/Stage/Lead/Quote/line routes | Replace caller-by-caller; remove after zero traffic |
| `app/api/sales.py` | 5 Sales Kanban/stage routes | Replace with authoritative read/command adapter, then remove |
| `app/web/admin/crm_leads.py` | 8 mounted Lead routes | Owner mapping verified; parity/data/cutover/traffic/deletion still open |
| `app/web/admin/crm_quotes.py` | 13 mounted Quote routes | Split query/authoring/acceptance replacement at their exact owners |
| `app/web/admin/crm_sales.py` | 16 mounted routes | Ten Pipeline/Stage routes are this slice; six SalesOrder routes stay with Orders |
| `app/services/crm/portal_quotes.py` and `app/api/crm/portal.py` | customer Quote request/list/accept and deposit/SalesOrder consequences | Re-point accepted-Quote behavior; downstream consequences remain separate; retire local Quote writes |

### Secondary CRM Lead/Quote writers

| Path | Write discovered | Disposition |
| --- | --- | --- |
| `app/services/crm/campaigns.py` | creates a Lead from campaign response | **Inventory only.** Campaign/audience ownership remains unverified; future adapter must call sales without moving campaign into this module |
| `app/services/crm/contacts/service.py` | creates a Lead from a contact | Re-point to sales after Party/customer mapping is verified |
| `app/services/crm/referrals.py` | creates a Lead | Referral remains separate; call sales through its public contract |
| `app/services/crm/serp_targets.py` | creates a Lead | Classify caller and migrate or remove; no new acquisition owner is inferred |
| `app/services/erpnext/importer.py` | constructs Lead, Quote and Quote lines and updates imported state | Replace with a typed import adapter into the owner; do not put ERPNext/provider logic in sales |
| `app/services/crm/inbox/resolve_gate.py` | creates Leads from Inbox resolution | **Inventory only for sales call.** Do not change Inbox ownership here |
| `app/services/meta_webhooks.py` | invokes Lead create and directly mutates attribution metadata/source | **Inventory only.** Connector/Meta transport stays outside this goal; eventual writer calls sales through Integrator/product API |

### CRM dependent readers/consequences

- workqueue/report/search providers read Lead/Quote state;
- `selfcare.notify_quote_event` sends best-effort `quote.created` and
  `quote.accepted` messages to Sub;
- `app/services/events/handlers/selfcare_customer.py` reads Quote lines and
  triggers invoice/order/project consequences; and
- portal templates and admin forms assume CRM-local ids and state.

These dependencies are parity/cutover work. They are not reasons to retain CRM
as a writer or to put transport/downstream behavior in the module.

## ERP callers — observed but not migrated here

ERP's `finance.ar.quote` model/service/web paths and Quote conversion calls are
distinct back-office owners. They were revision-pinned for requirements parity
only. This goal does not re-point, extract or retire them.

## Writer-retirement proof

Before a sales authority switch, generate a per-file writer inventory from the
pinned source and check it in as a two-directional ratchet: the build fails if a
writer appears **or disappears** without the baseline changing in the same
review. Include a sensitivity test that injects a direct `Lead`, `Quote` or
`QuoteLineItem` construction/assignment and proves the detector fires.

After cutover the allowed writer baseline is zero outside the module service and
migration/backfill code. Read projections remain allowed only when they name
their source output and reconciler. CRM route deletion additionally requires
the Sub retirement ledger's complete zero-traffic contract.
