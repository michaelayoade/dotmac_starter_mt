# Foundation `0.3.0a1` bootstrap candidate

**Status:** built, unpublished, untagged. Recorded 2026-08-30.

The `dotmac-deployment-foundation` `0.3.0a1` bootstrap candidate was built
**once**, from merged protected `main`, by `foundation-candidate.yml`. These
exact bytes carry through the recovery proof, the issuer bootstrap and Lane 3;
publication reuses them and **must not rebuild**.

The machine-readable record is
[`foundation-candidate-0.3.0a1.json`](./foundation-candidate-0.3.0a1.json)
(`CandidateArtifact.v1`). This page exists so the dependency is readable; the
JSON is what the tooling reads.

## The six coordinates

| fact | value |
|---|---|
| source SHA | `83b8a850012de240e22f3ab57cafe3f3403a40ff` |
| workflow run ID | [`33326824657`](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/33326824657) |
| artifact ID | `9736470881` |
| filename | `dotmac_deployment_foundation-0.3.0a1-py3-none-any.whl` |
| size | 235504 bytes (wheel); 445239 bytes (artifact archive) |
| SHA-256 | `6dc373192462b2f9eba66b0a064a60e63b61cfea2cfe96cd8651ac08cc7c4ba5` |

All six, because they answer different questions. A digest alone does not let
anyone re-fetch the bytes; a run ID alone does not prove which bytes came out;
an artifact ID alone does not survive the artifact being replaced. Together
they let a later reader both **locate** and **identify** the same wheel.

## Expiry, and why the margin is checked rather than assumed

`expires_at` is `2026-11-28T18:00:35Z` — **as the artifacts API returned it**,
not the 90 days the workflow requested. Those are different facts and only the
first is binding. 89 days remained at recording, against a floor of 30
(`MINIMUM_REMAINING_DAYS`), because bootstrap spans a restore proof, an issuer
stand-up and a full Lane 3 rehearsal.

## The invalidation rule — mechanical, not remembered

> If the artifact expires or becomes unavailable, **invalidate every dependent
> receipt** and restart bootstrap with a new candidate digest. **Rebuilding and
> claiming continuity is forbidden.**

Re-deriving bytes that happen to match is a claim, not a proof: the downstream
receipts name *this* digest, and a rebuild from the same source produces a
different artifact ID and expiry even when the wheel is bit-identical.

Two commands make this checkable rather than recalled:

```
python scripts/foundation_candidate.py check  --receipt docs/inventories/foundation-candidate-0.3.0a1.json
python scripts/foundation_candidate.py verify --receipt docs/inventories/foundation-candidate-0.3.0a1.json --wheel <path>
```

`check` refuses to *start* a dependent step on a candidate that may not outlive
it. `verify` proves a local file **is** the recorded candidate before anything
depends on it.

## Dependents — invalidate all of these together

Each of these names the digest above. If the candidate goes, they go with it;
none may be carried across a new candidate.

| dependent | status |
|---|---|
| recovery / restore proof | not yet run |
| issuer bootstrap | **blocked** — no issuer exists |
| Lane 3 exposure rehearsal (`exposure-rehearsal.yml`) | **blocked** — needs a Platform CP authorization run ID, and the `dotmac-control-runner` is unregistered |
| publication (`release-facility.yml`) | not reached; must reuse these bytes, never rebuild |

Lane 3 is blocked on a security decision that is Michael's alone: ADR a98
requires a `staff_admin` `WebFacetMount` with an `admission_permission`, and
the assembly declares no permissions at all. Foundation must never mint or
self-attest its own authorization.

## What the candidate lane cannot do

`foundation-candidate.yml` declares **no `environment:`** and
`contents: read` / `actions: read` only — no `packages: write`, no
`contents: write`, no tag command, no publishing tool. Publish authority in
this repository rests on exactly the two declarations it omits, so it is
incapable of publishing rather than merely not asked to.
`tests/architecture/test_candidate_lane_cannot_publish.py` holds that shut with
planted mutations.
