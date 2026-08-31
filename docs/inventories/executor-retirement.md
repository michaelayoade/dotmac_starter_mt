# Executor retirement: the entrypoint-family contract, the ratchets, the receipt

**Dated:** 2026-08-30, amended 2026-08-31. **Status:** machinery only. **Nothing is retired, frozen,
disabled or revoked by this document or by the code it describes.**

- Measurement: `scripts/executor_retirement.py`
- Per-product census: `docs/inventories/executor-retirement/<product>.toml`
- Frozen counts: `docs/inventories/executor-retirement-baseline.json`
- Gates: `tests/architecture/test_executor_retirement_ratchet.py`,
  `tests/unit/test_executor_retirement_receipt.py`
- Decision: `docs/adr/0072-a-displaced-executor-retires-on-proved-evidence.md`

## Why this exists

The programme scoreboard reads **zero retired legacy executors**. Every
release, guard and composed unit so far has added nothing to it. A replacement
adopted as declarative input and as a CI gate has proven nothing about its
ability to replace an executor — ERP is a contract consumer of
`dotmac-deployment-foundation` today and its `scripts/deploy.sh` is retired no
sooner for it.

Only the removal counts. And the removal is bracketed by two opposite failures:

> Deleting scripts before this would remove rollback capability; leaving them
> active afterward creates two executors.

The sequence exists to pass between them, and the decisive rule is that **a
replacement is not adopted while the displaced executor can still act
normally.** That rule is why `frozen` is a state with its own evidence and why
`active_executor` has no path to `retired`.

## 1. The entrypoint-family contract

A legacy executor is not one script. Eight families are enumerated by name —
never by directory, because a guard scoped to one directory is a guard with a
known hole (ADR-0018 §1):

| family | in-tree roots | can a tree enumerate it? |
| --- | --- | --- |
| `workflow` | `.github/workflows`, `.forgejo/workflows`, `.gitlab-ci` | yes |
| `script` | `scripts`, `bin`, `deploy`, `ops`, `tools`, repository root | yes |
| `cron` | `deploy/cron`, `cron.d`, `etc/cron.d` | **no** |
| `systemd_unit` | `deploy/systemd`, `systemd`, `etc/systemd` | **no** |
| `ssh_credential` | — | **no** |
| `webhook` | — | **no** |
| `manual_runbook` | `docs/runbooks`, `docs/operations`, `runbooks` | **no** |
| `runtime_reactivation` | repository root, `deploy`, `compose`, `docker` | **no** |

Five of the eight cannot be walked, and each records **why** in its
`incompleteness_premise`. That is not a formality. This programme has already
found, on a production host, `dotmac-books.service` installed and disabled, an
`app-dev` Compose service carrying production credentials, and `sync-static.sh`
rsyncing a checkout into an nginx root that served it ahead of the application.
None of the first two is discoverable from any repository.

### Absence is never a disposition

Every entrypoint carries `family`, `trigger`, `credential` and `disposition`.
Discovery walks the tree; **anything found and not declared FAILS.** An
entrypoint nobody listed is unmonitored, not clean.

The same rule applies one level up. A family is declared `present` or `absent`
by name — an inventory omitting a family is refused — and an absence in a
family a tree cannot walk needs a `[[family_absence]]` record naming who
observed it, when, how, and **at what scope**:

- `repository_tree` — the tree was walked and holds no member. **Says nothing
  about any host.**
- `host_observed` — a named host was inspected by a named person at a named
  time.

The Starter's five absences are all `repository_tree`, and the coverage block
prints that bound on every run. A product with a deployed host — ERP — owes
`host_observed` records, and their absence is precisely what let a disabled but
installed unit sit unnoticed.

### A product with no inventory is UNADOPTED, never zero

`ADOPTION_TARGETS` names `dotmac_erp`, `dotmac_sub` and `dotmac_starter_mt`.
A product without an inventory is reported as UNADOPTED on every run and is
carried in the baseline's `unadopted` list, which is itself ratcheted. Scoring
an unmeasured product zero would report the debt as retired.

### The disposition vocabulary — three disjoint sets

