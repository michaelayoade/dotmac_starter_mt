# Deployment foundation — both implementations exercised; release oracle remains exact-main

**Status as of 2026-08-28. Lane 1 ran green in an isolated environment. Lane 2
has exercised every ordered subject and all 21 injection subjects on an
explicitly authorised disposable test host.** The lanes prove different things
and remain reported separately. Neither branch evidence is a release oracle:
the first publication still requires a successful GitHub rehearsal at the
exact merged main SHA being released.

| Lane | What it proves | State |
|---|---|---|
| **Lane 1** — the written suites | the code does what its own tests say | **RUN.** Observer, commit `484d3ac6`: `tests/unit tests/architecture` exit 0, **zero failures**; **12/12** quality targets pass. All three adapter descriptors `validate`, and `render --check` confirms the committed output matches byte-for-byte. |
| **Lane 2** — the disposable host | a real engine, database, Nginx handoff, restore and observability loop | **IMPLEMENTATION EXERCISED.** The 2026-08-28 branch run used Docker 29.1.3 / Compose 2.40.3 and reached the complete 13-step ordered lane plus the 21-case zero-skip matrix. Publication still waits for the encoded exact-main GitHub oracle. |

Workstation runs are never accepted as test evidence. Lane 1's evidence is an
Observer run at an exact commit in a fresh checkout; the local tree is not the
evidence.

`AGENTS.md` rule 30: a repository-local fact is not evidence of a run. That
still binds Lane 2 completely, and it binds any PUBLICATION claim — see the
closing section.

The Lane 2 runs found real defects rather than producing false green:

| Run | Furthest proved point | Finding |
|---|---|---|
| first | rendered project load | Compose rejected disagreeing `pids_limit` and `deploy.resources.limits.pids` aliases |
| second | database start | a first-success readiness wait raced Postgres' temporary init server |
| third | backup and drift proofs | the non-root collector could not write its harness-owned sink, and timeout diagnostics hid the reason |
| `33111496459` | steps 1-12 | recovery required `alertname` to remain in an active-instance array after recovery had correctly removed the instance |
| authorised test host, 2026-08-28 | ordered lane + injection matrix | four injections had false premises; schema dumps carry random `restrict` tokens; three declared cases were skipped; Compose project identity came from a directory basename; and the written Nginx handoff claim had never loaded Nginx |

The latest run proved real drift refusal, backup restore to `PROVED`, truncated
backup rejection, and the nine required attributes on a real collector signal.
Those are disposable-harness results, not adopter or production wiring.

## Engine evidence, and what has NOT been established

Every statement in this document about "what the engine accepts" is evidence
from a named engine, and there are two — they are NOT the same version, which
is worth more than it looks.

| Engine | Where | Role |
|---|---|---|
| **Docker 28.0.4 / Compose 2.38.2** | GitHub-hosted runner | runs the rehearsal (Lane 2) and the required per-PR parse in `ci.yml` |
| **Docker 29.4.3 / Compose v5.1.3** | Observer | where the `pids` alias behaviour was isolated and the three candidate shapes compared |
| **Docker 29.1.3 / Compose 2.40.3** | explicitly authorised disposable test host | ran the complete branch rehearsal, including live Nginx handoff and all failure subjects |

The `pids_limit` defect was REJECTED by 28.0.4/2.38.2 (that is how the rehearsal
found it) and independently reproduced on 29.4.3/5.1.3. The repair is ACCEPTED
by both. Two independent versions, roughly a major apart, agreeing in both
directions is meaningfully stronger than one — but it is still two data points,
not a matrix.

| Question | State |
|---|---|
| the rendered project loads on Docker 28.0.4 / Compose 2.38.2 | **yes**, checked on every PR (`ci.yml` `docker-build`) and by every consuming product (`deployment-conformance.yml`) |
| the rendered project loads on Docker 29.4.3 / Compose v5.1.3 | **yes**, checked by hand |
| a supported Compose-version matrix exists | **no** |
| the adopter hosts' engine versions are known | **no — census before any cutover** |
| Podman / podman-compose or any non-Docker engine | **untested** |

The per-PR step prints `engine <version>` and `compose <version>` before it
parses, so the version this evidence rests on is recorded in every run's log
rather than inferred from this table months later.

The first rehearsal found that `pids_limit` beside a `deploy.resources.limits`
block listing only cpus and memory is rejected, because the two keys are
aliases and an absent `pids` counts as disagreeing. That is a Compose
*specification* property, so it very likely generalises — but "likely
generalises" is not the same as tested, and this document does not upgrade one
to the other. Before retiring any product's existing deployment engine, record
that product's host engine versions here and re-run the parse against them.

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

## Product observability is DEFINITION-ONLY; the harness is not the product

Reviewed 2026-08-26; amended 2026-08-27. The facility generates a product
collector configuration and a set of product alert rules. No adopter consumes
either. The disposable rehearsal separately starts a harness-owned collector,
Prometheus and one synthetic rule. That proves the execution primitives against
real processes; it does not connect the 22 rendered catalogue rules or an
adopter's signal path. A directory containing well-formed rules is not working
monitoring, and a control which cannot fire is worse than an absent one because
it is counted.

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

| Product/adopter link in the chain | State |
|---|---|
| collector configuration rendered | yes |
| a collector actually deployed | **no** |
| `/metrics` scraped by anything | **no** |
| alert rules loaded by a rule evaluator | **no** |
| alerts routed anywhere | **no** |
| deployment annotations emitted | **no** — `Annotation` is a type nothing calls |
| resource attributes on an adopter signal | **no** — the disposable harness proved its own nine attributes, not an adopter's export |
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
must be emitted, not a monitoring system. Lane 2 step 12 has proved nine
attributes on a real harness signal. Step 13 has proved the synthetic rule can
fire; the exact-main run's old predicate could not recognise recovery, and the
corrected predicate still needs its exact-SHA run. Neither result changes the
catalogue counts. Turning the product definitions into working monitoring is
its own piece of work with its own proof, after a product actually deploys
through this facility:

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

None of that adopter chain has been done, and none of it is claimed.

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
- **Lane 2 gates the first PUBLICATION, not merely production adoption, and
  that is now ENCODED rather than written down.** This package executes
  migrations, backup, handoff and rollback; shipping a version whose only
  evidence is fakes would put those paths in someone's hands having never met a
  real engine.

  `release-facility.yml` calls `scripts/require_rehearsal.py` as its first gate
  after the current-main freshness check — before anything is built, and long
  before the publish token exists. It asks the Actions API for a COMPLETED,
  SUCCESSFUL run of `deployment-rehearsal.yml` whose `head_sha` is
  byte-identical to the SHA under release, and fails closed on every ambiguity:
  no runs, a run in flight, any other conclusion, any SHA mismatch, or an
  oracle that cannot be read at all. There is no `--allow-missing`.

  A GitHub-hosted runner is the disposable host — real Docker daemon, destroyed
  after the job, and the script creates and tears down everything it uses
  (teardown is `if: always()`).

  The requirement stayed prose for one revision of this document, which was a
  defect: prose is bypassed by anyone who does not read it, including a future
  automation with no eyes. `tests/architecture/test_deployment_rehearsal_oracle.py`
  holds the gate in place, and every refusal case there carries a negative
  control so the suite cannot pass by refusing everything.
