# F0 — UI surface / template / CSS / static / navigation inventory

**Scope:** `dotmac_erp` (primary) and `dotmac_vendor_control_plane` (lighter pass).
**Method:** read-only. Mechanical enumeration via `find`/`grep` and a static AST
route scanner (`ast.parse` over every `*.py`, collecting `APIRouter(prefix=...)`
assignments and `@router.<verb>` decorators). No tests run, no servers started,
no files in either repo modified.

**Snapshot:**
- `dotmac_erp` @ `cab1c98d` ("fix(people): preserve upload validation errors"), version `1.23.2`.
- `dotmac_vendor_control_plane` @ `4fb56c8` ("chore(deps): repin dotmac-kernel 0.1.0a7 -> 0.1.0a8"), version `0.1.0`.

---

# PART 1 — `dotmac_erp`

## 0. Orientation (what the repo says about itself)

| Doc | Role |
|---|---|
| `AGENTS.md` | Codex instructions. Declares stack: FastAPI + SQLAlchemy + Alembic, Redis/Celery, **Jinja2 + Tailwind (PostCSS) + Alpine/HTMX**. Points at `CLAUDE.md`, `.claude/rules/`, `UI_CONVENTIONS.md`, `CONSISTENCY_CHECKLIST.md`. |
| `UI_CONVENTIONS.md` | App-shell / branding / typography / button / sidebar / print conventions. Names `templates/partials/_sidebar_header.html`, `topbar()` from `templates/components/macros.html`, `_brand_context.html`, `_org_branding_head.html`, `_document_header.html`. |
| `.claude/rules/design-system.md` | The de-facto design system doc — token table, module accent table, status colour table, typography table, spacing scale. |
| `.claude/rules/templates.md`, `ui-ux.md`, `web-routes.md`, `forms.md`, `accounting-ui-ux-standard.md` | Additional UI rules. |
| `README.md`, `PRD.md`, `MERGE_PLAN.md`, `ARCHITECTURE_REVIEW.md` | Product/architecture narrative. |

Notable doc drift found while enumerating: `.claude/rules/templates.md` instructs
`{% from "components/_badges.html" import status_badge %}` — **that file does not
exist**; `status_badge` actually lives in `templates/components/macros.html:12`.

---

## 1. UI surfaces

`dotmac_erp` is a **single server-rendered Jinja2 monolith**. There is exactly
one template root (`templates/`) and one static root (`static/`), one shared
Jinja environment (`app/templates.py`), and one compiled stylesheet
(`static/css/app.css`). Every "surface" below is a URL-prefix + base-template
convention inside that one app, not an independently mountable surface.

### Present

| # | Surface | URL prefixes | Template dir / base | Auth model | Approx routes | Approx templates |
|---|---|---|---|---|---|---|
| 1 | **Staff/admin app shell (the product)** — Finance, People/HR, Inventory, Procurement, Projects, Fleet, Fixed Assets, Expense, Support, Public Sector, Coach | `/finance/*`, `/gl`, `/ap`, `/ar`, `/tax`, `/banking`, `/cons`, `/rpt`, `/lease`, `/people/*`, `/hr`, `/payroll`, `/leave`, `/attendance`, `/recruit`, `/training`, `/perf`, `/pms`, `/discipline`, `/scheduling`, `/inventory`, `/procurement`, `/projects`, `/fleet`, `/fixed-assets`, `/expense`, `/expenses`, `/support`, `/ipsas`, `/coach`, `/tasks`, `/settings`, `/automation`, `/import` | `templates/{finance,people,inventory,procurement,projects,fleet,fixed_assets,expense,support,public_sector,coach,settings,workflow_tasks}/` — **13+ separate `base_*.html` layouts** | Cookie session → `require_web_auth` + per-module `require_*_access` guards in `app/web/deps.py` | ~1,700 of the 1,866 `app/web` routes | ~700 |
| 2 | **Platform/system admin** | `/admin/*` (incl. `/admin/sync/crm`, `/admin/sync/dotmac-sub`) | `templates/admin/` → `admin/base_admin.html` | `require_admin_access` | 105 (`/admin` prefix) | 42 |
| 3 | **Employee self-service** | `/self/*` (mounted under `/people`), `/profile`, `/notifications` | `templates/people/self/*` → `people/base_people.html` | Same cookie session, role-scoped | 58 (`/self`) + profile/notifications | ~25 |
| 4 | **Public careers / job board (unauthenticated)** | `/careers/{org_slug}/...` and short alias `/c/...` | `templates/careers/` → `careers/base_careers.html` | **None** — public, `org_slug`-scoped; applicant status/offer flows gated by opaque token | 27 | 10 |
| 5 | **New-hire onboarding portal (token, unauthenticated)** | `/onboarding/start/{token}/...`, `/onboarding/expired` | `templates/onboarding/portal/` → `onboarding/portal/base_onboarding.html` | **None** — single-use token in URL only | 5 web routes (18 under `/onboarding` incl. admin side) | 5 |
| 6 | **Auth / account screens** | `/login`, `/logout`, `/admin/login`, `/forgot-password`, `/reset-password` | top-level `templates/login.html`, `admin_login.html`, `forgot_password.html`, `reset_password.html`, `reset_password_required.html` | Pre-auth | ~21 (`/auth`) + top-level | 5 |
| 7 | **Marketing landing + module launcher** | `/`, `/gov-erp`, `/dashboard` (redirect) | `templates/index.html`, `module_select.html`, `module_select_gov.html`, `partials/_module_launcher.html` | Optional auth (`optional_web_auth`); content from `landing_content()` in `app/web/deps.py:246` driven by `LANDING_*` env vars | 6 (`app/web_home.py`) | 3 |
| 8 | **Help centre** | `/help/*` | `templates/help/` → `help/base_help.html` | Authenticated | 20 | 11 |
| 9 | **JSON API** | `/api/v1/*` **and** an identical bare-path legacy alias (`_include_api_router` in `app/main.py:676` mounts every API router twice) | n/a | Bearer / session; `require_tenant_auth`, `require_role` | 1,139 decorators → ~2,278 effective paths after double-mount | n/a |
| 10 | **Print / PDF document surface** (WeasyPrint) | rendered inline off report/document routes | `templates/finance/reports/_pdf_base.html` (28 extenders), `templates/documents/` (11 letter templates) | Inherits caller's guard | — | 39 |
| 11 | **Transactional email surface** | n/a (Celery-rendered) | `templates/emails/` → `emails/base_email.html` + `base_email.txt` | n/a | — | 23 |
| 12 | **Error pages** | any | `templates/errors/{400,403,404,409,429,500}.html` | n/a | — | 6 |
| 13 | **Webhook / machine surfaces** | `/health`, `/health/live`, `/health/ready`, `/health/monitoring`, `/metrics`, `/v2/api-docs`, payments + CRM + `dotmac_sub` + academy webhooks | n/a | Unauthenticated health/metrics; signed webhooks | 10 in `main.py` + webhook routers | n/a |
| 14 | **Authenticated file downloads** | `/files/*`, `/uploads/*` (legacy alias) | n/a | Session | — | n/a |

