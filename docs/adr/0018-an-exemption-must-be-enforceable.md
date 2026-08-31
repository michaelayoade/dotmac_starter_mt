# ADR 0018 — A guard exemption must carry an enforceable premise

**Status:** Accepted — **fleet-wide**. Amended 2026-08-26 (a guard named for a
property it does not test — signed release pipelines verify the produced
artifact's application identity and actual signing certificate, not secret or
file existence), and 2026-08-31 (a guard's subject, extent and lifetime must
each be correct). Each amendment is a dated addition; no earlier text is
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

## Decision amendment — 2026-08-31 (subject, extent, lifetime)

The 2026-08-26 amendment named one failure: a guard formally inside a check
whose test does not assert the property it is named for. A single day's evidence
produced eight more instances, and they do not all have that shape. They have
three, and the three are worth stating together because **they are one rule**:

> **A guard makes a claim about a SUBJECT, over an EXTENT, for a DURATION.**
> Each of the three can be wrong independently, while the other two look fine —
> and all three produce the same thing, which is an unmonitored region that
> reports as covered.

This is not three new rules. It is the existing rule — an exclusion states an
enforceable premise — applied to the three properties a guard's premise can be
wrong about.

### Shape 1 — wrong SUBJECT: the guard checks a name, not the property

Five instances:

- `"PYTHONPATH" not in dockerfile` failing on **the comment explaining its
  absence**;
- a ledger test catching **its own docstring**;
- an executor verb detector reading a **usage comment** as a deployment, and
  its edge resolver drawing a call edge **backwards** — because a path mention
  is symmetric while invocation is not;
- a connector ratchet failing on a **type-annotation import**;
- a shared rejection vocabulary breaking two other owners who had imported it
  **verbatim as their contract**.

Every one is the same mistake: matching the *spelling* of a thing instead of
establishing the *fact*. The repair is always to move from a name to a
property — parse rather than grep, strip prose before scanning, resolve an
identity from metadata rather than matching a string. The last instance adds a
corollary worth stating: **a vocabulary that other owners import verbatim is a
published contract**, and changing it is a contract change regardless of where
the file lives.

### Shape 2 — wrong EXTENT: a hand-maintained list, or authored files only

Two instances, and the second is sharper.

`dotmac_vendor_control_plane`'s profile guard **enumerated five stateful modules
by hand while the assembly composed six**, so `deployment_control` was covered
by nothing. A guard whose extent is a list drifts the moment the tree grows, and
it drifts silently, because the list is not wrong about anything it contains.

And in `dotmac_erp` (PR #426, merged `4ab8761d`),
`test_boot_time_installer_is_retired_from_all_compose_roles` asserted a deleted
entrypoint's absence — **scoped to the root compose only**. The rendered sibling
`deploy/rendered/docker-compose.yml` still named `/app/entrypoint-monitoring.sh`
in every role, a file absent from the image's COPY allowlist. **ERP's rendered
project could not have started, and the guard for exactly that defect was
looking one file away.**

That case deserves naming in its own right:

> **A repository that renders deployment artifacts has TWO populations, and a
> guard written against the authored one is silent over the deployed one —
> which is the population that matters.**

The repair for both halves is the same: **derive the extent, never declare it.**
A glob that finds six modules finds the sixth; a walk that does not exclude
`deploy/rendered/` sees the artifact that ships.

### Shape 3 — no EXPIRY: a relaxation that never says when it ends

`dotmac_erp` carried `require-real-digests: false` **while its descriptor
already held a real digest**. Nothing recorded the condition under which it
should be armed, so it was never armed. The relaxation was correct when written,
became unnecessary, and stayed off — and nothing could notice, because nothing
had been told what "no longer necessary" looked like.

This is not a new principle. It is § 2 read carefully:

> **An exemption must carry an enforceable premise — and a premise with no
> expiry is not enforceable. It is permanent by default while reading as
> temporary.**

The conforming shape exists and should be cited rather than invented:
`dotmac_vendor_control_plane`'s ADR 0017 records its two gaps **with stated
retirement conditions**, and this repository's `deployment-adopter.yml` states
"THE EXACT CONDITION FOR RE-ENABLING" as a numbered list. A relaxation that
names the place it is re-armed makes a **checkable** claim; one that names
nothing makes none.

## Enforcement

`tests/architecture/test_guard_subject_extent_lifetime.py`, with a sensitivity
proof per shape — the guard fires on the planted defect and stays silent on the
conforming form.

- **Subject** — enforcement ships in `scripts/executor_retirement.py`
  (`executable_text()` strips prose before detection and before edge
  resolution; `sanctioned_entry_points()` resolves an identity from installed
  distribution metadata rather than matching a name). The test asserts the RULE
  in both directions: a usage comment is not a deployment, and an inline
  trailing comment still keeps its command — because over-stripping produces
  false negatives, which is the direction that hides a defect.
- **Extent** — the Compose population is discovered by glob and a test asserts
  it is not a literal list; a planted `deploy/rendered/` artifact proves the
  walk reaches the rendered population; and the loopback guard's scoping
  premise ("trust auth is here and only here") is now itself checked, so a
  second file gaining `POSTGRES_HOST_AUTH_METHOD` fails rather than passing
  unnoticed. That premise was true and unchecked, which is the state every
  wrong-extent guard is in before it is wrong.
- **Lifetime** — a relaxation whose premise names where it is re-armed must be
  armed there. The detector is deliberately narrow: a generic "every `false`
  needs a comment" check fires on `required: false` and
  `cancel-in-progress: false`, which are not relaxations at all — that is shape
  1 arriving inside shape 3's guard.

**This amendment introduces its own guard over a region that already has debt,
and does it the way § 3 requires**: `docs/inventories/guard-lifetime-baseline.json`
records the one live instance exactly — `deployment-conformance.yml`'s
`strict-image-audit`, whose premise says "the release lane turns it on" while
this repository's only caller is dispatch-only and passes it nothing — and
ratchets it two-directionally. A blanket allow would have been the easier
choice and the wrong one.

