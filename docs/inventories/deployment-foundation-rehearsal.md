# Deployment foundation — Lane 1 is run, Lane 2 is not

**Status as of 2026-08-27. Lane 1 RAN on Observer; Lane 2 has NOT run.** The
two lanes prove different things and only one of them has evidence, so they are
reported separately rather than under one word.

| Lane | What it proves | State |
|---|---|---|
| **Lane 1** — the written suites | the code does what its own tests say | **RUN.** Observer, commit `484d3ac6`: `tests/unit tests/architecture` exit 0, **zero failures**; **12/12** quality targets pass. All three adapter descriptors `validate`, and `render --check` confirms the committed output matches byte-for-byte. |
| **Lane 2** — the disposable host | a real engine, a real database, a real handoff | **NOT RUN.** `scripts/deployment_rehearsal.sh` is written and has never been executed against a host. |

Tests are never run on the workstation. Lane 1's evidence is an Observer run at
an exact commit in a fresh checkout; the local tree is not the evidence.

`AGENTS.md` rule 30: a repository-local fact is not evidence of a run. That
still binds Lane 2 completely, and it binds any PUBLICATION claim — see the
closing section.

## What is already proven, and by what

| Claim | Proven by | Where |
|---|---|---|
| the descriptor parses, refuses what it says it refuses | `ProductDeploymentSpec` exercised directly | `test_deployment_foundation_*`, run on Observer |
| the plan's gate half mutates nothing | `first_mutating_index` vs the step list | `test_deployment_foundation_failure_injection.py` |
| twenty failure cases refuse at the right step | fake `Effects`, one knob per test | same file |
| the image audit fires on a planted image | driven against planted `docker inspect` JSON — nine violations, including the build-context rule whose prefix-matching bug this found | `image/audit.py` |
| `render --check` fails on a one-line hand-edit | run on Observer against all four descriptors | `deploy/rendered` |
| drift reports DRIFT, UNKNOWN and MATCH apart, with the right exit codes | planted observations, run on Observer | `drift.py` |
| the facility imports no kernel, ORM or web framework | AST guard with planted-defect proofs | `test_deployment_foundation_facility.py` |

None of these needed infrastructure, which was the design goal. What follows is
what genuinely cannot be established without it.

## Lane 1 — the written suites, on Observer (RUN 2026-08-27 at `484d3ac6`)

Prerequisites: Michael authorises the run and the branch commit is transferred.
The commands below are what was executed; re-run them at any later commit.

```
ssh observe
# fresh isolated writable worktree pinned to the exact commit under test;
# a shared checkout is not test evidence
git worktree add /srv/rehearsal/df-<sha> <sha>
cd /srv/rehearsal/df-<sha>
poetry install
poetry run pytest -n 2 \
  tests/unit/test_deployment_foundation_secrets_guard.py \
  tests/unit/test_deployment_foundation_compose.py \
  tests/unit/test_deployment_foundation_nginx.py \
  tests/unit/test_deployment_foundation_alerts.py \
  tests/unit/test_deployment_foundation_failure_injection.py \
  tests/unit/test_deployment_foundation_compose_host.py \
  tests/architecture/test_deployment_foundation_facility.py \
  tests/architecture/test_deployment_release_lane.py \
  tests/architecture/test_product_first_extraction.py \
  tests/architecture/test_lockfile_path_packages.py
make check
```

Cap xdist at `-n 2` or `-n 3`; never `-n auto`. Tear the worktree down after.

## Lane 2 — what a disposable host adds that a unit test cannot

Each row is a claim no fake can establish, with the exact reason.

| Claim | Why a fake cannot establish it |
|---|---|
| the rendered Compose file is valid to the real engine | the renderer emits text; only `docker compose config` knows whether the engine accepts it |
| `service_completed_successfully` really gates the runtime roles | a fake asserts the rendered key; only a run proves the daemon honours it |
| a read-only root with a `/tmp` tmpfs actually boots | a process needing another writable path fails at runtime and nowhere else |
| the migration runs as the owner and the runtime role genuinely cannot do DDL | needs two real Postgres roles and a real `CREATE TABLE` refusal |
| `alembic upgrade heads` produces exactly the declared heads | a fake returns the heads it was told to |
| `nginx -t` accepts the rendered site, and the `backup` upstream really takes over | nginx's own parser is the only authority, and failover is a runtime behaviour |
| a truncated `pg_dump` fails full-decompression verification | the whole point is a real half-written archive |
| a restore into a disposable database reaches PROVED | schema, row counts and heads have to be read from a restored database |
| a signal arrives at the collector carrying all nine resource attributes | `missing_attributes` checks an observation; something must produce one |
| an alert fires AND recovers | a rule file is not a firing rule |

