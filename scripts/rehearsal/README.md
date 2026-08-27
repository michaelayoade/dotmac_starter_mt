# Deployment foundation rehearsal — disposable product

This directory is the disposable product `scripts/deployment_rehearsal.sh`
deploys to prove Lane 2 of
`docs/inventories/deployment-foundation-rehearsal.md`: what a real,
disposable host adds that a unit test cannot. Read that document first — it
states which claims this rehearsal exists to establish and why a fake
`Effects` implementation cannot establish them.

**Status: nothing here has been run.** Every file in this directory and the
driving script were written and are unrun, exactly as the inventory document
says of the rest of `dotmac-deployment-foundation`. This rehearsal runs ONLY
when Michael has explicitly authorised the run, and ONLY against disposable
infrastructure it creates and destroys itself.

## What it touches, and what it never touches

- **It touches**: a disposable local Docker registry, a disposable image
  built from this directory's `Dockerfile`, two disposable Postgres
  databases (a primary and a restore target), a disposable Docker network, a
  disposable OpenTelemetry Collector, and a disposable Prometheus. Every
  container and database name it creates contains the literal string
  `rehearsal` — `scripts/deployment_rehearsal.sh`'s `require_disposable_name`
  refuses to run against anything whose name does not, the same discipline
  `dotmac_sub`'s integration lane applies before it will run a single query.
- **It never touches**: a named environment (staging, production, or
  anything else the fleet actually runs), a real database, a real registry,
  or a real Observability platform endpoint. It names no host and authorises
  no deployment of anything else. It does not use `docker compose down
  --remove-orphans` anywhere — see the comment on that call in the driving
  script for the specific incident that rule exists to prevent.
- **What it removes when finished**: the registry container and the image it
  holds, both scratch Postgres containers, the compose-managed `app` and
  `migrate` containers and the network they ran on, the rehearsal's own
  OTel Collector and Prometheus containers, the built image (by tag and by
  digest), and its own working directory (`REHEARSAL_WORK_DIR`, default
  `${TMPDIR:-/tmp}/dotmac-deployment-rehearsal`). `down` is idempotent —
  safe to run again if a prior run was interrupted.

## How to run it, on Observer

```
ssh observe
# a fresh isolated writable checkout pinned to the exact commit under test —
# a shared checkout is not test evidence (docs/inventories/
# deployment-foundation-rehearsal.md, Lane 1)
git worktree add /srv/rehearsal/df-<sha> <sha>
cd /srv/rehearsal/df-<sha>
poetry install   # puts the `dotmac-deploy` console script on PATH

./scripts/deployment_rehearsal.sh all
```

`all` runs `up` (build the disposable infrastructure once), `run` (the 14
ordered steps), every failure-injection case, then `down`. Each subcommand
is also runnable on its own — see the script's own header comment for what
each does, and:

```
./scripts/deployment_rehearsal.sh list-cases   # every case `inject` accepts
./scripts/deployment_rehearsal.sh up
./scripts/deployment_rehearsal.sh run
./scripts/deployment_rehearsal.sh inject migration-lock-contention
./scripts/deployment_rehearsal.sh down
```

Every path, port, image tag, database name, network name and timeout is a
`: "${VAR:=default}"` knob at the top of `scripts/deployment_rehearsal.sh` —
override any of them by exporting the variable before invoking the script.
The one thing NOT overridable away is the disposable-name check itself.

## Three cases are SKIPPED, honestly, and why

`scripts/deployment_rehearsal.sh inject` genuinely exercises seventeen of
the twenty required failure-injection cases against real infrastructure.
Three print `SKIPPED: <case> — <reason>` instead, because this disposable
product structurally cannot exercise them:

- **`candidate-never-ready`** — this product declares no `[ingress]` route
  (nginx is out of scope on Observer, per this rehearsal's own design). The
  warm-candidate mechanism (`ComposeHostEffects.start_candidate`/
  `candidate_ready`) derives its port from an ingress route and raises
  without one, so there is no candidate to gate.
- **`worker-unhealthy`** and **`scheduler-stale`** — this product declares
  one plain HTTP role with no `[roles.worker]`/`[roles.scheduler]`
  contract. Proving these against real infrastructure would need a
  Celery-shaped role, out of scope for the minimal reference product this
  rehearsal deploys.

All three are already proven against a scripted fake in
`tests/unit/test_deployment_foundation_failure_injection.py`
(`test_a_candidate_that_never_becomes_ready_is_not_handed_traffic`,
`test_an_unhealthy_worker_fails_the_deployment_even_though_its_container_is_up`,
`test_a_stale_scheduler_fails_the_deployment`). Because a silent skip is
exactly what this whole programme exists to remove, the script exits
non-zero whenever any case is skipped unless `ALLOW_SKIPS=1` is set — a
skip is a fact to report, never a fact to hide behind a green exit code.

## What "wrong-manifest-digest" actually proves

`build_plan` (`engine/plan.py`) has no step that checks
`assembly.manifest_digest` — the twenty-two-step deployment plan verifies
the image digest, the source revision, release evidence, materials and
heads, but the product-manifest digest is compared only by
`dotmac-deploy drift`. The `wrong-manifest-digest` case is written to prove
exactly that: it asserts `drift` reports DRIFT and exits 1, not that a
deployment refuses mid-flight. If a future manifest-verification GATE is
added to the plan, this case should be extended to prove that gate too,
alongside `drift`.

## Files here

| File | What it is |
|---|---|
| `product.toml` | A real `ProductDeploymentSpec.v1` descriptor, same schema as `deploy/product.toml`, with six `@@NAME@@` placeholders the driving script substitutes into a working copy — never edit a substituted copy, edit this file and re-run. |
| `Dockerfile` | Builds the disposable product's image to the same hardened contract `dotmac_deployment_foundation.image.audit` checks against every real product image. |
| `app.py` | The disposable product itself: `/health/live` (DB-free, always 200), `/health/ready` (503 until a marker file exists), `/metrics` (one gauge), `--migrate` and `--heads` modes that speak to Postgres via `psql`. |
| `thresholds.json` | Product-threshold alert placeholder values, same shape as `deploy/alerts/thresholds.json`, scoped to this rehearsal's own short-fused values. |

## If it fails

A failure here is a finding about the facility, not about the harness — the
whole design goal of Lane 1's fake-`Effects` unit suite was to prove the
gates fire in isolation; this lane exists specifically to prove they still
fire against the real world. Report the exact numbered step or case name
that failed and its printed reason; do not re-run with a wider try/except
or a swallowed exit code to make it green.
