# F0 — `dotmac_sub` UI surface / template / CSS / navigation inventory

**Repo:** `/Users/michaelayoade/Downloads/management/dotmac_sub`
**Version:** `7.96.0` (`VERSION`, `pyproject.toml` `name = "dotmac_sm"`)
**Commit surveyed:** `cbc5cc5d3` (`feat(kernel-adoption): S2 — pin dotmac-kernel==0.1.0a8`)
**Method:** read-only. Route table produced by importing every router module named in
`app/main.py`'s `_CORE_ROUTER_SPECS` + `_DEFERRED_API_ROUTER_SPECS` and walking
`router.routes` (3185 route objects, 0 module load errors). Template/CSS/brand
figures from mechanical `os.walk` + regex sweeps. No repo file was modified; no
tests or servers were run.

Orientation docs used: `AGENTS.md`, `CLAUDE.md`, `DESIGN.md`, `docs/FRONTEND_SPEC.md`,
`docs/UI_UX_COMPONENT_ARCHITECTURE.md`, `docs/UI_INFORMATION_AND_ACTION_STANDARD.md`,
`docs/CONTROL_RELATIONSHIPS_AND_BRANDING_SOT.md`, `docs/PLATFORM_ADOPTION_LEDGER.md`.

---

## 1. UI surfaces / portals

Nine distinct surfaces. All HTML surfaces are server-rendered Jinja2 + htmx +
Alpine.js from a single `templates/` root; there is one shared Jinja global
installer (`app/web/brand_globals.py`) patching `Jinja2Templates.__init__`, but
**each web route module constructs its own `Jinja2Templates` instance** — there is
no single kernel-style `render()` seam.

| # | Surface | URL prefix(es) | Template dir | Layout | Auth model | Routes (method×path) | Templates |
|---|---------|----------------|--------------|--------|------------|----------------------|-----------|
| 1 | **Staff / admin portal** | `/admin/*` | `templates/admin/` | `layouts/admin.html` (821 lines) | cookie session → `app/web/auth/dependencies.py::require_web_auth`, then `require_admin_web_auth` (principal_type must be `system_user`), then per-route `require_permission(...)` — 1336 `require_permission` call sites across `app/web/` | **1469** (701 GET / 768 mutating; 1080 declared `HTMLResponse`) | **583** |
| 2 | **Customer self-service portal** | `/portal/*` (+ legacy `/customer`, `/customer/{path:path}`) | `templates/customer/` | `layouts/customer.html` (609 lines), `layouts/customer_auth.html` | cookie session, `principal_type == "subscriber"`; login/MFA/enrollment in `app/web/customer/auth.py`; `require_customer_account_id` scoping | **110** (65 GET / 45 mutating) + 2 legacy | **56** |
| 3 | **Reseller / partner portal** | `/reseller/*` | `templates/reseller/` | `layouts/reseller.html` (257 lines) | cookie session, also `principal_type == "subscriber"` (see `STAFF_PRINCIPAL_TYPES` comment in `app/web/auth/dependencies.py`); `require_reseller_context` | **51** (27 GET / 24 mutating) | **26** |
| 4 | **Vendor / contractor portal** | `/vendor/*` | `templates/vendor/` | `templates/vendor/base.html` (18 lines — its own mini-layout, not under `layouts/`) | `require_web_auth` + capability check in `app/web/vendor_portal.py::_context(..., capability=...)` | **17** (2 GET / 15 mutating) | **4** |
| 5 | **Staff auth (pre-login)** | `/auth/*` | `templates/auth/` | `layouts/auth.html` (57 lines) | unauthenticated; login, MFA, MFA-enroll, forgot/reset password, `/auth/refresh` | **13** | **5** |
| 6 | **Public / unauthenticated** | `/` (landing), `/legal/*`, `/s/{survey_id}`, `/ticket-confirm/{token}`, `/network/graphs/{token}`, `/branding/*` | `templates/index.html`, `templates/public/` | `base.html` / standalone | none (token-scoped where applicable) | **19** | **9** (`index.html`, `domain.html`, `public/**` ×6, `base.html`) |
| 7 | **JSON API** | `/api/v1/*` (+ `/widget/*` chat widget) | — | — | bearer/session via `app/api/deps.py`; per-router mode in `_DEFERRED_API_ROUTER_SPECS` (`user` / `admin` / `perm:<domain>` / `readperm:<key>` / `none`) | **1478** (660 GET / 814 mutating; 0 HTML) | — |
| 8 | **WebSocket** | `/ws/inbox`, `/ws/workqueue` | — | — | session | **2** | — |
| 9 | **Legacy `/web/*` shim** | `/web/*` (24 GET pages: `/web/billing`, `/web/network`, `/web/rbac`, …) | reuses `templates/admin/**` | `layouts/admin.html` | `app/web_domains.py` (one monolithic module) | **24** | — |

