# `dotmac-billing` — parity-test inventory

**As of:** 2026-08-14
**Sources audited:** `dotmac_sub`, `dotmac_erp`, `dotmac_vendor_control_plane`
**Companion:** `docs/inventories/billing-extraction-dossier.md` (what is ported),
`docs/superpowers/specs/2026-08-14-billing-authority-profile-contract.md` (what
the contracts guarantee), `docs/inventories/billing-sources.md` (the evidence
base)
**Governed by:** ADR-0006's product-first amendment (port the qualifying
implementation *and its parity tests*), ADR-0016, ADR-0020 § 5

> **No test was executed for this inventory.** Every entry is a file that was
> confirmed to exist by `ls`/`wc -l`, read for its assertions, or located by
> symbol grep. LOC figures are `wc -l`. CI is the only acceptance owner in this
> fleet, and a green local run would not be evidence here even if one had been
> made.

---

## How to read this

The product-first procedure's whole point is that behaviour arrives with its
proof. A ported service whose test did not come with it is a rewrite wearing the
source's variable names.

Each row states **the behaviour it proves** and **the defect it guards** —
because a test whose guarded defect nobody can name is a test nobody will
maintain through a port.

Section 5 is the more valuable half of this document: the areas where **no
adequate source test exists**. Padding the port list with weak tests would hide
those. There are **six** — G6 was added on 2026-08-14 with the official-artifact
relation — and two sit on the critical path: invoice numbering (G1) blocks
internal issuance, and the artifact reconciler (G6) is required before Vendor CP
cutover.

G6 also shows the harder half of a parity audit: a source suite can exist, be
substantial, and still be the wrong thing to port. Sub has 517 lines of tests
over its PDF export path, and every one of them proves the render-cache model
this extraction rejects. **A nearby test is not a parity baseline.**

---

## 1. Coverage — ERP is the qualifying source

ADR-0016 is fleet-wide and ERP holds the reference implementation. This is the
single highest-value port in the programme, because the defect it guards produced
twelve divergent sites and seven rules in one repository, and reached the same
conflation independently in three accounting domains.

| Test | LOC | Proves | Guards |
|---|---|---|---|
| `dotmac_erp:tests/integration/test_coverage_parity.py` | 99 | Python `coverage_of` and SQL `coverage_case` agree, over a 12-case boundary matrix × three tolerances (`"0.01"`, `"0"`, `"1.00"`), evaluated over literals with **no tables**, **on PostgreSQL**. | The two-implementation drift that is structurally unavoidable once a rule exists in both Python and SQL. Running it on SQLite would mask a real `NUMERIC` difference — the PostgreSQL requirement is part of the proof, not an inconvenience. |
| — its `test_the_matrix_reaches_every_member` | (same file) | Every member of `UNPAID/PARTIAL/PAID/OVERPAID` is actually produced by the matrix. | **This is the parity test's own sensitivity proof.** A matrix that only ever produced `PAID` would agree perfectly and prove nothing. Port this with the parity test or the port is worthless. |
| `dotmac_erp:tests/architecture/test_paid_status_single_owner.py` | 176 | An AST walk over `app/` and `scripts/` finds no `<obj>.status = InvoiceStatus.PAID/PARTIALLY_PAID` outside two declared owner modules. Ships **four detector self-tests**: it fires; it ignores a local-name assignment; it covers AP as well as AR; it does not sweep in binary lifecycle enums like `ExpenseClaimStatus.PAID` that have no `PARTIALLY_PAID` member. | The eight implementations with six different rules its own docstring records. The four self-tests are why this guard can be trusted after a port — the fourth in particular stops the check becoming so broad that it fails legitimate code and gets disabled. **Path correction:** it is under `tests/architecture/`, not `tests/integration/`. |
| `dotmac_erp:tests/architecture/test_coverage_is_not_a_lifecycle_status.py` | 163 | No lifecycle status enum declares a `PAID` or `PARTIALLY_PAID` member. | The ADR-0016 defect reappearing in a *new* document type. This is the guard that must be live in `dotmac-billing` from revision 1, because Sub's `InvoiceStatus` (`app/models/billing.py:31-32`) would fail it today. |
| `dotmac_erp:tests/architecture/test_monetary_documents_carry_coverage.py` | 174 | Every model carrying a monetary total also carries the coverage operands. | ADR-0016's "where coverage was left out entirely" failure — three documents that cannot express partial payment at all because they have no `amount_paid` column. |
| `dotmac_erp:tests/unit/test_payment_coverage.py` | 141 | `coverage_of`'s four-branch classification, pure-function. | The branch **order**: `OVERPAID` before `PAID` before `PARTIAL` before `UNPAID`. The branches overlap, so reordering them silently changes answers at the boundaries. |

