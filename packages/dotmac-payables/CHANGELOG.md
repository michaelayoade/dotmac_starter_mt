# Changelog — dotmac-payables

## 0.1.0a7 — unreleased

Publication happens only through the release lane after review and Michael's separate
release go. No assembly adopts this candidate by this change. Billing is untouched.

D-9 (Michael, 2026-11-17/18: two-person approval is never compulsory; separation of
duties is an optional per-tenant policy, default off).

- `separation_of_duties: bool = False` (keyword-only) on `create_supplier_payment`,
  `approve_supplier_payment`, `record_supplier_payment` and
  `reverse_supplier_payment`. The default is OFF per Michael's no-compulsory-two-person
  ruling: a caller that does not name it gets no separation, so one operator can run
  the flow. A caller that wants two-person approval passes `True`; an assembly passes
  its tenant policy explicitly. The `separation_enforced` column default stays `true`
  so existing rows remain strict; new rows take whatever the service passes.
- `create_supplier_payment` stamps the policy in force on the row
  (`supplier_payments.separation_enforced`, NOT NULL, default true). Approve, record
  and reverse enforce the separation when `row.separation_enforced or
  separation_of_duties` (the stricter value wins). The flag is part of each command's
  idempotency fingerprint; a replay of `create_supplier_payment` for an existing
  number under a different policy is a `Conflict`.
- Migration `pa_0007_separation_policy`: adds the column, rewrites
  `ck_supplier_payments_sod` as `NOT separation_enforced OR (<a6 predicate>)`, and adds
  the `supplier_payments_guard_policy` UPDATE trigger that refuses any change of the
  stamp. Downgrade refuses while any row has `separation_enforced = false`.
- WIDENING, stated plainly: the owner's separation-of-duties rule and the
  database CHECK `ck_supplier_payments_sod` are relaxed PER ROW when that row's stamp
  is false. Rows stamped true (every pre-a7 row, and every row prepared with an
  explicit `True`) are enforced exactly as in a6, in the service and in the database.
- Forbidden and not done: no synthetic distinct actor strings; the audit trail names
  the one real actor.
- DISCLOSURE (explicit widening under section 8b): with the tenant policy ON, the
  database backstop is only as strong as the INSERT stamp. The database cannot see the
  tenant policy, so any `app_user`-level writer (or a buggy assembly path) can insert a
  DRAFT stamped `separation_enforced = false` and take it through single-handed. The
  a6 unconditional CHECK prevented that. The assembly (ERP) is responsible for passing
  the policy explicitly, reading back `separation_enforced` after create and refusing a
  mismatch; that control is not built here.
- `separation_of_duties` must be a real `bool`: any other value (None, 0, "") raises
  `TypeError` before anything is written, so a falsy non-bool can never relax a row.
- The flag joins an idempotency fingerprint only when it is `False`, so enforced
  (`True`) calls keep their a6 fingerprint bytes, replays and the reversal event's
  fingerprint.
- `pa_0007` downgrade takes `LOCK TABLE ... IN SHARE ROW EXCLUSIVE MODE` before it
  checks for relaxed rows.

## 0.1.0a6 — unreleased

Publication is authorized by Michael (register v1516, D-9 and the a6 VAT-release and
WHT work) but happens only through the release lane after review. No assembly adopts
this candidate by this change. Billing is untouched. No second person is required by
any new command (Michael, 2026-11-17: maker-checker is optional and off).

- Cash-basis release of deferred input tax at payment. `declare_tax_deferral` declares,
  as an immutable owner fact (one row per tenant and deferral account, never metadata),
  that tax booked to a deferral account is released to a release account as the
  invoice is paid. `record_supplier_payment` now freezes one
  `supplier_payment_tax_releases` row per (invoice, deferral account): the proportional
  share of what remains deferred, rounded half up to the policy's minor units, and the
  exact remainder on the final payment (`deferred_tax_release`,
  `invoice_deferred_tax_position`). The payment consequence appends Dr release / Cr
  deferral per row to the a5 lines; the reversal mirrors them. A payment with no rows
  is byte-identical to a5 (fingerprints pinned). A credit note carrying deferral tax
  must name its invoice and cannot exceed what remains deferred
  (`deferred_tax_credit_requires_invoice`, `deferred_tax_credit_exceeds_remaining`).
  A negative position is a data fault and is refused (`deferred_tax_position_negative`).
