# F0 — `dotmac_starter_mt` UI-surface, template, CSS, static-asset & navigation inventory

**Repo:** `/Users/michaelayoade/Downloads/management/dotmac_starter_mt`
**Kernel package:** `/Users/michaelayoade/Downloads/management/dotmac_starter_mt/packages/dotmac-kernel/src/dotmac_kernel/`
**Captured:** 2026-08-02, on `feat/kernel-module-registry` @ `76218e3` (parent `main` @ `1df5f4a`).
The F1 module-registry commit on this branch changes no route, template, or static asset, so the
route/template/static tables below are equally true of `main`.
**Method:** route table walked from the live `app.main:app` object
(`DATABASE_URL="postgresql+psycopg://u:u@127.0.0.1:59999/u" poetry run python …`, no DB needed);
everything else enumerated mechanically with `find`/`grep`/`git ls-files`. **Read-only — no file modified.**

**Headline structural fact:** the assembly (`app/`) ships **zero** templates and **zero** static
assets. `find` for `templates/`, `static/`, `*.html`, `*.css`, `*.js` under the repo root and under
`app/` returns nothing. 100% of the UI (34 templates, 15 static files) is **kernel package data** at
`packages/dotmac-kernel/src/dotmac_kernel/{templates,static}/`, resolved by package path
(`templating.py:67-69`, anchored on `Path(__file__).parent`, deliberately *not* CWD). The assembly's
`ProductAssemblySpec` (`app/assembly.py:23-28`) leaves `assembly_template_dir` / `assembly_static_dir`
**unset**, so the reference app renders the kernel's own look with no override layer active.

---

## 1. Route inventory

**Total mounted routes: 65** (63 `APIRoute` + 1 `Mount` + the 4 FastAPI-builtin doc routes counted
among the 65). Guard column shows the **full recursive dependency-callable set** as walked from
`route.dependant` (so `require_role(...)`'s closure is shown as `_dependency({'role_slug': …})`,
and its own `require_user_auth` sub-dependency is shown alongside it).

Surface classes: **INFRA** = framework/liveness/static; **PLATFORM** = control plane
(`/platform/*`, no tenant, platform-host-exact); **API** = tenant JSON API; **WEB** = HTML/HTMX admin
portal (`/admin/*`).

### 1.1 Infra / framework (owner: FastAPI + `dotmac_kernel.app_factory`)

| METHOD | Path | Owner | Class | Guards |
|---|---|---|---|---|
| GET, HEAD | `/openapi.json` | fastapi (builtin) | INFRA | *(none)* |
| GET, HEAD | `/docs` | fastapi (builtin) | INFRA | *(none)* |
| GET, HEAD | `/docs/oauth2-redirect` | fastapi (builtin) | INFRA | *(none)* |
| GET, HEAD | `/redoc` | fastapi (builtin) | INFRA | *(none)* |
| *(Mount)* | `/static` | kernel `app_factory` (`StaticFiles`/`LayeredStaticFiles`) | INFRA | *(none)* — mounted only when `WEB_ENABLED=true` |
| GET | `/health` | kernel `app_factory` | INFRA | *(none — deliberately DB-free liveness)* |

Notes: `/openapi.json` + `/docs` are **unguarded and unconditioned on `WEB_ENABLED`** — they publish the
full API shape (and the app title, see §6) to anyone who can reach the host.

### 1.2 Platform control plane (PLATFORM)

| METHOD | Path | Owner | Class | Guards |
|---|---|---|---|---|
| POST | `/platform/auth/login` | kernel `platform_auth` | PLATFORM | `require_platform_host`, `get_platform_db` |
| POST | `/platform/auth/logout` | kernel `platform_auth` | PLATFORM | `require_platform_admin`, `get_platform_db` |
| POST | `/platform/tenants` | `app.features.tenants.router` | PLATFORM | `require_platform_admin`, `get_platform_db` |
| GET | `/platform/tenants` | `app.features.tenants.router` | PLATFORM | `require_platform_admin`, `get_platform_db` |
| GET | `/platform/tenants/{tenant_id}` | `app.features.tenants.router` | PLATFORM | `require_platform_admin`, `get_platform_db` |

**The entire platform control plane is JSON-only.** There is no `/platform/*` HTML surface, no platform
login page, no platform template. 5 routes total.

### 1.3 Feature: `auth` (`app/features/auth/`)

| METHOD | Path | Class | Guards |
|---|---|---|---|
| POST | `/auth/register` | API | `require_tenant`, `get_db` *(pre-auth — in `MUTATING_ALLOWLIST`)* |
| POST | `/auth/login` | API | `require_tenant`, `get_db` *(pre-auth — allowlisted)* |
| GET | `/auth/me` | API | `require_user_auth`, `require_tenant`, `get_db` |
| GET | `/admin/login` | WEB | `require_tenant`, `get_db` *(pre-auth by design)* |
| POST | `/admin/login` | WEB | `require_tenant`, `get_db` *(pre-auth — allowlisted)* |
| POST | `/admin/logout` | WEB | `require_tenant`, `get_db` *(allowlisted; F7 fix made it POST + CSRF-bridged)* |

### 1.4 Feature: `parties` (`app/features/parties/`)