**Backlog** (debt; the ratchet counts it):

| disposition | means |
| --- | --- |
| `active_executor` | can act on a declared production target right now |
| `frozen` | retained and still capable, declared no longer invoked; the rollback path is deliberately intact |
| `displaced` | both controller receipts exist; removal is authorized but is a separate change |

**Reviewed** (a per-row verdict, each with a **machine-checked** premise —
`grandfathered` appears nowhere, and a test asserts that):

| disposition | the premise, and how it is checked |
| --- | --- |
| `not_an_executor` | commands no deployment verb, directly **or through any in-tree callee**. Refused over a file that deploys. |
| `non_production_executor` | acts only on an ephemeral or local target. Refused over a file naming a declared `production_targets` entry. |
| `retained_rollback` | deliberately kept as a retirement's recovery path. Requires `rollback_for`, so the retention has an owner and an end. |

**Terminal:** `retired` — removed, and requires the identity of the receipt
that proves how.

Permitted transitions are data, and the one that matters is the one that is
absent: `active_executor` may become `frozen`, and nothing else. It has no path
to `displaced` or `retired`.

### A caller inherits its callee's verbs

`deployment-adopter.yml` runs no container; it dispatches
`deployment-conformance.yml`, which does. Judged on its own bytes it reads as
inert. Verb resolution is therefore transitive over in-tree references, with a
visited set. This is hard rule 37's unchecked-caller hole seen from inside a
guard rather than across a wire.

`git checkout` was in the verb list on the first draft and was removed: in a CI
runner it is how every job starts, so it made every release workflow inherit a
"host source mutation" it never performs. A verb that fires on ordinary CI
teaches reviewers to override the finding. `git pull` and `git reset --hard`
stay, because those are how ERP's host-side synchronization mutates a running
release.

### What the check found on its first real run

`registry.dotmac.io` was originally in the Starter's `production_targets`. The
premise check immediately refused `non_production_executor` for
`deployment-conformance.yml`, which installs the published facility from that
index. The refusal was correct in mechanism and wrong in declaration: the list
means "a place a deployment LANDS", and a package index is an artifact store.
The declaration was narrowed. Recorded here because it is the in-situ evidence
that the premise check bites on the real corpus, before anything was planted.

## 2. The ratchets

Two-directional per **product, family and disposition**, plus two on the
programme scoreboard itself.

| ratchet | rises when | falls when |
| --- | --- | --- |
| family/disposition counts | a new entrypoint or a new caller lands | a row disappears without the baseline being lowered in the same change |
| `unadopted` | a product enters the expectation unrecorded | a product adopts and the baseline is not lowered with it |
| `retired_total` | a retirement lands without its receipt and baseline change | **ever** — the scoreboard does not go down; a `retired` row that vanished is a record being deleted |

Per **disposition**, not per family total: an `active_executor` becoming
`frozen` leaves the total still and changes everything about what may happen
next.

### Sensitivity — one planted violation per family, observed firing

The tree is near-clean, so every assertion would otherwise pass over an empty
set. Each family gets its own planted case, and the detector differs by family
because what is detectable differs:

| family | planted violation | detector |
| --- | --- | --- |
| `workflow` | an undeclared `.yml` under `.github/workflows/` | discovery + reconcile |
| `script` | an undeclared `.sh` under `scripts/` | discovery + reconcile |
| `cron` | an undeclared file under `deploy/cron.d/` | discovery + reconcile |
| `systemd_unit` | an undeclared `.service` under `deploy/systemd/` | discovery + reconcile |
| `manual_runbook` | an undeclared `.md` under `docs/runbooks/` | discovery + reconcile |
| `ssh_credential` | declared absent with no observer record | parser refusal |
| `webhook` | declared absent with no observer record | parser refusal |

The last two are the honest ones. No walk reaches a deploy key or a third
party's webhook registration, so the only thing a guard can hold there is
whether somebody claimed the absence and said how. Pretending a walk covers
them would be an unmonitored region wearing a guard's costume.

