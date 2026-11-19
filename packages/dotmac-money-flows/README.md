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
technical or owner failure (Billing refusal, unreadable open-document list,
database error) **raises**; nothing is written. Source references are
deterministic (`money-flows:settle:issue:{trigger}:{document}`), so a retry never
pays twice and the next invoice or deposit pass completes any missed work.

Duties of the caller (Sub-thin):

- Route each Billing invoice-issued document fact (`billing.invoice.document.fact`
  v1/v2/v3 with `document_kind=invoice`, `document_state=issued`, `fact_version=1`)
  to this command, in its own transaction per event.
- Let a failure propagate so the transport rolls back and its event delivery
  retries automatically. A persistent failure (retries exhausted) is a technical
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
