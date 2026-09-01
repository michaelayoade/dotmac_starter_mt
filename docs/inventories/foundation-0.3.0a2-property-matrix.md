# Foundation `0.3.0a2` — the eight property clusters, measured on one revision

> **INVALIDATED 2026-09-01 — do not build, publish, tag or reuse `0.3.0a2`.**
> Commit `0f390a9aa93b0bb1cb78621ab1e9febc90bc48d2` (#551) changed the facility
> source under this same declared version, so the name came to cover two
> contracts. The successor identity is `0.3.0a3`, which has never been built.
> The judgement is recorded as `CandidateDisposition.v1` in
> [`foundation-candidate-dispositions.json`](./foundation-candidate-dispositions.json);
> the `CandidateArtifact.v1` receipt beside it is preserved byte-for-byte and
> is NOT edited by that record. This document stays as the historical account
> of what those bytes are.

Every cluster below was found MISSING by the audit of closed PR #507
(retained head `d8eebbb38`) against main `83b8a850`, and restored **fresh**
against current main. Nothing was revived, cherry-picked or reconstructed from
that branch: it predates #514/#515, so porting forward risked regressing the
newer implementation of a shared property.

## Why this document exists

The eight clusters merged as five separate pull requests, each green on its own
head. Five accumulated PR runs are **not** the same claim as "these eight
properties hold simultaneously". The item-2 evidence is therefore the
`push: main` CI run on the single revision recorded at the bottom of this file
— one tree, all eight, twenty-one required contexts.

## The clusters

| # | property | owner module | tests |
|---|---|---|---|
| 1 | Authorized-only host execution | `authorization.py` | `test_deployment_foundation_execution_seam.py` |
| 2 | Launcher root of trust | `launcher.py` | `test_deployment_foundation_launcher.py` |
| 3 | Signed evidence, fork-head + protected-ref admission | `evidence.py` | `test_deployment_foundation_release_evidence.py` |
| 4 | Pinned host tool identity | `toolchain.py` | `test_deployment_foundation_toolchain.py` |
| 5 | Forward-only release ordering | `ancestry.py` | `test_deployment_foundation_ancestry.py` |
| 6 | Typed single-use downgrade override | `ancestry.py` | `test_deployment_foundation_ancestry.py` |
| 7 | Create-only publication | `release-facility.yml` | `test_release_is_create_only.py` |
| 8 | Runtime `DeploymentIdentity.v1` | `render/compose.py` | `test_deployment_foundation_compose.py`, `test_canonical_document_boundary_flag.py` |

## Against the shared-unit definition

One owner · typed inputs · deterministic digest-bearing output · no product,
provider or host branches · explicit refusal semantics · positive and
planted-negative tests · a durable receipt · a named first adopter · a
retirement gate for the displaced local implementation.

| # | owner | typed in | digest out | no branches | refusals | pos + planted | receipt | first adopter | retirement gate |
|---|---|---|---|---|---|---|---|---|---|
| 1 | yes | yes | via descriptor digest | yes | yes | yes | grant in provenance | **none yet** | **none yet** |
| 2 | yes | yes | launcher digest | yes | yes | yes | `LauncherIdentity` | **none yet** | **none yet** |
| 3 | yes | yes | canonical bytes | yes | yes | yes | `ReleaseEvidenceV1` | **none yet** | **none yet** |
| 4 | yes | yes | optional binary digest | yes | yes | yes | resolved path | **none yet** | **none yet** |
| 5 | yes | yes | n/a — a verdict | yes | yes | yes | ordering + method | **none yet** | **none yet** |
| 6 | yes | yes | n/a — a verdict | yes | yes | yes | reason + decision_ref | **none yet** | **none yet** |
| 7 | workflow | n/a | published artifact | yes | yes | yes | tag + release record | **none yet** | **none yet** |
| 8 | yes | yes | configuration digest | yes | yes | yes | labels on the object | **none yet** | **none yet** |

### The two honest gaps, stated rather than implied

**Named first adopter: none.** No product pins `0.3.0a2`; it is not published.
ERP consumes `0.2.0a2`. Until a product pins a2 and runs through it, every row
above is a property of code that nothing executes in anger.

**Retirement gate: none, for any cluster.** This is the item most easily left
implicit, and leaving it implicit is how a shared unit ends up running *beside*
the thing it was meant to replace rather than instead of it. Concretely:
`scripts/deploy.sh` is still the executor for every environment, and no local
product deployment engine has been retired or even scheduled for retirement.

Neither gap is a defect in the clusters. Both are unmet preconditions for
calling the work adopted, and they are why publishing a2 is the FIRST receipt
of the programme and not the second.

## Notes carried forward from the work

**A rule that stops matching fails silently.** #526 changed `docker_bin` from a
bare name to an absolute path, which left the compose-host scripted runner
keyed on `"docker"`. Two rules failed loudly with "no rule matched argv"; any
predicate that merely stops matching would have failed silently while the fake
kept reporting green. The rules now read argv[0] from `DEFAULT_TOOLS` so they
cannot drift from the code under test.

**A `render --check` mismatch that could not be reproduced — now explained.**
It was recorded as an unexplained anomaly rather than dropped, and the cause
turned out to be the measurement, not the renderer: `poetry -C <dir> run`
changes the working directory, so `-f deploy/product.toml` and
`-o deploy/rendered` were resolving against the main repository rather than the
worktree under test. The renderer is deterministic; three consecutive
byte-comparisons agree. The lesson is the one the anomaly was kept for: a green
check measured against the wrong tree is indistinguishable from a green check.

**The version bump is a rendered-bytes change.** `VERSION` lives inside the
canonical descriptor document, so a1 → a2 moved the configuration digest from
`sha256:81af03d8…` to `sha256:f481cfa2…` and every `DeploymentIdentity.v1`
label with it. The assets were re-rendered, never hand-edited. This is also why
the bump had to land on the near side of the freeze.

## `0.3.0a1` is not a predecessor of this release

It was built once, before any of these clusters existed, purely as a bootstrap
input. It is unpublished, untagged and unadoptable by ruling, authorized for
exactly two supervised uses — an isolated recovery proof and the Platform CP
bootstrap — and it cannot close Lane 3, because it predates the execution seam
it would be certifying.

## The frozen revision

**`e930f878ce400b766b4a50feb0369021a28ab2fa`**, version `0.3.0a2`.

Item-2 evidence: `push: main` run `33339110835` on that exact SHA — 20 executed
successes, 1 skipped (`allocation-gate`, which is pull-request-only by design
because it compares against a merge base undefined on a push; it executed and
passed on each of the six PR heads).

The five frozen identity components and the six artifact coordinates are in
[`foundation-candidate-0.3.0a2.json`](./foundation-candidate-0.3.0a2.json), with
the reasoning in
[`foundation-candidate-0.3.0a2.md`](./foundation-candidate-0.3.0a2.md).
