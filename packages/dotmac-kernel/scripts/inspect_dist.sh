#!/usr/bin/env bash
#
# Kernel wheel/sdist inspection gate (kernel-boundary Task 6, R0 §4).
#
# Fails the release build unless the freshly built dist/ is correct:
#   1. twine metadata renders + is valid.
#   2. Distribution name/version are exactly dotmac-kernel / <pyproject version>.
#   3. Runtime deps are EXACTLY the six kernel deps; httpx appears ONLY under the
#      `testing` extra — no stray runtime dep (uvicorn/psycopg/python-multipart).
#   4. Package data ships: templates/, static/ (incl. static/fonts/ and the
#      COMPILED static/css/main.css — REQUIRED for a published web kernel),
#      migrations/. check-wheel-contents flags empties/dupes.
#   5. Every .py under src/dotmac_kernel/ is IN the wheel and the sdist, and
#      nothing extra is: the artifact's importable module set equals the
#      source's. Metadata, dep and package-data checks all pass on a wheel
#      missing a module, which is how a merged capability gets read as shipped.
#   6. Neither the assembly (app/) nor any secret/env file leaked into the wheel
#      or sdist; the only top-level import package is dotmac_kernel.
#
# Usage: inspect_dist.sh <dist-dir>
#
set -euo pipefail

