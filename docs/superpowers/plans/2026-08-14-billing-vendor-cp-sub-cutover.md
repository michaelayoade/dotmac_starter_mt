# `dotmac-billing` adoption: Vendor Control Plane, then Sub

**Status:** execution plan; not evidence that the module exists or either
product has adopted it
**Decision:** ADR-0020 and its 2026-08-14 amendment
**Evidence:** `docs/inventories/billing-sources.md`
**Order selected by Michael:** Vendor Control Plane cutover 1, Sub cutover 2
**Scope:** invoices and credit notes, operational receivables, confirmed
settlements, allocations and reversals, and immutable applied tax/FX snapshots

## Recommendation

Build and cut over one complete operational-receivables boundary. Do not move
invoices, settlements, or allocations as independent production authorities:
an allocation joins the other two, and splitting their writers creates a
financial boundary no reconciler can make authoritative.

Implementation may land in small capability slices, and Sub may shadow each
calculation independently. The production authority switch is nevertheless one
deployment-wide switch from Sub's legacy billing writer to `dotmac-billing`.
Vendor CP proves the module's platform plane and greenfield path first; Sub then
proves product-first parity, historical provenance, and retirement of a mature
tenant-plane owner.

## Exit condition

The programme is complete only when:

1. Vendor CP and Sub pin the same exact released `dotmac-billing` version and
   independently compose its one allocated lineage.
2. Vendor CP uses only the platform plane and Sub uses only the tenant plane;
   neither uses a nullable or sentinel tenant.
3. Each deployment selects exactly one invoice/receivables authority and the
   losing writer is mechanically unable to write.
4. Invoice lifecycle and payment coverage are separate; `paid` and
   `partially_paid` are not invoice lifecycle states.
5. Confirmed settlement is the only input that creates money. Pending checkout,
   uploaded proof, provider acknowledgement without verification, and a UI
   action create no receivable or funding effect.
6. Every allocation, deallocation, reversal, refund, credit, tax effect, and FX
   use is represented by immutable evidence. Corrections append reversing
   evidence; they do not edit settled facts.
7. Collectible receivable, available customer credit, and prepaid funding are
   derived separately per currency, with exact arithmetic and no fuzzy money
   tolerance.
8. ERP or the selected finance authority consumes immutable accounting facts
   idempotently. Billing contains no chart of accounts, journal, fiscal period,
   statutory return, treasury, or shadow-GL owner.
9. Sub's legacy invoice, payment, allocation, balance, and tax/FX decision paths
   are deleted or reduced to read-only archive/delegating adapters under a
   two-directional retirement ratchet.
10. The `EXTRACTION.toml` dossier records both real cutovers, the measured shadow
    evidence, and the local-copy retirement proof before it claims `reuse-proven`.

Installing a lineage, copying rows, or rendering the same totals is not the exit
condition. The authority and its old writer must move.

## Authority map

| Decision or fact | Canonical owner | Explicitly not the owner |
|---|---|---|
| Rated-obligation acceptance and idempotent identity | `dotmac-billing` | Subscriptions, Vendor CP routes |
| Invoice/credit-note lifecycle and document facts | `dotmac-billing` under the selected internal authority | ERP, rendering, object storage |
| Confirmed settlement, refund, and reversal facts | `dotmac-billing` | PSP connector, uploaded proof, UI |
| Allocation/deallocation and operational positions | `dotmac-billing` | Sub/Vendor CP balance fields, ERP |
| Applied tax calculation and immutable policy snapshot | `dotmac-billing` through a typed product-supplied tax seam | A Nigerian default in the shared module; ERP tax returns |
| Applied FX observation snapshot | `dotmac-billing` through a typed product-supplied FX seam | A hardcoded currency/rate source; a merged cross-currency balance |
| Provider client, credentials, webhook verification, retry, checkpoint | Integrator connector plugin (ADR-0024) | `dotmac-billing`, Sub, Vendor CP |
| Number series | P4 numbering owner, bound by the consuming assembly | Invoice service-local counter |
| Document bytes | rendering owner, then `dotmac-files` | Billing and Template Studio |
| GL mapping, posting, periods, tax recognition/returns, reconciliation | ERP or selected finance authority | `dotmac-billing` |
| Service/access consequence after financial state changes | Sub or Vendor CP owning service | Billing and collections |

