# Phase 2b: Working Admin Portal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A clickable, branded, tenant-scoped admin portal (Jinja2 + HTMX + Alpine + Tailwind v4): login/logout, dashboard, and functional screens for parties, roles/grants, audit log, settings, and custom fields — plus HTML error negotiation and the branding pipeline.

**Architecture:** Web routes live IN feature packages (`web.py` next to `router.py`, added to each manifest — thin-wrapper and guard governance already scan `web.py`). A new `web` feature package owns the cross-feature shell (login/logout, dashboard). Templates are central (`templates/`, per-feature subdirs — single Jinja loader, documented). Web auth = HttpOnly `access_token` cookie riding the EXISTING auth service + `AuthSession`, validated by a shared core helper (one validation path for API and web — SoT), with redirect-to-login semantics for HTML. Branding = `brand.json` Jinja global (sub's install pattern) + DB `ui_branding` override (consumes the last orphan-allowlist entry).

**Tech Stack:** as phase 2a + Jinja2, htmx, Alpine.js (CSP build), Tailwind v4 CLI (npm), vendored JS assets.

**Spec:** `docs/superpowers/specs/2026-07-17-starter-consolidation-design.md` (phase 2 amendment: working admin portal pulled forward)
**Port intel:** the explorer findings recorded in this plan's task bodies; sources `ST:` = `/home/dotmac/projects/dotmac_starter`, `SUB:` = `/home/dotmac/projects/dotmac_sub`.

## Global Constraints

- Branch `phase2b` off `main`. PR-to-green-then-merge finish (repo: github.com/michaelayoade/dotmac_starter_mt, CI must pass incl. docker health gate).
- USER RULES: everything by config; SoT rubric (one canonical definition, enforced derivation, extension points over edits); reusable-template framing (no fleet narrative; extension points documented); SOT-complete criteria (routes validate/authorize/delegate only; new projections need owner+repair story).
- All 2a governance stays green every commit: thin wrappers (scans `web.py` too — NO direct queries in web routes), route guards (every route carries `require_*` or allowlisted with comment), manifests, contract-sync, no-orphan-settings (shrink-only), Any-ban, `make check`, unit+arch suite, canaries.
- Feature independence: a feature's `web.py` imports its OWN service + core only. The `web` shell feature imports core only (dashboard counts query CORE models directly is FORBIDDEN by thin-wrapper — dashboard gets a tiny `app/features/web/service.py` for its queries).
- Tenancy: every web route is tenant-scoped via the existing resolver; web session validation must enforce tenant match (AuthSession keyed tenant_id+party_id).
- CSRF: MT middleware validates the `x-csrf-token` HEADER against the JS-readable `csrf_token` cookie (double-submit). All browser mutations go through the JS header bridge — hidden form inputs alone DO NOT work. Never weaken the middleware.
- Templates escape by default; any `| safe` needs a sanitization comment (branding custom_css goes through the ported sanitizer).
- Compiled CSS (`static/css/main.css`) is gitignored; `npm run css:build` generates it; Dockerfile + CI build it; `make dev` docs mention it.
- Integration runs: `TEST_DB_PORT=5437` (5433/5434 are production — never touch). Baseline at branch start: 32. Unit+arch baseline: 188.
- Version bump to 0.6.0 in the final task.

---

### Task 1: Web foundation — Jinja setup, base shell, Tailwind build, CSRF bridge

**Files:**
- Create: `app/core/templating.py` (Jinja2Templates factory: installs globals `brand` (Task 2 fills; stub `{}` now), `static_asset_url` cache-busting helper ported from `ST:app/templates.py`, `current_year`), `templates/base.html`, `templates/layouts/admin.html`, `templates/components/{sidebar,topbar,form_macros,table_macros}.html`, `static/css/src/main.css`, `static/js/{htmx.min.js,alpine.min.js,components.js,csrf.js}` (vendor htmx+alpine from `ST:static/js/` — copy the exact files), `package.json`, `tailwind.config.js` (or v4 CSS-config — copy `SUB:` setup incl. `css:build`/`css:watch` scripts and the safelist convention), `.gitignore` (+`static/css/main.css`, `node_modules/`)
- Modify: `Dockerfile` (node stage or npx step to run `css:build` before COPY — pick multi-stage: node:20-slim builds CSS, final stage copies `static/`), `.github/workflows/ci.yml` docker-build unaffected (image build now includes CSS); `Makefile` (`css-build`, `css-watch` targets; `dev` docs)

**Interfaces:**
- Produces: `app.core.templating.templates` (Jinja2Templates singleton) + `render(request, name, ctx)` helper ALL web routes use; base template blocks `title/head/body/content/scripts`; `layouts/admin.html` block `admin_content` + context contract `{active_nav: str, page_title: str}`; macros `form_macros.{text_input,textarea,select_field,checkbox,submit_button}`, `table_macros.{table_header,empty_state,action_buttons,pagination,status_badge}` (ported from `ST:templates/admin/components/`, adapted: action_buttons drops the hidden csrf input — mutations rely on the header bridge).
- `static/js/csrf.js`: reads the `csrf_token` cookie (JS-readable by design) → sets `X-CSRF-Token` on `htmx:configRequest` AND monkey-patched `fetch` AND plain form submits (intercept submit, inject via fetch or convert to hx-post — simplest: all mutating forms in our templates use `hx-post`, so the htmx hook covers them; document that plain `<form method=post>` is NOT supported without hx-boost).

- [ ] Step 1: Tailwind build works: `npm install && npm run css:build` produces `static/css/main.css`; committed files exclude it.
- [ ] Step 2: templating + base shell + macros; smoke unit test: a trivial route rendered via TestClient returns 200 text/html containing the sidebar nav and the csrf meta/script tags (`tests/unit/test_web_foundation.py`).
- [ ] Step 3: Dockerfile CSS stage; `make docker-build` + existing smoke still green locally.
- [ ] Step 4: Full gates; commit `feat(web): jinja foundation, admin shell, tailwind v4 build, csrf header bridge`

---

### Task 2: Branding pipeline + HTML error negotiation

**Files:**
- Create: `brand.json` (template defaults: name "Starter", generic tagline/colors), `app/core/branding.py` (port `SUB:app/services/branding_config.py` `get_brand()` — defaults < brand.json < env, lru_cache, `BRAND_CONFIG_PATH` override; PLUS `ST:app/services/branding.py`'s DB override: `load_branding(db, tenant_id)` merging `resolve_value(db, SettingDomain.branding, "ui_branding", tenant_id=...)` over the static brand; port the CSS sanitizer (`sanitize_branding_css`) verbatim), `templates/errors/{400,401,403,404,409,422,500,csrf}.html` (adapt `SUB:templates/errors/*` shape onto our base.html)
- Modify: `app/core/templating.py` (install `brand` global = `get_brand()`), `app/core/errors.py` (content negotiation: if request prefers text/html (Accept header contains text/html and not an HX-Request JSON case — HTMX requests get HTML fragments too, they accept html) render `errors/<status>.html` with the envelope fields in context; else JSON envelope as today — `_envelope()` stays the single source), `app/core/middleware/csrf.py` (failure response goes through the same negotiation: HTML → `errors/csrf.html`; keep JSON for API), `tests/architecture/test_no_orphan_settings.py` (REMOVE `ui_branding` from allowlist — now consumed; shrink-only test enforces)

**Interfaces:**
- Produces: `get_brand() -> dict` (static, cached), `load_branding(db, tenant_id) -> dict` (static + DB override, per-request), `render_error(request, status, envelope) -> HTMLResponse`; every template can use `{{ brand.name }}` etc.

- [ ] Step 1: TDD branding resolution (defaults/env/brand.json precedence; DB override merge; CSS sanitizer port with its test cases from ST if present).
- [ ] Step 2: TDD negotiation: API client (Accept: application/json) gets envelope JSON; browser Accept gets branded HTML containing request_id; CSRF failure HTML page; unit tests extend `tests/unit/test_errors.py`.
- [ ] Step 3: orphan allowlist shrinks to EMPTY; gates; commit `feat(web): brand.json + ui_branding override, branded HTML error negotiation`

---

### Task 3: Web auth — shared validation, login/logout, dashboard (`web` feature)

**Files:**
- Create: `app/features/web/{__init__.py,feature.py,web.py,service.py}`, `app/core/web_deps.py` (`require_web_auth`, `WebAuthRedirect`), `templates/auth/login.html`, `templates/admin/dashboard.html`
- Modify: `app/core/deps.py` (extract the token+session+party validation body of `require_user_auth` into a shared `authenticate_request(request, db) -> Party | None` — ONE validation path (SoT); `require_user_auth` keeps its exact signature/behavior wrapping it), `app/core/errors.py` or main (register `WebAuthRedirect` handler → 302 `/admin/login?next=<safe>`), `app/features/__init__.py` + pyproject contract (add `web`), `app/features/auth/service.py` ONLY IF login needs a cookie-issuing variant (prefer reusing `login()` unchanged and setting the cookie in the web route)

**Interfaces:**
- Consumes: auth service `login()` (returns access token; AuthSession created — READ the service first for exact shape), `authenticate_request` shared helper.
- Produces: `require_web_auth(request, db) -> dict {party, roles}` — validates via `authenticate_request` reading the `access_token` COOKIE (fallback: none — web only), requires party_type person + role "admin" (sub's default-deny shape; comment: loosen per-portal in phase 3), raises `WebAuthRedirect(next_url)` on any failure; `GET/POST /admin/login` (unguarded — ALLOWLIST with comment; `_safe_next_url` port from `ST:app/web/auth.py` blocking open redirects), `GET /admin/logout` (revokes session, clears cookie), `GET /admin` dashboard (counts via `web/service.py`: parties count, roles count, active definitions count — core-model queries live in the service). Cookie: `access_token`, HttpOnly, SameSite=lax, Secure when forwarded-https (port `_is_secure_request`), max_age from `settings.jwt_ttl_seconds` (config, not literal).
- Manifest: `FeatureManifest(name="web", routers=[web_router], core=False)` — the portal is deletable/disable-able (template promise); with it disabled, API-only operation must stay fully green.

- [ ] Step 1: TDD shared-helper refactor (existing auth unit+integration tests unchanged = proof of no drift).
- [ ] Step 2: TDD login flow unit tests (app-builder + unit engine): GET login 200; POST bad creds re-renders with error (200, no cookie); POST good creds → 302 next + HttpOnly cookie; guarded page without cookie → 302 login?next=; with cookie → 200; org-party or non-admin → 302 (never 500). `_safe_next_url` cases (`//evil`, `http://`, `/ok`).
- [ ] Step 3: Postgres canary (`tests/test_web_auth_isolation.py`): login on tenant A's host; cookie replayed against tenant B's host → redirect to login (session tenant mismatch), no data rendered.
- [ ] Step 4: DISABLED_FEATURES=web smoke (import + api tests green); gates; commit `feat(web): cookie web auth via shared validation path, login/logout, dashboard`

---

### Task 4: Parties screens (list/create/detail/delete)

**Files:**
- Create: `app/features/parties/web.py`, `templates/admin/parties/{index.html,_table.html,create.html,detail.html}`
- Modify: `app/features/parties/feature.py` (append web router to manifest)

**Interfaces:**
- Consumes: parties service (existing functions only), `require_web_auth`, macros, `render`.
- Produces routes under `/admin/parties`: index (search + party_type filter, HTMX `_table.html` fragment pattern — `hx-get` with `delay:350ms` trigger, `hx-target="#parties-table"`, filters threaded through query params, server picks fragment vs full page by `HX-Request` header — port the `SUB:templates/admin/customers/` shape), create forms for person/org (hx-post → redirect or fragment), detail (subtype fields + custom-fields values readonly section placeholder for Task 7), delete (hx-post via action_buttons, confirm dialog). Pagination via existing service limit/offset.
- Service additions allowed ONLY in parties service and ONLY if a screen needs a query shape that doesn't exist (e.g. `search_parties(db, q, party_type, limit, offset)` — name it, test it; explicit tenant filter + RLS comment per convention).

- [ ] TDD unit web tests per route (auth-gated, fragment vs full render, search filters); gates; commit `feat(parties): admin screens with HTMX table fragments`

---

### Task 5: Party edit + display_name single-writer resolution (SOT criterion 3)

**Files:**
- Create: `templates/admin/parties/edit.html`
- Modify: `app/features/parties/service.py` (NEW `update_person_party(db, party_id, payload)` / `update_organization_party(...)` — subtype fields + email (lowercased — reuse/extract the shared normalize helper: create `app/core/identity.py::normalize_email` and refactor BOTH auth register and parties creates to use it — closes the dual-writer invariant with a shared owner), **display_name recomputed inside these update functions from the updated subtype fields — they are now the SINGLE write-owner of the projection**; add `recompute_display_name(party) -> str` used by create AND update paths), `app/features/parties/web.py` + `schemas.py` (`PersonPartyUpdate`, `OrganizationPartyUpdate`), `docs/ARCHITECTURE.md` ownership table (display_name row: owner = parties service recompute; drift repair = re-save), `docs/superpowers/phase2-backlog.md` (strike the display_name gap)

- [ ] TDD: update recomputes display_name; email uniqueness conflict on update → ConflictError; entity_type/subtype immutability (person party can't become org); web edit screen round-trip. Existing canaries green. Commit `feat(parties): edit flows; display_name has a single write-owner (SOT gap closed)`

---

### Task 6: RBAC + audit screens

**Files:**
- Create: `app/features/rbac/web.py`, `templates/admin/rbac/{roles.html,_roles_table.html,role_create.html,grants.html,audit.html,_audit_table.html}`
- Modify: `app/features/rbac/feature.py`

**Interfaces:** routes `/admin/roles` (list+create), `/admin/role-grants` (grant form: party dropdown fed by... rbac web CANNOT import parties service — feature independence. The grant form takes a party lookup via the CORE models the rbac service already queries — add `rbac/service.py::list_grantable_parties(db, q)` querying core `Party` directly (legal: core import), documented), `/admin/audit` (retention-filtered list via existing `list_audit_events`, paginated — add limit/offset to that service function now, plus the existing default cap; note it was the last unpaginated list).

- [ ] TDD per route; gates; commit `feat(rbac): roles, grants, audit screens`

---

### Task 7: Settings + custom-fields screens

**Files:**
- Create: `app/features/settings/web.py`, `templates/admin/settings/{index.html,edit.html,branding.html}`, `app/features/custom_fields/web.py`, `templates/admin/custom_fields/{index.html,_table.html,form.html}`, party-detail values partial `templates/admin/parties/_custom_fields.html`
- Modify: both `feature.py` manifests; `app/features/parties/web.py` detail route (values section renders definitions + current values via… parties web CANNOT import custom_fields service. Resolution per SoT/extension-point: custom_fields `web.py` owns a FRAGMENT route `GET /admin/custom-fields/party/{party_id}/values-panel` returning the partial; the parties detail template pulls it with `hx-get` lazy-load (`hx-trigger="load"`) — features stay independent, composition happens in the browser. Document this as the cross-feature UI composition pattern in ARCHITECTURE.md (Task 8).)

**Interfaces:**
- Settings screens: grouped-by-domain list with effective values + source badges (reuse the service's list path — masking already server-side), edit per key with validation errors re-rendered inline, `branding.html` = friendly editor for the `ui_branding` json (fields: display_name, tagline, logo_url, colors, custom_css — writes via existing `update_setting`; custom_css preview sanitized).
- Custom-fields screens: definitions list per entity_type (HTMX fragment), create/edit form (field_type select drives conditional option inputs via Alpine; entity_type select fed from the registry via a tiny service helper `list_entity_types()` exposing ENTITY_MODELS keys), deactivate action; values panel fragment (form generated from definitions; PUT via hx; validation errors inline).

- [ ] TDD per route incl. the values-panel fragment (auth, tenant isolation via unit overrides); gates; commit `feat(web): settings + custom-fields screens; cross-feature UI via fragment composition`

---

### Task 8: Web governance + e2e portal canary + CI

**Files:**
- Modify: `tests/architecture/test_route_guards.py` (web routes: `require_web_auth` counts as guard — verify by name prefix `require_`; ALLOWLIST adds `GET/POST /admin/login` with comments), NEW `tests/architecture/test_web_conventions.py` (every `templates/admin/**/*.html` extends a layout or is `_`-prefixed fragment; every mutating hx- attribute in templates is hx-post/hx-put/hx-delete — no method-override forms; every web.py route module imports only its own feature + core (belt-and-suspenders beside import-linter))
- Create: `tests/test_admin_portal_e2e.py` (Postgres): full clickthrough per tenant — register admin via API, web login, dashboard 200, create person party via web form (CSRF header via cookie), define custom field via web, set value via values panel, list settings, tenant B sees none of it (fresh login on B's host), logout kills the cookie.

- [ ] Governance tests RED-run against a seeded violation (temporarily, to prove sensitivity — revert), then green; e2e canary green; full gate incl. `make docker-build` + smoke; commit `test(web): portal governance + end-to-end canary`

---

### Task 9: Docs + v0.6.0

**Files:** `CLAUDE.md` (web rules: web.py in features, fragment composition pattern, CSRF header bridge contract, template escaping rule), `docs/ARCHITECTURE.md` (portal section: auth flow diagram-in-prose, branding pipeline, composition pattern; ownership table additions), `README.md` (portal quickstart: css:build, login, screenshots deferred; template-first voice), `CHANGELOG.md` (0.6.0), `make bump-version part=minor`, backlog updates (strike delivered; add discovered).

- [ ] Doc-vs-code verification greps as 2a-T11; full gate + one integration cycle; commit `docs: admin portal; v0.6.0`

---

## Completion criteria (phase-2b gate)

- All 2a governance + new web governance green; e2e portal canary green; docker health gate green (CSS built in image).
- Portal clickable end-to-end per tenant with branding applied; API-only mode (`DISABLED_FEATURES=web`) fully green.
- Orphan-settings allowlist EMPTY. display_name projection has a single write-owner (ownership table updated).
- PR to main, CI green, merge (established finish).

Phase 2c (auth hardening: MFA/TOTP, refresh rotation, password reset, lockout, API keys — then archive dotmac_starter) follows.

## Scope deviation (controller, flagged to user)
The original 2b scope listed "tenants" screens. Tenant CRUD is PLATFORM-scoped (require_platform,
no tenant context) while this portal is tenant-subdomain-scoped — a tenant admin must not manage
tenants. Deferred: a platform-admin surface is a phase-3 candidate (backlogged). Override if needed.