### Absent — stated plainly

- **Customer / client portal** — ABSENT. No customer-facing invoice, statement,
  quote-acceptance or payment portal. AR customers exist only as staff-side records.
- **Supplier / vendor portal** — ABSENT. Procurement is staff-side only; no
  supplier login, RFQ response, PO acknowledgement or invoice-submission surface.
- **Partner / reseller portal** — ABSENT.
- **Mobile app / PWA** — ABSENT. No `manifest.json`, no `manifest.webmanifest`,
  no service worker, no `sw.js`, no app-icon set anywhere in the repo. The only
  mobile artefact is a *specification document*, `docs/mobile/dotmac-frontline-spec.md`.
- **API-only deployment profile** — ABSENT as a *switch*. `ENABLED_MODULES` turns
  whole modules (API **and** web together) on/off; there is no `WEB_ENABLED`-style
  surface switch that keeps the JSON API while dropping every HTML route.
- **A separate design-system / component-showcase surface** — ABSENT (no storybook,
  no `/styleguide` route).
- **Tenant-facing admin console distinct from staff admin** — ABSENT; `/admin` is
  the single admin surface.

---

## 2. Route inventory summary

Static AST scan of every `@router.<verb>` / `@app.<verb>` decorator under `app/`:

```
TOTAL route decorators                     3,015
  app/web/**                               1,866   (HTML surface)
  app/api/**                               1,139   (JSON surface)
  app/main.py                                 10   (health, metrics, favicon, redirects)
```

By HTTP method:

```
ALL:  GET 1,518 | POST 1,323 | PATCH 83 | DELETE 75 | PUT 16
web:  GET 1,038 | POST   828                                  (no PATCH/PUT/DELETE at all)
api:  GET   470 | POST   495 | PATCH 83 | DELETE 75 | PUT 16
```

HTML-vs-JSON:

```
web routes declaring response_class=HTMLResponse / Redirect / File / Streaming   1,214
web routes with no such response_class (JSON or default)                            652
api routes declaring HTMLResponse                                                     0
api routes returning JSON                                                         1,139
raw `response_class=HTMLResponse` occurrences: app/web 1,170 | app/api 0
```

**Effective path count is roughly 2× the API figure**: `_include_api_router()`
(`app/main.py:676-694`) mounts every API router at both the bare path *and*
`/api/v1`, deliberately, as a documented legacy alias.

Top-level prefixes by declared route count (router prefix + first path segment):

```
 145 /inventory      133 /payroll        121 /perf          116 /pms
 113 /training       111 /banking        105 /admin          88 /recruit
  83 /ap              81 /expenses        75 /projects       73 /settings
  73 /tax             72 /gl              71 /ar             70 /leave
  62 /fixed-assets    58 /self            56 /attendance     53 /employees
  49 /support         48 /scheduling      48 /hr             48 /reports
  46 /fleet           44 /expense         42 /discipline     36 /automation
  35 /procurement     34 /sync            33 /ipsas          31 /assets
  29 /me              27 /careers         27 /lease          24 /import
  24 /cons            23 /rpt             21 /auth           21 /payments
  20 /help            20 /limits          20 /rbac           20 /lifecycle
  18 /onboarding      16 /tasks           15 /sales-orders   14 /expense-limits
  14 /contracts       12 /service-hooks   11 /remita         11 /quotes
  10 /job-descriptions 10 /invoices       10 /reservations    9 /handbook
   9 /positions        9 /resources        9 /vehicles        8 /info-changes
```

Router modules: 129 `include_router(...)` calls; ~60 distinct `APIRouter(prefix=…)`
declarations under `app/web/` alone.

---

## 3. Template inventory

### Counts

```
templates/**/*.html                       865 files (ONE template root)
templates/*.html (top level)               10
  admin_login.html, base.html, forgot_password.html, help_center.html,
  index.html, login.html, module_select.html, module_select_gov.html,
  reset_password.html, reset_password_required.html
```

By first-level directory:

```
289 people          217 finance          51 inventory        42 admin
 34 expense          26 fleet            23 projects         23 emails
 20 fixed_assets     19 procurement      18 public_sector    12 support
 12 documents        11 help             10 careers           9 settings
  9 partials          7 components        7 coach             6 errors
  5 onboarding        1 each: workflow_tasks, sla_policies, operations,
                              notifications, modules
```

Referenced-from-Python: 756 distinct `*.html` string literals appear in `app/`;
865 exist on disk → **~109 templates (12.6%) have no direct Python reference**
(some are `{% include %}`-only partials or base layouts, but this is also the
orphan-template surface).

### Layouts / bases — 17 of them

```
280 extends  people/base_people.html
175          finance/base_finance.html
 71          modules/base_modules.html
 49          inventory/base_inventory.html
 41          admin/base_admin.html
 33          base.html                       <-- the only "root" layout
 28          finance/reports/_pdf_base.html
 28          expense/base_expense.html
 21          emails/base_email.html
 20          emails/base_email.txt
 19          fixed_assets/base_fixed_assets.html
 18          procurement/base_procurement.html
 17          public_sector/base_public_sector.html
 11          help/base_help.html
  9          careers/base_careers.html
  5          coach/base_coach.html
  4          onboarding/portal/base_onboarding.html
```

829 of 865 templates `{% extends %}` something; 56 do not (fragments + the base
files themselves + PDF/letter roots).

`templates/base.html` is 1,084 lines and is the only file that links the
stylesheet, fonts, favicon and vendored JS. The other 12 `base_*.html` files
(125–383 lines each) do **not** extend `base.html` in most cases — they each
re-declare their own `<aside>` sidebar.

### Component / partial conventions

`templates/components/` — 7 files, dominated by one enormous file:

```
124,769 bytes  macros.html         <-- 40 macros
 25,117 bytes  app_topbar.html
 24,842 bytes  _import_wizard.html
  6,184 bytes  _file_upload.html   <-- 1 macro
  2,009 bytes  settings_macros.html<-- 3 macros
  1,595 bytes  _coach_cards.html
  1,550 bytes  _change_history.html
```

`templates/partials/` — 9 files: `_brand_context.html`, `_document_header.html`,
`_license_banner.html`, `_module_launcher.html`, `_module_switcher.html`,
`_org_branding_head.html`, `_recent_activity.html`, `_sidebar_footer.html`,
`_sidebar_header.html`.

