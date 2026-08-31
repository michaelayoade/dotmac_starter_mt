#!/usr/bin/env bash
#
# Empty-consumer WHEEL proof (K4) + release smoke modes (K6).
#
# Proves the kernel works when INSTALLED AS A WHEEL and consumed by an EXTERNAL
# application — not run from the workspace source tree. The milestone this gates
# is "the kernel is usable without copying source": a consumer that imports ONLY
# the public kernel surface (`from dotmac_kernel import create_app,
# ProductAssemblySpec`) can build and boot an app, serve the DB-free `/health`
# liveness endpoint, and resolve the kernel's packaged data (templates, static,
# migrations) — all from site-packages, with the repo `app/` NEVER on sys.path.
#
# MODES (one script, no parallel copy — R0 amendment):
#   (default)              SOURCE mode. Build the wheel from source here
#                          (poetry build -f wheel) and smoke it. The compiled
#                          static/css/main.css is a gitignored build artifact, so
#                          source mode TOLERATES its absence. This is what the
#                          `consumer-boot` CI gate on main runs — behavior
#                          unchanged.
#   --wheel <dist-dir>     RELEASE-ARTIFACT mode. Do NOT build; smoke an existing
#                          wheel in <dist-dir> (the release `build` job runs
#                          `npm run css:build` then `poetry build` first). Release
#                          mode REQUIRES static/css/main.css.
#   --from-pypi <version>  FROM-REGISTRY mode. `pip install --pre
#                          dotmac-kernel==<version>` from PyPI into a clean venv
#                          and smoke the PUBLISHED artifact. Release mode REQUIRES
#                          static/css/main.css.
#
# CI-friendly and deterministic: no Postgres, no port binding (TestClient runs
# the ASGI app in-process), temp workspace removed on exit.
#
set -euo pipefail

# ── Mode parsing ─────────────────────────────────────────────────────────────
MODE="source"
DIST_DIR=""
PYPI_VERSION=""
case "${1:-}" in
  --wheel)
    MODE="wheel"
    DIST_DIR="${2:?usage: consumer_boot_check.sh --wheel <dist-dir>}"
    ;;
  --from-pypi)
    MODE="pypi"
    PYPI_VERSION="${2:?usage: consumer_boot_check.sh --from-pypi <version>}"
    ;;
  --from-registry)
    # Install from a private index (Forgejo): KERNEL_INDEX_URL carries the
    # authenticated simple-index URL. Same smoke, different source.
    MODE="registry"
    PYPI_VERSION="${2:?usage: consumer_boot_check.sh --from-registry <version>}"
    : "${KERNEL_INDEX_URL:?--from-registry requires KERNEL_INDEX_URL (authenticated simple index)}"
    ;;
  "")
    MODE="source"
    ;;
  *)
    echo "unknown argument: ${1}" >&2
    echo "usage: consumer_boot_check.sh [--wheel <dist-dir> | --from-pypi <version> | --from-registry <version>]" >&2
    exit 2
    ;;
esac
# Release modes (a real built/published artifact) require the compiled web asset;
# source mode tolerates its absence (the gitignored build artifact isn't built).
REQUIRE_COMPILED_CSS=0
[ "$MODE" = "source" ] || REQUIRE_COMPILED_CSS=1

# ── Locations ───────────────────────────────────────────────────────────────
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
KERNEL_DIR="$REPO_ROOT/packages/dotmac-kernel"

# ── Overridable knobs (everything-by-config; documented defaults) ───────────
# CONSUMER_PYTHON pins the interpreter; default prefers python3.12 (the kernel's
# floor) and falls back to python3 so the script runs on any 3.12+ runner.
if [ -n "${CONSUMER_PYTHON:-}" ]; then
  PYTHON="$CONSUMER_PYTHON"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON="python3.12"
else
  PYTHON="python3"
