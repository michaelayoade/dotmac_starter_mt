# Changelog — dotmac-payables

## 0.1.0a5 — unreleased

Publication is authorized by Michael (register v1302 rulings; W9) but happens only
through the release lane after review. No assembly adopts this candidate by this
change. Billing is untouched.

- Governed reversal of a RECORDED supplier payment (`reverse_supplier_payment`):
  RECORDED -> REVERSED, terminal. A reason and reversal date are required; the
  reverser must differ from the payment's recorder and approver; the payment's own
  accounting receipt must exist (`payment_not_in_ledger`). Every allocation is
  returned to its obligation's outstanding balance (status recomputed, never above
  face) and a positive `settlement_reversed` liability event is appended; existing
  settlement observations are never edited. `build_payment_reversal_accounting_consequence`
  exposes the balanced GL reversal (Dr cash net, Dr each withholding account, Cr
  liability); `record_accounting_receipt` accepts `supplier_payment_reversal`. The
  original payment consequence stays rebuildable after reversal. Idempotent.
- Settled historical admission (`admit_historical_settled_invoice`): the
  verifier-bound admission of a POSTED STANDARD invoice with legacy settlements.
  The obligation is created at full face; each settlement becomes an immutable
  `historical_settlements` row (amount, date, legacy payment reference, withholding
  counted as settled) and a negative liability event; the outstanding balance is the
  residual proved exactly from the settlements. No accounting consequence (legacy AP
  control already holds the residual). Refused: FX or currency mismatch
  (`fx_difference_unsupported`), over-settlement, totals or residual not proved by
  the settlements, duplicate legacy settlement ids, settlements before the invoice
  or after admission, prepayment, no settlements, and a fully settled invoice (no
  open obligation). Uses the same receipt table, so the ledger guard and the
  supplier-payment lifecycle treat the residual like any admitted historical
  liability. The unpaid admission is unchanged and shares one implementation.
- Settled admission ledger binding: every `HistoricalSettlement` and
  `historical_settlements` row carries the legacy payment journal reference and the
  imported destination journal reference (both required). ERP verifier contract: read
  the restored flip-window legacy snapshot read-only; a settlement is one legacy
  payment allocation on a payment whose status is posted, whose journal is POSTED in
  the legacy GL and whose imported journal exists; `source_row_fingerprint` is the
  sha256 of the canonical legacy allocation row; the residual is cross-checked
  against the legacy GL control-account lines for that invoice. A legacy settlement
  id is unique per tenant across all admissions (one allocation settles one invoice).
  Money is exact two-decimal (correct for NGN); other minor-unit currencies are
  refused until the contract is widened.
- Reversal ceiling: a reversal may not reopen more than the obligation's face less
  its historical settlements and credit applications.
- Recognition of an imported APPROVED invoice (`recognize_approved_invoice`): writes
  the `invoice_recognized` liability event and the obligations from the invoice's own
  schedule exactly as `approve_invoice` would, with no status regression and no edit
  to imported evidence. Allowed only for an APPROVED invoice with no recognition
  event, obligation, accounting receipt or historical receipt. The evidence
  reference, actor and approval reference are kept in the permanent
  `invoice_recognitions` row, guarded by a database trigger; the assembly binds the
  reference to its reviewed list and enforces who may act. A trusted verifier
  (`ApprovedInvoiceRecognitionVerifier`, same savepoint-contained read-only model as
  admission) must prove from snapshot evidence that the legacy invoice was APPROVED
  and never POSTED (no legacy journal entry, posting date or GL lines); the evidence
  and its fingerprint are recorded on the permanent row, and the call is refused
  without it. Idempotent; other evidence for an already recognized invoice conflicts.
- Add `pa_0005_reversal_settled_history`: nullable reversal columns on
  `supplier_payments`; the status, evidence and separation-of-duties CHECKs and the
  lifecycle trigger are replaced by supersets of the pa_0004 ones (RECORDED ->
  REVERSED only; approval and recording evidence immutable); new tables
  `invoice_recognitions` and `historical_settlements` with forced RLS and
  append-only triggers. No released data is rewritten. Downgrade refuses once a
  reversed payment, recognition or historical settlement exists.
- Kernel floor unchanged at a103.

## 0.1.0a4 — 2026-11-15

Published and tagged as `dotmac-payables-v0.1.0a4` (release run 43678443005).

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
