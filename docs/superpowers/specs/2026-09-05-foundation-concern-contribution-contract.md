# The Foundation concern-contribution contract — DESIGN FOR REVIEW

**Status: PROPOSED. Not accepted, not implemented, and nothing depends on it.**
This document is the review package Michael asked for before nine consumers bind
to a shape. It contains no implementation: no build tool, no module declarations,
no entry-point scanning. Two things in this repository are real today and are
cited rather than proposed — `ApplicationFoundationProfile` and its canonical
encoding, and `IntegrationSurfaceAbsenceProofV1` as a slot value (landed
2026-09-05).

Everything under "PROPOSED" below is a shape to argue with.

---

## 0. The rule this whole design exists to serve

> **Applications supply declarations. Foundation supplies meaning.**

Stated as an obligation rather than a slogan, because it is checkable:

An application assembly MAY declare its identity, the modules and versions it
selects, its migration and plane selections, its profile selection, its product
descriptor, and thin adapters into owning services.

An application MUST NOT implement profile canonicalization, profile
verification, deployment execution, or settlement translation. **There is no
adapter for these.** Producing or translating the Foundation contract through an
application-local adapter preserves two competing contracts, and two contracts
that agree today are two contracts that diverge on the day one is improved. That
is compatibility plumbing, not composition.

The test of the rule is mechanical: **if a value could differ between two
correct deployments of the same artifacts, it is not profile material** — it
belongs to settings, the deployment descriptor, or the entitlement surface.
`ConcernBinding`'s closed field set already states this and refuses an extra key
rather than ignoring it; the contract below inherits that discipline.

---

## 1. Three independently supplied facts, and the joining rule

A provider satisfies a concern only when **three separately produced facts join
on the same typed contract identity and the same artifact coordinates.**

| fact | answers | produced by | produced when |
| --- | --- | --- | --- |
| `ConcernProvider` | *what capability exists, and in which implementation* | the **module** | at module build |
| `ConsumerBinding` | *that the assembly actually injects and uses it* | the **assembly** | at assembly build |
| `ConcernVerification` | *that the composed path was exercised, with evidence* | the **generic tool** | at candidate build |

Each of the three looks complete on its own, and that is exactly the hazard.

* A provider alone is a **declaration**. A module can ship a perfect provider
  into an assembly that never wires it.
* A provider plus a consumer binding is a **wiring claim**. Both can name the
  same capability and the code path can still never execute — the binding is a
  statement about intent, made by the party with the least interest in
  discovering it is wrong.
* A verification alone is **an exercise of something**, and without the other
  two nothing says what.

### 1.1 The join key

```
JOIN = (concern, contract_id, contract_version, artifact_coordinates)
```

All three facts MUST carry all four components, and all four MUST be identical
across the three. Not "compatible" — identical. A join on a subset is the defect
this design is built to prevent: joining on `concern` alone lets a verification
of one implementation certify another's provider; joining without
`artifact_coordinates` lets a verification produced against yesterday's wheel
certify today's.

`artifact_coordinates` is an **immutable** reference — `<name>@sha256:<64 hex>`,
`sha256:<64 hex>`, or a peeled 40-character commit. The existing
`_require_coordinate` rule applies unchanged: an installation adopts by digest,
so a claim measured against a reference that can move is not a claim about any
particular bytes.

### 1.2 Why the tool produces the verification, and the module does not

A module verifying itself is the shape this programme has already paid for: a
binding whose only consumer is a test, a verifier with no caller outside its own
module. The module supplies the **provider**; Foundation supplies the
**question**; the generic tool asks it against the composed assembly and records
what came back. That is what makes the third fact independent, and it is why
`ConcernVerification` is not something a module may declare.

---

## 2. The lifecycle is closed. Only `admitted` fills a slot.

```
declared → resolved → injected → exercised → admitted
```

