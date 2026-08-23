# Sales parity matrix and implementation canaries

**Status:** Contract frozen; P11 met; red-first implementation authorized
**Source:** Sub `f64946fc451ba94a1d4c8f0a61b7831367d5b598`
**Parity/retirement:** CRM `57e112f0757edcee6b9ad625ee3e13ebff5c7d71`
**Requirements-only:** ERP `2749ec5396cbbd7a1132b394e85855a1d133a7cd`
**Owner decision:** [ADR-0033](../adr/0033-sales-authority-stops-at-an-accepted-quote.md)

This is the TDD input for `dotmac-sales`. Each `C-SALES-*` canary is written
red before its behavior. The accepted checked-in P11 record now permits the
package and lineage; it does not waive any canary or adoption gate below.

## Behavior parity

| Capability/invariant | Sub evidence to port | CRM/ERP parity evidence | Required module disposition |
| --- | --- | --- | --- |
| Pipeline lifecycle | `sales.service::Pipelines`, pipeline boundary tests | CRM Pipeline CRUD tests | Preserve create/update/deactivate/delete policy; tenant-scope every identity |
| Ordered opportunity Stages | `PipelineStages`, `pipeline_configuration` | CRM Stage CRUD/order tests | Preserve order and presentation normalization; enforce same-tenant Pipeline FK |
| Lead lifecycle | `Leads`, `lead_authoring`, lifecycle tests | CRM CRUD/search/Kanban/probability tests | Preserve states `new/contacted/qualified/proposal/negotiation/won/lost`, typed transitions and query behavior |
| Party-first identity | `lead_authoring`, `sales.lifecycle` | CRM contact/Lead callers expose legacy coupling | Replace Party/Subscriber FKs with a typed opaque sales-subject reference; product adapter owns identity |
| Acquisition origin | `LeadOriginCapture` ORM guards, fingerprint/replay tests | CRM campaign/Meta/ERPNext writers expose sources | Preserve append-only evidence and collision rules; provider/campaign ids are opaque source references, not imports |
| Lead-to-Quote cardinality | Sub permits multiple Quotes per Lead | CRM permits multiple Quotes | Preserve; Quote acceptance may win the Lead only through the owner transition |
| Quote authoring | `quote_authoring` typed command and tests | CRM Quote/line CRUD tests | Start from Sub; one atomic command, at least one line, exact Decimal arithmetic |
| Quote lifecycle | `sales.service`, `quote_delivery`, acceptance | CRM state/filter tests; ERP Draft/Sent/Viewed/Accepted/Rejected/Expired | V1 supports Sub states; ERP `viewed` is an additive candidate only if a real adopter requires it—do not silently expand v1 |
| Discounts | current-state constraints plus append-only `QuoteDiscountHistory` | CRM line discounts; ERP header/line discounts | Port Sub's idempotent revision/fingerprint behavior; keep legacy line discount only if parity proves an active consumer |
| Money | Sub Decimal subtotal/discount/tax/total tests | CRM arithmetic; ERP `NUMERIC(19,4)`, FX/terms | No float; choose documented precision before schema; handoff serializes exact decimals. FX/terms enter only through a versioned pricing snapshot |
| Expiry | locked Sub acceptance refusal | ERP accepts only Sent/Viewed and marks expired | Evaluate once under the Quote lock; exact replay of an already accepted Quote does not re-evaluate expiry |
| Accepted immutability | stable Sub guard + financial safety tests | CRM negative evidence: accepted rows remain editable | Strengthen to service **and database** refusal for header, line, discount, deactivate and delete |
| Exactly-once acceptance | Sub parent lock, unique structural outcome, replay tests | CRM check-then-act idempotence only | One command identity/fingerprint, one accepted snapshot, one owner output; concurrent replays converge, conflicting content fails |
| Acceptance consequence | Sub currently creates account/order/project/work in one transaction | CRM creates SalesOrder/Project after commit; ERP converts explicitly | Mandatory delta: publish `AcceptedQuoteHandoffV1`; construct none of those rows |
| Owner-output delivery | Sub outbox/owner-output tests | CRM best-effort push is negative evidence | Event staged with acceptance; durable retry after commit; consumer receipt and local consequence commit together |
| Reconciliation | Sub lifecycle reconciler | CRM retirement ledger | Report-only by default; repair only through owner commands; never infer acceptance or customer identity |
| Tenancy | absent in all sources | absence is negative evidence | Tenant id non-null, composite identities/FKs, ENABLE+FORCE RLS and exact grants in the creating migration |
| Transaction authority | Sub owner commands are the source behavior | CRM service commits are rejected | Module mutates/flushes only; `dotmac_kernel.db` owns commit/rollback; conflicts use savepoints |
| Transport/product independence | Sub has product imports to cut | CRM/ERP are coupled | No assembly/sibling/provider imports, HTTP errors, campaign enum or product branches |

