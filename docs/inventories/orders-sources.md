# Orders sources

**As of:** 2026-08-18 (source decision from 2026-08-15, revalidated below)
**Starter:** `7c93ebf` (`origin/main`)
**Sales candidate:** `504ed25` (`origin/agent/dotmac-sales-implementation`)
**Sub:** `d1a1a913e` (`origin/main`)
**ERP:** `dd6416cd` (revision-pinned read)
**CRM:** `d363af3d` (revision-pinned read)
**Vendor CP:** `89848017d6b8`
**Integrator:** `d014116e63ad`
**Decision:** [ADR-0030](../adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md)
§5b authorizes `dotmac-orders` and rules Sub the starting point; §7 keeps ERP's
physical sales order ERP-owned and retires CRM's parallel writer.

This audit resolves which implementation the order aggregate and its line
snapshots start from, and — the reason the module exists at all — whether any
source actually snapshots a line price immutably. It does not claim any source
is safe to copy unchanged.

## Verdict

`dotmac-orders` is **product-first with a mandatory port delta**.

Sub is the only qualifying source. It is the sole implementation with an order
aggregate that is transport-neutral, has a typed funding/coverage receipt owner,
receipted cross-owner outputs and a drift reconciler, and it is the only source
whose commercial snapshot immutability is enforced *anywhere* by code and proved
by tests. ERP is a requirements and negative-test source for accepted-order
transition invariants and for the FX/terms capture Sub lacks; its aggregate is
not adoptable. CRM is strictly a subset of Sub's model with none of Sub's
guarantees, and is a retirement inventory only — the evidence does not come
close to outweighing Sub's, and nothing below should be read as adopting it.

The port delta is not optional and is the substance of the module:

1. **No source keeps an order line immutable.** Sub, ERP and CRM all mutate
   accepted lines in place. Sub's immutability guard stops at the *quote*.
2. **No source captures a price-version reference.** All three copy a scalar
   `unit_price` and reference a *live* catalogue/item/tax row. The version-one
   contract's "captured price/terms/specification references" has to be built,
   not ported.
3. **No source has tenant isolation.** Sub has no `tenant_id` and no RLS
   anywhere; ERP scopes by `organization_id` with no row policy; CRM likewise.

### 2026-08-18 revalidation

The qualifying-source decision still holds at the refs above. Sub remains the
only source with the finite coverage gate, receipted owner outputs and lifecycle
reconciler; ERP still contributes transition/FX requirements rather than an
adoptable aggregate; CRM remains a retirement-only subset.

One source defect was fixed after the original audit and is no longer a port
obligation: Sub `origin/main` contains the reviewed sequence beginning at
`9e8f78a0e` and `c99d39522`, plus the typed waiver follow-up through
`1ba0021a7`. Generic/operator order updates can no longer set funding fields;
the internal path requires an explicit `FundingAuthority`; and a waiver is an
accountable order decision rather than manufactured payment evidence. The
historical finding remains below because it explains the boundary, but the
current source is proof that the boundary can be retired in the first adopter.

The concurrent Billing package confirms the correct negative boundary. Its
pending `0.1.0a1` contract keeps allocation and coverage internal and publishes
`ReceivablePositionV1`/accounting facts, but no per-order coverage verdict.
Orders does not derive one from balance or allocation arithmetic, and an
assembly adapter cannot make that decision either. The named split is now:
Sales freezes finite eligibility requirement membership; the owner of each
external decision publishes explicit evidence addressed to one requirement;
Orders alone evaluates whether the complete registered set is present and
returns a reasoned `FulfillmentEligibilityDecisionV1`. Billing therefore need
not publish a synthetic coverage API. Sub's existing finite funding receipts
are the first evidence source; a greenfield adopter fails closed until it binds
equivalent explicit producers.

The separate committed `dotmac-sales` candidate explains why an Orders module
could look as though it already existed, but it does not own one. Its checked-in
contract explicitly stops at an immutable accepted Quote, publishes
`sales.accepted-quote.v1`, and forbids creating or identifying a `SalesOrder`.
That is the correct upstream boundary and confirms `dotmac-orders` is a distinct
owner rather than a duplicate package.

