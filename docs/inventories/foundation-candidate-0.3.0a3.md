# Foundation `0.3.0a3` — the frozen candidate

**Status:** built once, **unpublished, untagged**. Frozen 2026-09-02.

Built from merged protected `main` at `005490b2` by `foundation-candidate.yml`,
the lane that is structurally incapable of publishing. These exact bytes carry
through the Platform CP issuer cutover and Lane 3. Publication reuses them and
**must not rebuild**.

Machine-readable record:
[`foundation-candidate-0.3.0a3.json`](./foundation-candidate-0.3.0a3.json).

## Why this candidate exists at all

`0.3.0a2` is consumed — `CandidateDisposition.v1` records it `invalidated`,
`publishable: false`. `0.3.0a3` is its successor identity, and until this build
it had never been built: no wheel, no tag, no index entry.

It is also the **first Foundation artifact anywhere that can produce an
`ExecutionPlanDigestV1`**. `execution_plan.py` arrived in `f9503d97` (#583),
which is after both the published `0.2.0a2` and the frozen `0.3.0a2` candidate
— verified with `git ls-tree` against `dotmac-deployment-foundation-v0.2.0a2`
and against `e930f878`, neither of which contains the module. Platform CP
submits that digest and Deployment Control freezes and signs it, so before
these bytes existed neither repository could produce the value at all.

## The six coordinates

| fact | value |
|---|---|
| source SHA | `005490b278be73112fa9600bffb6e00a37c77a59` |
| workflow run ID | [`33587629491`](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/33587629491) |
| artifact ID | `9830633429` |
| filename | `dotmac_deployment_foundation-0.3.0a3-py3-none-any.whl` |
| size | 313714 bytes (wheel); 591959 bytes (artifact archive) |
| SHA-256 | `11978d919f1e910ae16d9b8262ffd3c473b074b4815067ab210fbe88e009d990` |

Companion sdist in the same artifact:
`dotmac_deployment_foundation-0.3.0a3.tar.gz`, sha256
`b64c680595f8ff86c55ec7637cbb8eedcb123b9a13e648c61dd3fc390972103a`.

`expires_at` is **`2026-12-01T03:35:45Z`, as the artifacts API returned it** —
exactly the 90 days the workflow requested, so retention was **not** silently
capped. That date is the programme's clock: the Platform CP cutover, Lane 3
reaching 16/16, and the `release-facility.yml` change that lets publication
consume these bytes must all complete inside it.

The coordinates were confirmed to RESOLVE, not merely to have been recorded:
fetching artifact `9830633429` by ID returns 591959 bytes whose wheel member
hashes to the SHA-256 above. That is the path a consumer uses — by run and
artifact ID, digest-verified, installed into an isolated deployment-tool
environment with no index fallback, no source-tree fallback and no rebuild.

## What the execution-plan smoke did and did not establish

From the run log, step `Release wheel CLI smoke`:

```
dotmac-deploy: execution-plan contract OK
  (sha256:6eb1d34d4e045d55fec362c894fd8acb643ce8d712d31d3f8e75574a2bf07172)
```

**That digest authorizes nothing.** It is a throwaway plan over this
repository's own reference descriptor and a fictitious target
(`release-smoke-host`), and it is recorded in the JSON under
`execution_plan_smoke_observation.digest_of_throwaway_smoke_plan` precisely so
it cannot be lifted out and mistaken for something Control should freeze. A
real plan digest is bound to a real target and a real descriptor; this is bound
to neither.

What it establishes is that the **installed** wheel produced it:
`execution-plan --format json` and `--format digest` both exited zero through
the console script, `execution_plan_digest` and
`FoundationExecutionPlanV1.digest()` each independently re-derived the value,
and `FoundationExecutionPlanV1` round-tripped the document the CLI printed.

Nothing in that check tests for a symbol named `ExecutionPlanDigestV1`.
The name denotes a **value**, not an importable object, so an attribute lookup
— or a comparison of the module's schema constant against the string literal —
would be checking the spelling, and a wheel can carry the right spelling and
produce nothing. The digest is checked by being produced and re-derived.

`Inspect artifacts` reported `content policy OK (50 entries)`.

## What is frozen is FIVE components, not the wheel alone

Any change to any of them invalidates the candidate and requires a new build
**even if the wheel is byte-identical**.

| # | component | value |
|---|---|---|
| 1 | source | `005490b278be73112fa9600bffb6e00a37c77a59` |
| 2 | version | `0.3.0a3` |
| 3 | canonical descriptor digest | `sha256:eff30b30…` |
| 4 | rendered assets | 3 files, sha256 each — see the JSON |
| 5 | `DeploymentIdentity.v1` labels | 8 `io.dotmac.deployment.*` keys |

Components 3-5 are read from the **committed** artifacts at this source SHA
(`deploy/rendered/`), not recomputed by running the package. `VERSION` sits
inside the canonical descriptor document, which is why the configuration digest
differs from `0.3.0a2`'s.

Until Lane 3 passes all sixteen requirements and a3 is published, nothing may
change a Foundation input — package source, workflows, fixtures, or bound
evidence. An improvement noticed after this point is recorded for the next
version, not applied to the frozen one.

## The simultaneous-property evidence

`push: main` run
[`33587497698`](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/33587497698)
on exactly `005490b2`: **20 executed successes, 1 skipped.**

The skip is `allocation-gate`, `pull_request`/`workflow_dispatch` only by
design — it compares a branch against its merge base, which is undefined on a
push. Its property was proven where it is meaningful: it executed and passed on
the PR head (#586, 22 of 22).

## The invalidation rule

> If the artifact expires or becomes unavailable, invalidate every dependent
> receipt and restart bootstrap with a new candidate digest. **Rebuilding and
> claiming continuity is forbidden.**

Re-deriving bytes that happen to match is a claim, not a proof: the downstream
receipts name *this* digest, and a rebuild produces a different artifact ID and
expiry even when the wheel is bit-identical.

```
python scripts/foundation_candidate.py check  --receipt docs/inventories/foundation-candidate-0.3.0a3.json
python scripts/foundation_candidate.py verify --receipt docs/inventories/foundation-candidate-0.3.0a3.json --wheel <path>
```

## Authorized consumers — two, and no others

| consumer | status |
|---|---|
| Platform CP first issuer cutover | authorized; not yet run |
| Lane 3 exposure rehearsal | authorized; **blocked** — needs the authorization run ID the cutover produces, and `LANE3_PROBE_HOST` is unset |
| publication via `release-facility.yml` | not reached; **the lane cannot consume these bytes yet** — see below |
| ERP, Sub, ordinary product deployment | **not authorized** |

The control runner is no longer a blocker: `control-runner-starter-mt` is
registered, online and idle with the `dotmac-control-runner` label. The comment
in `exposure-rehearsal.yml` claiming otherwise is stale.

## Publication cannot consume this candidate yet

As written today, `release-facility.yml`'s `build` job runs `poetry build` and
its `publish` job downloads that same job's upload. It never fetches artifact
`9830633429` and never compares a SHA-256 against this receipt. Step 5 of the
bootstrap — publish *these* bytes, never rebuild — therefore requires a change
to that lane, which is scheduled and must land inside the `2026-12-01` window.

## Scoreboard

Publishing a3 would be the programme's **first** receipt. It is not the second:
no product pins a3, `scripts/deploy.sh` is still the executor in every
environment, and **no local product deployment engine has been retired**. The
second metric remains at zero.
