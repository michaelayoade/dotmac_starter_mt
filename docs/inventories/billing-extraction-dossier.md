# `dotmac-billing` — extraction dossier content

**As of:** 2026-08-14
**starter:** working tree on `docs/whatsapp-connector-extraction-dossier`
(`b55c9a5`), carrying the uncommitted ADR-0020 2026-08-14 amendment
**Sub:** `/Users/michaelayoade/Downloads/management/dotmac_sub`, HEAD at time of
audit
**ERP:** `/Users/michaelayoade/Downloads/management/dotmac_erp`, HEAD at time of
audit
**vendor CP:** `/Users/michaelayoade/Downloads/management/dotmac_vendor_control_plane`,
HEAD `8984801`
**Decision:** ADR-0020 + its 2026-08-14 amendment (A1–A6), under ADR-0023,
ADR-0024, ADR-0022, ADR-0016, ADR-0014
**Execution plan:** `docs/superpowers/plans/2026-08-14-billing-vendor-cp-sub-cutover.md`
**Companion specs:** `docs/superpowers/specs/2026-08-14-billing-authority-profile-contract.md`,
`docs/inventories/billing-parity-tests.md`
**Evidence base:** `docs/inventories/billing-sources.md`

---

## Current disposition — 2026-10-13

This is the historical 2026-08-14 source audit. The package now exists;
[`packages/dotmac-billing/EXTRACTION.toml`](../../packages/dotmac-billing/EXTRACTION.toml)
is its maintained dossier. Source coordinates and findings below describe that
audit unless explicitly dated otherwise; they are not current production facts.
The original P11/Stage E implementation hold is historical, not today's next action.

Michael's 2026-10-13 rulings select **Sub first** (M1, Vendor-first waived),
**prepaid pay-then-record** (M6: obligation at confirmed funding with the final
service period; wallet = `available_credit`; advance drafts become notices),
and **open items plus opening lanes only** (M8). Closed history stays in Sub's
read-only archive. Compose clean Starter-owned modules inside Sub and lift proven
Sub/ERP logic behind contracts; exclude metadata identity, repair and
reconciliation machinery. The no-mutations freeze remains in force. Production
activation, financial data changes and writer retirement require their separate
authority and evidence.

The maintained package uses `source_mode = "greenfield-after-inventory"`:
[billing-source-variance.md](billing-source-variance.md) records why the older
product-first owner claim could not stand. This describes the clean shared core;
it does not permit rewriting proven working logic selected for extraction.

Billing's historical publication is recorded in
[module-release-legacy-unverified.json](module-release-legacy-unverified.json):
`dotmac-billing-v0.1.0a1`, tag object
`3dbb4e171903d954d58cbc90a28069d976e9df1e`, peeled commit
`92a1626b16d7e068f92536d8cfcb2ef9b6f270c2`. The newer
[module-release-verifications.json](module-release-verifications.json) has no
Billing receipt. A historical publication record proves neither modern wheel
verification nor pinnable artifact evidence, adoption, production authority transfer
or local-writer retirement.
See [Sub cutover readiness](billing-sub-cutover-readiness.md) for the current gaps.

Every path in the TOML block below was confirmed to exist by `ls`/`wc -l` during
this audit. Where `billing-sources.md` cites a path that turned out to be stale
or wrong, § 5 records the correction and this dossier uses the verified path.

---

## 1. Historical dossier content (current sequencing fields amended 2026-10-13)