`AcceptedQuoteHandoffV1` is now a mechanically consumable Orders input. It
carries exact line/totals values, explicit currency minor units, accepted terms
content, price and specification provenance, component tax evidence and the
finite eligibility requirement membership required by `SubmitOrderCommand`.
`tests/architecture/test_sales_orders_handoff.py` proves a product adapter can
translate those fields without a live catalogue read or a new commercial
decision. Sales remains the accepted-Quote owner and still creates no order.

## Sub source

Mandatory paths:

- `app/models/sales.py` (`SalesOrder` L967, `SalesOrderLine` L1047,
  `SalesOrderInvoiceLink` L809);
- `app/models/sales_order_funding.py` (`SalesOrderFundingGate`,
  `SalesOrderFundingObligation`);
- `app/services/sales_orders.py` (1789 lines — order/line CRUD, totals,
  `stage_funding_transition`, `apply_funding_consequences`);
- `app/services/sales_order_funding.py` (the finite funding gate);
- `app/services/sales/quote_acceptance.py` (`assert_quote_mutable`,
  `_locked_quote`, atomic conversion);
- `app/services/sales_fulfillment.py` (receipted consumption, published
  contract snapshots); and
- `app/services/sales_lifecycle_reconciliation.py` (report-then-apply drift
  repair through canonical owners).

Usage: `SalesOrder` is referenced by 24 tracked application files; the
`sales_orders` service is imported by 5 others (`app/api/sales_orders.py`,
`app/services/sales/selfserve.py`, `app/services/sales_fulfillment.py`,
`app/services/sot_registry/domains/sales_referrals/acquisition.py`,
`app/services/web_sales.py`). The funding gate is referenced by 6 more.

What the code actually proves:

- **Acceptance copies values, not rows.** `SalesOrders._stage_from_quote_
  acceptance` writes `description`, `quantity`, `unit_price`, `amount` and a
  copied `metadata_` dict onto each `SalesOrderLine`. `inventory_item_id` is a
  bare UUID with no FK. That is a value snapshot and is the right shape.
- **Exactly-once conversion.** `uq_sales_orders_quote_id` plus
  `_locked_quote`'s `SELECT ... FOR UPDATE` plus `create_from_quote`'s refusal
  to create (it only returns an existing order, else demands acceptance replay)
  gives one order per accepted quote with a real lock, not a check-then-insert.
- **Accepted commercial snapshots are immutable *upstream*.**
  `quote_acceptance.assert_quote_mutable` refuses `quote_fields`,
  `quote_deactivation`, `line_item_create`, `line_item_update`,
  `line_item_delete` once the quote is accepted, with a stable error code and a
  typed `attempted_mutation`. This is the behaviour to generalise — and to move
  from the quote to the order.
- **Funding is evidence, not arithmetic.** `SalesOrderFunding` binds a finite
  obligation set to a gate under `SELECT ... FOR UPDATE`, refuses set changes
  once funded (`gate_already_funded`), refuses unregistered obligations
  (`obligation_not_in_finite_set`), refuses naive resolution instants
  (`invalid_resolution_instant`), is idempotent per obligation, and advances
  exactly once while staging one owner output. Its own docstring is explicit
  that `SalesOrder.amount_paid` is "provenance, not authority".
- **Cross-owner effects leave through receipted outputs.**
  `stage_funding_transition` emits inside the committing transaction;
  `sales_fulfillment.consume_funding_satisfaction` wraps the effect in
  `consume_owner_output(consumer, event_id, ...)`, so replay is a no-op.
- **Reconciliation exists.** `reconcile_sales_to_service_lifecycle(apply=False)`
  reports drift and only repairs through the canonical owners.

Tests: `tests/test_sales_order_funding.py` (6 tests: partial-never-advances,
funded-exactly-once, unregistered-obligation-rejected, funded-set-frozen,
idempotent registration, empty-set-fails-closed);
`tests/test_quote_financial_safety.py` (9, including
`test_accepted_quote_fields_and_deactivation_are_rejected` and
`test_accepted_quote_line_items_are_rejected_without_money_drift`, which assert
no money drift on the derived order);
`tests/test_sales_orders_services.py` (18, including sequence continuation and
repair, duplicate-order rejection, `create_from_quote` idempotence, payment
transitions); `tests/test_quote_acceptance_workflow.py` (13, including
rollback-on-event-staging-failure and duplicate acceptance);
`tests/test_sales_lifecycle_chain.py` (4);
`tests/test_sales_to_service_lifecycle.py` (3, append-only evidence);
`tests/architecture/test_sales_lifecycle_chain_boundary.py` (17 static guards,
including `test_accepted_quote_snapshot_mutations_are_policy_guarded`).

