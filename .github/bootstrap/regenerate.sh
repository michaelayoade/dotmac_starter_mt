#!/usr/bin/env bash
# Regenerate the hash-locked Poetry bootstrap — the ONE command.
#
#   .github/bootstrap/regenerate.sh [poetry==<version>]
#
# Run this whenever the pinned Poetry version moves. A stale lock fails closed
# (loudly, in every job at once) rather than silently installing something
# unpinned — the intended trade, but it makes bumping Poetry a deliberate
# two-step: change the version here, commit the regenerated files.
#
# ONE FILE PER PYTHON MINOR. pip resolves a different dependency SET per
# interpreter: on 3.11 Poetry additionally needs backports.tarfile,
# importlib_metadata and zipp (49 packages vs 46). Reusing one interpreter's
# lock on another fails --require-hashes with a missing requirement, which
# would break the kernel-floors matrix that deliberately runs both.
#
# WHY A CONTAINER, and why linux/amd64:
#
# pip evaluates environment markers against the RUNNING interpreter. Its
# --platform/--python-version flags select wheel TAGS only; they do not change
# marker evaluation. Resolving on macOS therefore produces a genuinely
# different dependency SET than CI needs — it pulls `xattr`
# (sys_platform == "darwin") and omits `SecretStorage`/`jeepney`/`cryptography`
# (sys_platform == "linux"), which keyring needs. That bootstrap would install
# and then misbehave, so the resolution has to happen on the target platform.
#
# Each file records the sha256 of EVERY distribution PyPI publishes for the
# resolved version, not only the wheel that run selected: a runner image with a
# different glibc or a newer manylinux tag may legitimately pick a different
# wheel of the same version, and failing the hash check for that would be a
# false alarm rather than a security signal.
set -euo pipefail

POETRY_PIN="${1:-poetry==2.4.1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Must cover every interpreter any workflow sets up — see the kernel-floors
# matrix in .github/workflows/ci.yml.
PYTHON_MINORS=("3.11" "3.12")

if ! docker info >/dev/null 2>&1; then
  echo "error: this needs a usable Docker daemon (linux/amd64 containers)." >&2
  echo "       No local Docker? Run it on a throwaway Linux host — NOT on a" >&2
  echo "       production box and NOT on a self-hosted CI runner." >&2
  exit 1
fi

for minor in "${PYTHON_MINORS[@]}"; do
  out="${HERE}/poetry-requirements-py${minor/./}.txt"
  echo "resolving ${POETRY_PIN} for Python ${minor}…"
  docker run --rm -i --platform linux/amd64 "python:${minor}-slim" \
    python - "${POETRY_PIN}" <"${HERE}/generate.py" >"${out}.tmp"
  mv "${out}.tmp" "${out}"
  echo "  wrote ${out} ($(grep -c '^[a-zA-Z]' "${out}") pinned packages)"
done

echo
echo "Verify before committing — for EACH interpreter, INTO A FRESH VENV:"
echo
echo "  The venv is not a nicety. Installing into the image's site-packages"
echo "  lets anything already present satisfy a requirement, so a lock with a"
echo "  MISSING package still appears to install — which is exactly how a lock"
echo "  omitting 'packaging' passed local verification and then failed on a CI"
echo "  runner, where nothing is pre-installed."
echo
for minor in "${PYTHON_MINORS[@]}"; do
  out="${HERE}/poetry-requirements-py${minor/./}.txt"
  echo "  docker run --rm -i --platform linux/amd64 -v ${out}:/r.txt:ro python:${minor}-slim \\"
  echo "    sh -c 'python -m venv /v && /v/bin/pip install -q --require-hashes \\"
  echo "           --only-binary=:all: -r /r.txt && /v/bin/poetry --version'"
done
