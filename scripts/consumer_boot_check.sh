#!/usr/bin/env bash
#
# K4 — empty-consumer WHEEL proof.
#
# Proves the kernel works when INSTALLED AS A WHEEL and consumed by an EXTERNAL
# application — not run from the workspace source tree. The milestone this gates
# is "the kernel is usable without copying source": a consumer that imports ONLY
# the public kernel surface (`from dotmac_kernel import create_app,
# ProductAssemblySpec`) can build and boot an app, serve the DB-free `/health`
# liveness endpoint, and resolve the kernel's packaged data (templates, static,
# migrations) — all from site-packages, with the repo `app/` NEVER on sys.path.
#
# Steps:
#   1. Build the kernel wheel (poetry build -f wheel).
#   2. Create a CLEAN virtualenv in a temp dir (NOT the repo/.venv).
#   3. pip install the wheel + the two runtime pieces the kernel deliberately
#      leaves to the consumer (a DB driver + an HTTP test client) — see below.
#   4. Write a ~15-line external consumer app that imports only public names.
#   5. Boot it (Starlette TestClient) with an UNREACHABLE-but-well-formed
#      DATABASE_URL and assert GET /health == 200 (DB-free liveness invariant).
#   6. Assert the packaged data resolves FROM THE INSTALLED WHEEL (site-packages).
#   7. Clean up the temp venv/dir.
#
# CI-friendly and deterministic: no Postgres, no port binding (TestClient runs
# the ASGI app in-process), temp workspace removed on exit.
#
set -euo pipefail

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

# ── Temp workspace (venv + consumer app), removed on exit ───────────────────
WORKDIR=$(mktemp -d)
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "==> [1/6] Build the kernel wheel"
cd "$KERNEL_DIR"
rm -rf dist
poetry build -f wheel
WHEEL=$(ls "$KERNEL_DIR"/dist/*.whl | head -n1)
echo "    wheel: $WHEEL"

echo "==> [2/6] Create a CLEAN consumer virtualenv (isolated from the repo venv)"
VENV="$WORKDIR/venv"
"$PYTHON" -m venv "$VENV"
VPY="$VENV/bin/python"
"$VPY" -m pip install --quiet --upgrade pip

echo "==> [3/6] Install the wheel + its declared deps into the clean venv"
# The wheel's METADATA carries the kernel's declared dependency closure
# (fastapi, sqlalchemy, pydantic, pydantic-settings, jinja2, argon2-cffi) — pip
# resolves those from the wheel alone, which is the "dep set is complete" proof.
# Two runtime pieces are DELIBERATELY excluded from kernel deps as assembly/
# deploy concerns (see packages/dotmac-kernel/pyproject.toml) and are supplied
# HERE by the consumer, exactly as a real deployment would:
#   - psycopg[binary] : the DB driver the postgresql+psycopg:// URL names.
#                       SQLAlchemy imports it eagerly at create_engine() time
#                       (dotmac_kernel.db builds the engine at import), so
#                       building the app requires a driver even though /health
#                       never connects.
#   - httpx           : the transport Starlette's TestClient uses for the
#                       in-process /health probe.
# The workspace app/ is NOT installed and is NOT on sys.path — the whole point:
# the consumer sees only the public kernel surface from site-packages.
"$VPY" -m pip install --quiet "$WHEEL" "psycopg[binary]" httpx

echo "==> [4/6] Write the minimal EXTERNAL consumer app (public names only)"
cat > "$WORKDIR/consumer_main.py" <<'PY'
"""Minimal external consumer of dotmac-kernel.

Imports ONLY the public kernel surface — no `app.*`, nothing from the workspace.
An empty assembly (no feature modules) boots to just the kernel surface.
"""

from dotmac_kernel import ProductAssemblySpec, create_app

app = create_app(ProductAssemblySpec(name="ext-consumer", modules=()))
PY

echo "==> [5/6] Write the boot + package-data proof runner"
cat > "$WORKDIR/consumer_check.py" <<'PY'
"""Boot the external consumer app from the installed wheel and assert:
  1. GET /health == 200 (DB-free liveness, unreachable DATABASE_URL).
  2. The kernel and its packaged data resolve from site-packages (the installed
     WHEEL), NOT from the repo source tree.
  3. Templates / static (css SOURCE + js + fonts) / migrations 0001..0007 are
     present as package data.
"""

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import dotmac_kernel
import dotmac_kernel.migrations as kmig
import dotmac_kernel.templating as ktpl
from consumer_main import app

repo_root = Path(os.environ["REPO_ROOT"]).resolve()
site_root = Path(sys.prefix).resolve()  # the clean venv


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

# ── 3. packaged TEMPLATES resolve from the wheel ─────────────────────────────
templates_dir = ktpl.TEMPLATES_DIR
installed_from_wheel(templates_dir)
assert (templates_dir / "base.html").is_file(), "templates/base.html missing from wheel"
print(f"    templates/base.html-> present ({templates_dir})")

# ── 4. packaged STATIC resolves from the wheel ───────────────────────────────
static_dir = ktpl.STATIC_DIR
installed_from_wheel(static_dir)
# The css SOURCE (Tailwind input) IS package data; the COMPILED static/css/main.css
# is a gitignored build artifact absent from a source build of the wheel — so we
# assert on the source + js + fonts, which are always shipped.
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
compiled = static_dir / "css" / "main.css"
print(f"    static/css/main.css (compiled build artifact) -> "
      f"{'present' if compiled.is_file() else 'absent (expected in a source build)'}")

# ── 5. packaged MIGRATIONS 0001..0007 resolve from the wheel ─────────────────
versions_dir = kmig.versions_dir()
installed_from_wheel(versions_dir)
revs = sorted(p.name for p in versions_dir.glob("*.py") if p.name != "__init__.py")
for n in ("0001", "0002", "0003", "0004", "0005", "0006", "0007"):
    assert any(f"_{n}_" in r for r in revs), f"migration {n} missing from wheel"
print(f"    migrations 0001..0007 -> {len(revs)} revisions present ({versions_dir})")

print("\nOK — kernel boots and resolves its package data from the installed wheel.")
PY

echo "==> [6/6] Boot the consumer from the installed wheel and run the proof"
cd "$WORKDIR"
DATABASE_URL="$CONSUMER_DB_URL" REPO_ROOT="$REPO_ROOT" "$VPY" consumer_check.py

echo
echo "PASS — empty-consumer wheel proof succeeded."
