# Deployment foundation — product-first source inventory

**Dated characterization, 2026-08-26.** Facts about four repositories as they
stood at the revisions below. Not a mandate, not a plan, and explicitly not a
licence to extract anything not named here (ADR-0006 § "The extraction rule").

Audit coordinates:

| Repository | Revision | Branch at audit |
|---|---|---|
| `dotmac_sub` | `1a3edf0eb567fe02665606d368f8f342536f548c` | `main` |
| `dotmac_erp` | `7b62974b366eead1b32bead380e47d9cf10ec4c7` | `main` |
| `dotmac_integrator` | `78bcaebf4692fbde298ef2107b231d983e04a5c6` | `main` |
| `dotmac_starter_mt` | `ac5e439e622ec5adba94cf52f4f961f2c39a2d30` | `main` |

Consumed by `packages/dotmac-deployment-foundation/EXTRACTION.toml` and
ADR-0070.

---

## 1. Why an inventory was needed at all

`AGENTS.md` rule 24 requires the inventory before shared behaviour is written,
and this surface is the case the rule was written for. Three of four products
independently implement the same deployment sequence, and the fourth documents
itself as a hand-port of one of them:

> Ported from dotmac_sub's scripts/deploy.sh (the org's infra SoT)
> — `dotmac_starter_mt:scripts/deploy.sh:4`

A hand-port is a fork with a comment. It receives no fix the source later
makes, and `dotmac_starter_mt:scripts/deploy.sh` demonstrates the cost exactly:
the port dropped the deployment lock, the digest-and-evidence gates, the
dirty-tree refusal, the migration preflight, the migration lock and retry, and
the warm candidate — every one of which the source added *after* an incident.

---

## 2. Line counts, by repository

### `dotmac_sub` — the deployment engine

| Path | Lines |
|---|---|
| `scripts/deploy.sh` | 880 |
| `scripts/deploy_production.sh` | 167 |
| `scripts/deploy_staging.sh` | 38 |
| `scripts/db_backup.sh` | 89 |
| `scripts/docker_image_retention.sh` | 53 |
| `scripts/release_backup_policy.py` | 280 |
| `docker-compose.yml` | 850 |
| `docker-compose.dev.yml` | 81 |
| `deploy/shadow/docker-compose.shadow.yml` | 210 |
| `Dockerfile` | 53 |
| `nginx/selfcare.dotmac.io.conf` | 160 |
| `deploy/nginx/selfcare.dotmac.io` | 102 |
| `.github/workflows/staging-deploy.yml` | 317 |
| `.github/workflows/release-candidate.yml` | 221 |
| `.github/workflows/release-promotion.yml` | 226 |
| `.github/workflows/production-deploy.yml` | 166 |

### `dotmac_erp`

| Path | Lines |
|---|---|
| `scripts/deploy.sh` | 244 |
| `scripts/backup_erp_db.sh` | 65 |
| `scripts/restore_from_backup.py` | 70 |
| `scripts/sync-static.sh` | 61 |
| `scripts/bootstrap_database_roles.py` | — |
| `docker-compose.yml` | 247 |
| `Dockerfile` | 52 |
| `Dockerfile.hardened` | 122 |

### `dotmac_integrator`

| Path | Lines |
|---|---|
| `Dockerfile` | 112 |
| `docker-compose.yml` | 89 |
| `scripts/audit_image.sh` | — |
| `.github/workflows/release-image.yml` | 231 |

No `deploy.sh`, no backup script, no ingress configuration. Confirmed by
repository-wide `find`, not inferred from absence in a directory listing.

### `dotmac_starter_mt`

| Path | Lines |
|---|---|
| `scripts/deploy.sh` | 198 |
| `Dockerfile` | 78 |
| `docker-compose.yml` | 25 |
| `docker-compose.dev.yml` | 67 |

No ingress configuration, no standalone backup script, and no workflow that
builds this repository's own application image — so `deploy.sh`'s registry
check assumes a publication process this repository does not perform.

---

## 3. `dotmac_sub` — the deployment state machine, step by step

This is the extraction source for `engine/plan.py`. The order below is what
`scripts/deploy.sh` actually implements, with the environment knob and its
default, and whether the step fails closed.

