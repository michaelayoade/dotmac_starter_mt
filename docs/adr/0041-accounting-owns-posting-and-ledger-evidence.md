# ADR-0041: Accounting owns posting and ledger evidence

- Status: Accepted
- Date: 2026-08-19
- Deciders: Michael
- Supersedes: none
- Related: ADR-0006 (product-first extraction), ADR-0014 (at-most-once),
  ADR-0024 (application/module independence), ADR-0030 (ERP general-ledger
  authority), ADR-0031 (sealed cutovers),
  `docs/inventories/accounting-payables-sources.md`

## Context

ERP has the only production chart, fiscal-period, journal, posting and reversal
implementation in the audited fleet. Other products produce operational
economic facts, but Sub explicitly refuses to become a shadow GL and the
in-progress fixed-asset candidate explicitly excludes the ledger.

Extracting only models would preserve the dangerous part of the aggregate:
callers could still decide balance, period admissibility, source replay and
ledger mutation independently. The unit of extraction must therefore include
the posting decision and its immutable evidence.

## Decision

### 1. One tenant-only accounting owner

`dotmac-accounting` owns the chart of accounts, fiscal years and periods,
accounting dimensions, journals and lines, the balanced-posting decision,
reversal journals, period close/reopen/lock transitions, and append-only posted
ledger evidence.

V1 is tenant-only. No control-plane consumer exists. A platform plane requires
a named adopter and a later ADR; it is never inferred from a missing tenant
column.

### 2. Producers submit facts; Accounting decides the consequence

A producer supplies a typed journal command with an opaque source owner,
document kind/id/version/fingerprint, business dates, currency, accounts,
amounts and dimension values. Accounting resolves only its own accounts,
periods and dimensions. It refuses inactive/non-posting accounts, invalid or
inactive dimension values, dates outside the named period, a closed/locked
period, one-sided-line violations, zero lines and any functional-currency
imbalance.

The source tuple is provenance, not authority. Accounting never reads the
producer's ORM, database or filesystem and never lets a producer insert ledger
rows directly.

### 3. Posting and reversal are atomic, idempotent and immutable

The kernel idempotency ledger owns replay and request-fingerprint conflict.
Journal state, ledger lines, immutable dimension snapshots and the idempotency
record commit in the caller's transaction. Services flush but never commit or
roll back.

A posted journal and its lines cannot be edited or deleted. A correction is a
new linked journal whose debit/credit sides are swapped and whose posting date
must resolve to an admissible period. The original becomes `REVERSED` as a
projection, but its posted ledger evidence remains unchanged. A reversal does
not recycle the original idempotency key.

### 4. Period authority is explicit

Periods move `FUTURE -> OPEN -> SOFT_CLOSED -> LOCKED`. A soft-closed period
may reopen only with an approval reference and reason; the reopen produces a
new token required by postings while `REOPENED`. Soft-closing clears that
token. `LOCKED` is irreversible.

The assembly supplies typed close-check evidence; Accounting verifies every
check passed and stores its fingerprint but does not own bank reconciliation,
tax filing, inventory or another domain's close prerequisite. Every transition
appends immutable period evidence. Fiscal years/periods are date-contained and
non-overlapping under locked tenant/year decision rows.

### 5. Dimensions are data, not columns

Dimension definitions and values are tenant-owned rows. Journal lines link to
zero or more values, at most one value per dimension. Posted evidence snapshots
the dimension code/value code in immutable rows. Adding a product dimension
does not alter the shared schema and no sibling module becomes an import.

### 6. Accounting is not every finance domain

Accounting does not own receivables, payables documents, purchase commitments,
supplier/customer identity, assets, inventory, tax calculation/filing, cash or
banking, payment execution, budgets, consolidation, exchange-rate sourcing,
statutory report presentation, numbering, approvals or provider I/O. Those
owners submit or consume typed facts through assemblies.

## Consequences

- ERP is the first adopter and retiring source. It shadows and seals a separate
  Accounting cutover before Payables publishes consequences to it.
- `dotmac-finance` remains a producer of fixed-asset accounting facts rather
  than a second journal owner.
- Posted ledger and period-event immutability is enforced by PostgreSQL grants
  and triggers, not comments or service convention alone.
- The package may be built and proven while its extraction state remains
  `audit-complete`; publication and authority transfer are later gates.

## Alternatives rejected

**Copy ERP GL wholesale.** It imports product services and owns commits in
places; it also hard-codes four dimensions and carries product reporting/cache
concerns. Behavior and parity port, coupling does not.

**Let each domain write balanced entries.** That creates multiple owners for
period admissibility, replay, immutability and reversal. A balanced payload is
an input assertion, not the posting decision.

**Merge Accounting and Payables.** Payables is a document/subledger owner and
Accounting is the statutory ledger owner. Combining them makes other producers
second-class branches and prevents either module being adopted independently.

