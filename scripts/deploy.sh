#!/usr/bin/env bash
# Deploy dotmac_starter_mt from a registry-built image — no host build.
#
# Ported from dotmac_sub's scripts/deploy.sh (the org's infra SoT), trimmed to
# this repo's single-service prod compose (app only — no celery workers, no
# local db container, no image-retention pruning). See
# docs/superpowers/sdd/task-12-report.md for the full port-delta.
#
# Usage:
#   deploy.sh <image-tag>        e.g. deploy.sh sha-abc1234 or deploy.sh 0.4.1
#
# Procedure (per the org's infra safety flow):
#   verify image on registry -> pg_dump backup -> pin APP_IMAGE in .env ->
#   pull -> alembic upgrade heads (one-off container) -> recreate app ->
#   health gate.
#
# Config resolution order: explicit shell environment is overlaid by .env
# (sourced with set -a), then any var still unset falls back to its built-in
# default. The one exception is APP_IMAGE, the deploy target: it is always
# derived from the CLI <image-tag> argument, assigned last, unconditionally —
# never the stale APP_IMAGE= pin that .env sourcing just loaded (.env always
# carries the previous deploy's pin, since compose requires it).
#
# On a failed health gate the previous image is re-pinned and the app service
# is recreated on it. Migrations are NOT reverted automatically — new
# revisions must be backward-compatible with the previous release.
#
# Every environment-specific value is a config knob (env var or .env entry),
# never hardcoded:
#   DEPLOY_DIR             default: repo root (parent of this script's dir)
#   ENV_FILE                default: <DEPLOY_DIR>/.env
#   COMPOSE_FILE_PROD       default: docker-compose.yml
#   IMAGE_NAME              default: dotmac_starter_mt (prefix with a registry,
#                            e.g. ghcr.io/org/dotmac_starter_mt, for real deploys)
#   APP_PORT                default: 8000
#   DEPLOY_HEALTH_HOST      default: 127.0.0.1
#   BACKUP_DIR              default: backups (relative to DEPLOY_DIR)
#   HEALTH_RETRIES          default: 15
#   HEALTH_INTERVAL         default: 2 (seconds)
#   HEALTH_CURL_TIMEOUT     default: 5 (seconds; curl --connect-timeout/--max-time)
#   MIGRATION_DATABASE_URL  required, read from ENV_FILE — no default
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

log() { printf '\n==> %s\n' "$*"; }

usage() {
  cat >&2 <<'USAGE'
Usage: deploy.sh <image-tag>

Deploys IMAGE_NAME:<image-tag> to this host's production compose stack.

Config (env var, or set in ENV_FILE — default <DEPLOY_DIR>/.env):
  IMAGE_NAME              image repo/name, default dotmac_starter_mt
  COMPOSE_FILE_PROD       compose file, default docker-compose.yml
  APP_PORT                default 8000
  DEPLOY_HEALTH_HOST      default 127.0.0.1
  BACKUP_DIR              default backups
  HEALTH_RETRIES          default 15
  HEALTH_INTERVAL         default 2 (seconds)
  HEALTH_CURL_TIMEOUT     default 5 (seconds)
  MIGRATION_DATABASE_URL  required, must be set in .env
USAGE
}

