# Kernel Boundary Implementation Plan (program workstream 2 + fake-provider kit)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Delivers milestone 1's non-security half per `docs/superpowers/reviews/2026-07-18-kernel-program-directive.md`: publishable kernel boundary, ProductAssemblySpec, empty-assembly boot proof, fake-provider contract-test kit. Task 0 (boundary audit) generates the facts every later task consumes — its output doc is authoritative over this plan's assumptions; re-plan task briefs against it at execution.

**Goal:** `dotmac_starter_mt` becomes a publishable kernel package plus a reference application assembly, proven by an empty assembly that boots without copying source.

**Architecture:** Monorepo split: `packages/dotmac-kernel/` holds the kernel (today's `app/core` — config, db/RLS, models base + identity models, security, platform auth, deps/guards, middleware stack, errors, templating, settings resolver, features registry, audit); the repo root `app/` becomes the REFERENCE ASSEMBLY (feature packages + a `ProductAssemblySpec` + thin `main.py` calling `dotmac_kernel.create_app(spec)`). The public import surface is explicit and governance-tested — the reference app itself may only import supported public names, making the repo its own first consumer. `ProductAssemblySpec` declares what an assembly IS (modules, providers, settings overrides, branding, deployment profile ref — forward-compatible with the ModuleManifest expansion of workstream 3). The testing kit (`dotmac_kernel.testing`) ships the assembly harness + fake providers so consumers contract-test without a real Postgres or real providers.

**Tech Stack:** Poetry path dependency (workspace-style) now; PyPI-able metadata from day one (distribution workstream 6 does the actual publishing). No new runtime deps.

## Global Constraints

- **Sequencing: branch `kernel-boundary` off main AFTER the control-plane plan (v0.8.0) merges** — the milestone is a SECURE kernel prerelease; packaging an insecure platform surface is the wrong artifact. Kernel package version starts `0.1.0a1`; repo version 0.9.0.
- Proposed names (defaults — Michael may override at Task 1 review): distribution `dotmac-kernel`, import `dotmac_kernel`, testing extra `dotmac_kernel.testing`. Reference assembly keeps `app/` (it is a consumer, not a library).
- All existing governance suites stay green through the split — the import-linter contracts and architecture tests are re-pointed, never weakened; every rename lands with its test updated in the same commit.
- Stronger SoT rule applies: the kernel's public surface is ONE list (`dotmac_kernel.__all__` + documented public modules), consumed by the compat policy doc, the governance test, and the API reference — no second hand-maintained list.
- USER RULES: everything by config; template framing (the reference app demonstrates extension points; kernel docs never assume DotMac-fleet context outside ADRs).
- Integration DB ports: 5433/5434 production — never touch; use TEST_DB_PORT=5438+ in worktrees.

## File Structure (target)

- `packages/dotmac-kernel/pyproject.toml` + `packages/dotmac-kernel/src/dotmac_kernel/` (kernel code, moved from `app/core/`)
- `packages/dotmac-kernel/src/dotmac_kernel/testing/` (harness + fakes)
- `packages/dotmac-kernel/COMPATIBILITY.md` (public surface + version policy)
- Root `pyproject.toml` gains `dotmac-kernel = {path = "packages/dotmac-kernel", develop = true}`
- `app/assembly.py` (the reference `ProductAssemblySpec`), `app/main.py` (thin: `create_app(assembly)`)
- `tests/architecture/test_kernel_boundary.py` (public-surface governance)
- `.github/workflows/ci.yml` new job `consumer-boot` (empty-assembly proof)

---

### Task 0: Boundary audit (read-only; produces the authoritative surface doc)

- [ ] Inventory every `app.core.*` symbol imported by `app/features/**`, `app/main.py`, `alembic/`, `tests/` (AST-based script, committed as `scripts/audit_kernel_surface.py` so it can re-run): symbol, importing module, count. Output: `docs/superpowers/reviews/2026-07-18-kernel-surface-audit.md` — (a) the de-facto public surface (candidates for `__all__`), (b) internals leaked to consumers (candidates to wrap or make public deliberately), (c) coupling hotspots (e.g. templates/ directory ownership, static assets, alembic env — things that are NOT Python imports but still kernel-vs-assembly boundary questions: who owns `templates/base.html`? migrations for kernel tables vs module tables?).
- [ ] Decide + record in the audit doc: migration ownership model for milestone 1 (single alembic tree in the reference app, kernel ships its migration files as package data consumed by the assembly's alembic env — full plugin-migration orchestration is workstream 4/directive scope, NOT here); template/static ownership (kernel ships base layout + component macros as package data; assembly can override by path precedence — mirror Jinja `ChoiceLoader`).
- [ ] Commit the audit doc + script. No production code changes.

### Task 1: Package split

- [ ] Create `packages/dotmac-kernel` (src layout, own pyproject: name `dotmac-kernel`, version `0.1.0a1`, python `>=3.12,<3.14`, deps = the kernel's actual runtime deps only — derived from the audit, NOT a copy of the app's list).
- [ ] `git mv app/core packages/dotmac-kernel/src/dotmac_kernel` (history-preserving), then mechanical import rewrite `app.core.` → `dotmac_kernel.` across the repo (script the rewrite; commit the script's diff in one commit so review is tractable). Root pyproject gets the path dependency (`develop = true` so the monorepo stays one `poetry install`).
- [ ] Re-point import-linter: "Core must not import features" becomes "dotmac_kernel must not import app" (now structurally impossible via packaging — keep the contract anyway as belt-and-braces); "Features are independent" unchanged. Update the contract-sync architecture test.
- [ ] Templates/static per Task 0's decision (kernel package data + ChoiceLoader override precedence; `render()` unchanged for consumers).
- [ ] Full gates green (unit+arch, integration on 5438, docker build — Dockerfile's poetry install must handle the path dep; fix in same task).

### Task 2: Public surface + compatibility policy

- [ ] `dotmac_kernel/__init__.py` exports the audited public surface; every public module documented in `COMPATIBILITY.md`: what is supported (`dotmac_kernel`, `dotmac_kernel.testing`, `dotmac_kernel.models`, ...), what is internal (`dotmac_kernel._*` — rename leaked internals from the audit), the version policy (SemVer; 0.x = minor may break with CHANGELOG migration notes; `contract_version` field reserved for the ModuleManifest expansion), and the deprecation rule (one minor version with a `DeprecationWarning` minimum).
- [ ] Governance (AST-based): `tests/architecture/test_kernel_boundary.py` — `app/**` (the reference assembly) imports ONLY names reachable from the documented public surface; kernel internals (`_`-prefixed) unimported outside the kernel. Sensitivity-prove with a temporary private import.

### Task 3: ProductAssemblySpec + create_app

- [ ] `dotmac_kernel.assembly.ProductAssemblySpec` (frozen dataclass): `name`, `modules: Sequence[FeatureManifest]` (accepts today's manifests; field named `modules` for forward-compat with ModuleManifest), `settings_overrides: Mapping[str, object]`, `branding: BrandSpec | None`, `providers: Mapping[str, object]` (interface-keyed, empty today — the seam workstream 5 fills), `web_enabled`, `disabled_modules`.
- [ ] `dotmac_kernel.create_app(spec: ProductAssemblySpec) -> FastAPI`: everything `app/main.py` does today (middleware stack, error handlers, mounting, surface globals, platform auth surface, lifespan/seed) driven from the spec instead of module-level imports of `app.features`. `app/main.py` shrinks to: build the reference `ProductAssemblySpec` (in `app/assembly.py`, listing the seven reference modules) + `app = create_app(assembly)`. `FEATURE_MODULES` string list becomes the reference assembly's concern, not the kernel's.
- [ ] Existing behavior byte-identical: full suite green, route inventory diffed empty (dump `app.routes` before/after — commit the proof).

### Task 4: Empty-assembly boot proof

- [ ] Unit-level: `create_app(ProductAssemblySpec(name="empty", modules=()))` boots under TestClient — `/health` 200; platform auth routes present (kernel surface, always); zero module routes; zero nav. Pin as `tests/unit/test_empty_assembly.py`.
- [ ] Consumer-level (the real proof — "without copying source"): CI job `consumer-boot`: `poetry build` the kernel wheel; in a clean temp venv, `pip install` the wheel; generate a 20-line consumer project (its own `main.py` building an empty `ProductAssemblySpec` — written by the CI script, importing only public names); boot with a deliberately-unreachable DATABASE_URL; poll `/health` 200 (the DB-free liveness invariant already proven by the docker-build job pattern). This job is the milestone's acceptance test.

### Task 5: Fake-provider contract-test kit (`dotmac_kernel.testing`)

- [ ] Harness: `assembly_test_client(spec, *, db_url="sqlite in-memory") -> TestClient` (what tests/unit/conftest.py hand-builds today, packaged: create_all, tenant-inject middleware stand-in, dependency overrides, surface globals) — then REFACTOR the repo's own unit conftest to consume it (the kit's first consumer is this repo; no parallel harness maintained).
- [ ] Fakes for every provider seam that EXISTS at milestone 1 (from Task 3's `providers` mapping + audit): at minimum `FakeClock`, `FakeSeeder`, in-memory `RateLimitStore` (the Task-5 control-plane contract), fake branding loader. Do NOT invent fakes for provider interfaces that don't exist yet (workstream 5) — the kit grows with the seams (contracts-not-implementations rule).
- [ ] Contract tests consumers can run: `dotmac_kernel.testing.contract` — parametrizable suites asserting a provider implementation honors its interface (pattern established with RateLimitStore: run the same suite against the memory store and any consumer-supplied store).
- [ ] Kit documented in COMPATIBILITY.md as public surface.

### Task 6: Docs + version + final review + PR

- [ ] README restructure (kernel vs reference assembly framing; consumer quickstart = install kernel, write assembly), ARCHITECTURE.md boundary section + ownership rows, CHANGELOG (repo 0.9.0; kernel 0.1.0a1 gets its own CHANGELOG in the package), CLAUDE.md/AGENTS.md pointer updates.
- [ ] Final whole-branch review (most capable model): boundary honesty (no `app.*` import inside kernel, no undocumented public use), empty-assembly proof actually proves no-source-copying, kit consumed by own repo, migration/template ownership decisions coherent with the workstream-3/4 directives.
- [ ] PR → green → merge. Milestone 1 completion statement in the ledger + knowledge server (milestone = this + merged control-plane + tagged prerelease, which workstream 6 cuts).

## Completion criteria (maps to the milestone definition)

- Secure kernel: control-plane plan merged FIRST (prerequisite, not this plan's work).
- ProductAssemblySpec: exists, drives both the reference app and the empty assembly.
- Empty assembly boots: consumer-boot CI job green — wheel install, no source copy, /health 200.
- Fake provider test kit: `dotmac_kernel.testing` public, consumed by this repo's own suite, contract suites runnable by consumers.
- All existing governance + isolation suites green throughout; kernel surface governance sensitivity-proven.