Macros in `macros.html` (the actual component library): `status_badge`,
`stats_card`, `empty_state`, `detail_error_state`, `aging_bar`, `progress_bar`,
`pivot_table`, `section_card`, `sparkline`, `icon_svg`, `icon_path`,
`chart_canvas`, `search_autosuggest`, `search_filter_bar`, `live_search`,
`compact_filters`, `filter_select_field`, `filter_entity_select_field`,
`filter_custom_select_field`, `filter_date_field`, `sortable_th`, `pagination`,
`data_table`, `currency`, `list_header_actions`, `action_buttons`,
`bulk_select_header`, `bulk_select_cell`, `bulk_action_bar`, `bulk_select_table`,
`bulk_icon_*` (5), `topbar`, `success_banner`, `error_banner`,
`period_shortcuts`, `export_menu`. `icon_path` is a ~108-branch `if/elif` chain
— the icon "system".

### Does a shared component library exist? — Yes, but it is thin relative to the surface, and duplication is heavy

**Verdict: a real but shallow macro library sitting on top of a large body of
copy-pasted markup.**

| Signal | Count | Reading |
|---|---|---|
| Templates importing `components/macros.html` | **731 / 865 (85%)** | The macro library *is* genuinely adopted for badges/stats/pagination/topbar. |
| `<table` tags written by hand in templates | **591** | vs… |
| `data_table(` macro invocations | **8** | …so **~99% of tables are hand-rolled**, not composed from the table component. |
| Literal `btn btn-primary` class strings | **609** | No button macro at all — the "component" is a CSS class string repeated 609 times. |
| Inline `<svg>` tags in templates | **366** across **142 files** | Despite an `icon_svg`/`icon_path` macro existing. |
| Inline `<script>` blocks | **146** | JS logic lives in templates, not in `static/js`. |
| Inline `<style>` blocks | **54** across **53 files** | CSS written outside the PostCSS pipeline entirely — invisible to the token system and to Tailwind purge. |
| Templates with Alpine `x-data` | **175** | Per-template component state, not shared behaviours. |
| Files re-implementing the sidebar (`sidebarCollapsed`) | **15** | 12 module `base_*.html` + 3 leaf pages that grew their own `<aside>`. |
| Byte-identical duplicate templates | **0** | Duplication is *near*-duplication (drifted copies), not exact copies — which is worse for maintenance. |

Three leaf pages carry a full `<aside>` sidebar rather than inheriting one —
`templates/admin/system/alert_detail.html`, `templates/help/article_detail.html`,
`templates/inventory/receipt_approval_detail.html` (plus
`templates/people/hr/position_form.html` and `templates/people/self/attendance.html`
with partial nav) — i.e. the shell has already leaked out of the layouts.

---

## 4. CSS / static-asset inventory

### Toolchain

- **Tailwind CSS v3** (`tailwindcss ^3.4.0`, lockfile resolves **3.4.19**) with a
  **JS config file** (`tailwind.config.js`, 3,750 bytes) — i.e. the *old*
  config-file model, **not** the v4 CSS-first `@theme` model. There is **no
  `@theme` block anywhere in the repo.**
- **PostCSS 8** (`postcss.config.js`: `postcss-import` → `tailwindcss` → `autoprefixer`),
  driven by `postcss-cli ^11.0.1`. Locked: `postcss 8.5.6`, `autoprefixer 10.4.23`.
- Build: `npm run build:css` = `postcss ./src/css/main.css -o ./static/css/app.css`;
  `watch:css` / `dev` are the `--watch` variants.
- `node_modules/` is not checked in; `package-lock.json` is (65 KB).

### Source vs compiled

```
SOURCE   src/css/main.css                       34 lines (import manifest)
         src/css/base/_tokens.css               <-- the single token file
         src/css/base/_base.css, _backgrounds.css
         src/css/components/  _badges _bulk-selection _buttons _cards
                              _command-palette _dashboard _documents
                              _empty-states _forms _loading _navigation
                              _tables _workflow                (13 files)
         src/css/layout/      _app-shell _responsive
         src/css/utilities/   _animations _helpers _print _touch
         → 23 source files total

COMPILED static/css/app.css                 13,864 lines  (checked in? yes — present in tree)
HAND-WRITTEN, OUTSIDE THE PIPELINE:
         static/css/fonts.css                    58 lines  (@font-face declarations)
         static/css/font-overrides.css           25 lines
PLUS     54 inline <style> blocks in 53 templates
```

`templates/base.html` links `/static/css/app.css?v=20260609a`,
`/static/css/font-overrides.css?v=20260609a` and `/static/css/fonts.css`
(manual cache-bust query string, not a content hash).

### Vendored JS (checked into `static/js/vendor/`, served locally — no CDN)

| File | Bytes | Version |
|---|---|---|
| `htmx.js` | 51,251 | **htmx 2.0.8** (per `package.json`/lock) |
| `htmx-loading-states.js` | 5,551 | **htmx-ext-loading-states 2.0.2** |
| `alpine.js` | 45,764 | **Alpine.js 3.15.3** |
| `alpine-collapse.js` | 1,448 | **@alpinejs/collapse 3.15.3** |
| `alpine-focus-trap.js` | 3,096 | **hand-written in-repo plugin** (docstring "Alpine.js Focus-Trap Plugin"), not the upstream `@alpinejs/focus` package — a local fork of a vendor capability |
| `chart.js` | 208,518 | **Chart.js v4.5.1** (banner comment) |
| `qrcode.js` | 19,928 | **qrcodejs 1.0.0** |

First-party JS in `static/js/` (14 files): `accessibility-guardrails.js`,
`avatar.js`, `bulk-actions.js`, `charts.js`, `command-palette.js`,
`compact-filters.js`, `csv-parser.js`, `file-upload.js`, `fx-rate-lookup.js`,
`import-wizard.js`, `live-search.js`, `pivot-table.js`, `table-sort.js`,
`typeahead.js`.

### Fonts

Self-hosted variable woff2, 6 files, from `@fontsource-variable`:

```
static/fonts/dm-sans/{normal,italic}          (DM Sans        5.2.8)   — body / sans
static/fonts/fraunces/{normal,italic}         (Fraunces       5.2.9)   — display / headings
static/fonts/jetbrains-mono/{normal,italic}   (JetBrains Mono 5.2.8)   — numeric / mono
```

`base.html` preloads dm-sans-normal and fraunces-normal.

### Icons

**No icon font, no SVG sprite, no icon package.** Two mechanisms coexist:
1. `icon_svg(name)` / `icon_path(name)` macros in `macros.html` — a ~108-branch
   `if/elif` chain of inline `<path d="…">` strings (Heroicons-shaped paths,
   hand-copied).
2. 366 raw `<svg>` tags pasted directly into 142 templates.
Plus 4 icons encoded as `data:image/svg+xml` CSS custom properties in
`_tokens.css` (`--icon-chevron-down`, `--icon-error`, `--icon-check`, `--icon-search`),
with a dark-mode override for `--icon-chevron-down` only.

### Logos / imagery / favicons / PWA