```toml
schema_version = 1
package = "dotmac-billing"
classification = "optional-module"
status = "audit-complete"
source_mode = "greenfield-after-inventory"
owner = "Operational receivables on explicit tenant and platform planes: rated-obligation acceptance, invoice and credit-note lifecycle, confirmed settlement facts, allocation and reversal, and separately derived per-currency positions"
contract = "Accept an immutable pre-tax rated obligation under a database-enforced natural identity; issue and correct invoices and credit notes with frozen price/tax/FX snapshots; accept only independently confirmed, provider-neutral settlement observations exactly once; allocate, deallocate, reallocate, reverse and refund as immutable posting groups; derive collectible receivable, available customer credit and prepaid funding SEPARATELY per currency; derive payment coverage from amounts through one owner; publish versioned receivable, accounting and invoice-document facts; and record which stored object is the OFFICIAL artifact of a document fact version, as an opaque handle plus render provenance, written only by the assembly's reconciler through a typed command. NOT commercial contract lifecycle, NOT dunning policy or collections consequence, NOT general ledger / chart of accounts / journals / fiscal periods / statutory accounting or tax returns, NOT PSP clients / provider credentials / webhook signature verification, NOT document rendering, NOT byte storage, and NOT product access or entitlement mutation."
source_repositories = [
  "dotmac_sub",
  "dotmac_erp",
  "dotmac_vendor_control_plane",
]
source_paths = [
  # --- Sub: the qualifying source for the financial core (product-first) ---
  "dotmac_sub:app/services/billing/obligations.py",
  "dotmac_sub:app/services/billing/rating.py",
  "dotmac_sub:app/services/billing/cadence.py",
  "dotmac_sub:app/services/billing/invoices.py",
  "dotmac_sub:app/services/billing/credit_notes.py",
  "dotmac_sub:app/services/billing/payments.py",
  "dotmac_sub:app/services/billing/customer_subledger.py",
  "dotmac_sub:app/services/billing/subledger_opening.py",
  "dotmac_sub:app/services/billing/ledger.py",
  "dotmac_sub:app/services/billing/account_credit.py",
  "dotmac_sub:app/services/billing/adjustments.py",
  "dotmac_sub:app/services/billing/tax.py",
  "dotmac_sub:app/services/billing/_common.py",
  "dotmac_sub:app/services/billing/reconcile_unposted.py",
  "dotmac_sub:app/services/billing/shadow_verification.py",
  "dotmac_sub:app/services/provider_payment_settlements.py",
  "dotmac_sub:app/services/customer_financial_position.py",
  "dotmac_sub:app/services/customer_financial_ledger.py",
  "dotmac_sub:app/services/invoice_collectibility.py",
  "dotmac_sub:app/services/invoice_discounts.py",
  "dotmac_sub:app/services/tax_accounting.py",
  "dotmac_sub:app/services/customer_tax_policies.py",
  "dotmac_sub:app/models/billing.py",
  "dotmac_sub:app/models/billing_contract.py",
  "dotmac_sub:app/models/customer_subledger.py",
  "dotmac_sub:app/models/customer_tax_policy.py",
  "dotmac_sub:docs/adr/0007-end-to-end-billing-target-architecture.md",
  "dotmac_sub:docs/designs/BILLING_ACCOUNT_360.md",
  # --- ERP: the qualifying source for coverage and tax/FX structure ---
  "dotmac_erp:app/services/finance/coverage.py",
  "dotmac_erp:app/services/finance/ar/payment_status.py",
  "dotmac_erp:app/services/finance/ap/payment_status.py",
  "dotmac_erp:app/services/finance/ar/advance_allocation.py",
  "dotmac_erp:app/services/finance/ar/exact_match_allocation.py",
  "dotmac_erp:app/models/finance/ar/invoice.py",
  "dotmac_erp:app/models/finance/ar/invoice_line.py",
  "dotmac_erp:app/models/finance/ar/invoice_line_tax.py",
  "dotmac_erp:app/models/finance/ar/customer_payment.py",
  "dotmac_erp:app/models/finance/ar/payment_allocation.py",
  "dotmac_erp:app/models/finance/tax/tax_jurisdiction.py",
  "dotmac_erp:app/models/finance/tax/tax_code.py",
  "dotmac_erp:app/models/finance/tax/fiscal_position.py",
  "dotmac_erp:app/models/finance/tax/tax_transaction.py",
  "dotmac_erp:app/models/finance/core_fx/currency.py",
  "dotmac_erp:app/models/finance/core_fx/exchange_rate.py",
  "dotmac_erp:app/models/finance/core_fx/exchange_rate_type.py",
  "dotmac_erp:app/services/finance/platform/fx.py",
  "dotmac_erp:app/services/finance/money_boundary.py",
  # --- Vendor CP: historical candidate shape (platform plane, exact money) ---
  "dotmac_vendor_control_plane:src/vendor_cp/offers/models.py",
  "dotmac_vendor_control_plane:src/vendor_cp/contracts/models.py",
  "dotmac_vendor_control_plane:src/vendor_cp/contracts/service.py",
  "dotmac_vendor_control_plane:src/vendor_cp/accounts/models.py",
]
preserved_tests = [
  # --- ERP: coverage is the highest-value single proof in the fleet ---
  "dotmac_erp:tests/integration/test_coverage_parity.py",
  "dotmac_erp:tests/architecture/test_paid_status_single_owner.py",
  "dotmac_erp:tests/architecture/test_coverage_is_not_a_lifecycle_status.py",
  "dotmac_erp:tests/architecture/test_monetary_documents_carry_coverage.py",
  "dotmac_erp:tests/unit/test_payment_coverage.py",
  "dotmac_erp:tests/finance/test_advance_allocation.py",
  "dotmac_erp:tests/services/test_exact_match_allocation.py",
  "dotmac_erp:tests/ifrs/ar/test_payment_status.py",
  "dotmac_erp:tests/ifrs/ap/test_payment_status.py",
  "dotmac_erp:tests/ifrs/ar/test_customer_payment_service.py",
  "dotmac_erp:tests/ifrs/ar/test_invoice_service.py",
  "dotmac_erp:tests/ifrs/tax/test_tax_calculation_service.py",
  "dotmac_erp:tests/ifrs/platform/test_fx_service.py",
  "dotmac_erp:tests/finance/test_money_boundary.py",
  # --- Sub: allocation, settlement, refund and reversal — the money core ---
  "dotmac_sub:tests/test_billing_obligations.py",
  "dotmac_sub:tests/test_billing_rating.py",
  "dotmac_sub:tests/test_billing_cadence.py",
  "dotmac_sub:tests/architecture/test_billing_target_architecture.py",
  "dotmac_sub:tests/test_payment_allocation_settlement_consequence.py",
  "dotmac_sub:tests/test_payment_settlement_allocation_evidence.py",
  "dotmac_sub:tests/test_payment_reallocation.py",
  "dotmac_sub:tests/test_payment_reversal_evidence.py",
  "dotmac_sub:tests/test_payment_refund_evidence.py",
  "dotmac_sub:tests/test_refund_money_correctness.py",
  "dotmac_sub:tests/test_partial_refund_invoice_state.py",
  "dotmac_sub:tests/test_refund_guards.py",
  "dotmac_sub:tests/test_ledger_reversal_integrity.py",
  "dotmac_sub:tests/test_payment_import_batch_reversal.py",
  "dotmac_sub:tests/test_provider_payment_settlements.py",
  "dotmac_sub:tests/test_opening_settlement_correction.py",
  "dotmac_sub:tests/test_payment_update_settlement.py",
  "dotmac_sub:tests/test_payment_mark_status_guard.py",
  "dotmac_sub:tests/services/billing/test_payment_status_recompute.py",
  "dotmac_sub:tests/architecture/test_payment_settlement_participants.py",
  "dotmac_sub:tests/test_customer_subledger.py",
  "dotmac_sub:tests/test_subledger_opening_positions.py",
  "dotmac_sub:tests/test_subledger_forward_shadow.py",
  "dotmac_sub:tests/test_customer_financial_position.py",
  "dotmac_sub:tests/test_customer_financial_ledger.py",
  "dotmac_sub:tests/test_billing_money_bounds.py",
  "dotmac_sub:tests/test_credit_notes.py",
  "dotmac_sub:tests/test_credit_note_apply_on_issue.py",
  "dotmac_sub:tests/integration/test_credit_note_issue_concurrency.py",
  "dotmac_sub:tests/test_invoice_issued_at_invariant.py",
  "dotmac_sub:tests/test_invoice_transition_guards.py",
  "dotmac_sub:tests/test_invoice_recalc_status.py",
  "dotmac_sub:tests/test_invoice_written_off.py",
  "dotmac_sub:tests/test_invoice_closure_evidence.py",
  "dotmac_sub:tests/services/billing/test_invoice_lifecycle_owner.py",
  "dotmac_sub:tests/services/billing/test_invoice_construction_owner.py",
  "dotmac_sub:tests/test_tax_accounting.py",
  "dotmac_sub:tests/architecture/test_tax_accounting_ownership.py",
  "dotmac_sub:tests/integration/test_tax_accounting_concurrency.py",
  "dotmac_sub:tests/test_payment_provider_events.py",
  "dotmac_sub:tests/architecture/test_payment_provider_event_ownership.py",
  "dotmac_sub:tests/architecture/test_payment_webhook_ownership.py",
  "dotmac_sub:tests/integration/test_payment_provider_event_concurrency.py",
  # --- Vendor CP: exact money and immutable publish ---
  "dotmac_vendor_control_plane:tests/unit/test_offers.py",
  "dotmac_vendor_control_plane:tests/unit/test_contracts.py",
  "dotmac_vendor_control_plane:tests/migration/test_replay_concurrency.py",
]
contract_consumers = []
candidate_consumers = ["dotmac_vendor_control_plane", "dotmac_sub"]
composition_boundary = "ADR-0024: each adopter installs its own billing lineage, owns its own financial rows and binds its own commercial authority, tax/FX/numbering adapters and settlement input. Applications share the package contract, never a mod_<billing> schema, an invoice series or a receivable position. Billing imports neither dotmac-subscriptions nor dotmac-collections nor dotmac-files nor a consuming assembly; every cross-module outcome travels a versioned contract the assembly wires (obligation command in, settlement command in, receivable/accounting/document facts out). Provider identity, credentials, webhook verification, retries and checkpoints are Integrator connector-plugin concerns and appear nowhere in this package. ERP remains sole general-ledger and statutory-accounting authority and consumes immutable accounting facts idempotently."
inventory_evidence = [
  "docs/inventories/billing-sources.md",
  "docs/inventories/billing-extraction-dossier.md",
  "docs/inventories/billing-parity-tests.md",
  "docs/adr/0020-billing-owns-operational-receivables.md",
  "docs/adr/0023-dual-plane-modules-declare-both-persistence-planes.md",
  "docs/adr/0024-apps-compose-by-synchronizing-data.md",
  "docs/superpowers/specs/2026-08-14-billing-authority-profile-contract.md",
  "docs/superpowers/plans/2026-08-14-billing-vendor-cp-sub-cutover.md",
]
first_cutover = "dotmac_sub is cutover 1 on the tenant plane, per Michael’s 2026-10-13 M1 ruling; Vendor-first ordering is waived. Compose clean Starter-owned modules locally inside Sub, incrementally behind published contracts, with isolated shadow comparison and one coupled invoice/settlement/allocation watermark per accepted slice. M6: prepaid is pay-then-record, no financial document before funding; advance drafts become notices; create the obligation at funding with its final service period; wallet means available_credit and renewal draws from it. M8: migrate open items plus separately reconciled opening lanes only; closed history remains a provenance-labelled read-only Sub archive. Vendor CP remains a later platform-plane candidate with no sentinel tenant. Historical source/revalidation revisions above remain audit coordinates, not current deployment evidence. Production activation and authority transfer remain separately authorized."
shadow_and_drift = "Vendor CP is greenfield, so there is no old invoice owner to shadow. Its substitutes are the cutover plan's V1 preview (one real contract's invoice previewed without persisting or numbering, finance-verified), the full same-currency scenario matrix against fakes, a proven platform-role/tenant-role privilege split, one real production flow, an exact per-account/per-currency position rebuild hash, and an idempotent ERP accounting-fact receipt. Sub runs a PURE behaviour shadow: the module's engine executes against captured immutable source inputs in an isolated shadow database unreachable from product routes, the legacy Sub writer remains sole authority, and financial commands are never dual-written. Comparison is exact and at source-identity level over documents, settlements, allocations, positions and accounting facts, with NO money tolerance; every mismatch is classified as source defect, known intentional correction, missing evidence, contract defect or shadow bug, and an unclassified difference blocks cutover. Acceptance requires all active cadences and settlement paths exercised plus three consecutive complete reconciliations with zero unclassified drift. The six known non-conformances (see this dossier's section 4) each carry a named shadow measurement because several change customer-visible numbers, and customer-debit, over-credit, tax and access-impacting deltas need explicit Finance/product acceptance before the switch. Post-cutover drift detection is a replay reconciler that rebuilds every per-currency position from immutable posting groups and hash-compares; under a provider_owned authority the projection additionally carries a source label and a drift report a collections ladder may refuse to act on when stale."
local_copy_retirement = "Vendor CP has no local financial writer to retire; its obligation is to add one only through the module, never a second generic invoice facade. Sub retires its estate slice by slice under an ADR-0018 two-directional ratchet, and a slice is DONE ONLY WHEN THE LOCAL WRITER IS DELETED, not when the module can also do the work. The ratcheted slices are: (1) invoice and credit-note lifecycle plus invoice numbering — app/services/billing/invoices.py including next_invoice_number, app/services/billing/credit_notes.py, app/services/invoice_draft_authoring.py, app/services/advance_renewal_invoicing.py; (2) obligation creation/resolution — app/services/billing/obligations.py and the recurring-run dedupe path in app/services/billing_automation.py; (3) rating and tax application — app/services/billing/rating.py, app/services/billing/tax.py, app/services/tax_accounting.py's invoice-side decisions (statutory recognition stays ERP's); (4) settlement, allocation, refund and reversal — app/services/billing/payments.py, app/services/provider_payment_settlements.py, app/services/financial_import_batch_reversals.py, plus the PaymentUpdate.amount field in app/schemas/billing.py; (5) posting groups and per-currency positions — app/services/billing/customer_subledger.py, app/services/billing/subledger_opening.py, app/services/billing/ledger.py, app/services/customer_financial_position.py, app/services/customer_financial_ledger.py; (6) credit and adjustment writers — app/services/billing/account_credit.py, app/services/billing/adjustments.py, app/services/account_credit_deposits.py; (7) the derived-balance read surfaces — app/services/web_subscriber_details.py's current_balance computation and app/services/billing/reporting.py; (8) the invoice-document artifact relation — app/models/billing.py:856 InvoicePdfExport (table invoice_pdf_exports), app/services/billing_invoice_pdf.py and app/services/invoice_bank_details.py, which split three ways: the relation half to billing's document_artifacts, the rendering half to the Documents workstream, and the queued/processing/completed/failed job-state half to NOTHING, because it is a second at-most-once mechanism ADR-0014 forbids and it already shows the predicted stalled-processing failure. No replacement job table is created on any side. Each ratchet counts legacy model/service imports, direct invoice/payment/allocation/balance assignments, provider callbacks that mutate money, and jobs that bypass the module; each ships a sensitivity proof; and every removal lowers the baseline in the same change. Historical archives remain read-only and provenance-labelled and are never a fallback writer. Retaining tables for statutory retention does not retain their authority. ERP retires nothing to billing: its coverage owner app/services/finance/coverage.py retires to the KERNEL coverage slice (plan Stage B), which is a separate, independently adoptable cutover."
next_action = "Implement the Sub-first extraction map and bounded contract gaps recorded in docs/inventories/billing-sub-cutover-readiness.md: lift proven Sub/ERP logic behind complete owner contracts, retain parity scenarios, and exclude metadata identity, repair and drift-reconciliation imports. Preserve the clean-core greenfield-after-inventory classification and historical coordinates; it does not authorize rewriting qualifying working logic. Billing 0.1.0a1 already has a historical publication record (module-release-legacy-unverified.json, tag dotmac-billing-v0.1.0a1, peeled commit 92a1626b16d7e068f92536d8cfcb2ef9b6f270c2); this is not a modern wheel-verification receipt or proof of adoption. Successor release/version decisions remain separate. No production mutation, backfill execution, deployment, authority switch or legacy deletion is authorized by this dossier; the no-mutations freeze remains in force."
```