## Version-one accepted-Quote handoff

The public handoff is immutable and versioned. Its semantic fields are:

```text
AcceptedQuoteHandoffV1
  schema_version = 1
  event_id
  tenant_id
  occurred_at
  quote_id
  quote_version
  accepted_snapshot_sha256
  lead_id
  sales_subject {kind, opaque_id, version?}
  currency
  currency_minor_units
  subtotal
  discount {kind?, value?, amount, revision}
  tax_total
  total
  lines[] {
    line_id, position, description, quantity, unit_price,
    discount_amount, tax_amount, amount,
    catalogue_ref?, price_version_ref,
    terms_ref, terms_snapshot {version_ref, values[]},
    specification_ref,
    taxes[] {tax_code, source_version, taxable_basis, rate?, amount}
  }
  fulfillment_eligibility_requirement_refs[]
  source_ref?
  correlation_id?
  causation_id?
```

Decimal fields use canonical decimal strings. The digest covers a
domain-separated canonical encoding containing the schema version and field
names. Unknown additive fields are ignored only under the compatibility policy
for version 1; a semantic change requires a new schema version.

There is deliberately no `sales_order_id`, account row, project/service data,
provider payload, campaign object or delivery address owned by another domain.
Sales freezes requirement membership as accepted commercial intent; it does not
decide whether those requirements have later been satisfied.

## Canary catalogue

### C-SALES-01 — tenant service isolation

Create equal-shaped Pipelines, Leads and Quotes for tenant A and B. Through
every public list/get/update/delete/accept command, a `TenantScope(A)` can
observe and mutate only A. Supplying B ids under A returns the same not-found or
typed cross-scope refusal used for an unknown id and changes no row/event.

The canary runs on Postgres, not SQLite, and includes a reused UUID in different
tenants where the schema allows it so accidental global lookup is observable.

### C-SALES-02 — RLS, FORCE and grants in the live catalog

For every table declared by the eventual manifest:

- `tenant_id UUID NOT NULL`;
- RLS is ENABLED and FORCEd;
- the tenant policy constrains `USING` and `WITH CHECK`;
- tenant composite unique/foreign keys include `tenant_id`;
- the tenant runtime role has only required DML;
- `PUBLIC` and the platform-only role do not acquire tenant-row access by an
  inherited table or column grant; and
- the same migration creates the table, policy and grants.

The test reads tables from the manifest instead of carrying a second list.

### C-SALES-03 — cross-tenant relation rejection

Raw SQL and service commands cannot attach a Stage to another tenant's
Pipeline, a Lead to another tenant's Pipeline/Stage, a Quote to another
tenant's Lead, or a line/discount revision to another tenant's Quote. Each
attempt fails and leaves the transaction's tenant context usable.

### C-SALES-04 — accepted snapshot immutability

After acceptance, all of the following fail with the stable
`sales.accepted_quote_immutable` domain code and no money/event drift:

