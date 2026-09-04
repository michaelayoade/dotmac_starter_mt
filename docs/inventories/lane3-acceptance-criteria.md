# Lane 3 — acceptance criteria for the four runner fixes

- Date: 2026-09-04
- Measured at: `dotmac_starter_mt` `main` = `5b056f0a85`
- Author: Foundation lane. **Not the owner of the exposure runner** — this
  document states what the fixes must satisfy; whoever takes Lane 3 writes them.
- Status: criteria, not a plan. Nothing here prescribes an implementation.

## Why this document exists

### Where Lane 3 actually stands, stated precisely

**Today the lane produces no receipt at all.** The far-end source-address
sentinel makes `qualify_vantage` refuse at `_qualify(evidence)`
(`exposure_rehearsal_runner.py:287`), which is before the first
`results.record` at line 303. So the publication gate refuses on a missing
receipt, not on a tally.

**With the three placeholder defects fixed, the ceiling is 15 of 16**, because
`provoked_rollback` records `executed_failed` on an otherwise perfect run —
nothing provokes a rollback, so `transaction.rolled_back` stays `None`. That is
the sense in which item 8 is the one that makes sixteen structurally possible at
all.

The distinction matters to a fix-writer: three of these defects change a
*status*, one prevents a *receipt*, and one puts a false pointer inside a
receipt that passes.

Whoever fixes them will otherwise write **toward a number** — make the tally say
sixteen — rather than **against a contract**. Those come apart, and the shape
they come apart in is specific: a rehearsal that reaches 16/16 in a way the
receipt refuses, or worse, one the receipt accepts while the underlying property
was never observed.

So every criterion below is written as *what the receipt must be able to show*,
because **the gate reads a receipt, not a runner**. A criterion the receipt
cannot express is a criterion the gate cannot enforce.

Each item also names **what would satisfy it vacuously**. That is the half a
fix-writer working from a checklist skips, and it is the half that has cost this
programme the most.

## The gate, measured rather than recalled

Publication runs two oracles, and they fail differently
(`.github/workflows/release-facility.yml`, `scripts/require_rehearsal.py`):

1. **The Actions API** must show the *newest* `exposure-rehearsal.yml` run whose
   `head_sha` is byte-identical to the SHA under release, completed with
   conclusion `success`. Newest-then-check, never check-then-newest — an old
   green run must not mask a newer failure (`require_rehearsal.decide`).
2. **The published `RehearsalReceipt.v1`** must say what that run established
   (`rehearsal.verify_publication`).

`verify_publication` makes exactly three refusals, in this order: wrong lane;
wrong revision; **any item not `executed_passed`**. The status vocabulary is
closed — `executed_passed`, `executed_failed`, `not_executed`, `hand_measured`,
`blocked`, `vacuous` — and only the first satisfies publication
(`RequirementStatus.satisfies_publication`).

Three structural properties matter to a fix-writer:

- **An omitted item is not an implicit pass.** `build_receipt` refuses a receipt
  missing any of the sixteen: *"silence is exactly how the previous count went
  wrong."*
- **An item cannot appear twice.** *"Two rows for one item is how a failure
  hides behind a pass."*
- **A bare status is not evidence.** `RequirementResult` refuses an empty
  `detail`: *"the detail is what a reader checks the status against."* The
  `evidence` tuple is free-form pointers to the bytes behind the claim.

That last one is why several criteria below are about the **evidence field**
rather than the status. A status a reader cannot check is a number.

---

## 1. `provoked_rollback` (item 8) — the one that makes 16/16 possible at all

### What is true today

`scripts/exposure_rehearsal_runner.py` records:

```python
results.record(
    "provoked_rollback",
    PASSED if transaction.rolled_back is not None and not lost else FAILED,
    ...
)
```

`transaction.rolled_back` is `None` unless a rollback actually occurred. Nothing
in the run provokes one, so on a fully successful rehearsal this item is
`executed_failed` and the lane caps at 15/16. **This is not a reporting gap; the
item has never been executed.**

