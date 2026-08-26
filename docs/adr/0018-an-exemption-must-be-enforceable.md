# ADR 0018 — A guard exemption must carry an enforceable premise

**Status:** Accepted — **fleet-wide**. Amended 2026-08-26 (a guard named for a
property it does not test — signed release pipelines verify the produced
artifact's application identity and actual signing certificate, not secret or
file existence). The amendment is a dated addition; no earlier text is
rewritten.
**Date:** 2026-08-11
**Applies to:** every Dotmac repository. Enforcement for repositories other than
the starter lands through the pinned Governance source (hard rule 15); this ADR
is the statement of the rule.
**Extends:** ADR-0010 (adapters are thin, *and identifiable by a rule*) — the
same concern seen from the other side: 0010 asks what a guard must cover, this
asks what may be excluded from one and on what terms.
**Owns:** the rule that an exclusion from a lint, type, architecture or security
check states a premise, that the premise is machine-checkable, and that any
resulting backlog is a two-directional ratchet; and (2026-08-26 amendment) the
mirror rule that a guard must test the property it is named for, stated for
signed release pipelines
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

## Decision amendment — 2026-08-26 (a guard named for a property it does not test)

**Scope — this is the ruling, not a summary of it.** *Every signed release
pipeline verifies the produced artifact's application identity and its actual
signing certificate, not merely secret or file existence.* The amendment is
stated for **signed release pipelines**, because that is where the evidence is
and where the consequence is a signed artifact that ships. It is not a general
licence to demand deep inspection from every check.

The original decision covered exclusions: a region deliberately left out of a
guard. This amendment covers the mirror case, which turns out to be the same
failure wearing the opposite costume — a region formally **inside** a guard whose
check does not test the property the guard is named for. Both produce an
unmonitored region. Only one of them looks unmonitored.

**The evidence.** `dotmac_sub/.github/workflows/mobile-release.yml`, at the
audited revision `1a3edf0eb`, contains a step named:

```yaml
      - name: Verify the artifact is not debug-signed
        run: |
          OUT=$(find build/app/outputs -name '*.aab' -o -name '*-release.apk' | head -1)
          echo "Artifact: $OUT"
          test -n "$OUT"
```

`test -n "$OUT"` asserts that `find` matched a filename. It says nothing about
signing, nothing about which key signed, and nothing about which application was
built. **It cannot fail for the reason it is named for.** It would have passed on
a debug-signed bundle, and it would have passed on a correctly signed bundle of
the *wrong application* — which matters here, because the same audit found an
Xcode Cloud post-clone hook pointing at `$REPO/mobile` in a repository where that
path is the *self-care* app rather than the field app. `dotmac_sub` draft PR
#2716, *"fix(field_mobile): a real field release pipeline and an iOS script that
builds the right app"*, replaces it with real certificate inspection; it is
**open, not merged**, as of 2026-08-26.

**6. A release-pipeline guard verifies the produced artifact, not its
preconditions.** Concretely, for a signed release:

- **Application identity is read from the built artifact** — the Android
  `applicationId` / iOS `CFBundleIdentifier` as it appears in the output, not as
  it appears in a source file, a Gradle property or a workflow input. A pipeline
  that builds the wrong application from correct configuration is exactly the
  failure a source-side check cannot see.
- **The signing certificate is inspected** — issuer, subject and fingerprint of
  the certificate that actually signed the artifact — and compared to an expected
  value. "A signing secret was present in the environment" is a precondition, not
  a result. So is "a file was produced".
- **The check carries the sensitivity proof rule 5 already requires.** A release
  guard that has never been shown RED against a deliberately debug-signed or
  wrongly-identified artifact is indistinguishable from `test -n`.

**And the naming rule that follows.** A step's name is read as its contract by
every reviewer who does not open it. A step named for a property it does not test
is worse than an unnamed one, because it converts an unmonitored region into a
region everyone believes is covered — the precise inversion the original decision
was written to prevent. Either the check tests the named property, or the step is
renamed to what it actually does.


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
