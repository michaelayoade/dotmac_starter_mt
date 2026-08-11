# ADR 0016 — Payment coverage is derived arithmetic, never a lifecycle status

**Status:** Accepted — **fleet-wide**
**Date:** 2026-08-10
**Applies to:** every Dotmac repository that models a monetary document —
`dotmac_erp` (AR, AP, payroll, expense, lease, IPSAS), `dotmac_sub` (invoices,
credit notes), and any future product. This ADR states the rule; enforcement for
other repositories lands through the pinned Governance source (hard rule 15).
**Extends:** ADR-0008 (a vocabulary is declared by its owner, never enumerated by
its host) — the same disease, one layer down: here a *computation* has been
enumerated into a vocabulary.
**Owns:** the rule that a monetary document's payment coverage is derived from
its amounts, and that its `status` column carries lifecycle only
**Does not own:** what any document's lifecycle means, how payments are
allocated, dunning policy, or the tolerance any given product chooses

## Context

A monetary document has two independent facts about it:

* **Lifecycle** — where it is in its own process. DRAFT, SUBMITTED,
  PENDING_APPROVAL, APPROVED, POSTED, REJECTED, CANCELLED, VOID, DISPUTED.
  Changed by people and workflow.
* **Coverage** — how much of it has been paid. Unpaid, partially paid, paid,
  overpaid. Changed by money moving, and fully determined by
  `total_amount` and `amount_paid`.

Every Dotmac schema that models money today stores both in **one `status`
column**, and the results diverge in the two opposite ways you would predict.

### Where coverage was crammed into lifecycle

`dotmac_erp` AR and AP put `PARTIALLY_PAID` and `PAID` into the lifecycle enum.
Because coverage is then a *stored copy of a computation*, every code path that
moves money must recompute and rewrite it. Twelve did, with seven different rules:

| | fully-paid test | nothing paid |
|---|---|---|
| `ar/invoice.py` | `paid >= total` | PARTIALLY_PAID |
| `ar/customer_payment.py` (apply) | `paid >= total` | PARTIALLY_PAID |
| `ar/customer_payment.py` (reverse, ×2) | — | POSTED/OVERDUE |
| `ar/advance_allocation.py` | `paid >= total` | unchanged |
| `tasks/data_health.py` (allocate) | `paid >= total - 0.01` | PARTIALLY_PAID |
| `tasks/data_health.py` (repair) | `total - paid > 0.01` | unchanged |
| `scripts/reconcile_invoice_amount_paid.py` | `total - paid <= 0.01` | unchanged |
| `scripts/allocate_exact_match_payments.py` | `paid >= total` | PARTIALLY_PAID |
| `ap/supplier_invoice.py` | `paid >= total` | PARTIALLY_PAID |
| `ap/supplier_payment.py` (apply) | `paid >= total` | PARTIALLY_PAID |
| `ap/supplier_payment.py` (reverse) | — | POSTED |
| `ipsas/commitment_service.py` | `expended >= obligated` | PARTIALLY_PAID |

The IPSAS row is worth reading twice. `CommitmentStatus` reached the same
conflation independently, in a different accounting standard, over a different
pair of columns (`expended_amount` / `obligated_amount`), with a different word
for full coverage (`EXPENDED`, not `PAID`) — and the same exact `>=`, the same
absent tolerance and the same missing zero-guard. Three domains converged on the
same mistake without copying each other, which is what distinguishes a design
defect from a local one.

