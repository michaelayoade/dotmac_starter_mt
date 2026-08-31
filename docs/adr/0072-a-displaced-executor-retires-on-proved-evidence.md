# ADR 0072 — A displaced deployment executor retires on proved evidence, never on adoption

**Status:** Accepted — **fleet-wide**. Amended 2026-08-31 (an eighth
entry-point family, `runtime_reactivation`, and `DisplacementWindow.v1`), and
again 2026-08-31 (`SshCredentialConstraintV1`). Each amendment is a dated
addition; no earlier text is rewritten.
**Date:** 2026-08-30
**Decision owner:** Michael
**Extends:** ADR-0018 (a guard exemption states an enforceable premise — this
applies its three mechanisms, entry-point families, two-directional ratchets and
sensitivity proofs, to deployment entrypoints rather than to source call sites);
ADR-0070 (deployment is a stateless versioned foundation — this owns the
`local_copy_retirement` half that ADR left as prose); ADR-0032 (unobserved is
unknown, never absent)
**Related:** Governance ADR 0018 (authority cutovers leave receipts) — its
field 7, `old_writer_retirement`, is where a displaced executor's disposition
goes, and this ADR's receipt is what a `retired` disposition there points at
**Owns:** the entrypoint-family inventory contract, the disposition vocabulary
and its permitted transitions, the two-directional ratchets over both, and the
`ExecutorRetirementReceipt.v1` schema
**Does not own:** any product's deployment topology; when a product retires
anything; the cross-repository receipt registry, which is Governance's and whose
creation is a separate decision

## Context

Four Dotmac repositories carry their own deployment executors. ADR-0070 created
`dotmac-deployment-foundation` to replace them and recorded, in that package's
`EXTRACTION.toml`, exactly which local engines retire and on what terms. That
clause is prose, and prose does not fail.

The consequence is measurable. `dotmac_erp` is a `contract_consumer` of the
facility: it declares `deploy/product.toml`, commits the rendered assets, pins
the published `0.2.0a2` and runs the conformance workflow green. Its
`scripts/deploy.sh` is still the executor, 486 lines of it, still invoked with
`MIGRATION_DATABASE_URL` against a named production host. The dossier says so in
its own words — "a consumer that adopts the facility as declarative input and a
CI gate has proven nothing about its ability to replace an executor."

**The programme scoreboard therefore reads zero retired legacy executors after a
very large amount of work**, and nothing in any repository can tell the
difference between that and progress.

### Two opposite failures, and the rule between them

> Deleting scripts before this would remove rollback capability; leaving them
> active afterward creates two executors.

Both are real. A legacy executor deleted too early takes the rollback path with
it. One left active becomes a second deployment authority nobody is watching —
which is not hypothetical either: the 2026-08-30 ERP preflight found
`dotmac-books.service` installed and disabled on the production host, an
`app-dev` Compose service carrying production credentials, and `sync-static.sh`
rsyncing a checkout into an nginx root that served it ahead of the application.

The decisive rule between the two is:

> **A replacement is not adopted while the displaced executor can still act
> normally.**

### Why a census of `scripts/` is not the census

ADR-0018 established that a guard enumerates entry-point families rather than
directories, and the same is true of an inventory. A legacy executor is a
workflow, a script, a cron entry, a systemd unit, an SSH credential, a webhook,
and a manual runbook — and the three things ERP's preflight found are,
respectively, a systemd unit, a Compose service and a script. Two of the three
are invisible to any repository walk, which is the second thing this ADR has to
say.

## Decision

**A deployment executor's retirement is a state machine over a typed census,
each transition needs evidence a later reader can re-resolve, and the removal
itself writes a receipt that can fail.**

### 1. Every entrypoint is declared, by family, and absence is never a disposition

A product declares one `ExecutorInventory.v1`. Every entrypoint carries a
family, a trigger, a credential **identity** and a disposition. Discovery walks
the tree per family; anything found and not declared is a failure, because an
entrypoint nobody listed is unmonitored, not clean.

Seven families are enumerated by name: `workflow`, `script`, `cron`,
`systemd_unit`, `ssh_credential`, `webhook`, `manual_runbook`. Every family is
declared present or absent — an inventory that omits one is refused.

