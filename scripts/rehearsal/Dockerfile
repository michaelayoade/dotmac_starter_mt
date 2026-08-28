# The disposable rehearsal product's image — see scripts/rehearsal/README.md
# and docs/inventories/deployment-foundation-rehearsal.md for what runs it.
#
# This is deliberately built to the SAME hardened contract
# `image/audit.py` checks against every real product image, so that
# `dotmac-deploy image-audit` genuinely passes here rather than being told
# to skip a stub. Every rule below exists because a real product image once
# failed it (see that module's docstring for the finding each rule prevents):
#
#   - fixed, numeric, non-root UID:GID (10001:10001, the fleet convention)
#   - no ENTRYPOINT/CMD that migrates on boot
#   - no build/test tooling or build-context paths in the runtime layers
#   - no credential passed as a `--build-arg` (this file takes none)
#   - the three required org.opencontainers.image.* labels
#   - exec-form only — there is no shell-form CMD/ENTRYPOINT to accidentally
#     interpose a shell between PID 1 and SIGTERM
#
# `python:3.13-slim` ships no ENTRYPOINT/CMD of its own worth objecting to
# (a bare `["python3"]`, three characters short of the shell-form check and
# carrying no migration marker), and this file does not add one: every
# compose service in `scripts/rehearsal/product.toml` supplies its own full
# `command:` array — `["python", "/app/app.py", "--serve"]` for the role,
# `["python", "/app/app.py", "--migrate"]` for the one-shot migration
# service — the same convention `render/compose.py` already assumes for
# every product built from this facility (see `deploy/product.toml`'s own
# `roles.command`).

FROM python:3.13-slim

# `postgresql-client` is a genuine runtime dependency — the migration and
# head-reading modes of app.py shell out to `psql` — not build tooling.
# Nothing here compiles anything, so there is no builder stage to leak: a
# single stage already has no pytest/poetry/gcc/make/git/npm/node anywhere
# in it, which `dotmac-deploy image-audit`'s filesystem-listing rule proves
# rather than assumes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# A fixed numeric UID:GID, never a name — a named user resolves against the
# image's OWN /etc/passwd, so an entry dropped by a later layer silently
# restores root (image/audit.py's rule_non_root docstring).
RUN groupadd --gid 10001 rehearsal \
    && useradd --uid 10001 --gid 10001 --no-create-home \
       --shell /usr/sbin/nologin rehearsal \
    && mkdir -p /app \
    && chown -R 10001:10001 /app

# Revision metadata changes on every exact-SHA run. Declare it only after the
# dependency and user layers so a new commit does not invalidate apt and force
# the same runtime dependencies to be downloaded again. The final image still
# carries the exact labels the audit verifies; only cache authority changes.
ARG REVISION=0000000000000000000000000000000000000000
ARG SOURCE=https://github.com/michaelayoade/dotmac_starter_mt
ARG VERSION=0.0.0-rehearsal

LABEL org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="${SOURCE}" \
      org.opencontainers.image.version="${VERSION}"

WORKDIR /app
COPY app.py /app/app.py

USER 10001:10001
EXPOSE 8000