| METHOD | Path | Class | Guards |
|---|---|---|---|
| POST | `/parties/people` | API | `_dependency({'role_slug':'admin'})`, `require_user_auth`, `require_tenant`, `get_db` |
| POST | `/parties/organizations` | API | *same* |
| GET | `/parties` | API | *same* |
| GET | `/parties/{party_id}` | API | *same* |
| DELETE | `/parties/{party_id}` | API | *same* |
| GET | `/admin/parties` | WEB | `require_web_auth`, `require_tenant`, `get_db` |
| GET | `/admin/parties/create` | WEB | *same* |
| POST | `/admin/parties/people` | WEB | *same* |
| POST | `/admin/parties/organizations` | WEB | *same* |
| GET | `/admin/parties/{party_id}` | WEB | *same* |
| GET | `/admin/parties/{party_id}/edit` | WEB | *same* |
| POST | `/admin/parties/{party_id}/edit` | WEB | *same* |
| POST | `/admin/parties/{party_id}/delete` | WEB | *same* |

### 1.5 Feature: `rbac` (`app/features/rbac/`)

| METHOD | Path | Class | Guards |
|---|---|---|---|
| POST | `/rbac/roles` | API | `_dependency({'role_slug':'admin'})`, `require_user_auth`, `require_tenant`, `get_db` |
| GET | `/rbac/roles` | API | *same* |
| POST | `/rbac/role-grants` | API | *same* |
| GET | `/rbac/audit-events` | API | *same* |
| GET | `/admin/roles` | WEB | `require_web_auth`, `require_tenant`, `get_db` |
| GET | `/admin/roles/create` | WEB | *same* |
| POST | `/admin/roles` | WEB | *same* |
| GET | `/admin/role-grants` | WEB | *same* |
| POST | `/admin/role-grants` | WEB | *same* |
| GET | `/admin/audit` | WEB | *same* |

### 1.6 Feature: `settings` (`app/features/settings/`)

| METHOD | Path | Class | Guards |
|---|---|---|---|
| GET | `/settings/{domain}` | API | `_dependency({'role_slug':'admin'})`, `require_user_auth`, `require_tenant`, `get_db` |
| PUT | `/settings/{domain}/{key}` | API | *same* |
| GET | `/admin/settings` | WEB | `require_web_auth`, `require_tenant`, `get_db` |
| GET | `/admin/settings/{domain}/{key}/edit` | WEB | *same* |
| POST | `/admin/settings/{domain}/{key}/edit` | WEB | *same* |
| GET | `/admin/settings/branding` | WEB | *same* |
| POST | `/admin/settings/branding` | WEB | *same* |

### 1.7 Feature: `custom_fields` (`app/features/custom_fields/`)

| METHOD | Path | Class | Guards |
|---|---|---|---|
| POST | `/custom-fields/definitions` | API | `_dependency({'role_slug':'admin'})`, `require_user_auth`, `require_tenant`, `get_db` |
| GET | `/custom-fields/definitions` | API | *same* |
| GET | `/custom-fields/definitions/{field_id}` | API | *same* |
| PATCH | `/custom-fields/definitions/{field_id}` | API | *same* |
| DELETE | `/custom-fields/definitions/{field_id}` | API | *same* |
| GET | `/custom-fields/{entity_type}/{entity_id}/values` | API | *same* |
| PUT | `/custom-fields/{entity_type}/{entity_id}/values` | API | *same* |
| GET | `/admin/custom-fields` | WEB | `require_web_auth`, `require_tenant`, `get_db` |
| GET | `/admin/custom-fields/create` | WEB | *same* |
| POST | `/admin/custom-fields` | WEB | *same* |
| GET | `/admin/custom-fields/{field_id}/edit` | WEB | *same* |
| POST | `/admin/custom-fields/{field_id}/edit` | WEB | *same* |
| POST | `/admin/custom-fields/{field_id}/deactivate` | WEB | *same* |
| GET | `/admin/custom-fields/party/{party_id}/values-panel` | WEB (cross-feature fragment) | *same* |
| POST | `/admin/custom-fields/party/{party_id}/values-panel` | WEB (cross-feature fragment) | *same* |

### 1.8 Feature: `licensing` (`app/features/licensing/`)

| METHOD | Path | Class | Guards |
|---|---|---|---|
| POST | `/licences/apply` | API | `_dependency({'role_slug':'admin'})`, `require_user_auth`, `require_tenant`, `get_db` |
| POST | `/licences/revocations/import` | API | *same* |

**No web surface.** The WS8 licence receiver is API-only — there is no admin screen to view, apply, or
inspect a licence, its entitlements, or its revocation state.

### 1.9 Feature: `web` (`app/features/web/`, `core=False`, deletable)

| METHOD | Path | Class | Guards |
|---|---|---|---|
| GET | `/admin` | WEB | `require_web_auth`, `require_tenant`, `get_db` |

### 1.10 Feature: `tenants` (`app/features/tenants/`)

JSON only, and only on the platform plane — see §1.2. No `web_routers`, no `nav`.

### 1.11 Route counts by class

| Class | Count |
|---|---|
| INFRA (incl. `/static` mount) | 6 |
| PLATFORM (JSON) | 5 |
| API (tenant JSON) | 23 |
| WEB (HTML admin portal) | 31 |
| **Total** | **65** |

Guard-shape summary: every `APIRoute` carries at least one `require_*`. The only routes without an
auth-tier guard are the four documented pre-auth mutations (`POST /auth/register`, `POST /auth/login`,
`POST /admin/login`, `POST /platform/auth/login`) plus `POST /admin/logout`, all of which are in
`tests/architecture/test_route_guards.py`'s `MUTATING_ALLOWLIST` with inline justification.

---

## 2. UI-surface classification