### The ordered rehearsal

Disposable everything: a scratch Postgres whose name identifies it as test data,
a throwaway network, a temporary image tag. Nothing touches a named environment,
and no production host is involved at any point.

1. Build the image once. Record the digest. **Never rebuild** — every later step
   uses that digest.
2. `dotmac-deploy image-audit` against it. Expect a pass; a failure here is a
   real finding about the Dockerfile, not about the harness.
3. `dotmac-deploy render`, then `docker compose config` on the result.
4. Bring up the scratch database with two roles. Prove the online role cannot
   `CREATE TABLE`.
5. `docker compose up migrate`. Assert it exits 0 and that no runtime container
   started first.
6. Assert the real heads equal `expected_heads`.
7. Start the runtime roles. Assert `/health/live` answers with the database
   STOPPED, and that `/health/ready` answers 503 in the same state. This is the
   ERP defect, inverted into a test.
8. Restore the database, gate the candidate on readiness, switch, verify every
   role runs the one digest with zero restarts.
9. `dotmac-deploy drift` against the running state. Expect clean. Then hand-edit
   one rendered byte on the host and expect DRIFT.
10. Take a backup, verify it, restore it into a second disposable database, and
    assert PROVED.
11. Truncate a backup deliberately and assert verification FAILS. Without this
    the previous step passes on a checker that accepts anything.
12. Point the collector at a sink; assert nine attributes on a real signal.
13. Fire one alert and assert it recovers.
14. Tear everything down.

### Failure injection, on the same disposable host

The unit matrix already covers these against the plan; this lane proves the same
refusals against the real world. Run each, assert the deployment stops at the
NAMED step, and assert the database is unchanged for every gate-phase case:

wrong image digest · wrong product-manifest digest · missing migration
credentials · owner credentials present in a runtime role · migration failure ·
migration lock contention · missing migration head · failed backup · corrupt
backup · failed restore verification · candidate never ready · primary fails
after handoff · worker unhealthy · scheduler stale · invalid Nginx configuration
· telemetry collector unavailable · secrets unavailable at startup and at
refresh · untracked override or source bind mount · previous image reused after
an incompatible migration · a `maintenance_required` migration attempted through
the online path.

## Observability is DEFINITION-ONLY, and that is the honest word for it

Reviewed 2026-08-26; amended 2026-08-27. The facility generates a collector
configuration and a set of alert rules. Nothing consumes either. Stated plainly
because a directory containing well-formed alert rules reads, to anyone who
does not check, as working alerts — and the whole point of this programme is
that a control which cannot fire is worse than an absent one, because it is
counted.

**The vocabulary is load-bearing. These are RENDERABLE DEFINITIONS, not enabled
alerts:**

| Count | State |
|---|---|
| 64 | catalogued |
| 22 | producer-backed, therefore renderable — **what the default render emits** |
| 0 | connected to an evaluator or a routing path |
| 0 | fire/recovery-proven |

Since 2026-08-27 the 42 unbacked rows are OMITTED from the default render
rather than rendered with a `dotmac_unbacked` label, because an evaluator reads
neither labels nor comments. Calling the remaining 22 "enabled" would retire
the last two rows of that table by implication.

| Link in the chain | State |
|---|---|
| collector configuration rendered | yes |
| a collector actually deployed | **no** |
| `/metrics` scraped by anything | **no** |
| alert rules loaded by a rule evaluator | **no** |
| alerts routed anywhere | **no** |
| deployment annotations emitted | **no** — `Annotation` is a type nothing calls |
| resource attributes on a real signal | **no** — `missing_attributes` checks an observation; nothing produces one |
| backup/restore evidence reaching a dashboard | **no** |

**Measured 2026-08-26: 22 of the 64 are backed, 42 are not.** The backed ones
are all standard-exporter metrics (`node_exporter` 7, `blackbox_exporter` 5,
`postgres_exporter` 4, `cadvisor` 3, `redis_exporter` 3) — real, documented
stock metric names, which is a separate question from whether this facility
currently renders those exporters as sidecars. It does not; that is provider
wiring still owed.

