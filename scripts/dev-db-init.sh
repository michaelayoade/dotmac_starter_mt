#!/bin/sh
# Dev Postgres init hook (control-plane security Task 3) — mounted into the
# dev container's /docker-entrypoint-initdb.d/ by docker-compose.dev.yml, so
# it runs ONCE, when the data volume is first created (recreate the volume
# to re-run: `docker compose -f docker-compose.dev.yml down -v`).
#
# Creates the SAME three application roles production uses — app_user
# (RLS-enforced request role), platform_api (platform routes, no RLS
# bypass), app_admin (migrations, BYPASSRLS) — so `make dev` runs with RLS
# ACTIVE instead of as superuser. The initial Alembic migration's own
# CREATE ROLE block is IF NOT EXISTS, so it simply skips these.
#
# A .sh hook rather than a .sql one because the postgres entrypoint only
# expands environment variables (DEV_*_PASSWORD, overridable via compose)
# in shell scripts — .sql files are piped to psql verbatim.
#
# Passwords are dev-only defaults for a localhost-bound throwaway database;
# production role credentials are provisioned outside this repo (see
# .env.example).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_admin') THEN
            CREATE ROLE app_admin LOGIN BYPASSRLS PASSWORD '${DEV_APP_ADMIN_PASSWORD:-app_admin}';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
            CREATE ROLE app_user LOGIN PASSWORD '${DEV_APP_USER_PASSWORD:-app_user}';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'platform_api') THEN
            CREATE ROLE platform_api LOGIN PASSWORD '${DEV_PLATFORM_API_PASSWORD:-platform_api}';
        END IF;
    END
    \$\$;

    -- app_admin runs migrations: it must CREATE objects in public AND issue
    -- the migrations' own "GRANT USAGE ON SCHEMA public TO app_user,
    -- platform_api" — granting on a schema requires ownership (a plain
    -- GRANT ALL confers no grant option), so make it the schema owner.
    -- Postgres 15+ no longer gives every role CREATE on public anyway.
    ALTER SCHEMA public OWNER TO app_admin;
EOSQL