### What `executed` means for this item

Three things must all be true, and the receipt must show each:

1. **A condition was deliberately induced** that the apply path could not
   satisfy. Not an error injected into the *recording*, and not a rollback
   invoked directly — the rollback must be the system's own response to a
   failure it met.
2. **The rollback ran and was observed to complete** — `rolled_back` is not
   `None`.
3. **Restoration was compared against the pre-change snapshot and found exact.**
   The existing comparison is the right one: `foreign_before - foreign_after`
   must be empty, i.e. no rule belonging to another owner was lost. The receipt
   already carries this in `detail` as `foreign rules lost: …`.

### The trap: a provocation that cannot fail

**If the induced condition is one the system would have handled anyway, the item
passes without proving anything.** This is the single most likely way this fix
goes wrong, because the easiest provocations are the ones the code already
tolerates.

A conforming provocation must be one where, *with the rollback path removed*,
the run would leave the host changed. If the answer to "what would have happened
without the rollback?" is "nothing", the provocation proved nothing.

### Checkable against the receipt

Item 8's `detail` and `evidence` must together let a reader answer, without the
runner in hand:

- **what was induced** — named specifically, not "a failure was injected";
- **at which step** the apply path met it;
- **that the rollback was the response**, distinguishable from a run that
  completed and then tidied up;
- **the comparison result** against the pre-change snapshot, as it does now.

### Vacuous satisfactions to refuse

- `rolled_back` set by anything other than a real rollback — a test hook, a
  direct call, a flag.
- A provocation applied **after** the verification the rollback is supposed to
  respond to, so the rollback is a cleanup rather than a response.
- A provocation the system tolerates, making the rollback a no-op that still
  sets `rolled_back`.
- `foreign_before` empty because no foreign rules existed. **Then
  `not lost` is trivially true and the comparison ranges over nothing.** The
  receipt must show the snapshot was non-empty, or say plainly that the
  preservation half was vacuous — in which case the item is not
  `executed_passed`.

The last one deserves emphasis: the current `detail` renders `foreign rules
lost: none`, which reads identically whether five rules were preserved or zero
rules existed.

---

## 2. The three placeholder observations

`scripts/exposure-rehearsal/collect_probe_evidence.sh` emits three values it
cannot measure. Its own header is honest about two of them and calls them
"fail-closed placeholders". They are **not one problem**: the first is a
precondition, the other two are item outcomes.

### 2a. Far-end source addresses — a hard precondition, different in kind

```
"observed_source_v4": "__TARGET_OBSERVED_V4__",
"observed_source_v6": "__TARGET_OBSERVED_V6__"
```

`vantage.qualify_vantage` refuses when the far-end value is empty **or when it
disagrees with the address the vantage claims**. A sentinel is non-empty, so it
refuses through the *mismatch* branch. Either way `_qualify(evidence)` raises
before any item is recorded.

**So this does not make an item fail. It means no receipt exists at all.** The
run dies at qualification, `build_receipt` is never reached, and the publication
gate refuses on "no receipt" rather than on a count.

The comment in `vantage.py` states why it cannot be dropped: it is *"the one
check measured from the far end… what replaces the discrimination control lost
when the second NIC was removed."* A vantage cannot self-certify where it
egresses from; only the target can say what address it saw.

**What the runner must DO:** obtain, from the *target*, the source address it
observed for a connection from this vantage, in both families, and put those
observed values into the evidence. The readiness lane already established these
are operationally collectable.

**Checkable against the receipt:** indirectly but decisively — a receipt exists
at all, and items 13–16 are recorded. A run that reaches `build_receipt` has
passed qualification by construction.

**Vacuous satisfactions to refuse:**

- Substituting the vantage's *own* reported address for the far-end
  observation. It would compare equal and prove nothing — this is the check's
  whole point, and it is the easiest way to "fix" it.