One proof is **in situ** rather than fixture-only: real bytes written into the
real `scripts/` tree inside `try/finally`, the real checked-in census, the real
discovery, then restore and a Git-clean assertion.

## 3. The retirement receipt

`ExecutorRetirementReceipt.v1`. Value-free — identity names and digests only.
A status vocabulary in which **absence is not a status**: `proposed`,
`committed`, `superseded`. A committed receipt is immutable; corrections are by
supersession, never by edit.

**Relationship to Governance ADR 0018.** That ADR's seven-field
authority-cutover receipt is the model and remains the wider record. Its field
7, `old_writer_retirement`, is where a displaced executor's disposition goes;
**this** receipt is what a `retired` disposition there points at. One is the
cutover's evidence, this is the removal's. Its `retired` / `transferred` /
`still_live` vocabulary maps onto the dispositions above as: `retired` →
`retired`; `still_live` → `active_executor`, `frozen`, `displaced` or
`retained_rollback` (each of which names its own retirement condition);
`transferred` has no member here, because an executor is removed, not handed on.

**There is no registry in this repository.** Receipts are product-side
artifacts. Governance owns the cross-repository envelope, and the creation of
that store is a separate decision.

| # | Field | What it names | Not this |
| --- | --- | --- | --- |
| 1 | `subject` | The retired entrypoint's identity, its family, and the digest of the inventory census it was `displaced` in. | "the deploy script". A name with no census behind it. |
| 2 | `controller_receipts` | **Two** successful controller-owned cycles — `deploy` and `redeploy` — each with a distinct run id, a 40-character head commit, an observer and a time. | "we deployed twice". One run cited twice. A pull-request preview run. A successful *conformance* job. |
| 3 | `removals` | Every artifact removed, by class — `script`, `workflow`, `cron_or_unit`, `credential`, `permission`, `documentation`, `configuration_flag` — each with an identity and the commit that removed it. | "cleaned up". A pull-request number. A credential left live because deleting the script felt like the end of the job. |
| 4 | `zero_surface_guard` | The guard now covering that family: the check, and the test id of its sensitivity proof. | "CI is green". A guard named for a property it does not test. A guard over a different family. |
| 5 | `recovery_verdict` | `recovered` or `not_recovered`, with what it was restored from, the exercise's run id, and who observed it when. | "rollback is documented". "We can roll back if needed." An expectation. |
| 6 | `retained_rollback` | What was deliberately NOT removed, and why it is still needed. | Silence. |
| 7 | `digest` | `sha256:` over the receipt's own canonical content, excluding itself. | A timestamp. A version label. |

### What the schema refuses

- an unknown schema, or any unknown key — a receipt whose reader and writer
  disagree about the vocabulary proves nothing, silently;
- a missing or unknown `status` — absence is not a status;
- fewer than two controller receipts, two that name one run, a failed cycle,
  two `deploy`s with no `redeploy`, or a head commit that is not a commit —
  one successful deployment proves the replacement can deploy; only the second
  proves it can deploy **again, over its own previous state**, which is the
  property the legacy executor had;
- a subject whose inventory row is `active_executor` or `frozen` — a
  replacement is not adopted while the displaced executor can still act
  normally;
- a subject whose inventory digest no longer matches the census it names;
- a subject holding a credential when the receipt removes no credential — a
  live credential is a second executor waiting for whoever holds it;
- an empty `removals` list — a status change is not a retirement;
- a value-shaped string in any identity — a credential is named, never held
  (ADR-0009);
- a zero-surface guard with no sensitivity proof, or one over another family;
- a recovery verdict that is a word rather than a measurement, and any
  `committed` receipt whose recovery is `not_recovered`;
- a `committed` receipt whose digest does not match its content — an immutable
  record that can be edited is a mutable record with a stern comment;
- a `superseded` receipt that does not name its replacement.

## 4. Adoption

`dotmac_starter_mt` adopts here, because a contract its own repository does not
satisfy is a contract whose first real exercise happens in somebody else's pull
request. Its census is 25 entrypoints across two present families, five
families declared absent with tree-scoped observations, and **one
`active_executor`**: `scripts/deploy.sh`.