**Port note.** ERP declares `total_amount`/`amount_paid`/`balance_due` per model
with **no shared mixin**, duplicated across five models
(`ar/invoice.py:168`, `ap/supplier_invoice.py:179`,
`expense/expense_claim.py:320`, `people/payroll/salary_slip.py:215`,
`finance/lease/lease_payment_schedule.py:77`). ADR-0016 § 5 requires the mixin.
The module ships one, so
`test_monetary_documents_carry_coverage.py` ports with a **stronger** assertion
than it makes today: not "every money model has the columns" but "every money
model uses the mixin".

---

## 2. Rating, cadence and obligations — Sub is the qualifying source

| Test | LOC | Proves | Guards |
|---|---|---|---|
| `dotmac_sub:tests/test_billing_obligations.py` | 458 | Obligation creation, state transitions (`scheduled → open → partially_resolved → resolved`, with `canceled`/`written_off` branches), and resolution. Two identity tests are load-bearing: `:165 test_replaying_the_same_natural_identity_returns_one_obligation` and `:221 test_same_natural_identity_with_different_coverage_fails_closed`. | **C10.** Backed by a real constraint, `uq_billing_obligation_natural_identity` (`app/models/billing_contract.py:501`). The second test is the more important one: it proves the same key with *different content* fails closed rather than replaying — which is exactly the `IdempotencyConflict` semantics the module's `AcceptRatedObligationV1` fingerprint depends on. |
| `dotmac_sub:tests/test_billing_rating.py` | 405 | Net/tax/gross derivation per obligation. | Rating drift between the invoice line and the obligation it came from. |
| `dotmac_sub:tests/test_billing_cadence.py` | 287 | Cadence/period computation. | C1 — the `BillingCycle` enum. **Ports to `dotmac-subscriptions`, not billing** (P5); listed here so a billing port does not absorb it, and so nobody assumes cadence is untested. |
| `dotmac_sub:tests/test_subscription_billing_cadence.py` | 164 | Cadence as applied to a subscription. | Same; same destination. |
| `dotmac_sub:tests/architecture/test_billing_target_architecture.py` | 417 | Sub's own ADR-0007 invariants as an executable check. | This is the closest thing in the fleet to the module's C1–C10 conformance suite, and it is the right starting shape for it — an architecture test the source already maintains, rather than a set invented at the boundary. |

**Cadence caveat.** Sub's own ADR-0007 records that prepaid renewal "remains
materially monthly-specific" while postpaid supports five cycles. So
`test_billing_cadence.py` proves the *postpaid* cadence surface well and the
prepaid one narrowly. A subscriptions module that ported it unchanged would
inherit that asymmetry as coverage it does not have. **Flagged, not ported
blind.**

---

## 3. Payment allocation, refund and reversal — the highest-value proofs in the fleet

This is the block ADR-0020 § 5 and `billing-sources.md` § 5 both single out,
because these tests encode the reconciliation defects § 4 of the inventory lists.
Sub is the qualifying source by a wide margin; ERP contributes the allocation
edge cases its AR path handles differently.

### 3.1 Sub — settlement and allocation