What those tests do **not** cover: order-line immutability (there is none to
test), concurrency, tenant isolation, rollback of a consuming transaction, or
out-of-order/lost delivery of a funding output.

## ERP source

Paths read:

- `app/models/finance/ar/sales_order.py` (`SalesOrder`, `SalesOrderLine`,
  `Shipment`, `ShipmentLine`);
- `app/services/finance/ar/sales_order.py` (972 lines);
- `app/services/finance/ar/quote.py::QuoteService.convert_to_sales_order`; and
- `tests/ifrs/ar/test_sales_order_service.py`.

Usage is narrow: only 2 tracked application files reference
`SalesOrderService` — the service itself and
`app/services/finance/ar/web/sales_order_web.py`. This is not a broadly adopted
production base in the sense ERP's numbering service was (41 caller files).

**General accepted-order invariants that belong in the shared owner:**

- an explicit, refused-by-default transition ladder
  (`DRAFT → SUBMITTED → APPROVED → CONFIRMED`), each step raising
  `Cannot <verb> SO in {status} status` rather than silently no-oping;
- submission/approval/confirmation/cancellation each recording actor and
  instant (`submitted_by/_at`, `approved_by/_at`, `confirmed_at`,
  `cancelled_at`, `cancellation_reason`);
- cancellation refused once downstream consumption exists — ERP's version is
  "cannot cancel an SO with existing shipments", whose product-neutral form is
  "cannot cancel once a fulfillment request has been accepted";
- a captured **FX and terms** snapshot on the header (`currency_code`,
  `exchange_rate`, `payment_terms_id`) carried forward at conversion. Sub has
  no exchange rate at all; this is ERP's one genuinely additive contribution to
  the shared contract;
- `UniqueConstraint(organization_id, so_number)` — order identity is unique
  *within a scope*, which is the tenant-plane shape Sub's global
  `uq_sales_orders_order_number` is not.

**Physical-goods-specific and therefore OUT:** `quantity_shipped`,
`quantity_invoiced`, `quantity_backordered`, `FulfillmentStatus`,
`is_backorder`, `allow_partial_shipment`, `unit_of_measure`, `ship_to_*`,
`shipping_method`, `shipping_amount`, the `Shipment`/`ShipmentLine` tables,
`fulfillment_percent` / `is_fully_shipped` / `is_fully_invoiced`,
`_reserve_stock_on_confirm` and its warehouse/`StockReservationService`
coupling, `create_invoice_from_so`, and the `inv.item` / `gl.account` /
`tax.tax_code` / `core_org.*` foreign keys.

Tests: 46 focused tests across 12 classes — every one built on `MagicMock`,
`MockSalesOrder`, `MockSalesOrderLine` and `MockShipment`. None touches a
database. The 18 `tests/e2e/test_sales_orders.py` Playwright tests assert page
and form presence, not invariants. ERP therefore supplies requirements and
negative cases, not parity evidence.

## CRM source

Paths read: `app/models/sales_order.py` (97 lines),
`app/services/sales_orders.py` (589 lines), `app/services/field/sales_orders.py`,
`app/api/sales_orders.py`, `app/api/field/sales_orders.py`,
`app/services/events/handlers/selfcare_customer.py`, `app/services/billing_sync.py`.

CRM's model is the direct ancestor of Sub's — Sub's `app/models/sales.py`
docstring says so and names the CRM file. It is a strict subset: same
`SalesOrderStatus`/`SalesOrderPaymentStatus` vocabulary, same money columns,
same `metadata_` line tagging, minus the funding gate, the invoice links, the
discount history, the order-level discount fields and the subscriber
first-class link. There is nothing in CRM that Sub does not already have in a
better-guarded form. **CRM does not outweigh Sub and is not the base.**

What a retirement would have to displace — 21 tracked application files
reference the `SalesOrder` model family (16 the `SalesOrder` class itself):

- **Cross-app writers into Sub** (the real coupling):
  `selfcare_customer.push_sales_order_payment_to_selfcare`,
  `push_sales_order_subscription_to_selfcare`,
  `ensure_installation_invoice_for_sales_order` and `_ensure_installation_invoice`,
  driven from `sales_orders._sync_sales_order_payment_to_sub`; plus the
  re-runnable sweeper `billing_sync.backfill_sales_payments_to_sub`. These push
  payments, subscriptions and installation invoices into Sub over HTTP. Sub has
  since rewired every one of them natively (`sales_orders.py` L296-300 names the
  CRM source it replaced), so retirement means switching these off, not porting
  them.
