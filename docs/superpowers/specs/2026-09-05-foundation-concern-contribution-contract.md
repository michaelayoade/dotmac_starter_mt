# The Foundation concern-contribution contract — DESIGN FOR REVIEW

**Status: CONTRACT DESIGN ONLY — revision 2, after independent review.**
Not accepted, not implemented, and nothing depends on it. This is **not** profile
admission and **not** cutover readiness; governance ADR 0039 remains Proposed and
unenforced.

**Revision 2 changes** (independent review + Michael's nine-point gate): the
nonce echo was **defeated** and is replaced by a Foundation-owned versioned
scenario battery with negative scenarios (§ 3); the join key grows from four
module-scoped components to **five**, two of them assembly-scoped (§ 1.1); the
probe goes through the assembly's **composition root** rather than the entry
point (§ 3.2); the successor is a **new versioned contract**, not a widening
(§ 3B); documents close on **decode** as well as encode and carry no free text
(§ 4.4–4.5); `InventoryDigest.v1` becomes Foundation-owned (§ 5A); absence
inventories become **per concern** (§ 6.1); displacement and retirement are
canonical, and V1 now **rejects** retirement (§ 6B, landed); admission joins the
**retained-artifact release path** (§ 12A); and the parity map is committed and
two-directional (§ 11).
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

### 1.1 The join key — FIVE identities, not four

**Revised after review.** The first revision's four-part key was entirely
**module-scoped**, so a verification produced against assembly A joined cleanly
to assembly B's binding whenever both pinned the same wheel. Two assembly-scoped
identities close that, and a typed injection site closes the wiring hole:

```
JOIN = (
  assembly_artifact,        # the exact assembly / candidate artifact
  composition_digest,       # Foundation-canonical digest of the composition
  concern_contract,         # (concern, contract_id, contract_version)
  provider_artifact,        # resolved provider artifact + entry point
  injection_site,           # TYPED, not a string path
)
```

All three facts carry all five, and all five MUST be **identical** across the
three — not compatible, identical. Each omission is exploitable:

| omit | what becomes joinable |
| --- | --- |
| `assembly_artifact` | assembly A's verification certifies assembly B |
| `composition_digest` | a re-composed assembly reuses an old verification |
| `concern_contract` | one contract's evidence answers another's question |
| `provider_artifact` | yesterday's wheel certifies today's |
| `injection_site` | an object wired somewhere else satisfies this slot |

`assembly_artifact` and `provider_artifact` are **immutable** references —
`<name>@sha256:<64 hex>`, `sha256:<64 hex>`, or a peeled 40-character commit.
`_require_coordinate` applies unchanged: an installation adopts by digest, so a
claim measured against a reference that can move is not a claim about any
particular bytes.

`injection_site` is **typed**, not a dotted string. A string is a name two
parties can spell differently while meaning the same site, or spell identically
while meaning different ones; a typed site is compared by identity.

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

## 3. Inertness — REVISED after independent review

### 3.0 The previous revision was defeated, and here is the defeat

The first revision of this document proposed a Foundation-issued **nonce** that
a `ProbeOutcome` must echo, and claimed it distinguished a live provider from a
declared-and-dead one. The reviewer produced this:

```python
def probe(self, nonce):
    return ProbeOutcome(positive=True, nonce_echo=nonce, detail="ok")
```

It resolves, it echoes, it reports positive, and it fills a slot while
implementing nothing. **The inert surface did not close; it moved from a
manifest row to a five-line method** — and § 3.3 then forbade the tool from
holding the one opinion that could have told the difference.

Two further defects, both structural:

* **the probe resolved the provider from the ENTRY POINT, not through the
  assembly's composed graph.** A nonce echo was therefore compatible with a
  module installed and never wired. Refusal 2 caught a forgotten *row*, and the
  author of a forgotten row is the same person as the author of forgotten
  wiring;
* **every component of the join key was module-scoped**, so a verification
  produced against assembly A joined cleanly to assembly B's binding whenever
  both pinned the same wheel.

§ 2 conceded that `exercised` ≠ correct and said correctness lives with the
module. Nothing required that test to exist, run, or be evidence — and § 1.2's
own argument is that a module cannot be trusted to verify itself. The concession
was load-bearing and unfunded.

This section is the repair. It is recorded rather than replaced silently,
because the defeated design is the reason the new one has the shape it has.

### 3.1 Foundation owns a VERSIONED SCENARIO BATTERY, and judges the answers

> *"A provider-supplied boolean or nonce proves only reachability; Foundation's
> versioned battery must independently judge positive and negative scenarios."*

Per concern contract, Foundation defines a **versioned battery**: a set of
scenarios, each with an input Foundation constructs and an **expected outcome
Foundation knows**. The provider does not report whether it succeeded. It
returns an answer; **Foundation compares.**

The battery has two halves, and both are required:

| half | scenario expects | catches |
| --- | --- | --- |
| **positive** | the provider ACCEPTS and answers | a provider that refuses everything (`broken_shut`) |
| **negative** | the provider REFUSES | a provider that accepts everything — **the five-line stub** |

That pairing is the repair. `return positive=True` passes every positive
scenario and **fails every negative one**, because it says yes to inputs the
contract requires it to refuse. A stub inverted to refuse everything passes the
negatives and fails the positives. **A provider must pass both halves**, and no
constant satisfies both.

The battery is **versioned** and its version is part of the join key, so
"which questions were asked" is a fact about the record rather than about
whichever Foundation happened to run.

**Foundation writes the batteries.** They are per *concern contract*, not per
product — a battery that needed to know about ERP would be the adapter this
whole redirection removes.

### 3.2 The probe goes through the ASSEMBLY'S COMPOSITION ROOT

The tool does not resolve the provider from its entry point and call it. It asks
the **assembly's composition root** for the object it injected at the declared
**typed injection site**, and runs the battery against *that*.

Consequences, each of which was a hole before:

* a module installed but never wired has **nothing at the injection site**, so
  the battery cannot run — `uninjected`, distinguishable from a missing row;
* an object present at a **different** site than declared is `wrong_site`;
* the object obtained must have the **same artifact identity** as the resolved
  provider, or the assembly wired something else with the right shape.

### 3.3 The refusals

| # | shape | refusal |
| --- | --- | --- |
| 1 | declared, import-unresolvable | `unresolvable` |
| 2 | provider present, no consumer binding | `uninjected` |
| 3 | consumer names another contract/version/artifact | `contract_mismatch` |
| 4 | nothing at the declared injection site | `unexercised` |
| 5 | object present at a site other than the declared one | `wrong_site` |
| 6 | battery run, **every** outcome negative | `broken_shut` |
| 7 | **positive scenarios pass and negative scenarios also "pass"** | `answers_everything` |
| 8 | verification joins a different assembly | `wrong_assembly` |
| 9 | inventory digest not Foundation-computed over the typed inventory | `foreign_inventory` |
| 10 | any document carries an unknown key | `unknown_key` |
| 11 | runtime readback ≠ admitted artifact | `readback_drift` |

Refusal 7 is the one this revision exists to add, and refusals 6 and 7 are
**duals**: a provider that refuses everything and a provider that accepts
everything are both non-providers, and each is invisible to the check that
catches the other.

### 3.4 What the battery still does NOT establish, stated plainly

It establishes that the composed path **discriminates** — that it accepts what
the contract says to accept and refuses what it says to refuse, on Foundation's
inputs. It does not establish that the provider is *correct* on inputs the
battery does not contain.

That limit is real and is not patched over with a metric. It is narrower than
the previous revision's limit by exactly the amount that matters: the previous
design could not distinguish a stub from an implementation at all.

### 3.5 Still NOT inertness signals

Coverage, answer content beyond the battery's own expectations, and timing.
Each would make the generic tool product-aware, which is the property the whole
design protects. The battery is not an exception: Foundation defines it **per
concern contract**, so it is generic by construction.

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

## 3B. A NEW versioned admission contract, not a widening of V1

**Point 1 of the gate, and it is a constraint on how this lands rather than on
what it says.**

`ApplicationFoundationProfile.v1` is not widened to carry any of this. The
successor is a **new schema with its own name and version**, and V1 remains
readable exactly as it is.

The reason is one this repository has already paid for twice: a contract that
has crossed an artifact boundary cannot be widened, because a wheel already in
circulation writes documents the new reader refuses under the same schema name —
one name identifying two contracts. `HostLease.v2` alongside a read-only
`HistoricalLeaseV1` is the pattern in-tree, and it is the pattern here.

Two immediate consequences:

* **V1 REJECTS what it cannot bind.** Landed 2026-09-05: `retirement` was in
  `BINDING_FIELDS`, never read by the parser, never emitted by `as_document`, so
  the profile digest did not cover displacement evidence — and a document
  supplying `displaces` with its retirement rows was refused for carrying none,
  a message false about its own input. V1 now refuses at the parse (point 7's
  "until V2 supports this");
* **no number is allocated here.** Naming the successor is part of accepting
  this design, not part of proposing it.

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

### 4.3 `ConcernVerification` — owned by the GENERIC TOOL

| field | owner | notes |
| --- | --- | --- |
| `schema` | Foundation | fixed literal |
| join key (**five** identities) | tool, **copied from what it observed** | not from either declaration |
| `battery_id` / `battery_version` | Foundation | which questions were asked |
| `scenarios` | Foundation defines, tool records | each: `scenario_id`, `expected` (`accept`/`refuse`), `observed` (`accept`/`refuse`/`no_answer`) |
| `observed_at` / `observed_by` | tool | provenance |

**There is no `detail`, no `nonce_echo` and no free text** (point 5). `observed`
is a closed three-value vocabulary and Foundation compares it with `expected`;
the provider never reports its own verdict.

### 4.4 Every document is closed on ENCODING and DECODING

Point 5, and it is a rule about both directions because closing only one is how
a closed set quietly opens.

**Permitted value kinds, exhaustively:** typed codes from a closed vocabulary ·
bounded counts · immutable references · known digests · enumerated identifiers.

**Refused, by construction rather than by policy:** free-text `detail` ·
exception text · captured stderr · arbitrary maps · `default=str` serialisation
· any string not drawn from a declared vocabulary.

An unknown key on decode is `unknown_key`, never ignored.

### 4.5 The scope boundary this closes for free

> **"Host inventory, environment policy and secret pointers are deployment-time
> bindings and do not belong in the build-time composition join."**

These are not build-time facts, so the documents have **nowhere to put them** —
and that is a better answer than adding secret classification to the contract.
With `detail` removed and only typed codes retained, `ProbeOutcome` has no
carrier for a resolved secret, so the leak path into retained evidence closes
**by construction rather than by policing**. Nothing has to remember to redact.

This is ADR 0009's held-never-dereferenced rule reaching a fourth surface: a
value that cannot be represented cannot be leaked.

### 4.6 The profile — owned by FOUNDATION

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

A proven absence is **not** an inertness escape hatch. `broken_shut` is a
provider that does not work and `answers_everything` is one that does not
discriminate; absent-proven is a subject that is not there. A contribution that
fails § 3 may not be re-filed as an absence.

### 6.1 THIRTEEN closed inventories, not one (point 6)

> **"A worker-absence proof cannot certify integration, recovery or identity."**

Today one closed inventory exists — `INTEGRATION_SURFACE_FAMILIES` — and
`IntegrationSurfaceAbsenceProofV1` validates its `families` against it while its
`concern` field accepts **any** of the thirteen. A proof for
`worker_execution` would therefore have to enumerate *integration* families, and
the type is integration-shaped despite its discriminator.

**Each concern gets its own discriminated, closed absence inventory, each with
its own positive control.** The generalisation is of a shape that is already
real and mechanically enforced — the review confirmed the integration separation
holds, with a correct non-vacuity control — so this is extension, not invention.

Three properties carry over unchanged and one is added:

1. the inventory is **closed** and enumerated before the proof, never from its
   own results;
2. **complete enumeration** — a family never visited is distinguishable from one
   visited and found empty;
3. a **positive control**: the instrument shown finding something known to
   exist, with the same scan and scope;
4. **new** — the inventory is selected by the concern, so a proof cannot be
   built against another concern's families. The misfiling refusal landed
   2026-09-05 catches a proof filed under the wrong key; this catches one
   *built* against the wrong inventory, which is the earlier and quieter defect.

---


---

## 5A. `InventoryDigest.v1` — Foundation-owned (point 4)

Today `observed_inventory_digest` is compared against **a string the caller
supplies**, and the caller computes it however it likes. Two implementations
computing "the inventory digest" differently is the permanently-unequal-values
failure the canonical byte rules exist to prevent, one surface over.

So the digest becomes a **Foundation-owned document**:

| field | owner | notes |
| --- | --- | --- |
| `schema` | Foundation | `InventoryDigest.v1` |
| `subject` | Foundation vocabulary | WHAT was inventoried — the artifact identity, typed |
| `entries` | supplier (typed) | closed shape: distribution identity → version |
| `digest` | **Foundation computes** | over canonical bytes of `subject` + `entries` |

> **Platform supplies typed inventory, never a locally computed digest.**

That inverts today's relationship. A supplier that hands over a digest is asking
to be believed; a supplier that hands over the typed inventory is handing over
something Foundation can compute from and therefore check. The digest stops
being a claim and becomes a derivation.

`subject` is required and typed because a digest with no stated subject is a
number, and two parties can agree on a number about different things.

Canonical bytes: the § 8 rules, unchanged and not restated. **A committed golden
fixture** with a re-derivation test and a sensitivity pair — the same treatment
the profile fixture already has, which the review called out as done right.

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

## 6B. Displacement and retirement, canonically (point 7)

The successor contract represents both, and the profile digest **covers** them —
which is the property V1 lacks and why V1 now refuses rather than discards.

| binds | to |
| --- | --- |
| a **displacement** | the specific legacy responsibility displaced, and an evidence reference |
| a **terminal retirement** | back to the replacing assembly AND provider |

Two directions, deliberately. A displacement that named only "something was
retired" is the free-text shape point 5 removes; a terminal retirement that did
not bind back to its replacement leaves a retired responsibility with no
successor of record, which is how a capability comes to be owned by nobody.

**Until the successor exists, V1 REJECTS retirement.** Landed 2026-09-05 —
`_binding_from_document` refuses a document carrying it, with a message true
about that document, and the premise (that `as_document` omits it) is asserted
so the refusal cannot outlive its reason.

### 6C. A defect in this document's own cited premise

The review found that `_binding_from_document` — cited in § 0 as the enforcement
of the closed-field rule — **has no production caller.** Its only callers are
two tests.

By § 1.2's own standard that is *a provider whose only consumer is a test*,
sitting inside this design's cited premise. It is recorded here rather than
repaired here, because the repair is to wire admission into the release path,
which is point 9 and is implementation.

Until then § 0's claim is precise about what it is: the parser **would** refuse a
policy value, and nothing in production currently asks it to.

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
| N6 | nothing at the declared injection site | `unexercised` | an object at the declared site admits |
| N7 | all battery outcomes negative | `broken_shut` | a battery passing both halves admits |
| N7b | **negative scenarios "pass" too** — the five-line stub | `answers_everything` | a provider that refuses what it must admits |
| N7c | object present at a site other than the declared one | `wrong_site` | the declared site admits |
| N8 | two providers for one concern | `duplicate` | one provider admits |
| N9 | concern with nothing at all | `missing` | 13/13 admits |
| N10 | runtime digest ≠ admitted digest | `readback_drift` | equal digests pass |
| N11 | join on a SUBSET of the key | must be unrepresentable | the join takes all FIVE identities |
| N11b | verification joins another assembly | `wrong_assembly` | same assembly artifact joins |
| N11c | caller-supplied inventory digest | `foreign_inventory` | Foundation-computed over typed entries admits |
| N11d | unknown key on **decode** | `unknown_key` | a closed document decodes |
| N11e | a V1 document carrying `retirement` | refusal at the parse | a document without it parses |
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

**N11e and N12–N14 exist today** and are exercised
(`test_deployment_foundation_application_profile.py`). The rest are owed by the
implementation step.

**N7b is the row this revision exists to add.** Its absence is what the
independent review defeated: the previous design's nonce echo made N7b
unrepresentable, so a five-line stub filled a slot. N7 and N7b are duals — a
provider that refuses everything and one that accepts everything are both
non-providers, and each is invisible to the check that catches the other.

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

   The map is committed: `docs/inventories/foundation-admission-parity-map.json`
   (point 8). It is **two-directional** — `legacy_total` and the length of
   `added` are both fixed, so a case cannot appear or vanish as a side effect;
   a shrinking count would otherwise read as cleanup and a growing one as
   progress. Nine added cases: `uninjected`, `wrong_site`, `nonce_only`,
   `all_negative`, `answers_everything`, `wrong_assembly`, `foreign_inventory`,
   `unknown_key`, `retirement_round_trip`.

   **The 53 rows themselves are a COUNT here, not a list, and that is a stated
   gap.** They live in Platform's inventory and are not restated — a copy would
   be a second authority that drifts. The Platform lane owns the row-by-row
   mapping and must supply it before the parity gate can pass.
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

## 12A. Admission on the retained-artifact release path (point 9)

Admission is not a build-time side activity whose result is recomputed later.

* the generic tool runs admission against the **retained artifact** — the exact
  bytes already built and verified, never a rebuild;
* admission emits an **immutable receipt** carrying the five identities, the
  composition digest, the battery version and the scenario outcomes;
* **publication and authorization consume that receipt.** They do not re-derive
  it, re-run the battery, or rebuild anything.

This is build-once (§ 6A) applied to admission itself. A publication step that
recomputed admission would be a second authority over it, and the two would
agree until the day one changed — the same failure the whole redirection removes
at the profile level.

It also closes the § 6C gap by construction rather than by discipline: the
parser acquires a production caller because the release path calls it, so the
enforcement premise stops being a claim about code nothing runs.

**This section is design. The wiring is implementation** and is not in this PR.

---

## 13. What this document deliberately does NOT do

* no build tool (item 7);
* no module declarations (item 6);
* no entry-point group is claimed or reserved;
* no ADR number is allocated — this is a proposal in
  `docs/superpowers/specs/`, which the repository defines as
  **non-authoritative intent**. It becomes authoritative by an accepted ADR, not
  by being merged.