Vendor CP keeps its commercial accounts, contracts, approval,
entitlement-allocation/licensing, and consequence execution. Sub keeps
subscriber, service, subscription, provisioning, and access decisions. Product-
owned link tables relate those subjects to billing identities on the correct
plane; no cross-application FK is introduced.

## Non-negotiable financial shape

### Documents and coverage

- A draft may change. Issuance freezes number, seller/buyer identity snapshots,
  line amounts, price/source version, tax decision, currency, and any FX
  observation used.
- An issued document is corrected by a credit/reversal document, never by
  rewriting its financial snapshot.
- Lifecycle is structural (`draft`, `issued`, `void`, and only other states
  proven by source behavior). Coverage is derived from exact allocations.
- Credit notes follow the accepted Dotmac rule: `subtotal = total`,
  `tax_total = 0`, and no tax lines. Changing that needs a new accounting
  decision, not a permissive field.
- A document number is unique per tenant on the tenant plane and across the
  control plane on the platform plane. P4 owns issuance and concurrency.

### Settlements and allocations

The only money-moving path is:

```text
payment intent or review
  -> independently confirmed settlement observation
  -> settlement accepted exactly once
  -> explicit allocation and/or available-credit effect
  -> derived per-currency positions
  -> owning-service consequence request
```

- A settlement carries stable source identity, source version/fingerprint,
  occurred-at time, exact amount/currency, confirmation evidence name, and
  provenance. Reuse of one source key with a different fingerprint is a
  conflict.
- A settled amount is immutable. Refunds, chargebacks, reversals, deallocations,
  and reallocations append typed effects linked to the fact they offset.
- An allocation preview identifies exact settlement/invoice edges and amounts
  and has a fingerprint. Apply locks the billing account, verifies the preview,
  and commits effects plus the idempotency row in the same transaction.
- Cross-currency allocation is refused in the first release. FX snapshots
  support immutable valuation and accounting facts; they do not permit one
  currency's funding to erase another currency's receivable. A later conversion
  capability needs its own explicit owner and posting contract.
- No mutable `balance` is authoritative. Positions are rebuildable projections
  of immutable posting groups, separated into collectible receivable, available
  credit, and prepaid funding.

### Tax and FX snapshots

The shared module owns applied snapshots, not jurisdiction policy or market-data
transport. The typed seams return enough immutable information to replay a
document without consulting current policy:

- tax policy identity/version, jurisdiction/treatment, inclusive/exclusive or
  exempt/reverse-charge decision, rate components, taxable basis, exact tax
  amount, rounding policy, decision time, and source provenance;
- source and target currency, exact rate, rate type/purpose, observation
  identity/version, observed-at/effective-at times, rounding policy, and source
  provenance.

Tax identity displayed on an issued document is snapshotted too. Imported tax
identity never silently overwrites an application's local verified identity.

Invoice tax and statutory tax recognition are different decisions. Billing
emits exact invoice and cash-allocation facts; ERP applies the deployment's
statutory policy. For the current Nigerian cash-basis policy, ERP recognizes
VAT from confirmed allocated cash rather than treating invoice issuance as the
tax return basis. That rule stays out of the shared module.

## Entry gates

No implementation begins merely because this plan exists.

1. **Authority:** ADR-0020 permits the boundary but ADR-0017 still controls the
   start. P11 must be evidenced, or Michael must explicitly authorize a named
   owner-directed exception for `dotmac-billing`.