| Test | LOC | Proves | Guards |
|---|---|---|---|
| `tests/test_payment_allocation_settlement_consequence.py` | 773 | The full chain: settlement accepted → allocation → derived coverage → owning-service consequence. | The plan's canonical money path, end to end. If one Sub test had to be ported, this is it. |
| `tests/test_payment_settlement_allocation_evidence.py` | 289 | Every allocation is represented by immutable evidence linked to the settlement that funded it. | An allocation with no traceable source — the condition that makes a receivable unreconcilable. |
| `tests/test_payment_webhook_settlement.py` | 954 | Settlement arriving via provider webhook, including replay and ordering. | **Ports with a boundary change.** The webhook half is Integrator work (ADR-0024 § 6); what ports to billing is the *settlement-acceptance* half — same assertions, provider-neutral input. Porting it whole would drag a webhook receiver into billing, which A3 forbids. |
| `tests/test_provider_payment_settlements.py` | 462 | `stage_verified_invoice_payment` — verified settlement staging. | **Only an independently confirmed settlement creates money.** This is the source proof for the `confirmation_evidence` acceptance policy. |
| `tests/test_payment_reallocation.py` | 293 | Deallocation and reallocation across invoices. | Reallocation losing or inventing a minor unit; `Money.allocate()` is the primitive that must not. |
| `tests/services/billing/test_payment_status_recompute.py` | 604 | Recomputation of payment/invoice status after money moves. | **Ports as a NEGATIVE proof.** In the module there is nothing to recompute — coverage is derived and `balance_due` is generated. This suite's scenarios become assertions that the derived value is already correct with no recompute step, which is the strongest possible evidence that the ADR-0016 correction landed. |
| `tests/architecture/test_payment_settlement_participants.py` | 194 | Only declared participants may take part in a settlement transaction, flush-only. | ADR-0007's "no independent participant commit" and hard rule 8 (`dotmac_kernel.db` is the one transaction authority). Directly relevant to the module's one-transaction preview/apply. |
| `tests/test_payment_update_settlement.py` | 150 | `Payments.update` refuses every field but `memo` once a settlement exists (`payments.py:3342-3348`). | **The settled-immutability guard that already exists in Sub.** See § 6 — it guards the service but not the schema. |
| `tests/test_payment_mark_status_guard.py` | 54 | Payment status cannot be marked directly. | A UI action creating money. |
| `tests/services/billing/test_consolidated_settlement_reconciliation.py` | 397 | Consolidated/bulk settlement reconciliation. | Bulk paths bypassing the single-settlement invariants. |

### 3.2 Sub — refund, reversal and correction

| Test | LOC | Proves | Guards |
|---|---|---|---|
| `tests/test_payment_reversal_evidence.py` | 434 | A reversal appends typed evidence linked to the fact it offsets. | Correction by edit. This is the source proof for "settled facts are immutable; corrections are reversal/refund/supersession". |
| `tests/test_payment_refund_evidence.py` | 266 | Refund evidence chain. | Same. |
| `tests/test_refund_money_correctness.py` | 265 | Refund amounts are exact and bounded by what was settled. | Refunding more than was received. |
| `tests/test_partial_refund_invoice_state.py` | 142 | A partial refund's effect on the invoice. | **Ports as a coverage assertion, not a status assertion** — under ADR-0016 the invoice's *lifecycle* does not move; only `amount_paid` and therefore derived coverage do. This is one of the clearest places the port changes the assertion while keeping the scenario. |
| `tests/test_refund_guards.py` | 97 | Refusals on the refund path. | Refund of an unsettled or already-refunded payment. |
| `tests/test_ledger_reversal_integrity.py` | 194 | One active reversal chain per posting group; reversal does not corrupt the ledger. | ADR-0007 § 4's "one active reversal chain" invariant. |
| `tests/test_payment_import_batch_reversal.py` | 411 | Batch reversal of imported money. | A bulk reversal path with weaker invariants than the single one. |
| `tests/test_opening_settlement_correction.py` | 455 | Correction of an opening settlement. | Historical-data correction rewriting rather than appending. |

### 3.3 Sub — posting groups and per-currency positions

| Test | LOC | Proves | Guards |
|---|---|---|---|
| `tests/test_customer_subledger.py` | 381 | Posting-group and position-effect semantics. Includes `:163 test_positions_are_per_currency_and_semantic_lane`. | **C9.** This is the direct source proof that receivable, credit and prepaid funding are separate lanes per currency — the corrected shape for the `current_balance = balance_due + available_credit` defect. |
| `tests/test_customer_financial_position.py` | 277 | The typed per-currency position. Includes `:213 test_native_signed_balance_is_currency_typed_and_fail_closed`. | A cross-currency total, and a position that guesses instead of failing closed. |
| `tests/test_subledger_opening_positions.py` | 751 | Immutable opening posting groups. | Backfilled history becoming mutable. Directly relevant to the cutover plan's S2 backfill. |
| `tests/test_subledger_forward_shadow.py` | 469 | Forward shadow of subledger positions against the legacy path. | **This is a working example of the shadow harness the module's own cutover needs** — Sub has already built and proven this pattern once, which is a strong argument for porting the harness shape and not only the assertions. |
| `tests/test_customer_financial_ledger.py` | 702 | The ledger read model. | Read-model drift from posting groups. |
| `tests/test_billing_money_bounds.py` | 112 | Money bounds. | Overflow and precision at the extremes. |
| `tests/test_billing_alignment_audit.py` | 1,580 | Broad alignment audit; includes `:324 test_batch_position_preserves_per_currency_balances`. | Batch operations collapsing currencies. |