### 2. An absence in an unwalkable family is a positive claim with an observer and a scope

Four of the seven cannot be enumerated from a tree, and each records why. An
absence in one of those families needs a `[[family_absence]]` record naming who
observed it, when, how, and at what **scope**: `repository_tree` establishes
only that the repository holds nothing and **says nothing about any host**;
`host_observed` names the host that was inspected.

Conflating those two is exactly how a disabled-but-installed unit survives. A
clean walk must not be readable as a clean estate, so the scope is printed in
the coverage block on every run.

### 3. `active_executor` has no path to `retired`

The disposition vocabulary is three disjoint sets — backlog
(`active_executor`, `frozen`, `displaced`), reviewed (`not_an_executor`,
`non_production_executor`, `retained_rollback`), terminal (`retired`) — and
the permitted transitions are data. `active_executor` may become `frozen`, and
nothing else. That single absent edge is this ADR's whole safety argument: it is
the jump that takes the rollback path away with the script.

### 4. A reviewed verdict states a premise the checker can refuse

ADR-0018 §4 keeps "reviewed and correct" distinct from frozen debt. Here the
reviewed verdicts are per-row and their premises are **machine-checked**:
`not_an_executor` is refused over an artifact that commands a deployment verb;
`non_production_executor` is refused over one naming a declared production
target. A verdict that could be asserted by copying a comment is not a review.

Verb resolution is **transitive over in-tree callees**, because a caller
inherits its callee's authority and a dispatch-only workflow reads as inert on
its own bytes.

### 5. Ratchets in both directions, including over adoption itself

Per product, per family, per **disposition** — a total alone hides an
`active_executor` becoming `frozen`. And two over the programme:
`unadopted` (a product that adopts is removed in the same change; a product
cannot leave the expectation by being forgotten) and `retired_total`, which
rises only with a receipt and **may not fall at all**.

Every family carries a planted violation proving its detector fires, with the
detector matched to what that family permits, and at least one proof is in situ
over the real corpus rather than a fixture.

### 6. The removal writes a receipt that can fail

`ExecutorRetirementReceipt.v1`: the subject and the census digest it was
`displaced` in; **two** successful controller-owned cycles, deploy and
redeploy, as distinct runs; every removal by class, including the credential;
the zero-surface guard now covering that family, with its sensitivity proof;
the product's **proved** recovery verdict with its exercise coordinate; what
was deliberately retained; and a digest over the whole. Value-free. A status
vocabulary in which absence is not a status. Corrections by supersession.

One successful deployment proves the replacement can deploy. Only the second
proves it can deploy again, over its own previous state, which is the property
the legacy executor had and the reason it is trusted.

### 7. Products own their receipts; this repository owns the schema

There is no registry here. Governance owns the cross-repository envelope and
the creation of that store is a separate decision.

## Consequences

- The scoreboard becomes checkable. `retired_total` is in a committed file and
  a test asserts it is still zero, so the next change that claims a retirement
  has to move it, with a receipt, in the same commit.
- Adoption stops reading as retirement. ERP is a contract consumer and is
  reported UNADOPTED by this contract, which is the correct and uncomfortable
  answer.
- A retirement becomes more expensive than a deletion, deliberately. The
  credential, the cron entry, the unit and the runbook cost as much attention
  as the script, because they are what a partial retirement leaves behind.
- Four families remain declaration-only. This ADR does not close that gap; it
  makes the gap say its own name on every run rather than presenting a walked
  tree as a surveyed estate.

## Alternatives considered

**Put it in `dotmac-deployment-foundation`.** Rejected on ownership and on
timing. The facility owns the execution of one release on one host and holds no
durable record; a census of a product's repository and host estate, and the
evidence that a *displaced* executor is gone, is a different owner. Separately,
the facility's inputs are frozen behind an in-flight release candidate, and
invalidating that candidate would cost the programme its critical path.

**A new distribution beside it.** Rejected for now. `release-facility.yml` is
deliberately closed to exactly one facility, so a second one cannot be
published without editing a release workflow that is under the same freeze —
and an unpublishable package that three products must run is a contract that
cannot be adopted. The machinery is therefore repo-level, in the same shape as
`credential_lifecycle_sweep.py` and `external_connector_sweep.py`: a sweep, a
typed inventory, a JSON baseline and an architecture test. Promotion to a
distribution is a later, separate decision, and the module is written so that
promotion is a move rather than a rewrite — no imports outside the standard
library, no dependency on the assembly or the kernel.