- `admit_historical_tax_deferral`: verifier-bound, no GL. Records the deferral already
  released before admission on a settled historical invoice, so its first payment here
  cannot over-release. Without it, such a payment is refused
  (`historical_deferral_unproven`). Basis-neutral: `released_before_admission`, and
  opaque `releases` evidence whose meaning the verifier defines. Domain
  `payables.historical-tax-deferral.v1`.
- Withholding at payment: a withholding account that is the payment's cash account, an
  allocated invoice's liability account or a declared deferral or release account is
  refused at creation and re-checked at approval and recording
  (`withholding_account_conflict`). The consequence shape is unchanged.
- `admit_historical_supplier_credit` (R5b): verifier-bound, no GL. Admits a settled
  historical invoice that was overpaid as an APPROVED credit note (no lines, no tax),
  an immutable `historical_credit_receipts` row with its settlements, and a
  `historical_credit_recognized` liability event of minus the credit. Refused unless the
  settlements exceed the total by exactly the credit (`historical_credit_not_overpaid`)
  and the carried supplier control amount equals minus the credit
  (`legacy_gl_does_not_hold_credit`). Such a credit has no accounting consequence or
  receipt (`historical_credit_has_no_consequence`), and its invoice cannot be recognized,
  admitted or posted. `apply_credit_note` applies it unchanged. Domain
  `payables.historical-supplier-credit.v1`.
- Invoice and credit-note consequences skip a zero-value line's amount entry (a tax-only
  line keeps its tax entry) and refuse an empty, unbalanced or zero-valued entry set
  (`consequence_entry_invalid`). Stored lines are unchanged; fingerprints are unchanged
  for every document without a zero line.
- `PayablesError` accepts an optional keyword `code`; every new refusal sets it and
  carries it in parentheses in the message. Existing raises are unchanged.
- Add `pa_0006_tax_release_hist_credit` (additive: `tax_deferral_policies`,
  `supplier_payment_tax_releases`, `credit_note_tax_splits`,
  `credit_application_tax_releases`, `historical_tax_deferrals`,
  `historical_credit_receipts`, `historical_credit_settlements`; forced RLS,
  append-only triggers, SELECT and INSERT grants only). No released table or column
  changes. Its downgrade refuses once any row exists. The revision id is shortened
  from the design's `pa_0006_tax_release_history_credit`, which exceeds the 32-character
  `alembic_version.version_num`.
- Credit notes against paid invoices and credit applications (spec rev 3, section 11).
  Credit kind is structural: a CASH credit is a historical overpayment
  (`historical_credit_receipts`); every other credit is a DOCUMENT credit. At approval
  a credit's tax on a deferral account is split, frozen in `credit_note_tax_splits`:
  min(tax, still deferred) reverses the deferral account and the rest reverses the
  release account (the share already claimed); the credit consequence emits both
  (a credit whose split puts nothing on the release account is byte-identical to a5).
  Refused: tax above the invoice's net tax (`credit_tax_exceeds_invoice_tax`), a line
  taxed to a release account (`credit_tax_on_release_account`). Applications release
  deferred tax into `credit_application_tax_releases`: a CASH credit like a payment
  (proportional, exact remainder at final settlement); an own DOCUMENT credit only at the
  final settlement, which releases exactly the remainder; a DOCUMENT credit set off against
  another invoice that still defers tax is refused (`credit_setoff_on_deferred_invoice`).
  `build_credit_application_accounting_consequence` (Dr release / Cr deferral) and receipt
  kind `supplier_credit_application`; none when nothing was released
  (`credit_application_has_no_consequence`). Reversing a payment after a credit settled the
  invoice's VAT is refused (`payment_reversal_blocked_by_credit_vat`); reverse the credit
  first. Owner invariants at every write: R >= 0, Claimed >= 0, credit tax <= D
  (`deferred_tax_position_negative`). No un-apply. `deferred_tax_credit_exceeds_remaining`
  is removed.
- An external settlement observation takes the invoice lock and is refused while the
  invoice still defers tax (`settlement_observation_on_deferred_invoice`), because it
  lowers the balance without releasing; payments are unaffected. The payment owner string is reserved: the public `record_settlement` refuses it (`settlement_owner_reserved`) and `record_supplier_payment` uses a private path. A credit application
  validates its amount before any release is measured.
- Review hardening: a historical-credit replay with another credit number, evidence
  reference or admitting actor is a conflict; credit approval refuses deferral tax on
  unproven settled history (`historical_deferral_unproven`); `apply_credit_note` and
  `reverse_supplier_payment` take the invoice lock (payment, obligations, invoices in
  id order); both historical admission paths queue on a PostgreSQL advisory lock keyed
  by (tenant, legacy settlement id).
- Kernel floor unchanged at a103.

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