| # | Step | Knob (default) | Fails closed |
|---|---|---|---|
| 1 | Deploy-dir resolution (`:49-52`) | `DEPLOY_DIR`, `REPO_DIR` | — |
| 2 | Exclusive lock (`:117-140`) | `DEPLOY_LOCK_FILE` = `/var/lock/dotmac_sub_deploy.lock`, `flock -n 9` | yes |
| 3 | Orphaned `pg_dump` guard (`:142-149`) | `DB_NAME` = `dotmac_sub` | yes |
| 4 | Target identification (`:597-616`) | `APP_ENV`/`SERVER_NAME` exact pairs | yes |
| 5 | Proxy warm-handoff contract (`:308-335`, `:634-635`) | `REQUIRE_PROXY_HANDOFF` = 1, `CANDIDATE_PORT` = 18001 | yes on prod |
| 6 | Compose service resolution (`:637-654`) | `CELERY_WORKER_SERVICES` | yes |
| 7 | Registry existence (`:656-657`) | `docker manifest inspect` | yes |
| 8 | Pull (`:662-663`) | — | yes |
| 9 | Image revision label (`:531-556`, `:667-671`) | `org.opencontainers.image.revision`, 40-hex | yes |
| 10 | Source-tree label (`:518-529`, `:672-676`) | `io.dotmac.release.source-tree` vs `git rev-parse HEAD^{tree}` | yes |
| 11 | CI / release-evidence gate (`:684-703`) | `PRODUCTION_RELEASE_EVIDENCE` | yes |
| 12 | Source-bind-mount refusal (`:404-425`, `:829`, `:838-841`) | container mount `Destination == /app/app` | yes |
| 13 | Traps (`:704-753`) | `cleanup_children`, `restore_prev` | — |
| 14 | Backup before DDL (`:718-753`) | `DEPLOY_BACKUP_MODE`, `PRODUCTION_BACKUP_DECISION_FILE` | yes |
| 15 | OpenBao boot-secret verification (`:754-757`) | — | yes |
| 16 | Reconciliation preflight (`:759-767`) | `--check` variants | yes |
| 17 | Pin `.env` `APP_IMAGE`/`GIT_SHA` (`:769-771`) | — | point of no easy return |
| 18 | Migration lock + retries, `upgrade heads` (`:378-400`, `:775`) | `MIGRATION_MAX_ATTEMPTS` = 4, `MIGRATION_RETRY_SECONDS` = 10 | yes |
| 19 | Post-migration contract verification (`:777-786`) | — | yes |
| 20 | Warm candidate (`:788-798`) | `CANDIDATE_CONTAINER`, `CANDIDATE_PORT` = 18001 | — |
| 21 | Candidate bind-mount check (`:800`) | — | yes |
| 22 | Candidate readiness gate (`:186-207`, `:801`) | `CANDIDATE_HEALTH_URL`, `HEALTH_TIMEOUT_SECONDS` = 180 | yes |
| 23 | Atomic switch / recreate (`:803-804`) | `APP_SERVICES` (11) | — |
| 24 | Primary bind-mount check (`:806-809`) | — | yes, degraded rollback |
| 25 | Primary health gate (`:812-823`) | `HEALTH_URL`, same timeout knobs | yes |
| 26 | Worker/beat verification + stability (`:288-304`, `:826`) | `BACKGROUND_STABILITY_SECONDS` = 15, `CELERY_INSPECT_TIMEOUT_SECONDS` = 5 | yes |
| 27 | Candidate drain (`:329-336`, `:829`) | `CANDIDATE_DRAIN_SECONDS` = 2 | — |
| 28 | Image retention (`:831-836`) | `IMAGE_RETAIN_COUNT` = 5 | **no — the one fail-open step** |

Two behaviours that the ported engine adopts and one it deliberately does not:

- **Adopted: the retry predicate is narrow.** Step 18 retries only on
  `lock timeout` or `canceling statement due to lock`; every other migration
  error propagates immediately. A blanket retry would run a failing migration
  four times.
- **Adopted: the health budget is a knob with a real default, and its default
  was wrong.** `HEALTH_TIMEOUT_SECONDS=180` caused a false rollback on an 8 GiB
  staging host when the primary started concurrently with six workers, a
  poller and a listener; the known-good rollback image missed the same budget
  and became healthy shortly after
  (`seabone-staging-dotmac-sub-deploy-landmines`, 2026-08-03). The descriptor
  therefore makes the budget per-role rather than global.
