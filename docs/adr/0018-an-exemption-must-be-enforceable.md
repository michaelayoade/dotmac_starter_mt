# ADR 0018 — A guard exemption must carry an enforceable premise

**Status:** Accepted — **fleet-wide**
**Date:** 2026-08-11
**Applies to:** every Dotmac repository. Enforcement for repositories other than
the starter lands through the pinned Governance source (hard rule 15); this ADR
is the statement of the rule.
**Extends:** ADR-0010 (adapters are thin, *and identifiable by a rule*) — the
same concern seen from the other side: 0010 asks what a guard must cover, this
asks what may be excluded from one and on what terms.
**Owns:** the rule that an exclusion from a lint, type, architecture or security
check states a premise, that the premise is machine-checkable, and that any
resulting backlog is a two-directional ratchet
**Does not own:** which checks a repository runs, or what any individual check
asserts

## Context

An exemption is a promise that nothing checks.

`dotmac_erp` excluded `scripts/` from every architectural and security check it
had — semgrep in the `Makefile` and in both rulesets, pre-commit, and a blanket
`ruff` per-file-ignore commented *"scripts: one-off maintenance scripts"*. The
premise was true when written and false within a year. **One hundred scripts
opening unscoped database sessions accumulated behind it**, several of them
moving money: `allocate_splynx_fifo`, `post_unposted_ap_invoices`,
`reconcile_invoice_amount_paid`.

The same repository's `app/tasks/` held **zero** such scripts, because the same
checker had guarded it from the day it was written.

Same repo, same hazard, same engineers. The only difference was whether a guard
looked.

That is the general shape, and it is not about scripts:

> An exempted region does not stay small and harmless. It becomes the
> lowest-friction place to put work, and therefore where the defect
> concentrates.

Two further observations from the same investigation:

* **The guard's own documentation already described the wider scope.** ERP's
  `check_session_context.py` docstring said *"Celery tasks (and any other
  non-request entry point)"* while its scan roots covered two of three. The
  rule was right; the coverage had silently narrowed.
* **The premise was load-bearing and unverified.** "These are one-off
  maintenance scripts" was true of dated backfills and false of the tree. Nobody
  re-checked it because nothing could.

## Decision

**An exclusion from any check states its premise, and the premise is
machine-checkable. An exclusion whose premise cannot be checked is not an
exemption — it is an unmonitored region, and is not permitted.**

1. **Enumerate entry-point families, not directories.** Anything running outside
   the request lifecycle — tasks, scripts, CLI, workers, cron, migrations —
   faces the same hazards. A guard scoped to one family is a guard with a known
   hole. When a guard's docstring claims broader scope than its configuration
   implements, the configuration is the defect.

2. **An exemption's premise must be enforceable.** If the premise is "these have
   already run", make that structurally true: move them somewhere the guard
   deliberately skips, and make the move visible in review. `scripts/archive/`
   is the reference shape — retired code is *provenance*, not an entry point,
   and moving a file there is the act that retires its obligations.

3. **Retire a backlog with a two-directional ratchet, never a blanket allow.**
   Record each member with its exact violation count. Fail when the count moves
   in EITHER direction: upward is new debt, downward is progress that must be
   recorded by lowering the number. An entry that stops being scanned at all
   must be removed, so retirement is recorded rather than assumed.

4. **Keep "grandfathered" and "reviewed and correct" as distinct mechanisms.** A
   per-line marker meaning *this is genuinely fine here* must not be reachable
   by copying, or a new violation buys silence with a comment. ERP keeps
   `# session-context: allow` (reviewed) separate from
   `session_context_legacy.txt` (known-wrong, shrinking).

5. **Prove sensitivity.** A newly-covered region that passes must be shown to
   FAIL without its ratchet. Otherwise a clean run is indistinguishable from the
   guard having stopped looking.

## Consequences

**Adding a check to a new region is a two-part change**: the coverage, and the
ratchet that makes the existing backlog explicit. That is more work than an
exclusion and is the point — the cost lands when the debt is created rather
than when it is discovered.

**Ratchets accumulate.** `dotmac_erp` now carries three (unscoped sessions, RLS
coverage, hand-written `balance_due`). Each is a bounded, shrinking list with a
named exit, which is a materially different thing from three exclusions.

**Some exemptions remain correct.** A closed set, a vendored dependency, a
generated file: these have premises that are structurally true and stay true.
The rule is not "never exclude" — it is "state the premise, and make it
checkable".

## Enforcement

* Each guard's scan roots are asserted by its own test, so narrowing coverage
  fails the build rather than passing quietly.
* Each ratchet fails in both directions, with a sensitivity proof that the
  detector still fires.
* A new blanket per-file-ignore or tool-level exclusion is a reviewable event:
  it must name its premise in the same change, and say how the premise is
  checked.

## Alternatives rejected

**Ban exclusions outright.** Unworkable and dishonest: generated files,
vendored code and genuinely closed sets exist, and a rule that is routinely
violated stops being consulted. The problem was never that an exclusion
existed — it was that its premise decayed unobserved.

**Audit exclusions periodically.** Depends on somebody remembering, which is
the failure mode being fixed. `scripts/` was excluded for over a year and
nobody audited it; the exclusion was discovered by asking an unrelated
question about batch scripts.

**Delete the backlog instead of ratcheting it.** Attractive and usually
impossible at the scale these reach — 100 scripts, 75 hand-written
expressions. A ratchet lets coverage land immediately while the backlog
shrinks under pressure, rather than blocking coverage on a rewrite nobody has
time for.
