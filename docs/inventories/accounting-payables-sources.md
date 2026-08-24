# Accounting and payables extraction audit

- **As of:** 2026-08-19
- **Starter:** `f7d69f7d` (`origin/main`)
- **ERP:** `4aab5681` (`origin/main`)
- **Sub:** `feea7159` (`origin/main`)
- **CRM:** `60daaa2d` (`origin/main`)
- **Vendor control plane:** `2c4d88ab` (`origin/main`)
- **Academy:** `a5e25e4e` (`origin/main`)
- **Workspace:** `a158846c` (`origin/main`)
- **Backoffice:** `fcdd8270` (`HEAD`; repository has no configured origin)

This is the product-first source audit for two adjacent, independently
installable tenant modules. It is not a release, product composition, data
migration, deployment, or authority cutover.

## Contract named before code

`dotmac-accounting` owns a tenant's chart of accounts, fiscal years and
periods, accounting dimensions, draft journals, the balanced-posting decision,
period close/reopen/irreversible lock decisions, reversal journals, and the
append-only ledger evidence produced by posting.

`dotmac-payables` owns supplier invoices and credit notes, their lines and
approval lifecycle, recognized supplier liabilities, due-date payment
obligations, credit applications, and immutable observations of settlement and
accounting consequences.

The separation is load-bearing:

- Payables requests an accounting consequence through a typed value. It never
  imports Accounting or writes a journal/ledger table.
- Accounting accepts an opaque source owner, document identity, version and
  fingerprint. It never reads a payables, procurement, asset, billing, tax or
  banking table to rediscover what should be posted.
- Procurement owns sourcing and purchase commitments. Payables may retain an
  opaque purchase/receipt evidence reference but cannot edit procurement state.
- Party/supplier-directory owners own supplier identity and compliance.
  Payables stores an opaque supplier reference plus the invoice evidence
  snapshot needed to understand the liability.
- A treasury/payment owner executes disbursement and bank movement. Payables
  records a typed, deduplicated settlement observation; it stores no bank
  account, provider credential, payment batch or transport retry state.
- Numbering allocates document numbers. Both modules accept numbers already
  allocated by the assembly and never import `dotmac-numbering`.
- Approvals decides approval. The owning module validates a supplied approval
  reference and performs its own transition; it never treats approval as the
  business transition itself.

## Finding

**ERP is the sole qualifying implementation for both slices.** Its `gl` models,
services and IFRS tests implement the chart, fiscal-period guard, journal
posting, reversal and append-only ledger. Its `ap` models, services and tests
implement supplier invoices, credit notes, payment coverage and tenant
filtering. Those behaviors are the mandatory source and parity evidence.

**Sub is deliberately not a competing GL.**
`app/models/customer_subledger.py` states that its immutable customer position
effects are operational economic meanings, not chart-account debits and
credits, and that ERP owns the general ledger. Sub's invoices and credit notes
remain the operational receivables owner described by ADR-0020. They may emit
accounting facts but do not move into Accounting or Payables.

CRM has no generic chart, journal or fiscal-period owner, but it does have an
active, tested project-bound `VendorPurchaseInvoice` lifecycle and ERP push
path. It is not the qualifying Payables source and owns no general liability,
obligation, settlement or accounting-receipt ledger, but it is a legacy
supplier-invoice writer that must retire rather than be reported absent.
Vendor control plane, Academy, Workspace and Backoffice have no competing
owner at the pinned revisions; Academy's matches are course content.

The local `agent/dotmac-finance` worktree was inspected as non-authoritative
candidate evidence. It is uncommitted fixed-asset book work and explicitly
excludes chart accounts, fiscal periods and journals. It remains a future
producer of Accounting facts, not the ledger owner and not a source in this
audit.

## ERP accounting source surface

### Models

- `app/models/finance/gl/account_category.py`
- `app/models/finance/gl/account.py`
- `app/models/finance/gl/fiscal_year.py`
- `app/models/finance/gl/fiscal_period.py`
- `app/models/finance/gl/journal_entry.py`
- `app/models/finance/gl/journal_entry_line.py`
- `app/models/finance/gl/posted_ledger_line.py`
- `app/models/finance/gl/posting_batch.py`

### Owning behavior

- `app/services/finance/gl/chart_of_accounts.py`
- `app/services/finance/gl/fiscal_year.py`
- `app/services/finance/gl/fiscal_period.py`
- `app/services/finance/gl/period_guard.py`
- `app/services/finance/gl/journal.py`
- `app/services/finance/gl/ledger_posting.py`
- `app/services/finance/gl/reversal.py`
- `app/services/finance/gl/period_close.py`

### Parity evidence retained

- `tests/ifrs/gl/test_chart_of_accounts_service.py`
- `tests/ifrs/gl/test_fiscal_year_service.py`
- `tests/ifrs/gl/test_fiscal_period_service.py`
- `tests/ifrs/gl/test_period_overlap_guardrails.py`
- `tests/ifrs/gl/test_period_guard_service.py`
- `tests/ifrs/gl/test_journal_service.py`
- `tests/ifrs/gl/test_ledger_posting_service.py`
- `tests/ifrs/gl/test_reversal_service.py`

The reusable invariants are: tenant-unique codes/numbers; posting only to an
open or explicitly reopened period; posting accounts only; positive one-sided
lines; debit equals credit in functional currency; one idempotent posting;
posted ledger lines are append-only; and corrections are linked, opposite-side
reversal entries rather than mutation or deletion.