- Deriving the far-end value from anything the vantage controls.
- Relaxing `qualify_vantage` to tolerate the sentinel.

### 2b. `privileged_vantage_refused` (item 12)

Emitted `null`; the runner requires `is True`, so the item is
`executed_failed`.

**What the runner must DO:** run a real probe from a vantage **inside** an
accepted source set and have `accept_public_exposure_evidence` refuse it. The
item is about the refusal firing on a real probe, not about the refusal existing.

**Checkable against the receipt:** item 12 `executed_passed`, with `evidence`
naming the vantage that was inside the accepted set and the refusal that fired.

**Vacuous satisfactions to refuse:**

- Setting the field `true` from a vantage that was never inside an accepted
  source set — the refusal then fired for the ordinary reason, not the one the
  item is about.
- A refusal produced by a synthetic evidence object rather than a real probe.
- A refusal that fired because the probe failed for an unrelated reason
  (unreachable host, timeout) and was recorded as the privileged-vantage
  refusal.

### 2c. `private_inside` (item 16)

Hardcoded `reachable: false` because the collecting host is outside the declared
source set by construction. Item 16 wants `reachable == True`, so it is
`executed_failed`.

**What the runner must DO:** take the measurement from a vantage that *is*
inside the declared source set, rather than from the current probe host.

**Checkable against the receipt:** item 16 `executed_passed`, and its `evidence`
must identify the vantage well enough for a reader to confirm it was inside the
source set. `probe:private_inside` alone does not do that.

**Vacuous satisfactions to refuse:**

- Flipping the literal to `true` without moving the measurement.
- Widening the declared source set to include the existing probe host. That
  makes the item pass by changing the subject, and it weakens the very exposure
  the lane exists to prove.
- Reaching the port from the target itself — inside the source set is not the
  same as on the host.

---

## 3. `service_running` — hardcoded `true`, and measured at the wrong moment

All four probe entries carry `"service_running": true` as a literal. The runner
consumes it (`running = bool(probe.get("service_running", True))`) and refuses
the item when false.

The rule it exists to enforce is stated in the collector's own header: **"NEVER
PROBE A NEGATIVE AFTER TEARDOWN. A refusal against a port where nothing is
listening measures an absent service, not an enforced exposure."**

The defect is not only that the value is a literal. **Probe evidence is
collected in the workflow step *before* the controller runs**
(`exposure-rehearsal.yml`: "Collect and qualify the external probe evidence"
precedes "Execute Lane 3 through the controller"). So at the moment the literal
asserts the service is running, the stack has not been applied. The claim is
about the wrong moment, and a literal cannot be about any moment at all.

### The two fixes, and what each costs

**Mid-run collection** — move the negative probes to after the apply. Cost: the
workflow's current ordering exists for a reason worth preserving. Evidence
collected *before* the controller runs cannot be contaminated by it, and the
header notes a probe measured after teardown must never be reusable as a
negative. Moving collection means the ordering guarantee has to be re-stated:
after apply, before teardown, and demonstrably so.

**A measured value** — keep the ordering and have the collector *observe*
whether the service is listening at probe time, rather than assert it. Cost:
this alone does not fix the moment problem. A true observation taken before the
stack is applied is an accurate measurement of the wrong instant, and item 13
explicitly requires the negative to be measured against a **RUNNING** service.

**Read together, the second is necessary and the first is what makes it
sufficient.** A measured `service_running` collected before apply will honestly
report `false` and fail the item — which is correct behaviour and still leaves
Lane 3 short of sixteen. The fix has to move the negative probes to a point
where the service is genuinely up, and measure rather than assert there.

### Checkable against the receipt

Items 13–15 already render `service running={running}` in `detail`. For those
items to be admissible the receipt must let a reader establish **when** the
observation was taken relative to the apply — the current `detail` cannot, and
`probe:{key}` does not either.

### Vacuous satisfactions to refuse