- **Favicon:** exactly one — `static/favicon.svg`. `/favicon.ico` and
  `/favicon.svg` are both `RedirectResponse` shims in `app/main.py:890-899`.
  Per-organisation override supported via `org_branding.favicon_url`
  (`base.html` derives the MIME type from the extension).
- **Logos:** no product logo file is checked in. Brand mark is either an
  uploaded `brand_logo_url` / `logo_dark_url`, or a **2-letter initial derived
  from the brand name** (`_brand_mark()` in `app/web/deps.py:110`).
- **Illustrations:** 38 PNGs in `static/img/illustrations/` — 12 `module-*.png`,
  8 `empty-*.png`, 3 `error-*.png`, 6 `hero-*.png`, 2 `email-header-*.png`,
  plus `login-hero`, `maintenance`, `getting-started`, `help-*` (3),
  `onboarding-welcome`. These are raster PNGs; they are **not** theme-aware and
  **not** re-brandable without replacing the files.
- **PWA assets:** NONE. No manifest, no service worker, no maskable icons, no
  apple-touch-icon.
- `static/avatars/` contains only `.gitkeep`.

### Design tokens — the actual list

**77 distinct CSS custom properties are defined, all in one file
(`src/css/base/_tokens.css`).** Zero custom properties are defined anywhere else
in `src/css/`. Runtime branding injects ~25 more (see §6).

`:root` block:

```
INK / TEXT      --ink  --ink-light  --ink-muted  --ink-faint
BACKGROUNDS     --parchment  --parchment-dark
ACCENTS         --gold  --gold-light  --gold-dark
                --teal  --teal-light  --teal-dark
FINANCE SUB-MODULE ACCENTS
                --module-gl  --module-ap  --module-ar  --module-inv
                --module-fa  --module-tax --module-banking
                --module-reports  --module-automation
APP MODULE ACCENTS (each ×3: base/-light/-dark)
                --module-finance{,-light,-dark}
                --module-people{,-light,-dark}
                --module-expense{,-light,-dark}
                --module-operations{,-light,-dark}
                --module-admin{,-light,-dark}
SPACING         --spacing-xs --spacing-sm --spacing-md --spacing-lg --spacing-xl
                --spacing-2xl --spacing-3xl
                --spacing-input-x --spacing-input-y
                --spacing-card --spacing-card-sm --spacing-card-lg
                --content-gutter
RADIUS          --radius-sm --radius-md --radius-lg --radius-xl --radius-2xl
                --radius-card --radius-btn --radius-input --radius-badge --radius-icon
COMPONENT       --card-bg --card-border --card-shadow --card-shadow-hover
                --input-border --input-focus
                --btn-shadow --btn-shadow-hover
FOCUS           --ring-color --ring-offset --ring-width
BRAND (aliases) --brand-primary  --brand-primary-light  --brand-accent
ICONS           --icon-chevron-down --icon-error --icon-check --icon-search
```

`.dark` block redefines 14: `--ink`, `--ink-light`, `--ink-muted`, `--ink-faint`,
`--parchment`, `--parchment-dark`, `--card-bg`, `--card-border`, `--card-shadow`,
`--card-shadow-hover`, `--input-border`, `--btn-shadow`, `--btn-shadow-hover`,
`--ring-color`, `--icon-chevron-down` (+ `color-scheme: dark`).

### Verdict: tokens are named by VALUE, not by ROLE

This is the single most consequential white-label finding in the CSS layer.

- The two primary brand colours are literally named after their **hues**:
  **`--teal`**, `--teal-light`, `--teal-dark`, **`--gold`**, `--gold-light`,
  `--gold-dark`. A white-label deployment whose brand is red gets
  `--teal: #dc2626`.
- The background token is named **`--parchment`** — a material, i.e. a value.
- Only a 3-token sliver is role-named: `--brand-primary`, `--brand-primary-light`,
  `--brand-accent` — and those are defined as *aliases of the value-named ones*
  (`--brand-primary: var(--teal)`), so the value names remain the real identity.
- `--ink*` is semi-role-ish (text colour) but still a material metaphor and
  carries no role gradation (no `--text-primary` / `--text-secondary` /
  `--text-disabled`).
- Component tokens (`--card-bg`, `--input-border`, `--input-focus`, `--ring-color`,
  `--btn-shadow`) **are** role-named — this is the healthy part.
- `--module-*` tokens are role-named *by product module*, not by semantic role,
  which hard-wires a 13-module IA into the token layer.
- There is **no semantic status token set at all** — no `--color-success`,
  `--color-danger`, `--color-warning`. Status colour is expressed only as raw
  Tailwind utility strings (`text-emerald-700 dark:text-emerald-400` …) in
  `.claude/rules/design-system.md` and repeated inline in templates.
- Tailwind's own theme (`tailwind.config.js`) duplicates the palette a **third**
  time as `primary.50…950`, `accent.50…950`, `ink.*`, `module-*` — role-named at
  the Tailwind layer (`primary`/`accent`) but with hard-coded hex values that are
  **not** wired to the CSS custom properties. So the same colour exists as
  (a) a CSS var named by hue, (b) a Tailwind colour named by role, (c) a hex
  literal in `.claude/rules/design-system.md`, and (d) hundreds of raw
  `teal-600`/`emerald-50`/`rose-700` utility classes in templates. **Four
  parallel colour authorities.**

Radius/spacing tokens are duplicated the same way: `--radius-card: 16px` in
`_tokens.css` *and* `borderRadius.card: '16px'` in `tailwind.config.js`;
`--spacing-card: 24px` *and* `spacing.card: '24px'`.

`tailwind.config.js` also carries a **56-entry `safelist`** of responsive and
module-colour utility strings — a symptom of dynamic class construction in
templates that Tailwind's purge cannot see.

---

## 5. Navigation inventory

**Verdict: navigation is HARDCODED in templates. There is no nav registry, no
manifest, no Python-side nav model.**

- `grep` for `nav_items` / `NAV_ITEMS` / `NavItem` across `app/**/*.py` returns
  **no navigation registry** — the hits are unrelated (`help.py` article nav,
  branding schemas).
- Each module's sidebar is a hand-written `<aside>` inside that module's
  `base_*.html`, with literal `<a href="…">` entries:

```
16 hrefs  templates/people/base_people.html
16        templates/admin/base_admin.html
13        templates/finance/base_finance.html
11        templates/help/base_help.html
10        templates/public_sector/base_public_sector.html
 9        templates/procurement/base_procurement.html
 9        templates/expense/base_expense.html
 8        templates/fixed_assets/base_fixed_assets.html
 6        templates/inventory/base_inventory.html
 5        templates/modules/base_modules.html
 4        templates/coach/base_coach.html
(+ 3 leaf pages that grew their own <aside>: admin/system/alert_detail.html,
   help/article_detail.html, inventory/receipt_approval_detail.html)
```