**Reuse Governance's cutover receipt directly.** Rejected as a conflation. That
receipt records an authority moving between owners; this one records a
displaced writer being removed. Its field 7 is the seam between them, and
keeping two records lets the retirement carry the removal-specific evidence —
the two controller cycles, the removal classes, the proved recovery — that a
cutover receipt has no field for.

**Score an unmeasured product zero.** Rejected: it reports debt as retired,
which is the exact failure this ADR exists to prevent, arriving through the
measurement instead of through the code.


## Decision amendment — 2026-08-31 (an executor with no invoker, and the negative half)

ERP's census, taken against the merged v1 contract, produced two findings the
contract could not express. Both are ADDITIONS. No clause above is withdrawn.

### A. The eighth family is `runtime_reactivation`, not `restart_policy`

The naming is the ruling, not a preference. Docker's `restart: unless-stopped`
normally restarts **the same container**, which is not a deployment at all — so
a family named after the policy would describe the wrong thing and sweep in
every benign case, and a guard that fires on benign cases is a guard people
learn to override.

The property that makes one of these an executor is narrower and sharper:

> **It can reactivate a displaced executor after reboot.**

That reframes what a retirement owes. A retirement used to owe *the artifact is
gone*. It now owes *the executor cannot autonomously return* — and those are
different claims. A script deleted from a tree whose unit is still enabled, or
whose image is still named by a `restart: always` service, comes back at the
next reboot with nobody having invoked anything. That is not a retirement; it
is a pause with a receipt.

Concretely: the family's artifacts are the supervisor configurations a tree can
hold (Compose files, unit files, `@reboot` entries); its enablement lives on a
host, so `tree_complete` is false and its absence needs an observer. A row
carries `reactivates`, naming the declared executors it can return, and that
list is checked in **both** directions — a row claiming to reactivate nothing
is refused the moment another row names it, because a one-way check is
satisfied by declining to fill in your own field. A `not_an_executor` verdict is
refused over an artifact carrying a live reactivation directive; the weaker
`reactivates_no_declared_executor` verdict is refused over one carrying none,
so it cannot become the place everything lands.

The receipt gains `no_autonomous_return`: a typed method (`observed_reboot`,
`supervisor_catalog`, `mechanism_absent`), an observer, a host, and the
mechanisms considered with their disposition after the retirement. Every
inventory row naming the subject in its `reactivates` must be accounted for, or
the receipt is refused. That cross-check — not the transition table — is what
orders a mechanism's retirement against its subject's.

### B. `DisplacementWindow.v1` — attribution, not absence

`controller_receipts` prove two POSITIVE cycles. Nothing proved the negative,
and the negative is the harder half. A window in which the legacy executor was
not invoked reads as displacement, but two readings collapse into it:

1. the executor is idle and the controller owns the runtime; or
2. **the runtime changed and neither executor did it.**

(2) is not a legacy invocation, so a zero-invocation guard passes while the
controller's claim to own the runtime is false. There is a third executor and
the measurement cannot see it.

**A quiet window is not evidence; it is the claim under test.** `poll`,
`periodic_snapshot` and `quiet_window` are therefore named and REFUSED as event
sources rather than merely absent from the accepted list, because someone will
try each of them and deserves to be told why. The accepted sources —
`event_stream`, `audit_log`, `daemon_event_api` — are the ones that observe
every transition.

The window does not assert that nothing happened. It **enumerates everything
that happened and attributes each one**: to a controller receipt, or to a typed
non-deployment cause. Zero unattributed changes. A change nobody can account for
IS the finding.

Four properties make that more than a form to fill in:

- **Bounded**, with exact start and end runtime identities.
- **Chained.** Consecutive changes must meet, and the declared endpoints must
  match the ends. A source that missed a transition leaves a BREAK, so
  completeness is *tested* rather than declared — which is what "a complete,
  named event source" has to mean if it is to mean anything.
