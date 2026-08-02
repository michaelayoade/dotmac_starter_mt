#!/usr/bin/env bash
# Prove the kernel's declared dependency FLOOR is real.
#
# Widening `fastapi`/`pydantic` floors is a SUPPORT CLAIM: it says the kernel
# works on those versions, not merely that pip can resolve them. Without this
# check the widening would move a clean resolve-time failure into a runtime one
# for a consumer sitting at the floor — the products this widening exists to
# unblock (dotmac_sub, dotmac_erp at fastapi 0.111.0 / pydantic 2.7.4) are
# exactly the ones that would hit it.
#
# So: build the wheel, install it into a CLEAN venv with the floor versions
# PINNED EXACTLY, and exercise the supported surface a product assembly is
# permitted to import. Anything that needs a newer runtime fails here, loudly,
# before release.
set -euo pipefail

FLOOR_FASTAPI="${FLOOR_FASTAPI:-0.111.0}"
FLOOR_PYDANTIC="${FLOOR_PYDANTIC:-2.7.4}"
FLOOR_PYDANTIC_SETTINGS="${FLOOR_PYDANTIC_SETTINGS:-2.2.1}"
# dotmac_sub's exact production pin — the reason the floor is >=42.
FLOOR_CRYPTOGRAPHY="${FLOOR_CRYPTOGRAPHY:-42.0.8}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
kernel_dir="${repo_root}/packages/dotmac-kernel"
workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

echo "==> Building the kernel wheel"
(cd "${kernel_dir}" && poetry build -f wheel -o "${workdir}/dist" >/dev/null)
wheel="$(ls "${workdir}"/dist/*.whl)"
echo "    ${wheel##*/}"

echo "==> Clean venv with floors pinned exactly"
# The floor pydantic (2.7.4) predates cpython 3.13 wheels and its Rust build
# refuses 3.13, so pin the interpreter to the version CI actually uses rather
# than whatever `python3` happens to be locally.
FLOOR_PYTHON="${FLOOR_PYTHON:-python3.12}"
if ! command -v "${FLOOR_PYTHON}" >/dev/null 2>&1; then
  echo "!! ${FLOOR_PYTHON} not found. The floor pydantic has no wheels for" >&2
  echo "   newer interpreters; install it or set FLOOR_PYTHON." >&2
  exit 1
fi
"${FLOOR_PYTHON}" -m venv "${workdir}/venv"
# shellcheck disable=SC1091
source "${workdir}/venv/bin/activate"
pip install --quiet --upgrade pip
# Pin the floors FIRST so the kernel install cannot silently upgrade past them;
# a resolver that pulled a newer fastapi would make this check vacuous.
pip install --quiet \
  "fastapi==${FLOOR_FASTAPI}" \
  "pydantic==${FLOOR_PYDANTIC}" \
  "pydantic-settings==${FLOOR_PYDANTIC_SETTINGS}" \
  "cryptography==${FLOOR_CRYPTOGRAPHY}"
# BOTH extras: the products' allowlists include `dotmac_kernel.testing`, so a
# floor proven without it would not cover what they actually import.
pip install --quiet "${wheel}[testing,licensing]"

echo "==> Verifying the floors actually held"
python - <<'PY'
import os
import sys
from importlib.metadata import version

# ALL pinned floors, not just the two headline ones: pydantic-settings and
# cryptography are equally capable of silently resolving forward and making
# this check vacuous.
expected = {
    "fastapi": os.environ.get("FLOOR_FASTAPI", "0.111.0"),
    "pydantic": os.environ.get("FLOOR_PYDANTIC", "2.7.4"),
    "pydantic-settings": os.environ.get("FLOOR_PYDANTIC_SETTINGS", "2.2.1"),
    "cryptography": os.environ.get("FLOOR_CRYPTOGRAPHY", "42.0.8"),
}
drift = {
    name: (version(name), pin)
    for name, pin in expected.items()
    if version(name) != pin
}
if drift:
    raise SystemExit(
        f"floor drift {drift} — the resolver moved past a pinned floor, so "
        "this check would prove nothing"
    )
print("    " + ", ".join(f"{n} {v}" for n, v in expected.items()))
print(f"    python {sys.version.split()[0]}")
PY

echo "==> Exercising the supported surface at the floor (no DATABASE_URL)"
env -u DATABASE_URL -u PLATFORM_DATABASE_URL python "${repo_root}/scripts/floor/probe.py"

echo "==> Floor check passed"