- The **cross-module launcher** (`templates/partials/_module_launcher.html`) is
  a Jinja `{% if "<module>" in modules %}{% set launcher_modules = launcher_modules + [{...}] %}{% endif %}`
  chain — 14 hand-written blocks, each hardcoding label, href, icon PNG path and
  Tailwind accent class. Its only dynamism is filtering against
  `accessible_modules`; and when `user.is_admin and not modules` it falls back to
  a **hardcoded list of 13 module keys inline in the template** (lines 4-19).
- `templates/partials/_module_switcher.html` and `_sidebar_header.html` /
  `_sidebar_footer.html` are the only shared shell partials (used by 10-12 files each).
- Server-side module enablement is `is_module_enabled(<name>)` in `app/main.py:150`
  against `ENABLED_MODULES` — this gates **route mounting** but is *separately*
  mirrored in the launcher template's `modules` list, so a disabled module can
  still be linked unless both are kept in sync manually. There is no test
  enforcing that a nav link resolves to a mounted route.
- `topbar()` (`macros.html:1832`) is the one genuinely shared shell component;
  it takes `launcher_modules` and `accent` as parameters (accent default `"teal"`).
- `static/js/command-palette.js` provides a keyboard command palette — its
  entries are likewise not derived from a registry.

---

## 6. Branding / theming mechanisms already present

ERP actually has a **substantial per-organisation branding system** — more than
the starter's. Layers, outermost first:

**(a) Build-time / product defaults**
- `tailwind.config.js` palette + `src/css/base/_tokens.css` `:root`.

**(b) Deployment-level env config** (`app/config.py:66-107`, `.env.example:131-146`)
```
BRAND_NAME       (default "Dotmac ERP")     BRAND_TAGLINE
BRAND_LOGO_URL   BRAND_MARK                 APP_VERSION
LANDING_HERO_BADGE / _TITLE / _SUBTITLE     LANDING_CTA_PRIMARY / _SECONDARY
LANDING_CONTENT_JSON  (whole-landing-page content override as JSON)
BRANDING_URL_PREFIX (default /static/branding)
BRANDING_MAX_UPLOAD_SIZE  BRANDING_ALLOWED_TYPES
```

**(c) Per-organisation branding rows** — `OrganizationBranding`
(`app/models/finance/core_org/organization_branding.py`), 25 branding columns:
```
display_name, tagline, logo_url, logo_dark_url, favicon_url, brand_mark,
primary_color, primary_light, primary_dark,
accent_color, accent_light, accent_dark,
success_color, warning_color, danger_color,
font_family_display, font_family_body, font_family_mono,
border_radius (enum BorderRadiusStyle), button_style (enum ButtonStyle),
sidebar_style (enum SidebarStyle),
custom_css (free text), is_active
```

**(d) Runtime CSS generation** — `app/services/finance/branding.py` (648 lines)
derives a full tint/shade ramp from `primary_color`/`accent_color` and emits a
`<style>` block overriding the custom properties:
```
--teal, --teal-light, --teal-dark
--brand-primary, --brand-primary-{50,100,200,500,600,700,900}
--gold, --gold-light, --gold-dark, --brand-accent
--brand-success, --brand-warning, --brand-danger
--font-display, --font-body, --font-mono
--border-radius-base, --border-radius-card, --border-radius-btn
```
plus `!important` rules for `button_style` and `sidebar_style` variants
(e.g. `background: var(--brand-primary, var(--teal)) !important`).
**Note:** it writes `--brand-success/warning/danger`, which *are not defined in
`_tokens.css` and are not consumed by any component CSS* — a dead branding
channel. It also has to override the hue-named `--teal`/`--gold` because those
are what the stylesheet actually reads.

**(e) Template injection points**
- `templates/partials/_brand_context.html` — normalises `brand_name`,
  `brand_tagline`, `brand_logo_url`, `brand_logo_dark_url`, `brand_mark`,
  `report_logo_url`, `document_logo_url`. Included by 11 templates.
- `templates/partials/_org_branding_head.html` — injects `org_branding.fonts_url`
  as a `<link>` and `org_branding.css | safe` inside `<style id="org-branding">`,
  carrying a `{# nosemgrep: semgrep.safe-on-user-content #}` suppression. Included
  by 10 templates. **This is a raw `| safe` on database-sourced CSS with a
  semgrep suppression rather than a sanitiser** — contrast the starter's
  `sanitize_branding_css` requirement.
- `resolve_brand_context()` / `org_brand_context()` / `base_context()` in
  `app/web/deps.py` (lines 124-1160) build the `brand` dict per request.

**(f) Document / print branding**
- `templates/partials/_document_header.html` (included by 24 templates) —
  letterhead using `document_logo_url`.
- `templates/finance/reports/_pdf_base.html` uses `{{ primary_color | default('#0d9488') }}`
  — a hardcoded teal fallback baked into the PDF stylesheet.
- `templates/documents/` — 11 HR letter templates extending `base_letter.html`.

**(g) Email branding**
- `templates/emails/base_email.html` / `.txt`; 2 branded header images
  (`static/img/illustrations/email-header-finance.png`, `-hr.png`) — raster,
  not re-brandable.
- `app/models/email_profile.py:139` default sender display name `"Dotmac ERP"`.

**(h) Careers-portal branding** — `GET /careers/{org_slug}/branding/{filename}`
(`app/web/careers.py:228`) serves per-org branding assets to the public portal.

---

## 7. Hardcoded brand / product-name leakage

Raw `dotmac` (case-insensitive) hit counts:

| Area | Hits | Files |
|---|---:|---:|
| `app/` | **820** | 108 |
| `scripts/` | 313 | 97 |
| `docs/` | 101 | 21 |
| `alembic/` | 92 | 21 |
| `templates/` | **60** | 23 |
| `tools/` | 4 | 1 |
| `static/` | 2 | 2 |
| `locales/` | 0 | 0 |
| `license/` | 0 | 0 |

**Important qualification:** most of the `app/` volume is *integration module
naming*, not display-string leakage — `dotmac_sub`, `dotmac_crm`,
`dotmac_academy` are the names of sibling Dotmac systems this ERP integrates
with, so those identifiers are legitimate (they name an external system, the
way `stripe` would). Filtering those out:

**Display-name leakage (`DotMac` / `Dotmac` as a user-visible or product-identity
string, excluding integration module names): 92 occurrences.**

### Worst offenders — user-visible, must be templated for white-label