2. **Product-first dossier:** create `packages/dotmac-billing/EXTRACTION.toml`
   in the same change as the package root, before behavior. It names Sub as the
   principal source, ERP as the coverage/tax/FX structure source, Vendor CP as
   cutover 1, Sub as cutover 2, preserved tests, known divergences, shadow
   proof, and local-copy retirement.
3. **Allocation with implementation:** allocate the short code, schema, prefix,
   and branch label only in the package-creation change. Do not reserve a
   namespace in this plan.
4. **Profile prerequisites:** internal issuance needs P4 numbering. A locally
   produced legal PDF also needs P8a rendering plus `dotmac-files`; do not hide
   either behind billing. Provider payment ingress needs an Integrator payment
   connector, but a finance-reviewed manual confirmation can exercise the
   provider-neutral settlement contract first.
5. **Live database:** fresh and upgraded Postgres migrations, both declared
   planes, RLS/GRANT/REVOKE catalog canaries, prerequisite checks, and the
   composed migration gate must be green before a module release is published.
6. **Clean consumer install:** the wheel contains its migrations, imports
   against its exact kernel floor, and has no relative-path consumer.

## Starter implementation slices

These are review slices, not independently switchable production authorities.
The minimum releasable module contains B1-B5 as one coherent financial path.

### B0 — dossier and source disposition

- Inventory every Sub model, service, route, job, webhook adapter, and test that
  creates or mutates an invoice, settlement, allocation, credit, refund,
  receivable position, tax snapshot, or FX snapshot.
- Inventory ERP coverage, tax, FX, and accounting-consumer sources. Record what
  is ported, adapted, or deliberately left ERP-owned.
- Record the six known extraction corrections from the billing inventory; do
  not copy them as compatibility behavior.
- List every source test to port and every behavior with no source test. Missing
  financial proof is a test task, not implied coverage.

### B1 — public contracts, planes, and persistence

- Define plane-neutral commands, results, errors, events, and repositories.
- Ship one behavior engine with explicit tenant and platform repositories/link
  helpers; no `platform=` boolean and no ambiguous model class.
- Create both declared persistence planes in one module lineage. Tenant tables
  carry `tenant_id NOT NULL`, forced RLS, and composite identities; platform
  tables carry no tenant column or RLS, revoke all seven table privileges and
  column privileges from the tenant app role, and are reachable by the online
  platform role.
- Prove no FK crosses planes and no module imports a product, assembly, sibling
  module, web framework, provider client, or finance application.

### B2 — obligation, invoice, and credit lifecycle

- Accept an immutable rated obligation under the C10 database identity.
- Draft, issue, void, and credit through typed services that mutate and flush;
  `dotmac_kernel.db` remains transaction authority.
- Bind the P4 numbering contract at issuance.
- Separate lifecycle from ADR-0016 coverage from revision 1.
- Emit immutable document facts for the rendering assembly path; billing stores
  neither template decisions nor bytes.

### B3 — settlements, allocations, and positions

- Accept only confirmed, provider-neutral settlement observations and enforce
  source-key/fingerprint conflicts through kernel idempotency.
- Implement preview/apply allocation, available-credit remainder, deallocation,
  reallocation, refund, and reversal as immutable posting groups.
- Derive exact per-account/per-currency receivable, available-credit, and
  prepaid-funding positions. Add a replay that rebuilds and hash-compares every
  position from source effects.
- Port Sub's settlement/allocation/refund/reversal tests, including concurrent
  replay and the known mutable-payment and uncapped-credit regressions.

### B4 — tax/FX snapshots and finance facts

- Port ERP's tax/FX structure only through typed product seams; carry no
  jurisdiction or currency default.
- Freeze applied tax/FX and party-tax-identity snapshots on issue; enforce
  header/line arithmetic and credit-note rules.
- Emit versioned accounting facts after commit with stable source identities,
  typed operational effects, and enough allocation detail for cash-basis
  recognition. ERP maps them to accounts and periods; the module never does.
- Supply fakes and one parametrized contract suite for each seam.