- header update, lifecycle rewrite, deactivate and delete;
- line create, update, delete or reorder;
- discount apply/change/remove or history rewrite/delete; and
- raw SQL `UPDATE`/`DELETE` attempted through a role otherwise permitted to
  mutate Draft Quotes.

A revised offer creates a new Quote with a new id. The canary proves Draft/Sent
mutation still works so the guard is sensitive rather than a blanket revoke.

### C-SALES-05 — exactly-once acceptance under concurrency

Two transactions accept the same Quote with the same command id and
fingerprint. They converge on one accepted snapshot and one event id. Replay
returns that outcome without a new row/output. The same command id with a
different fingerprint is a conflict. Acceptance with two different command ids
still produces one transition/output, with the loser replaying the canonical
result rather than duplicating it.

The test uses real Postgres sessions and proves the parent Quote lock/unique
constraints, not a sequential mock.

### C-SALES-06 — acceptance rollback and owner-output delivery

- If output staging fails, Quote/Lead acceptance and the event all roll back.
- A committed acceptance with dispatcher failure remains accepted with one
  pending output.
- Retry delivers the identical bytes/event id.
- A downstream consumer receipts `(consumer, event_id)` in the same transaction
  as its local effect; a crash before commit leaves neither, and replay
  re-drives.
- Reuse of the event id with a changed handoff digest fails closed.

The downstream test double records an opaque result only. It never constructs a
SalesOrder inside `dotmac-sales` tests.

### C-SALES-07 — cross-tenant acceptance rejection

An actor/sales subject, Lead, Quote, line, Stage or typed product reference from
tenant B cannot participate in tenant A acceptance. The command creates no
accepted state, Lead-Won transition, output, audit event or idempotency receipt.

### C-SALES-08 — product-neutral boundary

Import-linter and AST guards prove the distribution imports no assembly,
feature, sibling module, CRM/Sub/ERP package, web framework, provider client,
campaign/Inbox/WhatsApp/consent package, `SalesOrder` model or
`dotmac_orders`. A sensitivity test injects each forbidden family and proves
the detector reports it.

### C-SALES-09 — backfill and shadow parity

Given the same Sub source pin/snapshot, repeated backfill is idempotent. A
report compares per-table counts plus full-column, typed, domain-separated
digests for Pipeline, Stage, Lead, origin, Quote, line and discount history.
Money is compared at declared precision; ordered lines are compared by stable
position/id. Report mode changes nothing. Repair calls only module owner
commands and emits repair evidence.

### C-SALES-10 — writer and route retirement

A two-directional baseline covers every Sub/CRM constructor, direct assignment,
bulk update, raw SQL mutation, job, webhook and API/web route that can write a
sales row. The test fails if the count rises **or falls** without a reviewed
baseline update and includes a temporary injected writer sensitivity proof.
After cutover the baseline is zero outside module service/migration code. CRM
routes cannot be marked retired until the checked-in ledger verifies data,
callers, parity, cutover, fallback removal, 30-day healthy two-source zero
traffic and source deletion.

### C-SALES-11 — no downstream ownership leakage

Acceptance fixture assertions inspect the module transaction and handoff and
prove it creates no account, SalesOrder/line, Project/Task, WorkOrder, invoice,
ServiceOrder or Subscription row. The handoff contains none of their ids.
Adding `dotmac-orders` to dependencies or a SalesOrder table to the manifest
fails the architecture test.

## Required validation sequence once P11 is met

1. Write C-SALES-01 through C-SALES-11 red against the empty implementation.
2. Port the smallest Sub behavior slice and its parity tests.
3. Run `make check` and `make test-unit`.
4. Run the Postgres catalog/isolation/concurrency suite with
   `make test-db-up && make test-integration && make test-db-down`.
5. Build/install the wheel in Sub's clean branch and run focused Sub parity.
6. Run backfill/shadow/reconciliation in a non-production rehearsal.
7. Production cutover and zero-traffic evidence require separate explicit
   authorization; neither is implied by green local/CI tests.
