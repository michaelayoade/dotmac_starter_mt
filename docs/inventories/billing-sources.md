# Billing, dunning and subscription sources

**As of:** 2026-08-11
**starter:** `c8237bd` (`origin/main`)
**Sub:** `9f6f9f36b` (`origin/dev`)
**ERP:** `766d4c0e` (`origin/main`)
**vendor CP:** `eb667fab`
**Decision applied:** ADR-0020 (2026-08-12)

Every hash above is the commit the measurement was taken AT, and is a
baseline rather than a claim about current state — re-run the counts rather
than trusting them (`docs/inventories/README.md`). The vendor CP entry read
"working tree" until 2026-08-12, which named nothing anyone else could
check; `eb667fab` is the commit that reproduces the 22 tables and three
commercial tables this file reports.

This inventory answers one question, asked before any shared billing code is
written: **which product code already owns billing, dunning and subscription
behaviour, and what does the kernel have to stand on?**

It is characterization, not a mandate. Per `README.md`, recording that two
products implement the same-looking table does not authorise extracting it —
ADR-0006 § "The extraction rule" and its 2026-08-08 product-first amendment
govern that. Read together with ADR-0017: the constraint on this capability is
not design, it is adoption.

## 1. Where billing lives today

Counts are mechanical (`__tablename__` occurrences; `wc -l` over the named
service files). Percentages are of that repo's total mapped tables.

| | starter (kernel) | Sub | ERP | vendor CP |
|---|---|---|---|---|
| Total mapped tables | 19 (kernel lineage) | 576 | 408 | 22 |
| Money-domain tables | **0** | **66** (billing, collections, subledger, subscription) | **32** (AR + AP) | **3** (`offer_versions`, `contracts`, `contract_lines`) |
| Money-domain service LOC | 0 | **~74,000** | not separately counted; AR/AP/tax/GL are the bulk of `app/services/finance/` | small |
| Money-domain test files | 0 | **174** | `test_coverage_parity.py` + AR/AP suites | contract tests |
| Invoicing | none | full (draft→issue→PDF→bulk→closure) | full (AR invoice, lines, line tax) | none |
| Payments / allocation | none | full (settlement, allocation, refund, reversal, arrangement, proof) | full (customer payment, allocation, advance allocation) | none |
| Dunning / collections | none | `dunning_cases`, `dunning_action_logs`, `collections_cases`, prepaid + postpaid policies, grace, sweeps | `reminder_service.py` (833 LOC) — AR **reminders**, not a dunning state machine | none |
| Subscription lifecycle | none | `subscription_engine`, `subscription_change`, `subscription_lifecycle_schedule`, `subscription_billing_treatment` | `ar/contract.py` only | `contracts` + `offer_versions` (commercial allocation, not recurring service) |
| Rating / cadence | none | `billing/rating.py`, `billing/cadence.py`, `billing/obligations.py` | none | none |
| Tax | none | `billing/tax.py`, `customer_tax_policies.py` | **10 tax models** (jurisdiction, code, fiscal position, period, return, transaction, deferred tax) | none |
| FX | `ExchangeRate` value object, **no table** | per-currency positions | `core_fx/`: currency, exchange rate, rate type, CTA | none |
| PSP integration | none | Paystack/Flutterwave via `integrations/connectors/payment_gateway.py`, `payment_gateway_adapter.py`, routing, autopay | Remita | none |
| Inbound webhooks | **none** | `payment_provider_events.py` (1,335), `payment_webhook_commands.py` (654), `api_billing_webhooks.py` (244) | — | none |
| Durable timers | **none** | `models/durable_timer.py` + `events/dispatcher.py` + `events/owner_outputs.py` | none | none |
| Document numbering | **none** | invoice/credit-note/receipt numbering in `billing/` | **five** implementations (ADR-0017) | none |
| Kernel modules imported | is the kernel | ~6, **none persistence** | **exactly one: `money`** | provisioning, money, messaging, licensing |

### Reading

1. **Sub is the qualifying source by an order of magnitude.** ~74k LOC and 174
   test files against ERP's AR/AP and the vendor CP's three commercial tables.
   Any plan sized against the starter (zero) or the vendor CP (three tables)
   will be wrong by roughly the same 20–25× the UI inventory found.