- **Local dependents** that must be re-pointed at a synchronized projection
  under ADR-0024: `billing_risk_reports.py` (an AR-aging report derived from
  `SalesOrder.balance_due` and `payment_status` — a receivable projection living
  in CRM), `reseller_commissions.accrue_for_sales_order`,
  `work_lifecycle.WorkEntityType.sales_order`, `subscriber_reports.py`,
  `web_admin_dashboard.py`, and the field-sales surfaces
  (`app/services/field/sales_orders.py` + `app/api/field/sales_orders.py`),
  which scope "my orders" by JSON `metadata_["created_by_person_id"]`.

Tests: `test_sales_order_payment_sync.py` (7), `test_sales_order_subscription_sync.py`
(5), `test_sales_order_plan_line.py` (2), `test_field_sales_orders.py` (2),
`test_sales_orders_project_fallback.py` (5). All mock the push boundary; they
test the writers that retire. There is no line-immutability, funding, tenancy or
concurrency test. One is literally named
`test_sales_order_line_create_retries_splynx_installation_invoice` — a provider
brand in the sales-order suite.

## Do not port

- **Closed enums.** `SalesOrderStatus`, `SalesOrderPaymentStatus`,
  `FundingGateState` and `BillingRecordAuthority` are Python enums, and
  `fundinggatestate` / `billingrecordauthority` are PostgreSQL `ENUM` types
  (Sub `alembic/versions/434_sales_funding_erp_exports.py`). ERP's `SOStatus`
  and `FulfillmentStatus` are the same mistake with a fiscal accent. Per
  ADR-0008 these become open registered strings; adding an order state or a
  resolution kind must not require a release.
- **Jurisdiction and currency constants.**
  `sales_orders.SALES_ORDER_VAT_RATE = Decimal("0.075")` and
  `fixed_vat_amount()` hardcode Nigerian VAT into the order aggregate;
  `currency` defaults to `"NGN"` in both Sub and CRM;
  `_record_order_payment_evidence` falls back to `"NGN"`. Tax is a captured
  policy snapshot supplied by the caller, never a module constant.
- **Product vocabulary and host coupling.** `subscriber_id`, `Subscriber`,
  `owner_agent_id`, `crm_agents`, `inventory_item_id`, `person_id`,
  `Project`/`InstallationProject`, `ProjectType`, `lead_source`,
  `SO-%06d`; ERP's `organization_id`, `ar.`/`inv.`/`gl.`/`tax.`/`core_org.`
  schemas and `settings.default_functional_currency_code`.
- **Provider names.** CRM's `splynx`/`selfcare` push vocabulary, and any
  `if provider ==` shape. A provider is an Integrator connector binding.
- **Cross-owner foreign keys.** `SalesOrderFundingObligation.obligation_id`
  has `ForeignKey("billing_obligations.id")`; `SalesOrderInvoiceLink` has FKs to
  `invoices` and `subscribers`; ERP's line FKs reach four other schemas. A peer
  owner's identity is an opaque reference (ADR-0030 §3), never an FK.
- **Framework errors and service-level commits.** `HTTPException(status_code=…)`
  is raised from inside Sub's and CRM's order services; `SalesOrders.create`,
  `.update`, `.delete`, `SalesOrderLines.create` and `.update` each call
  `db.commit()` themselves. The module raises typed domain errors and commits
  nothing.
- **Swallowed non-transactional effects.** ERP wraps every
  `fire_workflow_event` / `emit_hook_event` in `try/except Exception:
  logger.exception(...)`; CRM does the same for commission accrual; Sub's
  `_record_sales_order_payment` catches `Exception`, logs a warning and calls
  `db.rollback()` inside a service. Effects leave through the durable outbox or
  they fail the transaction.
- **Second writers.** CRM's `_sync_sales_order_payment_to_sub` and
  `billing_sync.backfill_sales_payments_to_sub` are exactly the parallel-authority
  shape ADR-0024 forbids.
