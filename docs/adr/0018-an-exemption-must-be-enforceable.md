# ADR 0018 — A guard exemption must carry an enforceable premise

**Status:** Accepted — **fleet-wide**
**Date:** 2026-08-11 (amended 2026-08-15 — see "Decision amendment — 2026-08-15
(a detector proves three legs, per arm)"; decision point 5 is superseded by it)
**Applies to:** every Dotmac repository. Enforcement for repositories other than
the starter lands through the pinned Governance source (hard rule 15); this ADR
is the statement of the rule.
**Extends:** ADR-0010 (adapters are thin, *and identifiable by a rule*) — the
same concern seen from the other side: 0010 asks what a guard must cover, this
asks what may be excluded from one and on what terms.
**Owns:** the rule that an exclusion from a lint, type, architecture or security
check states a premise, that the premise is machine-checkable, that any
resulting backlog is a two-directional ratchet, and — since the 2026-08-15
amendment — that every detector ARM proves sensitivity, specificity AND
liveness
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
   guard having stopped looking. *(Superseded by the 2026-08-15 amendment
   below: sensitivity is one leg of three.)*

## Decision amendment — 2026-08-15 (a detector proves three legs, per arm)

Point 5 asked for one proof. One proof is not enough, and the two it omitted
are precisely the two that fail *silently* — a detector missing either of them
still reports green, still reports coverage, and reads in review exactly like a
detector that works.

**A detector ARM is proved by three legs. All three are required, and all three
are per ARM.**

1. **SENSITIVITY** — a representative violation fires the arm. Without this, a
   clean run is indistinguishable from a guard that stopped looking.
2. **SPECIFICITY** — the near-miss the arm was deliberately narrowed against
   remains silent. Without this, the arm becomes noise and gets switched off,
   which is a slower route to the same unmonitored region.
3. **LIVENESS** — the arm REACHES, and CORRECTLY CLASSIFIES, real corpus code.
   Without this, the arm may be perfectly correct on the fixtures its author
   wrote and inert on the codebase it was written for.

Sensitivity and specificity are both statements about a fixture the detector's
author chose. Liveness is the only leg that is a statement about the SUBJECT.

### The evidence: an arm that passed both other legs and saw nothing

The Governance external-connector ratchet grew bounded factory tracing so that
a client arriving from a project-local factory would still be counted. The
textbook factory shape was implemented and proved in both directions:

```python
def make_client():
    return httpx.Client(base_url=...)      # sensitivity: resolved
def make_mapping():
    return {"base_url": ...}               # specificity: not resolved
```

Both legs passed. The arm then **resolved ZERO factory spellings across 5,626
measured real sources — 39 of which construct an HTTP client, and not one of
which returns the constructor directly.** Every real factory in the fleet
memoises, because a pooled or lazily-built client is the entire reason to have
a factory:

```python
def _pooled_client():
    global _client
    if _client is None:
        _client = httpx.Client(base_url=settings.crm_url)
    return _client                          # returns a LOCAL, not a constructor
```

(`dotmac_sub`'s `crm_client._pooled_client` and
`core_router_metrics._get_client` are the live instances.)

The arm's report was "no new hits". That reads as *precision*. It was
*blindness*. Nothing in the sensitivity proof, the specificity proof, the
review, or the green run could tell the two apart — only measuring the arm
against the real corpus could, and nothing did.

**A detector that is correct on its fixture and inert on its subject is worse
than an honest undercount, because it also reports coverage.**

### Liveness is per ARM, never per category

A category that fires 94 times says nothing about which of its arms fired. The
external-connector `http_client` category is live at 94 real sources — entirely
through its `import httpx` arm; its `from httpx import Client` arm resolves 0
across the same corpus. A single count per category lets a live direct-call arm
conceal an inert factory arm indefinitely.

So: **the unit of proof is the arm, not the check, not the category, and not
the file.** When an arm is added, narrowed, or widened, that arm carries its own
three legs. Retiring an arm is a deletion, recorded as one — not an arm left in
place with nothing driving it.

This repository's own instance of the same failure, found by the audit that
produced this amendment: the external-connector sweep's
`sync_checkpoint` "ambiguous `*Cursor` that names its feed" arm — added
2026-08-13 to fix a real miscount — fires on **0 of 4,378 real runtime
sources**, while a test named `test_narrowing_the_cursor_rule_did_not_blind_
the_checkpoint_detector` asserts anti-blinding entirely against string
literals. Sensitivity ✓, specificity ✓, liveness never asked.

### A legitimate zero baseline is proved by IN-SITU MUTATION

Most guards in a healthy repository are supposed to match nothing. Liveness
must therefore NOT be read as "existing debt must exist" — that would reward
leaving violations in the tree, and would make the newest, cleanest guard the
one that can never be proved.

**For a legitimate zero baseline, liveness is proved by mutating the REAL
repository scan.** The proof:

1. runs the detector's own DISCOVERY over the real corpus, and asserts it
   reached the file it is about (not a glob nobody checked);
2. injects a representative violation into that real source — the real bytes on
   disk, discovered the real way, not a fixture string and not a `tmp_path`
   file;
3. drives the detector's real classification path and requires it to fail,
   naming that real file;
4. leaves the corpus unchanged.

Injecting into the corpus the scan already read is preferred over writing to the
working tree: a run that dies between mutation and revert must not leave a dirty
tree or a poisoned guard. What makes it in-situ is that the bytes, the
discovery and the classifier are all the real ones — not that a file was
temporarily overwritten.

A `tmp_path` fixture is not a liveness proof. It proves the classifier's logic;
it cannot prove the classifier is ever handed the subject. The two failures a
`tmp_path` proof cannot see are a discovery glob that has stopped matching, and
a real-world SPELLING the arm does not recognise — which is the shape both the
Governance factory arm and this repository's worked example turned out to be.

### What liveness does not require

* Not a violation left in the tree. See in-situ mutation above.
* Not a slow test. The three legs of the worked example run in milliseconds
  against sources the scan already read.
* Not a measurement of sibling repositories from a repository that cannot see
  them. A fleet-measuring detector that abstains in a single-repo checkout
  cannot prove liveness there, and must carry an in-repo corpus for the arm, or
  say plainly that the arm is unproved in that configuration. Abstaining is
  honest; abstaining while claiming a sensitivity proof is not.

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
* **Every detector arm carries its three legs, named as such.** A test named
  for sensitivity that also happens to demonstrate specificity has proved two
  legs and must say so; the third is missing until something drives the arm
  over the real corpus. `tests/architecture/test_presentation_boundary.py` is
  the worked example in this repository — three legs, three named tests, one
  arm each.
* **Retrofitting is a programme, not a gate — and the programme is itself
  ratcheted.** This repository has arms today with two legs and no third
  (audited 2026-08-15). They are retrofitted as their guards are next touched;
  what is a gate from now on is that a NEW or MODIFIED arm lands with all
  three. A programme with no gate is a wish, so the audited backlog is frozen
  exactly by `tests/architecture/test_guard_proof_ratchet.py` +
  `tests/architecture/guard_proof_backlog.json` — decision point 3 applied to
  this ADR's own retrofit. Three populations, every member named by ARM rather
  than counted, failing when a population rises AND when it falls; regenerate
  with `make guard-proof-baseline`. Measured 2026-08-15: **23 fixture-only
  arms, 22 guards with no firing proof at all, 16 vacuous discovery scopes**.
  The ratchet is inside its own corpus and clean of all three populations, and
  each of its three arms carries its own sensitivity, specificity and in-situ
  liveness leg.

## Alternatives rejected

**Ban exclusions outright.** Unworkable and dishonest: generated files,
vendored code and genuinely closed sets exist, and a rule that is routinely
violated stops being consulted. The problem was never that an exclusion
existed — it was that its premise decayed unobserved.

**Audit exclusions periodically.** Depends on somebody remembering, which is
the failure mode being fixed. `scripts/` was excluded for over a year and
nobody audited it; the exclusion was discovered by asking an unrelated
question about batch scripts.

**Require a non-zero real count instead of an in-situ mutation** (the
2026-08-15 amendment's alternative). "The arm must match at least one real
source" is a one-line rule and would have caught the factory arm. It is
rejected because it is only satisfiable by leaving debt in the tree: the
cleanest guard in the repository — one written before the violation it prevents
ever lands — becomes the one that cannot be proved, and the incentive points at
keeping a specimen violation rather than fixing it. In-situ mutation gets the
same evidence with the opposite incentive.

**Prove liveness once per detector rather than per arm** (same amendment). It
is what a category count naturally gives you and it is exactly the measurement
that hid the factory arm behind a live direct-call arm for the whole of its
existence. A per-detector proof answers "does this file do anything", which was
never the open question.

**Delete the backlog instead of ratcheting it.** Attractive and usually
impossible at the scale these reach — 100 scripts, 75 hand-written
expressions. A ratchet lets coverage land immediately while the backlog
shrinks under pressure, rather than blocking coverage on a rewrite nobody has
time for.
