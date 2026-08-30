# Foundation `0.3.0a2` — the frozen candidate

**Status:** built once, unpublished, untagged. Frozen 2026-08-30.

Built from merged protected `main` at `e930f878` by `foundation-candidate.yml`,
the lane that is structurally incapable of publishing. These exact bytes carry
through the recovery proof, the issuer bootstrap and Lane 3. Publication reuses
them and **must not rebuild**.

Machine-readable record:
[`foundation-candidate-0.3.0a2.json`](./foundation-candidate-0.3.0a2.json).

## The six coordinates

| fact | value |
|---|---|
| source SHA | `e930f878ce400b766b4a50feb0369021a28ab2fa` |
| workflow run ID | [`33339810583`](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/33339810583) |
| artifact ID | `9740182233` |
| filename | `dotmac_deployment_foundation-0.3.0a2-py3-none-any.whl` |
| size | 263869 bytes (wheel); 497826 bytes (artifact archive) |
| SHA-256 | `2a6e0ccd040b05ab602be4b439e48dd61188b3b71ed6e80ecc8a482e70d57443` |

`expires_at` is **`2026-11-28T22:41:11Z`, as the artifacts API returned it** —
not the 90 days the workflow requested. **89 days** remained at recording,
against a floor of 30.

## What is frozen is FIVE components, not the wheel alone

Any change to any of them invalidates the candidate and requires a new build
**even if the wheel is byte-identical**.

| # | component | value |
|---|---|---|
| 1 | source | `e930f878ce400b766b4a50feb0369021a28ab2fa` |
| 2 | version | `0.3.0a2` |
| 3 | canonical descriptor digest | `sha256:f481cfa2…` |
| 4 | rendered assets | 3 files, sha256 each — see the JSON |
| 5 | `DeploymentIdentity.v1` labels | 8 `io.dotmac.deployment.*` keys |

Recording all five is what makes an invalidation **checkable against a named
component** rather than judged. A re-render that moves one asset, a descriptor
edit that shifts the configuration digest, or a change to the identity label
set each invalidate the candidate on their own.

Until Lane 3 passes all sixteen requirements and a2 is published, nothing may
change a Foundation input — package source, workflows, fixtures, or bound
evidence. An improvement noticed after this point is recorded for the next
version, not applied to the frozen one.

## The simultaneous-property evidence

`push: main` run
[`33339110835`](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/33339110835)
on exactly `e930f878`: **20 executed successes, 1 skipped.**

The skip is `allocation-gate`, which is `pull_request`/`workflow_dispatch` only
by design — it compares a branch against its merge base, which is undefined on
a push. Its property was proven where it is meaningful: it executed and passed
on each of the six PR heads, since none could merge without all twenty-one
required contexts reporting `SUCCESS`.

This run, not the six accumulated PR runs, is what establishes that all eight
property clusters hold **on one tree**. Five green branches are five claims
about five trees.

## The invalidation rule

> If the artifact expires or becomes unavailable, invalidate every dependent
> receipt and restart bootstrap with a new candidate digest. **Rebuilding and
> claiming continuity is forbidden.**

Re-deriving bytes that happen to match is a claim, not a proof: the downstream
receipts name *this* digest, and a rebuild produces a different artifact ID and
expiry even when the wheel is bit-identical.

```
python scripts/foundation_candidate.py check  --receipt docs/inventories/foundation-candidate-0.3.0a2.json
python scripts/foundation_candidate.py verify --receipt docs/inventories/foundation-candidate-0.3.0a2.json --wheel <path>
```

## Dependents — invalidate together

| dependent | status |
|---|---|
| recovery / restore proof | not yet run |
| issuer bootstrap | **blocked** — no issuer exists |
| Lane 3 exposure rehearsal | **blocked** — needs a Platform CP authorization run ID, and `dotmac-control-runner` is unregistered |
| publication via `release-facility.yml` | not reached; must reuse these bytes |

## `0.3.0a1` is not a predecessor of this candidate

Built once before any of the eight clusters landed, purely as a bootstrap
input. Unpublished, untagged and unadoptable by ruling, authorized for exactly
two supervised uses — an isolated recovery proof and the Platform CP bootstrap
— and unable to close Lane 3, because it predates the execution seam it would
be certifying. Its receipt
([`foundation-candidate-0.3.0a1.json`](./foundation-candidate-0.3.0a1.json))
stays on record for those two uses and for nothing else.

## Scoreboard

Publishing a2 would be the programme's **first** receipt. It is not the second:
no product pins a2, `scripts/deploy.sh` is still the executor in every
environment, and **no local product deployment engine has been retired**. The
second metric remains at zero.