- **Not adopted: the unconditional image rollback.** `restore_prev` restores
  the previous image whatever the migration did. That is correct only when the
  migration was backward-compatible, and nothing in the script establishes
  that. ADR-0070 makes the release's own `compatibility` declaration the gate,
  and refuses the image rollback for a `maintenance_required` release.

---

## 4. `dotmac_integrator` — the image and migration-ordering source

Extraction source for the hardened image contract and the one-shot migration
service.

- Two-stage `python:3.12-slim`; registry credential only via a BuildKit secret
  (`Dockerfile:66-68`).
- Fixed non-root identity: `ARG APP_UID=10001` / `APP_GID=10001`,
  `useradd … --shell /usr/sbin/nologin app`, `USER app`
  (`Dockerfile:76-80`, `:96`).
- `exec uvicorn` as PID 1 (`Dockerfile:100-112`), with an explicit comment that
  there is no entrypoint script and therefore no `alembic upgrade`, no
  "migrate if needed" check (`Dockerfile:21-29`).
- `scripts/audit_image.sh:28-85` proves against the BUILT artefact that the uid
  is not 0, that the default `CMD` is not a migration command, and that no
  registry credential leaked into image history — and it carries its own
  sensitivity proof (`AGENTS.md` rule 17 in that repository).
- Runtime hardening in Compose: `read_only: true`, `tmpfs: ["/tmp"]`,
  `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`
  (`docker-compose.yml:81-84`).
- One-shot migration service (`docker-compose.yml:31-44`):
  `command: ["python","-m","dotmac_integrator.migrate","upgrade","heads"]`,
  `restart: "no"`,
  `MIGRATION_DATABASE_URL: ${MIGRATION_DATABASE_URL:?owner DSN is required}`,
  and `api.depends_on.migrate.condition: service_completed_successfully`.
- Credential split (`docs/RUNBOOK.md:9-16`): `DATABASE_URL` is `platform_api`
  and cannot create a table; `MIGRATION_DATABASE_URL` is `app_admin` and is
  absent from the `api` service's environment.
- Readiness that can actually fail (`src/dotmac_integrator/health.py:35-74`):
  `SELECT 1` plus an `information_schema.schemata` check, returning 503 until
  the schema is present; the Compose healthcheck hits exactly that endpoint.

This is the readiness shape the foundation adopts, and it is the direct
counter-example to ERP's `/health`.

---

## 5. `dotmac_erp` — the production-used preflight, backup and static source

- **Migration-role preflight** (`scripts/deploy.sh:165-194`):
  `bootstrap_database_roles.py --verify-only`, refusing with "DEPLOY STOPPED:
  the migration identity, role posture, or database ownership contract is
  unsatisfied". The script documents "verify-only, never elevate" on the deploy
  path, never sets a password, never grants object privileges
  (`scripts/bootstrap_database_roles.py:22-29`).
- **Backup before migrate** (`scripts/deploy.sh:126-131`): `pg_dump | gzip -9`,
  `rclone copy` to a remote, retention enforced against the *remote* listing
  (`scripts/backup_erp_db.sh:41-63`). The script sets `set -euo pipefail`, so a
  failing `pg_dump` does fail the backup — what it does not establish is that
  the archive is intact or restorable.
- **Static asset synchronization** (`scripts/sync-static.sh:1-61`): rsync host
  → nginx webroot, plus `docker exec` introspection of `UI_ASSET_DIRECTORY` /
  `UI_ASSET_MOUNT` from the running container and a `docker cp` of packaged UI
  assets, all under `flock -w 60`.

---

## 6. Defects — extracted deliberately as NON-goals

Every row is a behaviour present in a source and deliberately not carried into
the foundation. Recording them is the point: a reader of the dossier can see
what was left behind, which is the only way to tell a faithful extraction from
a partial one.

