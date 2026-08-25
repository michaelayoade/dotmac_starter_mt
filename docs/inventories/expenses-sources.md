# Expenses source inventory

Status: product-first audit complete on 2026-08-18. This inventory records the
source behavior and ownership boundary for `dotmac-expenses`; it is not an
adoption or authority-cutover claim.

## Audited revisions

| Repository | Revision | Finding |
|---|---|---|
| `dotmac_erp` | `b969a889e8aba7255e32aa466960c22347c02fd8` (`origin/main`, 2026-08-18) | Qualifying production source. Owns the mature claim/category/line lifecycle, receipt validation, limit evaluation, approval application and reimbursement handoff. |
| `dotmac_crm` | `60daaa2dd305696636632f48505ab784110a55d2` (`origin/main`, 2026-08-18) | A replaceable field-request copy. It creates request/line rows, pushes them to ERP as claims and mirrors ERP terminal state. It is not an independent expense authority. |
| `dotmac_sub` | `510b80ca7fab4f54a57f261872f94b5e972c8eb6` (`origin/main`, 2026-08-18) | A second replaceable field-request copy associated with a work-order mirror. It owns local submission durability today but queues the back-office claim and mirrors its outcome. |
| `dotmac_backoffice` | `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d` (local branch; repository has no `origin`) | No expense persistence or service implementation. Candidate clean first consumer. |
| `dotmac_starter_mt` | `300ebd7523e85dff7e94efcdf81d8c1f34b80de5` (`origin/main`, 2026-08-18) | Supplies tenant scope/RLS, Party identity, module manifests, migration composition and the separate files/approvals contracts. |

The working checkouts of ERP and CRM were dirty, so the audit read fetched
`origin/main` objects with `git show`; no uncommitted product changes are
treated as evidence. Backoffice has no configured remote, which is recorded
rather than inferred away.

## Qualifying ERP source

ERP's authoritative implementation is split across:

- `app/models/expense/expense_claim.py`: `ExpenseCategory`, `ExpenseClaim` and
  `ExpenseClaimItem`; tenant-local category code uniqueness, claimed/approved
  totals, draft-only line mutation, receipt references and the claim lifecycle.
- `app/models/expense/limit_rule.py` and
  `app/services/expense/limit_service.py`: effective-dated rules by employee,
  grade, designation, department, employment type or organization; transaction,
  day, week, month, quarter, year and custom periods; block, warn, approval,
  multi-approval and escalation outcomes; cumulative usage counts submitted,
  pending, approved and paid claims while excluding drafts/rejected/cancelled
  rows. Category-scoped rules correctly sum matching lines rather than the
  whole claim.
- `app/services/expense/service_claims.py`: create/update/submit/approve/reject,
  approval withdrawal, cancel, resubmit and payment handoff. Items become
  immutable after draft. Approval corrections preserve claimed and approved
  amounts separately.
- `app/services/expense/approval_service.py`: receipt requirements are category
  policy and are evaluated per line; category amount caps produce findings.
- `tests/test_expense_approval_workflow.py`,
  `tests/services/test_expense_withdraw_approval.py`,
  `tests/services/test_expense_approve_error_handling.py` and
  `tests/finance/test_expense_transfer_lifecycle.py`: lifecycle and finance
  handoff parity evidence.

ERP also contains cash-advance, corporate-card, AP, GL, payment-recipient,
supplier-invoice and journal behavior. Those are real behavior, but they are
not one coherent Expenses owner. They remain with their future advance/card,
Payables, Finance and payment owners and compose through typed references.

## Replaceable request copies

CRM's `app/models/expense_request.py` has `ExpenseRequest` and
`ExpenseRequestItem`, with `draft -> submitted -> approved|rejected|paid` and
cancel behavior. `app/services/expense_requests.py` submits the row to ERP,
stores the ERP claim reference and mirrors terminal claim state. Its tests prove
at-least-one-line validation, amount totals, context inheritance, cancellation
before remote creation and idempotent push identity. A local `paid` state is a
transport projection, not a second payment decision.