| state | established by | what it does NOT mean |
| --- | --- | --- |
| `declared` | an entry-point row exists | that the row imports |
| `resolved` | the row imports and yields the typed object | that anything uses it |
| `injected` | a `ConsumerBinding` joins it on the full JOIN key | that the path executes |
| `exercised` | a `ConcernVerification` reports at least one positive outcome | that the answer is correct |
| `admitted` | all three facts join AND the verification is non-vacuous | — |

**Only `admitted` fills a profile slot.** Every earlier state is a refusal with
its own name, and the names matter: an operator repairing "declared but
unresolvable" edits a package, one repairing "no consumer binding" edits an
assembly, and one repairing "broken-shut" edits a provider. Collapsing them into
"not admitted" would send all three to the same place.

Note the final row's second column carefully. `exercised` does **not** mean the
provider's answer is correct. Nothing generic can establish that; correctness is
the concern's own contract test and lives with the module. What the generic tool
establishes is that the composed path is **live and responsive** rather than
declared and dead. Overclaiming here would itself be the defect — a check that
is believed to prove more than it does is worse than an absent one.

---

## 3. Inertness — the definition, and the six refusals

> **"An import, symbol lookup or manifest row alone must never count as
> 'answering.'"**

| # | shape | refusal |
| --- | --- | --- |
| 1 | declared, import-unresolvable | `contribution.unresolvable` |
| 2 | provider present, no consumer binding | `contribution.uninjected` |
| 3 | consumer names another contract or version | `contribution.contract_mismatch` |
| 4 | provider and consumer present, the probe never reaches them | `contribution.unexercised` |
| 5 | the probe produces **only** negative outcomes | `contribution.broken_shut` |
| 6 | runtime readback differs from the admitted artifact | `contribution.readback_drift` |

### 3.1 Refusal 4 — what "the probe never reaches them" means mechanically

Foundation issues a **nonce** with each probe. A `ProbeOutcome` must echo it.

A provider that returns a stored constant cannot echo a nonce it never saw, so
the echo distinguishes *a function that ran* from *a value that was written
down*. This is a modest claim and is stated as one: it establishes liveness, not
correctness. It is nonetheless the exact distinction refusal 4 needs, and it is
the only one available to a tool that knows no product.

### 3.2 Refusal 5 — broken-shut, the subtle one

A provider that refuses everything is not a working provider. A verification
whose every outcome is negative has not demonstrated the path works; it has
demonstrated that it fails uniformly, which is indistinguishable from a provider
that is wired to a dead dependency, misconfigured, or shut off.

This is the **inverse** of a check that cannot refuse, and this programme has
shipped both shapes. The guards that catch the first are the
sensitivity/non-vacuity proofs already required by AGENTS.md rule 25. Refusal 5
is the same discipline pointed at the provider instead of at the guard:

> A `ConcernVerification` MUST carry at least one outcome with `positive: true`,
> and that outcome MUST echo the issued nonce.

A verification of a concern that legitimately has nothing to do does not go here
— that is a **proven absence** (§ 6), which is a different slot value with its
own evidence. Broken-shut and absent-proven must never collapse: one is a
provider that does not work, the other is a subject that is not there.

### 3.3 What is deliberately NOT an inertness signal

* **Coverage.** A provider exercised by one probe is not less real than one
  exercised by ten.
* **Answer content.** The tool knows no product and must not develop opinions
  about payloads.
* **Timing.** A slow provider is not an inert one.

Each of these is a plausible metric that would make the tool product-aware,
which is the property the whole design is protecting.

---

## 3A. ANSWERED: the refusal is discriminated per lifecycle stage

Raised against Platform's inventory: its dialect made admission all-or-nothing,
so `CONCERNS_INCOMPLETE` is a single verdict naming the missing set. Under a
five-state lifecycle a slot can fail at four distinct points and one verdict
loses which. **My answer is yes — the refusal is discriminated, and the verdict
carries the stage.** Three reasons, in the order they bite.

**1. The stages have different owners, so one verdict routes to the wrong desk.**

