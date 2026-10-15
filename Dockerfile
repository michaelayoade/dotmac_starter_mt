# syntax note: CMD uses shell-form so ${APP_PORT} is expanded at container
# start (exec-form CMD ["python", ..., "--port", "$APP_PORT"] does NOT expand
# env vars — the shell never sees it, so the launcher would be handed the
# literal "$APP_PORT" and fail). Verified: `docker run -e APP_PORT=8081 ...`
# binds 8081 inside the container (see task-11-report.md for the smoke output).
#
# The command is the product's own launcher (`app/runtime.py`), not uvicorn
# with a spelled-out `--host 0.0.0.0`. The bind address is an implementation
# detail of the image and belongs to code the image digest binds; the
# deployment descriptor names the launcher and the port. See app/runtime.py.
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

# Rust exists only in the dependency build stage. Keep its Debian release
# aligned with Python so the resulting extension uses the final image's libc.
FROM rust:1.90.0-slim-bookworm AS rust-toolchain

FROM python:3.12-slim-bookworm AS dependency-builder

COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    RUSTUP_TOOLCHAIN=1.90.0 \
    PATH=/usr/local/cargo/bin:$PATH
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

ARG APP_PORT=8000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    APP_PORT=${APP_PORT}

WORKDIR /srv/app

COPY pyproject.toml poetry.lock ./
COPY scripts/check_poetry_toolchain.py ./scripts/check_poetry_toolchain.py
COPY .github/bootstrap/poetry-requirements-py312.txt ./poetry-requirements.txt
# Use the same hash-locked Poetry as CI. The contract check runs before and
# after installation: first to reject drift among the project, lockfile and
# bootstrap; then to prove PATH resolves to the version that was requested.
RUN python scripts/check_poetry_toolchain.py --bootstrap poetry-requirements.txt \
    && python -m pip install --disable-pip-version-check --no-cache-dir \
         --require-hashes --only-binary=:all: -r poetry-requirements.txt \
    && python scripts/check_poetry_toolchain.py \
         --bootstrap poetry-requirements.txt --active
# The kernel is an editable path dependency (packages/dotmac-kernel); its
# source must be present before `poetry install` so the develop install
# resolves. Copied before the install layer, after the manifests, so the
# dependency cache still keys on pyproject/lock.
COPY packages ./packages
RUN poetry install --only main --no-root --no-interaction

# The final image receives installed dependencies and editable module sources,
# never cargo, rustc, gcc or the build-stage OS packages.
FROM python:3.12-slim-bookworm
ARG APP_PORT=8000
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=${APP_PORT}
WORKDIR /srv/app
COPY --from=dependency-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=dependency-builder /usr/local/bin /usr/local/bin
COPY --from=dependency-builder /srv/app/packages ./packages

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
CMD python -m app.runtime --port ${APP_PORT}
