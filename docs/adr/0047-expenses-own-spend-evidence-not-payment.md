# ADR-0047: Expenses owns spend evidence and eligibility, not payment

- Status: accepted. Amended 2026-08-24 — see "Amendment — 2026-08-24" at the
  end of this record. The amendment narrows this record's one over-broad
  sentence about disbursement and cites ADR-0042 as the controlling record. No
  earlier text is rewritten.
- Date: 2026-08-18
- Scope: `dotmac-expenses`, ERP/CRM/Sub expense decomposition, Backoffice
  composition

## Context

ERP has the qualifying expense implementation, while CRM and Sub each keep a
field-request copy that is pushed into ERP as a claim and later mirrors ERP's
terminal status. That creates several apparent owners for one request and makes
`paid` cross an application boundary as mutable status rather than settlement
evidence.

The source audit in `docs/inventories/expenses-sources.md` also exposes adjacent
owners. Approvals decides whether eligible actors approved content. Files owns
stored bytes. Finance/Payables owns obligations, journals, disbursement and
settlement. People owns employment/reporting structure. A reusable Expenses
module cannot absorb those decisions merely because ERP currently calls them
from one service.

> **Narrowed 2026-08-24 — see Amendment A1.** "Finance/Payables owns
> obligations, journals, disbursement and settlement" is too broad on two
> words. ADR-0042 (accepted 2026-08-19, one day after this record, and devoted
> to exactly this separation) rules that Payables says what is owed and that a
> **Treasury/payment owner performs disbursement**; journals are Accounting's
> (ADR-0041) and cash/settlement observation is Banking's (ADR-0044). **ADR-0042
> controls.** A1 records the authoritative six-owner split. Nothing else in
> this record changes: Expenses still owns eligibility and never payment.

## Decision

`dotmac-expenses` is a tenant-only module. It owns category policy, intended
spend requests, incurred claims, request/claim lines, receipt meaning,
effective-dated limit evaluation, lifecycle evidence and the derived answer
whether an approved claim is eligible to be presented for reimbursement.

Requests and claims are distinct. A request authorizes intended spend; a claim
records incurred spend. An approved request may seed one claim, but approval of
the request does not approve the later claim and the claim preserves its own
line evidence.

The module applies an approval observation carrying an opaque decision
reference; it does not decide actor eligibility, quorum or separation of
duties. Published policy revisions and recorded evaluations are immutable.
Blocking findings refuse submission, while warnings and approval-required
findings remain explicit evidence.

Receipt records reference an opaque stored-file UUID and own only expense-domain
metadata. They never store bytes or provider paths. Reimbursement eligibility
is computed from approved state, a positive approved amount, receipt
completeness and absence of blocking findings. Eligibility is not a mutable
column and does not mean payment occurred.

Finance owns the reimbursement effect and its coverage. It may accept an
eligible-claim command through an assembly adapter and later publish settlement
observations. Expenses never stores bank account details, payment intent,
journal, supplier-invoice or `paid` status.

## Consequences

- CRM/Sub request copies and their remote ERP-state mirrors are migration
  sources, not permanent owners.
- ERP claim/category/limit behavior is the product-first port source, narrowed
  at typed seams for Approvals, Files, People, Numbering and Finance.
- Every adopter installs its own `mod_expenses` rows. Applications synchronize
  typed observations and commands; none reads another application's database.
- The package can be composed without People, Files, Approvals, Numbering or
  Finance imports. The assembly binds those capabilities.
- A later payment-coverage observation cannot regress or overwrite expense
  lifecycle state; Finance remains the only source of payment truth.


## Amendment — 2026-08-24 (accepted correction)

### A1. "Finance/Payables owns … disbursement" is narrowed — ADR-0042 controls

This record's § Context lists the adjacent owners its decision must not absorb,
and describes one of them as "Finance/Payables owns obligations, journals,
disbursement and settlement". That sentence was written to say *"not Expenses"*,
and it does say that correctly. What it also does, read literally, is name
Payables as the owner of payment EXECUTION.

ADR-0042 — accepted 2026-08-19, one day after this record, and devoted
specifically to separating a liability from the act of paying it — rules the
opposite in its § 3: *"Payables says what is owed, in what currency and by which
date. It does not choose a bank account, payment rail, provider, batch or
execution time and does not perform network I/O. A Treasury/payment owner
performs disbursement…"*. Its rejected alternative **"Let Payables execute
payments"** states the reason: an obligation and a bank movement have different
controls, security boundaries and failure modes, and combining them puts
provider I/O inside a domain transaction.

**ADR-0042 controls.** This record's sentence is narrowed to its intended
meaning — *those decisions are not Expenses'* — and is not authority for
locating disbursement in Payables. "Finance/Payables" is a department, and a
department is not an owner (the same error ADR-0061 A1 corrected when it
replaced "ERP's Treasury/payment owner" with a named service).

The authoritative split, which supersedes the compound sentence:

| Owner | Decision |
|---|---|
| **Expenses** (`dotmac-expenses`, this record) | whether a claim is ELIGIBLE for reimbursement |
| **Payables** (`dotmac-payables`, ADR-0042 §§ 1, 3) | what is OWED — to whom, in what currency, and when |
| **Treasury** (ADR-0042 § 3's unnamed owner; named for payouts by ADR-0061 § 1 + A1; scoped by ADR-0063) | the AUTHORIZED PAYMENT INSTRUCTION, its rail submission and its resolution |
| **Integrator** (`dotmac-integration` + a connector, ADR-0024 §§ 6–7, ADR-0061 § 1) | provider AUTHENTICATION, TRANSPORT and EVIDENCE — never whether, to whom or how much |
| **Banking** (`dotmac-banking`, ADR-0044) | statement/cash OBSERVATIONS and RECONCILIATION EVIDENCE |
| **Accounting** (`dotmac-accounting`, ADR-0041) | JOURNAL and LEDGER consequences |

Read down the column and the money story is one act per owner: Expenses says
*may this be reimbursed*, Payables says *this much is owed by this date*,
Treasury says *pay it, this way, now*, the Integrator says *the provider was
called and here is what it answered*, Banking says *cash actually moved*, and
Accounting says *here is what it means to the books*. Six owners, six different
failure modes, six different sets of controls — which is exactly why the
compound sentence had to be broken up.

Two consequences that were already true and are now stated:

- Eligibility is still not payment, and a later payment-coverage observation
  still cannot regress expense lifecycle state (§ Consequences, unchanged).
  What changes is only WHICH owner the settlement observation comes from:
  Treasury's disbursement, projected by Payables, evidenced by Banking — not
  "Finance".
- `dotmac-expenses` gains no dependency from this amendment. It continues to
  compose without Finance imports; the assembly still binds the capability.

*Corrects: § Context, third paragraph, third sentence. Cited by: ADR-0061
Amendment A5. Related: ADR-0041, ADR-0042 § 3, ADR-0044, ADR-0061, ADR-0063.*