## ERP payables source surface

### Models

- `app/models/finance/ap/supplier_invoice.py`
- `app/models/finance/ap/supplier_invoice_line.py`
- `app/models/finance/ap/supplier_invoice_line_tax.py`
- `app/models/finance/ap/ap_payment_allocation.py`
- `app/models/finance/ap/supplier_payment.py`

### Owning behavior

- `app/services/finance/ap/supplier_invoice.py`
- `app/services/finance/ap/payment_status.py`
- `app/services/finance/ap/posting/invoice.py`
- `app/services/finance/ap/posting/reversal.py`
- `app/services/finance/ap/supplier_payment.py`

### Parity evidence retained

- `tests/ifrs/ap/test_supplier_invoice_service.py`
- `tests/ifrs/ap/test_payment_status.py`
- `tests/ifrs/ap/test_ap_posting_adapter.py`
- `tests/ifrs/ap/test_supplier_payment_service.py`
- `tests/ifrs/ap/test_ap_multi_tenant_security.py`

The reusable invariants are: an invoice has lines and a due date no earlier
than its invoice date; standard invoices cannot be negative; credit notes are
credits rather than negative standard invoices; supplier document numbers are
tenant/supplier unique; only an approved document recognizes liability; credit
and settlement cannot exceed available/outstanding amounts; zero, partial and
full coverage are distinct; and every lookup is tenant scoped.

## Couplings and source defects not inherited

1. **Product transaction ownership.** Some ERP GL services commit/roll back,
   and AP keeps a mock-only commit shim. Module services mutate and flush the
   caller session only; `dotmac_kernel.db` remains transaction authority.
2. **Product aggregate imports.** ERP AP imports Supplier, purchase orders,
   goods receipts, inventory, fleet, tax, cost centres, projects, GL accounts
   and bank accounts directly. The module accepts typed facts and opaque
   references instead; no cross-module or cross-application ORM import ports.
3. **Posting coupled to document status.** ERP advances an invoice through
   `POSTED/PARTIALLY_PAID/PAID`, making an external GL consequence part of the
   invoice lifecycle. V1 recognizes liability at Payables approval; Accounting
   posting is separately observed and repairable.
4. **Payment execution inside AP.** ERP AP owns bank-backed payments and
   batches. This extraction owns obligations and settlement evidence only.
   Execution remains a treasury/payment decision and a non-transactional
   transport belongs behind outbox/Integrator.
5. **Fixed dimensions.** ERP journal lines hard-code business unit, cost centre,
   project and segment columns. Accounting owns an open definition/value model
   and line assignments, so a new dimension does not require a schema change.
6. **Weak physical immutability.** ERP describes posted ledger rows as
   append-only, but the portable contract makes it a database property: online
   grants plus triggers refuse update/delete. The same applies to period-event,
   credit-application, settlement and consequence evidence.
7. **Cross-tenant foreign keys.** Source child tables frequently identify only
   the parent id. Every target table carries `tenant_id UUID NOT NULL`, every
   internal relation carries `(tenant_id, id)`, and RLS is enabled and forced
   in the creating revision.
8. **Source idempotency mutation.** ERP retires a posting batch key after a
   reversal. A reversal does not make the original command un-happen. The
   kernel at-most-once ledger retains the command result; a new economic event
   uses a new key and points to the original journal.

## Target persistence

Accounting owns twelve tables in `mod_accounting`: account categories,
accounts, fiscal years, fiscal periods, dimension definitions/values, journal
entries/lines/line-dimensions, posted ledger lines/line-dimensions, and period
events. Posted ledger and period-event rows are immutable at the database.

Payables owns nine tables in `mod_payables`: supplier invoices/lines, credit
notes/lines, payment obligations, liability events, credit applications,
settlement observations, and accounting consequence receipts. Liability
events, applications and observations are immutable at the database.
Invoice/credit approval creates the liability or supplier-credit fact; an
obligation is a due-date allocation of an approved invoice, not a payment
instruction.

Both modules are tenant-only. The audit found no named control-plane consumer,
so no platform tables or selectable plane set are declared.

## Cutover sequence

ERP is source and first adopter for both modules. The products do not share a
database with Starter; ERP pins exact released packages, binds prerequisites
to its own tenant/role/idempotency providers, runs both independent lineages in
its database, backfills, and shadows while legacy GL/AP remain the sole
writers.

Accounting cuts over before Payables accounting publication. A sealed
Accounting switch compares chart identity, periods, journal/line snapshots,
source identity, ledger amounts/dimensions and reversal links while holding the
legacy writer boundary. ERP then retires its legacy GL mutation paths.

Payables backfills invoices, credits and obligations after Accounting is the
local ledger owner. It shadows liability, due-date, outstanding, credit and
settlement facts, then switches invoice/credit/obligation authority and retires
the legacy AP writers. The ERP assembly translates Payables consequences into
Accounting commands; neither module imports the other. Existing Procurement,
supplier-directory, tax, inventory and payment execution owners remain on
their own sides of typed adapters.

## Verdict

Proceed product-first with ERP as the sole behavioral source, two separate
tenant-only modules, and an assembly-owned consequence adapter. Do not merge
them into `dotmac-finance`, do not move payment execution into Payables, and do
not let a source document or imported identifier become a second ledger
writer.