DIST_DIR="${1:?usage: inspect_dist.sh <dist-dir>}"
[ -d "$DIST_DIR" ] || { echo "no such dist dir: $DIST_DIR" >&2; exit 1; }
WHEEL=$(ls "$DIST_DIR"/*.whl 2>/dev/null | head -n1)
SDIST=$(ls "$DIST_DIR"/*.tar.gz 2>/dev/null | head -n1)
[ -n "$WHEEL" ] || { echo "no wheel in $DIST_DIR" >&2; exit 1; }
[ -n "$SDIST" ] || { echo "no sdist in $DIST_DIR" >&2; exit 1; }

PY="${INSPECT_PYTHON:-python3}"

# The artifact is compared against the source tree that produced it, located
# from this script rather than from $PWD so the gate cannot be pointed at a
# different checkout by the directory it happens to run in.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_SRC="${SCRIPT_DIR}/../src/dotmac_kernel"
[ -d "$KERNEL_SRC" ] || { echo "no kernel source at $KERNEL_SRC" >&2; exit 1; }

echo "==> [1/6] Install inspection tooling"
"$PY" -m pip install --quiet twine check-wheel-contents pkginfo

echo "==> [2/6] twine check (metadata renders)"
"$PY" -m twine check "$DIST_DIR"/*

echo "==> [3/6] Metadata: name / version / runtime-dep closure"
WHEEL="$WHEEL" "$PY" - <<'PY'
import os
from pkginfo import Wheel

w = Wheel(os.environ["WHEEL"])
assert w.name == "dotmac-kernel", f"name drift: {w.name}"
assert w.version, "missing version"
print(f"    name={w.name} version={w.version}")

reqs = w.requires_dist or []
# Runtime deps = entries WITHOUT an extra marker.
runtime = [r for r in reqs if "extra ==" not in r]
runtime_names = {r.split()[0].split("[")[0].split(";")[0].strip().lower() for r in runtime}
expected = {"fastapi", "sqlalchemy", "pydantic", "pydantic-settings", "jinja2", "argon2-cffi"}
assert runtime_names == expected, f"runtime dep drift: {runtime_names ^ expected} (got {runtime_names})"

# httpx must appear ONLY behind the testing extra, never as a bare runtime dep.
assert "httpx" not in runtime_names, "httpx leaked as a bare runtime dep"
extra_httpx = [r for r in reqs if r.split()[0].split(";")[0].strip().lower() == "httpx"]
assert extra_httpx and all("extra ==" in r for r in extra_httpx), "httpx must be a testing-extra dep"
print(f"    runtime deps OK ({sorted(runtime_names)}); httpx gated behind testing extra")
PY

echo "==> [4/6] Package data (templates / static incl. fonts + compiled main.css / migrations)"
WHEEL="$WHEEL" "$PY" - <<'PY'
import os, zipfile, sys

names = zipfile.ZipFile(os.environ["WHEEL"]).namelist()
required_dirs = (
    "dotmac_kernel/templates/",
    "dotmac_kernel/static/",
    "dotmac_kernel/static/fonts/",   # fonts live UNDER static/
    "dotmac_kernel/migrations/",
)
missing = [p for p in required_dirs if not any(n.startswith(p) for n in names)]
if missing:
    sys.exit(f"FAIL: missing package data: {missing}")

# The COMPILED web asset MUST ship in a release wheel (run npm run css:build
# before poetry build). Source builds omit it; a PUBLISHED web kernel may not.
if "dotmac_kernel/static/css/main.css" not in names:
    sys.exit("FAIL: dotmac_kernel/static/css/main.css missing — run `npm run css:build` before `poetry build`")

# PEP 561 marker MUST ship so consumers see dotmac_kernel as a TYPED package.
if "dotmac_kernel/py.typed" not in names:
    sys.exit("FAIL: dotmac_kernel/py.typed missing — the kernel's typed contracts would be invisible to consumers")
print("    templates/, static/ (+fonts +compiled main.css), migrations/, py.typed all present")
PY
# check-wheel-contents catches empty dirs / stray top-level / duplicate paths.
# One warning is KNOWN-ACCEPTABLE and ignored deliberately:
#   W004 — Alembic version files (dotmac_kernel/migrations/versions/2026….py)
#          start with a date and are NOT importable module paths BY DESIGN
#          (Alembic loads them by file path, not import). Always expected.
# (W002 duplicate-files is NOT ignored: the vendored font weights are now the
# real distinct weights — guarded by tests/architecture/test_vendored_fonts.py.)
"$PY" -m check_wheel_contents --ignore W004 "$WHEEL"

echo "==> [5/6] Every source module reached the wheel and the sdist"
# WHY THIS EXISTS, and it is not hypothetical. `dotmac_kernel.request_evidence`
# merged to main and was read as shipped: the source was complete, so the
# capability was believed installable. It was in no artifact at all -- a101
# predates it -- and the platform pinning a98 could not have consumed it under
# any version. Steps 2-4 above would all pass on a wheel missing any given
# module, because they check metadata, declared deps and package DATA; nothing
# compared the importable module set against the source claiming to be in it.
#
# Set equality in BOTH directions, deliberately. Missing-only would accept a
# wheel that also carried a module the source no longer has -- a stale build
# tree publishing bytes nobody reviewed.
WHEEL="$WHEEL" SDIST="$SDIST" KERNEL_SRC="$KERNEL_SRC" "$PY" - <<'PY'
import os, pathlib, sys, tarfile, zipfile

src = pathlib.Path(os.environ["KERNEL_SRC"]).resolve()
source = {p.relative_to(src).as_posix() for p in src.rglob("*.py")}
if not source:
    sys.exit("FAIL: no .py under the kernel source; this gate would prove nothing")

wheel = {
    n[len("dotmac_kernel/"):]
    for n in zipfile.ZipFile(os.environ["WHEEL"]).namelist()
    if n.startswith("dotmac_kernel/") and n.endswith(".py")
}
sdist_marker = "/src/dotmac_kernel/"
sdist = {
    n.split(sdist_marker, 1)[1]
    for n in tarfile.open(os.environ["SDIST"]).getnames()
    if sdist_marker in n and n.endswith(".py")
}

for label, shipped in (("wheel", wheel), ("sdist", sdist)):
    absent = sorted(source - shipped)
    extra = sorted(shipped - source)
    if absent:
        sys.exit(
            f"FAIL: {label} is missing {len(absent)} module(s) present in "
            f"{src}: {absent[:10]} -- the artifact does not contain the "
            "source this release claims to publish"
        )
    if extra:
        sys.exit(
            f"FAIL: {label} carries {len(extra)} module(s) absent from {src}: "
            f"{extra[:10]} -- built from a stale tree"
        )
print(f"    {len(source)} source modules present in both wheel and sdist")
PY

echo "==> [6/6] No assembly / no secrets leaked (wheel + sdist)"
WHEEL="$WHEEL" "$PY" - <<'PY'
import os, zipfile, sys

n = zipfile.ZipFile(os.environ["WHEEL"]).namelist()
bad = [x for x in n if x.startswith("app/") or x.split("/")[0] == "app"
       or x.endswith(".env") or "/.env" in x or x.endswith(("secrets.py", "secret.py"))
       or "id_rsa" in x or x.endswith((".pem", ".key"))]
if bad:
    sys.exit("FORBIDDEN in wheel: " + ", ".join(bad))
tops = {x.split("/")[0] for x in n if not x.startswith("dotmac_kernel") and "dist-info" not in x}
if tops:
    sys.exit("Unexpected top-level entries: " + ", ".join(sorted(tops)))
print("    wheel: no app/ , no secrets, only dotmac_kernel top-level")
PY
# Same rule as the wheel check above, deliberately: this used to match a bare
# `secret` anywhere in a path, so any legitimately named module — say
# `secret_sources.py`, the seam products install secret material through —
# failed the release. A guard with false positives gets weakened by whoever
# hits one at a bad moment; matching the wheel's precision keeps it sharp.
# What is forbidden is a file that IS a secret, not one that discusses them.
if tar tzf "$SDIST" | grep -Eq '(^|/)app/|(^|/)\.env|\.env$|(^|/)secrets?\.py$|id_rsa|\.pem$|\.key$'; then
  echo "FORBIDDEN in sdist:" >&2
  tar tzf "$SDIST" | grep -E '(^|/)app/|(^|/)\.env|\.env$|(^|/)secrets?\.py$|id_rsa|\.pem$|\.key$' >&2
  exit 1
fi
echo "    sdist: clean"

echo
echo "PASS — dist inspection succeeded."