### 3.4 ERP — allocation edge cases Sub does not have

| Test | LOC | Proves | Guards |
|---|---|---|---|
| `dotmac_erp:tests/finance/test_advance_allocation.py` | 277 | Advance (prepayment) allocation against later invoices. | The `DUST_THRESHOLD` divergence ADR-0016 names: `ar/advance_allocation.py` held a `Decimal("0.01")` tolerance *and* an exact `>=` for the status a few lines away. **Port this test specifically to prove the module cannot reproduce that pair.** |
| `dotmac_erp:tests/services/test_exact_match_allocation.py` | 215 | Exact-match automatic allocation. | An automatic allocator with a different fully-paid rule from the manual one. |
| `dotmac_erp:tests/ifrs/ar/test_customer_payment_service.py` | 417 | AR customer payment apply/reverse. | The `apply` and `reverse` paths in ADR-0016's twelve-site table — two of the seven divergent rules live here. |
| `dotmac_erp:tests/ifrs/ar/test_payment_status.py` | 167 | AR's single coverage owner. | Same; ports alongside `coverage.py`. |
| `dotmac_erp:tests/ifrs/ap/test_payment_status.py` | 142 | AP's single coverage owner. | **Ports as a symmetry proof**, not because billing does AP: it demonstrates the owner pattern works for a second document family, which is what makes the shared mixin credible. |

---

## 4. Invoicing, credit notes, tax and settlement/webhook handling

### 4.1 Invoice and credit-note lifecycle — Sub

| Test | LOC | Proves | Guards |
|---|---|---|---|
| `tests/services/billing/test_invoice_lifecycle_owner.py` | 208 | One owner for invoice transitions. | Scattered status writers. |
| `tests/services/billing/test_invoice_construction_owner.py` | 136 | One owner for invoice construction. | Two ways to build an invoice with different defaults. |
| `tests/test_invoice_transition_guards.py` | 92 | Illegal transitions refused. | An issued invoice returning to draft. |
| `tests/test_invoice_issued_at_invariant.py` | 107 | `issued_at` is set once and not rewritten. | **Directly ports to "issuance freezes the snapshot"** — the smallest, sharpest proof of the immutable-issuance rule. |
| `tests/test_invoice_recalc_status.py` | 54 | Status recalculation. | **Ports as a negative proof** — see § 3.1's note on `test_payment_status_recompute.py`. In the module there is no status to recalculate. |
| `tests/test_invoice_written_off.py` | 89 | Write-off as a lifecycle fact. | The one lifecycle state beyond `draft`/`issued`/`void` that is genuinely a decision rather than arithmetic. Confirms `written_off` survives the ADR-0016 contraction while `paid`/`partially_paid` do not. |
| `tests/test_invoice_closure_evidence.py` | 389 | Closure carries evidence. | A closed invoice with no reason. |
| `tests/test_invoice_draft_authoring.py` | 539 | Draft mutability before issuance. | The draft/issued boundary — "a draft may change; issuance freezes". |
| `tests/test_credit_notes.py` | 748 | Credit-note issue, apply, void. | The accepted rule `subtotal = total`, `tax_total = 0`, no tax lines. |
| `tests/test_credit_note_apply_on_issue.py` | 600 | Apply-on-issue semantics. | Credit applied twice. |
| `tests/integration/test_credit_note_issue_concurrency.py` | 186 | Concurrent credit-note issuance. | Two concurrent issuances producing one number or two credits. |
| `tests/test_invoice_read_negative_lines.py` | 271 | Negative lines. | Sign handling in totals. |
| `tests/test_invoice_discounts.py` | 369 | Discount history. | A discount recomputed at read time rather than snapshotted. |

### 4.2 Tax — Sub for behaviour, ERP for structure