2. **ERP is the qualifying source for exactly two things**, and they are the two
   Sub has wrong: **coverage** (ADR-0016 stage 2 landed in #277 —
   `app/services/finance/coverage.py`, 198 LOC, one owner, Python/SQL parity
   test on PostgreSQL) and **tax/FX structure** (10 tax models, 4 FX models
   against Sub's single `tax.py`).
3. **The kernel owns none of it, and that is the correct current state.**
   What it owns is the substrate — money values, idempotency, outbox, entitlements,
   settings, consent, delivery — which is what a billing module should consume
   rather than restate.
4. **`money` is the one kernel contract ERP actually imports.** The single
   proven cross-product kernel adoption today is a billing primitive. That is
   evidence for the value objects and evidence about how narrow adoption is.

## 2. What the kernel already supplies

Present, released, and usable by a billing module without new kernel work:

| Facility | Module | What billing gets |
|---|---|---|
| Exact money + FX values | `money.py` (264 LOC) | `Money`, `Currency`, `ExchangeRate`, and `allocate()` — the exact split that never loses or invents a minor unit. No float, ever. |
| At-most-once execution | `idempotency.py` (340) + ADR-0014 | One owner for "this effect runs once", with a fingerprint column. Webhook and retry safety build on this, not beside it. |
| Transactional outbox + relay | `messaging/` (~1,050 across 9 files) | Emit a domain event atomically with the state change; lease/claim/retry relay worker; platform variant. |
| Idempotent inbox | `messaging/inbox.py` | `process_once(command_id)` — the consumer half of ADR-0007's guaranteed-handoff protocol. |
| Entitlement grants + evaluator | `entitlements.py` (146) | Where a commercial outcome is *projected to*. Explainable, local, never calls a provider at request time. |
| Settings resolution | `settings_resolver.py`, ADR-0011/0012 | Tolerances, cadence defaults, dunning ladders and gateway config as declared specs with scope and inheritance. |
| Consent + suppression | `consent.py` (445), `consent_models.py` | Dunning notices are contactable-checked, not blasted. Marketing/transactional scope rule already decided. |
| Channel policy | `channel_policy.py` (190) | Which channel a notice may use, as a settings document with a typed reader. |
| Delivery + receipts | `delivery.py` (341), `delivery_models.py`, migration `0020` | Outbound send with idempotent provider-receipt recording. |
| Module namespaces + lineage | `namespaces.py`, ADR-0006 D1 | `mod_<short>` schema allocation and a migration lineage a billing module can own without colliding with 576 Sub tables. |
| Declaration registries | `modules.py`, ADR-0008 | Capabilities, permissions, audit actions, setting domains — the mechanism that keeps a charge-model or dunning-action vocabulary open. |
| Coverage rule | ADR-0016 (fleet-wide) | The decision is made and ERP holds the reference implementation. |

## 3. Gaps — what the commercial-module stack still needs

Ten numbered capability areas, in dependency order, plus ADR-0017's external
lineage gate. P8 deliberately contains two owners: rendering produces a
document; object storage transports bytes. Four areas contain facilities on
ADR-0017's gap list, as clarified by its 2026-08-12 amendment.

| # | Gap and owner | Status | Evidence |
|---|---|---|---|
| P1 | **Money persistence primitives** — a column/composite type, the ADR-0016 `total_amount`/`amount_paid`/generated-`balance_due` mixin, declared rounding, configurable currencies, and immutable FX snapshots | missing; only the coverage slice has a named adopter now | `money.py` is values only. ADR-0016 § 5 requires the mixin and ERP can retire its local `coverage.py` when it adopts that owner. Configurable-currency and persisted-FX work wait for their own adopter and retirement path rather than riding that cutover. |
| P2 | **Inbound webhook receiver** — shared facility for signature verification, raw-payload storage, dedupe, replay, and ordering policy | missing; needed only for provider event ingestion | `security.py`'s HMAC is token signing; `delivery.py` is outbound receipts. Sub carries 2,233 LOC. ADR-0017's 2026-08-12 amendment confirms that its `webhooks` gap-list family includes inbound receivers. |
| P3 | **Durable timers** — shared facility to wake an owner/entity at a time, exactly once, with a generation | missing; needed for automated subscription and collections workflows | The outbox relay polls pending rows; it does not schedule. Grace expiry, dunning offsets, retry ladders, and arrangement due dates need timers. Sub has `durable_timer.py`. **Gap-listed as scheduling.** |
| P4 | **Document numbering** — shared facility with one owner per series, declared gapless-or-not policy, period reset, and concurrency safety | missing; needed for internal document issuance, not provider/manual invoicing | ERP has five implementations today. **Gap-listed.** |
| P5 | **Cadence/calendar arithmetic**, owned by `dotmac-subscriptions` | missing; needed for recurring contracts, not one-off billing | Sub's ADR-0007 designs `BillingCadence` but records the concrete failure the shared value object must correct: postpaid supports five cycles while prepaid remains materially monthly-specific. This is domain code, not a kernel prerequisite. |
| P6 | **Payment-provider seam**, owned by `dotmac-billing` — protocol, typed results, stable errors, fake, and parametrized contract suite | missing; needed only when the selected authority is provider-owned or takes provider payments | The current shared `providers/` surface contains only provisioning. A contract with no billing adopter must not land in the kernel merely because it is DB-free. |
| P7 | **Tax seam**, owned by `dotmac-billing` — rate resolution, inclusive/exclusive/exempt, reverse charge, and immutable applied-policy snapshots | missing; required only for an invoicing profile whose jurisdiction policy uses it | ERP has the structure (10 models); Sub has ISP-shaped behavior. Neither the kernel nor shared billing may acquire Nigerian VAT as a default. |
| P8a | **Document rendering**, a document-generation owner separate from billing and Template Studio | missing; needed when the selected invoice authority renders documents locally | Sub has `billing_invoice_pdf.py`. Billing supplies immutable invoice facts; rendering produces bytes. Template Studio's dossier already excludes document generation from template ownership. |
| P8b | **Object storage**, a byte-storage provider seam | missing; needed only for durable locally produced documents/attachments | The kernel has no storage abstraction. **Gap-listed as object storage.** Storage does not decide invoice content or render a PDF. |
| P9 | **Locale, message IDs, and formatting** (deployment plan WS4) | partial; needed for a second locale and for localized documents/notices | `display.py` resolves tenant timezone and date/datetime formats. There are no locale catalogs, stable message IDs, or currency-display rules. |
| P10 | **Operational receivables subledger**, owned by `dotmac-billing` — immutable posting groups with typed receivable/funding effects | missing; ownership resolved by ADR-0020 | Sub's ADR-0007 supplies the reference design. ERP retains the general ledger, chart of accounts, journals, fiscal periods, statutory posting, and accounting reconciliation; billing emits immutable accounting facts and never builds a shadow GL. |

**P11 is not an eleventh missing facility.** It is ADR-0017's external gate: the
kernel migration lineage must run in a product database in production before a
new stateful module lineage starts, absent a demand-pulled exception.

## 4. Non-conformances the extraction must not carry forward

Recorded so the port starts from the corrected shape, per the product-first
procedure's step 3.

1. **Sub's `InvoiceStatus` conflates coverage with lifecycle** —
   `app/models/billing.py:28` declares `partially_paid` and `paid` alongside
   `draft`/`issued`/`void`/`overdue`. This is precisely the ADR-0016 defect,
   and ERP has already fixed its copy. A shared module must ship ADR-0016's
   shape from revision 1: `balance_due` generated, coverage derived,
   tolerance a setting.
2. **Coverage arithmetic already produced divergence at scale in ERP** — twelve
   sites, seven rules (ADR-0016's table). Port `coverage.py` and its parity
   test, not a re-derivation.
3. **Prepaid and postpaid have separate scans, timers, notices and error
   handling in Sub** (ADR-0007 § Context) even though both end at the same
   access owner. That is the concrete cost of collection timing being two code
   paths instead of one contract field.
4. **Sub's admin surface adds credit to debt** —
   `web_subscriber_details.py:385` computes `current_balance = balance_due +
   available_credit` (Knowledge:
   `dotmac-sub-current-balance-credit-vs-debt-and-duplicate-billing`).
   Receivable and funding are different quantities; a shared read model must
   keep them separate per currency, as `BILLING_ACCOUNT_360.md` already does.
5. **Duplicate-billing dedupe keyed on a single `subscription_id`** in Sub's
   recurring run, so a standalone subscription and an add-on for the same
   service never collide (same Knowledge entry). The obligation identity in
   ADR-0007 (`contract line + version + charge component + source fact +
   period + currency`) is the fix and must be a database uniqueness
   constraint, not a query convention.
6. **`Payment.amount` editable on a settled payment**
   (`payments.py:1719-1731`) and an **uncapped pending ledger credit never
   reversed** (`billing/providers.py:310-337`) — both from the 2026-07-14 P0
   audit. Port the owners, not these paths.

## 5. Tests available to port

Sub's 174 money-domain test files are the behavioural proof base. The ones the
extraction procedure names as mandatory reading, by capability:

- **Coverage:** `dotmac_erp:tests/integration/test_coverage_parity.py`,
  `dotmac_erp:tests/.../test_paid_status_single_owner.py`
- **Rating and cadence:** Sub's `billing/rating.py`, `billing/cadence.py`,
  `billing/obligations.py` suites
- **Allocation and settlement:** Sub's payment allocation, refund and reversal
  suites — the highest-value proofs in the fleet, because they encode the
  reconciliation defects listed in § 4
- **Dunning:** Sub's `collections/` policy suites plus
  `docs/designs/DUNNING_STAFF_SAFE_ACTIONS.md`'s fingerprint/scope contract
- **Webhooks:** Sub's payment-provider-event tests

## 6. Decisions made after this characterization

ADR-0020, accepted 2026-08-12, settles the questions this inventory originally
left open:

1. **The operational receivables subledger is shared in `dotmac-billing`.** It
   owns invoices/credits, allocations, reversals/refunds, and separately
   derived per-currency receivable and funding positions. ERP remains the sole
   general-ledger and statutory-accounting owner.
2. **There are three independently stateful distributions:**
   `dotmac-billing`, `dotmac-subscriptions`, and `dotmac-collections`.
   Subscriptions and collections depend on billing; billing imports neither.
   Metering/usage rating is a later fourth module.
3. **The vendor control plane is the first billing-module adopter after the
   lineage gate.** It is greenfield on invoicing and has a live need. This does
   not change ADR-0017's choice of Sub as the first adopter of kernel
   persistence.
4. **The gate stays in force.** P2, P3, P4, and P8b are gap-listed facility
   work. A module proposal is not a demand-pulled exception.
