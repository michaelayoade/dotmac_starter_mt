# Changelog — dotmac-money-flows

## 0.1.0a3 — unreleased (C2b: several deductions on one MANUAL confirmation)

Source only; one additive migration. a2 is released.

- `SettleConfirmationV2` (subclass of V1, so V1 callers are unchanged) carries a tuple
  `deductions` of `SettleWithholdingV1`, for example withholding tax plus agency stamp
  duty, each a different Billing frozen withholding component. Every deduction is
  accepted, in order, through Billing's `accept_withholding_discharge` before the
  whole-invoice pass, so an invoice is discharged by cash plus all accepted deductions
  and the balance still pays whole invoices only, oldest first, skipping misfits.
- The same component (or the same retry key) twice is refused before any owner write
  as `duplicate_deduction_component`. Deductions stay MANUAL-only
  (`withholding_requires_manual_source` otherwise); no source is widened.
- `mf_0003_settle_discharge_list` adds nullable JSON `withholding_discharge_list_ref`
  (every discharge reference, in order). `withholding_discharge_ref` keeps the first
  one; no backfill. `SettleInstanceV1.withholding_discharge_refs` (new field with a
  default) reads the list, or the single legacy reference for an older row.
- No Billing change. MED-2 was checked and needs no code: every deduction is accepted
  inside a cash confirmation and the balance pass already runs after it.

## 0.1.0a2 — unreleased (GAP-4: the X1 receivables reader)

Source only. a1 is being published; this adds one stateless module on top of it.

- `receivables.BillingReceivablesReader` (X1): implements Collections'
  `ReceivablesReader` over Billing's sealed document exposure (native V1 and imported
  V2) and one caller session. No table, migration, event, lock or clock. It maps the
  exposure to `ReceivableObservationV1` with `subject_ref = "billing-account:{uuid}"`
  and adds `access_hold_from` = `due_at` + the account's `postpaid_grace` read at the
  due date (a terms change that takes effect later cannot move it). A prepaid
  account's open exposure is `PositionUnavailable("not_postpaid")`; an unverified due
  date carries no due or hold instant; an unknown document is `PositionUnknown`; a
  source owner other than Billing is an authority mismatch. Any other Billing refusal
  raises, so the caller's event delivery retries.
- `BillingReceivablesReader.terms_floor_at` (typed input, the flip instant entered at
  ship time): terms are read at the later of the instant and the floor, because Billing
  refuses back-dated terms and an item issued or due before the flip has none. If none
  exist even then the reader returns `PositionUnavailable("account_terms_missing")`
  (no poison message, no queue); any other Billing refusal still raises.
- Depends on `dotmac-collections >=0.1.0a3` (contract classes only; no Collections
  function is imported). Own-commit allowlist widening: `receivables.py` may read
  `get_effective_account_terms`, `ReceivableExposureV1/V2` and `DueDateBasisStatus`.
- Version surfaces move to `0.1.0a2` (pyproject, manifest, package, lock).

## 0.1.0a1 — unreleased

Source only; not published, not composed into any assembly.

- Tenant-plane manifest (`money_flows` / `mflows` / `mod_mflows` / `mf`), the
  instance-table allowlist and the Settle outbox event types.
- Migration `mf_0001` creates `settle_instances` with forced row-level security,
  no DELETE grant for the runtime role and a trigger that holds the whole
  transition graph: insert only as settled, refused or unapplied at version 0;
  refused to refused, settled or unapplied; unapplied to resolved only; settled
  and resolved terminal; every update advances `version` by exactly one; identity
  columns and an accepted `settlement_id` never change. A resolved row keeps its
  settlement and reason code.

- F1 Settle: `settle_confirmation`, `record_settle_refusal`, `retry_settle`,
  `get_settle_instance` and `list_settle_attention`, with the typed contracts, the
  `SettlementTargetResolver` and `SettlePolicyReader` ports and the
  `SettleRefused` error. Everything runs in the caller's transaction; no commit,
  rollback, savepoint, clock or timer. The row is written once at its outcome and
  a replay returns it with no owner call. The target comes only from the payment
  intent. Manual entry and transfer proof are off by policy default.
- Posting instant: Billing's claim and postings use the Payments cash
  observation's `observed_at`; the settle command carries no caller instant.
- No partial payments (C2a): cash always lands as available credit and the
  balance pays whole invoices only, via Billing `apply_available_credit`
  (Settle is its only allowed flows caller). A short bound payment stays as
  balance with the invoice open (`settled`, nothing applied); excess and
  deposits pay other open documents oldest due first, skipping any that does
  not fit. Accepted withholding is posted before the whole-invoice test.
