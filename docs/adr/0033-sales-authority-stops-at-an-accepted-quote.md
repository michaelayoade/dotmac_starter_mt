# ADR-0033: Sales authority stops at an accepted quote

- Status: Accepted
- Date: 2026-08-17
- Decider: Michael
- Scope: `dotmac-sales`, Sub's sales adoption, and CRM sales-writer retirement
- Related: ADR-0006 (product-first extraction), ADR-0014 (at-most-once),
  ADR-0017 (adoption is the scarce resource), ADR-0024 (applications compose by
  synchronizing data), ADR-0030 (complete commerce owners), ADR-0031
  (authority-cutover evidence)
- Evidence: [`sales-sources.md`](../inventories/sales-sources.md),
  [`sales-caller-inventory.md`](../inventories/sales-caller-inventory.md),
  [`sales-parity-and-canaries.md`](../inventories/sales-parity-and-canaries.md),
  [`sales-extraction-dossier.md`](../inventories/sales-extraction-dossier.md),
  [`sales-retirement-ledger.md`](../inventories/sales-retirement-ledger.md)

## Context

The fleet carried three apparently competing claims:

1. Sub's approved Sales-to-Service SOT names Sub's sales services as the
   authority from Lead capture through service activation.
2. CRM's agent-facing implementation still contains Lead, Pipeline, Stage,
   Quote and acceptance writers, and stale CRM guidance calls CRM the quote
   system of record.
3. Starter's measured `sales-agreements` family combines Leads, Quotes,
   SalesOrders, contracts and referrals in one counting bucket.

They are not three owners. The approved Sub SOT is the current authority. CRM
is a retirement source. The Starter family is a measurement bucket, not a
domain boundary. A reusable module needs a narrower boundary so it does not
become a second orders, customer-account, project or service owner.

Sub is the qualifying implementation. It has the production-used owner
commands, row locking, immutable origin/discount evidence, accepted-quote
mutation guards, idempotent acceptance and the parity tests. CRM is a weaker
ancestor/subset whose remaining value is caller, behavior and retirement
evidence. ERP's AR Quote supplies finance-oriented requirements and negative
cases, but it is a distinct back-office aggregate and not the code source.

## Decision

### 1. `dotmac-sales` owns opportunity state through acceptance

The Starter-owned installable module owns exactly:

- Leads and their lifecycle;
- immutable, provider-neutral acquisition-origin evidence;
- Pipelines and ordered opportunity Stages;
- Quote header and line authoring;
- Quote discount history and commercial totals;
- Quote lifecycle through `accepted`, including expiry/refusal rules; and
- the immutable accepted-Quote snapshot and its owner output.

It is tenant-plane and stateful. Its eventual manifest, schema and migration
lineage follow ADR-0006; no namespace is reserved by this ADR.

### 2. The boundary is a versioned accepted-Quote handoff

Acceptance commits one immutable commercial snapshot and one
`AcceptedQuoteHandoffV1` owner output in the same transaction. Delivery occurs
after commit with durable retry. A downstream consumer receipts
`(consumer, event_id)` in the same transaction as its local consequence; replay
is an exact no-op and reuse of an event identity with different content is a
conflict.

The v1 handoff is product-neutral and contains:

- `schema_version`, `event_id`, `tenant_id`, `occurred_at`, `quote_id` and the
  accepted snapshot/version fingerprint;
- the Lead id and an opaque, typed sales-subject reference supplied through a
  product seam, never a Sub Subscriber model;
- currency, subtotal, discount, tax and total as exact decimal strings;
- an ordered immutable line snapshot with line id, description, quantity,
  unit price, discount/tax contribution, amount and optional opaque
  catalogue/offer version references;
- source, correlation and causation references; and
- a content digest covering the versioned envelope.

It carries no `sales_order_id`, Project, WorkOrder, invoice, Subscription or
provider payload.

### 3. Orders and downstream conversion remain separate owners

`dotmac-sales` does not own `SalesOrder` or `SalesOrderLine` rows, does not
import `dotmac-orders`, and does not create customer accounts, projects, tasks,
work orders, invoices, service orders or subscriptions. An application may
consume the accepted-Quote handoff and ask those local owners to act, but it
must not put that consequence inside the reusable sales owner.