**`dotmac_erp` is the named first adopter** and is UNADOPTED today. The shape
this contract was built against is ERP's: `scripts/deploy.sh` invoked with
`MIGRATION_DATABASE_URL`, its direct GitHub SSH/deployment workflow, its
host-side source synchronization (`sync-static.sh`), and the credentials those
hold — plus the secondary executors that must retire in the same movement or
the retirement is nominal: `/etc/cron.d/dotmac_erp_db_backup`,
`dotmac-books.service`, the `app-dev` service, and the checkout bind mounts.
`docker-compose.yml` is `retained_rollback`, not retired.

`dotmac_sub` follows, with `deploy_production.sh` and the rest of the engine
its `local_copy_retirement` clause names. Sub adopts last: it is the only one
of the four whose engine is currently protecting a production deployment.

Nothing in this repository executes any of that. The products execute their own
retirements when their two controller cycles are proven, and this is the
machinery that says whether they were.


## 5. Amendment — 2026-08-31

Two additions from ERP's census, ratified by Michael. Full reasoning in
ADR-0072's dated amendment; the operational summary is here.

### The eighth family: `runtime_reactivation`

**Not** `restart_policy`, and the name is the ruling. `restart: unless-stopped`
normally restarts *the same container*, which is not a deployment — a family
named after the policy would describe the wrong thing and sweep in every benign
case. The executor property is narrower: **it can reactivate a displaced
executor after reboot.**

So the retirement receipt now owes `no_autonomous_return` — proof the executor
**cannot come back** — rather than only proof the artifact is gone. A script
deleted from a tree whose unit is still enabled returns at the next reboot with
nobody having invoked anything.

Two new dispositions, each checked:

| disposition | premise, and how it is checked |
| --- | --- |
| `reactivation_capable` (backlog) | can return a declared executor; `reactivates` must be non-empty and every name must resolve |
| `reactivates_no_declared_executor` (reviewed) | carries a live directive but returns nothing declared; refused if its own `reactivates` fills in **or** another row names it |

`not_an_executor` in a reactivation-bearing family is refused over an artifact
carrying a directive. `restart: "no"` correctly does not fire — that is the
right shape for a one-shot migration service, and a detector firing on it would
refuse the most carefully written file in the tree.

The Starter declares five rows here, all `reactivates` nothing: its one
executor is operator-invoked and runs in no container.

### `DisplacementWindow.v1` — attribution, not absence

A quiet window is **not evidence; it is the claim under test**. `poll`,
`periodic_snapshot` and `quiet_window` are named and REFUSED as event sources,
so the refusal can say why. The window enumerates every runtime change and
attributes each to a controller receipt or a typed non-deployment cause. **Zero
unattributed changes.**

| property | what it catches |
| --- | --- |
| bounded, with exact start/end runtime identities | an open or inverted interval |
| chained — changes must meet, ends must match | a transition the source never saw; completeness is TESTED, not declared |
| a non-deployment cause must be same-image | a restart that actually changed what is running |
| `cannot_establish` forces verdict `unmonitored` | a pass with a caveat; an `unmonitored` verdict cannot be committed |
| both cited cycles must appear inside the window | a cycle the window cannot vouch for |

### Sensitivity: both halves, same reach

The fleet rule now requires both. `test_the_planted_third_executor_change_is_refused`
strips one attribution from the conforming fixture and the validator refuses;
`test_a_properly_attributed_window_is_not_refused` runs the same fixture
unmodified through the same code path and it is admissible. A refusal test alone
proves the validator can say no; only the pair proves it says no to the right
thing.

### Calls, not mentions

The eighth family exposed a specificity defect on its first run: a usage
comment reading `docker compose -f … up` was read as a deployment, and a path
named in a comment drew a call edge backwards, so a Compose file inherited the
verbs of the script that deploys it. Whole-line comments are now stripped
before both verb detection and edge resolution — conservatively, so an inline
trailing comment keeps its command, because over-stripping produces false
negatives and those hide executors.

`retired_total` stays **0**. This amendment defines how a retirement is proven;
it performs none.