- **Soft delete as cancellation.** `is_active = False` on both the order (Sub
  and CRM `SalesOrders.delete`) and the line is not a lifecycle transition, has
  no reason, no actor, no instant, and silently changes derived totals because
  `_recalculate_order_totals` filters on `is_active`.

## Known defects/deltas

1. **Accepted order lines are mutable in place — the central defect.**
   `sales_orders.SalesOrderLines.update` does
   `for key, value in data.items(): setattr(line, key, value)` over any field,
   including `unit_price`, `quantity` and `description`, with **no status
   guard**. `SalesOrderLines.create` adds lines to an order at any status.
   `SalesOrderLine.updated_at` even carries `onupdate=`. The mutation is
   reachable from the JSON API (`PATCH /sales-order-lines/{id}`,
   `app/api/sales_orders.py` L130) and from the admin form
   (`app/services/web_sales.py` L3735-L3744). Sub's immutability guard covers
   only the quote. CRM has the identical hole; ERP mutates
   `quantity_shipped`/`quantity_invoiced`/`fulfillment_status` on the line and
   declares `cascade="all, delete-orphan"`, so an order can lose lines outright.
2. **The mutation propagates into published downstream contracts.**
   `sales_fulfillment._funding_contract_snapshots` reads `line.unit_price`,
   `line.quantity` and `line.description` *live* at funding time and stages them
   as the `sales.fulfillment.funding_applied` contract payload. A line edited
   after acceptance silently rewrites what downstream owners are told was bought.
3. **An operator can manufacture funding.** `SalesOrders.update` /
   `update_from_input` accept `total`, `amount_paid`, `payment_status` and
   `paid_at` straight from a web form; `_apply_payment_fields` then promotes the
   order to `paid`, and `stage_funding_transition` emits
   `sales_order.funding_satisfied`, which creates subscriptions, provisioning
   orders and a recorded payment. There is no obligation evidence on that path
   at all — it bypasses the funding gate that exists precisely to prevent it.
4. **Two competing funding authorities coexist.** `SalesOrder.amount_paid`
   arithmetic and `SalesOrderFundingGate` are both live; the SOT manifest
   records `sales.order_funding` as `AuthorityMigrationState.SHADOWING` with old
   owner "SalesOrder.amount_paid arithmetic and metadata payment-origin joins".
   The module must ship with only the gate.
5. **Order-number allocation is O(n) and self-repairing.**
   `_generate_order_number` locks the sequence, then runs
   `_highest_existing_order_number`, which selects *every* `SO-%` order number
   and parses each in Python, then advances the cursor past it. CRM's
   `_next_sequence_value` is a plain lock-or-insert with no repair. Both are
   displaced by `dotmac-numbering`, whose own dossier already names Sub's order
   series as an adopter.
6. **Naive datetimes.** ERP writes `datetime.utcnow()` into timezone-aware
   `submitted_at`, `approved_at`, `confirmed_at`, `cancelled_at` and
   `quote.converted_at`. Sub is correct here (`datetime.now(UTC)`) and the
   funding gate actively refuses a naive instant — port Sub's rule.
7. **ERP's focused suite is entirely mocked.** 46 tests, zero database. No
   concurrency, rollback, or isolation proof exists for the transition ladder
   the shared owner is adopting from it.
8. **No source has tenant isolation.** Sub has no `tenant_id` column and no
   `ROW LEVEL SECURITY` statement anywhere in `app/` or `alembic/`. ERP filters
   by `organization_id` in Python (`if so.organization_id != coerce_uuid(...)`)
   with no row policy. CRM has neither. There is no isolation evidence to port.
9. **ERP loses the item reference on the quote path.**
   `convert_to_sales_order` copies `item_code` but not `item_id`, so a
   quote-derived line carries a string where a direct-entry line carries a
   foreign key.
10. **Totals are derived from live rows, not from snapshots.** Sub's and CRM's
    `_recalculate_order_totals` sum `SalesOrderLine.amount` where
    `is_active`; ERP's `_recalculate_totals` iterates `so.lines`. The derivation
    direction is right; the inputs are mutable, which is defect 1 again.

## Shared contract

Version one **owns**:

- **customer and currency reference** — an opaque customer identifier supplied
  by the assembly, plus an ISO currency code and, where the order is
  cross-currency, the captured FX rate at acceptance (ERP's `exchange_rate`
  shape, exact via `dotmac_kernel.money`);
- **order identity** — one order row, an order reference unique within the
  tenant, and an idempotency identity for submission so a retried checkout
  cannot produce two orders;
