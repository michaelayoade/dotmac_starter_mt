# dotmac-deployment-foundation

One build-and-deploy facility for every Dotmac product assembly. A product
declares `deploy/product.toml` and nothing else; the Compose file, the ingress
site, the collector configuration, the alert rules and the ordered deployment
plan are all rendered or derived from it.

Decision: [ADR-0070](../../docs/adr/0070-deployment-is-a-stateless-versioned-foundation.md).
Sources and defect list: [`EXTRACTION.toml`](EXTRACTION.toml) and
[`docs/inventories/deployment-foundation-sources.md`](../../docs/inventories/deployment-foundation-sources.md).

## What it is

A **universal facility**: no `ModuleManifest`, no models, no migrations, no
lineage, no tenant, and **zero runtime dependencies** — not the kernel, not
SQLAlchemy, not FastAPI, not Jinja, not a YAML library. Standard library only.

That is the same shape `dotmac-ui` holds and it exists for the same reason: a
build runner rendering a Compose file has no database and no web framework, and
must not acquire them in order to validate a descriptor. Two import-linter
contracts hold the boundary in both directions.

Nothing here runs anything. The deployment plan is DATA and the executor talks
to an injected `Effects` provider — which is what makes twenty failure cases
(wrong digest, failed backup, corrupt backup, candidate never ready, a
maintenance-required release attempted online) ordinary unit tests instead of
disposable-VM exercises. A gate that has never been shown to fire is a gate
nobody should trust.

## Quick start

```bash
dotmac-deploy validate                     # parse and check the descriptor
dotmac-deploy render -o deploy/rendered    # write every asset
dotmac-deploy render --check -o deploy/rendered   # fail on any difference
dotmac-deploy plan                         # the ordered plan, gates marked
dotmac-deploy preflight                    # only the steps that mutate nothing
dotmac-deploy backup                       # the policy, and what "verified" means
dotmac-deploy restore-rehearsal            # what a restore PROOF requires
dotmac-deploy image-audit REF --inspect i.json --history h.json --layers l.txt
dotmac-deploy observe --deployment-id 42 --host web1   # the resource stamp
dotmac-deploy drift --observed observed.json
dotmac-deploy rollback --previous-image sha256:...     # or why it is refused
```

Exit codes are a contract: `0` ok, `1` refused (a gate said no, a check found
drift), `2` usage.

## The descriptor

`ProductDeploymentSpec.v1` holds **material names and approved pointers, never
secret values** — refused at parse time, not at review time. Unknown keys are
refused rather than ignored, because a typo in `read_only` that silently
disables a read-only filesystem is exactly the defect this facility removes.

Refusals worth knowing before you write one:

| Refused | Why |
|---|---|
| an image reference that is a tag | build once, promote the digest; a bare `docker compose up -d` against a tag once downgraded a production deployment by five weeks |
| a runtime role holding the migration owner material | that role could create, alter and drop any table for the life of the deployment |
| liveness and readiness at the same path | one of them is then wrong: liveness must not touch a dependency, readiness must fail when one is down |
| an ingress role with no readiness probe | a candidate with no readiness gate is handed traffic on a timer |
| `static = "volume"` | static assets belong to the image digest; a bind mount is how a host came to serve a different tree from its image |
| a websocket or SSE route with a read timeout under 300s | the proxy silently severs the stream and it looks like an application bug |
| `no_new_privileges = false` | there is no deployment shape that needs it |
| a relaxed security default with no declared `[[roles.security.exceptions]]` | the grant may stay; the silence may not |
| a postgres backup dataset that does not verify `schema` | a restore producing an empty database passes every other check |
| both `app_direct_shipping` and `logs` | every line stored twice, every rate threshold silently doubled |

## Layout

```
spec.py          ProductDeploymentSpec.v1 — the one thing a product declares
secrets_guard.py the descriptor holds names, never values (ADR-0009)
render/          deterministic text emitters: compose, nginx
alerts.py        64 common infrastructure alerts + the product's own
telemetry.py     resource attributes, deployment annotations, collector config
image/           the hardened OCI contract, audited against `docker inspect`
engine/          the plan as data, the executor, the exclusive lock
providers/       concrete Effects implementations; compose_host is the only one
backup.py        four assurance levels, because "backed up" is not "restorable"
drift.py         image + config + manifest digests vs the approved plan
conformance.py   the checks a product runs in its OWN CI
cli.py           dotmac-deploy
```

## Status

Built and validated in this repository. **Not published, not installed from an
index, and not pinned by any product** — `AGENTS.md` rule 30: a repository-local
fact is not a release claim, and a release lane that exists is not a
publication.

Its test suites RUN: Observer, commit `484d3ac6`, `tests/unit tests/architecture`
exit 0 with zero failures and 12/12 quality targets passing. Tests are not run
on a workstation.

What has NOT run is the disposable-host rehearsal
(`scripts/deployment_rehearsal.sh`) — a real engine, a real database, a real
handoff and a real rollback. Because this package executes migrations, backup,
handoff and rollback, that rehearsal gates the FIRST PUBLICATION, not merely
production adoption. See `docs/inventories/deployment-foundation-rehearsal.md`.