| Target surface | Status | Owner / evidence |
|---|---|---|
| **Login / authentication** | **PRESENT** (admin only) | `app/features/auth/web.py` → `GET/POST /admin/login`, `POST /admin/logout`; template `auth/login.html`. Cookie session (`access_token`) via `dotmac_kernel.web_deps.require_web_auth`, which additionally requires the `"admin"` role. JSON sibling: `app/features/auth/router.py` (`/auth/login`, `/auth/register`, `/auth/me`). **There is no non-admin login** — a non-admin authenticated party can obtain a token but has no HTML surface at all. No password-reset, no MFA, no email-verification, no "forgot password" route or template anywhere. |
| **Platform administration** | **PARTIAL — API only, zero UI** | `dotmac_kernel.platform_auth` + `app/features/tenants/router.py`: 5 JSON routes under `/platform/*`, host-exact to `PLATFORM_ROOT_DOMAIN`, guarded by `require_platform_admin`. **No platform HTML template, no platform login page, no platform nav, no platform layout exists.** Operating the control plane today means curl/HTTP client. |
| **Tenant administration** | **PRESENT** — this is the only real UI | The `/admin/*` portal: 31 routes across `web`, `auth`, `parties`, `rbac`, `settings`, `custom_fields`. Dashboard + People + Roles + Role Grants + Audit Log + Settings + Custom Fields. Everything is admin-role-gated. |
| **Customer portal** | **ABSENT** | No route, template, guard tier, or nav entry exists for a self-service end-customer surface. `docs/superpowers/phase2-backlog.md` records that all web-conventions governance is scoped to `templates/{admin,auth}` and the `/admin` path prefix precisely because such a surface does not exist yet. |
| **Reseller portal** | **ABSENT** | No reseller concept anywhere — no model, no role seed, no route, no template. |
| **Vendor portal** | **ABSENT** | No vendor concept. The `licensing` feature is a *receiver* (it consumes a vendor-signed envelope); the vendor-side issuing surface is not in this repo. |
| **Public / signup pages** | **ABSENT (as HTML)** / PARTIAL as API | `POST /auth/register` exists as a JSON API route (tenant-scoped, pre-auth), but there is **no registration page, no marketing/landing page, no public root route**. `GET /` returns 404. `components/topbar.html:59` links `href="/"` — a **dead link to a non-existent route**. |
| **Emails / SMS / documents** | **ABSENT** | Exhaustive grep for `smtp`, `sendgrid`, `twilio`, `weasyprint`, `reportlab`, `render_email`, `email_template`, `.pdf` across `app/` and the whole kernel: **zero hits**. `dotmac_kernel.messaging` is a transactional **outbox/inbox + command envelope** (DB-backed event durability, relay/worker, dead-lettering) — an internal integration primitive, **not** a notification or document-rendering system. No template dir for email, no from-address in `brand.json` (the docstring in `branding.py:14-16` explicitly notes the from-email/payment-scheme keys were *trimmed out* of the port). |
| **PWA / mobile assets** | **ABSENT** | No `manifest.json`, no service worker, no `apple-touch-icon`, no `theme-color` meta, no icon set of any size. **There is not even a favicon** — `base.html` has no `<link rel="icon">` at all, so every page falls back to the browser default. The layout *is* responsive (Tailwind `lg:` breakpoints, `sidebarToggle` mobile overlay in `layouts/admin.html:45-55`), but that is responsive HTML, not a PWA. |
| **API-only profile** | **PRESENT and real** | `WEB_ENABLED=false` (`dotmac_kernel.config.Settings.web_enabled`, `.env.example:88`) mounts **no** `web_routers` and **no** `/static` mount (`app_factory.py:176-184`), while every feature's JSON `routers` keep working. `install_surface_globals` sets `nav_items = ()` in that mode. Distinct from `DISABLED_FEATURES=<name>`, which drops one named feature's JSON **and** web routers together. |

### Surface gaps worth flagging for F0

- **Only one actor tier has a UI.** The portal is a single, uniform "admin" tier
  (`require_web_auth` hard-requires the `"admin"` role for *every* page). There is no
  role-differentiated portal, no read-only view, no per-feature portal permission.
- **The platform/tenant split is real in routing and data but has no UI counterpart.**
- **Licensing/entitlements have no operator-visible surface** despite being the mechanism a
  white-labelled/OEM deployment would be gated by.

---

## 3. Template inventory

**34 templates, all in `packages/dotmac-kernel/src/dotmac_kernel/templates/`, all git-tracked.**
**0 templates in the assembly.** Paths below are relative to that kernel templates root.

### 3.1 Layouts & shared chrome

| Path | Extends | Rendered by |
|---|---|---|
| `base.html` | — (root) | never directly; extended by `layouts/admin.html`, `auth/login.html`, all 8 `errors/*` |
| `layouts/admin.html` | `base.html` | never directly; extended by all 14 admin page templates |
| `components/sidebar.html` | *fragment (include-only)* | `{% include %}`d twice by `layouts/admin.html` (desktop aside + mobile overlay) |
| `components/topbar.html` | *fragment (include-only)* | `{% include %}`d by `layouts/admin.html` |
| `components/form_macros.html` | *macro library* | `{% from %}` imported by 8 page templates |
| `components/table_macros.html` | *macro library* | `{% from %}` imported by 9 page/fragment templates |

### 3.2 Auth

| Path | Extends | Rendered by |
|---|---|---|
| `auth/login.html` | `base.html` | `app/features/auth/web.py:73, 96, 107` (GET form + 2 failure re-renders) |

### 3.3 Admin — dashboard (`web` feature)

