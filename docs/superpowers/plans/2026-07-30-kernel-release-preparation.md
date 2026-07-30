# Kernel Release Preparation (R0)

> **Status: DESIGN / PLAN ONLY — not executed.** This document PREPARES the
> publication of the `dotmac-kernel` distribution. The actual publish is
> **kernel-boundary Task 6** (`docs/superpowers/plans/2026-07-18-kernel-boundary.md`).
> Nothing here configures a live registry, creates a trusted-publisher entry,
> handles a token, or pushes a tag. Every secret is referenced by its OpenBao
> path only — never a value (per Michael's hard rule: no secrets in any
> git-tracked or synced file).
>
> **Precondition (blocking):** the kernel package does not physically exist on
> this branch yet. `packages/dotmac-kernel/` is created by kernel-boundary
> **Task 1** (package split) and its public surface + metadata are finalized by
> **Tasks 2–5**. This release plan assumes those have landed and are green on a
> protected `main`. The concrete `pyproject.toml` `include` list, `__all__`, and
> `dependencies` referenced below are the kernel-boundary plan's stated targets;
> re-verify them against the real `packages/dotmac-kernel/pyproject.toml` at
> execution time — that file is authoritative over this plan.

## Package facts (from the kernel-boundary plan)

| Field | Value |
| --- | --- |
| Distribution name | `dotmac-kernel` |
| Import name | `dotmac_kernel` |
| Version | `0.1.0a1` (PEP 440 prerelease — alpha 1) |
| Build backend | `poetry-core` (`poetry.core.masonry.api`) |
| Layout | src (`packages/dotmac-kernel/src/dotmac_kernel/`) |
| Python | `>=3.12,<3.14` |
| Runtime deps (target) | `fastapi`, `sqlalchemy`, `pydantic`, `pydantic-settings`, `jinja2`, `argon2-cffi` — **only these** |
| Package data (target `include`) | `templates/`, `static/`, `fonts/`, `migrations/` shipped as package data |

The assembly (`app/`) is **not** part of this distribution. It is the reference
consumer and must never appear in the wheel or sdist (inspection check §4).

---

## 1. Registry decision

### Options

**A. Public PyPI (`pypi.org`).**
- Pros: zero-cost, canonical, native GitHub Actions OIDC trusted-publishing
  support (no token ever), universally reachable by any future consumer/CI,
  standard `pip install dotmac-kernel`. Matches the kernel-boundary intent that
  the repo be "its own first consumer" of a genuinely published artifact.
