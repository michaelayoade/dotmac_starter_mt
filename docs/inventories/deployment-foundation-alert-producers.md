# Deployment-foundation alert producers

Dated 2026-08-26. Covers the 64-row `COMMON_ALERTS` catalogue in
`packages/dotmac-deployment-foundation/src/dotmac_deployment_foundation/alerts.py`.
Facts, not mandates — see `docs/ARCHITECTURE.md` for as-built truth and
ADR-0006 §"The extraction rule" for what an inventory does and does not
license.

**22 of the 64 foundation alerts are backed by a producer that genuinely
emits the metric(s) the alert reads. 42 are `UNBACKED` — no process
anywhere in the Dotmac fleet, and no off-the-shelf exporter this facility
could plausibly run, emits that metric today.** An alert on a metric with no
producer never fires; it is not a quiet alert, it is a decoration reporting
coverage the deployment does not have. This document is the alert-by-alert
accounting behind `Alert.producer` (`alerts.py`) and the `UNBACKED_ALERTS`
ratchet.

## Methodology

`producer` is a closed vocabulary (`PRODUCERS` in `alerts.py`): five
standard, off-the-shelf infrastructure exporters (`node_exporter`,
`cadvisor`, `postgres_exporter`, `redis_exporter`, `blackbox_exporter`), the
`ingress` provider's own telemetry, the OTel `collector`'s own
self-telemetry, the product's own `application` `/metrics` endpoint, this
facility's own `deployment_engine`, and `unbacked`.

A metric counts as backed by a standard exporter when it is that exporter's
own stock, documented metric name — independent of whether THIS package's
Python code currently renders the sidecar that runs it (that is a
provider/compose-file concern, tracked separately from metric-name
honesty). A metric counts as backed by `application`, `deployment_engine`,
`ingress` or `collector` only when actual code was found emitting it; for
those four kinds nothing in this repository does today, which is why they
have zero members below despite being legitimate categories in the
vocabulary.

Concretely checked and found absent in this codebase (2026-08-26): no
`prometheus_client`/OTel-metrics import anywhere in `dotmac_kernel`; no
object-storage client; no circuit-breaker/external-dependency
instrumentation; no worker/outbox/inbox/scheduler metric export (the tables
exist in `dotmac_kernel.messaging`, nothing exports their depth); no metrics
module configured in `render/nginx.py`; `backup.py` and `drift.py` in this
package compute `BackupHealth`/`DriftReport` in-process and write no
metric; `telemetry.render_collector_config` renders an `otlp`-only
collector — no `prometheus` receiver, no remote_write exporter, so a
pull-based-Prometheus topology (`up`, `prometheus_remote_storage_*`) is not
what this pipeline builds. Full reasoning, including which cAdvisor/
postgres_exporter/redis_exporter metric names are real (checked against
public documentation) versus merely plausible-sounding, is recorded in
`alerts.py`'s `_METRIC_PRODUCERS` block comment — this document is its
alert-by-alert projection, not a duplicate of the research.

## Backed (22)

