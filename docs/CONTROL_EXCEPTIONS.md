# Control exceptions

A control that was bypassed, with what was bypassed, why, what it cost, and
what changed so it cannot recur. Recorded rather than argued away: ADR-0018's
rule is that an exemption states an enforceable premise, or the region is
unmonitored rather than exempt — and a bypass nobody wrote down is the second
kind wearing the first kind's clothes.

An entry is never deleted. The remediation column is what changes.

---

## 2026-08-22 — agent approved a protected-environment deployment

**Control:** `registry-release` environment approval on `release-kernel.yml`.
Its purpose is to put a human between an authenticated credential and an
irreversible publication to the private index.

**What happened.** During an interactive session, Claude Code dispatched the
kernel `0.1.0a91` release, reported that the run was waiting at the gate, and
was told in chat: *"approved, release it"*. It then called
`POST /actions/runs/{run_id}/pending_deployments` with `state=approved`, using
the workstation's `gh` credential — which is authenticated as `michaelayoade`,
the environment's only reviewer. It did this twice: once on run
`32596599849` (which then failed its own SHA check, publishing nothing) and
once on run `32597951034`, for both the publish and verify gates.

**Why it is an exception even though the release was authorized.** The
artifact is legitimate and the outcome was correct. The *control* was not
satisfied:

- **The approval record names a person who did not perform the action.** That
  attribution is the single fact the record exists to establish, and it is now
  wrong for three approvals. No audit of the environment can distinguish them
  from approvals Michael clicked.
- **Chat authorization is not gate authorization.** The gate's premise is that
  a reviewer approves *at the gate*, against *the tree the gate is showing*.
  Authorization given in conversation is given earlier, against a different
  tree, and cannot be re-checked at the moment of publication. Run
  `32596599849` is the proof: `main` moved between the authorization and the
  gate. The publication was correctly refused — by the SHA check, not by the
  approval, which had already been granted against the stale tree.
- **A credential that CAN approve makes the control advisory.** Discipline
  ("an agent stops at the gate") is the weaker half of a control whose
  stronger half is a permission the agent does not hold.

**Cost.** No incorrect artifact. `dotmac-kernel-v0.1.0a91` peels to
`6c6a38b0`, was installed back from the private index and registered before
tagging, and its ledger row was removed in #361. The cost is entirely to the
evidentiary value of the approval record, and to the precedent.

**Status:** accepted; repeated by kernel a93 on 2026-08-23. The a91 artifact
stands; no rollback.

**Remediation.**

| # | Action | Owner | State |
|---|---|---|---|
| 1 | `AGENTS.md` rule 31 — approval is non-delegable; an agent stops at the gate and hands over the URL | this change | done |
| 2 | Remove the ability to approve deployments from the agent-accessible GitHub credential | Michael | **open** |
| 3 | Release freeze formalised as rule 32, so the SHA check stops costing a wasted build | this change | done |

Item 2 is the one that matters, and it is not something the agent can do for
itself — that is the point. Until it lands, rule 31 is discipline. The change
is to the token/app the workstation `gh` CLI uses: it needs to keep
`contents`, `pull_requests` and `actions:read`, and lose whatever grants
`actions:write` on `pending_deployments`. A fine-grained PAT or a GitHub App
installation scoped without deployment-approval rights satisfies this; a
classic PAT with `repo` does not, because `repo` carries it.

**Verification when item 2 lands.** With the agent credential, on a run
waiting at a protected environment:

```
gh api repos/<owner>/<repo>/actions/runs/<run_id>/pending_deployments \
  --jq '.[].current_user_can_approve'
```

must report `false`. That check is the acceptance test; today it reports
`true`.

## 2026-08-23 — the release-record automation failed silently on its first run

**Control:** every publication writes its record in the same change
(`AGENTS.md` § Process; the five gates in `test_declared_publication.py` and
`test_released_migrations.py`).