if [[ $# -lt 1 || -z "${1:-}" ]]; then
  usage
  exit 1
fi
TAG="$1"

cd "${DEPLOY_DIR}"

ENV_FILE="${ENV_FILE:-${DEPLOY_DIR}/.env}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE} — deploy.sh requires an environment file with at least" >&2
  echo "APP_IMAGE and MIGRATION_DATABASE_URL set. See .env.example." >&2
  exit 1
fi

# Load .env (APP_IMAGE, MIGRATION_DATABASE_URL, etc.) into this shell's env
# BEFORE computing any derived config below — config-file values must land
# first so the "still unset" defaults that follow don't get clobbered by a
# stale value sourced afterwards. Precedence for everything below is:
# explicit environment > .env > built-in default — EXCEPT APP_IMAGE, which
# is always the CLI TAG regardless of what .env pins (see below).
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

# Defaults for anything still unset after sourcing .env.
: "${COMPOSE_FILE_PROD:=docker-compose.yml}"
: "${IMAGE_NAME:=dotmac_starter_mt}"
: "${APP_PORT:=8000}"
: "${DEPLOY_HEALTH_HOST:=127.0.0.1}"
: "${BACKUP_DIR:=backups}"
: "${HEALTH_RETRIES:=15}"
: "${HEALTH_INTERVAL:=2}"
: "${HEALTH_CURL_TIMEOUT:=5}"

if [[ -z "${MIGRATION_DATABASE_URL:-}" ]]; then
  echo "MIGRATION_DATABASE_URL is not set in ${ENV_FILE} — required for the" >&2
  echo "pre-migration backup and for the migration container. Aborting." >&2
  exit 1
fi

PREV_IMAGE="$(grep -E '^APP_IMAGE=' "${ENV_FILE}" | cut -d= -f2- || true)"

# APP_IMAGE is the deploy target: it must ALWAYS be the CLI-supplied TAG,
# never whatever stale value .env sourcing just loaded (.env always has an
# old APP_IMAGE= pin from the previous deploy, since compose requires it).
# Assigned last, unconditionally — this is the one exception to the
# .env-wins precedence above.
APP_IMAGE="${IMAGE_NAME}:${TAG}"

HEALTH_URL="http://${DEPLOY_HEALTH_HOST}:${APP_PORT}/health"
COMPOSE=(docker compose -f "${COMPOSE_FILE_PROD}")

log "Deploying ${APP_IMAGE} (currently pinned: ${PREV_IMAGE:-none})"

log "Verifying image exists on registry"
docker manifest inspect "${APP_IMAGE}" >/dev/null

log "Backing up database before migrations"
mkdir -p "${BACKUP_DIR}"
STAMP="$(date +%Y%m%d%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/${STAMP}.sql.gz"
# pg_dump/libpq don't understand the SQLAlchemy "+driver" URL suffix
# (e.g. postgresql+psycopg://) — strip it down to a plain postgresql:// URI.
PG_DUMP_URL="$(printf '%s' "${MIGRATION_DATABASE_URL}" | sed -E 's#^postgresql\+[A-Za-z0-9_]+://#postgresql://#')"
# pipefail is already on globally (set -euo pipefail above) — no need to
# toggle it locally here.
pg_dump "${PG_DUMP_URL}" | gzip > "${BACKUP_FILE}"
if [[ ! -s "${BACKUP_FILE}" ]]; then
  echo "Backup produced an empty file — aborting" >&2
  rm -f "${BACKUP_FILE}"
  exit 1
fi
log "Backup complete: ${BACKUP_FILE}"

repin_prev() {
  if [[ -n "${PREV_IMAGE}" ]]; then
    sed -i "s|^APP_IMAGE=.*|APP_IMAGE=${PREV_IMAGE}|" "${ENV_FILE}"
  fi
}
trap 'repin_prev; echo "Deploy FAILED — APP_IMAGE restored to ${PREV_IMAGE:-none} (running containers untouched)" >&2' ERR

log "Pinning APP_IMAGE=${APP_IMAGE} in ${ENV_FILE}"
if grep -q '^APP_IMAGE=' "${ENV_FILE}"; then
  sed -i "s|^APP_IMAGE=.*|APP_IMAGE=${APP_IMAGE}|" "${ENV_FILE}"
else
  printf 'APP_IMAGE=%s\n' "${APP_IMAGE}" >> "${ENV_FILE}"
fi

if ! grep -qFx "APP_IMAGE=${APP_IMAGE}" "${ENV_FILE}"; then
  echo "Pin verification failed — ${ENV_FILE} does not contain APP_IMAGE=${APP_IMAGE}" >&2
  echo "after the pin. Aborting before touching running containers. Restoring previous pin." >&2
  repin_prev
  exit 1
fi

log "Pulling image"
"${COMPOSE[@]}" pull app

log "Applying migrations (alembic upgrade heads)"
"${COMPOSE[@]}" run --rm -e DATABASE_URL="${MIGRATION_DATABASE_URL}" app alembic upgrade heads

log "Recreating app service"
"${COMPOSE[@]}" up -d app

log "Waiting for app health at ${HEALTH_URL} (${HEALTH_RETRIES} retries, ${HEALTH_INTERVAL}s apart)"
healthy=0
for ((i = 1; i <= HEALTH_RETRIES; i++)); do
  if curl -fsS --connect-timeout "${HEALTH_CURL_TIMEOUT}" --max-time "${HEALTH_CURL_TIMEOUT}" -o /dev/null "${HEALTH_URL}" 2>/dev/null; then
    healthy=1
    break
  fi
  sleep "${HEALTH_INTERVAL}"
done

if ((healthy == 0)); then
  trap - ERR
  log "Health gate FAILED (${HEALTH_URL} never became healthy) — rolling back to ${PREV_IMAGE:-none}"
  if [[ -n "${PREV_IMAGE}" ]]; then
    repin_prev
    "${COMPOSE[@]}" up -d app
    log "Rolled back to ${PREV_IMAGE}. NOTE: migrations from ${TAG} were NOT reverted."
  else
    log "No previous image recorded — cannot auto-roll-back. Investigate the app container."
  fi
  exit 1
fi

trap - ERR
log "Deployed ${TAG} successfully (was ${PREV_IMAGE:-none})"