| Test | LOC | Proves | Guards |
|---|---|---|---|
| `dotmac_sub:tests/test_tax_accounting.py` | 488 | Tax accounting behaviour on invoices. | ISP-shaped tax behaviour. **Ports as scenarios, with the Nigerian specifics moved to a product-supplied fake** — ADR-0020 is explicit that neither the kernel nor shared billing may acquire Nigerian VAT as a default. |
| `dotmac_sub:tests/architecture/test_tax_accounting_ownership.py` | 191 | One owner for tax accounting. | Tax computed in two places. |
| `dotmac_sub:tests/integration/test_tax_accounting_concurrency.py` | 111 | Concurrent tax accounting. | Race on the applied-tax snapshot. |
| `dotmac_erp:tests/ifrs/tax/test_tax_calculation_service.py` | 121 | Rate resolution and calculation against ERP's 10-model structure. | The structural half — inclusive/exclusive/exempt, jurisdiction, fiscal position. **This is the `TaxProvider` port's contract-suite seed.** |
| `dotmac_erp:tests/finance/test_cash_basis_vat.py` | — | Cash-basis VAT recognition from allocated cash. | **Does NOT port.** It is the boundary marker: statutory recognition is ERP's, and billing emits the allocation detail ERP needs. Listed so nobody ports it by association. |

### 4.3 Settlement and provider-event handling — Sub, with a boundary split

Everything in this block ports **split**: the transport half to
`dotmac-integration`'s connector work, the meaning half to billing.

| Test | LOC | Proves | Guards | Ports to |
|---|---|---|---|---|
| `tests/test_payment_provider_events.py` | 296 | Provider event → consequence. | The observation/decision boundary. | **Split** — observation to Integrator, consequence to billing |
| `tests/architecture/test_payment_provider_event_ownership.py` | 110 | One owner for provider-event consequences. | Two writers reacting to the same event. | Billing (as the settlement-acceptance owner) |
| `tests/architecture/test_payment_webhook_ownership.py` | 119 | One owner for webhook handling. | Scattered webhook handlers. | **Integrator** |
| `tests/integration/test_payment_provider_event_concurrency.py` | 89 | Concurrent provider events. | Double-processing one event. | **Split** — dedupe to Integrator, at-most-once acceptance to billing (`dotmac_kernel.idempotency`) |
| `tests/test_api_billing_webhooks.py` | 167 | The webhook API surface. | — | **Integrator** — billing ships no route at all |
| `tests/architecture/test_payment_gateway_control_plane.py` | 110 | Gateway configuration is control-plane. | Gateway config leaking into request paths. | **Integrator** |
| `tests/test_reconcile_webhook.py` | 195 | Webhook reconciliation. | — | **Integrator** |

**The split is the point.** Porting this block whole would put a webhook receiver
in `dotmac-billing`, which ADR-0020 A3 and ADR-0024 § 6 forbid — and the
architecture test in the authority-profile spec § 3 would then fail the module it
was shipped with. Listing them with an explicit destination is how that mistake
is avoided rather than discovered.

### 4.4 Vendor CP — exact money and immutable publish

| Test | LOC | Proves | Guards |
|---|---|---|---|
| `tests/unit/test_offers.py` | 87 | An offer version is write-once; re-publish raises `ConflictError`. | "A version is never edited; a change is a new version." The **mandatory port delta** ADR-0020 A4 names for subscriptions, and the same discipline billing applies to issued documents. |
| `tests/unit/test_contracts.py` | 242 | Contract lifecycle; frozen `unit_amount` price snapshot; `content_hash` binding approvals to an exact priced snapshot. | A price that moves under an approved contract. `:19,194` additionally assert `TenantEntitlementGrant` count stays **zero** — a negative test proving the control plane never writes product grants. |
| `tests/migration/test_replay_concurrency.py` | 217 | Replay and concurrency over the vendor lineage. | The concurrency shape the platform plane must survive. |

---

## 5. Where NO adequate source test exists

These are the gaps. Saying so is more useful than lengthening § 1–4, and each one
is a test the module must write **fresh**, with no parity baseline to fall back
on.