| Alert | Producer | Metric(s) |
|---|---|---|
| `FDN_READINESS_FAILING` | `blackbox_exporter` | `probe_success` |
| `FDN_LIVENESS_FAILING` | `blackbox_exporter` | `probe_success` |
| `FDN_SYNTHETIC_HEALTH_FAILING` | `blackbox_exporter` | `probe_success` |
| `FDN_TLS_CERT_EXPIRY_WARNING` | `blackbox_exporter` | `probe_ssl_earliest_cert_expiry` |
| `FDN_TLS_CERT_EXPIRY_CRITICAL` | `blackbox_exporter` | `probe_ssl_earliest_cert_expiry` |
| `FDN_CONTAINER_OOM_KILLED` | `cadvisor` | `container_oom_events_total` |
| `FDN_CONTAINER_CPU_SATURATION` | `cadvisor` | `container_cpu_usage_seconds_total`, `container_spec_cpu_quota` |
| `FDN_CONTAINER_MEMORY_SATURATION` | `cadvisor` | `container_memory_working_set_bytes`, `container_spec_memory_limit_bytes` |
| `FDN_HOST_CPU_SATURATION` | `node_exporter` | `node_cpu_seconds_total` |
| `FDN_HOST_MEMORY_SATURATION` | `node_exporter` | `node_memory_MemAvailable_bytes`, `node_memory_MemTotal_bytes` |
| `FDN_HOST_DISK_LOW` | `node_exporter` | `node_filesystem_avail_bytes`, `node_filesystem_size_bytes` |
| `FDN_HOST_DISK_CRITICAL` | `node_exporter` | `node_filesystem_avail_bytes`, `node_filesystem_size_bytes` |
| `FDN_HOST_INODES_LOW` | `node_exporter` | `node_filesystem_files`, `node_filesystem_files_free` |
| `FDN_HOST_CLOCK_SKEW` | `node_exporter` | `node_timex_offset_seconds` |
| `FDN_PG_DISK_LOW` | `node_exporter` | `node_filesystem_avail_bytes`, `node_filesystem_size_bytes` (at the PG data mountpoint) |
| `FDN_PG_DOWN` | `postgres_exporter` | `pg_up` |
| `FDN_PG_POOL_SATURATION` | `postgres_exporter` | `pg_settings_max_connections`, `pg_stat_activity_count` |
| `FDN_PG_LONG_TRANSACTION` | `postgres_exporter` | `pg_stat_activity_max_tx_duration_seconds` |
| `FDN_PG_REPLICATION_LAG_HIGH` | `postgres_exporter` | `pg_replication_lag_seconds` |
| `FDN_REDIS_DOWN` | `redis_exporter` | `redis_up` |
| `FDN_REDIS_MEMORY_HIGH` | `redis_exporter` | `redis_memory_used_bytes`, `redis_memory_max_bytes` |
| `FDN_REDIS_PERSISTENCE_STALE` | `redis_exporter` | `redis_rdb_last_save_timestamp_seconds` |

## Unbacked (42), grouped by what would back them

### The product's own HTTP-metrics middleware (`application`)

No HTTP request-count or latency-histogram instrumentation is wired into
`dotmac_kernel` — no `prometheus_client`, no OTel metrics import anywhere in
it. Needs a metrics middleware (e.g. the standard `http_requests_total` /
`http_request_duration_seconds_bucket` pair a `prometheus-fastapi-
instrumentator`-shaped tool produces) added to the kernel's request path.

| Alert | Metric(s) |
|---|---|
| `FDN_HTTP_5XX_RATE_HIGH` | `http_requests_total` |
| `FDN_HTTP_5XX_RATE_CRITICAL` | `http_requests_total` |
| `FDN_HTTP_LATENCY_P99_HIGH` | `http_request_duration_seconds_bucket` |
| `FDN_HTTP_TRAFFIC_DROP` | `http_requests_total` |

### A container-lifecycle signal cAdvisor does not expose

`container_restarts_total` and `container_exit_code` are not real cAdvisor
metrics (cAdvisor tracks `container_last_seen` and, since v0.40,
`container_oom_events_total`, but no per-container restart counter or exit
code; the closest real thing, `kube_pod_container_status_restarts_total`, is
Kubernetes-only and this fleet runs Compose hosts). Needs either a
Docker-events-based exporter or product-side instrumentation that watches
container lifecycle events directly.

| Alert | Metric(s) |
|---|---|
| `FDN_CONTAINER_RESTART_LOOP` | `container_restarts_total` |
| `FDN_CONTAINER_EXITED_UNEXPECTEDLY` | `container_last_seen`, `container_exit_code` |

### A custom postgres_exporter query for lock wait duration

`pg_locks_count` (a count by lock MODE) is real; a wait-DURATION gauge is
not a stock postgres_exporter metric. Needs a custom query (joining
`pg_locks`/`pg_stat_activity`) added to the exporter's queries file.