| file:line | Leak | Severity |
|---|---|---|
| `app/config.py:69` | `brand_name: str = os.getenv("BRAND_NAME", "Dotmac ERP")` | Default only — env-overridable. Acceptable pattern. |
| `app/config.py:98` | `landing_hero_badge = os.getenv("LANDING_HERO_BADGE", "Dotmac ERP")` | Default only. |
| `app/config.py:258-262` | Auto-reply body: `"Dotmac Technologies, and this mailbox is no longer being monitored."`, plus **`support@dotmac.ng`**, **`sales@dotmac.ng`**, **`inactives@dotmac.ng`** as defaults | **HIGH** — legal-entity name + Dotmac support/sales addresses baked into outbound email defaults. |
| `templates/partials/_brand_context.html:6` | `title \| default("Dotmac", true)` — final fallback brand name | **HIGH** — the last-resort brand name in the shared brand normaliser is literally "Dotmac". |
| `templates/module_select.html:16`, `module_select_gov.html:16` | `{{ brand.name \| default("Dotmac") }}` | HIGH — module launcher landing. |
| `templates/workflow_tasks/list.html:4`, `templates/notifications/list.html:4` | `<title>… - {{ brand.name \| default("Dotmac") }}` | MED — page titles. |
| `templates/finance/payments/callback.html:139` | `Powered by DotMac ERP` | **HIGH** — payment return page seen by the *payer*, a third party, with no brand variable at all. |
| `templates/help/glossary.html:20` | `Key terms and concepts used across DotMac ERP modules.` | **HIGH** — help content, no variable. |
| `templates/admin/user_form.html:248` | `Leave blank to use the default temporary password (Dotmac@123)` | **HIGH** — brand string *and* a hardcoded default credential surfaced in the UI. |
| `templates/people/hr/employee_detail.html:305` | `placeholder="Leave blank to use default (Dotmac@123)"` (×2 attrs) | **HIGH** — same, in an `aria-label` and a `placeholder`. |
| `templates/admin/settings/email.html:137,246` and `templates/finance/settings/email.html:133` | `placeholder="Dotmac ERP"` | MED — placeholder text. |
| `templates/admin/settings/email.html:157` | `hr@dotmac.ng` (placeholder) | MED — Dotmac domain in a placeholder. |
| `app/models/email_profile.py:139` | `default="Dotmac ERP"` sender display name | **HIGH** — DB column default; leaks into every outbound email of a fresh install. |
| `app/tasks/notifications.py:176` | `"<p>You have a new notification in Dotmac ERP.</p>"` | **HIGH** — email body literal, no variable. |
| `app/main.py:207` | `app = FastAPI(title="DotMac ERP API", …)` | MED — OpenAPI/Swagger title shown to API consumers. |
| `app/web/deps.py:263` | `"title": "Why teams choose Dotmac ERP"` (landing benefits block) | **HIGH** — marketing copy in Python, only overridable wholesale via `LANDING_CONTENT_JSON`. |
| `app/startup.py:356` | `logger.info("Dotmac ERP Starting")` | LOW — log line. |
| `.env.example:131,141` | `BRAND_NAME=Dotmac ERP`, `LANDING_HERO_BADGE=Dotmac ERP` | LOW — example file. |
| `templates/admin/sync/crm/*.html` (5 files, 15 hits), `templates/admin/sync/dotmac_sub/*.html` (3 files, 21 hits), `templates/people/hr/employee_form.html:682` (`DotMac Sub`) | "DotMac CRM" / "DotMac Sub" as the *name of the integrated system* | LOW-MED — legitimate as a system name, but the whole `/admin/sync/*` surface is Dotmac-fleet-specific and would need to be gated or renamed in an OEM build. |

### Domains / emails / URLs found

```
support@dotmac.ng      app/config.py:260
sales@dotmac.ng        app/config.py:262
inactives@dotmac.ng    app/config.py:248
hr@dotmac.ng           templates/admin/settings/email.html:157  (placeholder)
https://selfcare.dotmac.io   (×2)
https://crm.dotmac.io        (×1)
authors = "DotMac <dev@dotmac.ng>"   pyproject.toml
```

`static/` is nearly clean (2 hits). `locales/en.json` has **zero** brand hits —
but note there is only one locale file and the i18n `t()` helper is registered
globally, so most UI strings are *not* going through i18n at all.

### Summary judgement

- **Config-level branding is done well** — 11+ env knobs with sane defaults, plus
  a 25-column per-org branding table and runtime CSS generation.
- **The leak surface is the long tail**: ~92 display-string occurrences where a
  developer wrote the literal instead of reading `brand.name`, concentrated in
  (1) email bodies/defaults, (2) the payment-callback and help pages, (3)
  hardcoded default-password hint strings, (4) the Jinja `default("Dotmac")`
  fallbacks that make "Dotmac" the *last-resort* brand identity.
- There is **no test or lint rule anywhere in the repo** that forbids a hardcoded
  brand literal in a template or in Python.

---

## 8. Kernel adoption status

**`dotmac-kernel` is NOT consumed. Adoption is ZERO.**

Evidence:
- `pyproject.toml` — no `dotmac-kernel` dependency. The only Dotmac dependency is
  `dotmac-integration-client` (git tag `v0.1.1`), an unrelated HTTP client.
- `poetry.lock` / `uv.lock` — no `dotmac_kernel` entry.
- `.venv/lib/*/site-packages/` contains `dotmac_integration` only.
- No `dotmac_kernel` directory vendored anywhere in the tree.
- **No import-linter at all.** `pyproject.toml` has no `[tool.importlinter]`
  section, there is no `.importlinter` file, and no `lint-imports` target.
  → There is **no import allowlist to report**; ERP has no mechanism to constrain
  which kernel modules it may use, because it uses none.
- `AGENTS.md` never mentions the kernel; it predates ADR-0003.
- `tests/architecture/` (7 files) enforces ERP-local boundaries only —
  `test_identity_protocol_boundary.py`, `test_replaceable_application_boundary.py`,
  `test_sot_registry_liveness.py`, `test_openapi_contract_surface.py`,
  `test_webhook_org_attribution.py`, `test_metrics_scrape_safety.py`.

### Parallel implementations of kernel concerns (NOT forked kernel code)

ERP predates the kernel, so these are **independent prior implementations that
now overlap the kernel's responsibilities** — not copies of kernel files. No
kernel file has been copied into this repo. For F0 purposes they are the
*adoption surface*:

| Kernel module | ERP's parallel implementation | Size |
|---|---|---|
| `dotmac_kernel.templating` | `app/templates.py` (single shared `Jinja2Templates` + globals + filters) | 266 lines |
| `dotmac_kernel.deps` (route guards) | `app/api/deps.py` **and** `app/web/deps.py` (two separate guard seams — cookie and bearer do NOT share one `authenticate_request`) | `web/deps.py` alone >1,900 lines |
| `dotmac_kernel.branding` | `app/services/finance/branding.py` + `app/schemas/finance/branding.py` + `OrganizationBranding` model | 648 lines |
| `dotmac_kernel.features` (manifest/nav/registry) | none — replaced by `is_module_enabled()` string checks in `app/main.py` + hardcoded template nav | — |
| `dotmac_kernel.settings_resolver` / `settings_models` | `app/api/settings.py`, `app/services/admin/settings_web.py` | — |
| `dotmac_kernel.audit` | `app/models/audit.py`, `app/services/audit.py`, `app/api/audit.py`, `app/schemas/audit.py`, `app/tasks/audit.py` | — |
| `dotmac_kernel.middleware.*` | `app/middleware/` package | — |
| `dotmac_kernel.licensing` (WS8) | `app/licensing/` — `enforcement.py` (208), `validator.py` (86), `schema.py` (70), `state.py` (55), `fingerprint.py` (54) | **478 lines, a completely independent licensing scheme** |
| `dotmac_kernel.db` (transaction authority) | `app/db/`, `app/rls.py`, `get_db` / `get_db_for_org` in `app/web/deps.py` | — |

