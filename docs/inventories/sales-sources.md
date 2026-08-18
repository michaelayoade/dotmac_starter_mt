# Sales sources

**As of:** 2026-08-17
**Starter:** `7828697ef11fb1ae765a5397dfa7dc221ae6207a`
(`origin/main` at audit start)
**Sub:** `f64946fc451ba94a1d4c8f0a61b7831367d5b598`
(`origin/dev` at audit start; clean dedicated worktree)
**CRM:** `57e112f0757edcee6b9ad625ee3e13ebff5c7d71`
(`origin/main` at audit start; clean dedicated worktree)
**ERP:** `2749ec5396cbbd7a1132b394e85855a1d133a7cd`
(`origin/main`; revision-pinned object reads because its root was dirty)
**Decision:** [ADR-0033](../adr/0033-sales-authority-stops-at-an-accepted-quote.md)

This is the complete product-first source ruling for Leads, Pipelines,
opportunity Stages and Quotes through acceptance. It does not audit campaigns,
customer-retention case management, connector transport, ERP back-office
domains or Orders.

## Verdict

`dotmac-sales` is **product-first from Sub with mandatory port deltas**.

Sub is the only qualifying implementation: it is production-used, owns the
approved Sales-to-Service SOT, has typed owner commands, locks the acceptance
aggregate, preserves immutable origin and discount evidence, refuses accepted
snapshot mutation, emits owner outputs, and has focused unit, architecture,
integration and browser tests.

CRM supplies parity and retirement evidence only. Its sales implementation is
a weaker ancestor/subset: service methods commit internally, raise HTTP
exceptions, permit accepted Quote/line mutation, and combine acceptance with
post-commit SalesOrder/Project creation without Sub's locking/outbox contract.
ERP supplies finance-quote requirements and negative cases only. It has no Lead
or Pipeline model and its AR Quote is a separate organization-scoped
back-office aggregate.

No source has the reusable tenant-plane/RLS shape. No source has the required
accepted-Quote-only handoff. Those are mandatory, tested generalisations at
typed seams, not permission to replace Sub behavior with a greenfield design.

## Sub — qualifying implementation

### Model and contract source

| Path at the Sub pin | Relevant behavior |
| --- | --- |
| `app/models/sales.py` (1,080 lines) | `Pipeline`, `PipelineStage`, `Lead`, `LeadOriginCapture`, `Quote`, `QuoteLineItem`, `QuoteDiscountHistory`; string state vocabularies; append-only ORM guards; quote money |
| `app/schemas/sales.py` (318) | typed CRUD/list payloads for Pipeline, Stage, Lead, Quote and lines |
| `app/services/sales/service.py` (2,417) | Pipeline/Stage commands, Lead query/lifecycle/kanban, Quote query/lifecycle/line commands, parent-Quote locking |
| `app/services/sales/pipeline_configuration.py` (132) | Stage type, colour/icon normalization and presentation |
| `app/services/sales/lead_authoring.py` (1,108) | atomic staff Lead author/edit command, actor and Party/pipeline validation, command fingerprint |
| `app/services/sales/lifecycle.py` (553) | Party-bound Lead creation, immutable origin capture, Won transition |
| `app/services/sales/customer_quote_linkage.py` (96) | flush-only exact customer-to-Lead linkage participant |
| `app/services/sales/quote_authoring.py` (988) | atomic Lead-backed Quote and line authoring, exact totals, idempotent discount history |
| `app/services/sales/quote_acceptance.py` (515) | parent lock, expiry check, immutable guard, replay and current cross-domain acceptance |
| `docs/designs/SALES_TO_SERVICE_LIFECYCLE_SOT.md` | approved owner/transition contract and current as-built chain |

The current source tables also contain product-specific relations that are
**not** module schema: Subscriber, SystemUser, Reseller, Region, Campaign,
CampaignRecipient, IntegrationInbox, ProjectType, inventory/offer/tax rows,
PDF/file records, delivery requests, deposit-invoice links and SalesOrder
relations. They become opaque references, typed inputs or external adapters;
they do not cross into the module lineage.

### Behavior proved by the source

- Lead lifecycle is Party-first. Exact source-interaction replay uses an
  immutable origin row and fingerprint; changed content under the same identity
  is a conflict.
- Pipeline/Stage selection is validated, Stage order and presentation are
  normalized, and Lead search/filter/summary and Kanban behavior are owner
  queries.
- Quote authoring is separate from acceptance, requires a Lead/customer
  binding and line items, calculates exact Decimal totals and preserves an
  append-only discount revision history.
- Every Quote/line mutation locks the parent Quote and calls the stable
  `accepted_quote_immutable` guard. Revised accepted terms require a new Quote.
- Acceptance locks Quote and Lead, refuses expiry, stages the accepted state and
  current downstream consequences in one owner command, and replays by Quote
  identity without duplicating the result.
- Failed event staging rolls the source transaction back. Later owner-output
  delivery is visible and retryable.

### Parity source tests

The mandatory starting suites at the pin are:

- `tests/test_quote_acceptance_workflow.py` — 13 tests;
- `tests/test_quote_financial_safety.py` — 9;
- `tests/test_web_sales_quote_authoring.py` — 20;
- `tests/test_web_sales_lead_authoring.py` — 17;
- `tests/test_sales_services.py` — 24;
- `tests/test_sales_lifecycle_chain.py` — 4;
- `tests/architecture/test_sales_lifecycle_chain_boundary.py` — 16;
- `tests/architecture/test_pipeline_settings_boundary.py` — 3;
- `tests/architecture/test_sales_quote_list_query_boundary.py` — 3; and
- `tests/architecture/test_lead_list_query_boundary.py` — 2.