- **Same-image is proven, not accepted.** A `non_deployment_cause` whose
  runtime identity moved is refused. This is where Docker's benign case lives:
  `restart: unless-stopped` returning the same container is
  `same_container_restart`, and the schema checks the sameness instead of
  believing the label.
- **`cannot_establish` forces `unmonitored`.** Not a pass with a caveat. This
  ADR's own guard gets no exemption from ADR-0018's rule: an unmonitored region
  is honestly unmonitored, never quietly exempt. An `unmonitored` verdict cannot
  be committed.

The two cited controller cycles must appear as attributed changes inside the
window. A cycle the window did not see is a cycle the window cannot vouch for.

### C. Calls, not mentions — a specificity repair the eighth family exposed

On its first run the verb detector read a **usage comment** in
`docker-compose.dev.yml` (`docker compose -f … up`) as a deployment, and the
edge resolver drew a call edge backwards from `docker-compose.yml` to
`scripts/deploy.sh` because that path appears in a comment there — so the
topology inherited the verbs of the executor that deploys it.

Whole-line comments are now stripped before both verb detection and edge
resolution. Conservative by construction: only lines whose first non-blank
character is `#`, so an inline trailing comment keeps its command.
Over-stripping would produce false negatives, which is the direction that hides
an executor. This is the rule `credential_lifecycle_sweep.py` states as "calls,
not mentions", applied where no AST exists.

### D. What this amendment does not do

It performs no retirement. `retired_total` stays **0**. It defines how a
retirement is proven; it does not prove one. It does not block issuer
activation or the first receipt, and it must land before any executor is
declared retired.

## Decision amendment — 2026-08-31 (SshCredentialConstraintV1)

The v2 amendment closed two gaps and named a third it could not close: the
contract could **count** an SSH key and could not **characterise** it.
`Entrypoint` had no field for a key's restriction state, so "eight root keys on
the production host, none carrying `from=`, `command=` or `restrict`" was a
sentence in a census rather than a fact a guard could hold.

### The typed constraint

Every `ssh_credential`-family row now carries an `SshCredentialConstraintV1`,
and a row without one is refused at parse. It records the fingerprint and
principal, the exact source restriction, the forced-command **digest**,
`restrict`, and the PTY, agent, port and X11 permissions — each explicitly
`denied` or `permitted` — together with the host-observed evidence coordinate
that makes it re-checkable.

Three properties of the encoding are load-bearing:

- **A digest, never the command string.** A digest makes a *changed* forced
  command detectable. A string invites a near-match being waved through:
  `/usr/bin/dotmac-deploy` becoming `/usr/bin/dotmac-deploy --shell` reads the
  same at a glance and is a different key.
- **An unrestricted key must SAY it is unrestricted.** `source_restriction =
  "unrestricted"` and `forced_command_digest = "none"` are literal values, not
  omissions. Absence is never a disposition, and a blank field reads as "nobody
  looked" — which is precisely the state the eight keys were already in.
- **`restrict = present` beside any `permitted` is refused.** OpenSSH's
  `restrict` denies all current and future permissions, so such a key cannot
  exist. A row asserting both was written rather than observed, and that is
  worth refusing on its own.

The constraint is part of the census digest, so **loosening a key moves it**.
Without that, a key could be quietly unrestricted between two receipts and
every recorded digest would still match.

### The retained rollback key, and why this belongs here

This is the substantive rule, and it belongs in the retirement contract rather
than in a hardening document for a reason internal to the model:

> **This model requires the legacy executor's bytes be retained rather than
> deleted.** That is what `retained_rollback` is for — and it means a rollback
> credential survives every retirement.

A retained key that can open an interactive shell is not a rollback path. It is
the executor still being reachable by hand: the second failure mode this ADR
exists to prevent, arriving through the door the retirement itself held open.

A key held under `retained_rollback` must therefore be **source-restricted**,
**forced-command-only**, and **incapable of an interactive shell** — all three
proven rather than asserted, and each checked **independently**. A single
"is this key safe" verdict fires only when everything is wrong and passes the
realistic failure, which is one protection quietly dropped. So the removal of
`restrict`, of `from=` and of `command=` are three findings, planted and
observed separately, alongside a properly constrained key that is **admitted**
at the same reach.

`principal` is recorded and does **not** relax the gate. A non-root deploy user
holding an unrestricted key is still the executor reachable by hand.

