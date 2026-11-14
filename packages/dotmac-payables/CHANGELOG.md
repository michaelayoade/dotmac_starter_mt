# Changelog — dotmac-payables

## 0.1.0a4 — unreleased

Publication is authorized by Michael (register v1279) but happens only through
the release lane after review. No assembly adopts this candidate by this change.

- Add the supplier-payment lifecycle as governed commands:
  `create_supplier_payment`, `approve_supplier_payment`,
  `record_supplier_payment`, `cancel_supplier_payment`. State machine
  DRAFT -> APPROVED -> RECORDED, or CANCELLED from DRAFT/APPROVED. The approver
  must differ from the preparer; the recorder must differ from both. Every command
  is idempotent through the kernel `execute_once` and tenant scoped.
- Allocate a payment to open obligations of one supplier and currency; the sum
  committed to DRAFT/APPROVED payments can never exceed the outstanding balance.
  Recording settles each allocation through `record_settlement` in the caller's
  transaction (all or nothing) and refuses a reused bank reference.
- Create, approve and record refuse an allocation whose invoice has neither an
  accounting receipt nor a historical receipt (`liability_not_in_ledger`: paying
  it would debit a control account that was never credited), and whose invoice
  exchange rate differs from the payment's (`fx_difference_unsupported`: realised
  FX is not modelled in this release). Amounts with more than six decimal places
  are refused rather than rounded. Record locks obligations in obligation-id order.
- Optional withholding-tax lines reduce the cash credited, not the liability
  discharged.
- `build_payment_accounting_consequence` exposes the balanced, provider-neutral
  GL consequence of a RECORDED payment: Dr supplier liability account(s) for the
  gross, Cr the cash account for the net, Cr each withholding-tax account.
  `record_accounting_receipt` accepts document kind `supplier_payment`. Payables
  writes no GL, moves no money and calls no provider; RECORDED is an assertion
  that money left through an external channel.
- Add `pa_0004_supplier_payments` (additive: `supplier_payments`,
  `supplier_payment_allocations`, `supplier_payment_withholdings`, forced RLS,
  lifecycle/append-only triggers, evidence and separation-of-duties CHECKs). No
  released table or column changes. Its downgrade refuses once any supplier
  payment exists (forward correction only), as `pa_0003` does for receipts.
- Kernel floor unchanged at a103; no new kernel API.

## 0.1.0a3 — 2026-11-12

Published and tagged as `dotmac-payables-v0.1.0a3`.

- Add narrowly verified historical POSTED, wholly unpaid STANDARD invoice
  admission. Preserve original nullable actors and posting time, record a
  permanent immutable receipt, and recognize the full original obligation
  without approving again or producing duplicate imported GL consequences.
- Require kernel a103 for versioned cv1 semantic evidence fingerprints.
- Historical admission refuses a `scoped_session`; callers must pass a plain `Session`.

## 0.1.0a2 — 2026-11-10

Published, tagged as `dotmac-payables-v0.1.0a2` from exact protected-main
revision `a9b777d6` by release run `42883090361`.

- Historical or imported supplier invoices and credit notes whose
  supplier-issued document number was never captured may persist that absence
  as NULL. New documents created through the service contracts still require
  the supplier document number; internal document numbers remain required.
- Adds `pa_0002_optional_supplier_doc`, which makes both persisted
  `supplier_document_number` columns nullable.

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32479337788`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Product-first supplier-liability owner extracted from ERP behavior.
- Invoice, credit, obligation, credit-application, settlement-observation and
  accounting-consequence/receipt contracts.
- Independent `pa` migration lineage in `mod_payables` with forced RLS.