| stage reached | what failed | who repairs it |
| --- | --- | --- |
| `declared` | the row will not import | the **module** — a packaging defect |
| `resolved` | nothing injects it | the **assembly** — a wiring omission |
| `injected` | the probe never reached it | the **assembly or the tool** — composition |
| `exercised` | every outcome negative | the **module** — a broken provider |

`CONCERNS_INCOMPLETE` naming a set of concerns tells four different people the
same sentence, and three of them will look in the wrong repository first. This
programme already pays that tax elsewhere: an unfetched commit and a divergent
branch produce the same `merge-base` answer, and separating them was worth doing
precisely because the repairs are `fetch-depth: 0` versus a rebase.

**2. Uniform failure is a MEANING, not a count.** Thirteen concerns all failing
at `declared` is a broken install. Thirteen all failing at `exercised` is a dead
dependency or a misconfigured environment — the composition is right and the
world is wrong. An incomplete-set verdict renders those identically, and they
are the two most common real outcomes.

**3. Without the stage, `broken_shut` cannot be reported at all.** It is not a
missing concern; it is a present, resolved, injected, exercised concern whose
answers were all negative. Folded into "incomplete", the subtlest refusal in
this design becomes invisible in the one artifact an operator reads.

### The shape

```
ContributionRefusal = (concern, stage_reached, code, detail)
```

* `stage_reached` is the **last stage the slot actually attained** — not the one
  it failed to reach. "Reached `resolved`" is a fact established by evidence;
  "failed to reach `injected`" is the same fact stated as an absence, and this
  design prefers the positive form everywhere else for the same reason.
* `code` stays the closed vocabulary of § 3 — the stage does not replace it.
  Stage says *how far it got*, code says *what was wrong*, and one does not
  determine the other: `contract_mismatch` can arise at `resolved` or at
  `injected` and the repair differs.
* The profile-level verdict becomes the **set of discriminated refusals**, not a
  set of concern names. `CONCERNS_INCOMPLETE` survives only as a summary that
  must be derivable from the refusals — never a value stored beside them, or
  the two can disagree.

### What this obligates

Adds two rows to § 10: **N17** — every refusal names a stage, and the stage is
the highest actually attained; **N18** — the summary verdict is derived from the
refusals and cannot be set independently. Both need a planted defect: a refusal
with no stage, and a summary that disagrees with the refusals it summarises.

---

## 4. Canonical schemas, and who owns every field

### 4.1 `ConcernProvider.v1` — owned by the MODULE

| field | owner | notes |
| --- | --- | --- |
| `schema` | Foundation | fixed literal |
| `concern` | Foundation vocabulary | one of the closed thirteen |
| `contract_id` | Foundation | the typed concern contract this answers |
| `contract_version` | Foundation | see § 5 |
| `implementation` | module | distribution name |
| `version` | module | distribution version |
| `artifact_coordinates` | module | immutable; see § 1.1 |
| `entry_point` | module | the declared row this was resolved from |
| `displaces` | module | local writers this provider retires |

### 4.2 `ConsumerBinding.v1` — owned by the ASSEMBLY

| field | owner | notes |
| --- | --- | --- |
| `schema` | Foundation | fixed literal |
| `concern` / `contract_id` / `contract_version` / `artifact_coordinates` | Foundation vocabulary, **restated by the assembly** | the JOIN key; a restatement that disagrees is refusal 3, never a merge |
| `injection_site` | assembly | where the assembly wires it |
| `declared_by` | assembly | the assembly's own identity |

The assembly restates the join key rather than referencing the provider's copy.
That is deliberate: a reference cannot disagree, and disagreement is the signal.

### 4.3 `ConcernVerification.v1` — owned by the GENERIC TOOL

| field | owner | notes |
| --- | --- | --- |
| `schema` | Foundation | fixed literal |
| join key (four fields) | tool, **copied from what it observed** | not from either declaration |
| `probe_id` / `nonce` | Foundation | issued per probe |
| `outcomes` | tool | each: `positive`, `nonce_echo`, `detail` |
| `observed_at` / `observed_by` | tool | provenance |