| # | Area | Evidence of absence | Consequence |
|---|---|---|---|
| **G1** | **Invoice numbering** | Sub's `next_invoice_number` is defined at `app/services/billing/invoices.py:303` and referenced only from production code (`invoices.py` 706/1519/2123, `billing_automation.py` 1902/2277, `crm_api.py` 1462/1481). **Zero occurrences anywhere under `tests/`.** No uniqueness, no gap policy, no concurrency, no period-reset test. ERP has five numbering implementations covered only by two generic suites (`dotmac_erp:tests/ifrs/common/test_numbering_service.py` 713, `dotmac_erp:tests/ifrs/platform/test_sequence_service.py` 316) with **no invoice-specific test** — no file under ERP's `tests/` matches `*invoice*number*`. | **The most serious gap, and it is on the critical path.** P4 is a required prerequisite for internal issuance, and its product-first source has no behavioural proof. Fresh tests needed: per-plane uniqueness (composite on tenant, control-plane-wide on platform), declared gapless-or-not, period reset, concurrent double-issue produces one number, replayed issuance returns the same number. |
| **G2** | **FX snapshot immutability** | `grep -rl 'fx_rate\|fx_snapshot\|exchange_rate\|forex'` over `dotmac_sub` returns **nothing** under `app/` or `tests/` — Sub has no FX concept at all; multi-currency is handled by currency-scoped separation only. ERP has the structure (`dotmac_erp:app/models/finance/core_fx/` four models, `dotmac_erp:app/services/finance/platform/fx.py` 593, tests `dotmac_erp:tests/ifrs/platform/test_fx_service.py` 562, `dotmac_erp:tests/ifrs/gl/test_fx_revaluation_service.py` 1,640) but ERP's tests are about **GL revaluation**, not about an immutable applied-rate snapshot on an invoice. | Fresh tests needed: an applied FX observation is frozen at rating time; a later rate change does not alter a historical document; a snapshot replays a valuation without consulting current market data; cross-currency allocation is refused. **This is a design-and-test area, not a port.** ERP's `test_fx_service.py` seeds the `FxProvider` port's shape but proves a different behaviour. |
| **G3** | **Two commercial authorities refused at boot** | Nothing in any source repository implements a commercial-authority binding, so there is no test of it anywhere. Vendor CP has no invoicing at all; Sub has exactly one implicit authority (its own writer); ERP has one (its own AR). | Fresh tests needed — the whole § 1.4 table of the authority-profile spec, including the two sensitivity proofs. **A guard with no source precedent is exactly the kind that ships passing vacuously**, which is why the sensitivity proofs are specified before the guard. |
| **G4** | **Dual-plane money persistence** | `dotmac-ticketing` and `dotmac-files` prove the dual-plane *mechanism* (`dotmac_starter_mt:tests/architecture/test_ticketing_module.py`, `dotmac_starter_mt:tests/unit/test_live_catalog_contract.py`), but no money-domain module exists on either plane. Vendor CP has no `tenant_id` and no RLS anywhere; Sub has no platform plane. | Fresh tests needed: RLS FORCEd on tenant invoice/settlement/posting tables with a cross-tenant canary; `REVOKE ALL` from the tenant app role across all seven privileges and their column forms on every platform table; online platform role reachable (schema `USAGE` + row DML); no FK crossing planes; both invoice-number uniqueness shapes. The mechanism ports; the money-domain instance is new. |
| **G5** | **Accounting-fact emission and idempotent ERP receipt** | Sub has an ERP boundary (`app/services/sot_registry/domains/financial_access/erp_billing.py`, 167 LOC) and ERP has a Sub sync (`app/services/dotmac_sub/`, with `dotmac_sub_sync_watermark.py`), so a cross-app path exists — but it is the ERP↔Sub *sync* the fleet matrix explicitly says has "known money-correctness defects" and must become a versioned contract with drift detection. It is not a parity baseline for `AccountingFactV1`. | Fresh tests needed: a fact is emitted only after billing's own commit; ERP consumes it idempotently and a full replay is a no-op; an unmapped effect is an ERP exception, not a billing retry or a fallback journal; no synchronous cross-database transaction. **Porting the existing sync's tests would port the defect.** |

