# dotmac-money-flows

Tenant-plane composition of money flows over the published owners. The package
holds instance state only: a state, the policy and definition version it was
decided under, an optional deadline, a reason code and opaque references to the
owners' records. It stores no amount, currency or balance, owns no timer, lease,
retry or scanner, and never commits or rolls back.

It declares the manifest, the instance-table allowlist, the `mf` migration
lineage (the Settle instance table) and the event-type constants, and ships the
first flow, F1 Settle: one confirmed payment becomes accepted cash and is applied
to a bound document, or to open documents oldest-due-first for a deposit. The
caller owns the transaction. The assembly supplies two ports (target resolution
and policy) and, around an owner refusal, rolls back and records the refusal in a
new transaction; a uniqueness violation on the confirmation is a replay.

The second flow, F2 FundedCharge, pays a due prepaid charge from account credit
(invoice and credit draw in one transaction) or holds it `pending` with nothing
in the ledger. The contract type comes from Billing's account terms, the charge
and its invoice command from an assembly-supplied `RatedChargeSource`, and the
service access signal (PREPAID_DEPLETED) leaves as a refs-only outbox event.

Only an assembly imports this package. The Starter reference app does not
compose it; the intended consumer is the Sub thin-client deployment.

FundedCharge rows are never pruned: the access signal's `source_version` is a sum
over a service's rows.

## Terms re-check (F2 D3) and the tenant-default runbook

`reevaluate_account_terms(db, scope=..., request=AccountTermsChangedV1(account,
at), ports=...)` re-checks one account's pending charges after its contract terms
changed. `at` is the `effective_from` of the terms version that caused the call;
it must be timezone-aware, and **the caller must not call before `at` has
passed** (flows reads no clock and cannot check this). Each pending row is
checked at `max(due_at, at)`; an account no longer prepaid withdraws the row
(`contract_type_changed`) and the service resumes. The re-check never pays or
issues anything, and `attention_required` rows are not touched. A repeat is a
no-op. The assembly routes `money_flows.funded_charge.terms_recheck.v1` back to
the same function when the batch cap leaves rows over.

**Start and re-check (N1).** `start_funded_charge` reads the account's contract
type only after it holds the account lock, so a switch to postpaid that commits
while a start waits is seen: the start is refused (`not_prepaid`) and leaves no
pending row. That closes the race, but not a *late start*: a charge due before
the switch's `at` that is first started after the re-check ran is still prepaid
at its `due_at`, so the start holds it `pending` and the service off, and the
re-check that already ran cannot see it. The caller (Sub-thin) must therefore
re-run `reevaluate_account_terms` for the account whenever a start returns `pending`, including a replayed `pending`
outcome (a duplicate that won the race), after the start's transaction commits.
Use `at` = the `effective_from` of the latest terms version already in effect
(`effective_from` <= now; tenant-default versions count for an account with no
own terms); skip the call if there is none. Never use a future switch's `at`: it
would withdraw a valid unfunded prepaid charge now. The call withdraws the row
and resumes the service.

Changing the tenant default contract type is a runbook event, not code in this
package: the caller re-checks each prepaid account that falls back to the default
(no own terms), one account per transaction, with `reevaluate_account_terms`.

## Balance pays new postpaid invoices (GAP-3)

`apply_balance_on_invoice_issued(db, scope=..., request=InvoiceIssuedV1(document_id),
ports=SettlePorts)` runs the deposit pass when Billing issues an invoice: every open
invoice of the account, oldest due first, whole invoices only, skipping any that do
not fit. It takes the Billing account lock first (the same order as F2 and Settle),
then reads each gate fresh: `deposit_application` is not `none` (the one setting for
"the balance pays invoices"; there is no separate switch), the new invoice is still
open, the balance is positive, and the account is postpaid at the invoice's issue
date (a prepaid account is an explicit no-op). It creates no flows row, no
attention item and no event; Billing's own allocation facts are the record.

A business no-op posts nothing and returns `BalanceAppliedOutcomeV1` with an empty
`applied_document_ids` and a `reason_code` (`deposit_application_off`,
`invoice_not_open`, `no_balance`, `account_not_postpaid`, `no_invoice_fits`). A
technical fault **raises**; nothing is written. `BalancePassFailed(code)` is the
flows fault (open-document list unreadable, the new invoice missing from the open
list while its balance is positive, a document of another account); Billing
exceptions propagate unchanged. Source references are deterministic
(`money-flows:settle:issue:{trigger}:{document}:{source_version}`, the document's
Billing `source_version`), so a replay of the same state never pays twice, a
reversal (which bumps the version) can be paid again by a rerun, and the next
invoice or deposit pass completes any missed work.

**Application date.** Each application is dated the latest of the new invoice's
issue date, the paid invoice's issue date and the newest cash received since those
dates, spent or not (the latest `settlement_accepted` statement line that adds
credit, from Billing's account statement over `[earliest base, 9999-12-31)`;
opening lots post none). Flows reads no clock. Billing caps one statement at 5000 posting groups; when
the window overflows (`statement_too_large`) flows splits it at the midpoint and
searches the newer half first, then the older, recursively, so the newest cash
wins and the pass never wedges on a busy account. Only a window that cannot be
split further (over the cap at a single instant) raises
`BalancePassFailed("statement_unsplittable")`.