| Path | Extends | Rendered by |
|---|---|---|
| `admin/dashboard.html` | `layouts/admin.html` | `app/features/web/web.py:46` |

### 3.4 Admin — `parties`

| Path | Extends | Rendered by |
|---|---|---|
| `admin/parties/index.html` | `layouts/admin.html` | `app/features/parties/web.py:164` |
| `admin/parties/_table.html` | *fragment* | `app/features/parties/web.py:162` (htmx) **and** `{% include %}`d by `index.html` |
| `admin/parties/create.html` | `layouts/admin.html` | `app/features/parties/web.py:102` |
| `admin/parties/edit.html` | `layouts/admin.html` | `app/features/parties/web.py:124` |
| `admin/parties/detail.html` | `layouts/admin.html` | `app/features/parties/web.py:268` |

### 3.5 Admin — `rbac`

| Path | Extends | Rendered by |
|---|---|---|
| `admin/rbac/roles.html` | `layouts/admin.html` | `app/features/rbac/web.py:92` |
| `admin/rbac/_roles_table.html` | *fragment* | `app/features/rbac/web.py:90` (htmx) + `{% include %}`d by `roles.html` |
| `admin/rbac/role_create.html` | `layouts/admin.html` | `app/features/rbac/web.py:104` |
| `admin/rbac/grants.html` | `layouts/admin.html` | `app/features/rbac/web.py:186` |
| `admin/rbac/audit.html` | `layouts/admin.html` | `app/features/rbac/web.py:317` |
| `admin/rbac/_audit_table.html` | *fragment* | `app/features/rbac/web.py:315` (htmx) + `{% include %}`d by `audit.html` |

### 3.6 Admin — `settings`

| Path | Extends | Rendered by |
|---|---|---|
| `admin/settings/index.html` | `layouts/admin.html` | `app/features/settings/web.py:99` |
| `admin/settings/edit.html` | `layouts/admin.html` | `app/features/settings/web.py:145` |
| `admin/settings/branding.html` | `layouts/admin.html` | `app/features/settings/web.py:263` |

### 3.7 Admin — `custom_fields`

| Path | Extends | Rendered by |
|---|---|---|
| `admin/custom_fields/index.html` | `layouts/admin.html` | `app/features/custom_fields/web.py:112` |
| `admin/custom_fields/_table.html` | *fragment* | `app/features/custom_fields/web.py:110` (htmx) + `{% include %}`d by `index.html` |
| `admin/custom_fields/form.html` | `layouts/admin.html` | `app/features/custom_fields/web.py:266` — **serves both create and edit** |
| `admin/custom_fields/_values_panel.html` | *fragment* | `app/features/custom_fields/web.py:452` — the **cross-feature values-panel**, htmx-loaded from `admin/parties/detail.html:86` |

### 3.8 Error pages (kernel-owned, not feature-owned)

| Path | Extends | Rendered by |
|---|---|---|
| `errors/400.html` | `base.html` | `dotmac_kernel/errors.py:72` (`_STATUS_TEMPLATES`) |
| `errors/401.html` | `base.html` | `errors.py:73` |
| `errors/403.html` | `base.html` | `errors.py:74` |
| `errors/404.html` | `base.html` | `errors.py:75` |
| `errors/409.html` | `base.html` | `errors.py:76` |
| `errors/422.html` | `base.html` | `errors.py:77` |
| `errors/500.html` | `base.html` | `errors.py:78` |
| `errors/csrf.html` | `base.html` | `errors.py:120` (dedicated CSRF-failure copy) |

### 3.9 Multi-renderer and dead-template analysis

- **Rendered by more than one feature: NONE.** Every template has exactly one owning feature (or is
  kernel-owned in the case of `errors/*` and the shared chrome). The values-panel is the interesting
  case and it *confirms* the rule: `admin/custom_fields/_values_panel.html` is embedded in the
  `parties` detail page but is rendered **only** by `custom_fields/web.py`; `parties` reaches it via an
  `hx-get` URL, never a Python import. This is the documented composition pattern and it holds.
- **Rendered by nobody (dead): NONE.** All 34 resolve to a renderer, an `extends` parent, an
  `include` site, or a macro-import site. Verified by grepping every template basename against
  `app/**/*.py` and against the `extends`/`include`/`from` graph.
- **Six templates serve double duty as both a full page render and an htmx fragment render** (the four
  `_table.html`/`_values_panel.html` fragments plus their index pages) — the `HX-Request`-header split
  documented in each feature's `web.py` docstring.