### 4.4 `ApplicationFoundationProfile.v1` — owned by FOUNDATION (exists today)

Unchanged by this design except that a slot may now hold a proven absence. The
application supplies **selection**; Foundation supplies **structure, admission
and meaning**.

---

## 5. Compatibility and duplicates

**Incompatibility.** `contract_version` is compared by exact identity, not by
range. A running Foundation declares the set of contract versions it accepts; a
contribution outside that set is refused (`contract_mismatch`) rather than
coerced. A range would make "which contract is this" a computation rather than a
fact, and two parties computing it is how one name comes to identify two
contracts — the `0.3.0a2` defect this repository has already paid for.

**Duplicates.** Two admitted contributions for one concern is a **refusal**
(`contribution.duplicate`), naming every declarer. Which one wins must never be
an iteration-order accident. This mirrors `discovery.discover_one`'s first
refusal and must NOT be resolved through `displaces`: that field names local
writers a provider retires, not rival contributions, and overloading it would
turn a refusal into a race the loser cannot see.

**Missing.** A concern with no admitted contribution and no proven absence is a
refusal. That is the 13/13 gate, and it is why `not-yet-implemented` has no
constructible slot member.

---

## 6. Absence-proof integration (item 2, folded in)

Landed 2026-09-05, and cited here as the shape the contract must preserve.

A concern slot may hold a **proven absence**. Three constructible slot members
for four states; `not-yet-implemented` deliberately has none.

Two properties bind on the contract:

1. **It satisfies only when ESTABLISHED, never when merely well-formed.**
   Construction settles well-formedness alone; the proof is re-asked at
   verification against an independently derived artifact digest and inventory
   digest. **A slot whose evidence was not supplied is a finding, not a pass.**
2. **The proof binds the application wheel, not a self-referential image.** A
   proof embedded in an image cannot carry that image's own digest. Hence
   `artifact_digest`, and hence two distinct inputs to verification: the image
   digest a BINDING is checked against, and the artifact digest a PROVEN ABSENCE
   is checked against. Comparing a proof against the image digest could never
   match and would make the gate unsatisfiable — the failure that looks like
   strictness.

A proven absence is **not** an inertness escape hatch. Broken-shut (§ 3.2) is a
provider that does not work; absent-proven is a subject that is not there. A
contribution that fails § 3 may not be re-filed as an absence.

---

## 6A. The fleet lifecycle this contract sits inside

```
retained artifact → artifact admission → target-bound plan → signed authorization
→ controlled execution → verified evidence → durable settlement
```

The contract in this document occupies **artifact admission** and produces the
profile digest that **target-bound plan** and **signed authorization** bind. It
does not reach further, and the boundary is stated because a contract that
drifted into planning or settlement would be a second authority over them.

| owner | owns | must not own |
| --- | --- | --- |
| **Foundation** | canonical profile, admission, planning, execution, recovery, evidence contracts | durable intent, approval state, operator UI |
| **Application** | thin typed declarations: roles, migrations, ports, readiness, resource needs, product-specific preconditions | profile canonicalization, profile verification, deployment execution, settlement translation |
| **Control** | durable intent, approval, authorization, rollout state, settlement | artifact meaning |
| **Platform CP** | operator UI and orchestration wiring | any profile dialect |
| **Runtime providers** | generic Compose / PostgreSQL / Nginx / Kubernetes effects | anything ERP-, Sub- or Platform-specific |
| **Kernel** | in-process mechanics | deployment orchestration |

### Build-once, as checkable rules

Each is a property this contract must not make unrepresentable:

1. the artifact is built **exactly once**; verification and retention are of
   **those exact bytes**;
2. the **same digest** is promoted through rehearsal and production —
   `artifact_coordinates` is in the join key precisely so this is checkable
   rather than asserted;