| Alert | Metric(s) |
|---|---|
| `FDN_PG_LOCK_WAIT_HIGH` | `pg_locks_wait_seconds` |

### The deployment engine writing a migration-head drift gauge

`product_expected_migration_head` names a fact only the deployment
descriptor knows (`spec.migration.expected_heads`) — no database exporter
can observe it. Needs the deployment engine (after running or verifying a
migration) to write both the DB-observed head and the approved head as a
gauge pair, the same shape `drift.py` already computes in-process without
exporting.

| Alert | Metric(s) |
|---|---|
| `FDN_PG_MIGRATION_HEAD_DRIFT` | `pg_alembic_version_info`, `product_expected_migration_head` |

### Real application-level queue depth

redis_exporter has no concept of an application "queue"; `--check-keys`
against real key names would produce `redis_key_size`, not this name.
Needs either app-side instrumentation of the queue's own length or
`--check-keys` configured against the product's actual Redis key patterns.

| Alert | Metric(s) |
|---|---|
| `FDN_REDIS_QUEUE_DEPTH_HIGH` | `redis_queue_depth` |

### Worker/task-runner instrumentation (`application`)

No worker, task-runner or scheduler in the fleet exports backlog, failure,
heartbeat or poison-job counts as metrics today.

| Alert | Metric(s) |
|---|---|
| `FDN_WORKER_BACKLOG_HIGH` | `worker_queue_backlog` |
| `FDN_WORKER_FAILURE_RATE_HIGH` | `worker_task_failures_total`, `worker_task_runs_total` |
| `FDN_WORKER_HEARTBEAT_STALE` | `worker_last_success_timestamp_seconds` |
| `FDN_WORKER_POISON_JOBS_DETECTED` | `worker_task_poison_total` |
| `FDN_SCHEDULER_TICK_STALE` | `scheduler_last_tick_timestamp_seconds` |

### Outbox/inbox instrumentation (`application`)

`dotmac_kernel.messaging` owns the outbox/inbox tables (idempotency,
relay/leasing); nothing exports their queue depth, oldest-pending age or
dead-letter count as a metric.

| Alert | Metric(s) |
|---|---|
| `FDN_OUTBOX_BACKLOG_HIGH` | `outbox_queue_depth` |
| `FDN_OUTBOX_RETRY_AGE_HIGH` | `outbox_oldest_pending_age_seconds` |
| `FDN_OUTBOX_DEAD_LETTERS_PRESENT` | `outbox_dead_letter_count` |
| `FDN_INBOX_BACKLOG_HIGH` | `inbox_queue_depth` |
| `FDN_INBOX_RETRY_AGE_HIGH` | `inbox_oldest_unprocessed_age_seconds` |
| `FDN_INBOX_DEAD_LETTERS_PRESENT` | `inbox_dead_letter_count` |

### The ingress provider's own telemetry (`ingress`)

`render/nginx.py` configures no metrics module (no `stub_status`, no vts,
no exporter sidecar) today; these exact metric names also don't match any
off-the-shelf nginx exporter's default naming (nginx-prometheus-exporter's
`stub_status`-based metrics, or `nginx-module-vts`'s `nginx_vts_*` family).
Needs an ingress-side metrics module deployed AND its real metric names
used, not this generic placeholder pair.

| Alert | Metric(s) |
|---|---|
| `FDN_INGRESS_4XX_RATE_HIGH` | `ingress_requests_total` |
| `FDN_INGRESS_5XX_RATE_HIGH` | `ingress_requests_total` |
| `FDN_INGRESS_UPSTREAM_LATENCY_HIGH` | `ingress_upstream_response_seconds_bucket` |

### External-dependency client instrumentation (`application`)

No circuit-breaker or outbound-call client instrumentation exists anywhere
in the fleet.