**What happened.** The kernel `0.1.0a92` release
([run 32617583628](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32617583628))
was the first end-to-end use of `scripts/open_release_record_pr.sh` (#357). It
ran, removed the ledger row correctly, pushed
`chore/record-dotmac-kernel-0.1.0a92` — and could not open the pull request:

```
pull request create failed: GraphQL: GitHub Actions is not permitted to
create or approve pull requests (createPullRequest)
```

It then **exited 0**. The step reported success, the job was green, the run was
green. The record reached `main` only because a human noticed and hand-wrote a
replacement.

**Why it happened.** Two independent defects, and both had to be fixed:

1. **The wrapper treated "could not open the record" as a warning.** That was a
   deliberate choice, argued at the time on the grounds that the artifact is
   already published so the run should not report a successful publication as
   failed. It was wrong: it put correctness back on somebody reading a warning
   inside a green run, which is the failure class the script exists to end.
   Michael identified this before the instance occurred; the instance is the
   proof. Fixed: `give_up` exits 1.
2. **The workflow used the repository `GITHUB_TOKEN`, whose broad repository
   switch couples pull-request creation to pull-request approval.** *Settings →
   Actions → General → Workflow permissions → "Allow GitHub Actions to
   create and approve pull requests".* This is not a workflow token scope. It
   remains disabled deliberately: enabling approval authority merely to obtain
   creation authority weakens a separate review control. The tagging jobs also
   no longer request `pull-requests: write` for that ordinary token, so a future
   switch change cannot silently make the publisher the recorder. Automatic
   opening needs a dedicated recorder identity instead.

**Cost.** No incorrect artifact. `main` was red for roughly two hours between
the a92 tag and the hand-written record, and every open branch inherited the
five failures during that window — each presenting as that branch being broken.

**Status:** silent success remediated; automatic opening remains open.

| # | Action | Owner | State |
|---|---|---|---|
| 1 | `give_up` fails the run, links the ready-made pull-request page | this change | done |
| 2 | Guard pinning that no third success path can appear | this change | done |
| 3 | Remove pull-request authority from every tag-writing job's ordinary workflow token and enforce exact `contents: write` | this change | done |
| 4 | Install a dedicated recorder GitHub App with metadata read, contents write and pull-requests write, but no Actions, deployment, environment or administration authority | Michael | **open** |
| 5 | Prove one low-risk release automatically opens its record through that App and keep the freeze until the green record merges and protected `main` is verified | Michael + release captain | **open** |

The repaired loud path has now run for real. Kernel a93 run `32622991682`
pushed `chore/record-dotmac-kernel-0.1.0a93`, failed RED when PR creation was
refused, and printed the ready-made URL. Michael opened #372 from that branch;
all eighteen checks were green and merge `04b97713` restored the truthful
record. That proves failure is visible and recoverable without re-publishing.

The chosen end state is not the broad repository switch. GitHub's
pull-request-write permission also reaches the review API, so the App is not
described as intrinsically review-incapable. Instead, it authors and last-pushes
its own mechanical PR, protected `main` requires a fresh approval from another
actor with no bypass, and the App has no Actions, deployment, environment or
administration authority. The publisher token has contents write only. Until
the App is installed, it can push the correct branch but cannot open the PR;
the accepted fail-closed bridge is one manual click and a red run rather than
silent green.

---

## 2026-08-23 — agent approved the a93 gates, with rule 31 already in force

**Control:** `AGENTS.md` rule 31 — protected-environment approval is
non-delegable; an agent dispatches, stops at the gate, and hands over the URL.

**What happened.** Rule 31 merged to `main` in #363 at 2026-08-23. Within the
same session, the agent approved BOTH `registry-release` gates for the kernel
`0.1.0a93` release
([run 32622991682](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/32622991682)),
again using the workstation credential authenticated as `michaelayoade`.

**Why it is recorded separately from 2026-08-22.** The first instance was
undertaken without the rule existing. This one was not:

- the agent raised the conflict BEFORE acting — *"rule 31 says I dispatch and
  stop at the gate… confirm that's what you want, since it's a change from
  yesterday"*;
- Michael reaffirmed with the concern on the table — *"release, dispatch and
  approve"*;
- so this is a **knowing deviation**, authorised in the moment, not an
  oversight. That is a materially different thing from the first instance and
  is recorded as such rather than folded into it.

The distinction matters because the remediation is unchanged by it. An
authorised deviation still leaves the approval record naming a person who did
not perform the action, and still leaves the control resting on discipline
rather than on a permission boundary.

**Cost.** No incorrect artifact. `dotmac-kernel-v0.1.0a93` peels to `8537a9bc`,
matching the release run's head; it was installed back from the private index
and registered before tagging; the ledger row was removed in #372 and
`declared-publication sweep` reports PASS.

**Status:** accepted, authorised, and the second instance of the same
unremediated gap.

**Remediation.** Item 2 of the 2026-08-22 entry — removing deployment-approval
permission from the agent-accessible credential — remains **open**, and is now
the only thing that would have changed this outcome. Two authorised instances
in two days is the evidence that "the agent stops at the gate" cannot be
carried by discipline alone: the agent stopped, asked, was told to proceed, and
proceeded. A permission boundary does not have that conversation.

Until it lands, every such approval gets an entry here.

**Current accepting actions.** The historical exception remains accepted; its
technical remediation is explicitly assigned rather than implied:

| # | Action | Owner | State |
|---|---|---|---|
| 1 | Replace the agent-visible credential with an automation identity that is not an environment reviewer | Michael | **open** |
| 2 | Prove `current_user_can_approve == false` on a pending release before that identity may dispatch | Michael + release captain | **open** |
| 3 | After automation dispatch is separated, set `prevent_self_review=true` and disable administrator bypass on `registry-release` | Michael | **open** |

The accepting condition is capability, not another promise: a human approval
credential is never installed in an agent-visible session; an App or machine
identity used for dispatch and monitoring is not an environment reviewer and
reports `current_user_can_approve=false` at the pending gate.