Additional required evidence includes `tests/test_lead_capture_webhook.py`,
`tests/test_quote_discounts.py`, `tests/test_quote_documents_and_delivery.py`,
the four Postgres Lead/Quote integration suites, and the sales Lead/Quote
Playwright suites. Order/funding/fulfillment tests are boundary evidence only
and are not ported into `dotmac-sales`.

### Source defects and mandatory port deltas

1. **No tenant isolation.** Source rows have no `tenant_id`, composite
   tenant-scoped keys, RLS/FORCE policies or tenant-role grants.
2. **Acceptance crosses owners.** It constructs/attaches Subscriber,
   SalesOrder, Project, Tasks, InstallationProject and WorkOrders. The module
   must stop at the accepted snapshot and output.
3. **Product FKs and metadata leak inward.** Campaign, Inbox, subscriber,
   reseller, region, catalog, tax and project concepts must enter only through
   typed neutral ports or opaque references.
4. **Generic services still expose multiple mutation paths.** Authoring owners
   coexist with legacy `sales.service` CRUD; adoption must retire/gate the
   duplicate paths, not carry both forward.
5. **Delivery mutates Quote state.** `sales.quote_delivery` directly changes
   Draft to Sent. The transport adapter must call the module lifecycle owner.
6. **Out-of-domain writers remain.** Inbox merge metadata and the legacy
   `quotes_mirror` projection write sales rows directly. They must be migrated
   through the owner or retired, without changing Inbox/transport ownership.
7. **No module-level DB immutability.** The source service/ORM guards are strong
   but reusable accepted evidence also needs a catalog-level guard proved
   against direct SQL.

## CRM — parity and retirement evidence only

### Source surface

| Path at the CRM pin | Evidence |
| --- | --- |
| `app/models/crm/sales.py` (193 lines) | five tables: `crm_pipelines`, `crm_pipeline_stages`, `crm_leads`, `crm_quotes`, `crm_quote_line_items` |
| `app/schemas/crm/sales.py` (201) | legacy API payload/result shapes |
| `app/services/crm/sales/service.py` (1,214) | CRUD/search/Kanban/stage moves/quote totals and generic acceptance |
| `app/services/crm/portal_quotes.py` (484) | portal request/accept/deposit flow and downstream SalesOrder effects |
| `app/api/crm/sales.py` (21 routes) | CRUD API callers |
| `app/api/sales.py` (5 routes) | Kanban/stage callers |
| `tests/test_crm_sales_services.py` (1,508; 79 tests) | the useful legacy behavior matrix |

The retirement baseline in Sub pins CRM
`87f6273d040a3c3cc27213801da80ee91d278673`. It is an ancestor of the current
CRM audit pin, and the sales model/schema/service/API/web/test paths above are
unchanged between them. The ledger's historical source inventory therefore
still describes the current sales surface. This does **not** advance its Sub
target revision or prove retirement.

### Useful requirements

- Pipeline and Stage CRUD, ordering and active-state behavior;
- Lead CRUD, search, deduplication, probability, Kanban and stage movement;
- Quote CRUD/search, line arithmetic and status filtering;
- portal/customer Quote request and acceptance outcomes; and
- the exact writer/caller inventory needed to prove retirement.

### Behaviors rejected as source

- native DB enums and unscoped tables;
- service-owned commit/rollback and HTTP exception contracts;
- check-then-act acceptance with no parent lock or owner output;
- post-commit SalesOrder/Project creation;
- mutable or deletable accepted Quote headers and lines;
- provider/campaign/Inbox knowledge inside sales writers; and
- name-based parity without backfill, shadow, traffic or deletion evidence.

## ERP — distinct requirements source

Revision-pinned paths read:

- `app/models/finance/ar/quote.py`;
- `app/services/finance/ar/quote.py`;
- the AR quote web adapter/routes/templates; and
- the corresponding AR/IFRS tests.

ERP has no Leads, Pipelines or opportunity Stages. Its AR Quote contributes
requirements worth considering at typed seams: scoped quote numbers;
`NUMERIC(19,4)` money and captured FX rate/terms; explicit sent/viewed/accepted/
rejected instants and actors; Draft-only mutation; acceptance only from
Sent/Viewed; and expiry refusal. Conversion to invoice or SalesOrder is
explicitly downstream and stays outside `dotmac-sales`.

ERP is not the module source because its Quote is finance/back-office scoped,
has no tenant RLS, uses HTTP/service commit patterns, and lacks the accepted
snapshot/outbox/concurrency guarantees required here.

## Starter — no pre-existing implementation

At the Starter pin there is no sales/lead/quote/pipeline package, feature or
test implementation. That absence does not authorize greenfield code: Sub is a
qualifying source. It also means the audit must precede `packages/dotmac-sales`
and its `EXTRACTION.toml`, exactly as rule 24 requires.

## Source ruling

Port Sub behavior and parity tests first. Generalize only the tenant boundary,
the typed product seams, the database immutability/RLS guarantees and the
accepted-Quote owner output specified by ADR-0033. Treat CRM as a writer to
retire and ERP as a requirements/negative-test source. The accepted P11
evidence is now checked in. Create the package canary-first from this ruling;
P11 does not waive parity, tenancy, release or adoption proof.