### B5 — enforcement and sensitivity proofs

Add tests that fail against deliberate temporary violations for:

- sibling/product/provider/GL imports;
- lifecycle statuses named `paid` or `partially_paid`;
- float or undeclared rounding, provider/currency identifiers or defaults;
- direct balance assignment, settlement mutation, or allocation without a
  settlement;
- cross-currency allocation;
- missing scope or wrong-plane repository use;
- two active commercial authorities;
- manual/ERP mode writing local invoices or receivables;
- mutable issued snapshots;
- a declaration without a consumer or a consumed undeclared vocabulary; and
- direct session construction, commit/rollback in services, or adapters that
  query the database.

## Cutover 1 — Vendor Control Plane (platform plane)

Vendor CP is greenfield on invoicing, so it has no financial rows to migrate and
no invoice writer to retire. Its purpose is to prove that a real platform-only
assembly can operate the full contract before Sub entrusts a mature estate to
it.

### V0 — product contract before UI

1. Select the `internal` commercial authority and bind only the platform
   repository. A boot canary refuses the tenant repository, nullable tenant,
   sentinel tenant, or a second authority.
2. Define the Vendor-owned relationship from commercial account/contract/
   deployment to the module's billing identity through platform link tables.
   Billing does not absorb Vendor's contracts or allocation/licensing state.
3. Bind Vendor-owned tax/FX policy adapters, P4 numbering, and a finance-
   reviewed settlement-confirmation adapter or Integrator payment capability.
   No PSP name, credential, or webhook verifier enters Vendor or Billing.
4. Define the immutable accounting-fact contract accepted by ERP/the selected
   finance authority before a production invoice can be issued.

### V1 — preview and canary

- Preview one real contract's invoice without persisting or numbering it.
  Finance verifies seller/buyer identity, service period, line arithmetic,
  tax treatment, currency, FX purpose, due date, and accounting mapping input.
- Exercise same-currency full, partial, overpayment-to-credit, reversal, refund,
  duplicate replay, and conflicting-replay scenarios with production-shaped
  data and fakes.
- Prove the platform role can perform required row DML and the tenant app role
  has no table or column privilege. Prove every route has the required guard and
  every money mutation has an audit action.

### V2 — first production path

1. Issue the first real invoice through the module. The number, tax/FX snapshots,
   document facts, receivable effect, idempotency evidence, audit, and outbox
   record commit together.
2. Render/store only through the assembly's rendering -> `dotmac-files` path.
   If P8a is not ready, do not call an HTML preview a completed legal-document
   cutover; explicitly hold production issuance or obtain an accepted profile
   ruling.
3. Accept the first independently confirmed settlement, allocate it, and prove
   the invoice coverage and three per-currency positions rebuild exactly.
4. Deliver the accounting fact and reconcile ERP's idempotent receipt/posting
   reference before widening rollout.
5. Run replay and orphan/drift reconciliation after every step. No dashboard
   field or click changes money or restores access.

### V3 — Vendor acceptance evidence

- fresh and upgrade composed migrations;
- platform privilege catalog evidence and wrong-plane canaries;
- invoice/document/snapshot arithmetic and immutable-issued behavior;
- settlement/allocation/refund/reversal replay and conflict behavior;
- exact position rebuild hashes per account/currency;
- accounting-fact acceptance and idempotent replay;
- owning-service consequence request with a named reconciler; and
- one real production commercial flow, not seeded/demo rows.

Only then may the dossier move from `audit-complete` to `adopted`. Vendor CP
being greenfield means there is no shadow of an old invoice owner; preview,
replay, finance acceptance, and the external accounting consumer are its
substitutes.

## Cutover 2 — Sub (tenant plane)

Sub is the product-first source and the high-risk cutover. It already has real
money, imported history, customer-visible positions, and service consequences.
The module must prove both parity and intentional correction before it becomes
authority.

### S0 — classify authority and evidence before mapping

