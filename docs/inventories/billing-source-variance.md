# Billing source variance

**As of:** 2026-08-15

**Baseline documents revalidated** (all three last written by starter `96dd4cb`,
PR #163, 2026-08-14, and byte-identical to `origin/main` today):

- `docs/inventories/billing-sources.md` — dated 2026-08-11
- `docs/inventories/billing-extraction-dossier.md` — dated 2026-08-14
- `docs/inventories/billing-parity-tests.md` — dated 2026-08-14

**Heads resolved for this revalidation:**

| Repository | Document pin | Current `origin/main` | Drift |
| --- | --- | --- | --- |
| `dotmac_starter_mt` | `b55c9a5` (dossier), `c8237bd` (sources) | `b0956c8` | moved; worktree on `feat/kernel-a65-session-provenance` at `ed9dcc0` |
| `dotmac_sub` | `9f6f9f36b` (2026-08-11, cited as `origin/dev`) | `f336170b6f13` (2026-08-15) | **353 commits** |
| `dotmac_erp` | `766d4c0e` (2026-08-11) | `9d67c3990e01` (2026-08-15) | **58 commits** |
| `dotmac_vendor_control_plane` | `eb667fab` (sources), `8984801` (dossier) | `80d3f3478d92` (2026-08-15) | **12 / 11 commits** |
| `dotmac_crm` | not pinned by any billing document | `57e112f0757e` (2026-08-11) | n/a |

Local checkouts are stale — Sub by 112 commits, ERP by 37, CRM by 121, Vendor
CP by 1. **Every citation below is a revision-pinned read against `origin/main`
in each repository** (`git show origin/main:<path>`, `git grep <pattern>
origin/main -- <paths>`), never the working tree.

**Authorizing decision:** ADR-0030 §6 — *"Where a dossier is incomplete, the
exception permits completing the audit; it does not turn missing evidence into
permission to greenfield."* This document completes the audit for build-order
step 7. It creates no package, allocates no namespace, migration prefix or
kernel version, and changes no shared registration file. No test was executed;
CI is this fleet's only acceptance owner.

---

## 1. Headline verdict

**The ownership ruling holds. The product-first sourcing does not.**

ADR-0020's assignment of operational receivables to a shared `dotmac-billing`,
ADR-0023's dual-plane requirement, ADR-0024's peer-module rule and ADR-0030's
owner matrix all survive revalidation unchanged. Nothing found here argues for
a different owner.

What does not survive is the claim that this module is a **port**. The dossier
names Sub as *"the qualifying source for the financial core (product-first)"*
and ERP as *"the qualifying source for coverage"*. Measured by callers at
current heads, **both flagship capabilities are not the live path in their own
source repository**:

1. **Sub's ADR-0007 obligation stack is shadow-only, by declaration, and its
   consumers are its own shadow machinery.** Sub's SOT registry declares
   `billing.contracts`, `billing.obligations`, `billing.rating`,
   `billing.addon_contract_backfill` and `financial.customer_subledger` as
   `AuthorityMigrationState.SHADOWING`. Every row those owners write carries
   `BillingRecordAuthority.shadow`, whose docstring reads *"a shadow row is
   reviewable evidence only; nothing may read it as money"*
   (`app/models/billing_contract.py:46-54`). `obligations.py` has **two**
   importers under `app/`, and both are shadow infrastructure. The live
   invoicing path contains **zero** occurrences of the string `obligation`:
   `invoices.py` 0, `billing_automation.py` 0, `payments.py` 0,
   `advance_renewal_invoicing.py` 0. Obligation acceptance in Sub has never
   raised an invoice.

2. **ERP's `coverage.py` is dead for every behaviour it is credited with.**
   `coverage_of`, `coverage_case`, `PaymentCoverage` and `resolve_payment_dust`
   have **zero references anywhere under `app/`**. `resolve_payment_dust` — the
   settings-backed tolerance reader that is the entire "tolerance is a
   `SettingSpec`" story — has zero references in the repository at all, tests
   included. The only production import from the module is the constant
   `PAYMENT_DUST_DEFAULT`, taken twice, by two modules that then re-implement
   the classification themselves. This is precisely the
   `reconcile_next_value` failure the numbering revalidation found, in the file
   the parity ledger calls *"the single highest-value port in the programme"*.

Both findings are the *opposite* of the numbering case in one respect and
identical in the other. Opposite: here the code **moved** — Sub took 353
commits, ERP 58. Identical: the code that matters is **byte-identical to its
pin** (Sub 25 of 28 source paths unchanged, 50 of 54 tests unchanged; ERP 33 of
34 paths unchanged), so a diff-based revalidation would have reported "fine",
and the documents' *description* of that unchanged code was already wrong when
written.

**Three further variances change the build plan:**

3. **51 of the 54 preserved Sub tests run on SQLite**, not PostgreSQL. Sub's
   `tests/conftest.py:360` builds the unit-lane schema with
   `Base.metadata.create_all(engine)`; only `tests/integration/` requires
   PostgreSQL (`tests/integration/conftest.py:26`, `_require_postgres`). Three
   of the 54 are integration tests. Every PostgreSQL-only guarantee added in a
   migration — including the obligation lineage's `EXCLUDE USING gist`
   (`alembic/versions/430_billing_contract_obligation_identity.py:451`) and its
   `postgresql_where` partial index (`:293`) — is therefore absent from the
   schema the parity tests run against. The parity ledger states LOC and
   assertions for all 54 and never states the database.

4. **The contract freeze ADR-0030 §5 step 7 requires cannot be executed as
   written.** Of the six named contracts, two (`allocation`, `coverage`) have no
   published shape in any spec and arguably should not; and three of the
   remaining four carry a *recorded, unresolved, cross-team disagreement*
   already documented at
   `docs/superpowers/specs/2026-08-14-commercial-composition-and-conformance.md:551`
   — *"the key compositions cannot both ship."* See §6.

5. **Money is not exact, and the fleet convention is nowhere in the source.**
   Sub carries **six different money precisions** across three billing model
   files — `Numeric(12,2)`, `(12,3)`, `(14,2)`, `(14,4)`, `(18,4)`, `(38,28)` —
   none of them `NUMERIC(20,6)`. Sub's `Invoice` has **no `amount_paid`
   column**, so ADR-0016's coverage operands do not exist to port. There are
   **zero** `Computed(` / `GENERATED ALWAYS` declarations anywhere in Sub's
   models or migrations, and **125 `float()` casts on money-named values across
   33 service files** where the dossier records one.

**Bottom line for step 7.** `dotmac-billing` is a **greenfield-after-inventory
module with a large parity-scenario library**, not a product-first extraction.
The scenarios port. The owners largely do not, because in Sub they have no
authority and in ERP they have no callers. Sizing, sequencing and the
`source_mode` field of the dossier all need to change before the first commit.

---

## 2. Per-source revalidation

### 2.1 `dotmac_sub` — `origin/main` = `f336170b`, 353 commits past the pin

#### 2.1.1 The paths survive; the authority does not

All 28 dossier `source_paths` for Sub exist at head. Three changed content:

| Path | Lines | `git diff 9f6f9f36b..origin/main` |
| --- | --- | --- |
| `app/services/billing/obligations.py` | 728 | empty |
| `app/services/billing/rating.py` | 445 | empty |
| `app/services/billing/cadence.py` | 456 | empty |
| `app/services/billing/invoices.py` | 2,987 | **changed** |
| `app/services/billing/credit_notes.py` | 2,452 | empty |
| `app/services/billing/payments.py` | 6,731 | **changed** |
| `app/services/billing/customer_subledger.py` | 614 | empty |
| `app/services/billing/subledger_opening.py` | 642 | empty |
| `app/services/billing/ledger.py` | 342 | empty |
| `app/services/billing/account_credit.py` | 1,710 | **changed** |
| `app/services/billing/adjustments.py` | 1,233 | empty |
| `app/services/billing/tax.py` | 117 | empty |
| `app/services/billing/_common.py` | 798 | empty |
| `app/services/billing/reconcile_unposted.py` | 724 | empty |
| `app/services/billing/shadow_verification.py` | **3,644** | empty |
| `app/services/provider_payment_settlements.py` | 437 | empty |
| `app/services/customer_financial_position.py` | 368 | empty |
| `app/services/customer_financial_ledger.py` | 1,088 | empty |
| `app/services/invoice_collectibility.py` | 293 | empty |
| `app/services/invoice_discounts.py` | 530 | empty |
| `app/services/tax_accounting.py` | 1,189 | empty |
| `app/services/customer_tax_policies.py` | 264 | empty |
| `app/models/billing.py` | 2,683 | empty |
| `app/models/billing_contract.py` | 703 | empty |
| `app/models/customer_subledger.py` | 342 | empty |
| `app/models/customer_tax_policy.py` | 53 | empty |
| `docs/adr/0007-...md` | 1,124 | empty |
| `docs/designs/BILLING_ACCOUNT_360.md` | 58 | empty |

Note `shadow_verification.py` at **3,644 lines** — the single largest file in
the source list, and the only one the dossier lists without characterizing. It
is *"Durable billing shadow-pipeline and cutover-verification evidence"*
(its line 1), i.e. the machinery of Sub's own unfinished ADR-0007 migration. Its
presence in a `source_paths` list intended for extraction is a category error:
it is Sub's cutover harness, not billing behaviour.

#### 2.1.2 The authority map, read from the registry at head

`git grep -o "AuthorityMigrationState\.[A-Z_]*" origin/main --
app/services/sot_registry/domains/financial_access/` yields 30 `COMPLETE`, 13
`SHADOWING`, 6 `CUT_OVER`, 4 `CUTOVER_READY`, 2 `NATIVE`. The billing-relevant
split:

| Owner | Declared state | Registry coordinate |
| --- | --- | --- |
| `billing.contracts` | **SHADOWING** | `financial_access/billing.py:313`, state at `:538` |
| `billing.obligations` | **SHADOWING** | `billing.py:575`, state at `:743` |
| `billing.rating` | **SHADOWING** | `billing.py:1103`, state at `:1187` |
| `billing.addon_contract_backfill` | **SHADOWING** | `billing.py:158`, state at `:278` |
| `billing.shadow_verification` | **SHADOWING** | `billing.py:779`, state at `:1069` |
| `financial.customer_subledger` | **SHADOWING** | `financial_access/customer_subledger.py:23`, state at `:146` |
| `billing.opening_balance_history` | CUTOVER_READY | `billing.py:23`, state at `:122` |
| `financial.customer_subledger_opening_positions` | CUTOVER_READY | `customer_subledger.py:181`, state at `:391` |
| `financial.invoices` | live, no migration declared | `invoicing_tax.py:772` |
| `financial.credit_notes` | live, no migration declared | `invoicing_tax.py:801` |
| `financial.payments` | live, no migration declared | `invoicing_tax.py:159` |
| `financial.ledger` | live, no migration declared | `financial_core.py:22` |
| `financial.provider_payment_settlements` | live | `provider_payments.py:23` |
| `financial.tax_accounting` | COMPLETE | `invoicing_tax.py:1223`, state at `:1551` |
| `collections.{prepaid,postpaid}_policy`, `collections.lifecycle` | **SHADOWING** | `collections.py:22/96/200` |
| `runtime.durable_timers` | **SHADOWING** | `durable_timers.py:22`, state at `:130` |
| `integration.dotmac_erp_billing_adapter` | **SHADOWING** | `erp_billing.py:22`, state at `:135` |

The `financial.customer_subledger` migration block names its predecessor
exactly (`customer_subledger.py:157-160`): old owner is *"`financial.ledger`
per-entry rows plus the multi-source `customer.financial_position`
document-union formulas"*, new owner is `financial.customer_subledger`, and the
cutover gate is *"per-currency/lane shadow differences are zero for the approved
observation window, and finance signs the cohort evidence"* (`:164-169`). That
gate has not passed.

**So the operational receivable — ADR-0020's P10, the reason `dotmac-billing`
exists — is bifurcated in the source.** The shape the module wants (immutable
posting groups, per-currency lanes, no combined balance) has never held
authority. The shape that holds authority is
`app/services/customer_financial_position.py`, whose own header calls it *"the
shared query layer for customer-facing balances"* and which derives position by
summing the mutable `Invoice.balance_due` column through
`invoice_collectibility` predicates keyed on `InvoiceStatus` — the exact
lifecycle/coverage conflation ADR-0016 rejects.

#### 2.1.3 Caller counts, by credited capability

Counted at `origin/main` over `app/` (excluding the module's own file),
`tests/` and `scripts/`, across all four Python import styles.

| Credited capability | Module | app | tests | scripts | Live? |
| --- | --- | --- | --- | --- | --- |
| **Obligation acceptance** | `billing/obligations.py` | **2** | 3 | 1 | **No — closed shadow loop** |
| Rated-obligation derivation | `billing/rating.py` | **2** | 1 | 1 | **No — shadow** |
| Contract versioning | `billing/contracts.py` | 6 | 7 | 1 | **No — shadow** |
| Cadence arithmetic | `billing/cadence.py` | 7 | 8 | 1 | shadow + 1 live reader |
| **Operational receivable (posting groups)** | `billing/customer_subledger.py` | 9 | 3 | 1 | staged from live paths, **authority shadow** |
| Opening positions | `billing/subledger_opening.py` | **0** | 1 | 1 | **No production caller at all** |
| **Invoice issuance** | `billing/invoices.py` | 16 | 5 | 2 | yes |
| Credit notes | `billing/credit_notes.py` | 6 | 1 | 0 | yes |
| Settlement / allocation / refund / reversal | `billing/payments.py` | 11 | 9 | 1 | yes |
| Ledger postings (live receivable) | `billing/ledger.py` | 7 | 1 | 0 | yes |
| **Settlement acceptance** | `provider_payment_settlements.py` | 2 | 1 | 0 | yes, narrow — see below |
| Position read model (live) | `customer_financial_position.py` | 16 | 5 | 0 | yes |
| Financial ledger read model | `customer_financial_ledger.py` | 9 | 11 | 0 | yes |
| Collectibility predicates | `invoice_collectibility.py` | 10 | 2 | 0 | yes |
| Account credit | `billing/account_credit.py` | 7 | 5 | 0 | yes |
| Adjustments | `billing/adjustments.py` | 6 | 1 | 0 | yes |
| Tax rate config | `billing/tax.py` | 1 | 0 | 0 | barely |
| Tax accounting | `tax_accounting.py` | 4 | 3 | 0 | yes |
| Shadow harness | `billing/shadow_verification.py` | 2 | 4 | 1 | shadow only |

The two `obligations.py` importers are
`app/services/billing/shadow_verification.py` (itself a `SHADOWING` owner) and
`app/services/events/handlers/billing_lifecycle_projection.py`, whose header
states *"All records remain shadow while ADR 0007's cutover gates are open"*
(`:1-7`). The one script importer is
`scripts/billing/billing_target_shadow.py`, an operator CLI whose docstring
says *"Everything stays shadow except the deliberately separate
`activate-subledger-authority` command"* (`:23-27`). There is no fourth path.

**`stage_verified_invoice_payment`** — the source proof the parity ledger cites
for *"only an independently confirmed settlement creates money"*
(`billing-parity-tests.md` §3.1) — is defined at
`provider_payment_settlements.py:167` and called from exactly **two** production
sites, both in `app/services/payment_provider_events.py` (`:719`, `:823`). The
manual/cash and staff paths go through `Payments.stage_create` /
`Payments.create` (`payments.py:2955`, `:2973`), which accept a payment with
`settlement=None`. The invariant that partially rescues the claim is narrower
and worth porting on its own terms: `PaymentAllocations.available_amount`
returns zero while `payment.settlement is None` (`payments.py:937-938`), so an
unconfirmed payment exists but cannot allocate. That is *"unconfirmed money
cannot be applied"*, not *"only confirmed settlement creates money"*.

#### 2.1.4 What the tests actually cover, and on what database

All 54 files in the dossier's `preserved_tests` exist at head. 50 are
byte-identical to the pin; four changed
(`tests/test_opening_settlement_correction.py`,
`tests/services/billing/test_invoice_lifecycle_owner.py`,
`tests/test_invoice_draft_authoring.py`,
`tests/test_billing_alignment_audit.py` — now 1,600 lines, not 1,580). Every
other LOC figure in `billing-parity-tests.md` is exact.

**Database.** Sub has 1,738 test files; 44 are under `tests/integration/`.
`tests/conftest.py:47-48` forces `DATABASE_URL` to an unreachable port to stop
accidental Postgres use, monkey-patches `sqltypes.Uuid` and JSONB for SQLite
(`:141-190`), and builds the schema with `Base.metadata.create_all(engine)`
(`:360`) — the comment at `:313` calls this the *"Fast non-authoritative unit
lane"*. `tests/integration/conftest.py:1-5` is the only lane on real
PostgreSQL, and it *"rejects rather than silently skips"* SQLite.

Of the 54 preserved tests, **3** are under `tests/integration/`:
`test_credit_note_issue_concurrency.py` (186),
`test_tax_accounting_concurrency.py` (111),
`test_payment_provider_event_concurrency.py` (89). The other **51 run on
SQLite against a metadata-built schema.**

Two consequences the parity ledger does not state:

- The obligation natural-identity proof
  (`tests/test_billing_obligations.py:165`, `:221`) exercises a
  `UniqueConstraint` declared in `__table_args__`, which SQLite does create.
  That much ports. But the *sibling* PostgreSQL guarantees in the same lineage —
  `EXCLUDE USING gist` over effective version periods
  (`alembic/versions/430_...:446-451`, requiring `btree_gist`) and the
  `postgresql_where="status = 'effective' AND ends_at IS NULL"` partial index
  (`:293`) — exist only in the migration. The model's own docstring says so:
  *"PostgreSQL-only guarantees (temporal exclusion on effective versions) are
  added by the migration; `__table_args__` here stays portable so the SQLite
  test harness can create the same tables"* (`app/models/billing_contract.py:14-17`).
  **Those constraints have no test.**
- SQLite has no `NUMERIC` precision enforcement. A money assertion that passes
  in the unit lane says nothing about `NUMERIC(20,6)` rounding, and `pytest.ini`
  suppresses only two unrelated deprecation warnings, so nothing in the harness
  is watching for it.

#### 2.1.5 Money in Sub

| Fact | Evidence |
| --- | --- |
| Six money precisions across three billing model files | `app/models/billing.py`: 55×`Numeric(12,2)`, 4×`(14,2)`, 2×`(12,3)`, 1×`(6,4)`. `billing_contract.py`: 12×`(14,4)`, 2×`(38,28)`, 1×`(6,4)`. `customer_subledger.py`: 3×`(18,4)`, 1×`(14,4)` |
| None is the fleet convention `NUMERIC(20,6)` | as above |
| `Invoice` has **no `amount_paid`** column | `app/models/billing.py:497` `class Invoice` — its money columns are `subtotal:573`, `tax_total:591`, `total:592`, `balance_due:593`, and there is no paid-amount column |
| `balance_due` is a plain, independently writable column | `app/models/billing.py:593-595` — `Numeric(12,2), default=Decimal("0.00")`, no `Computed` |
| Zero generated columns anywhere | `git grep -E "Computed\(|GENERATED ALWAYS" origin/main -- app/models/ alembic/` → no matches |
| 125 `float()` casts on money-named values, 33 files | top offenders `billing/reporting.py` 21, `web_billing_overview.py` 14, `customer_portal_flow_changes.py` 11, `web_subscriber_details.py` 5 |
| Money crosses the PSP wire as a binary float | `app/services/integrations/connectors/payment_gateway.py:331` `"amount": float(Decimal(str(params["amount"])))`, and `:371` |
| No FX concept at all | `git grep -liE "fx_rate\|fx_snapshot\|exchange_rate\|forex" origin/main -- app/ tests/` → **0 files**. `billing-sources.md` §5.7's stronger restatement is correct and still true at head |

### 2.2 `dotmac_erp` — `origin/main` = `9d67c399`, 58 commits past the pin

#### 2.2.1 Paths hold; one test is newer than the document that cites it

All 34 dossier paths (19 source + 15 test) exist at head. **33 have an empty
diff against `766d4c0e`.** The single exception is
`tests/architecture/test_monetary_documents_carry_coverage.py` (174 lines) —
it **did not exist at the pin**; it arrived with commit `a577cbc3`
*"feat(finance): amount_paid and balance_due where they never existed"*. The
parity ledger cites it with a LOC figure, which is only correct because the
figure was taken after the pin it declares.

#### 2.2.2 `coverage.py` — the caller count that fails the claim

`__all__` at `app/services/finance/coverage.py:68-75`.

| Symbol | Production callers under `app/` | Test callers |
| --- | --- | --- |
| `PAYMENT_DUST_DEFAULT` | **2 files**, both rebinding it: `ar/payment_status.py:66,74`, `ap/payment_status.py:54,61` | 2 |
| `coverage_of` | **0** | 3 |
| `coverage_case` | **0** | 1 |
| `PaymentCoverage` | **0** | 3 |
| `resolve_payment_dust` | **0** | **0** |
| `PAYMENT_DUST_KEY` | **0** | 0 |

Verified independently of the sub-audit: `git grep -nE
"coverage_of|coverage_case|PaymentCoverage|resolve_payment_dust" origin/main --
app/ tests/ scripts/` returns matches only inside `coverage.py` itself, inside
`tests/`, and one **prose comment** at `app/services/settings_spec.py:491`
(`# Read through 'app.services.finance.coverage.resolve_payment_dust'.`).
`from app.services.finance import coverage` and `import
app.services.finance.coverage` return zero across the repository.

Two consequences:

- **The `payments.payment_dust` setting is not wired to anything.**
  `settings_spec.py:492-500` registers the spec; its only declared reader is
  `resolve_payment_dust`; that function has no callers. Both live deciders bind
  the default at import time (`PAYMENT_DUST = PAYMENT_DUST_DEFAULT`,
  `ar/payment_status.py:75`), so changing the row changes nothing at runtime.
  The parity ledger's §1 port note — *"the tolerance is the
  `payments.payment_dust` `SettingSpec`"* — describes a design, not a behaviour
  with a proof.
- **`coverage_case`, the SQL half whose parity with Python is "the single
  highest-value proof in the fleet", is used only by its own parity test.** The
  test is real and good; what it proves is that two functions agree with each
  other, neither of which production calls.

**What ERP actually has** is the AR/AP `payment_status` pair, and it is a
narrower capability honestly described: `apply_payment_status` has **8
production call sites across 7 files** (`ap/supplier_invoice.py:1562`,
`ap/supplier_payment.py:630,800`, `ar/advance_allocation.py:114`,
`ar/customer_payment.py:519,627,713`, `ar/exact_match_allocation.py:204`,
`ar/invoice.py:1496`, `app/tasks/data_health.py:252,716`). Its rule, quoted:

```
app/services/finance/ar/payment_status.py:106  if total_amount - amount_paid <= PAYMENT_DUST:
                                        :107      return InvoiceStatus.PAID
                                        :109  if amount_paid > PAYMENT_DUST:
                                        :110      return InvoiceStatus.PARTIALLY_PAID
```

`ap/payment_status.py:92,95` is the same arithmetic again. So the rule exists
in **three** live copies (`coverage.py`, `ar/`, `ap/`), of which the nominal
single owner is the unused one.

#### 2.2.3 Coverage consolidation is narrower than ADR-0016 claims

`tests/architecture/test_paid_status_single_owner.py:51-56` allowlists exactly
**two** owners (`InvoiceStatus` → `ar/payment_status.py`,
`SupplierInvoiceStatus` → `ap/payment_status.py`). Its docstring premise at
`:47-50` — that `CommitmentStatus` has no `PARTIALLY_PAID` member — is **false
at head**: `app/models/finance/ipsas/enums.py:58` declares
`PARTIALLY_PAID = "PARTIALLY_PAID"`. So the guard does not cover
`app/services/finance/ipsas/commitment_service.py:340-343`, an attribute
assignment of a coverage verdict with an exact `>=` and no tolerance.
`tests/architecture/test_coverage_is_not_a_lifecycle_status.py:56-101`
grandfathers **8** enums; its AR entry reads *"retires when AR reads move to
`coverage_of`"* — which has not happened and cannot be observed to be
progressing, since `coverage_of` has no callers.

Beyond the three canonical implementations, at least ten further sites decide
"settled" with their own threshold — `Decimal("0")` at
`payments/payment_service.py:125`, `ap/payment_batch.py:290`,
`gl/fx_revaluation.py:189,243`, `ar/exact_match_allocation.py:181-182`; a raw
`Decimal("0.01")` rather than the named constant at
`app/tasks/data_health.py:677,689,720,991` (in a file that imports
`PAYMENT_DUST` at `:35`); exact `>=` with no dust at
`import_export/invoices.py:712-716`; and `balance_due == Decimal("0")` at
`dotmac_sub/sync/_base.py:434-440`. Four models are stamped `PAID`
unconditionally despite now having coverage columns:
`people/payroll/payroll_service.py:1112`, `expense/service_claims.py:1490`,
`finance/lease/lease_variable_payment.py:362`.

#### 2.2.4 Money and the mixin claim

ERP's AR invoice is the shape the module should ship, and it is correct:
`app/models/finance/ar/invoice.py:151-176`, every money column `Numeric(20,6)`,
and `balance_due` genuinely generated —

```
app/models/finance/ar/invoice.py:168  balance_due: Mapped[Decimal] = mapped_column(
                               :169      Numeric(20, 6),
                               :170      Computed("total_amount - amount_paid", persisted=True),
                               :171      nullable=False,
                               :172  )
```

No `Float` column type and no `float(` call exists anywhere under
`app/models/finance/**`. Under `app/services/finance/**` there are 404 `float(`
occurrences, overwhelmingly presentation serialisation into `*_raw`/`*_display`
report keys; the non-presentation ones worth naming are
`banking/bank_upload.py:253` (money into an XLSX cell as a binary float),
`banking/reconciliation_parts/matching.py:1763`
(`bank_amt = abs(float(stmt_line.amount or 0))`, floated *before* matching
arithmetic), `gl/account_balance.py:731`, `rpt/management_accounts.py:164-168`,
`import_export/opening_balance.py:79-127,654-655`, and
`ar/exact_match_allocation.py:141` (the money tolerance itself floated).

**The dossier's five-coordinate mixin claim needs correcting in three places.**
No shared coverage mixin exists and none has been added — confirmed by symbol
sweep over `app/models/mixins.py` and `app/models/finance/base.py`. But:

| Dossier coordinate | Verdict at head |
| --- | --- |
| `ar/invoice.py:168` | correct |
| `ap/supplier_invoice.py:179` | correct |
| `expense/expense_claim.py:320` | **wrong line** — `amount_paid:291`, `balance_due:302`; and a different shape: `Numeric(12,2)`, nullable, over `Computed("net_payable_amount - amount_paid")` |
| `people/payroll/salary_slip.py:215` | correct line; different shape — `Numeric(18,2)`, `Computed("net_pay - amount_paid")` |
| `finance/lease/lease_payment_schedule.py:77` | correct line but **added after the pin** by `a577cbc3`; it did not exist at `766d4c0e` |

A **sixth** model declares `amount_paid` with no `balance_due`:
`app/models/procurement/procurement_contract.py:140`. It escapes
`test_monetary_documents_carry_coverage.py` because its total column is not in
that test's `TOTAL_COLUMNS` set. And `test_monetary_documents_carry_coverage.py`
does not test what ADR-0016 asked for: its own docstring at `:4-6` quotes the
ADR asking for *"every model carrying a monetary total also carries the coverage
mixin"*, while the implemented check is for two per-model column names
(`COVERAGE_COLUMNS = ("amount_paid", "balance_due")`). The guard enforces the
duplication rather than the mixin.

Five declarations, **three precisions** (`(20,6)`, `(18,2)`, `(12,2)`), **four
total-column names** (`total_amount`, `net_pay`, `net_payable_amount`,
`total_payment`), one nullable against four non-null. That divergence is the
strongest available argument *for* the shared mixin and *against* lifting any
one of these declarations as the canonical shape.

#### 2.2.5 FX and money-boundary are thinner than credited

`FXService` is imported by 5 production files, but of its 9 public methods only
**two** have any external caller: `lookup_spot_rate` (3 sites:
`app/api/finance/fx.py:41`, `dotmac_sub/sync/_base.py:159`,
`gl/fx_revaluation.py:582`) and `convert` (1 site:
`platform/approval_workflow.py:128`). The `fx_service` singleton
(`platform/fx.py:593`) and `ConversionResult` (`:28`) have zero references.
**`FXService.get_functional_currency` (`fx.py:429`) is dead** — the 35 apparent
hits belong to a same-named method on a different owner,
`platform/org_context.py:36`. That name collision must be resolved before either
is cited as a source.

`money_boundary.py` (490 lines) is genuinely good and genuinely narrow: 5
production consumers, all Sub/CRM sync ingress, and **8 of its 17 exports have
zero production callers** — including `require_same_currency`,
`rate_snapshot_from_observation` and `convert_with_snapshot`, the three most
relevant to a billing module.

One security change since the pin is worth carrying into the module's design:
commit `381eb7b1` *"fix(payments)!: fail closed — refuse to replay a stored
webhook payload (#295)"* made `retry_failed_webhook` unconditionally raise
`WebhookRetryDisabledError`, with the new docstring recording that the removed
path re-dispatched *"from `webhook.payload` — a row in our own database — with
**no signature verification anywhere in the path**"*. ERP has no verified-receipt
redrive; it has a disabled one.

### 2.3 `dotmac_vendor_control_plane` — `origin/main` = `80d3f347`, 11 commits past the dossier pin

**Cutover-1 viability holds.** The greenfield claim survives an exhaustive
re-grep: zero hits under `src/` or `tests/` for `invoice`, `payment`,
`receivable`, `settlement`, `credit_note`, `refund`, `reversal`, `dunning`,
`subledger`, `posting` or `arrears`. `ledger` hits are the provisioning
simulator's in-memory list; `billing` hits are the capability *code strings*
`"billing_export.erp_billing"` / `"billing.use"`; `allocation` is entitlement
allocation with `quantity: Integer` and no money column
(`src/vendor_cp/allocations/models.py:38,68`). The exclusion is documented at
`docs/design/domain-foundation.md:60` and `:597` (*"vendor invoicing"* listed
explicitly out of the first slice).

The 18-table count is exact. **The `billing-sources.md` "22" is now
reconciled**: 18 owned + 2 (`mod_rel`) + 2 (`mod_ealloc`) composed at the
dossier pin. At head the composed total is 25, because `mod_approvals` was
composed today. `billing-extraction-dossier.md` §5.4 correctly flagged this as
an unconfirmed hypothesis; it is now confirmed, and the recommended restatement
("18 owned + composed module tables") should be adopted.

No `tenant_id` column and no RLS exist: all 17 `tenant_id` matches across `src/`
and `alembic/` are docstrings saying *"no `tenant_id`, no RLS"*. Isolation is a
uniform three-statement grant block per table in v001–v010
(`GRANT SELECT, INSERT, UPDATE, DELETE ... TO platform_api;` /
`... TO app_admin;` / `REVOKE ALL ... FROM app_user;`). Roles are `app_user`,
`platform_api`, `app_admin` (`deploy/postgres/init-roles.sh:16-21`).

**The `offer_versions` defect ADR-0030 §5a cites is unchanged and worse than
recorded.** Verbatim at `alembic/versions/v002_offer_versions.py:58`:

```python
op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")
```

with the file's own docstring at `:5-6` conceding *"Rows are immutable —
`(offer_code, version)` is unique and never updated (enforced by the
service)"*. No later `REVOKE` touches it; no trigger exists anywhere
(`CREATE TRIGGER|CREATE OR REPLACE FUNCTION|CREATE RULE` → zero matches under
`alembic/` and `src/`). Three aggravating facts the ADR does not state: version
numbers are **caller-asserted** (`src/vendor_cp/offers/schemas.py:16`,
`service.py:50,116`), there is **no `previous_version_id`**
(`offers/models.py:42-52`), and there is **no content digest** on the offer
version — though `Contract.content_hash` exists at
`contracts/models.py:69`. `tests/unit/test_offers.py:109-117` proves only that
the *service* refuses a re-publish; a direct `UPDATE` by the online role is
untested and unblocked.

**The lineage is now v001–v012, not v001–v011.** Any statement scoped to "the
v001–v011 lineage" now under-covers the repo. The Claim-4 conclusion survives,
because v012 revokes only on `mod_approvals` tables.

Money is `String(40)` + `String(3)`, exactly as the dossier says, at five
columns: `offers/models.py:47,48`, `contracts/models.py:57,105,106`. Zero
`Numeric`/`Float`/`Decimal` in `src/`. The one `float` is an HTTP timeout
(`production_secrets.py:116`). Kernel `Money` is imported top-level at 3 sites;
`from dotmac_kernel.money import ...` has zero occurrences repo-wide.

**Two dossier corrections.** `tests/unit/test_contracts.py`'s
`TenantEntitlementGrant`-count-zero assertion has moved from `:194` to
`:282-285` (line 19 is the import; there is one such assertion, not two). And
the kernel import surface is wider than recorded: `db` 6→7, `migrations` 1→2,
top-level 28→31, plus three submodules the dossier omits entirely — `planes`,
`prerequisites`, `idempotency` — for **10 distinct kernel submodules, not 7**.

**One change that touches the priced surface.**
`alembic/versions/v011_product_identity.py` **drops
`uq_offer_versions_code_ver` and replaces it with
`uq_offer_versions_product_code_ver` on `(product_code, offer_code, version)`**
(`v011:44-49`), adds a partial unique for unmapped legacy rows, and refuses
`downgrade()` (`:76-80`). Product identity is now hashed into
`Contract.content_hash`. Any module that ports Vendor CP's version identity must
port the *product-qualified* one.

**Deployment status.** Confirmed: 0 tags, 0 releases, 0 deployments, 0
production-deploy workflow runs; the production-image workflow has run twice.
ADR-0030 §5 step 4's conditional skip is accurate at head. The nuance worth
recording is that the full deploy machinery — Dockerfile,
`docker-compose.production.yml`, nginx vhosts, `scripts/deploy_production.sh`,
`production_secrets.py` — is now checked in. This is a closing window, not a
stable fact, and the ADR's own trigger conditions should be re-checked
immediately before the Vendor cutover, not once.

### 2.4 `dotmac_crm` — `origin/main` = `57e112f0`

No billing document pins CRM, and none needs to. CRM declares no invoice,
payment, allocation, settlement or receivable writer: a `__tablename__` sweep
for money-domain names returns only `billing_rates`,
`subscriber_billing_risk_snapshots`, `vendor_purchase_invoices` and
`vendor_purchase_invoice_line_items` — a rate catalogue, a risk projection and
an AP-side purchase-invoice pair. ADR-0030 §7's disposition ("its parallel
sales-order and commercial writers retire") is an **orders** concern, not a
billing one. CRM is not a source, not an adopter, and not a blocker for step 7.

---

## 3. Defects

Numbered, at file:line against current heads. Status is relative to what
`billing-sources.md` §4, `billing-extraction-dossier.md` §4 and
`billing-parity-tests.md` record.

**D1 — `InvoiceStatus` conflates coverage with lifecycle. STILL PRESENT,
coordinates exact.** `dotmac_sub:app/models/billing.py:28` declares the enum;
`partially_paid` at `:31`, `paid` at `:32`, `written_off` at `:40`. Unchanged
across 353 commits.

**D2 — Sub's `Invoice` has no `amount_paid` column, so the ADR-0016 operands do
not exist. NEWLY FOUND.** `dotmac_sub:app/models/billing.py:497`
`class Invoice` carries `subtotal:573`, `tax_total:591`, `total:592`,
`balance_due:593` and no paid-amount column.
`git grep -c amount_paid origin/main -- app/` finds it only
on sales-order and vendor-purchase-invoice paths. ERP's
`test_monetary_documents_carry_coverage.py` would fail Sub's `Invoice` today.
The consequence for the port is concrete: `balance_due = total_amount -
amount_paid` cannot be *derived* from Sub's rows; the operand has to be
reconstructed by summing settlements during migration, which is a data project,
not a column rename.

**D3 — `balance_due` is a plain, independently writable column; there are zero
generated columns in Sub. NEWLY FOUND.**
`dotmac_sub:app/models/billing.py:593-595`. `git grep -E "Computed\(|GENERATED
ALWAYS" origin/main -- app/models/ alembic/` returns no matches. The dossier
treats the enum as the ADR-0016 defect; the persistence layer is the deeper
half.

**D4 — Six money precisions across three Sub billing model files, none of them
`NUMERIC(20,6)`. NEWLY FOUND.** `app/models/billing.py` `(12,2)`/`(12,3)`/
`(14,2)`/`(6,4)`; `billing_contract.py` `(14,4)`/`(38,28)`/`(6,4)`;
`customer_subledger.py` `(18,4)`/`(14,4)`. ADR-0020 §A7's ruling has no
representative in the product-first source.

**D5 — 125 `float()` casts on money across 33 Sub service files. WORSE THAN
RECORDED** (dossier records one, at `web_subscriber_details.py:502`). The
recorded site is confirmed at head:
`dotmac_sub:app/services/web_subscriber_details.py:385`
(`current_balance = balance_due + available_credit`) and `:500-502`
(`"balance_due": float(balance_due)`, `"credit_issued": float(available_credit)`,
`"current_balance": float(current_balance)`). Concentrations:
`billing/reporting.py` 21, `web_billing_overview.py` 14,
`customer_portal_flow_changes.py` 11.

**D6 — Money crosses the PSP wire as a binary float. NEWLY FOUND.**
`dotmac_sub:app/services/integrations/connectors/payment_gateway.py:331`
`"amount": float(Decimal(str(params["amount"])))`, and the same at `:371`. The
`Decimal(str(...))` round-trip makes it look careful; the outbound value is a
float. This retires to the Integrator under R8, so it is a *do-not-port* note
for the connector work, not for billing — but it must not travel with the
scenarios.

**D7 — The `current_balance` block swallows every exception and returns zeroed
money. NEWLY FOUND.** `dotmac_sub:app/services/web_subscriber_details.py:386-396`
— a bare `except Exception:` at `:386` that logs, calls `db.rollback()` at
`:391`, and then sets `balance_due`, `available_credit` and `current_balance`
each to `Decimal("0.00")` at `:394-396`. A database error therefore renders
"you owe nothing". A shared read model must fail closed, and this is the
concrete reason.

**D8 — `PaymentUpdate` still exposes money and status on a settled fact. STILL
PRESENT, coordinates exact.** `dotmac_sub:app/schemas/billing.py:715`
(`amount: Decimal | None = Field(default=None, gt=0, lt=10000000000)`), and the
same contract also exposes `status: PaymentStatus | None` at `:717` and
`paid_at` at `:718`. The service guard is confirmed real at
`dotmac_sub:app/services/billing/payments.py:3343-3348` (*"Settled payment
fields are immutable evidence"*, 409). An **unsettled** payment's amount, status
and paid-at remain freely editable.

**D9 — Invoice numbering has one definition, seven production call sites and no
test. STILL PRESENT, exact.** `dotmac_sub:app/services/billing/invoices.py:303`
defines `next_invoice_number`; production callers at `invoices.py:706,1519,2123`,
`billing_automation.py:1902,2277`, `crm_api.py:1462,1481`. `git grep -c
next_invoice_number origin/main -- tests/` → **0 files**. (`billing-parity-tests.md`
G1 says "5 in production code … and 2 in `crm_api.py`", which is seven; the
coordinates are right and the count sentence is confusing.)

**D10 — The PDF export path invalidates artifacts from live rows and from a
hand-edited constant. STILL PRESENT, coordinates exact.**
`dotmac_sub:app/services/billing_invoice_pdf.py:46` `STALE_EXPORT_SECONDS = 20`;
`:51` `INVOICE_PDF_TEMPLATE_REFRESHED_AT = datetime(2026, 3, 18, 9, 0, tzinfo=UTC)`;
`:945` `_is_export_fresh`, which at `:950` discards any export completed before
that constant; `:1246` `maybe_finalize_stalled_export`, which at `:1266`
processes inline once `age_seconds >= STALE_EXPORT_SECONDS`. This is the
second at-most-once mechanism ADR-0014 forbids, exhibiting the predicted stalled
`processing` row.

**D11 — Invoice payment instructions are resolved at render time, not
snapshotted. STILL PRESENT.**
`dotmac_sub:app/services/billing_invoice_pdf.py:266` and `:865` both call
`invoice_bank_details_service.get_invoice_bank_details(db, currency=invoice.currency)`
during rendering. Re-printing a two-year-old invoice prints today's bank
account. This is a live money-misdirection defect and it is the reason
`InvoiceDocumentFactV1`'s `payment_instructions` snapshot field is
load-bearing rather than cosmetic.

**D12 — ERP's `coverage.py` behavioural exports have zero production callers.
NEWLY FOUND, and it invalidates a headline claim.**
`dotmac_erp:app/services/finance/coverage.py:103` (`coverage_of`), `:134`
(`coverage_case`), `:78` (`PaymentCoverage`), `:174` (`resolve_payment_dust`) —
no reference under `app/` for any of them. The only production imports are
`PAYMENT_DUST_DEFAULT` at `ar/payment_status.py:66` and `ap/payment_status.py:54`.

**D13 — The `payments.payment_dust` setting has no live reader. NEWLY FOUND.**
`dotmac_erp:app/services/settings_spec.py:491-500` registers the spec and names
`resolve_payment_dust` as the reader in a comment; that function has zero
references repo-wide. Under the starter's own rule 10 (`test_no_orphan_settings`)
this shape would fail; ERP has no equivalent guard.

**D14 — `test_paid_status_single_owner.py`'s stated premise is false at head.
NEWLY FOUND.** The guard's docstring at
`dotmac_erp:tests/architecture/test_paid_status_single_owner.py:47-50` asserts
that `CommitmentStatus` has no `PARTIALLY_PAID` member. It does:
`dotmac_erp:app/models/finance/ipsas/enums.py:58`. The uncovered write is
`dotmac_erp:app/services/finance/ipsas/commitment_service.py:340-343`, an exact
`>=` with no tolerance assigning `CommitmentStatus.PARTIALLY_PAID` to an
attribute. Per ADR-0018 this is an exemption whose premise stopped being
enforceable; the region is unmonitored, not exempt.

**D15 — At least ten further ERP sites decide "settled" with their own
threshold. WORSE THAN RECORDED.** `Decimal("0")`:
`payments/payment_service.py:125`, `ap/payment_batch.py:290`,
`gl/fx_revaluation.py:189,243`, `ar/exact_match_allocation.py:181-182`. Raw
`Decimal("0.01")` instead of the named constant, in a file that imports it:
`app/tasks/data_health.py:677,689,720,991`. Exact `>=`:
`import_export/invoices.py:712-716`. Balance-equals-zero:
`dotmac_sub/sync/_base.py:434-440`. Four unconditional `PAID` stamps:
`people/payroll/payroll_service.py:1112`, `expense/service_claims.py:1490`,
`finance/lease/lease_variable_payment.py:362`.

**D16 — `FXService.get_functional_currency` is dead and collides by name with a
live method on another owner. NEWLY FOUND.**
`dotmac_erp:app/services/finance/platform/fx.py:429` has zero callers; the 35
apparent hits belong to `dotmac_erp:app/services/finance/platform/org_context.py:36`.
`fx_service` (`fx.py:593`) and `ConversionResult` (`fx.py:28`) also have zero
references.

**D17 — Vendor CP's `offer_versions` is UPDATE/DELETE-able by the online API
role. STILL PRESENT, lineage scope stale.**
`dotmac_vendor_control_plane:alembic/versions/v002_offer_versions.py:58`. No
later revoke, no trigger, across a **v001–v012** lineage (the dossier and
ADR-0030 §5a both say v001–v011). Aggravated by caller-asserted version numbers
(`src/vendor_cp/offers/schemas.py:16`), no `previous_version_id` and no content
digest (`src/vendor_cp/offers/models.py:42-52`).

**D18 — `billing-sources.md` §4 item 6's coordinates. FIXED BY THE DOSSIER,
confirmed at head.** `app/services/billing/providers.py` is still 76 lines of
configuration; the described logic is at
`app/services/payment_provider_events.py:794-840`. The dossier's §5.2 correction
stands.

**D19 — `billing-extraction-dossier.md` §5.8 is now stale.**
`docs/inventories/README.md:42-44` carries all three billing rows. The item can
be struck.

**D20 — `test_contracts.py:194` has moved to `:282`. STILL-CITED WRONG
COORDINATE.** `dotmac_vendor_control_plane:tests/unit/test_contracts.py:281-285`
holds the sole `TenantEntitlementGrant`-count-zero assertion; `:19` is the
import. The file grew 108 lines on product-identity work.

---

## 4. Do not port

Beyond the six non-conformances the dossier already lists, these are the shapes
this revalidation adds. Each is a thing a port would carry silently.

1. **Sub's shadow scaffolding.**
   `dotmac_sub:app/services/billing/shadow_verification.py` (3,644 lines) and
   `scripts/billing/billing_target_shadow.py` are Sub's *own* ADR-0007 cutover
   harness. They belong in the module's **shadow plan** as a proven pattern —
   `tests/test_subledger_forward_shadow.py` is a working example worth
   studying — and nowhere in `source_paths`.
2. **`BillingRecordAuthority` as a column.**
   `dotmac_sub:app/models/billing_contract.py:46-54` is a migration-state flag
   on a row. It is right for Sub mid-cutover and wrong for a shared module:
   a table whose rows may or may not be money is the same "flag with a default"
   failure ADR-0023 §5 rejects for `platform=True`. The module ships one meaning
   per table.
3. **`cadence.py` — recurrence, smuggled into billing's source list.**
   `dotmac_sub:app/services/billing/cadence.py` (456 lines) is *"a pure
   resolver … exact half-open service and invoice intervals … deterministic
   proration factors"* (`:1-18`). ADR-0030 §1 assigns cadence and proration to
   `dotmac-subscriptions`, and `billing-parity-tests.md` §2 already routes
   `test_billing_cadence.py` there — but the dossier's `source_paths` still
   lists the implementation under `dotmac-billing`. **Remove it.** This is the
   only one of ADR-0030's six not-owned categories that a candidate source path
   actually smuggles in; dunning, PSP transport, GL journals, rendering and
   service suspension are all correctly absent (verified:
   `provider_payment_settlements.py` contains no HTTP client, credential,
   signature or webhook reference; `billing/_common.py` and
   `invoice_collectibility.py` contain no suspension or access-state coupling).
4. **`invoice_collectibility.py` as a predicate library.**
   `dotmac_sub:app/services/invoice_collectibility.py:15-21` defines
   `OPEN_INVOICE_STATUSES` (`:15`), `DUE_INVOICE_STATUSES` (`:20`) and
   `OVERDUE_DEBT_STATUSES` (`:21`)
   over `InvoiceStatus` members including `partially_paid` and `overdue`. Ten
   production files import it. Porting it ports the ADR-0016 conflation as a
   *reusable API* — the worst possible form, because every consumer then encodes
   it too. Coverage is derived; collectibility is a predicate over a derived
   value plus an aging date, and it needs rewriting, not lifting.
5. **`float()` on money, anywhere on a persistence, arithmetic or wire path.**
   D5, D6 and ERP's `banking/bank_upload.py:253` /
   `banking/reconciliation_parts/matching.py:1763`. `dotmac_kernel.money`
   already refuses to construct `Money` from a float; the module needs an
   architecture guard that refuses the *cast*, not only the constructor.
6. **A silent zeroing fallback on a money read.** D7. Ship the opposite: a
   position read that cannot answer raises, and the consumer decides.
7. **Sub's six money precisions.** D4. `NUMERIC(20,6)` is the ruling
   (ADR-0020 §A7); no source contributes it except ERP's AR invoice, which is
   the one shape to copy.
8. **A closed lifecycle enum for coverage, in either direction.** Sub's
   `InvoiceStatus` (D1) and ERP's eight grandfathered enums are the same defect
   twice. And note the inverse trap: ADR-0016's rule is that coverage is *not* a
   status, so `PaymentCoverage` is one of the few places an enum is correct
   (`dotmac_erp:app/services/finance/coverage.py:22`). Do not "open-register"
   it.
9. **An unconditional money literal ban.** `billing-extraction-dossier.md` §4.2
   already makes this point and it is worth restating with the caller evidence:
   `PAYMENT_DUST_DEFAULT = Decimal("0.01")` must exist exactly once, as a
   `SettingSpec` default, and the guard must be "a money literal appears only in
   a spec default" — but the module must also ship what ERP lacks, **a test that
   the resolved setting actually reaches the decision site** (D13). ERP has the
   spec, the reader and the guard, and the reader has no callers.
10. **Vendor CP's write-once-by-service pattern.** D17. Structural refusal is
    the requirement (ADR-0030 §5a); a service-level SELECT-then-INSERT with the
    online role holding `UPDATE` is the shape being replaced.
11. **A second at-most-once mechanism for document jobs.** D10. `queued /
    processing / completed / failed` with a wall-clock staleness heuristic
    retires to nothing, on both sides.
12. **Render-time resolution of any value that appears on an issued document.**
    D11. If it is printed, it is snapshotted at issuance.

---

## 5. Contract — what version one owns, and what it does not

The dossier's `owner` and `contract` strings hold and should be carried
forward unchanged. Two additions the caller evidence forces:

- **Owns**, explicitly added: the coverage *derivation* as one function with a
  resolved tolerance parameter **and a proof that the resolved setting reaches
  the decision** — because the source has the function and not the wiring (D12,
  D13).
- **Does not own**, explicitly added: **cadence, proration and period
  arithmetic** (`dotmac-subscriptions`, ADR-0030 §1). The dossier's `contract`
  string does not currently exclude it, and its `source_paths` includes the
  implementation.

### 5.1 The ADR-0030 §5 step 7 freeze list, contract by contract

ADR-0030 requires *"the obligation, settlement, allocation, receivable,
coverage and document-fact contracts frozen before downstream assembly
wiring."* Assessed at head:

| Contract | Concrete shape exists? | Is it the shape a shared owner should publish? | Freezable now? |
| --- | --- | --- | --- |
| **Obligation** — `AcceptRatedObligationV1` | **Yes**, `billing-authority-profile-contract.md` §2.1. Identity is the C10 tuple, backed by a real constraint in the source (`dotmac_sub:app/models/billing_contract.py:501`, migration `430_...:409`) | Yes. But the *behaviour* behind it is shadow-only (§2.1.2), so this is a contract over an unproven engine | **Blocked on a naming decision only.** `commercial-composition-and-conformance.md:551` records **three names for one output** — `RatedObligationOutputV1` / `RecurringObligationDueV1` / `subscriptions.recurring_obligation_due.v1` — "still open" (gap G5) |
| **Settlement** — `AcceptSettlementV1` / `SettlementObservationV1` | **Yes**, §2.2 | Yes | **Yes.** No recorded disagreement. This is the one clean freeze |
| **Allocation** | **No published shape anywhere** | Correctly so — allocation is billing-internal state, not a cross-owner arrow. It appears in no arrow in the §2.5 graph | **Not applicable.** ADR-0030's sentence should be amended: there are five publishable contracts plus one internal invariant |
| **Receivable** — Billing `ReceivablePositionV1` → assembly → Collections `ReceivableObservationV1` | **Resolved by ADR-0030 amendment 2026-08-23** | No | Billing is the sole position/financial-state owner and uses kernel `Money`; its fact carries source/exposure/account/subject/service identity, separate receivable/credit/prepaid lanes, service-period and due-date provenance, financial authority, projection mode and completeness. Collections' differently named peer input carries only the already-funded collectible amount and the decision provenance it needs. Unknown/unverified due evidence fails closed; reversal is an immutable movement that may reopen, never a steady state. The assembly mapping is the conformance boundary and neither module imports the other. This resolves historical G1/G2 only and is not release or adoption evidence. |
| **Coverage** | Function, not a wire contract — `dotmac_erp:app/services/finance/coverage.py` | The function is right; it has no callers (D12) and its tolerance is unwired (D13) | **Freezable as a kernel function signature.** It is not a published contract and should not be described as one. Its port needs the *wiring* proof the source never had |
| **Document fact** — `InvoiceDocumentFactV1` | **Yes, and it is the most concretely specified of the six** — `billing-authority-profile-contract.md` §2.5 gives identity, idempotency key (`f"{invoice_id}:{fact_version}"`, `fingerprint=None`), scope typing, and a **21-row field table** | Yes | **Freezable, with one rename.** D1 in §6.3 of that spec: `document_profile_code` vs `template_profile_code`, *"Substance is identical; only the name differs"* |

**The distinction that matters for `dotmac-document-rendering`.** ADR-0030 says
rendering follows once `InvoiceDocumentFactV1` is frozen. That is achievable
now — the **fact** is complete and only its profile-field name is contested.
What is *not* resolvable now is the **artifact relation** flowing back, which is
a different contract (`RecordDocumentArtifactV1`) over a different table, and it
is gap **G3**: billing Part 5 specifies a partial unique
`… WHERE superseded_at IS NULL` with repair-by-append, a `supersession_reason`
from an open registry, `withdrawn_at`, and an idempotency key that **includes**
the checksum; rendering §6.4 specifies a composite unique
`(scope, invoice_id, fact_version, media_type)` where *"the unique constraint
refuses a second row"*, repair by updating `file_id`/`checksum`/`byte_length`,
and a key that **excludes** the checksum. Both are PROPOSED; both defer to
Michael; *"the key compositions cannot both ship."* Additionally
**`InvoiceArtifactReconciler` has no module owner** — both teams place it on the
assembly and rendering §6.6 then rejects the assembly as a resting place.

**So step 7's freeze reduces to three decisions, not six contracts.** Settlement
and the document fact can be frozen this week. The obligation name, the
receivable contract (both its shape and its missing service period), and the
artifact-relation key composition each need a ruling. Freezing five of six and
starting on the sixth's persistence would be the mistake, because the receivable
position and the artifact relation are both *tables*, not just messages.

---

## 6. Draft `EXTRACTION.toml`

Not created as a file. This is the corrected content of
`billing-extraction-dossier.md` §1, with the changes this revalidation forces.
Changes from that block are marked in the trailing comment list.

```toml
schema_version = 1
package = "dotmac-billing"
classification = "optional-module"
status = "audit-complete"
source_mode = "greenfield-after-inventory"
owner = "Operational receivables on explicit tenant and platform planes: rated-obligation acceptance, invoice and credit-note lifecycle, confirmed settlement facts, allocation and reversal, and separately derived per-currency positions"
contract = "Accept an immutable pre-tax rated obligation under a database-enforced natural identity; issue and correct invoices and credit notes with frozen price/tax/FX snapshots; accept only independently confirmed, provider-neutral settlement observations exactly once; allocate, deallocate, reallocate, reverse and refund as immutable posting groups; derive collectible receivable, available customer credit and prepaid funding SEPARATELY per currency; derive payment coverage from amounts through one owner whose resolved tolerance is proven to reach every decision site; publish versioned receivable, accounting and invoice-document facts; and record which stored object is the OFFICIAL artifact of a document fact version, as an opaque handle plus render provenance, written only by the assembly's reconciler through a typed command. NOT commercial contract lifecycle, NOT cadence/proration/period arithmetic, NOT dunning policy or collections consequence, NOT general ledger / chart of accounts / journals / fiscal periods / statutory accounting or tax returns, NOT PSP clients / provider credentials / webhook signature verification, NOT document rendering, NOT byte storage, and NOT product access or entitlement mutation."
source_repositories = [
  "dotmac_sub",
  "dotmac_erp",
  "dotmac_vendor_control_plane",
]

# Sources are declared with their measured authority, because three of the
# richest are shadow-only in their own product and one is dead code. A path
# marked `shadow` or `unused` supplies a SHAPE and SCENARIOS, never a proven
# implementation, and its parity tests prove a design rather than production
# behaviour.
source_paths = [
  # --- Sub, LIVE money path: the behaviour that actually issues and settles ---
  "dotmac_sub:app/services/billing/invoices.py",            # live, 16 app callers
  "dotmac_sub:app/services/billing/credit_notes.py",        # live, 6
  "dotmac_sub:app/services/billing/payments.py",            # live, 11
  "dotmac_sub:app/services/billing/ledger.py",              # live, 7
  "dotmac_sub:app/services/billing/account_credit.py",      # live, 7
  "dotmac_sub:app/services/billing/adjustments.py",         # live, 6
  "dotmac_sub:app/services/provider_payment_settlements.py",# live, 2 (provider path only)
  "dotmac_sub:app/services/customer_financial_ledger.py",   # live read model, 9
  "dotmac_sub:app/services/billing/reconcile_unposted.py",  # live, 3
  "dotmac_sub:app/services/billing/tax.py",                 # live, 1
  "dotmac_sub:app/services/tax_accounting.py",              # live, 4
  "dotmac_sub:app/services/customer_tax_policies.py",       # live, 3
  "dotmac_sub:app/services/invoice_discounts.py",           # live, 4
  "dotmac_sub:app/services/billing/_common.py",
  "dotmac_sub:app/models/billing.py",
  "dotmac_sub:app/models/customer_tax_policy.py",
  # --- Sub, SHADOW-ONLY (AuthorityMigrationState.SHADOWING): shape, not proof ---
  "dotmac_sub:app/services/billing/obligations.py",         # shadow; 2 app callers, both shadow
  "dotmac_sub:app/services/billing/contracts.py",           # shadow; 6, five of them shadow
  "dotmac_sub:app/services/billing/rating.py",              # shadow; 2, both shadow
  "dotmac_sub:app/services/billing/customer_subledger.py",  # shadow authority; 9 stagers
  "dotmac_sub:app/models/billing_contract.py",              # carries uq_..._natural_identity:501
  "dotmac_sub:app/models/customer_subledger.py",
  # --- Sub, DESIGN INPUT ONLY ---
  "dotmac_sub:docs/adr/0007-end-to-end-billing-target-architecture.md",
  "dotmac_sub:docs/designs/BILLING_ACCOUNT_360.md",
  # --- ERP: the correct persistence shape, and the LIVE coverage rule ---
  "dotmac_erp:app/models/finance/ar/invoice.py",            # NUMERIC(20,6) + Computed balance_due:168
  "dotmac_erp:app/models/finance/ar/invoice_line.py",
  "dotmac_erp:app/models/finance/ar/invoice_line_tax.py",
  "dotmac_erp:app/models/finance/ar/customer_payment.py",
  "dotmac_erp:app/models/finance/ar/payment_allocation.py",
  "dotmac_erp:app/services/finance/ar/payment_status.py",   # LIVE coverage rule, 8 call sites
  "dotmac_erp:app/services/finance/ap/payment_status.py",   # LIVE, second copy — port as ONE
  "dotmac_erp:app/services/finance/coverage.py",            # UNUSED in production; the rule to adopt
  "dotmac_erp:app/services/finance/ar/advance_allocation.py",   # live, 1 caller
  "dotmac_erp:app/services/finance/ar/exact_match_allocation.py",# live, 2 callers
  "dotmac_erp:app/services/finance/money_boundary.py",      # 9 of 17 exports live, sync-boundary only
  "dotmac_erp:app/models/finance/tax/tax_jurisdiction.py",
  "dotmac_erp:app/models/finance/tax/tax_code.py",
  "dotmac_erp:app/models/finance/tax/fiscal_position.py",
  "dotmac_erp:app/models/finance/tax/tax_transaction.py",
  "dotmac_erp:app/models/finance/core_fx/currency.py",
  "dotmac_erp:app/models/finance/core_fx/exchange_rate.py",
  "dotmac_erp:app/models/finance/core_fx/exchange_rate_type.py",
  "dotmac_erp:app/services/finance/platform/fx.py",         # 2 of 9 methods live; shape only
  # --- Vendor CP: cutover-1 target shape (platform plane, migration source) ---
  "dotmac_vendor_control_plane:src/vendor_cp/offers/models.py",
  "dotmac_vendor_control_plane:src/vendor_cp/contracts/models.py",
  "dotmac_vendor_control_plane:src/vendor_cp/contracts/service.py",
  "dotmac_vendor_control_plane:src/vendor_cp/accounts/models.py",
]

# REMOVED from the 2026-08-14 list, with reasons:
#   dotmac_sub:app/services/billing/cadence.py       -> dotmac-subscriptions (recurrence, ADR-0030 s1)
#   dotmac_sub:app/services/billing/shadow_verification.py -> Sub's own cutover harness, 3644 LOC
#   dotmac_sub:app/services/billing/subledger_opening.py   -> zero production callers
#   dotmac_sub:app/services/customer_financial_position.py -> the shape being replaced (live but wrong)
#   dotmac_sub:app/services/invoice_collectibility.py      -> encodes the ADR-0016 conflation as an API

preserved_tests = [
  # --- ERP coverage: PORT THE ASSERTIONS, NOT THE WIRING (it has none) ---
  "dotmac_erp:tests/integration/test_coverage_parity.py",            # PostgreSQL, 99
  "dotmac_erp:tests/architecture/test_paid_status_single_owner.py",  # 176; premise stale, see D14
  "dotmac_erp:tests/architecture/test_coverage_is_not_a_lifecycle_status.py", # 163
  "dotmac_erp:tests/architecture/test_monetary_documents_carry_coverage.py",  # 174; post-pin
  "dotmac_erp:tests/unit/test_payment_coverage.py",                  # 141
  "dotmac_erp:tests/ifrs/ar/test_payment_status.py",                 # 167
  "dotmac_erp:tests/ifrs/ap/test_payment_status.py",                 # 142, symmetry proof
  "dotmac_erp:tests/finance/test_advance_allocation.py",             # 277
  "dotmac_erp:tests/services/test_exact_match_allocation.py",        # 215
  "dotmac_erp:tests/ifrs/ar/test_customer_payment_service.py",       # 417
  "dotmac_erp:tests/ifrs/ar/test_invoice_service.py",                # 963
  "dotmac_erp:tests/ifrs/tax/test_tax_calculation_service.py",       # 121
  "dotmac_erp:tests/ifrs/platform/test_fx_service.py",               # 562, shape seed only
  "dotmac_erp:tests/finance/test_money_boundary.py",                 # 489
  # --- Sub: SQLite unless marked; scenarios port, database proofs do not ---
  "dotmac_sub:tests/test_payment_allocation_settlement_consequence.py", # 773, the canonical path
  "dotmac_sub:tests/test_payment_settlement_allocation_evidence.py",    # 289
  "dotmac_sub:tests/test_payment_reallocation.py",                      # 293
  "dotmac_sub:tests/test_payment_reversal_evidence.py",                 # 434
  "dotmac_sub:tests/test_payment_refund_evidence.py",                   # 266
  "dotmac_sub:tests/test_refund_money_correctness.py",                  # 265
  "dotmac_sub:tests/test_partial_refund_invoice_state.py",              # 142, assertion inverts
  "dotmac_sub:tests/test_refund_guards.py",                             # 97
  "dotmac_sub:tests/test_ledger_reversal_integrity.py",                 # 194
  "dotmac_sub:tests/test_payment_import_batch_reversal.py",             # 411
  "dotmac_sub:tests/test_provider_payment_settlements.py",              # 462
  "dotmac_sub:tests/test_opening_settlement_correction.py",             # 455, changed since pin
  "dotmac_sub:tests/test_payment_update_settlement.py",                 # 150, must get stronger
  "dotmac_sub:tests/test_payment_mark_status_guard.py",                 # 54
  "dotmac_sub:tests/services/billing/test_payment_status_recompute.py", # 604, negative proof
  "dotmac_sub:tests/architecture/test_payment_settlement_participants.py", # 194
  "dotmac_sub:tests/services/billing/test_consolidated_settlement_reconciliation.py", # 397
  "dotmac_sub:tests/test_payment_webhook_settlement.py",                # 954, ports SPLIT
  "dotmac_sub:tests/test_customer_subledger.py",                        # 381, shadow owner
  "dotmac_sub:tests/test_customer_financial_position.py",               # 277
  "dotmac_sub:tests/test_subledger_opening_positions.py",               # 751
  "dotmac_sub:tests/test_subledger_forward_shadow.py",                  # 469, harness pattern
  "dotmac_sub:tests/test_customer_financial_ledger.py",                 # 702
  "dotmac_sub:tests/test_billing_money_bounds.py",                      # 112
  "dotmac_sub:tests/test_credit_notes.py",                              # 748
  "dotmac_sub:tests/test_credit_note_apply_on_issue.py",                # 600
  "dotmac_sub:tests/integration/test_credit_note_issue_concurrency.py", # 186, PostgreSQL
  "dotmac_sub:tests/test_invoice_issued_at_invariant.py",               # 107
  "dotmac_sub:tests/test_invoice_transition_guards.py",                 # 92
  "dotmac_sub:tests/test_invoice_recalc_status.py",                     # 54, negative proof
  "dotmac_sub:tests/test_invoice_written_off.py",                       # 89
  "dotmac_sub:tests/test_invoice_closure_evidence.py",                  # 389
  "dotmac_sub:tests/test_invoice_draft_authoring.py",                   # 539, changed since pin
  "dotmac_sub:tests/test_invoice_read_negative_lines.py",               # 271
  "dotmac_sub:tests/test_invoice_discounts.py",                         # 369
  "dotmac_sub:tests/services/billing/test_invoice_lifecycle_owner.py",  # 208, changed since pin
  "dotmac_sub:tests/services/billing/test_invoice_construction_owner.py", # 136
  "dotmac_sub:tests/test_tax_accounting.py",                            # 488, de-Nigerianize
  "dotmac_sub:tests/architecture/test_tax_accounting_ownership.py",     # 191
  "dotmac_sub:tests/integration/test_tax_accounting_concurrency.py",    # 111, PostgreSQL
  "dotmac_sub:tests/test_billing_obligations.py",                       # 458, proves a SHADOW engine
  "dotmac_sub:tests/test_billing_rating.py",                            # 405, proves a SHADOW engine
  "dotmac_sub:tests/architecture/test_billing_target_architecture.py",  # 417, C1-C10 seed
  # --- Vendor CP ---
  "dotmac_vendor_control_plane:tests/unit/test_offers.py",              # 146
  "dotmac_vendor_control_plane:tests/unit/test_contracts.py",           # 330 (grant-zero now :282)
  "dotmac_vendor_control_plane:tests/migration/test_replay_concurrency.py", # 217
]

# NOT ported: dotmac_sub:tests/test_billing_cadence.py and
# test_subscription_billing_cadence.py -> dotmac-subscriptions.
# dotmac_erp:tests/finance/test_cash_basis_vat.py -> boundary marker, stays ERP's.
# dotmac_sub:tests/test_billing_invoice_pdf_storage.py (517) -> requirement input only;
#   it proves the render-cache model this module rejects (D10).
# dotmac_sub:tests/test_api_billing_webhooks.py, test_reconcile_webhook.py,
#   tests/architecture/test_payment_webhook_ownership.py,
#   tests/architecture/test_payment_gateway_control_plane.py -> Integrator.

contract_consumers = []
candidate_consumers = ["dotmac_vendor_control_plane", "dotmac_sub"]
composition_boundary = "ADR-0024: each adopter installs its own billing lineage, owns its own financial rows and binds its own commercial authority, tax/FX/numbering adapters and settlement input. Applications share the package contract, never a mod_<billing> schema, an invoice series or a receivable position. Billing imports neither dotmac-subscriptions nor dotmac-collections nor dotmac-numbering nor dotmac-files nor a consuming assembly; every cross-module outcome travels a versioned contract the assembly wires. Cadence, proration and period arithmetic are dotmac-subscriptions' and appear nowhere in this package. Series allocation is dotmac-numbering's, bound as a NumberingProvider at issuance. Provider identity, credentials, webhook verification, retries and checkpoints are Integrator connector-plugin concerns. ERP remains sole general-ledger and statutory-accounting authority and consumes immutable accounting facts idempotently."
inventory_evidence = [
  "docs/inventories/billing-sources.md",
  "docs/inventories/billing-extraction-dossier.md",
  "docs/inventories/billing-parity-tests.md",
  "docs/inventories/billing-source-variance.md",
  "docs/adr/0020-billing-owns-operational-receivables.md",
  "docs/adr/0023-dual-plane-modules-declare-both-persistence-planes.md",
  "docs/adr/0024-apps-compose-by-synchronizing-data.md",
  "docs/adr/0030-cloud-commerce-is-composed-from-complete-domain-owners.md",
  "docs/superpowers/specs/2026-08-14-billing-authority-profile-contract.md",
  "docs/superpowers/specs/2026-08-14-commercial-composition-and-conformance.md",
  "docs/superpowers/plans/2026-08-14-billing-vendor-cp-sub-cutover.md",
]
first_cutover = "dotmac_vendor_control_plane, PLATFORM plane, per ADR-0020 s6. Greenfield on invoicing, re-verified 2026-08-15 at origin/main 80d3f347: zero hits under src/ or tests/ for invoice, payment, receivable, settlement, credit_note, refund, reversal, dunning, subledger, posting or arrears; the exclusion is documented at docs/design/domain-foundation.md:60 and :597. 18 owned tables (plus composed module tables in mod_rel, mod_ealloc and mod_approvals), no tenant_id column anywhere, no RLS, isolation by GRANT/REVOKE against app_user. It has never deployed (0 tags, 0 releases, 0 deployments, 0 production-deploy runs) but the full deploy machinery is now checked in, so the ADR-0030 s5 step-4 trigger conditions must be re-checked immediately before cutover rather than once. Its String(40) money is a MIGRATION SOURCE converted to NUMERIC(20,6), and v011 replaced uq_offer_versions_code_ver with a product-qualified uq_offer_versions_product_code_ver, so the version identity to port is the product-qualified one. dotmac_sub is cutover 2 on the TENANT plane; it is the scenario source, not a proven implementation, because its ADR-0007 obligation/rating/subledger stack is declared SHADOWING and its live money path is the pre-ADR-0016 invoice/ledger/position stack."
shadow_and_drift = "Vendor CP is greenfield, so there is no old invoice owner to shadow; its substitutes are the cutover plan's V1 preview, the same-currency scenario matrix against fakes, a proven platform-role/tenant-role privilege split, one real production flow, an exact per-account/per-currency position rebuild hash, and an idempotent ERP accounting-fact receipt. Sub runs a PURE behaviour shadow against captured immutable source inputs in an isolated shadow database unreachable from product routes; the legacy Sub writer remains sole authority and financial commands are never dual-written. Sub has already built and proven this harness shape once for its own ADR-0007 phase 3 (tests/test_subledger_forward_shadow.py, 469 lines, plus app/services/billing/shadow_verification.py, 3644 lines) and that pattern -- not that code -- is what the module reuses. Comparison is exact at source-identity level over documents, settlements, allocations, positions and accounting facts, with NO money tolerance; every mismatch is classified as source defect, known intentional correction, missing evidence, contract defect or shadow bug, and an unclassified difference blocks cutover. Acceptance requires all active cadences and settlement paths exercised plus three consecutive complete reconciliations with zero unclassified drift. ADDED 2026-08-15: because Sub's Invoice has no amount_paid column, the shadow must first RECONSTRUCT that operand from settlement history and prove the reconstruction is stable across two independent runs before any coverage comparison is meaningful; and because Sub carries six money precisions, every comparison must be performed at NUMERIC(20,6) with an explicit refusal on any value that does not round-trip."
local_copy_retirement = "Vendor CP has no local financial writer to retire; its obligation is to add one only through the module. Sub retires its estate slice by slice under an ADR-0018 two-directional ratchet, and a slice is DONE ONLY WHEN THE LOCAL WRITER IS DELETED. The eight ratcheted slices are unchanged from the 2026-08-14 dossier section 3 (R1 invoice/credit-note lifecycle and numbering; R2 obligations; R3 rating and applied tax; R4 settlement/allocation/refund/reversal including the PaymentUpdate money fields; R5 posting groups and per-currency positions; R6 credit/deposits/adjustments; R7 derived-balance read surfaces; R8 provider-event money consequences, which retires to the INTEGRATOR not to billing; R9 the invoice-document artifact relation, which splits three ways). ADDED 2026-08-15: R2 and R5 are ratchets over SHADOW writers, so their baselines must count shadow-row producers as well as live writers, and neither slice can be declared done while billing.obligations or financial.customer_subledger remains AuthorityMigrationState.SHADOWING in Sub's own registry -- retiring a shadow writer to a module that is also not authoritative moves nothing. R7 additionally counts float() casts on money: 125 across 33 files at origin/main, not the single site the 2026-08-14 dossier recorded. ERP retires nothing to billing; its coverage.py retires to the KERNEL coverage slice, and that cutover must ADD the caller wiring the source never had, since coverage_of, coverage_case, PaymentCoverage and resolve_payment_dust have zero production callers in ERP today."
next_action = "Three contract rulings, then the PostgreSQL proofs, then code. (1) Michael rules on the obligation output NAME (three names for one output, gap G5), the ReceivablePositionV1 shape AND its missing service-period field (gaps G2 and G1), and the artifact-relation key composition plus the InvoiceArtifactReconciler's owner (gap G3) -- all recorded in docs/superpowers/specs/2026-08-14-commercial-composition-and-conformance.md:551. AcceptSettlementV1 and InvoiceDocumentFactV1 can be frozen now, the latter subject only to the document_profile_code / template_profile_code rename. Freezing InvoiceDocumentFactV1 unblocks dotmac-document-rendering; it does NOT unblock the artifact relation, which is a different contract over a different table. (2) Land the PostgreSQL test matrix in this document's section 7 as the module's first commit, before any behaviour, because the source's money proofs run on SQLite (51 of 54) and its two flagship owners are shadow-only or unused. (3) Bind dotmac-numbering as the NumberingProvider -- the package exists at packages/dotmac-numbering (PR #193) with status audit-complete and contract_consumers = [], so billing is its first candidate consumer and that adoption is a named deliverable, not an assumption. dotmac_kernel.durable_timers (ADR-0030 step 6) does NOT exist yet; billing does not need it, but ADR-0030 s5 sequences it before step 7 and the ordering exception should be recorded explicitly rather than taken silently. When the rulings land, Stage E creates packages/dotmac-billing/ with this content as its EXTRACTION.toml, allocates the short code, schema, prefix and branch label IN THAT SAME CHANGE, ships both declared persistence planes in revision 1, and exposes versions_dir() from day one."
```

---

## 7. PostgreSQL test matrix

Fresh proofs, on real migrated PostgreSQL. The source contributes almost nothing
here: 51 of Sub's 54 preserved tests run on SQLite against a
`metadata.create_all` schema, so **every constraint added in a migration is
unproven in the source**, and ERP's one PostgreSQL money proof
(`test_coverage_parity.py`) evaluates over literals with no tables.

Each guard must assert an observable **only the guard produces** — a SQLSTATE, a
constraint name, or an exact value — never timing, liveness or set membership.

| # | Proof | Setup | Mechanism | PASS asserts | A FAILING run looks like |
| --- | --- | --- | --- | --- | --- |
| **T1** | Obligation natural identity refuses a duplicate under concurrency | tenant plane, one contract version, one line | two sessions, both `INSERT` the same C10 tuple, `SET LOCAL lock_timeout`; commit A then B | B raises SQLSTATE **`23505`** with `constraint_name == "uq_billing_obligation_natural_identity"`, and `SELECT count(*)` is exactly **1** | two rows and no exception, or a `23505` naming a different constraint (the composite is wrong) |
| **T2** | Same key, different rated amount is a loud conflict, not a replay | one accepted obligation | re-issue `AcceptRatedObligationV1` with an altered net amount and the same idempotency key | the module raises `IdempotencyConflict` **and** the stored `fingerprint` column is unchanged; the returned error carries the stored and offered fingerprint hexes | a silent replay returning the original obligation (this is Sub's `billing_automation` duplicate-billing shape) |
| **T3** | `balance_due` is generated and cannot be written | tenant + platform invoice tables | `UPDATE invoices SET balance_due = 0` | SQLSTATE **`428C9`** (`cannot insert into a generated column`), and a second assertion that `balance_due` still equals `total_amount - amount_paid` to 6 dp | the `UPDATE` succeeds — the module reproduced Sub's plain column (D3) |
| **T4** | Money round-trips at `NUMERIC(20,6)` and refuses overflow | one invoice per currency incl. a 0-minor-unit and a 3-minor-unit currency | insert `999999999999.999999`, then `9999999999999.9` | first reads back **exactly equal** as `Decimal`, second raises SQLSTATE **`22003`** (`numeric_value_out_of_range`) | a silently quantized value — the failure Sub's six precisions and Vendor CP's `String(40)` migration both risk |
| **T5** | Coverage: Python and SQL agree, **and the resolved setting reaches both** | `payments.payment_dust` seeded to `0.00`, `0.01` and `1.00` on three tenants | run the 12-case boundary matrix under each tenant's resolved dust | `coverage_of(...) == coverage_case(...)` for all 36 cells; **and** a fourth tenant whose dust row is `0.05` produces `PAID` where the default `0.01` produces `PARTIAL` | the `0.05` tenant behaves like the default — the tolerance is hardcoded, which is exactly ERP's live state (D13) |
| **T6** | The coverage matrix reaches every member (sensitivity proof for T5) | same | collect the produced set | `reached == {UNPAID, PARTIAL, PAID, OVERPAID}` | a matrix producing three members: T5 would agree perfectly and prove nothing |
| **T7** | Invoice-number uniqueness differs per plane, and both bite | both planes, one series | tenant: two tenants each allocate `INV-2026-00001` (must both succeed); then one tenant allocates it twice. platform: two allocations of the same `(series_code, number)` | the cross-tenant pair succeeds; the intra-tenant duplicate raises `23505` naming **`uq_invoices_tenant_series_number`**; the platform duplicate raises `23505` naming **`uq_platform_invoices_series_number`** | either constraint name absent, or the cross-tenant pair rejected (the constraint was "unified" and needs a nullable tenant — §2.3's refusal) |
| **T8** | Concurrent issuance allocates one number, and a replay returns the same one | one series at `n` | two sessions issue simultaneously through the bound `NumberingProvider`; then replay session A's exact command | the two numbers are `n+1` and `n+2` with **no gap and no duplicate**; the replay returns **byte-identical** `document_number` and creates no row | both sessions get `n+1`, or the replay allocates `n+3`. Note this is the module's *first* test of this behaviour anywhere in the fleet: `next_invoice_number` has zero tests (D9) |
| **T9** | RLS is ENABLEd **and FORCEd** on every tenant money table | migrated tenant plane, two tenants with rows | as `app_user` with tenant A's GUC set, `SELECT` and `UPDATE` tenant B's invoice by primary key; then repeat as the table owner | `SELECT` returns **0 rows** and `UPDATE` reports **0 rows affected** for every tenant table; and `pg_class.relforcerowsecurity` is **true** for each (the owner check is what FORCE adds) | any row visible, or `relforcerowsecurity` false — RLS enabled but not forced means the owner bypasses it |
| **T10** | Platform tables are revoked across all seven privileges **and their column forms** | migrated platform plane | for each platform table, `has_table_privilege('app_user', t, p)` for all of `SELECT, INSERT, UPDATE, DELETE, REFERENCES, TRIGGER, TRUNCATE`; then `has_column_privilege('app_user', t, c, p)` for every column | every one of them is **false**; and the online platform role has schema `USAGE` **plus** at least one of `SELECT/INSERT/UPDATE/DELETE` per table | a table-level revoke with a surviving column grant — the ADR-0023 hole that a table-only check cannot see |
| **T11** | No foreign key crosses the planes, in either direction | migrated catalog | query `pg_constraint` for `contype='f'` joined to both tables' plane declarations | the set of cross-plane FKs is **empty**, asserted by naming the offending constraint if not | any FK whose two ends are declared on different planes |
| **T12** | The current-artifact partial unique holds and supersession appends | one issued invoice, one artifact | insert a second artifact for the same `(fact_id, media_type)` with `superseded_at IS NULL`; then set the first's `superseded_at` and retry | the first attempt raises `23505` naming the partial index; after supersession the insert succeeds and **exactly one** row has `superseded_at IS NULL` | both rows current, or supersession blocked (repair impossible). **Gated on the G3 ruling — do not build the table before it** |
| **T13** | The reconciler converges with the event suppressed | issued invoice, `invoice.issued` delivery disabled at the outbox | run the reconciler; then force the work queue empty and run again | run 1 produces an artifact and the assertion names its `file_id`; run 2 with an empty queue produces **no** artifact, which is the canary proving the test can fail | convergence in run 2 — the test is not measuring the reconciler |
| **T14** | An accounting fact replays as an exact no-op | one committed invoice, ERP intake fake | deliver `AccountingFactV1` three times | after each delivery the intake's row count and the SHA-256 of its ordered fact digests are **identical**; an unmapped effect raises in the *consumer*, and billing records no retry | a second row, a changed digest, or billing retrying an unmapped effect (a fallback journal) |
| **T15** | Position rebuild is exact and hash-stable | ~10k posting groups across 3 currencies and 3 lanes | rebuild every per-currency/per-lane position from immutable posting groups twice, in different orders | the two rebuilds produce the **same SHA-256** over the ordered `(account, currency, lane, amount)` tuples, and each amount equals the stored projection to 6 dp | any hash difference, or a lane sum that matches only after rounding — the drift detector is order-dependent |
| **T16** | There is no combined-balance field, and no caller can make one | the published `ReceivablePositionV1` | reflect the contract's fields; and AST-sweep the module for any expression adding two of `{collectible_receivable, available_credit, prepaid_funding}` | the field set has exactly three money members and no fourth; the AST sweep finds **zero** sites; and a planted `a + b` in a fixture module **is** found (the sensitivity proof) | the sweep passes with the plant present — the detector does not bite. This is the guard against D5/`current_balance` returning |
| **T17** | No money literal outside a `SettingSpec` default; no `float()` on money | module source | AST walk for `Decimal("...")` in a comparison, `CASE`, or service body, and for `float(x)` where `x` is money-typed; plus a planted violation of each | zero real hits; both plants are found and named at file:line | a plant missed. A blanket string ban on `0.01` would fail the very implementation being ported — the rule is *location*, not value |

T1, T2, T7, T8 and T12 are the concurrency proofs; each needs two real
connections and an explicit `lock_timeout`, not `threading` against one session.
T9–T11 need the migrated catalog, not `metadata.create_all` — which is exactly
the gap that made 51 of the source's 54 tests unable to prove any of this.

---

## 8. Adoption and retirement

**First cutover is unchanged: Vendor CP, platform plane.** It is the only clean
adopter — greenfield on invoicing (re-verified), no tenant column, no RLS, no
sentinel tenant, and it already consumes kernel `Money`. Three conditions this
revalidation adds:

1. **Re-check the deployment trigger immediately before the cutover, not once.**
   ADR-0030 §5 step 4's conditional skip is accurate today, but the deploy
   workflow, compose file, nginx vhosts, deploy script and secret materializer
   are all checked in and the image has built twice. If a production-deploy run,
   a non-empty deployments API or host-side evidence appears first, the
   `offer_versions` hardening (D17) becomes due *before* the extraction, not
   with it.
2. **The money migration is a representation conversion with a refusal, not a
   column type change.** Every `String(40)` parses to `NUMERIC(20,6)` under the
   declared minor-unit precision for its currency, and a value that will not
   round-trip is a migration failure, not a silent quantization (T4).
3. **Port the product-qualified version identity.** `v011` replaced
   `uq_offer_versions_code_ver` with `uq_offer_versions_product_code_ver` on
   `(product_code, offer_code, version)`, and refuses `downgrade()`. A module
   that ports the pre-`v011` identity would be porting a shape Vendor CP has
   already outgrown.

**Second cutover is Sub, tenant plane — and it is a bigger job than the dossier
prices.** The eight retirement slices stand. Two need restating:

- **R2 (obligations) and R5 (posting groups) are ratchets over shadow
  writers.** Their baselines must count shadow-row producers as well as live
  writers, and neither slice can be called done while `billing.obligations` or
  `financial.customer_subledger` is still `SHADOWING` in Sub's own registry.
  Retiring a shadow writer into a module that is also not authoritative moves
  no authority; it renames the shadow.
- **R5 has a prerequisite nobody has costed: Sub has no `amount_paid`.** The
  coverage operand must be reconstructed from settlement history and proven
  stable across two independent runs before any coverage comparison means
  anything. That is a data project inside the cutover, and it is the single
  most likely source of schedule surprise.
- **R7's ratchet is 125, not 1.** The float-on-money count across 33 files (D5)
  is the real baseline.

**Sequencing against ADR-0030 §5.** Step 5 (`dotmac-numbering`) is **done** —
`packages/dotmac-numbering` landed in PR #193, `status = "audit-complete"`,
`contract_consumers = []`. Billing is its first candidate consumer, and binding
it as the `NumberingProvider` is a named deliverable of step 7, not an
assumption. Step 6 (`dotmac_kernel.durable_timers`) **does not exist** — no
timer module is present in the kernel at `origin/main`. Billing does not need
timers (§4 of ADR-0030 names subscriptions and collections as the reason they
must be separate), so running step 7 before step 6 is defensible — but §5 says
work completes one owner before opening the next, so **the ordering exception
should be recorded explicitly rather than taken silently.**

**What must not be reported as adoption.** ADR-0030 §2: *"A green test suite may
not be reported as a cutover."* For this module the temptation is specific and
worth naming, because the source makes it easy: Sub already has 54 passing money
tests and a 3,644-line shadow harness, and both would go green against a module
that has never carried a real receivable. A cutover is Vendor CP issuing a
numbered invoice against a real contract in a real deployment, or Sub's registry
declaration moving off `SHADOWING` with the finance sign-off its own gate
requires. Nothing short of that.

---

## 9. Method, and what this revalidation did not cover

**Method.** Every repository was fetched, `origin/main` resolved, and every
citation read at that revision with `git show` / `git grep`. Path existence and
drift were established by `git diff <pin>..origin/main -- <path>`. Caller counts
were taken over `app/`/`src/`, `tests/` and `scripts/` separately, using four
import styles (`from a.b.c import X`, `from a.b import c`, `import a.b.c`, bare
symbol) and cross-checked, because the first pass under-counted
`from app.services import X` and would have reported false zeros for
`tax_accounting` and `invoice_discounts`. The two headline caller findings —
Sub's obligation stack and ERP's `coverage.py` — were each verified twice, by
independent greps.

**Not covered, stated so the next agent does not assume it was:**

- **Sub's routes, jobs, webhook adapters and CLI.** The cutover plan's B0 asks
  for money-touching inventory at route/job/adapter granularity. This audit
  reached service and model granularity only. Sub has 576 tables and ~74k LOC of
  money-domain service code; the 22 service files and 54 test files named here
  are the dossier's list, re-verified, not an exhaustive sweep.
- **The 353 Sub commits' content.** Only the 82 dossier-named paths were
  diffed. Something relevant may have landed in a file no document names.
- **`dotmac-collections` and `dotmac-subscriptions` sources**, except where a
  boundary is shared (cadence, `prepaid_policy.py:57`'s `period_start` read).
  `collections-sources.md` and `subscriptions-sources.md` are separate audits.
- **Whether the shadow rows are actually being written in Sub's production
  database.** The registry declares `SHADOWING`; whether the operator CLI has
  been run, and against how much of the estate, is host-side evidence this audit
  did not seek and must not infer.
- **Any test execution.** No test was run. Every LOC figure is `wc -l`, every
  assertion claim is from reading the file, and the database each lane uses was
  established from `conftest.py` and `pytest.ini`, not from a run.
- **`dotmac_academy_app`, `dotmac_workspace`, `dotmac_integrator`,
  `dotmac_governance`.** None is named as a billing source or adopter by any of
  the three documents or by ADR-0030 §7.