3. **host inventory, environment policy and secret pointers bind AFTER build.**
   None of them may appear in a `ConcernProvider`, a `ConsumerBinding` or the
   profile. This is the closed-field-set rule stated as a lifecycle property: if
   a value could differ between two correct deployments of the same artifacts,
   it is not profile material;
4. an artifact is **never rebuilt to change environment configuration** — a
   rebuild produces new bytes, and every digest bound to the old ones becomes a
   claim about something that no longer exists;
5. **no secret value** in an artifact, plan, receipt or log. The contract's
   documents carry **pointers and names only**, which is ADR 0009's held-never-
   dereferenced rule applied to a fourth surface.

Per release only the deployment transaction repeats — select artifact, derive
plan, approve, execute, settle. **No reminting identities, recreating targets,
redesigning adapters, or rebuilding environment-specific images.** A contract
that required any of those per release would have moved a build-time fact into
the release loop, which is the defect rule 4 names.

---

## 7. Artifact / build / runtime boundary

| stage | subject | may read | may NOT read |
| --- | --- | --- | --- |
| **module build** | the module's own wheel | its own source | any assembly |
| **assembly build** | the assembly's declaration | selected module identities | provider internals |
| **candidate build** | the **installed distributions** | the installed inventory of the exact artifact | any source checkout |
| **runtime** | the running system | its own admitted digest | nothing it may edit |

The candidate-build row is the one with a history. A profile in
`deploy/product.toml` would be **a checkout fact describing an image**: a source
tree states what an assembly intends to compose, an image holds what it will
run, and the two differ routinely and innocently — an uncommitted pin, a build
argument, a dependency resolved to a newer compatible release, a wheel that
never reached the registry.

At runtime the readback **compares and never derives** (refusal 6). Editing an
accepted profile to match what a deployed image turned out to contain inverts
the relationship, and from that moment drift and correction arrive as the same
commit.

---

## 8. Canonical bytes and digest

Reused verbatim from `canonical_profile_bytes`, not re-specified — a restatement
of a rule is a copy of it:

* sorted keys at **every** depth;
* separators `(",", ":")` — no spaces;
* `ensure_ascii=True`;
* UTF-8 encoded;
* **no wrapper**: the digest covers the document alone. Hashing a wrapper that
  merely contains one is how two parties compute permanently unequal values
  while both look correct;
* the document's `schema` is checked before hashing, so a non-profile cannot be
  hashed as one.

`profile_digest` = `sha256:` + hex over those bytes.

The three contribution documents use the **same** rules. One canonicalizer, one
answer.

---

## 9. Golden byte fixtures

Computed from the shipped canonicalizer at this commit, not written by hand. The
committed fixture is `docs/inventories/foundation-profile-golden.json`.

Bound slot (`identity_session`):

```json
{"coordinates":"acme-foundation@sha256:bbbb…bbbb","displaces":[],"implementation":"acme-foundation","state":"bound","version":"1.0.0"}
```

Absent-proven slot (`integration`) carries, sorted: `artifact_digest`,
`concern`, `established_at`, `established_by`, `families`, `method`,
`observed_inventory_digest`, `positive_control`, `schema`, `source_revision`,
`state`.

Canonical form begins:

```
{"application":"acme-app","concerns":{"api_web_interaction":{"coordinates":…
```

* canonical length: **3320 bytes**
* profile digest: **`sha256:aa4e0f9334c57d089efc3b85debfa49f5103a0bbf2cf59d756948b4d3c906b4a`**

A golden fixture nobody re-derives is the inert shape this document is about, so
one architecture test re-derives the digest from the committed fixture through
the shipped canonicalizer. That test guards the FIXTURE; it does not implement
the contract.

---

## 10. Negative and non-vacuity matrix

Every row is an obligation on the implementation (item 7), not a claim that it
exists. "Non-vacuity" is the column that fails in practice.