This deliberately changes the seam of Sub's current acceptance command. Today
Sub accepts the Quote, converts the account, creates the SalesOrder and creates
implementation scope in one cross-domain transaction. During adoption that
behavior is split at the accepted-Quote commit/output boundary. The old command
remains authoritative until the module is adopted and cut over; no parallel
writer is allowed.

### 4. Product knowledge enters through typed seams

The module may consume typed, provider-neutral ports for actor eligibility,
sales-subject identity, catalogue/offer snapshots, tax decisions, clock and
owner-output dispatch. It does not import an assembly, sibling module, CRM/Sub
models, HTTP exceptions, provider clients or campaign/Inbox code. Opaque
references preserve correlation without creating cross-application foreign
keys.

### 5. Sub adopts first; CRM only retires

Sub is the first consumer and the source of behavior/tests. Adoption requires:

1. tenant-aware backfill into module-owned rows;
2. report-only reconciliation and full-column typed digests;
3. shadow reads and command/result comparison;
4. one authority switch sealed under ADR-0031;
5. caller-by-caller removal of Sub local writers; and
6. CRM writer, route, job and webhook retirement after its own evidence gates.

CRM contributes no module implementation and never becomes a shadow writer.
Its sales tables remain readable migration input until retirement is sealed.

### 6. P11 remains an external implementation gate

The source audit, ownership decision, canary contract and retirement planning
are permitted evidence work. Creating the stateful package or its lineage is
not. At the pinned Starter revision, the accepted checked-in
[`p11-adoption-status.md`](../inventories/p11-adoption-status.md) says P11 is
`UNMET`: no real product has run the kernel migration lineage in production.
This ADR does not clear, reinterpret or modify that gate.

Implementation begins only when the platform/adoption workstream checks in the
accepted production-lineage evidence required by ADR-0017. A prepared branch,
rehearsal, stamp, package pin or Starter-only run is insufficient.

### 7. Adjacent domains are explicitly out

Campaigns, audience selection, surveys, Inbox/conversations, WhatsApp,
connector transport, consent and customer-retention case management are not
part of this decision. Campaign ownership remains unverified pending its own
audit. Checked-in retention guidance conflicts and requires a separate explicit
owner decision. ERP back-office quote/order extraction is unchanged.

## Consequences

- A Quote is the last commercial object sales owns; acceptance is a durable
  fact, not permission for sales to own fulfillment.
- Sub's current atomic acceptance behavior is source evidence and a migration
  liability: it must be split without weakening rollback, idempotency or money
  correctness.
- CRM can be removed only after module adoption and production evidence; a
  similar Sub route is not retirement proof.
- The measured `sales-agreements` family remains reproducible, but its
  disposition is disaggregated: `dotmac-sales` through acceptance,
  `dotmac-orders` after acceptance, and other families under their own owners.
- No empty package is created while P11 is unmet.

## Alternatives rejected

**Include SalesOrder in `dotmac-sales`.** Rejected because ADR-0030 assigns a
complete orders owner and a quote-to-order conversion is a cross-owner
consequence, not a reason to merge aggregates.

**Port CRM because it has the agent UI.** Rejected because CRM lacks Sub's
transaction, immutability, origin and reconciliation guarantees and is the
writer being retired.

**Keep Sub's full acceptance transaction inside the module.** Rejected because
it imports product account/project/order decisions into a shared owner and
prevents independent composition.

**Create the package now and leave it unused.** Rejected by ADR-0017, the
product-first extraction rule and the zero-consumer rule.

## Amendment — 2026-08-18: P11 is met

The P11 status in decision 6 was the correct gate state at this ADR's pinned
Starter revision. It is now superseded by the accepted checked-in record in
[`p11-adoption-status.md`](../inventories/p11-adoption-status.md), merged on
Starter `main` as `ae508e1173b8643a4031936cc32cc411a6395f26` after the
Approvals release-record prerequisite merged as
`f10b19ae863b3867cbbb630eeaf4a33393efe7a8`.

Package, lineage and red-first canary implementation may therefore begin from
the completed product-first dossier. This amendment changes no ownership
boundary and advances no sales-specific release, Sub tenant/RLS, adoption,
cutover, CRM retirement, deployment or production-data gate.
