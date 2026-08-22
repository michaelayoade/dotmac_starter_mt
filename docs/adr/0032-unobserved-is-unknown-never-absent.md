# ADR-0032: Unobserved is UNKNOWN, never ABSENT

- Status: Accepted
- Date: 2026-08-16
- Deciders: Michael
- Related: ADR-0018 (an exemption states an enforceable premise), ADR-0031 (an
  authority cutover is sealed by its own evidence)

## Context

Decisions in this fleet are increasingly gated on an observation of a real
system: is this estate empty, does this table exist, is this privilege held, is
this database provisioned. The gate is only as good as the observation behind
it, and observations fail in two very different ways that are easy to conflate:

- **the thing is not there** — a real, decisive fact;
- **we could not look** — no tooling, no access, no mechanism, no credential.

The second is not weak evidence for the first. It is not evidence for it at all.
Yet it presents as a result, arrives at the same moment a result would, and
answers the question in the direction that lets work proceed. That is what makes
it dangerous: the conflation is *convenient*, and it appears at exactly the point
where someone is trying to finish something.

This ADR exists because the fleet came within one step of that error, on a
consequential decision, and avoided it only because a check refused to answer.

### The episode

Vendor CP's approvals estate had to be measured before its authority could move
to `dotmac-approvals`. An empty estate licensed a greenfield switch that retires
a writer and drops tables; a populated one required a sealed cutover.

A read-only inventory tool was built, reviewed and merged for exactly this
question. It then could not be run: no published image contained it, and the
sanctioned in-network path could not be pointed at one without editing
production compose — which would have been a deployment change, not a read-only
inventory.

**It reported neither "empty" nor `TARGET_ABSENT`. It reported that it could not
run, and explicitly declined to convert that into a statement about the
database.** The decisive observation came later, from a different, directly
authorized check that actually reached the host.

Had "no mechanism to run the tool" been recorded as "the target is absent",
the greenfield path would have been taken on manufactured evidence — and it
would have been the most consequential decision in the programme, because
absence alone was sufficient to switch authority and drop tables.

The tool's value turned out not to be measurement. **Its value was refusing to
answer.**

## Decision

### 1. Unavailable tooling or access yields UNKNOWN

A check that cannot reach its subject reports **UNKNOWN**, naming what blocked
it. It never reports absence, emptiness, zero, healthy, clean, or any other
substantive value.

This holds regardless of how strongly the surrounding circumstances suggest the
answer. Plausibility is not observation. "Nothing in the repository records this
database being provisioned" is a fact about the repository, not about the
database.

### 2. UNKNOWN never satisfies a gate that requires an observation

If a decision is gated on an observed fact, UNKNOWN blocks it. The gate is not
downgraded, waived, or satisfied by a preponderance of indirect signals.

An UNKNOWN is a prompt to obtain access or tooling — or to have an authorized
human observe directly — not a prompt to reason around the gap.

### 3. The distinction is preserved in the record, not collapsed on the way

Reports, documentation and knowledge entries state which of the two happened.
Wherever a summary says a measurement was taken, the artifact that took it must
actually have run.

This matters more than it sounds, because **the false version reads better.**
"We built a tool, the tool measured, we acted" is a cleaner story than "the tool
could not run, so it declined to answer, and a human looked instead." A summary
that credits the tool does not merely get provenance wrong — it teaches the
opposite lesson to the next reader, who will take from it that the tool
succeeded rather than that declining was correct.

An inventory of this class had exactly that misattribution in six checked-in
places before it was corrected.

### 4. Declining to answer is a success condition

A check that reports UNKNOWN when it cannot observe has **worked correctly**. It
should not be treated as a failure, a blocker to route around, or a defect in
the check.

Guards, agents and operators are expected to refuse in this situation, and a
refusal is not overridden by the confidence, seniority, or urgency of whoever
asks. An instruction that arrives without the authority it claims is not an
authorization, and a rule that bends when the asker sounds certain is not a
rule.

### 5. The mechanism stays product-local until reuse is proven

This ADR is the **rule**. No shared UNKNOWN/ABSENT type, result envelope, or
probe framework is extracted. Each product implements the distinction in its
own checks.

Extract a shared mechanism only when **two current consumers** need it, and
migrate both in that change — the bar stated in ADR-0031's amendment. One
instance is not a pattern.

## Consequences

- A blocked check is a normal, reportable outcome with its own vocabulary. Tools
  in this class need a third result, not a boolean.
- Some decisions will wait on access rather than proceed on inference. That is
  the intended cost.
- Reviewers should ask, of any absence-based decision: *what observed it, and
  did that artifact actually run?*
- Where an observation must be made by an authorized human rather than
  automation, the record names who observed it and by what means — as a fact
  about provenance, not as a lesser form of evidence.

## Alternatives rejected

**Treat a blocked check as a soft negative, weighted with other signals.** This
is the error, dressed as rigour. Combining a non-observation with indirect
signals produces a confident number from no information.

**Let strong circumstantial evidence stand in.** The repository's deployment
history is genuinely informative about the repository. A hand-provisioned
database, a manual dispatch, or infrastructure outside the tree leaves no trace
in it — so the inference is unsound precisely in the cases that matter.

**Extract a shared result type now.** One production instance. ADR-0006's
product-first rule forbids it, and a shared envelope shaped by a single adopter
becomes a constraint later adopters inherit without having agreed to it.
