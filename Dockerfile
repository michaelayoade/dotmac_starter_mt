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
FROM node:20-slim AS css-builder

WORKDIR /build

COPY package.json ./
RUN npm install

COPY tailwind.config.js ./
COPY static ./static
COPY templates ./templates
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
RUN poetry install --only main --no-root --no-interaction

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini VERSION ./
COPY templates ./templates
COPY --from=css-builder /build/static ./static

EXPOSE ${APP_PORT}
CMD uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT}