- Cons: the name and every uploaded version are **public and permanent**
  (PyPI does not allow re-upload of a deleted version's filename). A `0.1.0a1`
  alpha is world-visible. Requires that we are comfortable publishing the kernel
  surface openly.

**B. Approved private registry** (e.g. a self-hosted index, or a hosted private
index the fleet already runs).
- Pros: surface stays internal until the API is stable; access controlled.
- Cons: consumers must configure an index URL + auth for every install and CI
  job; trusted-publishing/OIDC support varies by backend and may force us back
  to a stored upload token (the exact thing we are trying to avoid); one more
  piece of fleet infrastructure to run, back up, and secure. No such registry is
  currently declared as an approved Dotmac source of truth.

### Recommendation

**Publish `0.1.0a1` to public PyPI using OIDC trusted publishing.**

Rationale: the kernel is a *framework boundary*, not customer data or a
competitive secret — its whole purpose (kernel-boundary milestone 1) is to be
installable "without copying source." A prerelease (`aN`) is the correct vehicle
for a not-yet-stable public API: `pip install` ignores prereleases unless
`--pre` or an explicit prerelease specifier is given, so publishing `0.1.0a1`
does **not** expose it to accidental `pip install dotmac-kernel` while still
proving the real publish path end-to-end. PyPI is the only option with
first-class, tokenless OIDC trusted publishing, which satisfies the "no package
token ever handled" requirement (§2) with zero standing credentials. A private
registry buys confidentiality we do not need here at the cost of reintroducing a
stored token and bespoke index config.

> **Ratification required — Michael's call.** This is a recommendation, not a
> decision. Publishing to public PyPI is irreversible per version and makes the
> kernel surface public. If the kernel surface must stay internal for now,
> switch to option B and record it as an architecture decision naming the
> registry, its OIDC/trusted-publishing capability (or the accepted stored-token
> exception + its OpenBao path), and the consumer index-configuration story.

### Package-name-ownership confirmation (before any publish)

The name `dotmac-kernel` must be confirmed unowned or owned-by-us on the chosen
registry **before** the release workflow runs — a first publish is what claims
the name, and trusted publishing can be configured for a project that does not
exist yet ("pending publisher").

Confirmation steps (public PyPI):

1. **Check availability** — read-only, no login:
   - `curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/dotmac-kernel/json`
     → `404` means the name is unclaimed; `200` means it already exists (inspect
     the owner before proceeding — do **not** publish over an unrelated project).
2. **Confirm the org account** that will own the project. The owning PyPI
   account/organization must be a Dotmac-controlled account. Its credentials live
   in OpenBao at `secret/dotmac/pypi/owner-account` (path reference only — the
   value is never needed by the workflow, only by a human to log into pypi.org
   and create the pending publisher). 2FA on that account is mandatory.
3. **Create the "pending publisher"** on pypi.org (Account → Publishing) *before*
   the first tag: project name `dotmac-kernel`, bound to this repo + the release
   workflow filename + the `pypi-release` environment (see §2). This reserves the
   name and lets the first OIDC publish create the project with no token.
4. **Record** the confirmed owner and the pending-publisher binding in this doc's
   sign-off section (below) so a future session does not re-claim or fork the name.

For a private registry (option B), the equivalent is: confirm the project
namespace exists and is writable by the CI identity, and that the registry
supports the OIDC audience GitHub presents (otherwise fall back to a stored
upload token referenced by an OpenBao path — see §2's fallback note).

---

## 2. Trusted publishing (OIDC — no token ever)

Trusted publishing replaces a long-lived upload token with a short-lived OIDC
token that GitHub Actions mints per-run and the registry verifies against a
pre-registered binding. **No package/API token is created, stored, pasted, or
referenced anywhere** — not in the repo, not in GitHub secrets, not in OpenBao.

### The two halves of the binding

**GitHub side (in the release workflow — see §3):**
- The publishing job declares `permissions: id-token: write` (plus
  `contents: read`). This is what lets the job request an OIDC token; it is
  scoped to the single job, not the whole workflow.
- The job runs in a GitHub **Environment** named `pypi-release` (see §3). The
  environment can carry protection rules (required reviewer / tag restriction)
  so a publish cannot be triggered from an arbitrary branch or by an
  unauthorized actor.
- Upload is done by `pypa/gh-action-pypi-publish` (the maintained action that
  performs the OIDC exchange). No `password:`/`user:` inputs — omitting them is
  what selects trusted publishing.

**Registry side (configured once, by a human, in the pypi.org UI — not in git):**
A trusted publisher entry on the `dotmac-kernel` project referencing exactly:
- Repository owner: `michaelayoade` (the GitHub owner of `dotmac_starter_mt`).
- Repository name: `dotmac_starter_mt`.
- Workflow filename: `release-kernel.yml` (must match the committed workflow
  filename at Task 6 — see §3).
- Environment name: `pypi-release`.

The registry only accepts an OIDC token whose claims match all four. A run from
a fork, a different workflow file, or outside the `pypi-release` environment is
rejected — this is the security boundary that makes tokenless publishing safe.

### Configuration shape (illustrative — the real config is entered in the UI)

```
# pypi.org → project "dotmac-kernel" → Publishing → Add a trusted publisher
Owner:        michaelayoade
Repository:   dotmac_starter_mt
Workflow:     release-kernel.yml
Environment:  pypi-release
```

```yaml
# GitHub side — the shape the workflow declares (full workflow in §3)
permissions:
  id-token: write   # mint the OIDC token
  contents: read
environment: pypi-release
# publish step uses pypa/gh-action-pypi-publish with NO password input
```

### Secret handling

- **No secret value appears in this document, the workflow, or any synced file.**
- The **only** credential involved is the human login to the PyPI owner account
  used once to create the pending publisher and trusted-publisher entry. It lives
  at OpenBao `secret/dotmac/pypi/owner-account` — **path reference only**.
- **Fallback (option B / a registry without OIDC only):** if a stored upload
  token is unavoidable, it lives at OpenBao `secret/dotmac/<registry>/upload-token`
  and is injected into CI as a GitHub Actions secret at deploy time — never
  committed, never printed, referenced by path only. This fallback should be
  recorded as an explicit deviation because it reintroduces a standing
  credential the recommended path avoids.

---

## 3. Draft release workflow

> **Not committed.** This is the YAML that kernel-boundary Task 6 will place at
> `.github/workflows/release-kernel.yml`. It is presented here for review only.
> It mirrors the existing `.github/workflows/ci.yml` conventions (checkout@v4,
> setup-python@v5, install-poetry@v1, Python 3.12).

Design points:
- **Trigger:** prerelease tags only — `v*a*`, `v*b*`, `v*rc*` (matches
  `0.1.0a1` → tag `v0.1.0a1`). A final `vX.Y.Z` tag does **not** match, so a
  stable release cannot be published by accident through this prerelease
  pipeline; promoting to stable is a deliberate, separately-reviewed change.
- **Builds from the exact protected-main candidate:** the job checks out the
  tagged commit and asserts that commit is an ancestor of `origin/main`
  (fail-closed if someone tags a commit that never passed `main`'s protected
  CI). Build once, publish that exact artifact — no rebuild between inspect and
  publish.
- **Two jobs, gated:** `build` (build + inspect + smoke, uploads artifacts) →
  `publish` (needs `build`, `pypi-release` environment, OIDC). The inspection and
  smoke gates (§4, §5) must pass before `publish` can run.
- **Artifact retention:** built wheel/sdist are uploaded with a bounded
  retention (90 days) — long enough to investigate a bad release, not
  indefinite. PyPI itself is the durable store of a *published* artifact; the
  CI artifact is a build/debug copy only.

```yaml
name: Release kernel
on:
  push:
    tags:
      # Prerelease tags ONLY (aN / bN / rcN). Stable vX.Y.Z is intentionally excluded.
      - "v[0-9]+.[0-9]+.[0-9]+a[0-9]+"
      - "v[0-9]+.[0-9]+.[0-9]+b[0-9]+"
      - "v[0-9]+.[0-9]+.[0-9]+rc[0-9]+"

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: packages/dotmac-kernel
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # need history to verify the tag descends from main

      # Fail closed unless the tagged commit is an ancestor of origin/main
      # (i.e. it actually passed main's protected CI).
      - name: Assert tag is on protected main
        working-directory: .
        run: |
          git fetch --no-tags origin main
          if ! git merge-base --is-ancestor "${GITHUB_SHA}" origin/main; then
            echo "::error::Tagged commit ${GITHUB_SHA} is not an ancestor of origin/main"
            exit 1
          fi

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: snok/install-poetry@v1
        with:
          virtualenvs-create: true
          virtualenvs-in-project: true

      # Assert the tag matches the package version (v0.1.0a1 -> 0.1.0a1).
      - name: Assert tag matches package version
        run: |
          PKG_VERSION="$(poetry version --short)"
          TAG_VERSION="${GITHUB_REF_NAME#v}"
          if [ "${PKG_VERSION}" != "${TAG_VERSION}" ]; then
            echo "::error::Tag ${TAG_VERSION} != package version ${PKG_VERSION}"
            exit 1
          fi

      - name: Build wheel + sdist
        run: poetry build

      # --- Inspection gate (§4) ---
      - name: Inspect artifacts
        run: |
          python -m pip install --quiet twine check-wheel-contents
          twine check dist/*
          check-wheel-contents dist/*.whl
          # Package-data present in the wheel:
          python - <<'PY'
          import zipfile, glob, sys
          whl = glob.glob("dist/*.whl")[0]
          names = zipfile.ZipFile(whl).namelist()
          required = ("dotmac_kernel/templates/", "dotmac_kernel/static/",
                      "dotmac_kernel/fonts/", "dotmac_kernel/migrations/")
          missing = [p for p in required if not any(n.startswith(p) for n in names)]
          # The assembly and secrets must NOT be present.
          forbidden = [n for n in names
                       if n.startswith("app/") or n.endswith(".env")
                       or "/.env" in n or n.endswith("secrets.py")]
          if missing:
              sys.exit(f"Missing package data: {missing}")
          if forbidden:
              sys.exit(f"Forbidden files in wheel: {forbidden}")
          print("wheel contents OK")
          PY

      # --- External install-smoke gate (§5) ---
      - name: Install-smoke in a clean venv
        working-directory: .
        run: bash packages/dotmac-kernel/scripts/install_smoke.sh \
               packages/dotmac-kernel/dist

      - name: Upload build artifacts
        uses: actions/upload-artifact@v4
        with:
          name: dotmac-kernel-dist
          path: packages/dotmac-kernel/dist/*
          retention-days: 90

  publish:
    needs: build
    runs-on: ubuntu-latest
    environment: pypi-release        # protected env; trusted-publisher binding
    permissions:
      id-token: write                # OIDC — no token stored anywhere
      contents: read
    steps:
      - name: Download built artifacts
        uses: actions/download-artifact@v4
        with:
          name: dotmac-kernel-dist
          path: dist
      # Trusted publishing: NO password/user inputs -> OIDC exchange.
      - name: Publish to PyPI (trusted publishing)
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: dist
          # repository-url omitted -> pypi.org. For a private/Test index,
          # set repository-url; never add a password input.
```

> `scripts/install_smoke.sh` is the §5 procedure, committed alongside the
> workflow at Task 6. Publishing the exact artifact that `build` inspected and
> smoked (via `download-artifact`, not a rebuild) is deliberate — inspect and
> publish must be the same bytes.

---

## 4. Wheel / sdist inspection checks

Run against the freshly built `dist/` (the workflow automates these; they are
also the manual pre-flight before ratifying a first publish).

Build:
```bash
cd packages/dotmac-kernel && poetry build   # -> dist/dotmac_kernel-0.1.0a1-py3-none-any.whl + .tar.gz
```

**Metadata correctness:**
```bash
twine check dist/*                                   # long_description + metadata render
python -m pip install pkginfo
python - <<'PY'
from pkginfo import Wheel
w = Wheel("dist/dotmac_kernel-0.1.0a1-py3-none-any.whl")
assert w.name == "dotmac-kernel", w.name
assert w.version == "0.1.0a1", w.version
# Runtime deps must be EXACTLY these six (names only; version pins may follow):
expected = {"fastapi","sqlalchemy","pydantic","pydantic-settings","jinja2","argon2-cffi"}
got = {r.split()[0].split("[")[0].split(";")[0].strip().lower() for r in (w.requires_dist or [])}
assert got == expected, f"dep drift: {got ^ expected}"
print("metadata OK")
PY
```
- Confirms name `dotmac-kernel`, version `0.1.0a1`, and that no stray runtime
  dependency (e.g. `uvicorn`, `psycopg`, `python-multipart` — assembly/runtime
  concerns, not kernel API deps) has leaked in.

**Package-data inclusion** (templates / static / fonts / migrations):
```bash
python -c "import zipfile,glob; \
n=zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist(); \
[print('OK', p) if any(x.startswith('dotmac_kernel/'+p) for x in n) else exit('MISSING '+p) \
 for p in ('templates/','static/','fonts/','migrations/')]"
check-wheel-contents dist/*.whl        # flags empty dirs / stray top-level files / duplicate paths
```
- Verifies the `include` list actually shipped. Also inspect the sdist
  (`tar tzf dist/*.tar.gz`) — sdist include rules differ from wheel and can
  silently drop data files.

**No accidental inclusion of the assembly or secrets:**
```bash
python - <<'PY'
import zipfile, glob, sys
n = zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist()
bad = [x for x in n if x.startswith("app/") or x.split("/")[0]=="app"
       or x.endswith(".env") or "/.env" in x or x.endswith(("secrets.py","secret.py"))
       or "id_rsa" in x or x.endswith((".pem",".key"))]
if bad: sys.exit("FORBIDDEN in wheel: " + ", ".join(bad))
# top-level import package must be ONLY dotmac_kernel
tops = {x.split("/")[0] for x in n if not x.startswith("dotmac_kernel") and "dist-info" not in x}
if tops: sys.exit("Unexpected top-level entries: " + ", ".join(sorted(tops)))
print("no assembly / no secrets OK")
PY
tar tzf dist/*.tar.gz | grep -E '(^|/)app/|\.env|secret|\.pem$|\.key$' && \
  { echo "FORBIDDEN in sdist"; exit 1; } || echo "sdist clean"
```
- The src layout is the structural guarantee here (only `src/dotmac_kernel/` is
  packaged), but this check fails loudly if a future `include`/`packages` edit
  ever pulls `app/` or an env/secret file into the artifact.

---

## 5. External install-smoke procedure

Proves the built wheel installs and boots **as an external consumer would** —
from a clean venv, **not** the workspace/editable path, importing only public
names. This is the packaged, standalone form of the kernel-boundary Task 4
`consumer-boot` proof, run against the *release* artifact.

Committed as `packages/dotmac-kernel/scripts/install_smoke.sh` (invoked by the
workflow, §3). Sketch:

```bash
#!/usr/bin/env bash
set -euo pipefail
DIST_DIR="${1:?usage: install_smoke.sh <dist-dir>}"
WHEEL="$(ls "${DIST_DIR}"/*.whl | head -1)"

# 1. Clean venv OUTSIDE the workspace (mktemp -> not the repo path).
WORK="$(mktemp -d)"; trap 'rm -rf "${WORK}"' EXIT
python -m venv "${WORK}/venv"
# shellcheck disable=SC1091
source "${WORK}/venv/bin/activate"
python -m pip install --quiet --upgrade pip
# Install the built wheel by path (NOT `pip install -e`, NOT the repo).
python -m pip install --quiet "${WHEEL}" uvicorn

# 2. Minimal consumer importing ONLY public names.
cat > "${WORK}/consumer.py" <<'PY'
from dotmac_kernel import create_app                 # public entrypoint
from dotmac_kernel.assembly import ProductAssemblySpec  # public
app = create_app(ProductAssemblySpec(name="smoke", modules=()))
PY

# 3. Boot with a deliberately UNREACHABLE database (liveness must not need a DB).
export DATABASE_URL="postgresql+psycopg://x:x@127.0.0.1:1/x"
cd "${WORK}"
python - <<'PY'
from fastapi.testclient import TestClient
import importlib, dotmac_kernel
consumer = importlib.import_module("consumer")
with TestClient(consumer.app) as c:
    r = c.get("/health")
    assert r.status_code == 200, f"/health -> {r.status_code}"

# 4. Packaged data resolves from the INSTALLED wheel (site-packages, not repo).
from importlib.resources import files
root = files("dotmac_kernel")
for sub, probe in (("templates","base.html"),):
    assert (root / sub).is_dir(), f"missing packaged {sub}/"
# templates/static/fonts/migrations dirs all present in the installed package:
for sub in ("templates","static","fonts","migrations"):
    assert (root / sub).is_dir(), f"packaged {sub}/ did not resolve from install"
# Prove it resolves from site-packages, not the source tree:
assert "site-packages" in str(root), f"resolved from source, not install: {root}"
print("install-smoke OK")
PY
```

Assertions this makes:
- **Clean-venv / not-workspace:** venv created under `mktemp -d`; installed by
  wheel path; `str(files('dotmac_kernel'))` must contain `site-packages` — a
  hard guard that we did not accidentally import the editable source tree.
- **Public-names-only consumer:** imports `create_app` and `ProductAssemblySpec`
  only — if either is not in the documented public surface, the smoke fails,
  keeping the release honest about what consumers can rely on.
- **DB-free liveness:** boots with an unreachable `DATABASE_URL` and asserts
  `/health` → `200` (same invariant the `docker-build` job already relies on).
- **Packaged assets resolve:** `templates/`, `static/`, `fonts/`, `migrations/`
  all resolve via `importlib.resources` from the installed location — the real
  test that the §4 `include` data is usable at runtime, not just present in the
  zip.

---

## Execution checklist (kernel-boundary Task 6)

1. Confirm precondition: `packages/dotmac-kernel/` exists, Tasks 1–5 merged and
   green on `main`; re-verify `pyproject.toml` `include` + `dependencies` match
   the table above (this doc's facts are the plan target, the file is truth).
2. **[Michael] Ratify registry choice** (public PyPI recommended) and confirm
   `dotmac-kernel` name ownership (§1 steps 1–4); create the pending trusted
   publisher (§2) on the owner account (OpenBao `secret/dotmac/pypi/owner-account`).
3. Commit `release-kernel.yml` (§3) + `scripts/install_smoke.sh` (§5) to `main`
   via PR; green CI.
4. Tag the exact reviewed `main` commit `v0.1.0a1`; the workflow builds,
   inspects (§4), smokes (§5), and publishes via OIDC (§2) — no token touched.
5. Verify `pip install --pre dotmac-kernel==0.1.0a1` from a clean external venv
   post-publish.

## Decisions requiring Michael's ratification

- **R0-D1 — Registry.** Public PyPI (recommended) vs an approved private
  registry. Irreversible-per-version and makes the kernel surface public if
  PyPI. If private, record an architecture decision naming the registry, its
  OIDC capability (or the stored-token exception + OpenBao path).
- **R0-D2 — Name ownership.** Confirm the Dotmac PyPI owner account and that
  `dotmac-kernel` is unclaimed / ours (§1). Record the confirmed owner +
  pending-publisher binding here after it is created.
- **R0-D3 — Workflow filename + environment name.** `release-kernel.yml` and
  `pypi-release` are proposed; the trusted-publisher binding must match whatever
  is committed. Ratify or rename before creating the publisher entry.
