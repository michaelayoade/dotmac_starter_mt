# syntax note: CMD uses shell-form so ${APP_PORT} is expanded at container
# start (exec-form CMD ["uvicorn", ..., "--port", "$APP_PORT"] does NOT expand
# env vars — the shell never sees it, so uvicorn would try to bind literal
# "$APP_PORT" and fail). Verified: `docker run -e APP_PORT=8081 ...` binds
# 8081 inside the container (see task-11-report.md for the smoke output).
#
# Multi-stage: the `css-builder` stage runs the Tailwind v4 CLI build
# (package.json's `css:build`) to produce static/css/main.css, which is
# gitignored (build artifact, not source — see .gitignore). The final
# python stage never installs node; it only COPYs the built static/ tree
# (source assets + compiled CSS) and templates/ from css-builder.
#
# `npm ci` (not `npm install`) — requires package-lock.json to be present
# and installs exactly what it specifies, failing loudly if package.json
# and the lockfile have drifted, instead of silently re-resolving/rewriting
# the lockfile the way `npm install` would in a throwaway container.
# Tailwind v4 is CSS-first (static/css/src/main.css's `@theme`/`@source`/
# `@custom-variant`) — there is no tailwind.config.js to COPY in this stage
# any more; see static/css/src/main.css for why the old config.js was
# deleted.
FROM node:20-slim AS css-builder

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

# Templates + static are kernel package data now (Task 1b). css:build reads
# from / writes into the package's static/css, and its `@source` directives
# scan the sibling templates/ and static/js — copy both trees to their package
# path so the relative scans resolve.
COPY packages/dotmac-kernel/src/dotmac_kernel/static ./packages/dotmac-kernel/src/dotmac_kernel/static
COPY packages/dotmac-kernel/src/dotmac_kernel/templates ./packages/dotmac-kernel/src/dotmac_kernel/templates
RUN npm run css:build

FROM python:3.12-slim

ARG POETRY_VERSION=1.8.3
ARG APP_PORT=8000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    APP_PORT=${APP_PORT}

WORKDIR /srv/app

RUN pip install --no-cache-dir poetry==${POETRY_VERSION}
COPY pyproject.toml poetry.lock ./
# The kernel is an editable path dependency (packages/dotmac-kernel); its
# source must be present before `poetry install` so the develop install
# resolves. Copied before the install layer, after the manifests, so the
# dependency cache still keys on pyproject/lock.
COPY packages ./packages
RUN poetry install --only main --no-root --no-interaction

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini VERSION brand.json ./
# templates/ and static/ (js, css sources) arrived under ./packages via the
# `COPY packages` layer above (they are kernel package data). The compiled
# Tailwind CSS is a gitignored build artifact — overlay it from css-builder
# into the package's static tree so the installed kernel serves a real
# stylesheet.
COPY --from=css-builder \
    /build/packages/dotmac-kernel/src/dotmac_kernel/static/css/main.css \
    ./packages/dotmac-kernel/src/dotmac_kernel/static/css/main.css

EXPOSE ${APP_PORT}
CMD uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT}
