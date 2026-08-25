# ADR-0040: Expenses owns spend evidence and eligibility, not payment

- Status: accepted
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

