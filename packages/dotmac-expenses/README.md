# dotmac-expenses

`dotmac-expenses` owns tenant expense requests, incurred-expense claims,
category and receipt meaning, effective-dated limit evaluation, guarded
submission/decision transitions, and derived reimbursement eligibility.

It does not own Party/employee identity, approval quorum, stored bytes,
numbering, work orders/projects, advances/cards, GL accounts, journals, bank
details or payment initiation. Receipt rows reference an
opaque file UUID. Approval transitions carry an opaque decision reference.
An eligible claim is ready to be presented to Finance; it is not evidence of
payment.

The package is an installable tenant-plane module. Its `ex` lineage owns
`mod_expenses`. Services require an explicit `TenantScope`, mutate and flush,
and never commit or roll back. Draft revisions replace the complete request or
claim line snapshot through that same service owner; submitted lines cannot be
edited. See `EXTRACTION.toml`, ADR-0047 and
`docs/inventories/expenses-sources.md` for the product-first boundary.

## 0.1.0a2: the claim liability

The declared `0.1.0a2` successor adds the staff claim liability lifecycle
(ADR-0047 amendment 2026-11-19), with additive migration
`ex_0002_claim_liabilities`:

- `accept_claim_for_reimbursement` turns one APPROVED, eligible claim into a
  `ClaimLiability` (Dr each line's expense account and tax, Cr claims payable);
  every account ref is an explicit command field frozen in rows. The claim's
  own status stays APPROVED, and `withdraw_claim_approval` is refused while a
  live liability exists.
- States: OPEN, PARTIALLY_SETTLED, SETTLED and REVERSED (from OPEN only).
  Status is derived from the amounts after every transition and held by database
  CHECKs; `amount` never changes, the projections `amount_settled` and
  `amount_adjusted` are guarded by a column grant and a trigger.
- `record_claim_settlement`, `reverse_claim_settlement` (bank return; reopens),
  `adjust_claim_liability` (signed, audited, forward corrections) and
  `reverse_claim_acceptance`, each in `execute_once` under a permanent unique
  row. No second person is compulsory; `require_distinct_actor` is the caller's
  per-tenant policy.
- Pure, sealed consequence builders (`consequences`) and
  `record_claim_accounting_receipt`, which rebuilds the consequence and compares
  the fingerprint. A settlement can be returned only after its receipt exists.
- Reads from rows: `claimant_claims_balance`, `claims_payable_total`,
  `open_claim_liabilities`.
- `expenses.claim_input_vat` (`none` default, `immediate` to the immediate
  input-VAT account `1440`) is declared by `claim_input_vat_spec()` for the
  adopting assembly to register and read; the value is frozen on the liability.
- `admit_historical_claim_liability` admits a legacy posted, partly
  unreimbursed claim with no GL, through a trusted verifier that runs in an
  always-discarded savepoint (mirrors payables `admit_historical_settled_invoice`).

Payout batches, the accept-open step and the reclass journal belong to the
adopting assembly. This declaration is not publication evidence.