An invoice one kobo short was PAID down one path and PARTIALLY_PAID down another,
and which a customer got depended on whether a human, a scheduler or a hand-run
script moved the money. Two files each contained a `Decimal("0.01")` tolerance
*and* an exact `>=` for the status a few lines away
(`ar/advance_allocation.py`'s `DUST_THRESHOLD`, `ap/supplier_payment.py:373`).

Three further symptoms follow from the conflation itself, not from the
duplication:

* **A voided invoice reads PAID.** VOID typically carries a zero balance, so any
  rule keyed on balance alone relabels it. The sync layer had already been bitten
  and left a comment about it.
* **A partial payment destroys OVERDUE.** OVERDUE is a time-derived lifecycle
  fact; a coverage write overwrites it, and nothing downstream chases the
  invoice again.
* **A repair task exists at all.** `tasks/data_health.py` scans for invoices
  whose stored status disagrees with their amounts. That task is only necessary
  because a cache can drift. Arithmetic cannot.

### Where coverage was left out entirely

Three further monetary documents never got a `PARTIALLY_PAID` member, so they
cannot express partial payment at all — while their data can:

| document | amount it carries | statuses |
|---|---|---|
| `SalarySlip` | `net_pay`, `net_pay_functional` | DRAFT…POSTED, **PAID**, CANCELLED |
| `ExpenseClaim` (two modules) | `total_amount` | DRAFT…APPROVED, **PAID**, CANCELLED |
| `LeasePaymentSchedule` | `total_payment` (+ principal/interest split) | SCHEDULED, INVOICED, **PAID**, OVERDUE, CANCELLED |

None of the three has an `amount_paid` column at all, so partial coverage is not
merely unrepresentable in the status — there is nowhere to record how much was
actually paid.

`payroll_service.py:1101` is `slip.status = SalarySlipStatus.PAID` — set
unconditionally, with no comparison against `net_pay`. `TransferBatchItem`
carries its own `amount`. **So disbursing ₦50,000 against a ₦100,000 salary slip
leaves the slip reading PAID.** Expense claims have the same shape: `MARK_PAID`
is an action, not a computation.

This is not a hypothetical purity concern. Part-disbursement when cash is tight
is ordinary practice, and the ledger currently records it as settled in full.

### The shape of the mistake

Storing coverage as a status member enumerates a *computation* into a
*vocabulary*. Every consequence above follows: a computation stored is a cache,
a cache needs writers, writers diverge, and a vocabulary that must be extended
per document type gets extended for three of six.

## Decision

**Payment coverage is derived from amounts. It is never a member of a lifecycle
status enum, and never written by application code.**

1. **`status` carries lifecycle only.** `PAID` and `PARTIALLY_PAID` are removed
   from every lifecycle enum. What remains is the set a human or a workflow
   moves the document through.

2. **Coverage is computed from `total_amount` and `amount_paid`**, yielding a
   shared closed vocabulary: `UNPAID`, `PARTIAL`, `PAID`, `OVERPAID`. This
   vocabulary *is* closed and *is* an enum — it has exactly these members for
   every document in every product, which is precisely the case ADR-0008 says an
   enum is right for.

3. **The database derives the ARITHMETIC; code applies the POLICY.** The
   generated column is `balance_due`, not `coverage`:

       balance_due GENERATED ALWAYS AS (total_amount - amount_paid) STORED

   Pure subtraction, no threshold, no vocabulary. It is indexable and
   filterable like any other column and has no writer, so drift becomes
   structurally impossible rather than merely discouraged.

   Coverage — `UNPAID`/`PARTIAL`/`PAID`/`OVERPAID` — is then derived from
   `balance_due` and the configured tolerance by one owning function. Still no
   stored status, still one owner, but the *threshold* is not welded into a
   schema object.

   **Generating `coverage` directly was the first draft of this ADR and is
   rejected**, because it compiles policy into the schema: changing the dust
   threshold would become a migration, and altering a generated expression
   means dropping and re-adding the column. That contradicts "everything by
   config" for a value that is explicitly a business tolerance.

   `balance_due` is also the smaller, more obviously correct change. It
   *already exists* as a Python `@property` on `Invoice` and
   `SupplierInvoice` — `total_amount - amount_paid` — and because a property
   is not queryable, at least three services hand-write the same expression
   as SQL (`ar_overdue.py` twice, `ap_due.py`). Making it a column collapses
   those onto one definition and lets the ORM and SQL agree, which is worth
   doing on its own merits.

4. **The tolerance is a setting, declared once per product.** Sub-cent
   rounding dust is a real quantity; modules independently choosing
   `Decimal("0.01")` is how they later diverge — this codebase produced four
   such declarations (AR, AP, GL posting, period close). It is a setting with
   a spec (ADR-0011/0012), read at the point coverage is derived and applied
   in the query (`WHERE balance_due > :dust`), never baked into DDL.

5. **Any document with an amount gets coverage.** A shared mixin supplies
   `total_amount`, `amount_paid` and the derived column. Partial payment stops
   being a feature somebody remembers to add to an enum and becomes a property of
   being a monetary document.

6. **Lifecycle transitions may read coverage; coverage never reads lifecycle.**
   "You may not approve a claim that is partly paid" is a legitimate lifecycle
   rule consuming a derived fact. The reverse — coverage depending on status — is
   the circularity that produced VOID-reads-PAID.

## Consequences

**A voided document keeps its balance and its VOID.** The two facts no longer
share a field, so neither can overwrite the other. Same for OVERDUE, which
becomes a lifecycle/aging concern that a payment cannot erase.

**The repair task and the reconcile script become unnecessary.**
`data_health.reconcile_invoice_statuses` and
`scripts/reconcile_invoice_amount_paid` exist to fix drift in a derived value.
Both retire once the value is derived. This is the concrete form of the
standing rule that a reconciler exists to repair drift from authoritative
inputs — here the drift is eliminated instead.

**Reads change shape.** `WHERE status = 'PAID'` becomes
`WHERE coverage = 'PAID'`, and callers that treated PAID as *both* "settled" and
"a terminal lifecycle state" must say which they meant. That disambiguation is
work, and it is the point: those callers were relying on the conflation.

**Aging, dunning and AR reports gain a cleaner input.** They currently infer
outstanding-ness from a status that a rounding residue can flip.

**`amount_paid` becomes the single thing that must be right.** The correctness
budget concentrates on one number maintained by allocation, rather than being
spread across a number and a redundant label. Allocation remains the owner of
`amount_paid`; nothing in this ADR changes who writes it.

**Not every product must move at once.** The rule is fleet-wide; adoption is
per-repository through expand/contract.

## Migration

Two stages, and **stage 1 is worth doing even if stage 2 never happens** —
which is a property a first step should have.

### Stage 1 — `balance_due` becomes a generated column

Touches no status enum and changes no behaviour.

1. Add `balance_due GENERATED ALWAYS AS (total_amount - amount_paid) STORED`
   to `ar.invoice` and `ap.supplier_invoice`, replacing the Python
   `@property` of the same name and value.
2. Point the three hand-written SQL copies (`ar_overdue.py` ×2, `ap_due.py`)
   at the column.
3. Index it where aging and dunning filter on it.

The payoff is immediate and independent: one definition instead of four, and
`balance_due` becomes queryable, which it never was.

### Stage 2 — coverage stops being a status

Per table, expand/contract, never big-bang:

1. **Expand** — add `amount_paid` and `balance_due` where missing (payroll,
   expense, lease). Nothing reads coverage yet.
2. **Shadow** — assert derived coverage agrees with the stored status for
   every row whose status is PAID/PARTIALLY_PAID. Disagreements are
   pre-existing data defects being surfaced, and are triaged before cutover —
   expect a non-zero count, because the twelve divergent rules produced it.
3. **Cut reads over** — callers move from `status` to derived coverage, one
   call site at a time, disambiguating "settled" from "terminal" as they go.
   This is the real work; no mechanism avoids it.
4. **Contract** — remove `PAID`/`PARTIALLY_PAID` from the lifecycle enums and
   delete the repair paths that maintained them.

`dotmac_erp` is the reference implementation because it has both failure modes.
The consolidation already merged (#242 AR, #243 AP) is the prerequisite, not
wasted work: twelve divergent rules could not be migrated simultaneously, and
each owner module is now a single delegation point.

## Enforcement

* An architecture test failing the build if any lifecycle status enum declares a
  `PAID` or `PARTIALLY_PAID` member.
* The existing `test_paid_status_single_owner.py` (AR + AP) stays until contract
  completes, then retires with the enum members it guards — a guard whose subject
  no longer exists is noise.
* A test that every model carrying a monetary total also carries the coverage
  mixin, so a new money document cannot ship without partial-payment support.

## Alternatives rejected

**Add `PARTIALLY_PAID` to the three enums that lack it.** The obvious reading of
the question that prompted this ADR, and it makes things worse: three more
lifecycle enums acquire a coverage member, three more sets of writers appear, and
the divergence that produced seven rules across twelve sites is invited into
payroll and expense. It treats the symptom (missing member) rather than the
cause (coverage is not a status).

**Keep coverage stored, but behind one owner per domain.** This is what merged
in #242/#243 and it is a genuine improvement — but it leaves a cache with a
writer, so `data_health`'s repair task must stay, VOID and OVERDUE still share a
field with coverage, and the three documents without a `PARTIALLY_PAID` member
still cannot express one. Correct as a step; insufficient as the destination.

**Generate `coverage` itself, rather than `balance_due`.** This was the first
draft of this ADR. It is rejected because it compiles a business tolerance
into DDL: changing the dust threshold becomes a migration, and altering a
generated expression means dropping and re-adding the column. A threshold is
policy, and policy belongs in settings (ADR-0011/0012), not welded to a
schema object. Generating the subtraction and applying the threshold in the
query gets the same drift-impossibility with none of that.

**Compute everything in Python at read time, with no column at all.**
Drift-free, but unfilterable and unindexable: `WHERE balance_due > :dust`
becomes a full scan plus application-side filtering, which AR aging and
dunning cannot afford. Note this is what the codebase does TODAY —
`balance_due` is a Python `@property` — which is precisely why three services
hand-write the subtraction as SQL to get a query they can run.

**A database trigger maintaining a normal column.** Equivalent in effect to a
generated column but strictly worse: triggers are invisible at the model layer,
can be disabled, run in an order that must be reasoned about, and are skipped by
bulk-load paths. `GENERATED ALWAYS AS ... STORED` is declared where the column
is, and cannot be bypassed.

**Model coverage as a separate table.** Correct-by-construction and heavier than
the problem: coverage is a pure function of two columns already on the row, so a
join buys nothing that arithmetic does not.
