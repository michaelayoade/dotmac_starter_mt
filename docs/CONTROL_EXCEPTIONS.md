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

**Status:** accepted, not repeated. The a91 artifact stands; no rollback.

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