- **One macro is defined but never used from a page:** `table_macros.html`'s `pagination(page,
  total_pages, base_url)` (line 142) — the four list screens each hand-roll their own prev/next
  `hx-get` block instead. Low-severity dead code, but it means "pagination" has **two** shapes.

---

## 4. CSS / static-asset inventory

**15 files under `packages/dotmac-kernel/src/dotmac_kernel/static/`. 0 static files in the assembly.**

### 4.1 Committed vs build artifact

| Path | Size | Kind | Tracked? |
|---|---|---|---|
| `static/css/src/main.css` | 231 lines | **Tailwind v4 source** | committed |
| `static/css/main.css` | 44,486 B | **compiled artifact** | **gitignored** (`.gitignore` names it explicitly) |
| `static/fonts/fonts.css` | 3,257 B | `@font-face` declarations | committed |
| `static/fonts/Outfit-{400,500,600,700,800}.woff2` | ~13–14 KB each | vendored font | committed |
| `static/fonts/PlusJakartaSans-{400,500,600,700}.woff2` | ~12 KB each | vendored font | committed |
| `static/js/htmx.min.js` | 49,082 B | **vendored lib — htmx `2.0.0`** (`version:"2.0.0"` in the bundle) | committed |
| `static/js/alpine.min.js` | 61,851 B | **vendored lib — Alpine.js (minified, version string stripped from the build; ~3.x by API shape)** | committed |
| `static/js/components.js` | 4,303 B | first-party Alpine components | committed |
| `static/js/csrf.js` | 2,257 B | first-party CSRF header bridge | committed |

**No images of any kind.** No `.png`, `.svg`, `.ico`, `.jpg`, `.webp` anywhere in the repo. Every icon
in the UI is an **inline SVG path** — either hand-written in a template or emitted by
`table_macros.html`'s `icon(name)` macro (line 14). There is **no logo file, no favicon, no
brand mark image, no PWA icon**.

### 4.2 Build pipeline

- **Source:** `packages/dotmac-kernel/src/dotmac_kernel/static/css/src/main.css`
- **Tool:** Tailwind CSS v4, **CSS-first config only**. `tailwind.config.js` was deleted and no
  `@config` import exists, so there is no JS config path at all. Config lives in the source file as
  `@theme`, `@variant`, `@source`, `@source inline(...)`.
- **`package.json`** (repo root):
  - `css:build` → `npx tailwindcss -i .../static/css/src/main.css -o .../static/css/main.css --minify`
  - `css:watch` → same with `--watch`
  - devDeps: `@tailwindcss/cli ^4.1.18`, `tailwindcss ^4.1.18`, `postcss ^8.5.6`, `autoprefixer ^10.4.23`
- **`Makefile`:** `css-build` (line 67) = `npm install && npm run css:build`; `css-watch` (line 70) =
  `npm install && npm run css:watch`. `make dev` (line 65) documents that `css-build` must have run
  at least once because the artifact is gitignored.
- **Dockerfile:** a `css-builder` stage runs `npm ci && npm run css:build` (`npm ci`, not `install`, so
  lockfile drift fails loudly).
- **Cache-busting:** `templating.static_asset_url()` appends `?v=<sha256[:12]>` of the file's bytes
  (`templating.py:178-202`); a missing/unbuilt asset degrades to `?v=missing` rather than raising.
- **Dark mode:** class-based, `@variant dark (&:where(.dark, .dark *))` — toggled by Alpine's
  `$store.dark` on `<html>` (`base.html:2`, `components.js:20`).
- **Content scanning:** v4 auto-detection plus two explicit `@source` pins (`../../templates`,
  `../../static/js`) and **7 `@source inline(...)` safelist patterns** covering
  `{primary,accent,slate,green,red,yellow,blue}` across `bg-`, `text-`, `border-`, `from-`, `to-`, and
  two `dark:` variants — a forward-looking safelist for macros that build class names by interpolation.

### 4.3 Design tokens — **authored** set (the F0-relevant list)

**27 tokens, all inside the single `@theme { … }` block at `static/css/src/main.css:21-55`.** These are
the *entire* design-token vocabulary this codebase actually defines:

**Typography (2)**
```
--font-sans      : 'Plus Jakarta Sans', system-ui, sans-serif
--font-display   : 'Outfit', system-ui, sans-serif
```

**Primary colour ramp (11)**
```
--color-primary-50   #edf3eb
--color-primary-100  #dbe7d7
--color-primary-200  #b8cfb0
--color-primary-300  #90b483
--color-primary-400  #5a9147
--color-primary-500  #367920
--color-primary-600  #206a07
--color-primary-700  #1a5706
--color-primary-800  #154605
--color-primary-900  #103504
--color-primary-950  #0a2503
```

**Accent colour ramp (11)**
```
--color-accent-50   #ecfeff
--color-accent-100  #cffafe
--color-accent-200  #a5f3fc
--color-accent-300  #67e8f9
--color-accent-400  #22d3ee
--color-accent-500  #06b6d4
--color-accent-600  #0891b2
--color-accent-700  #0e7490
--color-accent-800  #155e75
--color-accent-900  #164e63
--color-accent-950  #083344
```

**Animation (3)**
```
--animate-fade-in        fadeIn 0.2s ease-in-out
--animate-stagger-in     staggerFadeIn 0.5s ease-out forwards
--animate-counter-pop    counterPop 0.4s cubic-bezier(0.4,0,0.2,1)
```

**No other custom property is authored anywhere.** Grep for `--*:` across all 34 templates returns
**zero**; grep for `var(--…)` across all templates returns **zero**. Templates consume tokens
exclusively through Tailwind utility class names, never through CSS variables.

**Token categories that do NOT exist and would have to be invented for a design-token system:**
spacing scale, radius scale, shadow/elevation scale, semantic surface/border/text roles
(`--surface-raised`, `--text-muted`, …), z-index scale, motion-duration scale, breakpoint tokens, and
any semantic status colours (success/warning/danger are raw Tailwind palette classes — `green-600`,
`red-600`, `yellow-500`, `blue-600` — hardcoded in `base.html:60-63` and throughout).

### 4.4 Design tokens — **compiled** set (for contrast)

The built `static/css/main.css` emits **177 unique custom properties**: the 27 authored above (minus
`--color-primary-800/950` and `--color-accent-800/950`, tree-shaken as unused) plus Tailwind's own
default theme surface (`--color-slate-*`, `--color-green-*`, `--color-red-*`, `--color-blue-*`,
`--color-amber-*`, `--color-yellow-*`, `--color-rose-500`, `--color-sky-500`, `--color-orange-500`,
`--text-*` + `--text-*--line-height`, `--font-weight-*`, `--radius-{lg,xl,2xl}`, `--container-*`,
`--tracking-{wide,wider}`, `--ease-{in,out}`, `--spacing`, `--default-*`) and **61 internal
`--tw-*` engine variables**. Breakdown by prefix: `color` 75, `tw` 61, `text` 16, `font` 8,
`container` 5, `default` 4, `radius` 3, `tracking` 2, `ease` 2, `spacing` 1.

### 4.5 First-party JS surface

`components.js` registers, on `alpine:init`: `Alpine.store('dark')` (with `toggle`/`isOff`),
`Alpine.data('toastStore')` (add/remove/isSuccess/isError/isWarning/isInfo), `Alpine.data('userMenu')`,
`Alpine.data('sidebarToggle')`. It also exposes `window.showToast(message, type, duration)` and bridges
the `HX-Trigger: {"showToast": …}` response header onto the `show-toast` window event. There are **no
inline `<script>` blocks in any template** — CSP is `script-src 'self' 'unsafe-eval'` with no
`unsafe-inline`; `font-src 'self'` and `connect-src 'self'` (no external origins at all, per the
no-CDN standard).

---

## 5. Navigation inventory

### 5.1 Declared `NavItem`s — 7 total, from 5 of 8 features

| Label | Path | Owning feature | Manifest |
|---|---|---|---|
| Dashboard | `/admin` | `web` | `app/features/web/feature.py` |
| People | `/admin/parties` | `parties` | `app/features/parties/feature.py` |
| Roles | `/admin/roles` | `rbac` | `app/features/rbac/feature.py` |
| Role Grants | `/admin/role-grants` | `rbac` | *same* |
| Audit Log | `/admin/audit` | `rbac` | *same* |
| Settings | `/admin/settings` | `settings` | `app/features/settings/feature.py` |
| Custom Fields | `/admin/custom-fields` | `custom_fields` | `app/features/custom_fields/feature.py` |

Features declaring **no** nav: `tenants` (platform JSON only), `auth` (login/logout are not nav items),
`licensing` (no web surface at all).

**Sidebar ordering is manifest-registration order, not the nav declaration order and not alphabetical.**
`install_surface_globals` iterates `manifests` in `FEATURE_MODULES` order
(`tenants, auth, parties, rbac, settings, custom_fields, licensing, web`), so the rendered sidebar
order is: **People, Roles, Role Grants, Audit Log, Settings, Custom Fields, Dashboard** — i.e. the
Dashboard link lands **last**, at the bottom, because the `web` feature is registered last. That is
almost certainly not the intended visual order and there is no `order`/`weight` field on `NavItem` to
fix it without reordering `FEATURE_MODULES`.

### 5.2 How the sidebar renders

1. `app/main.py` → `dotmac_kernel.create_app(assembly)`.
2. `app_factory.create_app` (line ~114) calls
   `dotmac_kernel.templating.install_surface_globals(manifests, disabled, web_enabled)` **once at
   import time** — process-static, a config change needs a restart.
3. That function builds two Jinja globals:
   - `enabled_features` — `frozenset` of enabled manifest names (used for optional-slot conditionals,
     e.g. `admin/parties/detail.html`'s `{% if 'custom_fields' in enabled_features %}`).
   - `nav_items` — a tuple of `NavItem`, each stamped with `feature=manifest.name` via
     `dataclasses.replace`. **`nav_items` is `()` when `web_enabled` is False.**
4. `components/sidebar.html:35-46` loops `nav_items`. Active-state is **path-based**:
   `request.url.path == item.path` OR (`item.path != "/admin"` AND
   `request.url.path.startswith(item.path ~ "/")`) — the `/admin` special-case stops the dashboard
   matching every admin path as a prefix.
5. Every entry renders the **same generic hamburger SVG** — `NavItem` carries only
   `label`/`path`/`feature`, so per-item icons were dropped when the hardcoded list was removed.
6. `layouts/admin.html` `{% include %}`s `components/sidebar.html` **twice**: the fixed desktop
   `<aside>` (line 24) and the mobile overlay (line 53).
7. `tests/architecture/test_feature_manifests.py::test_nav_items_paths_exist_in_web_routers` fails the
   build if a `NavItem.path` doesn't resolve to a route in that same manifest's `web_routers`.

### 5.3 Links hardcoded in templates rather than derived from a manifest

The sidebar itself is fully manifest-derived (its header comment forbids adding links there). But
**every other link in the portal is a hardcoded literal path**. Full enumeration:

**Chrome (`components/`, `layouts/`, `base.html`) — 3 sites:**

| File:line | Link | Note |
|---|---|---|
| `components/sidebar.html:27` | `href="/admin"` — the brand-mark home link | hardcoded; bypasses `nav_items` |
| `components/topbar.html:59` | `href="/"` — "Home" in the user menu | **hardcoded AND dead — `GET /` is not a mounted route (404)** |
| `components/topbar.html:66` | `hx-post="/admin/logout"` | hardcoded (correctly a POST per the F7 fix) |

**Error pages — 8 sites:** `errors/{400,401,403,404,409,422,500,csrf}.html` each hardcode a single
`href="/admin"` recovery link. In a `WEB_ENABLED=false` deployment these templates are still reachable
via the HTML content-negotiation path, and the link points at a surface that no longer exists.

**Auth — 1 site:** `auth/login.html:43` `hx-post="/admin/login"`.

**Feature pages — 35+ sites**, all literal `/admin/...` strings, e.g.
`admin/settings/index.html` → `href="/admin/settings/branding"` (×2) and
`href="/admin/settings/{{ domain }}/{{ item.key }}/edit"`;
`admin/rbac/roles.html` → `href="/admin/roles/create"`, `href="/admin/role-grants"`;
`admin/rbac/grants.html` → `href="/admin/roles"`, `hx-post/hx-get="/admin/role-grants"`;
`admin/parties/index.html` → `href="/admin/parties/create"`, `hx-get="/admin/parties"`;
`admin/parties/detail.html` → `href="/admin/parties"`, `href="/admin/parties/{{ party.id }}/edit"`,
`hx-get="/admin/custom-fields/party/{{ party.id }}/values-panel"` (the one deliberate cross-feature
URL, guarded by `{% if 'custom_fields' in enabled_features %}`);
`admin/custom_fields/*` → `href="/admin/custom-fields/create?entity_type=…"`,
`href="/admin/custom-fields/{{ field.id }}/edit"`, `hx-post="/admin/custom-fields/{{ field.id }}/deactivate"`;
plus every list screen's hand-rolled prev/next `hx-get="/admin/<x>?page={{ page ± 1 }}"`.

**Consequence for white-labelling / remounting:** the `/admin` prefix is not configurable. It is baked
into 45+ template literals, `require_web_auth`'s scope, the governance tests' path prefix, and the
`MUTATING_ALLOWLIST`. A product that wants its portal at `/console` or `/manage` cannot get there by
configuration.

---

## 6. Hardcoded brand / product-name leakage

Grepped `templates/`, `static/`, and all Python in `app/` + the kernel for `dotmac|DotMac|Dotmac`,
`example.com`, `support@`, and literal `http(s)://` URLs. Vendored bundles, lockfiles, docs, tests, and
`CHANGELOG/README/CLAUDE/AGENTS` excluded from the leak count (noted separately where relevant).

**The templates and static assets are essentially clean.** Every user-visible identity string in the
HTML goes through the `brand` Jinja global. The leaks are all in Python defaults, config data, and one
runtime title.

### 6.1 Real leaks — would ship in a white-labelled build

| File:line | Literal | Assessment |
|---|---|---|
| `packages/dotmac-kernel/src/dotmac_kernel/branding.py:100` | `"tagline": "A DotMac starter application"` | **HARDCODED DEFAULT, USER-VISIBLE.** This is `_DEFAULTS`, the built-in fallback used when neither `BRAND_TAGLINE` nor `brand.json` supplies a value. Overridable, but a deployment that forgets one env var renders the vendor's name. The module docstring at line 96-98 claims the defaults are *"intentionally generic/template-neutral … this starter ships no production identity to accidentally leak"* — that claim is **false for this key**. |
| `brand.json:5` | `"BRAND_TAGLINE": "A DotMac starter application"` | Same string in the shipped config file. Configurable (replace the value), but it is the checked-in default. |
| `app/assembly.py:24` | `name="dotmac_starter_mt"` | **HARDCODED, PUBLICLY EXPOSED.** `app_factory.py:135` does `FastAPI(title=spec.name)`. Verified at runtime: `app.title == "dotmac_starter_mt"` and `openapi()["info"] == {'title': 'dotmac_starter_mt', 'version': '0.1.0'}`. So the internal product codename is published on the **unauthenticated** `/openapi.json` and is the `<title>` of `/docs` and `/redoc`. Not driven by `brand.name`. Highest-visibility leak in the repo. |
| `packages/dotmac-kernel/src/dotmac_kernel/licensing.py:39-42` | `"dotmac-licence-envelope/1"`, `"dotmac-licence/1"`, `"dotmac-licence-revocation/1"`, `"dotmac-licence-applied-state/1"` | **HARDCODED wire-protocol schema identifiers.** They appear verbatim inside every signed licence envelope, revocation list, and applied-state acknowledgement an OEM deployment exchanges. Not UI-visible, but they are vendor-named strings on the contract surface — changing them is a breaking protocol change, so this is a decision to make now, not later. Mirrored in `app/features/licensing/schemas.py:9,25`. |
| `packages/dotmac-kernel/src/dotmac_kernel/licensing.py:574` | `"the dotmac-kernel[licensing] extra"` | Hardcoded **operator-visible exception message** naming the vendor distribution. Surfaces in logs/error output on a misconfigured install. |
| `packages/dotmac-kernel/src/dotmac_kernel/testing/licensing.py:48` | `"the dotmac-kernel[licensing] (or [testing]) extra"` | Same, test-harness path. |
| Distribution/import name: `dotmac-kernel` / `dotmac_kernel` | ~150 import sites across `app/` and the kernel | **Structural.** Not a UI leak, but the package name is unavoidably present in tracebacks, `pip list`, logger names (`dotmac_kernel.messaging.worker`, `dotmac_kernel.app_factory`), and every JSON log line's `logger` field. Renaming is a whole-distribution decision. |

### 6.2 Generic placeholders — not vendor-branded, but not production-safe either

| File:line | Literal | Assessment |
|---|---|---|
| `packages/dotmac-kernel/src/dotmac_kernel/branding.py:99` | `"name": "Starter"` | Generic placeholder default. Configurable. Renders as the sidebar title and `<title>` if unset. |
| `packages/dotmac-kernel/src/dotmac_kernel/branding.py:101-102` | `"#206A07"`, `"#06B6D4"` | Default brand colours, duplicated from the Tailwind `@theme` ramp. Configurable — but see §6.4, they have no rendering effect. |
| `packages/dotmac-kernel/src/dotmac_kernel/branding.py:103-104` | `"support@example.com"`, `"https://example.com"` | Placeholder contact/URL. Configurable. **Rendered nowhere** — see §6.4. |
| `brand.json:2-8` | `BRAND_NAME "Starter"`, colours, `support@example.com`, `https://example.com` | The shipped white-label template file. Correctly designed: flat upper-case keys that each match a same-named env var, so a deployment overrides one key without editing the file. |
| `packages/dotmac-kernel/src/dotmac_kernel/testing/fakes.py:65-66` | `"test@example.com"`, `"http://testserver"` | Test fixture only. Not shipped behaviour. |
| `.env.example:18` | `DATABASE_URL=…/starter` | Example DB name. Documented as override-required. |

### 6.3 Comment-only occurrences (no functional leak)

`templates/base.html:10` — the phrase *"per the cross-Dotmac no-CDN standard"* inside an HTML comment.
**This is the only occurrence of the vendor name in any template, and HTML comments are served to the
browser**, so it is technically visible in view-source of every rendered page. Cosmetic, one-line fix.
All other Python `dotmac` hits are import statements, module docstrings, or logger names.

### 6.4 The bigger white-labelling problem the grep exposed — the branding system is largely inert

Enumerating `brand.*` usage across all 34 templates against `branding.py`'s key maps surfaces four
mismatches that matter more than the string leaks:

1. **`brand.mark` is used but never defined.** `components/sidebar.html:29` and `auth/login.html:31`
   render `{{ brand.mark if brand and brand.mark else "A" }}` — the logo tile's letter. `mark` is
   **not** in `_KEY_MAP`, **not** in `_DEFAULTS`, and **not** in `_KNOWN_BRAND_KEYS`
   (`branding.py:88-118`). It can never be set by env, `brand.json`, or the tenant override. **Every
   deployment's logo tile shows a literal `"A"` forever.**
2. **`logo_url` is editable but rendered nowhere.** It is a form field
   (`admin/settings/branding.html:36`), is read into `_branding_form` (`settings/web.py:246`), is
   allowlisted in `_KNOWN_BRAND_KEYS`, is persisted — and **no template ever reads `brand.logo_url`**.
   Uploading a logo has zero effect. Combined with (1), **the portal has no way to display a customer's
   logo at all.**
3. **`custom_css` never reaches the portal.** `{{ brand.custom_css | safe }}` appears **only** in
   `admin/settings/branding.html:68`, the *preview pane on the branding editor page itself*.
   `base.html` has no `<style>` block and no `custom_css` reference, so a tenant's custom CSS styles
   nothing except its own editor preview.
4. **`primary_color` / `accent_color` are cosmetic swatches only.** They render at
   `admin/settings/branding.html:49-50` as two inline-styled squares in the preview. The **actual** UI
   colours come from Tailwind classes (`bg-primary-600`, `text-accent-400`, …) resolved at
   **compile time** from the `@theme` block. Changing a tenant's `primary_color` in the admin UI
   repaints two swatches and nothing else. Per-tenant colour theming is **not implemented**, and
   cannot be without either CSS-variable-backed utilities or a per-tenant CSS build.
   `support_email` and `app_url` are likewise rendered by no template.

**Net:** of the six deployment-static brand keys plus six tenant-override keys, exactly **one**
(`name`) actually changes what a user sees, in four places (`base.html:7` title, `layouts/admin.html:20`
title, `sidebar.html:31`, `login.html:23,33`). Everything else is either dead (`mark`, `logo_url`,
`custom_css`, `support_email`, `app_url`) or preview-only (`tagline`, the two colours). A
white-labelled build today can change the product's *name* and nothing else.

---

## Appendix — quick counts

| Metric | Value |
|---|---|
| Mounted routes | 65 (63 `APIRoute` + `/static` mount; 4 are FastAPI doc builtins) |
| — INFRA / PLATFORM / API / WEB | 6 / 5 / 23 / 31 |
| Registered features | 8 (`tenants, auth, parties, rbac, settings, custom_fields, licensing, web`) |
| Features with a web surface | 6 of 8 (`tenants`, `licensing` have none) |
| Templates | 34 — **all kernel package data, 0 in the assembly** |
| — page templates / fragments / macro libs / layouts / error pages | 14 / 4 / 2 / 2 (`base`+`layouts/admin`) + 2 chrome includes / 8 |
| Dead templates | 0 |
| Templates rendered by >1 feature | 0 |
| Static files | 15 — **all kernel package data, 0 in the assembly** |
| Vendored JS libs | 2 (htmx 2.0.0; Alpine.js minified, version string stripped) |
| Vendored fonts | 9 woff2 (Outfit 400–800, Plus Jakarta Sans 400–700) + `fonts.css` |
| Images / icons / favicon / PWA assets | **0** — all icons are inline SVG |
| Gitignored build artifacts | 1 (`static/css/main.css`) |
| Authored design tokens (`@theme`) | **27** (2 font, 11 primary, 11 accent, 3 animation) |
| Compiled custom properties in the artifact | 177 (incl. 61 internal `--tw-*`) |
| `var(--…)` uses in templates | 0 |
| `NavItem`s | 7, from 5 features |
| Hardcoded link sites in templates | 47+ (3 chrome, 8 error pages, 1 auth, 35+ feature pages) |
| Brand-leak sites (vendor name / product name / placeholder identity) | **12 file:line sites** — 7 real (§6.1) + 5 generic-placeholder (§6.2), plus 1 comment-only |
| Brand keys that actually affect rendered UI | **1 of 12** (`name`) |
