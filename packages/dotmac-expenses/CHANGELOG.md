# Changelog

## 0.1.0a2 — declared, unpublished

Source successor to published 0.1.0a1 (ERP v2 cutover). It adds the staff claim
liability lifecycle; publication goes through the authorized release lane with a
verified release record. This entry is not publication evidence.

- Add `claim_liabilities` and its lines, settlements, bank-return reversals,
  adjustments, acceptance reversals, historical receipts and accounting
  receipts (additive migration `ex_0002_claim_liabilities`; `ex_0001` bytes
  unchanged; downgrade refuses once any row exists).
- Add `accept_claim_for_reimbursement`, `reverse_claim_acceptance`,
  `record_claim_settlement`, `reverse_claim_settlement`,
  `adjust_claim_liability` and `record_claim_accounting_receipt`; status is
  derived from the amounts and held by CHECKs.
- Add the sealed consequence builders (Dr expense / Cr claims payable on
  acceptance), the per-employee balance and the claims-payable control total.
- Add the `expenses.claim_input_vat` setting declaration (`none` default,
  `immediate` to account 1440), frozen on the liability at acceptance.
- Add `admit_historical_claim_liability` (0 GL, verifier-bound).
- `withdraw_claim_approval` now refuses (`claim_has_liability`) while a live
  liability exists.
- `ExpensesError` gains an optional machine-readable `code`.
- The kernel floor rises to a103 (`semantic_encoding`).

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32478753869`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Add tenant expense categories, versioned policy rules and append-only
  evaluation evidence.
- Add expense requests/lines, claims/lines and receipt metadata around opaque
  file references.
- Add guarded submission and approval-observation lifecycle plus derived
  reimbursement eligibility.
- Add canonical complete-snapshot revision for draft requests and claims,
  including rejected claims returned to draft.
- Add the `ex` migration lineage with forced tenant RLS and immutable evidence
  guards.