- `list_settle_attention` pages by a `(created_at, id)` keyset cursor.
- Depends on `dotmac-billing >=0.1.0a12` and `dotmac-payments >=0.1.0a4`.
- A database test applies the schema vocabulary rule to the live `mod_mflows`
  catalog.

- GAP-L1: `on_account_credit_changed` pays a prepaid account's pending charges when
  credit arrives without a deposit (opening lot, admitted held partition, reversed
  refund or allocation), routed for every `billing.receivable.position.v2`. Dated
  `max(due_at, newest credit line since due_at)`. New contracts
  `AccountCreditChangedV1` / `CreditRecheckOutcomeV1`, event
  `money_flows.funded_charge.credit_recheck.v1`, and
  `record_funded_charge_attention(continue_credit_recheck=True)`. The account
  statement read moves to the private `_credit_dating` module (read-only allowlist
  entry); Settle is unchanged. No Billing change, no migration.

### Before release

- Measure the oldest-due-first page walk of `list_open_receivable_documents`
  under the account lock on an ST-4-scale account (many open invoices). No such
  fixture exists in the repository yet (the opening scale fixture is a census,
  not an account's open documents), so none was built here.
- Raise the kernel floor to the kernel release that carries the `money_flows`
  migration-namespace reservation.

- Migration `mf_0002` creates `funded_charge_instances` (F2 FundedCharge) with
  forced row-level security, no DELETE grant and a guard trigger: insert only as
  pending, discharged or attention_required at version 0; pending to discharged,
  attention_required or withdrawn; attention_required to attention_required,
  pending, discharged or withdrawn; discharged and withdrawn terminal; version
  advances by exactly one; identity columns and recorded discharge references
  never change. There is no deadline, timer or cancellation: `deadline_at` and
  `resolution_ref` exist only as common columns and CHECKs pin them NULL.
  `mf_0001` is unchanged. No flow behaviour yet.
- `charge_ref` is capped at 1024 bytes (a CHECK on `octet_length`, not on
  characters). `charge_ref_for` refuses a longer identity with `ValueError` and
  writes no row, so a charge source must keep its identity short.

### F2 FundedCharge (D2)

- `start_funded_charge`, `on_settlement_settled`, `record_funded_charge_attention`,
  `retry_funded_charge`, `get_funded_charge_instance`,
  `list_pending_funded_charges` and `list_funded_charge_attention`, with the
  typed contracts, the `RatedChargeSource`, `FundedChargePolicyReader` and
  `FundedChargePorts` ports and the `FundedChargeRefused` and
  `FundedChargeStepFailed` errors. Everything runs in the caller's transaction;
  no commit, rollback, savepoint, clock or timer.
- Prepaid is pay as you go. A due charge the balance covers in full is invoiced
  (Billing `issue_rated_invoice`) and paid from available credit
  (`apply_available_credit`) in one transaction: `discharged`. Otherwise it is
  held `pending`: nothing in the ledger, no deadline, no cancel. Whole charges
  only, oldest first, skipping any that does not fit.
- The contract type is read from Billing (`get_effective_account_terms`) at the
  instant the period would start. A postpaid account is refused at start
  (`not_prepaid`, no row); a charge that has become postpaid while pending is
  `withdrawn` (`contract_type_changed`) with nothing posted. A charge the source
  no longer holds is `withdrawn` (`charge_withdrawn`).
- The prepaid period starts when it is paid. The instant is the due date at
  start, `max(due_at, settlement.observed_at)` on re-evaluation and the same
  rule over the recorded funding settlement on retry. The charge source builds
  the `IssueRatedInvoiceV3` for that instant; F2 refuses a command that is not
  the charge's own identity, account and total with a verified period starting at
  the instant (`charge_command_mismatch`, `attention_required`, no owner write).
  The charge identity must not depend on the period dates, and the source (not
  flows) schedules the next charge.
- Access signal (D-F2-7): the service is off exactly while a `pending` row exists
  for it; `attention_required` never counts. F2 emits
  `money_flows.funded_charge.access.v1`, shaped like the Service Access Policy
  `RecordAccessInputV2` (signal PREPAID_DEPLETED), only when the state changes.
  Its `source_version` is the sum of `version + 1` over the service's rows under
  a service advisory lock, so consecutive charges of one service always rise. F2
  must be the only PREPAID_DEPLETED writer.
- `reevaluation_batch_cap` (policy default 20) bounds the pending rows examined
  per transaction. The rest continue through `money_flows.funded_charge.reevaluate.v1`,
  which the assembly routes back to `on_settlement_settled`. The continuation
  carries a keyset cursor after the last row examined, so a row skipped for lack
  of funds is never examined again by the chain and the chain always ends.
- New event types: `funded_charge.discharged.v1`, `.attention.v1`, `.access.v1`
  and `.reevaluate.v1`, all in the manifest.
- Depends on `dotmac-billing >=0.1.0a13` (account terms) and
  `dotmac-service-access-policy >=0.1.0a2` (contract classes only).

- Contract type and identity are decided before funds: a pending or retried row
  whose account is no longer prepaid at the period start is withdrawn whatever
  the deposit, and a charge the port reports under another key or account is
  `attention_required` (`charge_command_mismatch`) with no owner write; the funds
  check always uses the row's own account. A charge-source failure on one row is
  a `FundedChargeStepFailed` for that row (`charge_source_error`).
- `funded_charge_instances` rows are never pruned: the access `source_version`
  is a sum over a service's rows, so deleting or archiving rows would lower it.

- F2 D3: `reevaluate_account_terms` and `AccountTermsChangedV1`, a per-account
  terms re-check. Each pending row of the account is gated at `max(due_at, at)`:
  no longer prepaid is `withdrawn/contract_type_changed` (service resumes), a
  vanished charge is `charge_withdrawn`, a key or account mismatch is attention.
  It never pays or issues, leaves `attention_required` rows alone, is idempotent,
  takes locks in the `on_settlement_settled` order and continues over the batch
  cap with the new `money_flows.funded_charge.terms_recheck.v1` event. No
  migration, no new table; `funded_charge.v1` is unchanged.
- F2 N1: `start_funded_charge` reads the account's contract type after taking the
  account lock, not before, so a terms switch committed while a start waited is
  refused (`not_prepaid`) instead of leaving a pending row after the re-check.
  Lock order unchanged; no migration. A start after the re-check for a charge due
  before the switch still holds pending: the caller re-runs the re-check whenever
  a start returns pending (replays included), with `at` = the latest terms
  version already in effect, never a future one (README). A postpaid start is
  still refused before any lock.

### Release gates (F2)

- Billing `0.1.0a13` must be published before money-flows `0.1.0a1`. This joins
  the kernel `0.1.0a110` floor, the L6 deposit page-walk measurement and the
  reseller-scale re-evaluation test (many pending rows on one account).
- Not covered by a test here: an opening-lot credit funding a charge (Billing
  admits opening lots through a sealed window under a restricted role, which the
  scratch fixture does not provision).

- GAP-3: `apply_balance_on_invoice_issued` runs the C2a pass when Billing issues a
  postpaid invoice (account lock first; gates read after it; prepaid and
  `deposit_application=none` are no-ops). No row, event or attention item; owner
  failures raise and a replay posts nothing. Settle may now read account terms and
  funding availability (allowlist widened from FundedCharge only), and the
  account statement (Settle only). Applications are dated the latest of the two
  invoice dates and the newest cash received since (spent or not); the source
  reference carries the document's Billing `source_version`; faults raise the
  new `BalancePassFailed`. An overflowing statement window is split (newer half
  first) rather than failing.
- GAP-5: F2 takes the invoice period from the Billing account terms. The period is
  built from day or month units counted from its own start in the policy's billing
  time zone, clamped to the month end (private `_service_period`); F2 also requires
  `advance`, `due_at == issue_at` and the policy time zone. `FundedChargePolicyV1`
  gains `proration` (`actual_calendar_days` default, or `none`) and
  `billing_timezone` (default `UTC`); `RatedChargeV1` gains optional
  `period_starts_at`/`period_ends_at` for a prorated charge (full price in `total`,
  amount due by actual calendar days, half-up once). No day left is withdrawn as
  `period_elapsed`. No migration, no Billing change; the flows stdlib allowlist adds
  `zoneinfo`.
- GAP-5 review: every F2 command reads the policy through one check that refuses
  an unknown `proration` value or time zone (`ValueError`, nothing written);
  `retry_funded_charge` without a funding settlement dates the charge at the newest
  credit since `due_at`; the flows architecture rules ban `zoneinfo.reset_tzpath`,
  `clear_cache` and `from_file`.