Produce a read-only, reproducible inventory by tenant/account/currency and by
source provenance:

- native internal invoices and credits;
- provider-owned/imported invoice observations;
- open versus closed documents;
- confirmed settlements, refunds, chargebacks, reversals, and payment proofs
  that never became settlements;
- allocation edges and unallocated funding;
- tax-complete versus total-only documents and FX-complete versus missing-
  provenance documents; and
- every writer and every behavior-reading path.

Known Splynx-era populations include total-only invoices and paid invoices
without canonical settlement/allocation evidence. Re-measure them from the
current source; remembered counts are not a migration baseline. Never infer
cash from `paid`, invent a VAT split from a gross total, or turn a service
extension into money.

Classify each row into exactly one disposition:

| Disposition | Treatment |
|---|---|
| authoritative active fact with complete provenance | eligible for target backfill and parity |
| provider-owned observation | source-labelled projection; no local rewrite |
| closed legacy evidence gap | read-only legacy archive/projection; excluded from collection and accounting re-emission |
| active/open financial fact with missing provenance | cutover blocker; quarantine and Finance decision |
| known incorrect native fact | separate idempotent repair, dry-run and Finance approval before cutover |

No default bucket is allowed. Existence counts and causal interpretations need
separate adversarial verification; exact totals do not prove the narrative used
to map them.

### S1 — pure behavior shadow

Run the module's behavior against captured immutable source inputs in an
isolated shadow database or schema unreachable from product routes. The legacy
Sub writer remains the sole authority. Do not dual-write financial commands.

Compare, per tenant/account/currency and at source-identity level:

| Surface | Exact comparison |
|---|---|
| Documents | identity, lifecycle, lines, subtotal/tax/total, due dates, source/price/tax/FX versions |
| Settlements | confirmed source identity/version, amount, currency, time, refund/reversal chain |
| Allocations | exact settlement -> invoice edges, amounts, reversals, unallocated remainder |
| Positions | collectible receivable, available credit, prepaid funding separately |
| Accounting facts | stable identity/version, typed effects, allocation-derived tax basis, replay hash |

There is no money tolerance. Every mismatch is classified as source defect,
known intentional correction, missing evidence, contract defect, or shadow
bug. Unclassified differences block cutover. Customer-debit, over-credit, tax,
and access-impacting changes require explicit Finance/product acceptance.

Shadow acceptance requires all active cadences and settlement paths to be
exercised, followed by three consecutive complete reconciliations with zero
unclassified drift. A calendar duration alone is not evidence.

### S2 — target backfill and rehearsals

1. Restore a recent production snapshot into an isolated rehearsal environment.
2. Compose the real tenant lineage; import eligible facts with stable source
   identity and provenance. Imported rows are not re-rated and do not emit new
   accounting facts.
3. Quarantine the classified legacy cohorts exactly as S0 decided. A closed
   archive remains queryable but cannot become spendable credit, collectible
   debt, settlement evidence, or a new ERP posting.
4. Replay immutable posting groups and compare target positions with both the
   source facts and independently recomputed controls.
5. Rehearse the complete switch and rollback boundary at production scale,
   measuring lock time, outbox backlog, migration time, and reconciler runtime.
6. Run every cross-tenant RLS and wrong-plane canary under the real online role.

### S3 — one production authority switch

Do not partition live authority by customer, new document date, or invoice
type. Settlements and credits cross those partitions.

1. Announce a bounded money-write maintenance window. Stop invoice, payment,
   credit, refund, allocation, recurring-billing, webhook-consumer, and repair
   workers that can mutate the legacy boundary.
2. Drain and record inbound transport to a durable Integrator/checkpoint
   watermark; receipt may continue, but billing consequence processing pauses.
3. Acquire the product's billing cutover lock, record the legacy high-water
   marks, and prove no unclassified writer remains.
4. Import/replay facts through the watermark. Run exact document, settlement,
   allocation, position, tax/FX, and accounting-fact reconciliation.