Duties of the caller (Sub-thin):

- Route each Billing invoice-issued document fact (`billing.invoice.document.fact`
  v1/v2/v3 with `document_kind=invoice`, `document_state=issued`, `fact_version=1`)
  to this command, in its own transaction per event.
- Treat `BalancePassFailed` and Billing exceptions as retryable. Let the failure
  propagate so the transport rolls back and its event delivery retries
  automatically. A persistent failure (retries exhausted) is a technical
  ops alert, never a billing staff queue.
- Date postpaid invoices at the real issue instant. An application is dated the
  later of the new invoice's issue date and the paid invoice's issue date and
  flows reads no clock, so a back-dated `issued_at` (for example a period-end
  date set by a later run) would apply cash before it was received.
- Postpaid timing (advance or arrears) is the Billing account-terms setting
  (`postpaid_billing_timing`), not fixed here; this command only needs the
  contract type, read at the invoice's issue date.

Credit-note excess and reversals trigger no pass of their own; the next invoice
issue or deposit picks them up.

## Credit that arrives without a deposit pays pending prepaid charges (GAP-L1)

A pending F2 charge keeps the service off until funds cover it. A deposit re-checks
it (`on_settlement_settled`), but credit also reaches an account in other ways: an
opening cash lot, a held partition admitted after the seal, a refund or an
allocation reversed, an opening refund corrected. None of those settles anything, so
without this command a prepaid customer could hold credit and stay cut off.

`on_account_credit_changed(db, scope=..., request=AccountCreditChangedV1(account),
ports=FundedChargePorts)` returns a `CreditRecheckOutcomeV1(billing_account_id,
changed, reason_code)`. Order: no pending row returns `no_pending_charges` with one
indexed `EXISTS` and no lock; otherwise the Billing account lock is taken first (as
every F2 command does) and no unrestricted credit returns `no_balance`; then the
pending rows are locked oldest first (at most `reevaluation_batch_cap`) and each goes
through the same re-evaluation as a settlement: withdrawn if the source no longer
holds the charge or the account is no longer prepaid at the period start, whole
charges only, a misfit is skipped (`nothing_fits` if none fit). The payload carries
only the account; everything else is re-read from Billing, so a mis-routed event
causes at most an early, harmless re-check. There is no new source reference: the
pending to discharged change under the row lock is the exactly-once guard, and the
discharge keeps its own `money-flows:funded-charge:{instance}:{invoice}` reference.

**Dating.** A charge is dated `max(due_at, newest credit line since due_at)`: the
latest Billing account-statement line of any group kind that adds available credit,
in `[due_at, 9999-12-31)` (`issue_at = due_at` when there is none). The charge is
never dated before the money that paid it reached the account. The statement is read
through the private `_credit_dating` module after the account lock, once per distinct
due time per call, with the same split-on-`statement_too_large` rule as GAP-3; Settle
passes the `settlement_accepted` kind filter so its behaviour is unchanged.

A technical fault (an unreadable or unsplittable statement) raises
(`BalancePassFailed`) and writes nothing; the caller rolls back and event delivery
retries. An owner refusal on a covered charge raises `FundedChargeStepFailed` as in
`on_settlement_settled`; the transport rolls back and calls
`record_funded_charge_attention(..., continue_credit_recheck=True)`, which enqueues
`money_flows.funded_charge.credit_recheck.v1` (account, no cursor) while pending rows
remain, so rows rolled back with the failed one are not stranded. There is no loop:
the failed row is no longer pending.

Duties of the caller (Sub-thin):

- Route **every tenant `billing.receivable.position.v2`** to `on_account_credit_changed`
  (account id only), in its own transaction per event, and route
  `money_flows.funded_charge.credit_recheck.v1` back to the same command with its
  cursor. A generic route is deliberate: a list of group kinds kept in Sub-thin would
  silently miss a source. F2's own draw emits `position.v2`; that run is a no-op.
- Run the step-failure transport with `continue_credit_recheck=True` (the
  `record_funded_charge_attention` call after a `FundedChargeStepFailed` from this
  command). Let a `BalancePassFailed` or Billing exception propagate so the relay
  retries and dead-letters after its attempts: an ops alert, never a billing queue.
- Start F2 charges only after the authority switch. A pending row writes nothing to
  Billing, so one can exist before its account's opening credit lands; this command
  closes that gap, but a start before the switch would price the account on the
  wrong authority.
- Q1 dating duty (opening lane): the opening import command's `occurred_at` is the
  date of the lot, and Billing does not check it. Date an admitted held partition at
  its **admission instant**, so the customer never pays for the days they were cut
  off and nothing is back-dated across a month end. A lot dated before the charge's
  due time pays at the due time.
- Q3 (census duty, no code): a held prepaid account goes off at its first due date
  after the seal; if the customer pays before admission the account is no longer
  pristine and the admission is refused for good, stranding the legacy credit. Count
  held prepaid accounts with credit in the dry-run census and admit them within the
  first billing day (D-9 makes approval default off).

Out of scope: an earmark release raises credit with no posting group (only
`settlement.earmark.changed.v1`) and has no caller outside Billing tests.
