# Phase 2b.1: SOT-Composability Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Tasks respond 1:1 to Michael's post-merge review of 22192f6 (findings F1–F7, recorded below); each task cites its findings.

**Goal:** Close all seven review findings: a canonical feature/surface capability model driving mounting, navigation, and fragment composition; conflict handling that preserves RLS context; one email authority; per-request tenant branding; consumed visibility flags; CSRF-protected logout.

**Branch:** `phase2b1` off main (22192f6). PR-to-green-then-merge finish. Version 0.6.1 (breaking: user_credentials.email dropped — template repo, acceptable).

## The findings (authoritative statement, from Michael's review)

F1 [P1] DISABLED_FEATURES=web ≠ API-only (30 /admin routes remain; per-domain web routers unaffected). F2 [P1] two email authorities drift visibly (Party.email edited; login reads UserCredential.email). F3 [P1] db.rollback() on conflicts discards SET LOCAL tenant context; handlers then re-render/query context-less (500s/empty renders under RLS). F4 [P2] tenant branding only affects the editor preview. F5 [P2] disabled features leave dead nav links + broken fragments (custom_fields off → 404 inside party detail). F6 [P2] show_in_form/detail/list declared but never consumed (dead controls). F7 [P2] logout is a CSRF-exempt GET (forced-logout CSRF).

## Global Constraints

- All existing governance green every commit; integration via TEST_DB_PORT=5437 (5433/5434 production — never touch). Baselines: 386 unit+arch, 37 integration.
- USER RULES: everything by config/settings; SoT rubric (the capability model IS the SoT for surfaces — nav, mounting, fragments all derive from manifests; no parallel lists); template framing.
- New env knob(s) documented in .env.example; new behavior in CLAUDE.md/ARCHITECTURE.md with tests named.
- F3's canary MUST be RED against current main first (Postgres, FORCE RLS — the bug is invisible on SQLite).

### Task 1: Capability model — web surface switch + nav/fragment composition (F1, F5)

- `app/core/features.py`: `FeatureManifest` gains `web_routers: Sequence[APIRouter] = ()` and `nav: Sequence[NavItem] = ()` (`NavItem(label: str, path: str, feature: str-auto)` dataclass). `mount_features(app, *, manifests, disabled, web_enabled: bool)`: mounts `routers` always (for enabled features); mounts `web_routers` ONLY when `web_enabled`. Config: `web_enabled: bool = True` in Settings (`WEB_ENABLED` env; document: `false` = pure JSON API — no /admin, no static UI need remains but static mount stays harmless—NO: gate the StaticFiles mount on web_enabled too in main.py).
- Every feature moves its web router from `routers` to `web_routers`; auth's login/logout router included (API-only mode has NO login pages — cookie auth is meaningless without a web surface). The `web` shell feature keeps dashboard in web_routers; its manifest nav = [] (dashboard is the home link, hardcoded in sidebar? No — nav derives fully: web feature declares NavItem("Dashboard", "/admin")).
- Registry exposure to templates: `app/core/templating.py` gains process-static globals `enabled_features: frozenset[str]` + `nav_items: tuple[NavItem,...]` (set once at startup from the loaded manifests via a `install_surface_globals(manifests, disabled)` call in main.py — config is process-static so globals are correct; document).
- `templates/components/sidebar.html` renders from `nav_items` (delete the hardcoded link list). `templates/admin/parties/detail.html` wraps the custom-fields panel in `{% if 'custom_fields' in enabled_features %}`; document this as THE optional-slot pattern (ARCHITECTURE.md).
- Tests: API-only pin (`WEB_ENABLED=false` → zero /admin routes, zero static mount, JSON API intact — the F1 repro inverted); `DISABLED_FEATURES=custom_fields` → party detail 200 WITHOUT the panel div and sidebar lacks the entry; nav derives from manifests (add a temp manifest in-test → link appears). Update test_lifespan/mount tests + web-governance tests for the new manifest field. DISABLED_FEATURES=web keeps meaning "the web feature (dashboard) off"; WEB_ENABLED=false is the surface switch — document the distinction (CHANGELOG + .env.example + CLAUDE.md).
- Commit: `feat(core)!: manifest capability model — web_routers/nav; WEB_ENABLED surface switch (F1, F5)`

### Task 2: Savepoint conflict handling preserves RLS context (F3)