| # | Defect | Evidence | What the foundation does instead |
|---|---|---|---|
| D1 | Production bind-mounts of `static/`, `templates/`, `gunicorn.conf.py`, an entrypoint script | `dotmac_erp:docker-compose.yml:36-42` | `StaticStrategy` refuses `static = "volume"`; assets are image-baked and promoted with the digest. `refuse_dirty_state` fails the deployment on a source mount. |
| D2 | `/health` returns a hardcoded `{"status":"ok"}` and is used as BOTH the container healthcheck and the deploy gate, while the real `readiness_probe()` is unused | `dotmac_erp:app/main.py:913-916` and `:969-980`; `docker-compose.yml:43-48`; `scripts/deploy.sh:35` | The descriptor declares liveness and readiness separately and refuses a role that points both at the same path. Compose's healthcheck consumes READINESS; liveness becomes a label. |
| D3 | Runtime DSN defaults to the Postgres superuser | `dotmac_erp:.env.example:13-17`; `scripts/backup_erp_db.sh:26-30` | The descriptor refuses a role that names the migration owner material, statically, at parse time. |
| D4 | `CSP_ALLOW_UNSAFE: 'true'`, `OPENBAO_ALLOW_INSECURE: 'true'`, `OPENBAO_TOKEN: ${OPENBAO_TOKEN:-devtoken}` hardcoded in the PRODUCTION service blocks | `dotmac_erp:docker-compose.yml:22,25,80-81,112-113,146-147` | The renderer emits `KEY: ${KEY:?…}` and never a literal value, so a development default cannot be baked into a rendered production asset. |
| D5 | Floating `:latest` as the Compose default; a bare `docker compose up -d` outside the script silently downgraded production by five weeks | `dotmac_erp:docker-compose.yml:3,99,133`; the incident is documented at `scripts/deploy.sh:59-69` | `image.reference` must match `…@sha256:<64 hex>`; a tag does not parse. |
| D6 | Root containers, no `USER`, writable filesystem, and a boot-time `pip install` | `dotmac_erp:Dockerfile`, `Dockerfile.hardened`, `scripts/entrypoint-monitoring.sh:1-6` | The image contract requires a fixed non-root UID/GID, a read-only root and `cap_drop: ALL`, and the audit asserts them against the built artefact. |
| D7 | Fixed `container_name` on every service, which the script itself documents as the reason it cannot do blue/green | `dotmac_erp:docker-compose.yml:5,63,101,135,169,193,219`; `scripts/deploy.sh:38-48` | The renderer emits no `container_name`. |
| D8 | Backups establish COMPLETION and nothing after it. No checksum is recorded at write time, nothing decompresses the archive, and no product has ever restored one. **CORRECTED 2026-08-26** — an earlier revision of this row claimed `pg_dump \| gzip` hides a failing `pg_dump`; it does not, because all three scripts set `set -euo pipefail`, which returns the rightmost non-zero status. The retraction is left visible because the wrong version was cited in a commit message and in Knowledge. | `dotmac_sub:scripts/db_backup.sh`; `dotmac_erp:scripts/backup_erp_db.sh`; `dotmac_starter_mt:scripts/deploy.sh:127-142`; `dotmac_erp:scripts/restore_from_backup.py` exists and nothing schedules it | `BackupDataset.checksum` is required and recorded at WRITE time, `verify` must include `schema` for a Postgres dataset, a full decompression is required, and `restore_proof_max_age_days` makes an unrehearsed restore an alertable condition. |
| D9 | `privileged: true` + `cap_add: [NET_ADMIN]` + `pid: host` on two services for one narrow WireGuard need | `dotmac_sub:docker-compose.yml:56-61`, `:721-727` | Each is a separately declared `SecurityException` with a justification and a named approver, rendered with the justification as a comment above the service. The grant stays; the silence does not. |
| D10 | Nineteen of twenty-two services carry no container healthcheck; the `app` service is one of them | `dotmac_sub:docker-compose.yml` | Every role declaring a readiness probe gets a Compose healthcheck; a role serving ingress must declare one, refused at parse time otherwise. |
| D11 | A declared named volume `dotmac_sub_db_data` that nothing uses, beside a host bind mount that actually holds the data | `dotmac_sub:docker-compose.yml:118` vs `:830-838` | Volumes are derived from the descriptor, so a declared-but-unused one cannot be written. |
| D12 | No CSP header on the canonical edge configuration, which ships every other security header | `dotmac_sub:nginx/selfcare.dotmac.io.conf` | The ingress renderer emits the security-header set including CSP when `security_headers` is on. |
| D13 | The canonical Nginx config REGRESSED the older file's dedicated SSE location; streaming now falls into the generic `/api/` block with `proxy_read_timeout 300s` and buffering on | `dotmac_sub:nginx/selfcare.dotmac.io.conf` vs `deploy/nginx/selfcare.dotmac.io:46-58` | `IngressRoute.sse` is a first-class declaration and the parser refuses `sse = true` with a read timeout below 300s. |
| D14 | The release workflow states it deploys BY DIGEST while the Compose file it deploys reads `${INTEGRATOR_TAG:-latest}`; no digest knob exists | `dotmac_integrator:.github/workflows/release-image.yml:192-198,230` vs `docker-compose.yml:18` | The rendered Compose file carries the digest reference itself. |
| D15 | Compose defaults `ENVIRONMENT: production`, which makes `METRICS_TOKEN` prod-fatal, and the same file never sets it — the shipped defaults refuse to boot | `dotmac_integrator:settings.py:424-433` vs `docker-compose.yml:45-61` | `runtime_materials.names` is the declared set and the renderer emits `${NAME:?…}` for each, so a required material missing from the rendered asset is a render-time fact rather than a boot-time discovery. |
| D16 | No deployment lock in the ported Starter engine — the exact race that caused Sub's 2026-07-12 incident (concurrent `pg_dump`s, load 52 on 16 cores, ten minutes of 502s) | `dotmac_starter_mt:scripts/deploy.sh` (no `flock` in 198 lines) | `acquire_lock` is step 1 of every plan and is not optional. |
| D17 | Host-side deploy scripts drifting from the deployed release; twice the cause of a staging incident | `seabone-staging-dotmac-sub-deploy-landmines` (2026-07-28, 2026-08-04) | The engine ships in a versioned distribution that a product exact-pins, and `render --check` fails when the committed asset does not match the descriptor. |
| D18 | Image retention is the one fail-open step: a retention failure is logged and the deployment still reports healthy | `dotmac_sub:scripts/deploy.sh:831-836` | Kept fail-open deliberately, and stated. Retention is housekeeping; failing a healthy deployment over it would be worse. The difference is that it is now a `notes` entry in the plan rather than an undocumented `|| true`. |

