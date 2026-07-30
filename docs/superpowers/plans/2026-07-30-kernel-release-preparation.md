# Kernel Release Preparation (R0)

> **⚠️ REGISTRY CHOICE SUPERSEDED (2026-07-30) by [ADR-0005](../../adr/0005-forgejo-private-registry.md).**
> The registry decision below (§1 — public PyPI via OIDC trusted publishing) is
> **no longer in effect**: `dotmac-kernel` publishes to a **private Forgejo
> registry** (`https://registry.dotmac.io`), not public PyPI. The *release-gate*
> design here (protected `workflow_dispatch`, exact-main-SHA gate, version-input
> match, build-once/inspect/publish-same-bytes/verify-from-registry,
> tag-after-verify, SHA-pinned actions) is **retained**; only the publish target
> and credential mechanism change. See ADR-0005 and `deploy/forgejo/` for the
> Forgejo standup + the reworked `release-kernel.yml`.

> **Status: RATIFIED (with amendments) — 2026-07-30.** R0 was ratified by
> Michael. The registry, ownership model, and trusted-publisher identity are
> **decided** (below). The publish itself is **kernel-boundary Task 6 (K6)**,
> which implements this plan: it commits `.github/workflows/release-kernel.yml`,
> extends `scripts/consumer_boot_check.sh`, configures the `pypi-release`
> environment, and runs the protected publish. This document does not itself
> configure a live registry, create a trusted-publisher entry, handle a token,
> or push a tag. Every secret is referenced by its OpenBao path only — never a
> value (per Michael's hard rule: no secrets in any git-tracked or synced file).
>
> **Precondition (now satisfied):** `packages/dotmac-kernel/` exists and its
> public surface + metadata are finalized and green on protected `main` —
> Task 1 (package split, PRs #13/#14), K2 (public API, #15), K3B (provisioning
> contract, #17), K3A (ProductAssemblySpec + create_app, #18), K5 (testing kit,
> #19), K4 (consumer-boot wheel proof, #20) are all merged. The concrete
> `pyproject.toml` `include` list, `__all__`, and `dependencies` referenced below
> were re-verified against the real `packages/dotmac-kernel/pyproject.toml` on
> 2026-07-30; that file remains authoritative over this plan.

## Package facts (verified against the real `packages/dotmac-kernel/pyproject.toml`, 2026-07-30)

| Field | Value |
| --- | --- |
| Distribution name | `dotmac-kernel` |
| Import name | `dotmac_kernel` |
| Version | `0.1.0a1` (PEP 440 prerelease — alpha 1) |
| Build backend | `poetry-core` (`poetry.core.masonry.api`) |
| Layout | src (`packages/dotmac-kernel/src/dotmac_kernel/`) |
| Python | `>=3.12,<3.14` |
| Runtime deps | `fastapi`, `sqlalchemy`, `pydantic[email]`, `pydantic-settings`, `jinja2`, `argon2-cffi` |
| Optional extra | `testing = ["httpx"]` (the `dotmac_kernel.testing` HTTP helper; K5) |
| Package data (`include`) | `src/dotmac_kernel/templates/**`, `src/dotmac_kernel/static/**` (incl. `static/fonts/**` and the COMPILED `static/css/main.css`), `src/dotmac_kernel/migrations/**` |

> **Dependency-fact amendment (ratified).** `pydantic` carries the `email`
> extra: the kernel's own public `create_app` mounts `platform_auth`, whose
> `EmailStr` field needs `email-validator` — a genuine kernel runtime dep the K4
> wheel proof caught (declared as `pydantic = { version = "^2.9", extras =
> ["email"] }`). The `testing` extra adds `httpx` for
> `dotmac_kernel.testing.assembly_test_client` only. `psycopg` (DB driver) and
> `uvicorn`/`python-multipart` remain deliberately consumer/deploy-supplied, NOT
> kernel deps.

The assembly (`app/`) is **not** part of this distribution. It is the reference
consumer and must never appear in the wheel or sdist (inspection check §4).

---

## 1. Registry decision — RATIFIED: public PyPI via OIDC Trusted Publishing

**Decision:** publish `0.1.0a1` to **public PyPI (`pypi.org`)** using GitHub
Actions **OIDC Trusted Publishing** — no upload token, ever. PyPI supports
creating a brand-new project through a *pending* trusted publisher, so the first
upload both claims the name and creates the project with zero standing
credentials. (PyPI: [Creating a project through OIDC](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).)

Rationale (ratified): the kernel is a *framework boundary*, not customer data or
a competitive secret — its whole purpose (kernel-boundary milestone 1) is to be
installable "without copying source." A prerelease (`aN`) is the correct vehicle
for a not-yet-stable public API: `pip install` ignores prereleases unless
`--pre` or an explicit prerelease specifier is given, so publishing `0.1.0a1`
proves the real publish path end-to-end without exposing the kernel to an
accidental `pip install dotmac-kernel`. PyPI is the only option with first-class,
tokenless OIDC trusted publishing.

**Name availability (checked 2026-07-30):** PyPI's canonical JSON endpoint
returns `404` for `dotmac-kernel` — the name is **unclaimed**. A pending
publisher does **not** reserve the name until the first upload, so the pending
publisher must be configured **immediately before** publication and the first
publish run **promptly** thereafter.

```bash
# read-only availability re-check, run again at publish time:
curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/dotmac-kernel/json
# 404 -> unclaimed (expected); 200 -> already exists, STOP and inspect the owner.
```

### Ownership model (RATIFIED)

- **Business owner:** Dotmac.
- **Initial PyPI Owner:** Michael's **individual** PyPI account (2FA mandatory).
  Its login is referenced by an OpenBao path only, never a value — **do not use
  a shared human account.**
- **Immediately after the first release:** add a **second named
  Dotmac-controlled human** as an Owner of the project, so the project is never
  bus-factor-1 on a single individual.
- **Later (optional):** transfer the project to a **Dotmac PyPI organization**
  if/when one is established.

---

## 2. Trusted publishing (OIDC — no token ever)

Trusted publishing replaces a long-lived upload token with a short-lived OIDC
token GitHub Actions mints per-run and PyPI verifies against a pre-registered
binding. **No package/API token is created, stored, pasted, or referenced
anywhere** — not in the repo, not in GitHub secrets, not in OpenBao. PyPI
strongly recommends binding a dedicated GitHub environment. (PyPI:
[Using a publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/).)

### Trusted-publisher identity (RATIFIED — must match exactly)

| Claim | Value |
| --- | --- |
| GitHub owner | `michaelayoade` |
| Repository | `dotmac_starter_mt` |
| Workflow filename | `release-kernel.yml` |
| Workflow display name | `Release kernel` |
| Environment | `pypi-release` |
| Jobs | `build`, `publish`, `verify` |

Only the **`publish`** job receives `permissions: id-token: write`; `build` and
`verify` do not. PyPI accepts an OIDC token only when the owner, repository,
workflow filename, and environment all match the pending-publisher entry — a run
from a fork, a different workflow file, or outside the `pypi-release`
environment is rejected. That match is the security boundary that makes
tokenless publishing safe.

```
# pypi.org -> "dotmac-kernel" -> Publishing -> Add a pending trusted publisher
Owner:        michaelayoade
Repository:   dotmac_starter_mt
Workflow:     release-kernel.yml
Environment:  pypi-release
```

### Secret handling

- **No secret value appears in this document, the workflow, or any synced file.**
- The **only** credential involved is the human login to Michael's individual
  PyPI Owner account, used once to create the pending publisher — referenced by
  its OpenBao path only, never a value. 2FA on that account is mandatory.
- There is **no fallback stored token** on the ratified path: public PyPI's OIDC
  trusted publishing needs no upload token at all.

---

## 3. Release workflow — protected `workflow_dispatch` (AMENDED)

> The YAML below is the **design** kernel-boundary Task 6 commits at
> `.github/workflows/release-kernel.yml`. It supersedes the earlier
> tag-triggered draft. Every amendment Michael required is folded in.

### Ratified amendments (vs. the earlier draft)

1. **Protected `workflow_dispatch`, not a tag trigger.** The pipeline is started
   deliberately by a maintainer, never automatically by a pushed tag.
2. **Runs only on the exact current protected-`main` SHA.** `build` fails closed
   unless the checked-out `GITHUB_SHA` equals the current tip of `origin/main`
   (not merely an ancestor). A stale or side-branch run cannot publish.
3. **Explicit `version` input that must match package metadata.** The dispatch
   requires a `version` input (e.g. `0.1.0a1`); `build` fails unless it equals
   `poetry version --short`. No implicit version inference.
4. **Build once → inspect those bytes → publish those same bytes → install and
   verify from PyPI.** `build` produces `dist/` and uploads it as an artifact;
   `publish` downloads and uploads *those* bytes (no rebuild); `verify` installs
   the now-published release *from PyPI* into a clean venv and runs the smoke.
5. **Tag only after registry verification.** `release-kernel.yml` creates the
   annotated tag **`dotmac-kernel-v0.1.0a1`** on the published SHA **only after**
   the `verify` job confirms the artifact installs from PyPI — the tag is a
   record of a verified publish, not its trigger.
6. **`pypi-release` environment: `main` only, required approval by Michael, no
   admin bypass.** Self-review stays permitted until a second release maintainer
   exists (see §Environment configuration). Admins cannot bypass the required
   review.
7. **Every publishing-workflow action pinned to a reviewed full commit SHA.**
   GitHub identifies a full-length commit SHA as the immutable pinning option.
   (GitHub: [Security hardening / secure use](https://docs.github.com/en/actions/reference/security/secure-use).)
   K6 resolves each `@vN` tag to its reviewed commit SHA and pins it, with the
   human-readable version in a trailing comment.
8. **`npm ci && npm run css:build` before `poetry build`.** The compiled
   `static/css/main.css` is a build artifact (gitignored); the published web
   kernel MUST ship it. See §4 — inspection FAILS if it is absent.
9. **Reuse and extend `scripts/consumer_boot_check.sh`.** No parallel smoke
   implementation: the K4 script gains a "release / from-registry" mode that
   §5 / the `verify` job call, rather than a second copy drifting out of sync.

```yaml
name: Release kernel          # display name "Release kernel"
on:
  workflow_dispatch:
    inputs:
      version:
        description: "Kernel version to publish (must equal packages/dotmac-kernel pyproject version, e.g. 0.1.0a1)"
        required: true
        type: string

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      # NOTE: every action below is pinned to a reviewed FULL commit SHA at K6
      # commit time (shown here as @vN for readability). Immutable pinning per
      # GitHub secure-use guidance.
      - uses: actions/checkout@<sha>          # v4
        with:
          fetch-depth: 0

      # (Amendment 2) Fail closed unless this run is the exact current main tip.
      - name: Assert run is on the current protected main SHA
        run: |
          git fetch --no-tags origin main
          MAIN_SHA="$(git rev-parse origin/main)"
          if [ "${GITHUB_SHA}" != "${MAIN_SHA}" ]; then
            echo "::error::run SHA ${GITHUB_SHA} != current origin/main ${MAIN_SHA}"
            exit 1
          fi

      - uses: actions/setup-python@<sha>      # v5
        with:
          python-version: "3.12"
      - uses: actions/setup-node@<sha>        # v4
        with:
          node-version: "20"
      - uses: snok/install-poetry@<sha>       # v1
        with:
          virtualenvs-create: true
          virtualenvs-in-project: true

      # (Amendment 3) Explicit version input must equal package metadata.
      - name: Assert version input matches package metadata
        working-directory: packages/dotmac-kernel
        run: |
          PKG_VERSION="$(poetry version --short)"
          if [ "${{ inputs.version }}" != "${PKG_VERSION}" ]; then
            echo "::error::input ${{ inputs.version }} != package ${PKG_VERSION}"
            exit 1
          fi

      # (Amendment 8) Compile Tailwind CSS so static/css/main.css is shipped.
      - name: Build CSS (npm ci && npm run css:build)
        run: npm ci && npm run css:build

      - name: Build wheel + sdist
        working-directory: packages/dotmac-kernel
        run: poetry build

      # (§4) Inspection gate — fails on dep drift, missing package data
      # (including compiled static/css/main.css), or leaked app/secrets.
      - name: Inspect artifacts
        run: bash packages/dotmac-kernel/scripts/inspect_dist.sh packages/dotmac-kernel/dist

      # (Amendment 9, §5) Reuse consumer_boot_check.sh in from-wheel mode against
      # the freshly built release artifact.
      - name: Release wheel smoke (built artifact)
        run: bash scripts/consumer_boot_check.sh --wheel packages/dotmac-kernel/dist

      - name: Upload built artifacts
        uses: actions/upload-artifact@<sha>   # v4
        with:
          name: dotmac-kernel-dist
          path: packages/dotmac-kernel/dist/*
          retention-days: 90

  publish:
    needs: build
    runs-on: ubuntu-latest
    environment: pypi-release        # protected: main-only + required review (Michael)
    permissions:
      id-token: write                # OIDC — ONLY this job; no token stored anywhere
      contents: read
    steps:
      - name: Download the exact built bytes
        uses: actions/download-artifact@<sha> # v4
        with:
          name: dotmac-kernel-dist
          path: dist
      # (Amendment 4) Publish the SAME bytes build inspected — no rebuild.
      - name: Publish to PyPI (trusted publishing)
        uses: pypa/gh-action-pypi-publish@<sha>  # release/v1
        with:
          packages-dir: dist
          # repository-url omitted -> pypi.org. No password/user input -> OIDC.

  verify:
    needs: publish
    runs-on: ubuntu-latest
    permissions:
      contents: write                # create the post-verification tag
    steps:
      - uses: actions/checkout@<sha>          # v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@<sha>      # v5
        with:
          python-version: "3.12"
      # (Amendment 4) Install the PUBLISHED release from PyPI and smoke it.
      - name: Install from PyPI and verify
        run: bash scripts/consumer_boot_check.sh --from-pypi "${{ inputs.version }}"
      # (Amendment 5) Tag ONLY after registry verification, on the published SHA.
      - name: Tag the verified release
        run: |
          git tag -a "dotmac-kernel-v${{ inputs.version }}" \
            -m "dotmac-kernel ${{ inputs.version }} (verified on PyPI)" "${GITHUB_SHA}"
          git push origin "dotmac-kernel-v${{ inputs.version }}"
```

### Environment configuration (`pypi-release`, RATIFIED)

Configured on the repository (Settings → Environments), K6 applies it via API:

- **Deployment branch policy:** `main` only (selected branches → `main`).
- **Required reviewer:** Michael. Deployment to `pypi-release` (the `publish`
  job) pauses until he approves.
- **No admin bypass:** admins cannot skip the required review.
- **Self-review:** permitted for now (Michael may approve his own dispatch)
  **until a second release maintainer exists** — flip `prevent_self_review` on
  once the second Owner (§1) is added.

GitHub environments natively support required reviewers and branch restrictions.
(GitHub: [Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).)

---

## 4. Wheel / sdist inspection checks (AMENDED)

K6 commits these as `packages/dotmac-kernel/scripts/inspect_dist.sh` (called by
`build`); they are also the manual pre-flight before a first publish.

Build (after `npm run css:build`, so the compiled CSS exists):
```bash
cd packages/dotmac-kernel && poetry build   # -> dist/dotmac_kernel-0.1.0a1-py3-none-any.whl + .tar.gz
```

**Metadata correctness:**
```bash
twine check dist/*
python - <<'PY'
from pkginfo import Wheel
w = Wheel("dist/dotmac_kernel-0.1.0a1-py3-none-any.whl")
assert w.name == "dotmac-kernel", w.name
assert w.version == "0.1.0a1", w.version
# Runtime deps (names only; the pydantic[email] extra shows as an email-validator
# marker on pydantic). Expect exactly these top-level distributions:
expected = {"fastapi","sqlalchemy","pydantic","pydantic-settings","jinja2","argon2-cffi"}
got = {r.split()[0].split("[")[0].split(";")[0].strip().lower() for r in (w.requires_dist or [])}
# httpx is an EXTRA (testing) — allowed only behind the extra marker, never a bare runtime dep.
runtime = {r for r in (w.requires_dist or []) if "extra ==" not in r}
runtime_names = {r.split()[0].split("[")[0].split(";")[0].strip().lower() for r in runtime}
assert runtime_names == expected, f"runtime dep drift: {runtime_names ^ expected}"
print("metadata OK")
PY
```
- Confirms name/version and that no stray runtime dependency (`uvicorn`,
  `psycopg`, `python-multipart`) leaked in, and that `httpx` appears **only**
  under the `testing` extra, never as a bare runtime dep.

**Package-data inclusion — templates / static (incl. fonts AND compiled CSS) / migrations:**
```bash
python - <<'PY'
import zipfile, glob, sys
names = zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist()
required = (
    "dotmac_kernel/templates/",
    "dotmac_kernel/static/",
    "dotmac_kernel/static/fonts/",        # (AMENDED) fonts live UNDER static/
    "dotmac_kernel/migrations/",
)
missing = [p for p in required if not any(n.startswith(p) for n in names)]
if missing: sys.exit(f"Missing package data: {missing}")
# (AMENDED) the COMPILED web asset MUST ship — the K4 proof tolerated its
# absence (source build); the PUBLISHED web kernel may not.
if "dotmac_kernel/static/css/main.css" not in names:
    sys.exit("FAIL: dotmac_kernel/static/css/main.css missing — run npm run css:build before poetry build")
print("package data (incl. compiled main.css + static/fonts) OK")
PY
check-wheel-contents dist/*.whl
```
- **Amendment:** the compiled `dotmac_kernel/static/css/main.css` is now a
  **hard requirement** (the K4 `consumer_boot_check.sh` explicitly *tolerates*
  its absence because K4 builds from source; that tolerance is wrong for a
  published web kernel, so the release inspection asserts its presence). The
  font path is corrected to `dotmac_kernel/static/fonts/`.

**No accidental inclusion of the assembly or secrets** (unchanged structural guard):
```bash
python - <<'PY'
import zipfile, glob, sys
n = zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist()
bad = [x for x in n if x.startswith("app/") or x.split("/")[0]=="app"
       or x.endswith(".env") or "/.env" in x or x.endswith(("secrets.py","secret.py"))
       or "id_rsa" in x or x.endswith((".pem",".key"))]
if bad: sys.exit("FORBIDDEN in wheel: " + ", ".join(bad))
tops = {x.split("/")[0] for x in n if not x.startswith("dotmac_kernel") and "dist-info" not in x}
if tops: sys.exit("Unexpected top-level entries: " + ", ".join(sorted(tops)))
print("no assembly / no secrets OK")
PY
tar tzf dist/*.tar.gz | grep -E '(^|/)app/|\.env|secret|\.pem$|\.key$' && \
  { echo "FORBIDDEN in sdist"; exit 1; } || echo "sdist clean"
```

---

## 5. External install-smoke — extend `scripts/consumer_boot_check.sh` (AMENDED)

**No parallel smoke script.** The K4 proof `scripts/consumer_boot_check.sh`
already builds a wheel, installs it into a clean venv, boots a public-imports-only
external consumer with an unreachable DB, and asserts `/health == 200` plus
package-data resolution from site-packages. K6 **extends** it with two modes so
the release path reuses exactly that logic:

- `consumer_boot_check.sh --wheel <dist-dir>` — smoke the freshly **built**
  release artifact (used by the `build` job before publish).
- `consumer_boot_check.sh --from-pypi <version>` — `pip install --pre
  dotmac-kernel==<version>` **from PyPI** into a clean venv and run the same
  assertions (used by the `verify` job after publish).

The extension also flips the compiled-CSS assertion for release mode: in
`--from-pypi`/`--wheel` release mode, `static/css/main.css` **must** be present
(matching §4), whereas the default source-build mode keeps tolerating its
absence. The existing default (source-tree) behavior is preserved so the
`consumer-boot` CI gate on `main` is unchanged.

Assertions (shared across modes):
- **Clean-venv / not-workspace:** venv under `mktemp -d`; resolved package path
  must contain `site-packages`.
- **Public-names-only consumer:** imports `create_app` + `ProductAssemblySpec`
  only.
- **DB-free liveness:** boots with an unreachable `DATABASE_URL`, `/health` → 200.
- **Packaged assets resolve:** `templates/`, `static/` (incl. `static/fonts/`
  and, in release mode, `static/css/main.css`), `migrations/` all resolve from
  the installed location.

---

## Execution checklist (kernel-boundary Task 6)

1. **Re-verify** `packages/dotmac-kernel/pyproject.toml` `include` +
   `dependencies` still match the facts table (file is truth over this plan).
2. **Finalize metadata:** kernel `CHANGELOG` for `0.1.0a1`; bump the reference
   assembly to `0.9.0`; confirm the kernel version is `0.1.0a1`.
3. **Commit** `release-kernel.yml` (§3, actions pinned to reviewed SHAs),
   `scripts/inspect_dist.sh` (§4), and the `consumer_boot_check.sh` extension
   (§5) to `main` via PR; all eight required checks green.
4. **Configure** the `pypi-release` environment: `main` only, required reviewer
   Michael, no admin bypass, self-review permitted (§Environment configuration).
5. **[Michael] Create the pending trusted publisher** on pypi.org (§2 identity)
   from his individual Owner account (OpenBao path only) — do this **immediately
   before** step 6 (the name is not reserved until first upload).
6. **Dispatch** `Release kernel` with `version=0.1.0a1` on `main`. `build`
   inspects + smokes; **Michael approves** the `pypi-release` deployment;
   `publish` uploads via OIDC; `verify` installs from PyPI, smokes, and tags
   `dotmac-kernel-v0.1.0a1` on the published SHA.
7. **[Michael] Post-release:** add the second named Dotmac-controlled Owner on
   PyPI (§1); once a second release maintainer exists, enable
   `prevent_self_review` on `pypi-release`.

## Ratification record (2026-07-30)

- **R0-D1 — Registry:** RATIFIED — public PyPI, OIDC trusted publishing. Name
  `dotmac-kernel` confirmed `404`/unclaimed; pending publisher configured
  immediately before first upload, published promptly.
- **R0-D2 — Ownership:** RATIFIED — business owner Dotmac; initial Owner
  Michael's individual account (no shared account); add a second named
  Dotmac-controlled human Owner right after first release; optional later
  transfer to a Dotmac PyPI org.
- **R0-D3 — Trusted-publisher identity:** RATIFIED — owner `michaelayoade`, repo
  `dotmac_starter_mt`, workflow `release-kernel.yml` (display "Release kernel"),
  environment `pypi-release`, jobs `build`/`publish`/`verify`, `id-token: write`
  on `publish` only.
- **Workflow amendments:** RATIFIED — protected `workflow_dispatch`;
  exact-current-`main`-SHA gate; explicit `version` input matched to metadata;
  build-once/inspect/publish-same-bytes/verify-from-PyPI; tag only after
  verification; `pypi-release` main-only + Michael-approval, no admin bypass,
  self-review until a second maintainer; all actions pinned to reviewed full
  SHAs; deps refreshed (`pydantic[email]` + `testing`/`httpx` extra);
  `npm ci && npm run css:build` before build; inspection fails without
  `static/css/main.css`; font path `static/fonts/`; extend
  `consumer_boot_check.sh` rather than fork it.