- Canary FIRST (`tests/test_conflict_rls_context.py`, Postgres): duplicate-email person edit via the WEB flow → currently 500/broken (RED against main); duplicate role grant via web → currently empty re-render (RED). Then fix; canary GREEN asserting: 200 re-render WITH correct data (grants list still populated; edit form re-renders with field error).
- Fix pattern (SoT — one helper): `app/core/db.py` (or crud.py) gains `with_conflict_savepoint(db)` context manager: `begin_nested()`; on IntegrityError roll back to the savepoint (outer transaction + SET LOCAL intact) and re-raise for the caller's ConflictError translation. Refactor EVERY service conflict site (parties create ×2/update ×2, rbac create_role/assign_role, tenants create, auth register, custom_fields create — grep `db.rollback()` for the inventory; each site: nested-savepoint + NO bare rollback). Add the convention to CLAUDE.md hard rules (named canary as enforcement).
- Commit: `fix(core)!: conflict paths use savepoints — RLS tenant context survives ConflictError (F3)`

### Task 3: One email authority — drop UserCredential.email (F2)

- Migration: drop `user_credentials.email` (+ its unique constraint); login flow: `auth/service.py::login` resolves Party by `normalize_email(email)` + tenant (core model — legal), then UserCredential by party_id. Register: no email written to credentials. All flows atomic by construction: the parties email edit IS the login email change.
- Person-party email NULLing: now blocks login entirely — add guard: `update_person_party` may not null email... cannot check credentials (independence). Resolution: login of a party with NULL email is simply impossible (query by email finds nothing) — acceptable & document; the backlog drift entry is RESOLVED (strike with rationale); ARCHITECTURE ownership table: email owner = parties/auth via core identity, single column.
- Canaries: login after portal email change uses the NEW email (RED-first against main where old email still works); old email 401s; cross-tenant same-email unaffected. Update existing auth canaries.
- Commit: `fix(auth)!: Party.email is the single email authority — credential copy removed (F2)`

### Task 4: Per-request tenant branding (F4)

- `app/core/web_deps.py` (or templating): request-scoped branding — `render()` gains optional auto-enrichment: a `get_request_branding(request, db)` helper resolving `load_branding(db, tenant.id)` once per request (cache on `request.state`), injected as `brand` context override for web renders (login + error pages included — tenant is host-resolved pre-auth; platform/no-tenant contexts fall back to static get_brand()). Key-allowlist tightening from the final review folded in (merge only known brand keys).
- Tests: portal page reflects saved ui_branding (name/color in sidebar); login page branded per tenant; unknown-host error page falls back to static; the one-DB-read-per-request cost documented (settings cache remains phase-3).
- Commit: `feat(web): tenant branding portal-wide, resolved once per request (F4)`

### Task 5: Consume visibility flags + logout POST (F6, F7)

- values-panel renders only `show_in_form` fields (edit mode) and `show_in_detail` (read rows if the panel has a read varian—implement: panel form filters show_in_form; party detail read section filters show_in_detail); definitions `_table.html` honors `show_in_list` for a "visible in lists" badge AND the future list-column story documented; service list_for_entity gains optional `visible_in: Literal["form","detail","list"] | None` filter (single query-level owner). Tests per flag.
- Logout: `POST /admin/logout` (hx-post button in topbar, CSRF bridge applies); GET /admin/logout removed (breaking, changelog); allowlist updated (logout POST carries require_tenant only — still allowlisted from auth-tier with comment, matching login).
- Commit: `fix(web): visibility flags consumed; logout is a CSRF-protected POST (F6, F7)`

### Task 6: Docs + v0.6.1 + final review + PR

- CLAUDE.md (capability model as THE extension point for surfaces; conflict-savepoint hard rule; WEB_ENABLED), ARCHITECTURE.md (capability model section; email single-authority; per-request branding; optional-slot pattern), README (API-only mode for real now), CHANGELOG 0.6.1 (BREAKING: credential email column dropped, logout GET removed, manifest signature), .env.example (WEB_ENABLED), backlog reconciliation (strike F1-F7 trackers incl. the UserCredential.email entry with resolution rationale).
- `make bump-version part=patch` → 0.6.1. Full gate + docker smoke. Whole-branch final review scoped to "do the seven findings' fixes hold, coherently" then PR → green → merge.

## Completion criteria
All seven findings have a test that would catch regression (F1 pin, F3+F2 canaries, F5 composition tests, F6 flag tests, F7 allowlist/POST test, F4 branding tests); suites green; PR merged.