- Measuring `service_running` against a *different* service or port than the one
  the negative probes.
- Observing it once and reusing the value for probes taken at other times.
- Defaulting to `True` on an unreadable observation. The runner's
  `probe.get("service_running", True)` **already does this** — an evidence file
  that omits the key entirely is read as a running service. A fix should make
  absence fail rather than pass.

---

## 4. The compose-project claim — a correctness defect, and it is inside the receipt

### What is true today

```python
project = f"{lease.compose_project_prefix}{args.authorization_run}"
if not lease.owns_project(project):   # pragma: no cover - derived from prefix
    raise ...
```

`owns_project(p)` is `p.startswith(self.compose_project_prefix)`, and `project`
is built by concatenating that prefix. **The check is `True` by construction and
can never fire.** The `pragma` says so.

The consequence is larger than a dead check. The derived `project` is **never
passed to the effects**. `ComposeHostExposureEffects.apply_compose` invokes
`docker compose --project-name self._spec.product …` — for this descriptor,
`lane3_exposure`. So Docker labels every object
`com.docker.compose.project=lane3_exposure`, not `<prefix><run>`.

The comment above the check states the opposite:

> The Compose project is derived from the authorization run, so every object
> Docker creates is labelled `com.docker.compose.project=<prefix><run>` and the
> post-rehearsal deletion set is scoped by construction rather than by anyone
> remembering which objects were theirs.

**That is false as written**, and the property it claims — a deletion set scoped
by construction — does not hold.

### Does it reach `executed_passed` for any item?

**Yes, and that is the part to fix first.** The derived `project` appears in
exactly one place beyond the dead check: as the `evidence` pointer on item 1.

```python
results.record(
    "apply_under_lock",
    PASSED if report.ok else FAILED,
    f"applied and verified under the {spec.product} deployment lock",
    f"project={project}",
)
```

So a passing item 1 carries an evidence pointer naming a Compose project that
does not exist. It does not change item 1's *status* — that is `report.ok` — but
it puts a **false pointer inside the receipt**, and the receipt is the artifact
the gate reads and a reader checks a status against. `RequirementResult` refuses
an empty `detail` on exactly that reasoning; an evidence pointer that is
checkable and wrong is worse than an empty one.

### Criteria

- Either the run must actually use the derived project name, or the receipt must
  stop claiming it does. **Not both left as they are.**
- If the derived name is adopted, the deletion-set claim becomes true and should
  be provable: the receipt should let a reader confirm which project the objects
  carried.
- If it is not adopted, the comment and the evidence string must be corrected to
  name `spec.product`, and the dead `owns_project` check should go or be given a
  premise it can actually fail on.

### Vacuous satisfactions to refuse

- Keeping the check and making it "meaningful" by passing a literal that still
  starts with the prefix.
- Correcting the evidence string while leaving the false comment.
- Adopting the derived project name without confirming teardown scopes to it —
  which would convert a documentation defect into a host-cleanup defect.

---

## Summary

| # | Item | Today | Nature of the fix |
|---|---|---|---|
| 8 | `provoked_rollback` | `executed_failed` — never provoked | Induce a condition the apply path genuinely cannot satisfy |
| — | far-end source addresses | **no receipt at all** — qualification refuses | Collect the observation from the target |
| 12 | `privileged_vantage_refused` | `executed_failed` — emitted `null` | Real probe from inside an accepted source set |
| 16 | `private_from_source` | `executed_failed` — hardcoded `false` | Measure from a vantage inside the source set |
| 13–15 | `service_running` | asserted `true`, before apply | Measure, at a moment when the service is up |
| 1 | compose project | passes with a **false evidence pointer** | Use the name, or stop claiming it |

**Sixteen `executed_passed` is the requirement. Reaching it by any route that
would not survive a reader checking the `detail` and `evidence` against the host
is not reaching it** — it is the "green preflight reads as attested" failure that
`require_rehearsal.py`'s own docstring was written against.
