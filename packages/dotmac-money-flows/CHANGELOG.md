# Changelog — dotmac-money-flows

## 0.1.0a5 — source candidate, not yet published (W04 PR-C: withholding triggers verified against Billing)

Source only. a4 is an unpublished candidate and must be tagged `dotmac-money-flows-v0.1.0a4`
before this merges, so a4 ships at its reviewed source. No migration, no schema or grant change.
The Billing requirement floor rises to `dotmac-billing >=0.1.0a16` (Billing a16 adds
`get_withholding_discharge_record`).

- `apply_balance_on_withholding_released` re-reads the release with Billing's
  `get_withholding_release`; `apply_balance_on_withholding_accepted` re-reads the discharge with
  `get_withholding_discharge_record`. The published V1 request shapes are unchanged; the caller's
  values are checked, never trusted. Before the account lock a mismatch raises `BalancePassFailed`:
  `withholding_trigger_is_reversal` (an undo or reversal row is the trigger),
  `document_account_mismatch` (published a3 code), `withholding_trigger_mismatch` (document),
  `withholding_instant_mismatch` (`released_at` / `accepted_at` is not Billing's `recorded_at`; a
  naive value never matches).
- Dating floor comes from Billing: `recorded_at` for a release; the later of `recorded_at` and the
  discharge's ledger `occurred_at` for an accepted deduction. A trigger whose original was reversed
  later is not special-cased; Billing's fresh gates decide.
- Architecture allowlist: `get_withholding_release` and `get_withholding_discharge_record` are
  read-only names permitted in `settle.py` only.
- `apply_balance_on_withholding_released` is added to `settle.__all__` (the package export existed).

## 0.1.0a4 — source candidate, not yet published (GAP-4 follow-up: database faults redeliver)

Source only. a3 is published and tagged `dotmac-money-flows-v0.1.0a3`; released
`mf_0001`..`mf_0003` bytes are unchanged. No migration, no Billing requirement
change, and the `dotmac-collections >=0.1.0a3` floor stays (the Collections a4
change is internal to Collections).

- A charge-source read failure that is a `sqlalchemy.exc.DBAPIError` (a database fault) is
  re-raised so the message redelivers, instead of becoming a per-row
  `charge_source_error` step failure. Every other source error stays a per-row step
  failure, including ORM usage errors such as `NoResultFound`. Applies to the four reads: the issue command, the funded and terms re-checks, and retry.

## 0.1.0a3 — released, tag `dotmac-money-flows-v0.1.0a3` (C2b: several deductions on one MANUAL confirmation)

One additive migration. a2 is released and tagged.

- `SettleConfirmationV2` (subclass of V1, so V1 callers are unchanged) carries a tuple
  `deductions` of `SettleWithholdingV1`, for example withholding tax plus agency stamp
  duty, each a different Billing frozen withholding component. Every deduction is
  accepted, in order, through Billing's `accept_withholding_discharge` before the
  whole-invoice pass, so an invoice is discharged by cash plus all accepted deductions
  and the balance still pays whole invoices only, oldest first, skipping misfits.
- The same component (or the same retry key) twice is refused before any owner write
  as `duplicate_deduction_component`. Deductions stay MANUAL-only
  (`withholding_requires_manual_source` otherwise); no source is widened.
- `mf_0003_settle_discharge_list` adds nullable `withholding_discharge_list_ref` (`varchar(160)[]`)
  (every discharge reference, in order). `withholding_discharge_ref` keeps the first
  one; no backfill. `SettleInstanceV1.withholding_discharge_refs` (new field with a
  default) reads the list, or the single legacy reference for an older row.
- Refused before any write: a V2 confirmation setting both `withholding` and
  `deductions` (`withholding_and_deductions_both_set`), and a V2 deduction whose
  document is not the bound document (`deduction_document_not_bound`). A Billing
  refusal of one deduction propagates unchanged with a note naming its position and
  component (no amounts); the caller's transaction rolls every earlier deduction back.
- MED-2: a withholding certificate that arrives after the cash is accepted straight
  by Billing (Payments refuses a zero confirmation), so no settle pass follows it.
  `apply_balance_on_withholding_accepted(WithholdingAcceptedV1)` runs the same full
  pass as `apply_balance_on_invoice_issued` (lock-first order, same gates, whole
  invoices oldest first, misfits skipped), dated at the later of the usual dating
  rule and `accepted_at`, idempotent on the discharge id. Exposed only: wiring it to
  Billing's accepted fact is assembly work (W04).
- Q-F: `apply_balance_on_withholding_released(WithholdingReleasedV1)` runs the same
  full pass after Billing releases an expected-withholding component, dated at the
  later of the usual rule and `released_at` (the release instant), with source
  references `money-flows:settle:release:{release_id}:...`. The assembly calls it from
  delivery of `billing.withholding.released.v1` in a new transaction. Requires
  `dotmac-billing >=0.1.0a14`. No allowlist change.
- Otherwise no Billing change.

## 0.1.0a2 — released, tag `dotmac-money-flows-v0.1.0a2` (GAP-4: the X1 receivables reader)

Published after a1; this added one stateless module on top of it.

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

## 0.1.0a1 — released, tag `dotmac-money-flows-v0.1.0a1`

Published first; not composed into any assembly by Starter.

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
  migration-namespace reservation. MET: the floor is kernel `0.1.0a110`.

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

- Billing `0.1.0a13` must be published before money-flows `0.1.0a1`. MET: it was
  published before a1. The kernel `0.1.0a110` floor is also MET. The L6 deposit
  page-walk measurement and the reseller-scale re-evaluation test (many pending
  rows on one account) are not claimed met here.
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