**The ERP licensing module is the sharpest boundary conflict**: `app/licensing/`
implements its own envelope schema, validator, fingerprint and enforcement,
entirely disjoint from the kernel's WS8 (`dotmac_kernel.licensing`) that the
vendor control plane issues against and the starter's `licensing` feature
receives. Two incompatible licence formats exist in the fleet today.

---

# PART 2 — `dotmac_vendor_control_plane` (lighter pass)

## Surfaces

| Surface | URL prefix | Auth | Routes |
|---|---|---|---|
| **JSON API — platform-admin only** | `/platform/vendor/*` (`offer-versions`, `contracts`, `provisioning`, `accounts`, `allocations`, `licences`, `approvals`) | `dotmac_kernel.platform_auth.require_platform_admin` — deny-case D4 forbids re-implementing auth | **39** |
| **Admin console shell (HTML)** | `GET /admin` | same | **1** |
| **TOTAL** | | | **40** |

Route breakdown by feature: licensing 15, contracts 10, provisioning 4,
approvals 3, offers 3, accounts 3, allocations 1, console 1.
Methods: POST 24, GET 16. No PUT/PATCH/DELETE.

**Absent:** customer portal, supplier portal, public surface, staff portal
beyond the shell, mobile/PWA, marketing/landing, email templates, PDF/document
templates, error pages. Everything except the one shell route is API-only.

## Templates / static

**There is no `templates/` directory and no `static/` directory.** Zero `.html`,
`.css` or `.js` files exist anywhere in the repo (outside `.venv`/`.git`).

The entire HTML surface is a **28-line Python string constant** `_SHELL` in
`src/vendor_cp/console/web.py`:

```
<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>DotMac Vendor Control Plane</title></head>
<body><h1>DotMac Vendor Control Plane</h1>
<p>Administration console shell (platform-admin only).</p></body></html>
```

It is explicitly documented as "a slice-2 placeholder shell".

## CSS toolchain

**None.** No `package.json`, no `tailwind.config.js`, no `postcss.config.js`,
no `node_modules`, no CSS file, no design tokens, no fonts, no icons, no
favicon, no logo, no PWA assets. Nothing to inventory.

## Navigation mechanism

**Manifest-derived — the correct pattern, already in place.**
`src/vendor_cp/console/feature.py` declares:

```python
feature = FeatureManifest(
    name="console",
    web_routers=[router],
    nav=(NavItem(label="Vendor Console", path="/admin"),),
    core=False,
    enabled_by_default=True,
)
```

Nav comes from `dotmac_kernel.features.NavItem` on the manifest; the kernel
mounts it. There is no hardcoded nav list anywhere. (The rendered sidebar would
come from the kernel's `install_surface_globals` + kernel templates — this repo
supplies no template of its own, so the `nav` entry is currently declared but
not visually rendered by any local page.)

## Branding mechanism

**None locally.** Whatever branding exists comes from `dotmac_kernel.branding`
via the kernel's app factory. The one HTML page hardcodes the title and `<h1>`
as `DotMac Vendor Control Plane` with no brand variable.

## Brand leakage scale

| Area | `dotmac` hits | files |
|---|---:|---:|
| `src/` | 89 | 44 |
| `tests/` | 89 | 18 |
| `docs/` | 35 | 6 |
| `alembic/` | 3 | 1 |
| `scripts/` | 1 | 1 |

**Almost all of this is `dotmac_kernel` import statements** — i.e. dependency
naming, not brand leakage. Filtering those out, display-string leakage is
**exactly 3 occurrences**:

```
src/vendor_cp/__init__.py:1     """DotMac Vendor Control Plane.
src/vendor_cp/console/web.py:20 <title>DotMac Vendor Control Plane</title>
src/vendor_cp/console/web.py:22 <h1>DotMac Vendor Control Plane</h1>
```

Plus a `secret/dotmac/licensing/signing-key` OpenBao path and
`/run/secrets/dotmac/vendor-control-plane/...` deployment paths in
`src/vendor_cp/config.py` docstrings (operational, not UI), and
`https://registry.dotmac.io/api/packages/dotmac/pypi/simple` in `pyproject.toml`.

**This repo is effectively brand-clean.** Note it is also the *vendor's own*
control plane, so a Dotmac identity here is arguably correct rather than a leak.

## Kernel pin

```
dotmac-kernel = { version = "0.1.0a8",
                  extras = ["testing", "licensing"],
                  source = "forgejo" }
```

- **Exact pin** (`0.1.0a8` — no caret), resolved **only** from the private
  Forgejo index `https://registry.dotmac.io/api/packages/dotmac/pypi/simple`,
  declared `priority = "explicit"` so only `dotmac-kernel` is pulled from it
  (dependency-confusion guard).
- Python `>=3.12,<3.14`; mypy `strict = true` over `src/vendor_cp`.

**Doc drift found:** `README.md` still says *"consumed as `dotmac-kernel==0.1.0a1`"*
while `pyproject.toml` pins `0.1.0a8` (the repo's most recent commit,
`4fb56c8`, was the a7→a8 bump). The README pin statement is stale by seven alphas.

### Kernel import allowlist

There is **no import-linter** config in this repo. The allowlist is enforced
instead by a runtime architecture test — `tests/architecture/test_deny_cases.py`
deny-cases **D1–D6**:

| Deny case | Rule |
|---|---|
| D1 | One control-plane database; kernel owns the engine. No `create_engine`/`sessionmaker`, no product DSNs. |
| D2 | No product data-plane imports (`dotmac_sub`/`crm`/`erp`/`app`). |
| D3 | Fake providers only; a real-provider `VENDOR_PROVIDER_MODE` fails startup; no real-provider SDKs. |
| D4 | Platform-admin auth **through the kernel** (`require_platform_admin`) on every web route; never re-implemented. |
| D5 | **Only the kernel's public surface** — `test_d5_only_public_kernel_surface_is_imported` walks every vendor `.py`, and for each `from dotmac_kernel…` import asserts: top-level names ∈ `dotmac_kernel.__all__`; submodules ∈ `SUPPORTED_MODULES`; never in `INTERNAL_MODULES`; never an `_`-prefixed name. |
| D6 | No plan/mode/tier/profile string branching in `contracts/`. |