| # | negative case | must refuse with | non-vacuity partner |
| --- | --- | --- | --- |
| N1 | entry-point row that will not import | `unresolvable` | a row that imports is admitted |
| N2 | provider, no consumer binding | `uninjected` | provider + binding proceeds |
| N3 | consumer names another `contract_id` | `contract_mismatch` | identical ids join |
| N4 | consumer names another `contract_version` | `contract_mismatch` | identical versions join |
| N5 | consumer names other `artifact_coordinates` | `contract_mismatch` | identical coordinates join |
| N6 | verification present, no outcome echoes the nonce | `unexercised` | an echoing outcome admits |
| N7 | all outcomes negative | `broken_shut` | ≥1 positive echoing outcome admits |
| N8 | two providers for one concern | `duplicate` | one provider admits |
| N9 | concern with nothing at all | `missing` | 13/13 admits |
| N10 | runtime digest ≠ admitted digest | `readback_drift` | equal digests pass |
| N11 | join on a SUBSET of the key | must be unrepresentable | the join function takes all four |
| N12 | absence proof, no observed inventory | finding | supplied inventory verifies |
| N13 | absence proof for another artifact | finding | matching artifact verifies |
| N14 | absence proof misfiled under another concern | `absence_proof.wrong_concern` | correctly filed admits |
| N15 | a moving `artifact_coordinates` | coordinate refusal | an immutable one admits |
| N16 | **the tool admits everything** | — | every refusal above must be shown FIRING |
| N17 | a refusal carrying no lifecycle stage | must be unrepresentable | every refusal names the highest stage ACTUALLY attained |
| N18 | a summary verdict disagreeing with its refusals | must be unrepresentable | the summary is derived, never stored beside them |
| N19 | the generic path reproduces 53 and produces no new refusal | acceptance failure | `uninjected`, `unexercised` and `broken_shut` each shown firing |

**N16 is the one that must not be skipped.** A verifier that has never refused
anything and a composition that is correct are the same colour. Rows N1–N15 each
need a planted defect and a near miss, and the suite must fail if the detector
is removed.

**N12–N14 exist today** and are exercised
(`test_deployment_foundation_application_profile.py`). N1–N11 and N15–N19 are
owed by item 7.

**N19 is the migration's acceptance criterion**, and it is deliberately not
satisfiable by parity alone: reproducing all 53 of Platform's rows while adding
no refusal would mean the generic path had reproduced the dialect rather than
replaced it. See § 11 step 3.

---

## 11. Migration from Platform's #166 / #168 — an EXTRACTION, not a deletion

Michael's framing, which is the standard this programme already applies to
product-first extraction: those PRs were **sound under the former direction.
They become superseded implementation — not bad implementation, and not
programme progress.**

> **Updated 2026-09-05.** The parity inventory is merged (`d7b8ca6a`): **53
> driven rows.** The sequence below is the obligation; the row-by-row contents
> live in Platform's inventory and are not restated here.

1. **Preserve** their behaviour and negative cases as **parity fixtures**. The
   inventory is merged: **53 driven rows — 33 verifier, 14 builder, 6
   type-boundary.**

   One of those rows is *a proof carrying only a concern name, a revision and
   free text must not be admitted.* **Correction, 2026-09-05:** an earlier
   revision of this document said Platform had a MERGED TEST admitting that
   shape. **That was false and is retracted** — the repair landed inside #166.
   What survives is the REQUIREMENT, as a bar in the parity matrix, and it is
   precisely the "well-formed proves nothing" shape § 6 turns on. The false
   version is left visible here rather than quietly deleted, because a wrong
   claim about another team's code is the kind of thing that gets repeated once
   it has been written down.