The receipt participates: a `retained_rollback` entry whose identity resolves to
an inventory row is checked against that row's constraint, so the retention a
receipt creates cannot be looser than the contract allows.

### Deliberately narrow

The gate bites on `retained_rollback` only. ERP's eight unrestricted keys are
`active_executor` debt that the ratchet counts and the constraint characterises;
refusing them at parse would make an honest census impossible on day one. **The
census records reality; the gate guards the retention this contract itself
creates.**

`retired_total` stays **0**. This amendment defines how a retained credential is
proven safe; it retires, disables and revokes nothing.

### A hole the constraint exposed: the census digest was never compared

`SshCredentialConstraintV1` is part of the canonical census form so that
loosening a key moves the digest. Building that revealed that **nothing
compared digests** — the ratchet was over counts only, so any change to a row's
CONTENT that left the row count identical passed silently: a target repointed,
a trigger changed, a credential renamed, a key unrestricted. The ratchet now
compares the recorded census digest against the live one and fails in either
direction, so a content change must be re-recorded in the same change. This is
wider than SSH and was found by asking what the new field's digest membership
actually bought.

### Two directions folded into v3 — 2026-08-31

**The roster names Vendor CP.** `vendor-cp-prod` is a named production host
retaining a rollback credential at Wave 7C, and it sat outside
`ADOPTION_TARGETS` — so it would have been **silently unmonitored rather than
reported UNADOPTED**, which is the roster reproducing the exact failure the code
was written to prevent. The entry is the REPOSITORY that owes the inventory
(`dotmac_vendor_control_plane`), not the host that retains the credential:
`measure()` resolves `<product>.toml`, so a host identity in the roster would
name a file nobody could ever write and would report UNADOPTED forever for the
wrong reason. `vendor-cp-prod` belongs in that product's own inventory, as the
`host` on the credential's row and as a `production_targets` entry.

**Compose sanction is ENTRY-POINT IDENTITY, not a disposition.** A compose verb
is sanctioned **iff it is reached through the installed
`dotmac-deployment-foundation` entry point**, resolved from installed
distribution metadata — never from a path, a comment, a filename or a declared
premise. Anything else that reaches a compose verb is unsanctioned, whatever its
intent.

The reason to prefer identity is a defect this module already produced: the verb
detector read a *usage comment* as a deployment and the edge resolver drew a
call edge backwards, because a path mention is symmetric while invocation is
not. *"Is this the Foundation-sanctioned compose call?"* has the same shape — it
asks about intent, which a tree cannot answer. *"Was this reached through that
entry point?"* is a fact about topology.

The implementation is almost embarrassingly simple once stated that way: a
sanctioned mutation happens inside the installed distribution, which is **not in
the tree**, so it never appears in a resolved verb set; an unsanctioned one is in
the tree, so it always does. Delegation does not launder a direct call beside
it. Three properties keep it honest:

- **The console-script name is never written down.** It is read from metadata,
  and a test asserts the literal does not appear in the module — a hardcoded
  name would turn an identity check back into a string match. That test had to
  strip the distribution name first, because the script name is a PREFIX of it;
  the near-match hazard this ADR warns about arrived inside its own test.
- **An unresolvable distribution is UNMONITORED, never a pass.** If the
  distribution is not installed where the check runs, the sanction question
  cannot be answered and the coverage block says so.
- **Whether it is installed is not recorded in the baseline.** That is a
  property of where the check runs, not of the product; freezing one machine's
  answer into a committed file is how a local environment becomes a fleet fact.

Wave 7C's *"remove direct Compose mutation outside Foundation"* is therefore
expressed as driving the unsanctioned SET to empty, ratcheted two-directionally
— the set rather than the count, because a swap (one path retired while another
gains the ability) leaves the count still and is exactly the move that matters.
It is a counted measurement rather than a refusal for the same reason the SSH
gate is narrow: the Starter's own `scripts/deploy.sh` is unsanctioned today, and
refusing it would make an honest census impossible on day one.

This pairs with Wave 4's installed `dotmac-platform` CLI ratchet. Once Platform
CP is installed as a wheel with real entry points, the same
`sanctioned_entry_points()` shape applies there, and the vocabulary should be
coordinated before either ratchet hardens.