---

## 7. Gaps — behaviour no product has

Recorded because a gap is a finding, and because the foundation must not be
described as extracting something that was never there.

| Gap | Evidence |
|---|---|
| **No product declares a container stop budget.** Zero `stop_grace_period` and zero `stop_signal` across all four repositories' Compose files. Every product relies on Docker's default 10-second SIGTERM-then-SIGKILL. | repository-wide grep, all four repos |
| **Three of four products have no ingress configuration at all.** Only `dotmac_sub` has a real vhost. | `dotmac_erp`, `dotmac_integrator`, `dotmac_starter_mt` |
| **`dotmac_integrator` has no backup of any kind.** | repository-wide `find` |
| **No product has a restore rehearsal.** `dotmac_erp:scripts/restore_from_backup.py` (70 lines) restores; nothing verifies a restore on a schedule or records when one last succeeded. | all four |
| **No product bootstraps its host repeatably.** No Ansible, no cloud-init, in any of the four. Host state is whatever an operator typed. | repository-wide `find` |
| **No product records deployment evidence as a durable artefact on the host.** Sub's evidence is produced upstream in CI and consumed as a gate input; nothing writes what actually ran. | `dotmac_sub:scripts/deploy.sh` |

---

## 8. Two patterns that are already fleet-consistent

Recorded so the descriptor adopts them rather than re-deciding them:

1. **The owner/online credential split is consistent in name and shape across
   three of four products** — `MIGRATION_DATABASE_URL` → `app_admin` for DDL,
   `DATABASE_URL` → `platform_api` / `app_user` for runtime
   (`dotmac_integrator:docs/RUNBOOK.md:9-16`;
   `dotmac_erp:scripts/bootstrap_database_roles.py:1-45`;
   `dotmac_starter_mt:docker-compose.dev.yml:44-46`). ERP's `.env.example`
   superuser default (D3) is a *violation* of a shared pattern, not a fourth
   pattern.
2. **Build once, promote the digest, never rebuild per environment** is already
   implemented by two products.
   `dotmac_sub:.github/workflows/release-candidate.yml:126-141` refuses a
   duplicate rebuild via a `docker manifest inspect` precheck, and
   `release-promotion.yml` promotes the already-built digest;
   `dotmac_integrator:.github/workflows/release-image.yml` builds once, audits,
   gates on approval and republishes the exact audited bytes. The foundation
   generalises what two products already do rather than proposing it.