The other 42 read metrics **nothing emits**, and they are enumerated with what
would have to be built for each in
`docs/inventories/deployment-foundation-alert-producers.md`. Two findings there
were not on the original list and are worth naming: the trace-export alert reads
`otel_exporter_send_failed_spans_total`, and the real collector metric is
`otelcol_exporter_send_failed_spans`; and the `up` / remote-write alerts assume a
pull-based Prometheus topology that this facility's OTLP-only collector
configuration does not create.

Every unbacked rule now renders with a `# UNBACKED:` comment above it and a
`dotmac_unbacked: "true"` label, and `render_alert_rules(include_unbacked=False)`
emits only the 22 and says how many it omitted — so a rules file cannot be
mistaken for coverage by anyone reading it.

**Several catalogue alerts reference metrics no Dotmac process emits.**
`worker_queue_backlog`, `outbox_queue_depth`, `outbox_oldest_pending_age_seconds`,
`scheduler_last_tick_timestamp_seconds`, `backup_last_success_timestamp_seconds`,
`deadman_heartbeat_total` and the drift gauges are all named by the catalogue
and produced by nothing today. An alert on a metric with no producer never
fires: it is not a quiet alert, it is a decoration that reports coverage.

The honest reading of the catalogue is therefore: it is a specification of what
must be emitted, not a monitoring system. Turning it into one is its own piece
of work with its own proof, and belongs after a product is actually deploying
through this facility — the sequence is:

1. deploy the collector as part of the rendered deployment;
2. authenticate the export to the Observability platform;
3. scrape `/metrics` and confirm the nine resource attributes arrive on a real
   signal;
4. for each catalogue alert, either point it at a metric something emits or
   record it as unbacked — a ratchet that only shrinks;
5. load the rules, fire one deliberately, and watch it recover;
6. emit deployment-start/success/failure/rollback annotations from the executor
   and see them on a dashboard;
7. run the dead-man signal and prove it fires when the pipeline stops.

None of that has been done, and none of it is claimed.

## The Governance re-pin is NOT part of this branch, and here is the evidence

Step 5 of the accepted train is "land the Governance authority/pin update".
It is deliberately **not** in these branches, for two reasons that are worth
recording rather than assumed.

**Order.** The authority row should reference a RELEASED facility. Adding it
before publication would name a distribution nobody can install, which is the
same defect as the placeholder digests.

**Risk, measured rather than guessed.** `dotmac_starter_mt` pins Governance
`e0f636c3f5adea77da136f75f49c2349fbf9eeaa`; `dotmac_erp`, `dotmac_sub` and
`dotmac_integrator` all pin `a19259b10568d29dc0a9617347498fea7f1e7a97`. All four
are profile schema 9, so the move is not a schema migration — but two commits in
that range add RULES to `standards_control/engine.py`:

| Commit | Adds |
|---|---|
| `916ec92` (#24) | distinguishes connector SOURCE from product debt in the external-connector ratchet (+134 lines of engine) |
| `e0f636c` (#26) | `checkpoint` must name its feed, as `cursor` already does (+19 lines of engine) |

So moving three products onto the newer pin subjects each to two checks it has
never been evaluated against. Either could fail for reasons entirely unrelated
to deployment, and finding that out inside a deployment pull request is how a
focused change becomes an unreviewable one — which the commissioning task
warned against in terms ("keep each implementation slice coherent and
reviewable"; "do not combine all repositories into one giant PR").

The re-pin is therefore its own change, per product, with its own CI run. What
is owed: bump the pin, run the standards job, and fix whatever #24 and #26
newly find — and only then add the facility's authority row.

## What this rehearsal deliberately does NOT do

- It names no production or staging host, and authorises no deployment.
- It does not retire any product's existing engine. Retirement is a separate
  change per product, gated on proven parity, and parity is not this document.
- It does not make a release claim. **The release lane EXISTS** —
  `.github/workflows/release-facility.yml`, its closed allowlist
  `.github/release-facilities.json`, `scripts/release_facility.py`, and the
  guard `tests/architecture/test_deployment_release_lane.py` — but a lane that
  exists is not a publication. Publication needs a `release_run` oracle (rule
  30), and no run has been dispatched, so no version of this distribution is
  published or pinnable.
- **Lane 2 gates the first PUBLICATION, not merely production adoption.** This
  package executes migrations, backup, handoff and rollback; shipping a version
  whose only evidence is fakes would put those code paths in someone's hands
  having never met a real engine. The disposable-host rehearsal runs before the
  release lane is dispatched, not after.