**Native mobile (out of the HTML template scope, but part of the product's UI surface):**

- `mobile/` — Flutter **customer self-care** app, `pubspec.yaml` `name: dotmac_portal`,
  version `7.96.0+127`. Riverpod + dio + go_router. Consumes `/api/v1`. Brand values
  come from the repo-root `brand.json` via `flutter build --dart-define-from-file=../brand.json`.
- `field_mobile/` — Flutter **field technician / vendor** app, `name: dotmac_field`,
  version `1.0.1+2`. Default API base `https://sub.dotmac.io`.
- **PWA**: `templates/base.html:20` links `/branding/manifest.webmanifest`, generated at
  runtime from the resolved brand (`app/web/public/branding.py:151`). Icons are static:
  `static/branding/favicon/{favicon-16,favicon-32,icon-192,icon-512,apple-touch-icon}.png`.
  There is **no service worker** — no `sw.js` / `serviceWorker` registration anywhere.

**Route-module layout** (`app/web/`, 123 `.py` files; `app/api/`, 97 `.py` files):
`app/web/admin/` alone is **94 modules** (`network_*.py` ×34, `billing_*.py` ×16, …).

---

## 2. Route inventory summary

**Total: 3185 route objects → 3183 unique (method, path) pairs.**
Methods: GET 1505, POST 1336, DELETE 159, PATCH 158, PUT 25.
Response classes declared: `HTMLResponse` 1245, `JSONResponse` 15, `PlainTextResponse` 3,
`DefaultPlaceholder` (JSON) 1920.

### By top-level prefix

| Prefix | Routes | Notes |
|--------|--------|-------|
| `/api` (`/api/v1/*`) | 1474 | JSON |
| `/admin` | 1469 | HTML (1080 explicit `HTMLResponse`; remainder redirects/fragments) |
| `/portal` | 110 | HTML — customer |
| `/reseller` | 51 | HTML |
| `/web` | 24 | HTML — legacy shim |
| `/vendor` | 17 | HTML |
| `/auth` | 13 | HTML |
| `/legal` | 8 | HTML public |
| `/widget` | 4 | JSON — chat widget |
| `/branding` | 4 | `theme.css`, `manifest.webmanifest`, `login-hero/{portal}`, `assets/{file_id}` |
| `/ticket-confirm` | 3 | HTML public, token-scoped |
| `/customer` | 2 | legacy → `/portal` |
| `/s` | 2 | public survey |
| `/ws` | 2 | WebSocket |
| `/` | 1 | public landing |
| `/network` | 1 | `/network/graphs/{token}` public graph |

**HTML vs JSON:** ~1707 HTML-surface routes (`/admin`+`/portal`+`/reseller`+`/vendor`+`/auth`+`/web`+`/legal`+public) vs ~1478 JSON routes. Roughly 54/46.

### `/admin` by section (top of 33 sections)

`network` 572 · `system` 178 · `billing` 124 · `catalog` 106 · `sales` 52 ·
`customers` 47 · `integrations` 45 · `reports` 43 · `notifications` 39 ·
`inbox` 37 · `projects` 35 · `support` 32 · `vendors` 31 · `provisioning` 30 ·
`gis` 18 · `settings` 15 · `resellers` 10 · `dispatch` 9 · `surveys` 7 ·
`workqueue` 7 · `referrals` 6 · `dashboard` 5 · `alerts` 4 ·
`service-requests` 3 · `operations` 2 · `nas` 2 · `crm` 2 · `design-system` 2 ·
`usage` 2 · `help` 1 · `contacts` 1 · `drift` 1.

Network is **39% of the entire admin portal** — it is the dominant, most
ISP-specific surface and the least likely to generalize into a shared UI package.

### `/api/v1` by second segment (top 20)

`field` 88 · `me` 70 · `crm` 60 · `settings` 54 · `ont-units` 40 ·
`provisioning` 35 · `reseller` 33 · `network` 33 · `dispatch` 32 ·
`integrations` 32 · `vendor` 30 · `support` 29 · `nas` 27 · `gis` 25 ·
`qualification` 25 · `olt-devices` 20 · `payments` 20 · `rbac` 20 ·
`wireguard` 20 · `auth` 19. (Long tail of ~120 further resource prefixes.)

### `/portal` and `/reseller` sections

- `/portal`: billing 26, auth 17, services 11, support 8, profile 7, location 6,
  speedtest 4, service-orders 4, bandwidth 3, contacts 3, work-orders 3, +14 more.
- `/reseller`: accounts 11, auth 10, billing 9, profile 7, contacts 4,
  work-orders 3, service-requests 2, dashboard/quotes/reports/fiber-map 1 each.

---

## 3. Template inventory

**718 HTML templates, 118,006 non-blank lines**, all under one `templates/` root.

| Root | Count |
|------|-------|
| `templates/admin/` | 583 |
| `templates/customer/` | 56 |
| `templates/reseller/` | 26 |
| `templates/components/` | 24 |
| `templates/errors/` | 6 |
| `templates/public/` | 6 |
| `templates/auth/` | 5 |
| `templates/layouts/` | 5 |
| `templates/vendor/` | 4 |
| root (`base.html`, `index.html`, `domain.html`) | 3 |

`templates/admin/` largest subdirs: `network/` 226, `system/` 67, `billing/` 66,
`catalog/` 32, `reports/` 25, `integrations/` 20, `customers/` 16, `inbox/` 15,
`projects/` 14, `provisioning/` 14, `sales/` 14, `notifications/` 13, `vendors/` 13.

### Layouts and inheritance

| Layout | Extended by |
|--------|-------------|
| `layouts/admin.html` | **451** |
| `layouts/customer.html` | 44 |
| `layouts/reseller.html` | 22 |
| `base.html` | 21 |
| `layouts/auth.html` | 7 |
| `layouts/customer_auth.html` | 7 |
| `vendor/base.html` | 3 |

**163 templates have no `{% extends %}`** — of these 132 are `_`-prefixed
fragments (htmx partials); the remaining ~31 are standalone pages (e.g.
`templates/public/surveys/*.html`, which each carry their own `<!doctype html>`).
There is no enforced "extends-a-layout-or-is-a-fragment" architecture test
equivalent to `dotmac_starter_mt`'s.

### Component conventions

Three separate mechanisms coexist:

1. **Macro library (the real shared component layer)** —
   `templates/components/ui/macros.html`: **80 macros, 1395 lines, 101 KB**,
   imported by **313 of 718 templates (44%)** via `{% from … import name %}`,
   with **1888 macro call sites**. Macro names:
   `ambient_background, page_header, action_button, stats_card, status_badge,
   status_presentation_badge, data_table, table_head, table_row, row_actions,
   row_action, empty_state, card, search_input, filter_select, filter_bar,
   icon_badge, detail_header, tabs, info_row, animation_styles, icon_* (×38),
   pagination, type_badge, info_banner, status_filter_card, avatar,
   vendor_badge, submit_button, danger_button, warning_button, validated_input,
   info_grid, metric_row, progress_bar, timeline_item, section_divider,
   setting_row, setting_toggle, step_indicator, alert_count_badge,
   connection_status, accordion_section`.
2. **Specialised "spine" macro modules** (newer archetype work, per file headers) —
   `components/ui/triage.html` (9 macros, "Triage workspace spine, archetype A"),
   `components/ui/record.html` (4 macros, "Record / 360 spine, archetype B"),
   `components/ui/ledger.html` (7 macros, "Data / ledger spine, archetype D"),
   `components/data/data_grid.html` (13 cell macros), `components/ui/list_macros.html` (2).
3. **Includes** — 750 include statements, 87 distinct targets. **618 of the 750
   (82%) are the single `components/forms/csrf_input.html`.** The next-most-included
   is `components/data/recent_activity_panel.html` at 11. Includes are effectively
   *not* a component mechanism here.

39 templates define macros locally (186 macros total) — i.e. ~1/3 of macro
definitions live outside `components/`, in feature templates (e.g.
`admin/projects/_components.html` 7, `admin/support/tickets/_components.html` 6,
`customer/billing/_payment_macros.html` 6, `admin/inbox/_queue_macros.html` 4).

**Interaction style:** htmx is *partial*, not universal — `hx-get` 122 uses / 67
templates, `hx-post` 111 / 52, `hx-swap` 169 / 84, `hx-delete` 5, `hx-put` 0.
Alpine.js dominates: `x-data` 192 uses / 123 templates, `@click` 482 / 93.
**418 plain `method="post"` forms across 202 templates** — Sub's CSRF is a hidden
form input (`components/forms/csrf_input.html`, included 618×), not a header
bridge, so a shared package ported from `dotmac_starter_mt` cannot assume the
hx-only mutation rule.

### Duplication verdict — **heavy**

- **Tables: 280 templates contain raw `<table>` markup; only 8 use the
  `data_grid` macros; only 34 use `table_head`/`table_row`.** So **279 templates
  hand-roll their own table markup** — this is the single biggest duplication
  cluster.
- Raw HTML control counts across templates: **1526 `<button>`, 2351 `<input>`.
  Zero of them go through a shared button/input macro** (`submit_button` 4 calls,
  `danger_button` 1, `warning_button` 1, `validated_input` — the whole
  form-control layer is unowned).
- **653 of 718 templates (91%) contain at least one ≥120-character `class="…"`
  attribute** — Tailwind utility soup rather than component classes.
- Copy-paste at block level: of 108,832 distinct 6-line blocks, **753 appear in
  ≥3 different files and 48 appear in ≥10 different files**; the worst single
  6-line block is duplicated across **80 files**.
- The icon layer is imported but dead: `icon_plus` is imported by 32 templates
  and **called 0 times**; `icon_chart_bar` 16 imports / 0 calls; `icon_cog` 14/0.
  ~30 icon macros have more importers than callers, i.e. templates were
  copy-pasted with their import headers.
- Largest templates show the "one giant page file" pattern:
  `admin/customers/detail.html` **289 KB**, `admin/customers/form.html` 105 KB,
  `admin/catalog/subscription_detail.html` 100 KB, `admin/customers/index.html` 98 KB.

`docs/UI_UX_COMPONENT_ARCHITECTURE.md` is explicitly marked
**"Status: historical implementation catalog … not a current page requirement"**,
so the documented macro-import convention is aspirational, not enforced.

There **is** a live design-system showcase page: `/admin/design-system` and
`/admin/design-system/modules` (`app/web/admin/design_system.py`,
`templates/admin/design_system/{index,modules}.html`).

---

## 4. CSS / static-asset inventory

### Toolchain

- **Tailwind CSS v4** (`@tailwindcss/cli ^4.1.18`, `tailwindcss ^4.1.18` in
  `package.json` devDependencies). Build: `npx tailwindcss -i ./static/css/src/main.css
  -o ./static/css/main.css --minify` (`npm run css:build`).
- **CSS-first config**: `static/css/src/main.css` starts `@import "tailwindcss";`
  + `@variant dark (&:where(.dark, .dark *));` + an `@theme { … }` block.
- **Stale v3 config still checked in**: `tailwind.config.js` (3926 bytes) and
  `postcss.config.js` exist and duplicate the same palette/fonts/animations, but
  **nothing loads them** — the v4 CLI is invoked without `--config` and the source
  CSS has no `@config` directive. The `safelist` of ~70 dynamic macro colour
  classes in `tailwind.config.js` is therefore **not applied** under v4. This is a
  live drift/bug to flag, and a trap for anyone porting the config.

### Artifacts

| Path | Kind | Size |
|------|------|------|
| `static/css/src/main.css` | Tailwind v4 source (`@theme`, `@font-face`, keyframes, utilities) | 10.8 KB |
| `static/css/main.css` | **compiled, git-tracked** | **558 KB** |
| `static/css/design-system.css` | hand-written token/role stylesheet, loaded by `base.html` | 11 KB |
| `static/css/admin-inbox-replica.css` | inbox-specific | 5.2 KB |
| `static/css/live-chat.css` | chat widget | 4 KB |
| `src/css/**` (27 files: `base/_tokens.css`, `components/_{buttons,tables,cards,forms,badges,navigation,…}.css`, `layout/`, `utilities/`) | **git-tracked but entirely unreferenced** — no template links it, it is not a Tailwind input | ~dead |

`templates/base.html` loads, in order: `/static/css/main.css?v=…`,
`/static/css/design-system.css?v=…`, `/branding/theme.css` (runtime). So the
runtime brand stylesheet wins by cascade order — that is the theming seam.

### Vendored JS (`static/vendor/`, `static/js/vendor/`)

| Lib | File | Version |
|-----|------|---------|
| Chart.js | `static/js/vendor/chart.min.js` (205 KB) | **4.4.1** (banner) |
| ECharts | `static/js/vendor/echarts.min.js` (1.03 MB) | unlabelled |
| Cytoscape | `cytoscape.min.js` (373 KB) + `cytoscape-dagre.min.js`, `dagre.min.js` (284 KB) | unlabelled |
| htmx | `htmx.min.js` (49 KB) + `htmx-loading-states.min.js` | unlabelled |
| Alpine.js | `alpine.min.js` (44 KB) + `alpine-focus`, `alpine-collapse` | unlabelled |
| Leaflet | `static/vendor/leaflet/leaflet.{js,css}` + `leaflet.draw.{js,css}` + 7 marker images | unlabelled |

**CDN leakage (breaks air-gapped / self-hosted builds):** 7 templates load
`https://cdn.jsdelivr.net/npm/chart.js@4/…`, 3 more `chart.js@4.4.0`, 2 `chart.js@4.4.1`,
2 bare `chart.js`, 2 `chartjs-adapter-date-fns@3`, 1 `alpinejs@3.x.x` — i.e. the
same libraries are sometimes vendored and sometimes CDN-loaded. Payment SDKs are
CDN-only by necessity: `https://js.paystack.co/v1/inline.js` (3), `https://checkout.flutterwave.com/v3.js` (1).

21 first-party JS modules in `static/js/` (largest: `admin-inbox.js` 53 KB,
`dynamic-table-config.js` 34 KB, `bandwidth-chart.js` 26 KB, `charts.js` 24 KB,
`echarts-charts.js` 19 KB).

### Fonts / images / icons

- Fonts self-hosted, woff2: **Outfit** 400/500/600/700/800 (`static/fonts/outfit/`),
  **Plus Jakarta Sans** 400/500/600/700 (`static/fonts/plus-jakarta-sans/`),
  declared with `@font-face` in `static/css/src/main.css`.
- Icons: **inline SVG only** — 38 `icon_*` macros in `components/ui/macros.html`
  plus another 17 inline `{% set icon_* %}` SVG blocks in
  `components/navigation/admin_sidebar.html`. No icon font, no sprite sheet.
- Illustrations: `static/illustrations/` — `customer-hero.{png,webp}`,
  `login-hero-{admin,customer,reseller}.webp`, `email-header.png`, `og-image.png`.
- Favicons/PWA icons: `static/branding/favicon/` (5 PNGs, listed above).
- Uploaded brand assets (logos, favicon, login heroes) are **DB-backed**, served
  from `/branding/assets/{file_id}` (`StoredFile` with `entity_type="branding_asset"`).

### Design tokens actually defined in this repo

**A. `@theme` block, `static/css/src/main.css` (27 properties)** — Tailwind v4 theme:

```
--font-sans, --font-display
--color-primary-{50,100,200,300,400,500,600,700,800,900,950}
--color-accent-{50…950}
--animate-fade-in, --animate-stagger-in, --animate-counter-pop
```

**B. `static/css/design-system.css` (90 properties)** — the semantic layer:

```
--font-display, --font-body
--color-brand-{50…900}
--color-accent-{50…900}
--color-semantic-positive-{50…950}
--color-semantic-info-{50…950}
--color-semantic-warning-{50…950}
--color-semantic-negative-{50…950}
--color-semantic-neutral-{50…950}
--surface-primary, --surface-secondary, --surface-tertiary
--text-primary, --text-secondary, --text-tertiary
--border-default, --border-subtle
--status-surface, --status-border, --status-foreground, --status-indicator
--tw-ring-color
```

**C. Runtime-generated `/branding/theme.css`** (`app/web/public/branding.py::_theme_css`,
scales from `app/services/brand_theme.py`) — emits, per request, from the resolved
brand's primary/secondary/semantic hexes:

```
--color-primary-{50…950}          (11 stops, generated from brand primary)
--color-brand-{50…900}            (10 stops, alias)
--color-accent-{50…950}           (11 stops, from brand secondary)
--color-semantic-{positive,info,warning,negative,neutral}-{50…950}   (55)
--color-{red,rose,orange,amber,yellow,lime,green,emerald,sky,blue,cyan,
         teal,indigo,violet,purple,fuchsia,pink}-{50…950}
                                  (17 legacy Tailwind palettes × 11 = 187 aliases,
                                   each `var(--color-<role>-<step>)`)
--color-data-{1..7}               (categorical chart roles → var(--color-<role>-600))
```
Semantic colours are contrast-gated: `is_accessible_semantic_color` with
`MIN_SEMANTIC_TEXT_CONTRAST = 4.5`, falling back to defaults on failure.

**D. `src/css/base/_tokens.css` (82 properties) — DEAD/unreferenced**, and a
different, older naming universe:
```
--ink, --ink-light, --ink-muted, --ink-faint, --parchment, --parchment-dark,
--gold, --teal, … --module-{subscribers,billing,network,catalog,provisioning,
monitoring,reports,admin}[-light|-dark], --status-{active,suspended,terminated,
pending,provisioning}[-light], --spacing-{xs…3xl,input-x,input-y,card,card-sm,
card-lg}, --content-gutter, --radius-{sm,md,lg,xl,2xl,card,btn,input,badge,icon},
--card-{bg,border,shadow,shadow-hover}, --input-{border,focus},
--btn-{shadow,shadow-hover}, --brand-{primary,primary-light,accent},
--icon-{chevron-down,error,check,search}
```

**E. Component-local:** `--channel-color` (`admin-inbox-replica.css`),
`--dm-chat-{primary,out-bg,out-text,in-bg,in-text}` (`live-chat.css`).

### Token-naming verdict

**Mixed, and the good half already exists.** Sub is *further along* than a
value-named palette: `design-system.css` and the runtime `theme.css` are named by
**role** — `--color-semantic-positive-600`, `--surface-primary`, `--text-secondary`,
`--border-subtle`, `--status-foreground`, `--color-data-3`. And
`app/services/brand_theme.py::LEGACY_TAILWIND_PALETTE_ROLES` is an explicit,
documented remap of every value-named Tailwind palette onto a role
(`red → semantic-negative`, `indigo → primary`, `cyan → accent`, …), with the
comment *"New UI code should author primary/accent/semantic tokens directly."*

But:
- The **step-scale** naming is still value-shaped: `--color-primary-600` is a
  ramp position, not a role like `--color-action-primary` / `--color-action-primary-hover`.
  There are **no** interaction/intent tokens (`-hover`, `-pressed`, `-disabled`,
  `-on-*`), and no spacing/radius/elevation/typography tokens in the *live* layer
  (they exist only in the dead `src/css/base/_tokens.css`).
- **Templates overwhelmingly bypass tokens entirely** and use raw Tailwind
  utilities (`text-slate-600`, `bg-indigo-500/10`, `border-slate-200`,
  `dark:bg-slate-800`) — 91% of templates carry ≥120-char class strings. The
  187 compatibility aliases exist precisely because of this; they neutralise
  hue, but a semantic system would need those call sites migrated, not aliased.
- Two parallel identity token names for the same colour (`--color-primary-*` and
  `--color-brand-*`) plus a dead third vocabulary (`--brand-primary`, `--ink`,
  `--gold`) means three naming systems to reconcile.

Net: **roles exist at the CSS-variable layer and are brand-driven at runtime;
the consuming layer (templates) is value-named Tailwind utilities.** A shared
semantic token system can adopt `brand_theme.py`'s role vocabulary largely as-is,
and the migration cost is at the template/utility-class level, not the token level.

---

## 5. Navigation inventory

**No registry, no config, no manifest — every portal's navigation is hardcoded in
its own template.**

| Portal | Where nav lives | Mechanism |
|--------|-----------------|-----------|
| Admin | `templates/components/navigation/admin_sidebar.html` (273 lines), included by `layouts/admin.html:272` | Two local macros (`nav_link`, `section_label`), 17 inline `{% set icon_* %}` SVGs, a hardcoded `nav_link(...)` call per item under three hardcoded section labels ("Core", "Operations", "ADMIN"). Active-state highlighting is a **hand-maintained 60-entry `parent_for_page` dict** mapping ~200 `active_page` string ids to parent nav ids, at the top of the same file. Every page must pass a matching `active_page` string; there is nothing that detects a stale mapping. |
| Customer | `templates/layouts/customer.html` — desktop `<nav>` at line 77 + a duplicated mobile `<nav>` at line 460 | Literal `<a href="/portal/…">` per item, active class chosen by `{{ '…' if active_page == 'x' else '…' }}` inline. Desktop and mobile lists are separate copies. |
| Reseller | `templates/layouts/reseller.html` — desktop `<nav>` line 43 + mobile `<nav>` line 193 | Same pattern, again duplicated desktop/mobile. |
| Vendor | `templates/vendor/base.html` (18 lines) | Two literal links. |
| Public/auth | none | — |

**The only dynamic input to nav** is `sidebar_stats.module_states`, from
`app/services/module_manager.py` (`MODULE_KEY_MAP`, 7 toggles backed by
`domain_settings`: `network, integrations, crm, provisioning, vpn, gis, reports`),
plus two badge counts (`pending_orders`, `overdue_invoices`, `pending_location_requests`).

**Drift finding:** the sidebar guards items with
`module_states.get('customer', True)`, `.get('billing', True)`,
`.get('catalog', True)`, `.get('notifications', True)` — **none of those four keys
exist in `MODULE_KEY_MAP`**, so those four toggles are permanently on. Silent
dead-toggle, no test catches it.

Compare: `dotmac_starter_mt` builds its sidebar from every `FeatureManifest.nav`
via `install_surface_globals`, with a test that fails when a `NavItem.path`
doesn't resolve to a mounted route. Sub has **no equivalent** — a moved `/admin/*`
route silently leaves a 404 link, and there is no per-feature nav contribution point.

---

## 6. Branding and theming mechanisms

Sub already has a **real, layered white-labelling system** — this is the single
strongest existing asset for the foundation programme.
Its governing doc is `docs/CONTROL_RELATIONSHIPS_AND_BRANDING_SOT.md`.

### Layer 1 — deployment-static brand config

- **`brand.json`** (repo root) — the single flat file shared by backend *and*
  Flutter. Keys: `BRAND_NAME, BRAND_PRODUCT_NAME, BRAND_LEGAL_NAME, BRAND_TAGLINE,
  BRAND_PRIMARY_COLOR, BRAND_SECONDARY_COLOR, BRAND_SEMANTIC_{POSITIVE,INFO,
  WARNING,NEGATIVE,NEUTRAL}_COLOR, BRAND_SUPPORT_EMAIL, BRAND_FROM_EMAIL,
  BRAND_FROM_NAME, BRAND_APP_URL, BRAND_MOBILE_APP_NAME, BRAND_PAYMENT_SCHEME,
  API_BASE_URL, GLITCHTIP_*`.
- **`app/services/branding_config.py`** — resolution order
  `built-in defaults < brand.json < same-named env var`; path overridable via
  `BRAND_CONFIG_PATH`; `lru_cache`d for process lifetime. Exposes friendly keys
  (`brand.primary_color`, …) mapped from the flat JSON names.
- **`app/web/brand_globals.py`** — monkey-patches `Jinja2Templates.__init__` before
  routers import, so **every** Jinja env gets the `brand` global plus filters
  (`money`, `app_datetime`, status-presentation helpers, `can`/`action_permitted`).
  Templates are told to guard with a default (`brand.primary_color if brand is
  defined and brand else "#3b82f6"`) — this is the direct cause of the 37 "soft"
  brand-name fallbacks counted in §7.

### Layer 2 — per-scope brand profiles (DB)

- **`app/models/branding.py::BrandProfile`** — `scope_type ∈ {platform, reseller,
  organization}` + `scope_id`, with a CHECK constraint and partial unique indexes
  (one platform row; one per scoped id). Fields: `brand_name, product_name,
  legal_name, tagline, primary_color, secondary_color, logo_url, dark_logo_url,
  favicon_url, support_email, support_phone, from_email, from_name, app_url,
  portal_domain, legal_address (JSON), metadata, is_active`.
- **`app/services/brand_profiles.py::resolve_brand(db)`** → `ResolvedBrand`
  (also carries `semantic_colors`).
- **So per-reseller and per-organization branding is already modelled**, not just
  per-deployment. This is the closest existing analogue to per-tenant branding.

### Layer 3 — runtime theming

- **`GET /branding/theme.css`** (`app/web/public/branding.py:81`) — generates the
  full custom-property sheet described in §4C from the resolved brand, with
  contrast gating and total fail-safe (`except: → checked-in defaults`).
  `Cache-Control: public, max-age=300`. Linked from `base.html` **after**
  `main.css`, so it overrides the compiled palette without a CSS rebuild.
- **`app/services/brand_theme.py`** — `generate_scale(hex)` → 11 stops;
  `LEGACY_TAILWIND_PALETTE_ROLES` (17 palettes → roles);
  `CATEGORICAL_COLOR_ROLES` (7 chart colours);
  `is_accessible_semantic_color` (WCAG 4.5:1).
- **`GET /branding/manifest.webmanifest`** — brand-driven PWA manifest
  (`name = f"{brand.name} Selfcare"`, `theme_color = brand.primary_color`).
- **`GET /branding/login-hero/{portal}`** — per-portal (`customer|reseller|admin`)
  login hero image, DB-configurable.
- **`GET /branding/assets/{file_id}`** — uploaded logo/favicon blobs from
  `StoredFile`, with a redirect fallback to `/favicon.ico`.

### Layer 4 — admin-editable branding settings

`app/services/settings_spec.py` (SettingDomain `comms`) declares:
`sidebar_logo_url, sidebar_logo_dark_url, favicon_url, brand_primary_color,
brand_secondary_color, brand_semantic_{positive,info,warning,negative,neutral}_color,
login_hero_{customer,reseller,admin}_url` — 13 keys.
Admin screens: `templates/admin/system/{branding,company_info,email,settings}.html`;
API `app/api/branding.py`; storage `app/services/branding_storage.py`;
public read `app/services/public_branding.py`.
Per-portal context processors: `app/web/customer/branding.py`,
`app/web/reseller/branding.py`, `app/web/portal_branding.py` (fills the gap for
staff/vendor auth pages that don't get a portal context processor).

### Documents and email

- **Invoice/receipt PDFs** — `app/services/billing_invoice_pdf.py` (1423 lines) is
  brand-aware: `_branded_company_info()` overrides `company_name / company_email /
  company_phone / address` from the resolved `ResolvedBrand`, and `_logo_src()`
  pulls `comms.sidebar_logo_url` (supports `data:` URIs).
- **Email** — `app/services/email.py` is **NOT** fully brand-aware: it hardcodes
  `DOTMAC_RED = "#FF0000"`, `DOTMAC_GREEN = "#008000"`, `DOTMAC_WHITE = "#F4F4F9"`,
  `DOTMAC_BUTTON_TEXT = "#ffffff"` (lines 134-137) and uses them directly in the
  HTML shell and in password-reset / verify-email / portal-invite bodies
  (lines 715-772, 1209-1454). `company_name` *is* injected, but the palette is not.
  This is the largest un-white-labelled output path.
- **HTML receipt** — `templates/customer/billing/receipt.html:27` hardcodes the
  literal text `DOTMAC` and the hex `#176f37`.

---

## 7. Hardcoded brand / product-name leakage

`dotmac` (case-insensitive) occurrences, excluding `.venv`, `node_modules`,
`.git`, `.claude/worktrees`, `__pycache__`:

| Area | Matches | Files |
|------|---------|-------|
| `app/` (Python) | 653 | 158 |
| `scripts/` | 174 | 58 |
| `templates/` | 112 | 56 |
| `static/` | 45 | 10 |
| `alembic/` | 30 | 16 |
| `mobile/lib/` | 29 | 13 |
| `field_mobile/lib/` | 23 | 12 |

### Templates: 112 total = 37 soft + 75 hard

**37 "soft"** = `{{ brand.x if brand is defined and brand else "DotMac Subs" }}`
fallback defaults. Harmless at runtime when `brand` resolves, but they still ship
"DotMac" strings in a white-labelled build and they're the reason the
brand-name string appears in `base.html`, all 5 `templates/auth/*.html`,
`layouts/{auth,customer_auth}.html`.

**75 hard leaks.** Worst offenders by class:

*JS global namespace (46 of the 75 — mechanical, safe to rename, but everywhere):*
- `window.DotmacCharts` ×18, `window.DotmacChartRegistry` ×4, `window.DotmacTour` ×3,
  `window.DotmacKanban` ×1 — defined in `static/js/{charts,echarts-charts,bandwidth-chart,admin-tour,kanban,sales-dashboard,admin-inbox}.js` and referenced from
  `templates/admin/billing/{index,payments,ar_aging}.html`,
  `templates/admin/reports/{revenue,network,churn,technician,subscribers}.html`,
  `templates/admin/network/detected_outages.html`,
  `templates/customer/usage/_content.html`, `templates/layouts/admin.html:763`.

*Customer-visible copy (the real damage):*
- `templates/customer/billing/receipt.html:27` — `<div …>DOTMAC</div>` + brand hex `#176f37`
- `templates/customer/billing/receipt.html:95` — `Prepared by Dotmac Selfcare`
- `templates/customer/referrals/index.html:10` — `Invite friends to DotMac.`
- `templates/customer/services/speedtest.html:14,18` — `not the Dotmac connection` / `the Dotmac line into your building`
- `templates/reseller/auth/login.html:10` — `<h1>Grow your business with DotMac</h1>`
- `templates/reseller/reports/revenue.html:13,22,35` — `paid Dotmac`, `paid to Dotmac`
- `templates/public/network/graph.html:6` — `<title>… - Dotmac</title>`
- `templates/public/surveys/{respond,thank_you,unavailable}.html:1-2` — `<title>… · Dotmac</title>`
- `templates/vendor/project_detail.html:53` — `Request Dotmac-owned material`
- `templates/admin/vendors/operations.html:67` — `Approve Dotmac's release decision`
- `templates/admin/network/nas/device_detail.html:536` — `saves credentials in Dotmac`
- `templates/admin/dashboard/index.html:9` — `{% block title %}Dashboard - DotMac Subs{% endblock %}` (hardcoded, no brand lookup)

*Defaults / placeholders that persist a Dotmac value into config:*
- `templates/admin/system/email.html:14` — `"from_name": "DotMac SM"`
- `templates/admin/system/settings.html:155` — `<input name="from_name" value="DotMac SM">` (a *value*, not a placeholder — it writes DotMac into the tenant's config)
- `templates/admin/system/config/portal.html:25,38,43` — placeholders `selfcare.dotmac.io`, `oss.dotmac.ng`, `reseller.dotmac.io`
- `templates/admin/inbox/email_routes.html:28` — placeholder `support@dotmac.ng`
- `templates/admin/network/tr069/acs_form.html:25` — placeholder `http://oss.dotmac.ng:7547`
- `templates/admin/network/pop-sites/form.html:162` — placeholder `Dotmac`
- `templates/admin/system/company_info.html:26` — placeholder `Dotmac Technologies Ltd`
- `templates/admin/design_system/index.html:662` — demo data `admin@dotmac.ng`
- `templates/admin/dashboard/index.html:103,116` — `localStorage` key `dotmac_whats_new_dismissed`; `layouts/admin.html:768` — `storageKey: 'dotmac_admin_tour_seen_v1'`

*Architecture-prose comments (cosmetic only):* `components/ui/{ledger,triage,record}.html:2`
— `"— dotmac_sub admin"`.

### Other brand/locale leakage

| Pattern | `templates/` | `static/js/` | `app/` |
|---------|-------------|-------------|--------|
| `DotMac Subs` | 25 | – | 4 |
| `Dotmac Technologies` | 5 | – | 2 |
| `DotMac SM` | 2 | 2 | 4 |
| `dotmac.io` | 2 | – | 13 |
| `dotmac.ng` | 5 | – | 7 |
| `@dotmac` / `support@` | 2 / 1 | – | 7 / 2 |
| `selfcare.` | 1 | – | 16 |
| `oss.` | 3 | – | 7 |
| `+234` (Nigeria dialling code) | **12** | 1 | 1 |
| `Africa/Lagos` | 5 | – | **17** |
| `NGN` | **57** | 4 | **274** |
| `₦` | **43** | – | 21 |

**Locale leakage is larger than brand leakage.** 274 `NGN` + 43 `₦` + 17
`Africa/Lagos` means currency and timezone are baked into services, not resolved.
`app/web/brand_globals.py` documents this explicitly: the `money` filter
*"renders with the default NGN symbol"*, and `app_datetime` *"formats … in the
fixed display timezone (Africa/Lagos / WAT)"*. `+234` appears as a phone
placeholder in 12 templates (customer forms, wizards, reseller profile, company info).

**Python (`app/`) 653 matches — bulk is architectural prose, not user-visible.**
Top files: `app/services/sot_relationships.py` 101 (ownership-registry docstrings),
`app/services/dotmac_erp/` 38+15+14+12+10+8+8 (a genuinely-named external
integration — the ERP product is called Dotmac ERP; renaming is a product decision,
not a leak), `app/services/scheduler_config.py` 28,
`app/services/email.py` 26 (**a real leak — the `DOTMAC_*` colour constants**),
`app/services/settings_spec.py` 20, `app/services/branding_config.py` 9
(the intentional defaults). Also `app/telemetry.py:28`
`OTEL_SERVICE_NAME` default `"dotmac_sm"`, `app/monitoring.py` `app_name="dotmac-sub"`,
`app/team_inbox_smtp.py:74-105` `X-Dotmac-Probe` header + `[Dotmac probe]` subject.

**Scale verdict:** ~75 hard template leaks (46 of them a single mechanical JS-namespace
rename), ~12 customer-visible copy strings, ~10 config-default/placeholder leaks,
4 hardcoded email colour constants, plus **~340 currency/timezone locale
hardcodings** which are a bigger white-label blocker than the name itself.

---

## 8. Kernel adoption status

**Pinned, proven compatible, but with ZERO imports today. Adoption is at slice S2 of a documented plan.**

- `pyproject.toml:50` — `"dotmac-kernel==0.1.0a8"` (runtime dependency).
  `:64` — `dotmac-kernel = { version = "0.1.0a8", source = "forgejo" }` (private index).
  `:69` — `{ version = "0.1.0a8", extras = ["testing"], source = "forgejo" }`.
  `:311` — dev group `"dotmac-kernel[testing]==0.1.0a8"` (same wheel; an import
  guard keeps `dotmac_kernel.testing` out of `app/`).
- **`grep -rn 'dotmac_kernel' app --include='*.py'` → 0.** The package is
  installed and pinned; no application code imports it yet. `tests/architecture/
  test_kernel_import_boundary.py` states this deliberately: the guard exists
  *"BEFORE the dependency exists"*.

### Import allowlist (enforced two ways)

`tests/architecture/test_kernel_import_boundary.py::ALLOWED_KERNEL_MODULES`, kept
byte-for-byte in sync with the "Kernel import allowlist" section of
`docs/PLATFORM_ADOPTION_LEDGER.md:116-122` by
`test_allowlist_matches_the_ledger`:

```
dotmac_kernel.assembly
dotmac_kernel.capabilities
dotmac_kernel.features
dotmac_kernel.money
dotmac_kernel.profiles
dotmac_kernel.providers
dotmac_kernel.providers.provisioning
```
Plus one allowlisted named object: `("dotmac_kernel.features", "mount_features")`.
`dotmac_kernel.testing.*` is allowed in `tests/` only (ledger line 131).

Everything else is rejected by an AST-based checker: bare `import dotmac_kernel`,
`from dotmac_kernel import X`, and specifically `dotmac_kernel.db`,
`dotmac_kernel.models`, `dotmac_kernel.middleware.*`, `dotmac_kernel.messaging.*`,
and any `dotmac_kernel._*`.

**Note for a shared-UI package:** the allowlist contains **no UI/templating/branding
kernel module** — no `templating`, `branding`, `display`, `web_deps`, `deps`.
Adding one is a ledger amendment + architecture-test change, i.e. an explicit,
reviewed gate. That gate is exactly where a shared UI package would have to enter.

### Adoption governance

`docs/PLATFORM_ADOPTION_LEDGER.md` — rebaselined 2026-08-02 for slice S1, amended
same day for S2. Decision authority is cited as `dotmac_starter_mt`'s
`docs/adr/0003-unified-deployment-profiles.md` + the execution plan
`.../plans/2026-08-02-dotmac-sub-kernel-improvements.md`. Key constraints it records:
Sub stays authoritative for subscriber/billing/network/support/timeline state;
kernel adapters get **no** owner rows in `app/services/sot_relationships.py`;
six known table collisions with the kernel (`parties`, `party_roles`, `roles`,
`user_credentials`, `audit_events`, `domain_settings`) re-verified on every rebase;
the ISP operator maps to exactly one platform `Tenant` behind an S7 ADR gate.
Compatibility is proved by `tests/architecture/test_kernel_compatibility.py`
(no kernel module reaches the import graph, middleware stack, or route table).

---

## Cross-cutting observations for the foundation programme

1. **Sub already solved runtime theming better than the starter.**
   `/branding/theme.css` + `brand_theme.py` (role remap, contrast gate, fail-safe)
   + `BrandProfile(scope_type ∈ platform|reseller|organization)` is a working
   per-scope white-label engine. A shared package should absorb this, not replace it.
2. **The component layer is a macro library, not a component system.** 80 macros,
   44% template adoption, but 279 templates hand-roll tables and 1526 raw
   `<button>`s / 2351 raw `<input>`s bypass it entirely. Its own architecture doc
   is marked "historical".
3. **Navigation has no registry.** 4 portals × hardcoded template lists, admin
   with a 60-entry hand-maintained active-page dict, plus 4 dead module toggles.
   No test prevents a dead sidebar link. This is the clearest gap vs
   `dotmac_starter_mt`'s manifest-driven `nav`.
4. **Mutation style differs from the starter.** Sub uses 418 plain
   `method="post"` forms with a hidden CSRF input; the starter mandates
   `hx-post` + a header bridge. A shared form/button component must support both.
5. **`tailwind.config.js` is dead under Tailwind v4** — its ~70-class dynamic
   colour safelist is not applied. Any macro that interpolates `{{ color }}` into
   a Tailwind class is relying on classes that may not be generated.
6. **`src/css/**` (27 files, 82 tokens) is git-tracked and completely
   unreferenced** — a third, incompatible token vocabulary that should be deleted
   or explicitly retired before a shared system lands.
7. **Locale, not brand, is the bigger white-label blocker**: 274 `NGN` + 43 `₦` +
   17 `Africa/Lagos` + 12 `+234` placeholders, with the `money` and `app_datetime`
   Jinja filters documented as fixed to NGN / Africa-Lagos.
8. **`app/services/email.py` is the one un-white-labelled output path** —
   hardcoded `DOTMAC_RED/GREEN/WHITE` constants used directly in customer-facing
   password-reset, email-verification, and portal-invite HTML.