So the "allowlist" is dynamic — it is exactly the kernel's own
`SUPPORTED_MODULES` frozenset (53 modules: `app_factory`, `assembly`, `audit`,
`branding`, `capabilities`, `config`, `crud`, `db`, `deps`, `entitlements`,
`errors`, `exceptions`, `features`, `identity`, `licensing`, `logging`,
`messaging.*` (11), `middleware.{csrf,observability,rate_limit,security_headers,tenant}`,
`migrations`, `models`, `models_platform`, `modules`, `money`, `platform_auth`,
`profiles`, `providers`, `providers.provisioning`, `query`, `security`,
`settings_admin`, `settings_models`, `settings_resolver`, `templating`,
`testing.*` (4), `web_deps`) plus the top-level `__all__`.

**Kernel modules actually imported today** (occurrence counts):

```
28  from dotmac_kernel            (top-level public names)
 9  from dotmac_kernel.messaging
 8  from dotmac_kernel.platform_auth
 8  from dotmac_kernel.features
 6  from dotmac_kernel.db
 3  from dotmac_kernel.providers.provisioning
 3  from dotmac_kernel.licensing
 1  from dotmac_kernel.testing
 1  from dotmac_kernel.migrations
```

Notably **`dotmac_kernel.templating`, `dotmac_kernel.branding`,
`dotmac_kernel.web_deps` and `dotmac_kernel.settings_resolver` are permitted but
unused** — consistent with the repo having no UI yet.

---

# PART 3 — Copied / forked kernel code (the key F0 check)

Grep for tell-tale duplicated kernel module names outside a `dotmac_kernel`
import path (`templating.py`, `settings_resolver.py`, `branding.py`,
`features.py`, `deps.py`, `audit.py`, `security.py`, `middleware/`), plus a
search for any vendored `dotmac_kernel` directory.

## `dotmac_erp`

- **No vendored `dotmac_kernel` directory.** `find . -type d -name dotmac_kernel`
  (excluding `.venv`/`.git`) returns nothing.
- **No copied kernel files.** Nothing in `app/` is a byte-copy or a renamed copy
  of a kernel module — the kernel was never a dependency, so there was nothing
  to copy.
- **But it has full parallel implementations of nine kernel concerns** (table in
  §8): `app/templates.py`, `app/api/deps.py` + `app/web/deps.py`,
  `app/services/finance/branding.py`, `app/middleware/`, `app/rls.py`,
  `app/models/audit.py` + `app/services/audit.py`, `app/api/settings.py`,
  `app/db/`, and — most significantly — `app/licensing/` (478 lines
  implementing an entirely separate licence envelope/validator/enforcement
  scheme from `dotmac_kernel.licensing` WS8).
- **Framing for F0:** this is not *fork drift*, it is *pre-kernel divergence*.
  ERP is a greenfield adoption target, not a de-forking exercise. The
  adoption cost is the nine parallel subsystems plus the 865-template /
  ~3,000-route surface built on them.
- One genuine **vendor-library** fork does exist:
  `static/js/vendor/alpine-focus-trap.js` is a hand-written 3 KB re-implementation
  of the upstream `@alpinejs/focus` plugin, sitting in the `vendor/` directory
  alongside real upstream builds — i.e. a local fork disguised as a vendored lib.

## `dotmac_vendor_control_plane`

- **No copied kernel code.** Confirmed by `test_d5_only_public_kernel_surface_is_imported`,
  which fails the build on any private name, any `INTERNAL_MODULES` import, and
  any non-`SUPPORTED_MODULES` submodule.
- No local `templating.py` / `branding.py` / `features.py` / `deps.py` /
  `settings_resolver.py` exists. The only near-name is
  `src/vendor_cp/config.py`, which deliberately holds **only** vendor-specific
  knobs (provider mode, licence signing mode/key path/offered capabilities) and
  documents in its docstring that the kernel owns `DATABASE_URL`, the engine,
  auth and security config (deny-case D1).
- `src/vendor_cp/migrations.py` composes the vendor Alembic lineage with the
  kernel's shipped `dotmac_kernel.migrations.versions_dir` rather than copying
  base migrations.
- **Verdict: clean.** This repo is the reference for how an assembly should
  consume the kernel.

---

# Headline findings for F0

1. **ERP has zero kernel adoption and no import governance** — no
   `dotmac-kernel` dependency, no import-linter, no allowlist. Nine kernel
   concerns exist as independent ERP implementations, including a *second,
   incompatible licensing scheme* (`app/licensing/`, 478 lines) that is disjoint
   from kernel WS8.
2. **ERP design tokens are named by VALUE, not by ROLE** — the two brand colours
   are `--teal` and `--gold`, the background is `--parchment`. Only 3 role-named
   brand aliases exist, and they are defined *as* aliases of the hue names.
   Runtime branding therefore has to overwrite `--teal` with, say, a red — the
   token layer actively fights white-labelling. There are **four parallel colour
   authorities** (CSS vars / Tailwind theme / design-system.md hex table / raw
   utility classes in 865 templates) and **no semantic status tokens at all**.
3. **ERP navigation is 100% hardcoded in templates** — 12 module `base_*.html`
   files each hand-write their own `<aside>`, and the cross-module launcher is a
   14-block Jinja `if` chain with a hardcoded 13-module fallback list. No
   registry, no manifest, no test that a nav link resolves to a mounted route.
4. **ERP's component library is adopted but shallow** — 731/865 templates import
   `macros.html`, yet 591 hand-written `<table>`s vs 8 `data_table()` calls, 609
   literal `btn btn-primary` strings, 366 inline `<svg>`s, 146 inline `<script>`s
   and 54 inline `<style>` blocks. Zero byte-identical files means the duplication
   is *drifted* copies.
5. **ERP has real per-org branding infrastructure** (25-column model + 648-line
   runtime CSS generator + careers-portal asset serving) — better than the
   starter's — but injects DB-sourced CSS via `| safe` under a semgrep
   suppression rather than a sanitiser, and generates `--brand-success/warning/danger`
   that nothing consumes.
6. **ERP brand leakage is ~92 display-string occurrences**, concentrated in
   email defaults/bodies, the third-party-visible payment callback page,
   help content, and `default("Dotmac")` Jinja fallbacks — plus three
   `@dotmac.ng` addresses as config defaults. No lint or test forbids it.
7. **Customer, supplier and partner portals, and any mobile/PWA surface, are
   entirely absent from ERP** — and there is no API-only deployment switch.
8. **Vendor control plane is UI-less and kernel-clean** — 40 routes, one 28-line
   hardcoded HTML string, no templates/static/CSS at all, manifest-declared nav,
   exact kernel pin `0.1.0a8` from Forgejo, D1–D6 deny-case tests including a
   dynamic public-surface allowlist. Its README's kernel pin (`0.1.0a1`) is stale
   by seven alphas — the only drift found.