| Alert | Metric(s) |
|---|---|
| `FDN_EXTERNAL_DEP_LATENCY_HIGH` | `external_dependency_request_duration_seconds_bucket` |
| `FDN_EXTERNAL_DEP_ERROR_RATE_HIGH` | `external_dependency_requests_total` |
| `FDN_EXTERNAL_DEP_BREAKER_OPEN` | `external_dependency_circuit_open` |

### `secret_sources.py` exposing refresh/expiry as metrics (`application`)

`dotmac_kernel.secret_sources` (`refresh_secrets`, `install_secret_source`)
tracks names in memory but exports no timestamp gauge.

| Alert | Metric(s) |
|---|---|
| `FDN_SECRET_REFRESH_FAILED` | `secret_refresh_last_success_timestamp_seconds` |
| `FDN_SECRET_MATERIAL_EXPIRING` | `secret_material_expiry_timestamp_seconds` |

### An object-storage client that does not exist yet (`application`)

No S3/MinIO/object-storage client exists anywhere in `dotmac_kernel` or this
package to instrument.

| Alert | Metric(s) |
|---|---|
| `FDN_OBJSTORE_FAILURE_RATE_HIGH` | `objstore_requests_total` |
| `FDN_OBJSTORE_CAPACITY_HIGH` | `objstore_used_bytes`, `objstore_quota_bytes` |

### A `backup` exporter this facility must write (`deployment_engine`)

