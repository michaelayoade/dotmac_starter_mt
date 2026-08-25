# ADR-0042: Payables owns supplier liabilities, not payment execution

- Status: Accepted
- Date: 2026-08-19
- Deciders: Michael
- Supersedes: none
- Related: ADR-0024 (application/module independence), ADR-0026 (approval is
  not the transition), ADR-0040 (Accounting owner),
  `docs/inventories/accounting-payables-sources.md`

## Context

ERP AP currently combines supplier invoices, purchase-order/goods-receipt
lookups, tax, inventory updates, GL posting, bank-backed payments and coverage
status. That aggregate demonstrates mature invoice/credit/liability behavior,
but copying it would make the shared package a second Procurement, Accounting,
Inventory, Tax and Treasury owner.

## Decision

### 1. One tenant-only payables owner

`dotmac-payables` owns supplier invoices and credit notes, their lines and
submission/approval/void lifecycle, recognized supplier liabilities, due-date
payment obligations, credit applications, and immutable observations of
settlement and accounting consequences.

An approved invoice recognizes a liability and materializes one or more
payment obligations whose amounts exactly total the invoice. An approved credit
note recognizes supplier credit. Applying that credit changes obligation and
available-credit projections without creating the economic credit a second
time.

### 2. Supplier, Procurement and Approvals remain separate

The supplier is an opaque Party/directory reference plus an evidence snapshot;
Payables cannot create or update supplier identity, compliance or bank details.
Purchase commitment and receipt references are opaque evidence supplied by the
assembly. Payables never updates an order or goods receipt.

Approvals decides approval; Payables owns the transition after validating a
non-empty approval reference. Draft editing stops at submission. Approved
documents are corrected by credit notes, never voided or rewritten.

> **Navigation note, 2026-08-24 (no decision change).** § 3 below leaves the
> disbursement owner unnamed on purpose. It is named for payouts by ADR-0061 § 1
> and its Amendment A1 (`PaymentService` in ERP, the sole interim owner) and
> scoped as a shared module by ADR-0063 (`PaymentInstruction`, gated). This
> record's § 3 is also the CONTROLLING statement where ADR-0047 § Context said
> "Finance/Payables owns … disbursement"; see ADR-0047 Amendment A1 for the
> six-owner split that supersedes that sentence.

### 3. Payment obligations are not payment instructions

Payables says what is owed, in what currency and by which date. It does not
choose a bank account, payment rail, provider, batch or execution time and does
not perform network I/O. A Treasury/payment owner performs disbursement and
submits a typed observation containing source identity/version/fingerprint,
amount and occurrence time. Payables deduplicates it, refuses changed replay or
over-settlement, appends evidence and updates only its obligation projection.

### 4. Accounting is a consequence through an assembly seam

Payables produces a typed, balanced accounting consequence containing opaque
account and dimension references. It never imports `dotmac-accounting` or
writes a journal. The adopting assembly translates that value into an
Accounting command and later records the returned journal/evidence reference.
That receipt is immutable and repairable. Failure to deliver the consequence
does not reclassify the invoice liability; reconciliation retries the missing
projection.

### 5. Evidence and replay are durable

Kernel idempotency owns at-most-once commands. Credit applications, settlement
observations and accounting receipts are append-only and protected by database
triggers/grants. Every internal foreign key carries tenant identity and every
table has forced RLS from its creating migration.

## Consequences

- ERP is the source and first adopter after Accounting authority has moved.
- The existing ERP payment/batch implementation is not ported into this module;
  it becomes a Treasury/payment candidate with a versioned settlement adapter.
- Tax computation, inventory valuation and purchase matching stay behind typed
  product adapters. Payables persists only the bounded evidence needed to
  validate the invoice decision and reconstruct the liability.
- Invoice status does not contain `POSTED`, `PARTIALLY_PAID` or `PAID`.
  Accounting delivery and payment coverage are separate facts; obligation
  status is derived from its own outstanding amount.

## Alternatives rejected

**Port ERP AP wholesale.** It would introduce direct imports and foreign keys
to Procurement, Inventory, Fleet, Tax, GL and Banking, violating independent
module ownership.

**Let Accounting own supplier invoices.** A general ledger records their
consequence; it does not own document validation, due schedules, supplier
credit or settlement allocation.

**Let Payables execute payments.** An obligation and a bank movement have
different controls, security boundaries and failure modes. Combining them
would put provider I/O inside a domain transaction and recreate a second
Integrator/Treasury engine.