Sub's `app/models/field_expense.py` repeats the request and line shape with an
opaque work-order mirror, immutable client reference, receipt attachment
reference, totals and the same terminal vocabulary. Its service now preserves
the local submission even when the back-office enqueue fails. That durability
behavior is retained, but provider enqueue/retry belongs to the Integrator or
product outbox rather than the reusable domain module.

The two request copies demonstrate a shared contract and also demonstrate the
drift mechanism: request state is mirrored from a separately owned ERP claim.
The reusable owner therefore stores request and claim in one local module and
links them explicitly; products synchronize data between independent
installations instead of sharing or mirroring another application's ORM row.

## Accepted owner boundary

`dotmac-expenses` owns:

- tenant expense-category identity and receipt/amount policy;
- intended-spend requests and their immutable submitted line snapshots;
- incurred-expense claims and claimed versus approved line amounts;
- the guarded request and claim lifecycle, while applying approval decisions
  made by the separate Approvals owner;
- receipt meaning and verification metadata around an opaque stored-file ID;
- immutable, effective-dated policy revisions and limit rules;
- append-only policy-evaluation and lifecycle evidence; and
- reimbursement eligibility derived from current claim state, approved amount,
  receipt completeness and blocking policy evidence.

It does not own Party/employee identity, work orders, projects, vehicles,
approval quorum or actor eligibility, stored bytes, document numbering,
currency conversion, advances/cards, AP obligations, tax, GL accounts or
journals, bank details, payment initiation, settlement or payment coverage.
An eligible claim is a request to Finance, not proof that Finance paid it.

## Port deltas

The first module slice intentionally improves source ownership without changing
the preserved business behavior:

1. Every row is tenant-scoped, every identity has `UNIQUE (tenant_id, id)`, and
   every internal foreign key carries the tenant. RLS is enabled and forced in
   the table-creating migration.
2. Published policy revisions are immutable. Evaluation rows point to the exact
   revision and rule that produced the outcome; counters are derived rather
   than mutable rule statistics.
3. Category receipt exemptions are data (`requires_receipt` and an optional
   threshold), not the ERP hard-coded normalized name `fuelmileage`.
4. File bytes and upload paths do not enter the module. Receipt metadata holds
   an opaque `file_id`, content digest and domain facts; the assembly validates
   that file through its `dotmac-files` adapter.
5. Approval is a referenced observation. The module never rebuilds ERP's
   approval hierarchy beside `dotmac-approvals`/`dotmac-people`.
6. Reimbursement eligibility is computed. There is no writable `paid` or
   `eligible` status and no bank/payment fields.
7. Request/claim numbering is caller-supplied. An adopter may allocate it with
   `dotmac-numbering`, but Expenses does not import that sibling module.

## First cutover and retirement proof

`dotmac_backoffice` is the recommended clean first consumer. It pins the exact
kernel and module releases, installs the `ex` lineage in its own database, and
uses versioned API/webhook adapters for product synchronization. Before ERP
authority moves, backfill categories, effective policy revisions, requests,
claims, lines, receipt metadata and evaluation/lifecycle evidence, then compare
counts, stable references, status, totals, receipt completeness and eligibility
for a fixed shadow window.

The cutover is sealed only when one explicit switch makes the module
installation the sole writer. Two-directional ratchets then retire:

- ERP category, claim, line, receipt-requirement, limit-evaluation and lifecycle
  writers in the accepted boundary;
- CRM and Sub request/line writers and ERP-status mirrors; and
- direct CRM/Sub-to-ERP expense clients after their integrations target the
  product-owned typed expense port.

Finance handoff, legacy read compatibility and historical audit export receive
separate ratchets. Deleting rows is not evidence that decision authority moved.