| **G6** | **The official-artifact relation, and its reconciler** | Sub has one nearby suite — `dotmac_sub:tests/test_billing_invoice_pdf_storage.py` (517 LOC) — but it proves a **render-job/cache** model, not an official-artifact relation, and the model it proves is the one the extraction rejects. Three verified defects in the code it covers: `_is_export_fresh` (`app/services/billing_invoice_pdf.py:945`) invalidates the stored PDF whenever `invoice.updated_at` moves; `INVOICE_PDF_TEMPLATE_REFRESHED_AT = datetime(2026, 3, 18, …)` (`:51`) invalidates every artifact rendered before a hand-edited constant; and `maybe_finalize_stalled_export` (`:1246`) plus `STALE_EXPORT_SECONDS = 20` (`:46`) work around a `processing` row that nothing finishes — the exact second-at-most-once-owner failure ADR-0014 predicts. So the stored artifact tracks the **current invoice row**, not the issued document. | **Requirement input only — do not port.** Fresh tests needed: one current artifact per `(fact_id, media_type)` under a partial unique index; a repair with a new `file_id` and a declared supersession reason becomes current; a re-render with a differing `presentation_model_digest` is refused as `ArtifactContentMismatch`; an artifact for a superseded `fact_version` is recorded but never current; zero artifact rows is legal and no billing decision reads the relation (with the planted-read sensitivity proof); and the **event-suppressed reconciler canary** — disable `invoice.issued` delivery entirely, run the reconciler, assert convergence, and prove the canary fails when the work queue is forced empty. |

**One non-gap worth recording, because two earlier guesses were wrong.** Both
per-currency position separation and obligation natural-identity uniqueness were
initially assumed untested in Sub. **Both have real tests** —
`tests/test_customer_subledger.py:163`, `tests/test_customer_financial_position.py:213`,
`tests/test_billing_obligations.py:165` and `:221`. Assuming a gap where a proof
exists is how a port turns into a rewrite; both are in § 2 and § 3.3 as ports.

---

## 6. One guard that must get *stronger* in the port

`dotmac_sub:tests/test_payment_update_settlement.py` (150 LOC) proves that
`Payments.update` refuses every field but `memo` once a settlement exists. It
passes today, and the service guard is real
(`app/services/billing/payments.py:3342-3348`).

It is not sufficient, and the port must say so: **the API contract still exposes
the field.** `app/schemas/billing.py:715` declares
`amount: Decimal | None = Field(default=None, gt=0, lt=10000000000)` on
`PaymentUpdate`, so an amount edit is accepted by the schema and rejected only at
runtime, and only when a settlement row happens to exist — an **unsettled**
payment's amount is freely editable.

In the module the guard is structural: **no update command names a money field
on any payment state**, so there is no runtime check to get wrong. The ported
test therefore changes shape from "the service refuses this call" to "this call
does not exist", and gains a schema-level assertion that no update contract
exposes a money field on a settled fact. That assertion is also a row in the R4
retirement ratchet (`billing-extraction-dossier.md` § 3).

This is the general pattern for several ports in § 3 and § 4.1: **the scenario
survives, the assertion inverts.** Where the source proves a runtime refusal, the
module proves the absence of the path.

---

## 7. Summary

| Area | Source | Ports | Fresh tests needed |
|---|---|---|---|
| Coverage | **ERP** | 6 files, incl. a parity test with its own sensitivity proof | none |
| Rating / obligations | **Sub** | 3 files (cadence goes to subscriptions) | none |
| Allocation / settlement / refund / reversal | **Sub** + ERP edges | ~18 files — the largest and highest-value block | none |
| Positions / posting groups | **Sub** | 7 files, incl. a working shadow harness | none |
| Invoice / credit-note lifecycle | **Sub** | 13 files | none |
| Tax | Sub behaviour + **ERP** structure | 4 files, with Nigerian specifics moved to a fake | none |
| Settlement / provider events | **Sub**, split | ~3 to billing, ~4 to the Integrator | none |
| Exact money / immutable publish | **Vendor CP** | 3 files | none |
| **Invoice numbering** | — | **nothing** | **G1 — critical path** |
| **FX snapshots** | — | ERP seeds the port shape only | **G2** |
| **Authority binding** | — | **nothing** | **G3** |
| **Dual-plane money persistence** | mechanism only | **nothing money-domain** | **G4** |
| **Accounting facts** | — | **nothing usable** | **G5** |
| **Official-artifact relation + reconciler** | Sub, requirement input only | **nothing** — the one nearby suite proves the model being rejected | **G6** |