---

## 2. Dual-plane design (ADR-0023)

### 2.1 One persistence-free behaviour engine

The financial behaviour — obligation acceptance and resolution, invoice
lifecycle, coverage derivation, settlement acceptance, allocation preview/apply,
reversal, refund, position derivation — is **one engine that imports no
persistence**. If it imports a model or a session, the "one behaviour" claim is
false and the vendor control plane cannot reuse the guards on its plane
(ADR-0023 § 1). The engine takes typed commands and repository protocols and
returns typed results and effects; the plane-specific repositories do the I/O.

Sub already demonstrates the shape this must beat: its posting participants are
**flush-only** (`app/services/billing/customer_subledger.py`) with
`dotmac_kernel.db` as the one transaction authority (hard rule 8), and ADR-0007
§ 4 requires "no independent participant commit". That property ports; the
model-coupling around it does not.

### 2.2 The two planes

| | tenant plane (`tables`) | platform plane (`platform_tables`) |
|---|---|---|
| `tenant_id` | `NOT NULL` on every table | **absent from every table** |
| isolation | RLS `ENABLE` **and** `FORCE`, tenant policy per table | no RLS at all; **`REVOKE ALL` from the tenant app role across all seven table privileges and their column-level forms** — the revoke *is* the isolation |
| reachability | tenant app role via policy | online platform role: schema `USAGE` **plus** at least one of `SELECT`/`INSERT`/`UPDATE`/`DELETE` per table. `REFERENCES`/`TRIGGER`/`TRUNCATE` alone is declared-and-unreachable, which is a violation |
| uniqueness | composite, includes `tenant_id` | control-plane-wide |
| models | `TenantInvoice`, `TenantPostingGroup`, `TenantSettlement`, … | `PlatformInvoice`, `PlatformPostingGroup`, `PlatformSettlement`, … |
| repositories | one per plane, separate classes | one per plane, separate classes |
| link helpers | tenant-scoped, composite FK, RLS | no tenant column, single-column FK, revoke |

Both planes ship in **revision 1**. Retrofitting a platform plane after a
tenant-only release is the `dotmac-ticketing 0.1.0a1` mistake, and it cost only a
rename there because nothing consumed it yet; a money-domain module will not be
that lucky.

Naming follows ADR-0023 § 5: the bare table name is the tenant plane
(`invoices`), the prefixed one is the platform exception (`platform_invoices`),
and the **Python classes are explicit on both sides** because the class is what a
developer reads when writing a query.

### 2.3 Explicitly rejected

| Rejected | Why, restated for money |
|---|---|
| `platform=True` on a repository, model, link helper or service | ADR-0023 § 5: a flag has a default, and whichever value that default takes is the plane a caller gets by **forgetting to think**. On one side that is a missing RLS policy on an invoice; on the other it is a control-plane invoice the product data plane can read. Two functions, two classes, no default. |
| Nullable `tenant_id` on an invoice, settlement or posting group | The column stops being an isolation key. An RLS predicate on a nullable tenant either denies platform rows to everyone or needs a second, wider policy — and the kernel's one documented exception of this shape (`domain_settings`) already costs a split read/write policy pair. For a receivable it would also mean a row whose owner is a `NULL`. |
| A sentinel / fake vendor tenant | Every query and every financial report then has to know which tenant id means "not a tenant". That knowledge is unwritten and spreads by copy-paste. The audit confirms the vendor control plane has **no `tenant_id` on any of its 18 owned models, no RLS policies in its own lineage, and no sentinel tenant anywhere** — so inventing one would be importing a defect into a clean adopter. |
| A polymorphic scope column (`scope_kind` + nullable `scope_id`) | Referential integrity is gone — PostgreSQL does not know what the UUID means — and the isolation predicate becomes a conditional on data rather than a structural property. |
| Inferring the plane from a missing `tenant_id` | The load-bearing half of ADR-0023 § 2: an invoice table that merely **forgot** the column would reclassify itself as platform and lose isolation silently. |
| An FK crossing the planes, in either direction | A tenant-scoped delete cascading into control-plane financial data, or a platform posting group whose visibility depends on a tenant predicate it has no column to satisfy. |

### 2.4 Invoice-number uniqueness, per plane

This is the concrete place the two planes differ visibly, and it is the same
shape ticketing's ticket numbers took:

- **Tenant plane:** `UNIQUE (tenant_id, series_code, number)`. Two tenants may
  each hold invoice `INV-2026-00001` and that is correct — the number is unique
  *within the tenant's own books*.
- **Platform plane:** `UNIQUE (series_code, number)`, control-plane-wide. There
  is no tenant to qualify it with, and the vendor control plane issues one set of
  books.

Two further points the constraint alone does not carry:

1. **Allocation is P4's, not billing's.** Billing binds a `NumberingProvider` at
   issuance and holds the returned number frozen. The gapless-or-not policy,
   period reset and concurrency safety are the numbering owner's. **This is the
   least-proven part of the whole extraction:** Sub's `next_invoice_number`
   (`app/services/billing/invoices.py:303`) has **zero test occurrences anywhere
   under `tests/`** — no uniqueness, gap or concurrency test — and ERP's five
   numbering implementations are covered only by two generic sequence-service
   suites with no invoice-specific test. See `billing-parity-tests.md` § 5.
2. **The uniqueness constraint is per plane and must not be "unified".** A single
   constraint that tried to cover both would need a nullable tenant column, which
   § 2.3 refuses.