2. **Land** the canonical Foundation contract and the generic verifier.
3. **Prove** the generic path produces **at least the same refusals**, against
   **real artifact bytes**. Not equivalent-looking output: the same refusals,
   from the same inputs. This is the measurable gate, and it is mine.

   **53 is a FLOOR, not the bar, and the gap is the point.** Platform's dialect
   collapses provider, binding and verification into one declaration plus an
   import probe. A shape that cannot be *stated* cannot be *tested*, so the
   inventory structurally cannot contain a case for:

   * a provider that **resolves but is never injected** (`uninjected`);
   * a provider **injected but never exercised** (`unexercised`);
   * a verification whose outcomes are **all negative** (`broken_shut`).

   Those are three of the six inertness refusals, and they are exactly what the
   three-fact separation exists to catch. So "at least the same refusals" is
   necessary and not sufficient: passing all 53 while producing no new refusal
   would mean the generic path had reproduced the dialect rather than replaced
   it. **The acceptance criterion is 53 reproduced AND the inertness refusals
   Platform could not express, each shown firing.**
4. **Replace** Platform's acceptance invocation.
5. **Delete** Platform's builder, canonicalizer and verifier **in that same
   composed change** — never a separate one, because that produces a temporary
   state with neither verifier.
6. **Ratchet** so the local dialect cannot return: a two-directional guard that
   fails when a product-local canonicalizer or profile verifier reappears, and
   also fails if the count is lowered without the row being removed.

Steps 4–6 are Platform's file set, not mine. Step 3 is mine and gates them.

---

## 12. Reviewed against three consumers, implemented for one

Michael: *"Review the shape against Platform, ERP and Sub, but implement Platform
as the first reference assembly before enrolling the other consumers."*

| consumer | what the shape must survive | open question for review |
| --- | --- | --- |
| **Platform CP** | a concern with genuinely no surface (integration) → the absence slot | does Platform hold any concern where the provider is in-process rather than a distribution? |
| **ERP** | many modules contributing to one assembly; several concerns per module | does one distribution contributing to two concerns need two providers, or one with two rows? **Proposed: two rows** — one provider answers one concern, so the join key stays total. |
| **Sub** | the largest existing writer surface; `displaces` will carry real weight | does Sub have a concern satisfied by two implementations today, which § 5 would refuse as a duplicate? If so the refusal is correct and the repair is Sub's, but it must be found before enrolment, not during. |

Those three questions are the ones I would most like answered in review. Each is
a place where the design could be right in the abstract and unbuildable for a
real assembly.

### 12.1 The composability proof: ERP with NO ERP adapter

> **"Compose ERP as the second consumer without adding an ERP adapter."**

This is the real test of the design and it is worth stating as a falsifiable
claim rather than an aspiration:

**If enrolling ERP requires anything ERP-shaped in Foundation, the contract has
failed.** Not "needs work" — failed, because an adapter per consumer is the
two-competing-contracts shape this whole redirection exists to remove, arriving
one consumer at a time instead of all at once.

Concretely, none of these may be required to enrol ERP:

* an ERP branch, flag or `if product ==` anywhere in Foundation;
* an ERP-specific concern, or a fourteenth concern added to carry an ERP need;
* an ERP-specific provider base class, mixin or registration helper;
* a Foundation-side translation of an ERP-local profile dialect;
* a widened `contract_version` range to admit ERP's build.

What ERP MAY supply is exactly what any module supplies: typed
`ConcernProvider` rows, and — as an assembly — `ConsumerBinding` rows and thin
typed declarations. If ERP cannot be expressed that way, the missing expressive
power is a **generic** gap in the contract and the repair is generic.

The design's known pressure point for ERP is in the table above: one
distribution contributing to two concerns. The answer proposed there — **two
provider rows, one per concern, so the join key stays total** — is what keeps
this generic. A single row with two concerns would make the join key partial for
one of them, and a partial join is the defect § 1.1 exists to prevent.

**This proof is scheduled, not assumed.** It belongs to the enrolment step and
must be run before ERP is composed, not after — the cheapest moment to discover
an ERP-shaped requirement is while the contract is still a document.

---

## 13. What this document deliberately does NOT do

* no build tool (item 7);
* no module declarations (item 6);
* no entry-point group is claimed or reserved;
* no ADR number is allocated — this is a proposal in
  `docs/superpowers/specs/`, which the repository defines as
  **non-authoritative intent**. It becomes authoritative by an accepted ADR, not
  by being merged.