5. In one deployment change, disable the legacy writer and enable the module as
   the sole `internal` authority. Routes, jobs, and consumers delegate to the
   module; they do not branch inside feature code.
6. Resume inbound settlement processing from the recorded checkpoint exactly
   once, then recurring obligations and other writers. Reconcile after each
   family resumes.
7. Keep service/access consequences asynchronous and idempotent. A named
   reconciler repairs a failed consequence; billing never writes service or
   entitlement state directly.

Once the module has accepted the first post-switch financial fact, rollback is
roll-forward: stop inputs, repair/replay the module, and keep it authoritative.
Re-enabling the old writer would create two owners. A technical rollback to the
old release is allowed only before any module fact exists after the watermark,
and the cutover lock/check proves that premise.

### S4 — burn down the old owner

- Add a two-directional ratchet over legacy model/service imports, direct
  invoice/payment/allocation/balance assignment, provider callbacks that mutate
  money, and jobs that bypass the module. Include sensitivity proofs and lower
  the baseline in the same change as every removal.
- Move readers to the module contract. Historical legacy archives remain
  explicitly read-only and provenance-labelled; they are not a fallback writer.
- Delete local status/coverage arithmetic, allocation, refund/reversal,
  available-credit, and tax/FX decision paths. Keep only assembly adapters,
  product policy seams, subject links, and approved archive readers.
- Delete compatibility adapters when their ratchet reaches zero. Retaining old
  tables for statutory retention does not retain their authority.
- Update Sub's architecture/adoption ledger and the module dossier with source
  revisions, reconciliation hashes, accepted divergences, cutover watermark,
  and retired paths.

Sub acceptance evidence includes fresh/upgrade migrations, cross-tenant
isolation, a total disposition classifier, pure-shadow parity, independently
recomputed exact positions, replay/conflict tests, all accepted customer-impact
deltas, a successful production watermark switch, post-switch reconciliation,
and zero legacy financial writers.

## Release and PR sequence

Keep the work reviewable while preserving the complete release gate:

1. **Starter docs:** this plan plus any accepted amendment required to authorize
   implementation. No package or namespace.
2. **Starter package foundation:** dossier, allocation, manifest, both planes,
   migrations, live-catalog canaries, public contracts.
3. **Starter document slice:** obligations, invoice/credit lifecycle, coverage,
   numbering contract, document facts, source tests.
4. **Starter money slice:** settlements, allocations, reversals/refunds,
   positions, replays, source tests.
5. **Starter policy/fact slice:** tax/FX snapshots, accounting facts, fakes,
   contract suites, architecture sensitivity proofs.
6. **Starter release:** prescribed checks, integration database, clean-wheel
   probe, registry configuration, exact version publication only when requested.
7. **Vendor CP adoption:** exact pin, platform lineage, product seams/surface,
   first live flow, ERP accounting receipt, dossier -> `adopted`.
8. **Sub shadow preparation:** exact pin, tenant lineage in rehearsal, total
   classifier, shadow runner/reconciler, retirement ratchet.
9. **Sub authority cutover:** production watermark switch, reconciliation,
   legacy-writer deletion, dossier -> `reuse-proven`.

Every repository change runs its own prescribed formatter, linter, unit,
architecture, migration, and Postgres integration gates before a commit or
push. Cross-repository success is evidence only for the exact revisions tested.

## What this plan does not authorize

- It does not lift ADR-0017's moratorium or create `packages/dotmac-billing/`.
- It does not publish a package, commit, push, deploy, migrate, or touch a
  production database.
- It does not install billing in ERP, CRM, Academy, Workspace, or Integrator.
- It does not build subscriptions, collections, usage metering/rating,
  numbering, rendering, object storage, or a provider connector inside billing.
- It does not repair the known Sub financial cohorts. Repairs need separate,
  measured, dry-run-first Finance approval.
- It does not permit a permanent shadow, a compatibility writer, or a fallback
  GL.