`backup.py` computes `BackupRecord`/`BackupHealth`/`Assurance` entirely
in-process (see its own docstring's four-level assurance model) and writes
no metric — the host `Effects` implementation that runs the actual backup
never exports one either.

| Alert | Metric(s) |
|---|---|
| `FDN_BACKUP_AGE_STALE` | `backup_last_success_timestamp_seconds` |
| `FDN_BACKUP_JOB_FAILED` | `backup_job_failures_total` |
| `FDN_RESTORE_NOT_VERIFIED` | `backup_last_restore_verified_timestamp_seconds` |

### The deployment engine writing drift gauges (`deployment_engine`)

`drift.py` computes `DriftReport`/`Comparison` entirely in-process (image,
config and manifest digests) and writes no metric.

| Alert | Metric(s) |
|---|---|
| `FDN_IMAGE_DRIFT_DETECTED` | `deployed_image_digest`, `desired_image_digest` |
| `FDN_CONFIG_DRIFT_DETECTED` | `deployed_config_digest`, `desired_config_digest` |
| `FDN_MANIFEST_DRIFT_DETECTED` | `deployed_manifest_digest`, `approved_manifest_digest` |

### A synthetic multi-step journey runner (`application`)

blackbox_exporter probes a single HTTP/TCP/DNS endpoint; it has no concept
of a multi-step "journey" with its own pass/fail counter. Needs a dedicated
journey runner (e.g. a scripted browser or API-sequence check) that
instruments its own failure counter.

| Alert | Metric(s) |
|---|---|
| `FDN_SYNTHETIC_JOURNEY_FAILING` | `synthetic_journey_failures_total` |

### A pull-based scrape/remote-write pipeline this facility does not build

`telemetry.render_collector_config` renders an OTLP-only collector: one
`otlp` receiver, one `otlp/platform` exporter. No `prometheus` receiver
means the collector never synthesizes the standard per-target `up` metric;
no remote_write exporter means `prometheus_remote_storage_*` (a Prometheus
server's own internal metric) never applies. Needs either a `prometheus`
receiver added to the rendered collector config, or a standalone Prometheus
server this facility does not render today.

| Alert | Metric(s) |
|---|---|
| `FDN_SCRAPE_TARGET_DOWN` | `up` |
| `FDN_REMOTE_WRITE_FAILING` | `prometheus_remote_storage_failed_samples_total` |

### A log-pipeline health metric under a name nothing produces

No tool in this stack — the collector's `filelog` receiver included — emits
a metric named `log_lines_received_total`. Needs either the collector's own
receiver-accepted-records self-telemetry wired into an alert, or a
dedicated log-ingestion counter.

| Alert | Metric(s) |
|---|---|
| `FDN_LOG_INGESTION_GAP` | `log_lines_received_total` |

### A literal name mismatch against the collector's real self-telemetry

The OTel collector's real metric is `otelcol_exporter_send_failed_spans`
(no `_total` suffix, `otelcol_` prefix) — confirmed against upstream
collector documentation and issue tracker. `otel_exporter_send_failed_spans_total`,
as written, will never match any series this or any other OTel collector
emits. This is a correction to the EXPRESSION, not just a missing producer:
even with `collector` as the intended producer, the string is wrong.

| Alert | Metric(s) |
|---|---|
| `FDN_TRACE_EXPORT_FAILING` | `otel_exporter_send_failed_spans_total` |

### A dedicated heartbeat pusher (`deployment_engine`)

Nothing in the fleet increments a dead-man's-switch heartbeat counter on a
schedule. Needs a scheduled job (or the deployment engine itself, on a
timer) pushing to a pushgateway or the collector.

| Alert | Metric(s) |
|---|---|
| `FDN_TELEMETRY_DEADMAN` | `deadman_heartbeat_total` |

---

## Renderability is one of four conditions (2026-08-27)

**Nothing in this repository is an operationally enabled alert. What is
produced is a RENDERABLE DEFINITION.** Renderability is one of four
conditions, and the only one this repository can establish.

| Count | State |
|---|---|
| 64 | catalogued in `COMMON_ALERTS` |
| 22 | producer-backed, therefore **renderable** |
| 0 | connected to an evaluator or a routing path |
| 0 | fire/recovery-proven |

A rendered rule is either backed and rendered, or explicitly omitted. There is
no third state, and a label is not a state.

Until 2026-08-27 the 42 unbacked rows still rendered into
`deploy/rendered/alerts.rules.yml` carrying `dotmac_unbacked: "true"` and a
`# UNBACKED:` comment. That reads like a disclosure, but a Prometheus evaluator
does not read comments or labels — it loads the rule and evaluates it. A rule
whose metric nothing emits **never fires**, and a rule that never fires is
indistinguishable from a system that is never unhealthy. Sixty-four rules
loaded, twenty-two capable of firing, and a dashboard that looks fully covered
is a worse position than twenty-two rules and a stated gap.

`render_alert_rules(..., include_unbacked=False)` is therefore the DEFAULT. The
rendered file names the omitted count, the reason, this document, and the
opt-in, directly above the `rules:` list. `include_unbacked=True` still renders
them for catalogue review; it is not for loading into an evaluator.

### Four conditions, all required, before an alert is operationally live

| # | Condition | How it is established |
|---|---|---|
| 1 | **Producer / renderable** — some process actually emits every metric the expression reads, so the definition is worth rendering | `producer_consistency_errors()` plus the `UNBACKED_ALERTS` two-directional ratchet. **The only one established today.** |
| 2 | **Evaluator** — a running Prometheus (or compatible) has the rule loaded | a live query against the evaluator's `/api/v1/rules`, naming the alert |
| 3 | **Routing path** — a firing alert reaches a human through a named receiver | Alertmanager config resolving the alert's labels to a receiver, with the receiver proven reachable |
| 4 | **Fire/recovery proof** — the alert has been *observed* to fire on an induced fault and to recover when the fault is removed | the disposable-host rehearsal (Lane 2) and the live observability chain |

Conditions 2-4 are **not established for any alert today**, backed ones
included. Condition 1 is why the other 42 are omitted rather than merely
labelled. Rendering a rule without 2-4 buys a definition, not coverage — see
`docs/inventories/deployment-foundation-rehearsal.md` for which lane proves what.

**Vocabulary discipline.** Call the 22 *renderable definitions*, never
"enabled", "active", "live" or "covered". The wrong word retires three unmet
conditions by implication, which is the exact failure the omission of the 42
was meant to prevent.

**Production cutover is gated on all four**, per Michael's 2026-08-27 release
train. Moving a code from `UNBACKED` to backed satisfies condition 1 only — it
makes the definition renderable, and nothing more.