### 2.5 Obligation uniqueness, per plane

C10's identity — `contract line + contract version + charge component + source
fact + source fact version + period_start + period_end + currency` — is a
`UNIQUE` constraint, composite with `tenant_id` on the tenant plane and
control-plane-wide on the platform plane.

**This is a port, not an invention.** Sub already enforces it:
`uq_billing_obligation_natural_identity` at
`app/models/billing_contract.py:501`, with behavioural tests at
`tests/test_billing_obligations.py:165`
(`test_replaying_the_same_natural_identity_returns_one_obligation`) and `:221`
(`test_same_natural_identity_with_different_coverage_fails_closed`). See § 5.3
for the correction this implies for `billing-sources.md` § 4 item 5.

### 2.6 The official-artifact relation, per plane

Added 2026-08-14 after the Team 2 / Team 4 decision gate. Billing owns the
statement *"this stored file is the official artifact of invoice X at fact
version Y"* — it is a domain statement, and ADR-0022 § 2 assigns exactly that
to the invoice domain (*"stores an opaque file UUID and **owns its relation**,
visibility, permissions, legal hold, retention rule, and audit vocabulary"*).
The full contract is
`docs/superpowers/specs/2026-08-14-billing-authority-profile-contract.md` Part 5;
what belongs in the dossier is its persistence shape.

| | tenant plane | platform plane |
|---|---|---|
| table | `document_artifacts` | `platform_document_artifacts` |
| model | `TenantDocumentArtifact` | `PlatformDocumentArtifact` |
| isolation | `tenant_id NOT NULL`, RLS ENABLEd + FORCEd | no tenant column, no RLS, `REVOKE ALL` from the tenant app role, `USAGE` + row DML for the online platform role |
| current-artifact uniqueness | partial unique `(tenant_id, fact_id, media_type) WHERE superseded_at IS NULL` | partial unique `(fact_id, media_type) WHERE superseded_at IS NULL` |
| replay uniqueness | unique `(tenant_id, fact_id, media_type, file_id)` | unique `(fact_id, media_type, file_id)` |

Three properties this table must have, each of which is a way the shape could go
wrong:

- **`file_id` is a plain `UUID` column with NO foreign key and no import of
  `dotmac_files`.** ADR-0022 § 2 states the rule directly: *"a domain module
  does not import this module merely to make a foreign key."* Billing never
  dereferences the handle.
- **No FK crosses the planes**, per ADR-0023 § 4 — and none reaches
  `dotmac-files` either, because a module FK to another module's table would
  make two independently-installed lineages one schema.
- **Zero rows is a legal state.** There is no `NOT NULL` artifact column on any
  invoice and no lifecycle state requiring one, because rendering failure never
  rolls back an issued invoice. `artifact_state`
  (`none`/`pending`/`available`/`degraded`/`withdrawn`) is a derived read model,
  and its `none`/`degraded` members are the reconciler's work queue — which is
  what makes absence **queryable rather than exceptional**.

---

## 3. Retirement inventory — Sub, slice by slice

ADR-0020's consequence is that "product-local allocation and balance writers
retire at cutover". This is that list, made specific. **A slice is done when the
local writer is DELETED**, not when the module can also do the work — two writers
of an allocation is the failure the whole programme exists to remove.

Each slice carries a ratchet in the ADR-0018 two-directional shape: a counted
baseline that fails when the count **rises** *or* when it **falls without the
baseline being lowered in the same change**, plus a sensitivity proof that the
detector still bites.

| # | Slice | Local writers to delete (verified paths, LOC) | The ratchet that proves retirement |
|---|---|---|---|
| **R1** | Invoice + credit-note lifecycle, and invoice numbering | `app/services/billing/invoices.py` (2,987 — includes `next_invoice_number` at :303), `app/services/billing/credit_notes.py` (2,452), `app/services/invoice_draft_authoring.py` (870), `app/services/advance_renewal_invoicing.py` (480), `app/services/invoice_discounts.py` (530) | Count of call sites constructing or transitioning an `Invoice`/`CreditNote` outside the module adapter; **plus** call sites of `next_invoice_number` (currently 5 in production code: `invoices.py` 706/1519/2123, `billing_automation.py` 1902/2277, and 2 in `crm_api.py`). Reaches zero before `invoices.py` is deleted. |
| **R2** | Obligation creation and resolution | `app/services/billing/obligations.py` (728), the recurring-run dedupe path in `app/services/billing_automation.py` (2,610) | Count of writes to `billing_obligations` outside the module adapter. Note the natural-identity constraint already exists (§ 2.5) — this ratchet is about the *writer*, not the constraint. |
| **R3** | Rating and applied tax | `app/services/billing/rating.py` (445), `app/services/billing/tax.py` (117), the invoice-side decisions in `app/services/tax_accounting.py` (1,189), `app/services/customer_tax_policies.py` (264) | Count of sites computing a line net/tax/gross outside the module. **Statutory tax recognition stays ERP's and is out of this ratchet** — an over-broad ratchet here would pull ERP work into a billing cutover. |
| **R4** | Settlement, allocation, refund, reversal | `app/services/billing/payments.py` (6,731), `app/services/provider_payment_settlements.py` (437), `app/services/financial_import_batch_reversals.py` (727), `app/services/payment_reconciliation.py` (811), and the `amount` field on `PaymentUpdate` at `app/schemas/billing.py:715` | Count of writes to `payment_allocations`, `payment_settlements`, `payment_refunds`, `payment_reversals` outside the module; **plus a schema-level assertion** that no update contract exposes a money field on a settled fact. The schema line is in this ratchet because § 5.2 found the service guard already exists while the contract still accepts the edit. |
| **R5** | Posting groups and per-currency positions | `app/services/billing/customer_subledger.py` (614), `app/services/billing/subledger_opening.py` (642), `app/services/billing/ledger.py` (342), `app/services/customer_financial_position.py` (368), `app/services/customer_financial_ledger.py` (1,088), `app/services/billing/opening_balance_history.py` (343) | Count of writes to `customer_posting_groups`/`customer_position_effects`/`ledger_entries` outside the module, **plus** a "no balance formula" detector: any site computing a position by summing rows rather than reading the module's derived projection. |
| **R6** | Credit, deposits, adjustments | `app/services/billing/account_credit.py` (1,710), `app/services/billing/adjustments.py` (1,233), `app/services/account_credit_deposits.py` (993), `app/services/quote_deposits.py` (1,066), `app/services/topup_intents.py` (1,509) | Count of sites creating available credit or prepaid funding outside the module. **This is the slice most likely to be under-scoped**, because prepaid top-up is spread across five services and reads as product domain until you notice each one mints funding. |
| **R7** | Derived-balance read surfaces | `app/services/web_subscriber_details.py` (1,015 — the `current_balance` computation at :385), `app/services/billing/reporting.py` (1,559) | Count of sites that add, subtract or otherwise combine two of {receivable, credit, prepaid funding}, and of sites casting money to `float` (`web_subscriber_details.py:502` does both). Reaches zero — the module publishes three separate values and no combination. |
| **R8** | Provider-event money consequences | `app/services/payment_provider_events.py` (1,335), `app/services/payment_webhook_commands.py` (654), `app/services/api_billing_webhooks.py` (244), `app/services/integrations/connectors/payment_gateway.py` (403), `app/services/payment_gateway_adapter.py` (354), `app/services/autopay.py` (467) | **This slice retires to the INTEGRATOR, not to billing** (ADR-0024 § 6, ADR-0020 A3). The ratchet counts provider clients, credentials, signature verifiers and webhook routes in Sub's runtime, and it is the Integrator adoption's ratchet — listed here only so a billing cutover does not accidentally claim or absorb it. |
| **R9** | The invoice-document artifact relation | `app/models/billing.py:856` (`InvoicePdfExport`, table `invoice_pdf_exports`) + `app/models/billing.py:71` (`InvoicePdfExportStatus`), `app/services/billing_invoice_pdf.py` (1,434), `app/services/invoice_bank_details.py` | **Splits three ways, and getting the split wrong is the risk.** The *relation* half — which stored object is the official PDF of which invoice — retires to billing's `document_artifacts` (§ 2.6). The *rendering* half retires to the Documents workstream. The *job-state* half (`queued/processing/completed/failed`) retires to **nothing**: it is a second at-most-once mechanism that ADR-0014 forbids, and it already exhibits the predicted failure — a `processing` row nothing finishes, worked around by `maybe_finalize_stalled_export` and a 20-second `STALE_EXPORT_SECONDS` heuristic. The ratchet counts writers of `invoice_pdf_exports` and callers of `billing_invoice_pdf`; it reaches zero, and **no replacement job table is created on any side**. |

**Two slices deliberately absent.** Dunning/collections
(`app/services/collections/`, 5,547 LOC) retires to `dotmac-collections`, not to
billing (plan Stage I). Subscription lifecycle (~10,000 LOC across
`app/services/subscription_*`) retires to `dotmac-subscriptions` (Stage H). A
billing cutover that pulled either would be the mega-module ADR-0020 rejects.

**What ERP retires.** Nothing to billing. ERP's `coverage.py` retires to the
**kernel** coverage slice (plan Stage B), a separate and independently adoptable
cutover, and ERP installs none of the three commercial modules (ADR-0020 A6).

---

## 4. The six non-conformances — corrected shape and required shadow measurement

`billing-sources.md` § 4 records six behaviours the extraction must not carry
forward. For each: what the module ships **from revision 1**, and the shadow
measurement that must precede cutover — because several of these change
customer-visible numbers and must be measured before a billing run, not
discovered during one.

### 4.1 `InvoiceStatus` conflates coverage with lifecycle

**Verified.** `dotmac_sub:app/models/billing.py:28` declares
`InvoiceStatus` with `partially_paid` (:31) and `paid` (:32) alongside
`draft`/`issued`/`void`/`overdue`, and additionally `written_off` (:40).

**Corrected shape from revision 1.** Lifecycle is structural only —
`draft`, `issued`, `void`, `written_off` (the last is a genuine lifecycle fact:
the obligation was closed as bad debt, which is a decision, not an arithmetic
result). `partially_paid` and `paid` do not exist in any lifecycle enum.
`balance_due` is `GENERATED ALWAYS AS (total_amount - amount_paid) STORED`,
coverage is `UNPAID | PARTIAL | PAID | OVERPAID` derived by one owning function
from `balance_due` and a `SettingSpec` tolerance, and `overdue` becomes a
time-derived lifecycle/aging concern that a payment cannot erase (ADR-0016).
ERP's `coverage.py` is the ported owner, including its four-branch order —
`OVERPAID` before `PAID` before `PARTIAL` before `UNPAID` — which is load-bearing
because the branches overlap, and its documented Python/SQL asymmetry (SQL reads
the generated column; Python subtracts live, because a loaded instance's
`balance_due` is stale after a Python assignment to `amount_paid`).

**Shadow measurement.** For every Sub invoice, compare the derived coverage
against the stored `InvoiceStatus`. Expect a non-zero disagreement count — ERP's
migration path expects the same, and disagreements are pre-existing data defects
being surfaced, triaged before cutover. Specifically enumerate: (a) invoices with
`status = void` whose balance is zero and would read `PAID` under a balance-only
rule; (b) invoices with `status = overdue` that a partial payment already
overwrote or would overwrite; (c) invoices within one minor unit of full
coverage, tabulated across the candidate tolerances. **Customer-visible:** an
invoice that displays "Paid" today and "Partially paid" after cutover is a
customer-facing change and needs explicit Finance acceptance.

### 4.2 Coverage arithmetic already diverged at scale in ERP

**Verified.** ERP's `test_paid_status_single_owner.py`
(`tests/architecture/`, 176 LOC) records in its own docstring that the rule "had
grown to **eight implementations with six different rules** — three tolerances,
four different answers for 'nothing paid'". ADR-0016's table records twelve sites
and seven rules across AR/AP/IPSAS.

**Corrected shape.** Port `coverage.py` (198 LOC) and its parity test; do not
re-derive. The tolerance is the `payments.payment_dust` `SettingSpec`. Note the
precise form of the rule, taken from the implementation being ported:
`PAYMENT_DUST_DEFAULT = Decimal("0.01")` exists **once**, as the declared default
of that spec, and every decision site takes `dust` as a resolved parameter. The
architecture check is therefore "a money literal appears only in a `SettingSpec`
default declaration, never in a comparison, a `CASE`, or a service body" — a
blanket ban on the string `0.01` would fail the very implementation being ported.

**Also port a structural correction ERP does not have.** ERP declares
`total_amount`/`amount_paid`/`balance_due` **per model, with no shared mixin**,
duplicated across five models (`ar/invoice.py:168`, `ap/supplier_invoice.py:179`,
`expense/expense_claim.py:320`, `people/payroll/salary_slip.py:215`,
`finance/lease/lease_payment_schedule.py:77`). ADR-0016 § 5 requires a shared
mixin; the module ships one. This is a place the extraction is *better* than both
sources, and it is worth naming because product-first does not mean
product-identical.

**Shadow measurement.** Run the ported `coverage_of`/`coverage_case` parity
matrix on the target database, then compare derived coverage against each of
Sub's own coverage-writing paths, per path, so a divergence is attributed to a
rule rather than to a row.

### 4.3 Prepaid and postpaid are two subsystems

**Verified.** Sub has separate policy modules
(`app/services/collections/prepaid_policy.py` 116,
`postpaid_policy.py` 61, dispatched by `mode_policies.py` 64) and a distinct
legacy sweep (`prepaid_balance_sweep.py`, 758) that Sub's own ADR-0007 § 7 says
to retire in favour of durable timers.

**Corrected shape.** `collection_timing` is **one field on the contract
version** (`advance` | `arrears`), not two engines (C2). One obligation machine,
one resolution protocol, one collections entry point. **Most of this correction
lands in `dotmac-subscriptions` and `dotmac-collections`, not in billing** — the
cadence value object is P5 and the dunning ladder is C4. Billing's share is that
it accepts obligations and settlements identically regardless of timing, and
ships an architecture test that no billing symbol is named for exactly one timing
mode.

**Shadow measurement.** Run the same scenario under `advance` and `arrears` and
assert both traverse the same owner functions. This one is *not* customer-visible
in billing, which is why it is safe to correct at the boundary rather than
staging it.

### 4.4 `current_balance = balance_due + available_credit`

**Verified, and worse than recorded.** The real path is
`dotmac_sub:app/services/web_subscriber_details.py:385` (the inventory's
directory is stale — see § 5.1). Two further defects at the same site that the
inventory does not record:

- the sum is **cross-currency-blind** — neither term filters by currency, so a
  customer with balances in two currencies gets a meaningless number; and
- the result is surfaced at `:502` as `float(current_balance)` — **a float cast
  of money**, in the same code path.

Sub's own ADR-0007 § 4 already indicts this: "There is no cross-currency total
and no generic `account.balance`", and "an asynchronous UI cache is never an
enforcement input".

**Corrected shape from revision 1.** `ReceivablePositionV1` carries three
separate exact `Money` values per currency — `collectible_receivable`,
`available_credit`, `prepaid_funding` — and **no fourth combined field**. A
consumer wanting a single number writes the arithmetic in its own code where a
reviewer can see it. No float anywhere; `dotmac_kernel.money` already refuses to
build `Money` from a float. Sub has real proofs to port here:
`tests/test_customer_subledger.py:163`
(`test_positions_are_per_currency_and_semantic_lane`) and
`tests/test_customer_financial_position.py:213`
(`test_native_signed_balance_is_currency_typed_and_fail_closed`).

**Shadow measurement — this one is definitely customer-visible.** For every
account, compute today's `current_balance` and the module's three values, and
tabulate: accounts where the displayed number changes at all; accounts where it
changes *sign*; and accounts holding more than one currency, where today's number
is not merely different but was never meaningful. Finance and product must accept
the display change before cutover; a subscriber portal that showed a smaller debt
because credit was added to it will show a larger one afterwards.

### 4.5 Duplicate-billing dedupe keyed on a single `subscription_id`

**Partly corrected in the source already** — see § 5.3. Sub's
`billing_obligations` table already carries
`uq_billing_obligation_natural_identity`
(`app/models/billing_contract.py:501`) with replay and conflict tests. The
single-`subscription_id` dedupe is in the **recurring run**
(`app/services/billing_automation.py`), a different path that has not adopted the
constraint the obligation model already enforces.

**Corrected shape from revision 1.** The C10 tuple is the module's `UNIQUE`
constraint on both planes (§ 2.5) and is *also* the idempotency key for
`AcceptRatedObligationV1`, with a non-`None` fingerprint over the rated amounts —
so a rerate arriving under the same natural key is a loud `IdempotencyConflict`
rather than a silent replay. Duplicate billing becomes impossible under replay
and concurrency rather than merely unlikely. Port
`tests/test_billing_obligations.py`'s two identity tests as the parity proof.

**Shadow measurement.** Replay the recurring run against captured inputs and
count how many obligation pairs the C10 tuple collides that
`subscription_id`-dedupe did not — specifically the standalone-subscription /
add-on-for-the-same-service pair the inventory names. **Customer-visible in the
correct direction:** these are invoices that should not have been raised, so the
delta is a set of historical duplicates, and whether they are credited is a
separate Finance repair decision the cutover plan explicitly does not authorize.

### 4.6 Editable settled `Payment.amount`, and the uncapped pending ledger credit

**Both coordinates in `billing-sources.md` § 4 item 6 are stale, and one of the
two defects has already been fixed in the source.** See § 5.2 for the
correction. What is actually true today:

- **Settled-amount edit:** the service guard **exists** at
  `dotmac_sub:app/services/billing/payments.py:3342-3348` — `Payments.update`
  refuses any field but `memo` when `payment.settlement is not None`, with
  "Settled payment fields are immutable evidence". The **residual exposure is the
  contract, not the service**: `dotmac_sub:app/schemas/billing.py:715` still
  declares `amount: Decimal | None` on `PaymentUpdate`, so an amount edit is
  accepted by the API schema and rejected only at runtime when a settlement row
  happens to exist. An **unsettled** payment's amount remains freely editable.
- **Uncapped pending credit:** `dotmac_sub:app/services/billing/providers.py` is
  **76 lines of configuration only** at HEAD and contains no money logic. The
  logic the inventory describes moved to
  `dotmac_sub:app/services/payment_provider_events.py:794-840`, where a
  `PaymentStatus.pending` payment is staged with `auto_allocate=False` and handed
  to a cash-first settlement path.

**Corrected shape from revision 1.** A settled fact is **structurally**
immutable: there is no update command that names a money field, on any payment
state, so the guard is the absence of an API rather than a runtime check on one.
Corrections are reversal, refund or supersession, appended as typed effects
linked to the fact they offset. And **only an independently confirmed settlement
creates money** — a pending checkout, an uploaded proof, an unverified provider
acknowledgement and a UI click each carry a `confirmation_evidence` code that the
acceptance policy refuses, which removes the "pending credit" concept entirely
rather than capping it.

**Shadow measurement.** (a) Enumerate every settled payment whose `amount`
differs from its settlement evidence — those are historical edits, and they are a
data-classification input to S0's disposition table, not something the module
repairs. (b) Enumerate every `PaymentStatus.pending` payment that produced a
ledger credit and was never reversed, by age and amount. This population is
exactly the cutover plan's "active/open financial fact with missing provenance"
disposition — a **cutover blocker** requiring quarantine and a Finance decision,
not a default bucket.

---

## 5. Errors found in the integration owner's documents

Reported, not edited. Every item was verified by direct file read.

### 5.1 `billing-sources.md` § 4 item 4 — stale directory (minor)

The inventory cites `web_subscriber_details.py:385` without a directory, so it is
not strictly wrong; but the file is at
`dotmac_sub:app/services/web_subscriber_details.py`, **not** under `app/web/`.
Worth pinning, because the defect is real and someone will go looking. The
inventory also omits the two aggravating factors at the same site
(cross-currency-blind sum; `float()` cast at `:502`).

### 5.2 `billing-sources.md` § 4 item 6 — **both coordinates are stale; one defect is already fixed**

> "`Payment.amount` editable on a settled payment (`payments.py:1719-1731`) and
> an uncapped pending ledger credit never reversed
> (`billing/providers.py:310-337`) — both from the 2026-07-14 P0 audit."

- `app/services/billing/payments.py:1700-1740` is the **settlement audit-staging
  block**, not an amount mutation. The pre-refactor revision has the same audit
  block at the same coordinates. The settled-amount guard **exists** at
  `:3342-3348`. The live residue is `app/schemas/billing.py:715`.
- `app/services/billing/providers.py` is **76 lines** at HEAD, configuration
  only. The 625-LOC revision that held the described logic predates PR #1519; the
  logic now lives at `app/services/payment_provider_events.py:794-840`.

**Why this matters beyond tidiness:** a dossier that ports "the owners, not these
paths" against stale coordinates would port a file that no longer contains the
defect and miss the schema field that still does. Suggested correction is in
§ 4.6 above.

### 5.3 `billing-sources.md` § 4 item 5 — understates what Sub already has

The inventory says the obligation identity "must be a database uniqueness
constraint, not a query convention". Sub **already has it**:
`uq_billing_obligation_natural_identity` at
`app/models/billing_contract.py:501`, with two behavioural tests
(`tests/test_billing_obligations.py:165` and `:221`). The single-`subscription_id`
dedupe is in the recurring run, a different path. The correct statement is "the
constraint exists on `billing_obligations` and is proven; the recurring run does
not use it" — which changes the extraction task from *invent* to *port and extend
coverage*.

### 5.4 `billing-sources.md` § 1 — vendor CP table count

The table reports **22** mapped tables for the vendor control plane. An
exhaustive `__tablename__` grep of `src/` returns **18** owned tables at
`8984801`. The likely reconciliation is that the vendor assembly composes two
released modules (`dotmac_release_catalog`, `dotmac_entitlement_allocation`)
whose tables live in separate schemas (`mod_rel`, `mod_ealloc`); 18 + 4 would
reach 22. That is a hypothesis — those packages are not installed in the
checkout, so it could not be confirmed. **The "three commercial tables" claim is
correct.** Recommend the count be restated as "18 owned + composed module tables"
so the two numbers stop being confusable.

### 5.5 `billing-sources.md` § 1 — vendor CP kernel imports

The table says "provisioning, money, messaging, licensing". The measured import
set is larger: `messaging` (9 sites), `platform_auth` (8), `features` (8), `db`
(6), `providers.provisioning` (4), `licensing` (3), `migrations` (1), plus 28
top-level `from dotmac_kernel import ...` — which is where `Money`, `MoneyError`
and `currency` actually come from (re-exported), so the *submodule* path
`dotmac_kernel.money` is not what the vendor imports. Functionally the claim
holds for money; the import surface is four modules wider than recorded.

### 5.6 `billing-sources.md` § 1 — ERP kernel imports

"Exactly one: `money`" is true for runtime code — three import sites, all
`dotmac_kernel.money`, funnelled through one 490-LOC adapter
(`app/services/finance/money_boundary.py`). Worth recording alongside it that
ERP's own guard `tests/architecture/test_kernel_import_boundary.py` **permits
eight** kernel modules and its ledger tracks two more as intended-but-not-adopted.
The one-import fact is about adoption, not about the allowed surface, and the
inventory reads as if it were about both.

### 5.7 `billing-sources.md` § 1 — Sub FX

The table records Sub's FX as "per-currency positions", which is accurate as far
as it goes. The stronger and more useful fact for the extraction: **Sub has no FX
concept at all** — a grep for `fx_rate|fx_snapshot|exchange_rate|forex` returns
nothing under `app/` or `tests/`. Multi-currency is handled purely by
currency-scoped separation. So the FX snapshot the cutover plan requires has
**exactly one source in the fleet** (ERP's `core_fx/` four models plus
`app/services/finance/platform/fx.py`, 593 LOC) and **no behavioural parity test
from Sub is possible**. This is a fresh-test area, not a port.

### 5.8 `docs/inventories/README.md` — index rows missing

The inventory index does not list `billing-extraction-dossier.md` or
`billing-parity-tests.md`. Two rows are needed. Not edited here: the README is
the integration owner's file.

### 5.9 `fleet-decomposition-matrix.md` — measurement-definition divergence (not an error)

Row `billing-revenue` reports ERP 12 / Sub 74 tables, while `billing-sources.md`
reports ERP 32 money-domain (AR + AP) / Sub 66. These are different buckets
measured for different purposes and neither appears wrong; flagging it only
because two documents in the same directory give four different numbers for "how
much billing is there", and a reader will assume one is stale.

---

## 6. Historical comparison with the focused cutover plan (2026-08-14)

Read against
`docs/superpowers/plans/2026-08-14-billing-vendor-cp-sub-cutover.md` in full.
**At that audit date, no contradiction was found.** The 2026-10-13 M1 ruling
now supersedes Vendor-first sequencing; the plan is non-authoritative intent and
its historical ordering is not a current entry gate. The historical `shadow_and_drift`
and `local_copy_retirement` were written against that plan; their source-retirement
requirements survive the changed adopter order.

Four points of **tension** — not contradictions, but places where the plan and
the verified evidence need each other and one of them should be updated by its
owner:

1. **B0 asks for an inventory this dossier now partly supplies.** The plan's B0
   ("dossier and source disposition") requires inventorying every Sub model,
   service, route, job, webhook adapter and test that touches money, and "every
   source test to port and every behavior with no source test". § 3 and
   `billing-parity-tests.md` are that inventory at service and test granularity.
   B0 additionally requires route/job/webhook-adapter granularity, which this
   audit did not reach. **Not a conflict; a stated remainder.**
2. **B0's "six known extraction corrections" now has corrected coordinates.**
   The plan says "record the six known extraction corrections from the billing
   inventory; do not copy them as compatibility behavior." Two of the six are
   recorded against stale paths and one is already fixed at the service layer
   (§ 5.2). The plan's instruction is right; the inventory it points at needs the
   correction in § 4.6 before B0 can execute it literally.
3. **V2 step 2 and P8a.** The plan says: "If P8a is not ready, do not call an
   HTML preview a completed legal-document cutover; explicitly hold production
   issuance or obtain an accepted profile ruling." Document generation is now a
   separate workstream with its own owner and contract spec. This dossier's
   position is that the plan's sentence stands unchanged and the new workstream
   is what may eventually satisfy it — but **whether Vendor CP may issue a
   production invoice with no rendered PDF is an unresolved Finance/legal
   question**, not an engineering one, and it gates V2. Listed as an open
   question in § 7.
4. **Entry gate 4 and numbering.** The plan requires P4 numbering for internal
   issuance. This audit adds a fact the plan could not have: Sub's
   `next_invoice_number` has **no test at all**, and ERP's invoice numbering is
   covered only by generic sequence suites. So P4 is not merely an unbuilt
   facility — it is a facility whose *product-first source has no behavioural
   proof to port*. That raises P4's cost and should be reflected wherever P4 is
   sized.

One thing this dossier **declines to restate**: the plan's B1–B5 slice
definitions, V0–V3 Vendor evidence, and S0–S4 Sub sequence. Those are the plan's
and duplicating them here would create a second copy to drift.

---

## 7. Historical open questions (2026-08-14)

The questions below preserve the audit record and are not a current approval
checklist. P11/implementation-start claims are historical; package/publication
evidence and current Sub-first readiness are stated above. Vendor-specific
questions do not gate Sub-first preparation. No new approval is created here.

1. **The gate itself (historical).** P11 was not met at this audit date. Does `dotmac-billing` get an
   owner-directed exception in the ADR-0026 shape, or does it wait for Sub's S7?
   Nothing below matters until this is answered.
2. **Production issuance without a rendered document** (§ 6.3). May Vendor CP
   issue a legally numbered invoice when P8a is not ready? This is a Finance and
   legal question that gates the cutover plan's V2.
3. **P4's cost, restated.** Numbering has no tested source anywhere in the fleet
   (§ 5.7, § 6.4). Is it built with fresh proofs as part of the billing slice, or
   does it stay a separate gap-listed facility with its own adopter?
4. **The § 4.4 display change** is customer-visible on the subscriber portal and
   needs Finance/product acceptance before cutover, not after.
5. **The pending-credit population** (§ 4.6) is a cutover blocker under the
   plan's own disposition table. Its repair — if any — needs a separate,
   dry-run-first Finance approval that the cutover plan explicitly does not grant.
6. **Vendor CP's money column type — RULED 2026-08-14 (ADR-0020 § A7), no
   longer open.** The module persists money as **`NUMERIC(20,6)` with an
   uppercase ISO-4217 currency code and persisted minor-unit precision**.
   Decimal strings remain a **wire format only**.

   Vendor's quantized `String(40)` decimal string plus ISO code, reconstructed
   as kernel `Money`, is therefore the **migration source** for Vendor's first
   adoption and not a shape the module ships. That adoption carries a
   representation conversion as well as a lineage: every stored string is parsed
   to `NUMERIC(20,6)` under the declared minor-unit precision for its currency,
   and a value that will not round-trip is a migration failure rather than a
   silent quantization. ERP's existing `Numeric(20, 6)` columns need no
   conversion.
7. **The official-artifact relation belongs to `dotmac-billing`** (§ 2.6, and
   the contract in the authority-profile spec's Part 5). Team 2 and Team 4
   independently reached the same recommendation from ADR-0022 § 2. This is a
   named decision gate and needs Michael's ruling — two agreeing teams is
   evidence, not a decision. Its residual is documentation: ADR-0020 A5's
   *"emits immutable document facts and stops"* reads absolutely and needs a
   one-line clarifying amendment by its owner, or § 2.6 will read as a
   contradiction to every later reader.
8. **`InvoiceArtifactReconciler` is required before Vendor CP cutover.** The
   `invoice.issued` event is a wake-up signal, not the convergence mechanism.
   The reconciler is assembly-owned — it is the only place allowed to touch the
   renderer, `dotmac-files` and billing in one flow — and it ships a canary in
   which the event is SUPPRESSED and reconciliation still converges. Adding it
   to the Vendor CP entry gates is the integration owner's call, since the
   cutover plan is not this dossier's to edit.


## Current account observation extension — 2026-10-15

This bounded PORT-DELTA extends the existing clean Billing account owner; it
creates no module, lineage, migration or second identity owner. The implementation
source is `dotmac_billing.service.create_billing_account` and
`dotmac_billing.queries.get_invoice_document_fact` at accepted Starter source
`49f35ca6008ef36c3db69f3230782d8f83146ac9`: preserve scoped tenant/platform routing,
read persisted table mappings inside `no_autoflush`, and return a frozen typed copy.
The existing product inventory and clean-core source variance remain applicable.
Sub's subscriber reads identify legacy product subscribers, and ERP's AR/customer
reads identify its product accounts; neither is a public read of the clean Billing
account tables. Their identity/repair machinery is not imported.

The concrete prospective consumer is Sub's `ModuleBillingAccountBinding` in
`app/models/billing.py`, present in the invoice-cash foundation at Sub source
`bae92fe01a24cdec9d2283dabe7a793efe25674a` (tree
`946d668835b92b074323cbc7a101f926da1db762`). Its composite FK proves scope/id
existence, but cannot prove currency, minor-unit precision or external product
reference. Its separately reviewed consumer source at Sub commit
`60ff4a755636ce55a8f683806bdf3a6b1d8b1929` (tree
`cf0929550d63284760e9092d2c8c79d2d466312e`) implements
`get_module_invoice_billing_account` in
`app/services/provider_payment_settlements.py`: resolve persisted checkout facts
and the local mapping, call this genuine public query, then compare payer
reference, currency and precision. Its focused PostgreSQL canaries call the real
API; no foreign ORM read or local copy is introduced. The consumer branch is
published as source only, without a pull request: type and positive PostgreSQL
acceptance require this API to be published and exactly pinned first. The earlier
foundation commit contains neither the getter import nor proven account
validation. This is prospective composition, not adoption;
`contract_consumers` remains empty.

`BillingAccountObservationV1` contains current scope, account id, external
reference and kernel `Currency`. Frozen describes the returned value only. The
external reference is mutable and the account can disappear; each query observes
its own statement's persisted state and callers must re-observe as needed. It is
not a historical fact, balance, authorization, lock proof or cached admission token.
The query neither flushes/commits nor reads dirty ORM attributes. Existing real
Alembic-migrated PostgreSQL canaries are reused for RLS, cross-plane same-id
isolation, missing/invalid input, dirty identity maps, relink/delete freshness and
corrupt stored identity. Hosted execution remains pending. Billing `0.1.0a8` stays
an unpublished candidate; this change neither bumps a version nor grants
publication or production authority.

## Public rated invoice authoring — 2026-10-15 candidate

This bounded API delta composes Billing's existing `accept_rated_obligation`,
`create_draft_document` and `issue_document` owners. `IssueRatedInvoiceV1` carries
one already-rated obligation and the existing frozen document snapshots;
`IssuedRatedInvoiceV1` returns persisted scope/account/obligation/invoice/line
identities, line number and the original issued fact version. Quantity is exactly
one. Numbering and the manifest-backed obligation-source registry are injected by
the assembly; the command cannot declare trusted source kinds. Kernel's existing
idempotency operation/savepoint contains all three stages, including number
allocation, postings and facts; services never commit or roll back the root.
This adds neither a ledger nor tax/rating/numbering authority.

The real intended consumer is Sub `app/services/prepaid_period_purchases.py`,
`settle_prepaid_period_purchase` / `settle_verified_prepaid_period_purchase`,
which issues one invoice and one line per funded final service period (source
inspection at prospective Sub bridge head
`99313fb2cc0e179d13742d26a75e3a58c6720c6b`, in
`dotmac_sub-billing-module-invoice-cash`). Its existing staged invoice/line and
issuance logic is the financial reference, including quantity one, frozen price
and tax evidence, confirmed funding, and per-period identity. This API reuses
clean Billing's accepted implementation rather than adding a second algorithm.
No Qualification prototype or API #845 dependency is introduced.

Consumer composition is deferred: Sub still needs its module-line/product-FK
bridge; frozen price and Tax-owner evidence mapping; owner conflict-savepoint
and root transaction composition; and verified cash-source binding. No consumer,
adoption, activation, legacy retirement, release, or production authority transfer
is claimed. The historical published Billing baseline remains `0.1.0a1`;
`0.1.0a8` and this source delta remain unpublished candidates. Hosted PostgreSQL
canaries cover isolation, exact replay and changed replay, original persisted
identities, one issuance effect, and failure atomicity; local static checks are
not runtime evidence. No new schema target or migration is required by this API.

The public authoring path validates persisted applied-tax evidence with the
existing `_require_obligation_taxes` rule inside its outer operation, before
creating the draft. Missing evidence for nonzero tax, mismatched currency or
minor-unit precision, and a tax sum that differs from the rated obligation are
refused atomically. Legacy single-line authoring behavior is unchanged.
Source fact version remains part of Billing's established obligation natural
identity: changing it creates a new identity, not a correction of an issued
invoice. Assemblies retain source revision/deduplication ownership and must use
the existing correction owner for documented obligations. Hosted canaries now
compose the actual Numbering lineage and public allocator to verify number
receipt/counter rollback, caller root rollback and concurrent public retries.

## Exact line source provider key — 2026-10-16 candidate

The qualifying consumer reference remains Sub
`99313fb2cc0e179d13742d26a75e3a58c6720c6b`:
`app/services/prepaid_period_purchases.py` stages a financial invoice and line
for each funded final period; `app/models/prepaid_funding.py` and
`app/models/billing.py` retain legacy purchase and entitlement invoice-line FKs.
The public rated-invoice receipt returns stable stored identities. This separate
provider schema prerequisite lets a future product FK prove that those identities
belong to the exact Billing document and account. The existing tenant `(tenant_id,
id)` line key alone cannot prove the product account's affinity.

PORT-DELTA `bi_0010_line_source_key`, following `bi_0009_opening_window`, adds
`uq_document_lines_source_key` over `(tenant_id, id, document_id,
billing_account_id)` and `uq_platform_document_lines_source_key` over `(id,
document_id, billing_account_id)`. These are ordinary nonpartial unique
constraints, installed only on `selected_module_planes('billing')`. Existing
keys, line/document/account FKs, FORCE RLS, issued-line immutability and runtime
ACLs remain their existing owners. No released migration is edited. Downgrade
uses PostgreSQL RESTRICT: a bound consumer FK refuses removal; no cascade or
financial-history deletion is introduced.

Sub's nullable module-line bridge is planned after its revision 693, preserving
legacy FKs, with no historical backfill; closed invoice history stays in the
read-only archive. This change adds no product field, authoring API, ledger or
caller activation. It is not consumer adoption, qualified cash or payment-status
proof. Billing `0.1.0a8` and this source delta remain unpublished.

`tests/test_billing_line_source_key.py` authors real PostgreSQL canaries against
the actual fresh migration chain and populated bi_0009 predecessor upgrade,
with tenant-only, platform-only and both-plane selections. Disposable cross-schema
probe FKs exercise exact tuple acceptance and wrong document/account/scope
refusal; catalog and runtime probes retain financial guards and ACLs, and bound
consumer FKs refuse downgrade. Probe DDL is not a deployed Sub schema. Runtime
execution is reserved for Git-hosted CI; local AST/Ruff/diff checks are static
evidence only.


### 2026-10-16 — exact current settlement allocation observation (source only)

The direct Sub `fund_prepaid_module_period_entitlement` consumer replays
`allocate_settlement` before granting or replaying a product entitlement. The
historical posting-group result survives later deallocation, refund or reversal,
so Billing now exposes `GetCurrentSettlementAllocationV1`, frozen
`CurrentSettlementAllocationV1`, and `get_current_settlement_allocation`. The
existing public queries/contracts/facade own this narrow observation; allocation
replay and schema remain unchanged. The request binds scope, account, exact
allocation posting group, settlement, invoice and Money. The query reads scalars
under the account lock, requires READ COMMITTED, validates live invoice and
confirmed backing, and sums exact linked inverse descendants. Unlinked
reallocation has no original-allocation identity and therefore refuses.

`tests/test_billing_current_allocation.py` authors both-plane hosted migrated-PG
canaries for scope/identity refusal, initial/replay, frozen values, partial/full
deallocation, inverse-of-inverse, refund, reversal, unlinked reallocation and
rollback, dirty identity-map avoidance, stale-isolation refusal and bounded real
backend lock waits in both directions (observation waits for committed correction;
correction waits for the product-decision transaction). Local validation is
static only; runtime acceptance, package
publication, exact consumer pinning and installation activation remain separate.

### 2026-10-16 — purchase funding earmarks (implementation inventory)

The concrete consumer is Sub's accepted-quote prepaid purchase. Cash and its
selected source survive optional invoice/allocation/entitlement rollback; retry
must use that same source. The qualifying algorithms and guards are in Sub
`PaymentAllocations._available_amount` (`billing/payments.py:5436`), purchase
allocation affinity (`:4804`), `_source_payments` (`billing/account_credit.py:449`)
and reserved versus spendable credit (`billing/_common.py:245,285`).
`tests/test_prepaid_period_purchase_safety.py:144` exercises cash/reservation
retention, zero unrelated spending and recovery using the original receipt.
These observations were inspected in the cached Sub source and are not runtime
or production acceptance.

The prospective Sub694 selected-settlement FK is a product link, not a Billing
capacity restriction. Existing Billing explicit allocation, refund and reversal
commands can consume or invalidate that source without consulting the link.
The actual consumer uses PREPAID_FUNDING; today's pooled available-credit draw
filters AVAILABLE_CREDIT and does not consume that prepaid source. Billing's
prepaid-funding position nevertheless has no held/unrestricted distinction.

Under ADR 0020's funding-position, allocation and correction ownership, extract
a whole-settlement economic earmark into the existing Billing core. Preserve
Sub's product selection and optional-consequence transaction semantics. Do not
copy legacy mutable balance fields, attach Sub financial-policy triggers to
module-owned tables, add a reservation module, or import Sub into Billing.

The source contract binds the exact original unused settlement/account/Money,
declared producer identity and immutable rated-obligation source targets with
exact amounts. Targets may be empty for an exceptional capture held for review.
Claims and capacity attribution are append-only; corrections use linked facts
and fresh operation-qualified authority. Every existing monetary mutation owner
must discover the claim, including allocation, deallocation, reallocation,
refund and reversal ancestry. Unrestricted capacity is actual unspent capacity
minus still-held capacity; spending already released capacity must not subtract
from held capacity again. Historical position facts retain their original
meaning; a typed availability query supplies held/unrestricted values and a
watermark covering both monetary and earmark facts.

Required hosted proofs include both migrated planes, exact target/replay and
wrong-target refusal, ordinary correction refusal and authorized immutable
inverses, source/correction races, account rollback, and Sub's original cash plus
earmark surviving optional issuance failure. Published migrations remain frozen.
This inventory precedes implementation; no installed contract, package release,
positive correction verifier or production activation is claimed.

The source review also found a generic inverse gap in the existing clean core:
`_reverse_group` negated allocation effects without a per-invoice room check.
After deallocation and another receipt funding that invoice, reversing the
deallocation could overcover it while a second open invoice kept aggregate
account receivables nonnegative. Aggregate position validation is insufficient.
The source change therefore guards source and document capacity for all affected
postings, including unearmarked sources and opening/mixed inverses. Removing
receipt backing requires zero remaining consumption. Deallocation follows linked
inverse descendants so legitimate restored funding can be corrected again.
The current-allocation test now refuses backing removal while its allocation
remains, then proves the linked deallocation and backing inverse sequence.
Authored regressions and independent source review are not hosted acceptance.