- **commercial line snapshots** — one immutable row per line, written and
  frozen when checkout is submitted, then carried unchanged through the
  separate acceptance decision. No `onupdate`, no `is_active`, no operator edit
  path. A correction is a new order or a documented amendment order, never an
  in-place rewrite;
- **captured price/terms/specification references** — each line records the
  price it was sold at as an exact value *and* the immutable price-version
  identifier it came from, the terms snapshot in force, and an opaque
  specification reference naming what was bought. The specification reference is
  never imported, parsed, resolved or interpreted by this module; it is carried
  and republished;
- **totals derived from those snapshots** — subtotal, discount, tax and total as
  consequences of the frozen lines, recomputed only when the snapshot set is
  first written;
- **submission, acceptance and cancellation** — the transition ladder with actor,
  instant and reason, refused by default (ERP's shape, product-neutral names),
  with cancellation refused once a fulfillment request has been accepted;
- **fulfillment eligibility** — Sales supplies the accepted finite membership;
  Orders owns an askable set decision over opaque requirement references and
  typed deduplicated owner receipts: no advance on partial evidence, one advance
  ever, unregistered references refused, set frozen once satisfied; and
- **fulfillment request publication** — one typed per-line fulfillment request
  emitted through the outbox when the order is accepted and covered, carrying the
  frozen line snapshot rather than a live read.

It does **NOT** own:

- CRM quote authoring, quote lifecycle, discount negotiation, leads or pipelines;
- invoice existence, invoice state, receivable, settlement, allocation or dunning
  — Billing owns those (ADR-0020); Orders only observes explicitly addressed
  evidence and owns the separate fulfillment-eligibility decision;
- subscription contracts, cadence, proration or recurrence — `dotmac-subscriptions`;
- offer or price definition and versioning — `dotmac-subscriptions` publishes the
  immutable price version this module references;
- fulfillment attempts, saga steps, compensation or convergence — `dotmac-fulfillment`;
- service lifecycle or permission to transition — `dotmac-domains` / `dotmac-hosting`;
- stock reservation, warehouse, shipment, backorder or delivery;
- document numbering policy — `dotmac-numbering` allocates, Orders chooses the
  series and supplies the business date;
- any provider call, credential, webhook or wire payload;
- GL postings, fiscal periods or statutory accounting — Dotmac ERP.

**The cart line.** A cart stays assembly/UI state and is out of scope. Neither
Sub, ERP nor CRM has one: a revision-pinned search of all six repositories for
cart/checkout code found only payment-checkout and CI-checkout matches, and Sub's
self-serve path (`app/services/sales/selfserve.py`) goes straight from a pinned
address to a draft quote. The durable business record starts when checkout
produces the order snapshot, because that is the first moment a price, a term and
a specification are *fixed* — everything before it is a re-priceable selection
with no immutability obligation and no audit value. Putting the cart inside the
owner would force it to hold mutable line rows, which is exactly the defect this
module exists to remove.

**Plane:** tenant only. Every table carries `tenant_id NOT NULL` with FORCEd RLS.
There is no platform plane: ADR-0030 §7 gives Vendor CP the platform planes of
Billing, Subscriptions and Collections only, and no inspected repository has a
control-plane order. Declared, not inferred.

## Kernel floor

Capabilities this owner consumes from `dotmac_kernel` at `7c93ebf`, now named
and enforced by the package manifest and root migration:

- `db` — the single transaction authority; the order aggregate never commits.
- `money` — exact `Money`, `Currency`, `ExchangeRate` for every line amount,
  total and captured FX rate. No float, no bare `Decimal` arithmetic.
- `idempotency` — `execute_once` / `fingerprint_of` for order submission, for
  coverage-resolution receipts, and for the conflict rule when a key is replayed
  with a different fingerprint (this is what Sub hand-rolls in
  `execute_owner_command` / `consume_owner_output`).
- `messaging` (`outbox`, `envelope`, `inbox`, `relay`, `worker`) — fulfillment
  request publication and coverage-observation intake. Nothing leaves in-request.
- `audit` — `write_audit_event` for submission, acceptance, cancellation and
  lifecycle/refusal evidence, with declared `audit_actions` and the verified
  `tenant_audit_log.v1` storage prerequisite.
- `planes` — `ModulePlane.tenant` declared on the manifest; the gate must see a
  single-plane declaration, not infer one.
- `namespaces` / `prerequisites` / `migrations` — one immutable `mod_<code>`
  schema, one lineage, logical `requires`/`provides` effects rather than a named
  foreign revision.
- `errors` — stable typed error classes; no `HTTPException` crosses the boundary.
- `modules` / `features` / `product_manifest` — manifest declaration of
  permissions, capabilities, audit actions and setting domains (ADR-0008 open
  registries, not enums).
- `tenancy` — request tenant context for the RLS canaries.

An assembly may obtain the opaque order reference from `dotmac-numbering`, but
Orders does not import or require that sibling: its command receives the
already-allocated reference. Orders likewise has no durable-timer dependency —
quote expiry belongs to the quote owner and dunning to Collections.

## Fresh proof required

1. tenant-plane RLS isolation: a second tenant can neither read nor write
   another tenant's order, line, or coverage receipt. No source supplies this.
2. an accepted line snapshot cannot be updated or deleted by service, API,
   admin surface, direct ORM or SQL write; persistence-level attempts fail and
   leave the row and derived totals byte-identical.
3. two concurrent submissions of the same checkout produce exactly one order and
   one reference; the loser replays rather than duplicating.
4. a rolled-back consuming transaction leaves no order, idempotency ledger row,
   or staged outbox row. Reference allocation is a separate owner's transaction.
5. same-key/same-fingerprint submission replay returns the original order;
   a changed fingerprint raises `IdempotencyConflict`.
6. partial coverage never advances the gate; the complete registered set
   advances it exactly once even under concurrent resolution; an unregistered
   reference is refused; the set is frozen after satisfaction.
7. coverage resolutions delivered out of order converge to the same gate state,
   and a duplicated delivery is a no-op (Sub proves idempotence per obligation,
   not ordering).
8. a lost fulfillment-request delivery is repaired by the reconciler without
   re-emitting a second request or mutating a snapshot.
9. cancellation is refused once a fulfillment request has been accepted, and the
   refusal is recorded.
10. a price-version reference that no longer resolves does not change the
    captured line price — the snapshot answers, not the catalogue.
11. the specification reference is never dereferenced: an architecture test that
    the module imports no service-owner package and no code path parses that
    field.
12. totals equal the sum of the frozen snapshots for every order in a
    property-style sweep, including a cancelled one.

## Adoption and retirement

**Dotmac Sub is the first adopter**, not Cloud: it has the 24 caller files, the
live funding gate already declared `SHADOWING`, and the reconciler needed to
prove the cutover. Cloud is the first *composition* but has no rows to shadow.

Cutover slicing:

1. install the module, migrate its lineage, and write orders in shadow beside
   `sales_orders`; compare snapshot, totals and gate state per order;
2. cut over the coverage gate first — it is already a shadow owner with a
   declared old owner, so retiring `SalesOrder.amount_paid` arithmetic and the
   `update_from_input` payment path is one bounded change that also closes
   defect 3;
3. cut over acceptance and line snapshots, deleting `SalesOrderLines.update`,
   `SalesOrderLines.create`, `PATCH /sales-order-lines/{id}` and the admin form's
   line-edit branch in the same change — an immutable snapshot with a live edit
   route is not immutable;
4. cut over submission/cancellation and retire the `is_active` soft delete;
5. re-point `sales_fulfillment._funding_contract_snapshots` at the published
   snapshot instead of a live line read.

**CRM retirement** follows Sub's cutover, not the package's completion. Switch
off `_sync_sales_order_payment_to_sub`, the two `push_sales_order_*` handlers,
`_ensure_installation_invoice` and `billing_sync.backfill_sales_payments_to_sub`
first — Sub already owns each of those natively. Then re-point
`billing_risk_reports`, `reseller_commissions`, `work_lifecycle`,
`subscriber_reports`, `web_admin_dashboard` and the field-sales surfaces at a
rebuildable projection synchronized under ADR-0024, and drop CRM's
`sales_orders` / `sales_order_lines` tables last.

**ERP retires nothing.** Its physical sales order stays ERP-owned (ADR-0030 §7);
ERP contributes the transition ladder, the FX/terms capture and its negative
cases as ported tests, and receives immutable accounting facts as before.

The package is not adopted until Sub runs the exact released version and its
displaced local writers are gone. A green test suite is not a cutover.