fi
# Well-formed but deliberately unreachable — proves /health never touches the DB.
CONSUMER_DB_URL="${CONSUMER_DB_URL:-postgresql+psycopg://x:x@127.0.0.1:59999/x}"
# The wheel consumer is also the declared FastAPI ceiling proof. The floor job
# covers 0.111; this exact pin covers 0.140's lazy included-router shape.
export CONSUMER_FASTAPI="${CONSUMER_FASTAPI:-0.140.13}"

# ── Temp workspace (venv + consumer app), removed on exit ───────────────────
WORKDIR=$(mktemp -d)
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "==> [1/6] Resolve the kernel wheel (mode: ${MODE})"
INSTALL_SPEC=""       # what pip installs to provide dotmac_kernel
case "$MODE" in
  source)
    cd "$KERNEL_DIR"
    rm -rf dist
    poetry build -f wheel
    WHEEL=$(ls "$KERNEL_DIR"/dist/*.whl | head -n1)
    INSTALL_SPEC="$WHEEL"
    echo "    built wheel: $WHEEL"
    ;;
  wheel)
    # Resolve <dist-dir> relative to CWD or repo root.
    [ -d "$DIST_DIR" ] || DIST_DIR="$REPO_ROOT/$DIST_DIR"
    WHEEL=$(ls "$DIST_DIR"/*.whl 2>/dev/null | head -n1)
    [ -n "$WHEEL" ] || { echo "no wheel found in $DIST_DIR" >&2; exit 1; }
    INSTALL_SPEC="$WHEEL"
    echo "    release wheel: $WHEEL"
    ;;
  pypi)
    INSTALL_SPEC="dotmac-kernel==${PYPI_VERSION}"
    echo "    from PyPI: --pre ${INSTALL_SPEC}"
    ;;
  registry)
    INSTALL_SPEC="dotmac-kernel==${PYPI_VERSION}"
    echo "    from Forgejo index: --pre ${INSTALL_SPEC} (KERNEL_INDEX_URL)"
    ;;
esac

echo "==> [2/6] Create a CLEAN consumer virtualenv (isolated from the repo venv)"
VENV="$WORKDIR/venv"
"$PYTHON" -m venv "$VENV"
VPY="$VENV/bin/python"
"$VPY" -m pip install --quiet --upgrade pip

echo "==> [3/6] Install the kernel + its declared deps into the clean venv"
# The wheel's METADATA carries the kernel's declared dependency closure
# (fastapi, sqlalchemy, pydantic[email], pydantic-settings, jinja2, argon2-cffi) —
# pip resolves those from the wheel/release alone, which is the "dep set is
# complete" proof. Two runtime pieces are DELIBERATELY excluded from kernel deps
# as assembly/deploy concerns (see packages/dotmac-kernel/pyproject.toml) and are
# supplied HERE by the consumer, exactly as a real deployment would:
#   - psycopg[binary] : the DB driver the postgresql+psycopg:// URL names.
#                       SQLAlchemy imports it eagerly at create_engine() time
#                       (dotmac_kernel.db builds the engine at import), so
#                       building the app requires a driver even though /health
#                       never connects.
#   - httpx           : the transport Starlette's TestClient uses for the
#                       in-process /health probe.
# The workspace app/ is NOT installed and is NOT on sys.path — the whole point:
# the consumer sees only the public kernel surface from site-packages.
# NOTE: no global `--pre`. The alpha installs because INSTALL_SPEC is an EXACT
# pre-release pin (`dotmac-kernel==0.1.0aN`), which pip honors on its own; a
# global `--pre` would ALSO pull pre-releases of the transitive tools (it once
# pulled a pre-release httpx that dropped `httpx.BaseTransport` and broke
# Starlette's TestClient). httpx is pinned to the TestClient-compatible range
# (matches the kernel's `testing` extra) for the same reason.
_HTTPX='httpx>=0.27,<1'
_FASTAPI="fastapi==${CONSUMER_FASTAPI}"
if [ "$MODE" = "registry" ]; then
  # Install from the PRIVATE Forgejo simple index (auth carried in
  # KERNEL_INDEX_URL), with PyPI as an extra index ONLY for the public transitive
  # deps (fastapi, sqlalchemy, psycopg, httpx, …) that Forgejo does not host.
  # NOTE: real consumers (Vendor CP, Sub) should guard against dependency
  # confusion — pin dotmac-kernel with a hash and/or resolve it from Forgejo
  # alone. This is the release VERIFY smoke of an internal alpha.
  "$VPY" -m pip install --quiet --index-url "$KERNEL_INDEX_URL" \
    --extra-index-url "${PUBLIC_INDEX_URL:-https://pypi.org/simple}" \
    "$INSTALL_SPEC" "psycopg[binary]" "$_HTTPX" "$_FASTAPI"
else
  "$VPY" -m pip install --quiet "$INSTALL_SPEC" "psycopg[binary]" "$_HTTPX" "$_FASTAPI"
fi

echo "==> [4/6] Write the minimal EXTERNAL consumer app (public names only)"
cat > "$WORKDIR/consumer_main.py" <<'PY'
"""Minimal external consumer of dotmac-kernel.

Imports ONLY the public kernel surface — no `app.*`, nothing from the workspace.
An empty assembly (no feature modules) boots to just the kernel surface.
"""

from dotmac_kernel import (
    ProductAssemblySpec,
    create_app,
    environment_api_documentation_policy,
)

# An external consumer must declare its documentation exposure: the kernel
# refuses to build without one. Resolved from ENVIRONMENT, failing closed to
# production -- so this also proves the helper is on the public surface.
app = create_app(
    ProductAssemblySpec(
        name="ext-consumer",
        modules=(),
        api_documentation=environment_api_documentation_policy(),
    )
)
PY

echo "==> [5/6] Write the boot + package-data proof runner"
cat > "$WORKDIR/consumer_check.py" <<'PY'
"""Boot the external consumer app from the installed kernel and assert:
  1. GET /health == 200 (DB-free liveness, unreachable DATABASE_URL).
  2. The kernel and its packaged data resolve from site-packages (the installed
     WHEEL/release), NOT from the repo source tree.
  3. Templates / static (css source + js + fonts, and — in release mode — the
     COMPILED css/main.css) / migrations 0001..0007 are present as package data.
"""

import os
import sys
from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

import dotmac_kernel
import dotmac_kernel.migrations as kmig
import dotmac_kernel.templating as ktpl
from dotmac_kernel import (
    FeatureManifest,
    ProductAssemblySpec,
    create_app,
    environment_api_documentation_policy,
)
from dotmac_kernel.capabilities import UndeclaredCapabilityError
from dotmac_kernel.deps import require_capability, require_permission
from dotmac_kernel.permissions import UndeclaredPermissionError
from consumer_main import app

repo_root = Path(os.environ["REPO_ROOT"]).resolve()
site_root = Path(sys.prefix).resolve()  # the clean venv
require_compiled_css = os.environ.get("REQUIRE_COMPILED_CSS") == "1"


def installed_from_wheel(p: Path) -> None:
    p = p.resolve()
    assert site_root in p.parents or p == site_root, f"{p} is not under the venv {site_root}"
    assert repo_root not in p.parents, f"{p} leaked from the repo source tree {repo_root}"


# ── 1. kernel itself came from the installed wheel, not the source tree ──────
installed_from_wheel(Path(dotmac_kernel.__file__))
print(f"    dotmac_kernel      -> {Path(dotmac_kernel.__file__).parent}")
print(f"    version            -> {dotmac_kernel.__version__}")

# ── 2. DB-free liveness: /health == 200 under an unreachable DATABASE_URL ────
print(f"    DATABASE_URL       -> {os.environ.get('DATABASE_URL')}")
with TestClient(app) as client:
    resp = client.get("/health")
assert resp.status_code == 200, f"/health returned {resp.status_code}"
assert resp.json() == {"status": "ok"}, resp.text
print(f"    GET /health        -> {resp.status_code} {resp.json()}")

# ── 2b. FastAPI ceiling: lazy included routers cannot hide route guards ─────
expected_fastapi = os.environ["CONSUMER_FASTAPI"]
assert version("fastapi") == expected_fastapi, (
    f"FastAPI ceiling drift: expected {expected_fastapi}, got {version('fastapi')}"
)
for kind, guard, expected_error in (
    ("permission", require_permission("ghost.read"), UndeclaredPermissionError),
    ("capability", require_capability("ghost.use"), UndeclaredCapabilityError),
):
    router = APIRouter(prefix=f"/{kind}")

    @router.get("/probe", dependencies=[Depends(guard)])
    def guarded_probe() -> dict[str, bool]:
        return {"ok": True}

    try:
        create_app(
            ProductAssemblySpec(
                name=f"lazy-{kind}-probe",
                modules=(FeatureManifest(name=kind, routers=(router,)),),
                # Declared so this probe still fails on the DECLARATION
                # validation it is testing, not on the documentation policy.
                api_documentation=environment_api_documentation_policy(),
            )
        )
    except expected_error:
        pass
    else:
        raise AssertionError(
            f"FastAPI {expected_fastapi} lazy route bypassed {kind} declaration validation"
        )
print(f"    fastapi {expected_fastapi} -> lazy permission/capability routes fail closed")

# ── 3. packaged TEMPLATES resolve from the wheel ─────────────────────────────
templates_dir = ktpl.TEMPLATES_DIR
installed_from_wheel(templates_dir)
assert (templates_dir / "base.html").is_file(), "templates/base.html missing from wheel"
print(f"    templates/base.html-> present ({templates_dir})")

# ── 4. packaged STATIC resolves from the wheel ───────────────────────────────
static_dir = ktpl.STATIC_DIR
installed_from_wheel(static_dir)
# The css SOURCE (Tailwind input), js, and fonts are ALWAYS shipped.
required = [
    "css/src/main.css",
    "js/htmx.min.js",
    "js/csrf.js",
    "fonts/fonts.css",
    "fonts/Outfit-400.woff2",
    "fonts/PlusJakartaSans-400.woff2",
]
for rel in required:
    assert (static_dir / rel).is_file(), f"static/{rel} missing from wheel"
print(f"    static (css-src/js/fonts) -> {len(required)} required files present ({static_dir})")

# The COMPILED web asset: REQUIRED in release mode (a published/built web kernel
# must ship it), TOLERATED in source mode (gitignored build artifact not built).
compiled = static_dir / "css" / "main.css"
if require_compiled_css:
    assert compiled.is_file(), (
        "static/css/main.css MISSING — a release build must run `npm run css:build` "
        "before `poetry build` so the compiled web asset ships"
    )
    print(f"    static/css/main.css (compiled) -> present [REQUIRED in release mode]")
else:
    print(f"    static/css/main.css (compiled build artifact) -> "
          f"{'present' if compiled.is_file() else 'absent (tolerated in source mode)'}")

# ── 5. packaged MIGRATIONS 0001..0007 resolve from the wheel ─────────────────
versions_dir = kmig.versions_dir()
installed_from_wheel(versions_dir)
revs = sorted(p.name for p in versions_dir.glob("*.py") if p.name != "__init__.py")
for n in ("0001", "0002", "0003", "0004", "0005", "0006", "0007"):
    assert any(f"_{n}_" in r for r in revs), f"migration {n} missing from wheel"
print(f"    migrations 0001..0007 -> {len(revs)} revisions present ({versions_dir})")

print("\nOK — kernel boots and resolves its package data from the installed kernel.")
PY

echo "==> [6/6] Boot the consumer from the installed kernel and run the proof"
cd "$WORKDIR"
DATABASE_URL="$CONSUMER_DB_URL" REPO_ROOT="$REPO_ROOT" \
  REQUIRE_COMPILED_CSS="$REQUIRE_COMPILED_CSS" "$VPY" consumer_check.py

echo
echo "PASS — empty-consumer ${MODE} proof succeeded."
