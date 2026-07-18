# Architecture

This expands the `CLAUDE.md` summary. See `docs/adr/0001-multi-tenant-architecture.md`
for the founding tenancy design and `docs/adr/0002-starter-consolidation.md`
for how this repo came to be the org's one starter.

## Layout

```
app/
  core/          config, db, models base (+ 6 cross-cutting models), security,
                 deps (route guards), middleware/, logging, errors, crud,
                 unit_of_work, features (manifest registry), audit,
                 settings_models (DomainSetting), settings_resolver (spec
                 registry + tenant->platform->default resolver), templating
                 (Jinja env + render()), branding (static + per-tenant DB
                 override), identity (Party invariant helpers), web_deps
                 (cookie auth guard, shared with the bearer seam in deps.py)
  features/
    tenants/       platform-level tenant provisioning (no tenant context)
    auth/          JWT login, sessions, /auth/me; owns /admin/login+logout (web)
    parties/       person + organization CRUD (/parties/people, /parties/organizations)
                   + /admin/parties/* web screens (list/detail/create/edit)
    rbac/          roles, role grants, audit-event read endpoint
                   + /admin/roles, /admin/role-grants, /admin/audit web screens
    settings/      tenant settings admin API (spec declarations, seed, router)
                   + /admin/settings web screens (generic editor + branding editor)
    custom_fields/ field definitions CRUD + values on a registered entity's
                   custom_fields JSONB column (zero migrations per field)
                   + /admin/custom-fields web screens, incl. the
                   values-panel fragment other features' pages embed
    web/           core=False, deletable — owns only the /admin dashboard shell
  main.py        app assembly: middleware stack, error handlers, /health,
                 feature mounting
templates/       Jinja templates for the admin portal (see "Admin portal" below)
static/          Tailwind v4 CSS + vendored htmx/Alpine JS for the portal
```

Core never imports `app/features` (import-linter contract). Features never
import each other (import-linter contract). Cross-feature references are
FK/UUID columns, never a Python import — e.g. `rbac`'s `PartyRole` refers
to `parties` via a composite FK, not by importing `app.features.parties`.

### Model placement: core vs. feature

`Tenant`, `TenantDomain`, `Party` (+ subtype tables `PartyPerson`/
`PartyOrganization`), `Role`, `PartyRole`, `AuthSession` live in
`app/core/models.py`; `AuditEvent` + `write_audit_event` live in
`app/core/audit.py`. These are the models `app.core.deps` (route guards) and
`app.core.middleware.tenant` (the resolver) query directly — and since core
cannot import features, anything core needs to query at runtime must live in
core. `Party` (spec amendment 2026-07-17) replaced the bare `Person` model —
it's the fleet-wide identity source of truth (`party_type` person|
organization), with profile data on the subtype tables. `DomainSetting`
(`app/core/settings_models.py`) and the spec registry + resolver
(`app/core/settings_resolver.py`) live in core for the same reason, one
level removed: the `custom_fields` feature must call `resolve_value`
directly (the per-entity field limit), and features may never import each
other, so the shared mechanics sit in core while the `settings` feature
keeps only what nothing else needs (spec declarations, seed, router,
schemas). `CustomFieldDefinition` stays feature-local
(`app/features/custom_fields/models.py`) — nothing outside `custom_fields`
touches it. Everything not needed outside its own feature stays
feature-local: `UserCredential` (password hashes) lives in
`app/features/auth/models.py`, referencing `parties`/`tenants` by
string-form `ForeignKey`/`ForeignKeyConstraint` only. See ADR-0002 for the
full rationale — this is a deliberate deviation from "one model per feature
package." The complete model-by-model list — owner and port source-of-truth
for every model class in the repo — is the **Model provenance table** below.

## Settings resolution order + platform-row RLS design

`domain_settings` (`app/core/settings_models.py::DomainSetting`) is keyed by
`(tenant_id, domain, key)` where `tenant_id` is **nullable**: a
`tenant_id IS NULL` row is a platform-level default, readable by every
tenant but writable only by the `platform_api` role. This is the one
tenant-scoped-ish table that deliberately does not follow the standard
"NOT NULL + single RLS policy" template (see the hard-rules exception noted
in `CLAUDE.md`):

- Two partial unique indexes stand in for one composite `UniqueConstraint`,
  because Postgres treats every `NULL` as distinct from every other `NULL` —
  a plain `UNIQUE(tenant_id, domain, key)` would let unlimited
  `tenant_id IS NULL` rows collide on `(domain, key)`:
  `uq_domain_settings_platform` (`tenant_id IS NULL`) and
  `uq_domain_settings_tenant` (`tenant_id IS NOT NULL`).
- RLS is a **split read/write policy pair**, not the single
  `USING/WITH CHECK` policy every other tenant-scoped table gets: `app_user`
  may `SELECT` a row where `tenant_id = app_current_tenant_id() OR
  tenant_id IS NULL` (own rows + platform defaults), but may only
  `INSERT/UPDATE/DELETE` where `tenant_id = app_current_tenant_id()` — never
  `NULL`. Only `platform_api` (no `BYPASSRLS`, but its own broader grants)
  writes `tenant_id IS NULL` rows. See
  `tests/test_settings_isolation.py` for the three load-bearing properties
  this buys: (a) a tenant-owned row is invisible cross-tenant, (b) a
  platform-default row is visible to every tenant, (c) a tenant session
  cannot smuggle a `tenant_id IS NULL`/other-tenant write past the policy.

**Resolution order** (`app.core.settings_resolver.resolve_with_source`,
`resolve_value` is a thin wrapper that drops the `source`): tenant row (if
`tenant_id` is not `None`) → platform row (`tenant_id IS NULL`) → the
`SettingSpec`'s own `default`. A stored value that fails coercion to the
spec's `value_type`, or violates `allowed`/`min_value`/`max_value`, degrades
all the way to the spec default (not to "ignore this row") — a corrupted
row can never break every caller. `source` (`"tenant" | "platform" |
"default"`) tells the settings admin API whether to mask a secret's value
(masked whenever a real row exists; never for the built-in default — there's
nothing to hide there).

**Spec registration**: `SettingSpec` instances are declared in a feature's
own module (today only `app/features/settings/spec.py`) and registered into
the core registry via `register_specs([...])` as an import-time side
effect — importing the `settings` feature package registers them. Nothing
enforces *where* a spec is declared beyond "some feature module, at import
time" — this is the extension point other features use to add their own
settings (see `CLAUDE.md`'s Extension points section). The
no-orphan-settings architecture test (`tests/architecture/
test_no_orphan_settings.py`) fails the build if a registered key's `key`
string never appears as a literal anywhere under `app/` outside the
`settings` package and the resolver module — a spec with no reader is a
dead control an admin could "change" with zero effect.

**Extension-point hazard for 2b feature authors**: a new `SettingSpec` under
an EXISTING `SettingDomain` (`auth`/`audit`/`branding`/`custom_fields`) needs
no migration — but adding a NEW `SettingDomain` member does, and it's a
manual, unlinked two-place edit: the Python enum
(`app.core.settings_models.SettingDomain`) AND the migration's
`ck_domain_settings_domain` CHECK constraint (`"domain IN ('auth', 'audit',
'branding', 'custom_fields')"`, `alembic/versions/
20260717_0002_settings_table.py`) must both change together. Nothing
statically enforces this pairing; forgetting the migration means the enum
member is valid Python but every `INSERT`/`UPDATE` against it fails the DB
CHECK constraint at write time (a 500, not a clean validation error). See
`docs/superpowers/phase2-backlog.md`'s SOT-complete gaps for the tracked
ticket.

Write path: `PUT /settings/{domain}/{key}` → `settings/service.py::
update_setting` → `validate_spec_value` (raises `BadRequestError` on any
violation, never silently coerces on write — unlike the read-path
degradation above) → `settings_resolver.upsert_by_key` (always writes the
TENANT row; only a platform-role session can pass `tenant_id=None`) → an
audit event (`settings.update`, domain/key only — never the value, in case
it's a secret) written by the router, not the service.

## Party family + subtype RLS

`Party` (`app/core/models.py`) is the fleet-wide identity source of truth —
`party_type` discriminates `person`/`organization`. It carries the standard
tenant-scoped template (`tenant_id NOT NULL`, composite unique
`(tenant_id, id)`, a case-insensitive partial unique index
`(tenant_id, lower(email)) WHERE email IS NOT NULL`, single `USING/WITH
CHECK (tenant_id = app_current_tenant_id())` RLS policy) plus a
`custom_fields` JSONB column (default `{}`) that custom-fields values ride
on top of.

`PartyPerson`/`PartyOrganization` (1:1 subtype profile tables, PK = FK =
`party_id`) carry **no `tenant_id` column of their own** — isolation is
inherited entirely through the FK to `parties`, enforced by an
`EXISTS`-based RLS policy of the shape:

```sql
USING (EXISTS (
  SELECT 1 FROM parties p
  WHERE p.id = party_persons.party_id
    AND p.tenant_id = app_current_tenant_id()
))
```

(see `alembic/versions/20260717_0003_party_identity.py` for the literal
policy on both subtype tables). `tests/test_party_isolation.py` is the
load-bearing proof this pattern actually holds — it's the pattern any
future subtype/detail table hanging off `parties` (or off any other
identity-shaped entity) should copy rather than adding its own `tenant_id`.

Auth credentials (`UserCredential`), RBAC grants (`PartyRole`), audit actors
(`AuditEvent.actor_party_id`), and auth sessions (`AuthSession`) all bind to
`party_id` — there is exactly one identity table for every feature to peg
to, replacing the old bare `Person` model (spec amendment 2026-07-17).

## Custom-fields value flow

Field **shape** (type, validation rules, display) is defined per-tenant in
`CustomFieldDefinition` (`app/features/custom_fields/models.py`) — a
standard tenant-scoped table (single RLS policy, same shape as `parties`).
Field **values** live on the entity's own row, in a `custom_fields` JSONB
column (e.g. `Party.custom_fields`) — riding on that row's *existing* RLS
policy rather than a separate values table with its own isolation to get
right.

The write path (`PUT /custom-fields/{entity_type}/{entity_id}/values` →
`service.py::set_values`) is **validate → merge → flag_modified**:

1. **Resolve** the entity row via `registry.resolve_entity(entity_type)` +
   `db.get(model, entity_id)` — `None` (including a cross-tenant row RLS
   hides) raises `NotFoundError`.
2. **Validate** (`validate_values`) every submitted key against that
   `entity_type`'s active `CustomFieldDefinition`s: required fields present,
   known field codes only (an unrecognized code is a caller-side error, not
   a silent drop — a deliberate gap-closure over the ERP port, see
   `service.py`'s module docstring), each value passing
   `CustomFieldDefinition.validate_value` (type-specific checks — BOOLEAN/
   DATE/DATETIME are strict, URL/PHONE/CURRENCY are documented passthrough
   pending a project-specific `validation_regex`). Any violation raises one
   `BadRequestError` joining every message — nothing is written on a
   partial failure.
3. **Merge** (partial-update semantics): `dict(row.custom_fields or {})`,
   then for each submitted key — `None` deletes the key, any other value
   overwrites it. Keys not present in the request are left untouched.
4. **`flag_modified(row, "custom_fields")`** — SQLAlchemy cannot detect an
   in-place mutation of a JSON/JSONB column's Python `dict` on its own;
   without this call the `UPDATE` never fires and the merged value is
   silently lost on flush. This is the load-bearing line in `set_values`.

This is also the **runtime-field / zero-migration** proof point: creating a
new field (`POST /custom-fields/definitions`) is a plain row insert against
an already-existing table — no Alembic migration, no deploy, no app
restart. `tests/test_custom_fields_isolation.py` plus the eye-color e2e
canary (`tests/unit/test_custom_fields_api.py`) demonstrate a field being
defined and used in the same test run with zero schema changes in between.

### Visibility flags are consumed, query-level (2b.1-T5, finding F6)

`CustomFieldDefinition` has always declared `show_in_form`/`show_in_detail`/
`show_in_list` (defaults `True`/`True`/`False`), but until this task nothing
read them — every consumer listed every active definition regardless. The
fix is a single query-level owner: `custom_fields_service.list_for_entity`
gained an optional `visible_in: Literal["form", "detail", "list"] | None`
filter that maps directly to the matching `show_in_*` column
(`visible_in="form"` → `show_in_form == True`, etc.); `None` (every
pre-existing caller) is unchanged — every definition, unfiltered. No
consumer re-implements this filtering itself; each just asks for the slice
it needs:

- The values-panel **edit form**
  (`app.features.custom_fields.web.party_values_panel`) requests
  `visible_in="form"` — only `show_in_form` fields get an input.
- The same panel's read-only **"Details" section** requests
  `visible_in="detail"`, then layers one more in-Python condition,
  `not show_in_form`, via the panel's own `_detail_only_definitions`
  helper — a field that is BOTH `show_in_form` and `show_in_detail` (the
  default for both flags, so the common case) renders its value exactly
  ONCE, in the editable form input, not a second time as a duplicate
  read-only row. This narrower "detail-only" combination is scoped to this
  one consumer, in-Python, rather than overloading `list_for_entity`'s
  established single-flag `visible_in="detail"` semantics, which other
  future callers may still want unfiltered by `show_in_form`.
- The definitions **table** (`templates/admin/custom_fields/_table.html`)
  reads `show_in_list` per-row directly, to render a "visible in lists"
  badge — a list-VIEW consumer that picks actual columns from
  `visible_in="list"` is still future work (no entity-list admin screen
  exists yet), but the flag is no longer a dead control now that this badge
  reads it.

Tests: one per flag/consumer (`tests/unit/test_custom_fields_service.py`,
`tests/unit/test_custom_fields_web.py`) plus the detail/form
non-double-render pin.

## Capability model: manifest-driven surfaces (2b.1-T1, findings F1 + F5)

Before this task, `FeatureManifest` had one router list; whether a route was
a JSON endpoint or an HTML admin screen was a fact only the router itself
knew, and the sidebar was a hand-maintained list in
`templates/components/sidebar.html` with no link back to what was actually
mounted — `DISABLED_FEATURES=web` looked like an API-only switch but wasn't
(the other ~30 `/admin` routes stayed up), and a disabled feature could
leave a dead nav link or an embedded fragment 404ing inside another
feature's page. The fix makes the manifest the single, queryable source of
truth for a feature's surfaces:

- `FeatureManifest.routers` — JSON API routes. Mounted for every ENABLED
  feature, always; `web_enabled` has no say here (this is what makes
  API-only mode possible without also killing the API).
- `FeatureManifest.web_routers` — HTML/HTMX admin-portal routes (the
  feature's `web.py`). Mounted for an enabled feature ONLY when
  `web_enabled` is `True`. `auth`'s login/logout router lives here too
  (not in `routers`) — cookie auth has no meaning without a web surface to
  authenticate into.
- `FeatureManifest.nav: Sequence[NavItem]` — the sidebar entries this
  feature contributes. `NavItem(label, path, feature="")` — `feature` is
  left blank by the declaring `feature.py` and stamped in by the registry
  when collecting nav items across manifests, so a feature never repeats
  its own name.

`app.core.features.mount_features(app, *, manifests, disabled, web_enabled)`
mounts each enabled manifest's `routers` unconditionally, then its
`web_routers` only `if web_enabled`. `app.main` calls
`app.core.templating.install_surface_globals(manifests, disabled,
web_enabled)` once at startup (config is process-static, so this is safe)
to set two Jinja globals every template can read: `enabled_features:
frozenset[str]` (every feature name that is actually mounted — the general
"is this feature on" question, not specifically a web concept) and
`nav_items: tuple[NavItem, ...]` (`()` when `web_enabled` is `False` — there
is no sidebar to populate). `templates/components/sidebar.html` renders
from `nav_items` — there is no parallel hardcoded link list to keep in
sync.

**Two independent on/off switches — do not conflate them:**

- `DISABLED_FEATURES` (`Settings.disabled_feature_set`) turns off ONE named
  feature entirely — both its `routers` and `web_routers` together, one
  feature, one switch. `DISABLED_FEATURES=web` disables the `web` package
  specifically (the admin DASHBOARD SHELL, `GET /admin`, and nothing else);
  every other feature's own `/admin/*` screens and JSON routes are
  unaffected.
- `WEB_ENABLED` (`Settings.web_enabled`, env var, default `true`) is the
  SURFACE switch: `WEB_ENABLED=false` mounts NO feature's `web_routers` at
  all (zero `/admin` routes across every feature) and also drops the
  `/static` `StaticFiles` mount (`app/main.py`) — there is no HTML UI left
  to serve assets to. Every feature's `routers` (JSON API) keeps working
  unchanged. This is the real API-only deployment mode, independent of
  which individual features are enabled/disabled via `DISABLED_FEATURES`.

**The optional-slot pattern (F5):** a feature's nav entry or an embedded
fragment must never point at a route that might not be mounted. Two
enforcement mechanisms:

1. **Nav↔routes coherence test** —
   `tests/architecture/test_feature_manifests.py
   ::test_nav_items_paths_exist_in_web_routers` fails the build if any
   manifest's `NavItem.path` doesn't correspond to a route actually mounted
   in that SAME manifest's `web_routers` — a stale/dead sidebar link is a
   build failure, not a 404 a user finds later.
   `test_nav_paths_coherence_detects_bogus_entry` is the sensitivity check:
   it injects a bogus nav entry and asserts the coherence test actually
   catches it (a green coherence test that can't go red proves nothing).
2. **`enabled_features`-gated fragment embeds** —
   `templates/admin/parties/detail.html` wraps its custom-fields
   values-panel embed in `{% if 'custom_fields' in enabled_features %}`;
   with `DISABLED_FEATURES=custom_fields`, the party detail page renders
   200 without the panel div (and without an htmx call to a route that no
   longer exists) instead of a broken fragment. This is THE pattern for any
   template that conditionally embeds another feature's optional UI: guard
   on `enabled_features`, never assume a feature is mounted.

Every feature that has portal pages puts them in that feature's own
`web.py` (never `router.py`), mounted under `/admin/...`. Every `web.py`
route calls the SAME `service.py` functions its JSON sibling calls (e.g.
`parties/web.py`'s edit form and `PATCH`-equivalent both call
`parties_service.update_person_party`) — one write-owner per resource, two
presentation surfaces, never a second implementation of the write.

## Admin portal (web UI)

Phase 2b added an HTML/HTMX admin portal alongside the existing JSON API —
same tenants, same services, a second thin presentation surface (see
"Capability model" above for how a feature's admin screens get mounted and
how they reach the sidebar). The deletable `web` feature package
(`app/features/web/`, `core=False`) owns only the dashboard shell
(`GET /admin`) — `DISABLED_FEATURES=web` drops that one route and nothing
else, since login/logout (`GET`/`POST /admin/login`, `POST /admin/logout`)
are owned by `auth` (a core feature) and every other feature's `/admin/*`
routes mount independently.

### Template / asset layout

```
templates/
  base.html                 <html> shell: brand-aware <title>, static asset links
  layouts/admin.html         {% extends "base.html" %} + sidebar/topbar chrome
  components/                sidebar, topbar, form_macros, table_macros (Jinja macros)
  auth/login.html             standalone (does not extend layouts/admin.html — pre-auth)
  admin/
    dashboard.html
    parties/  rbac/  settings/  custom_fields/   one dir per feature's pages
    <feature>/_*.html          "_"-prefixed = htmx fragment, not a full page
  errors/{400,401,403,404,409,422,500,csrf}.html   branded error pages
static/
  css/src/main.css           Tailwind v4 CSS-first source (@theme/@source/@custom-variant)
  css/main.css                compiled output — gitignored, build-only (`make css-build`)
  js/{htmx,alpine}.min.js     vendored (no CDN, no node_modules at runtime)
  js/csrf.js                  CSRF header bridge (see below)
  js/components.js            small Alpine component glue
```

Every `templates/admin/**/*.html` and `templates/auth/*.html` file either
`{% extends %}` a layout or is `_`-prefixed (a fragment meant to be
`{% include %}`d or returned directly to an htmx swap) —
`tests/architecture/test_web_conventions.py::test_every_admin_or_auth_template_extends_a_layout_or_is_a_fragment`.
A `GET` route that serves both a full page and an htmx fragment (e.g.
`GET /admin/parties`) branches on the `HX-Request` header: present → render
just the `_table.html` fragment; absent → render the full `index.html`,
which itself `{% include %}`s that same fragment once, so there is exactly
one template that knows how to draw the table.

Tailwind v4 is CSS-first — `static/css/src/main.css`'s `@theme` (design
tokens) and `@source` (an explicit safelist of class-name patterns the
compiler must not tree-shake away, since Jinja templates aren't a build-time
scannable source the default content-detection understands) replace the old
`tailwind.config.js` entirely. `npm run css:build` (`make css-build`, or the
Dockerfile's `css-builder` stage) compiles it; `static/css/main.css` is
gitignored — never commit it, always rebuild.

### Auth flow: cookie + bearer share one seam

`app.core.deps.authenticate_request` is the ONE function that validates a
token (signature, expiry, session-revocation, tenant-claim match) and
resolves it to a `Party` — both the JSON API's bearer `Authorization`
header and the portal's cookie flow call it, so a security fix to token
validation lands once and covers both surfaces:

1. **Login** (`POST /admin/login`, `app.features.auth.web`) — a plain HTML
   form POST (via `hx-post`, see the CSRF section below), calling
   `auth_service.web_login` (same credential-check path `POST /auth/login`
   uses for the JSON API) and, on success, setting an `access_token` cookie
   (`HttpOnly`, `SameSite=Lax`, `Secure` iff `is_secure_request()`) instead
   of returning the token in a JSON body.
2. **Every portal page** depends on `app.core.web_deps.require_web_auth`,
   which: reads the `access_token` COOKIE (no header fallback — cookie-only
   is deliberate, this dependency is web-only) → calls
   `authenticate_request(request, db, token=token)` (the shared seam) →
   additionally requires the `"admin"` role (every portal page is
   admin-only in this phase; no other portal-facing role exists yet,
   see the phase 3 note below) → returns `{"party", "roles"}` or raises
   `WebAuthRedirect` (a 302 to `/admin/login?next=...`, registered as a
   dedicated exception handler in `app.core.errors`) — a portal auth
   failure is ALWAYS a redirect, never a bare 401/403 JSON body.
3. **Logout** (`POST /admin/logout` — was `GET /admin/logout` until 2b.1-T5,
   finding F7: a bare `<a href="/admin/logout">` is a CSRF-exempt safe
   method, so a third-party page could force a victim's logout just by
   loading `<img src="/admin/logout">`; the topbar's logout control is now
   an `hx-post` button, routed through the same CSRF header-bridge as every
   other mutation) revokes the `AuthSession` server-side (not just clearing
   the cookie) and redirects to the login page — verified by the e2e canary
   re-submitting the revoked cookie value and getting redirected again, not
   authenticated.

Phase 3 TODO (tracked in the backlog): `require_web_auth` hardcodes the
`"admin"` role; loosen this per-route once non-admin portal surfaces exist.

### CSRF header-bridge contract

`app.core.middleware.csrf.CSRFMiddleware` validates a double-submit pair:
the `X-CSRF-Token` HEADER must match the `csrf_token` COOKIE (deliberately
NOT `HttpOnly`, so client JS can read it). `static/js/csrf.js` is the
bridge — it copies the cookie onto that header for every htmx request
(`htmx:configRequest` listener) and every `fetch()` call (monkey-patched),
so every mutating form in these templates uses `hx-post`/`hx-put`/
`hx-delete`, never a bare `<form method="post">` (which has no hook to
attach a custom header and would 403 with `csrf_failed`) —
`tests/architecture/test_web_conventions.py::test_no_template_uses_a_plain_method_post_form`
enforces this. `tests/test_admin_portal_e2e.py` replicates the same bridge
server-side (capture the `csrf_token` cookie from a safe `GET`, send it back
as the `X-CSRF-Token` header on the following `POST`) rather than bypassing
CSRF for the test.

### Branding pipeline: static config + per-tenant DB override

Two layers, kept deliberately separate (`app.core.branding`'s module
docstring):

- **`get_brand()`** — deployment-STATIC identity (name, tagline, colors,
  support email, app URL). Resolution order, lowest to highest precedence:
  built-in generic defaults < `brand.json` (repo root; path overridable via
  `BRAND_CONFIG_PATH`) < same-named `BRAND_*` environment variable
  (`BRAND_NAME`, `BRAND_TAGLINE`, `BRAND_PRIMARY_COLOR`,
  `BRAND_ACCENT_COLOR`, `BRAND_SUPPORT_EMAIL`, `BRAND_APP_URL`). Cached for
  the process lifetime (`lru_cache`) and installed as a Jinja global
  (`app.core.templating`), so every template reads `brand.name` etc.
  without a route passing it explicitly — a restart is required to pick up
  a `brand.json`/env change.
- **`load_branding(db, tenant_id)`** — the static brand above, with any
  keys present in the tenant's `ui_branding` domain setting
  (`SettingDomain.branding`, resolved via the same
  tenant→platform→spec-default resolver every other setting uses) overlaid
  on top. Per-request, not cached — a tenant admin's branding edit is live
  on the next page load, no restart. Only keys in `_KNOWN_BRAND_KEYS`
  (`name`, `tagline`, `logo_url`, `primary_color`, `accent_color`,
  `custom_css` — the branding editor's own form fields) are merged from the
  stored override; anything else in that dict is ignored (2b final-review
  follow-up, resolved 2b.1-T4/F4 — previously any key merged unchecked).
  `primary_color`/`accent_color` overrides are validated as `#RRGGBB` hex
  (falling back to the static color on a bad value); `custom_css` is run
  through `sanitize_branding_css` (strips `@import`, `javascript:`/`data:`
  URLs, `expression()`, `behavior:`, and any literal `<` — a `<script>`
  breakout attempt) before it is ever rendered.

**Portal-wide resolution (2b.1-T4, finding F4):** `load_branding` used to
have exactly one caller (the branding editor's own preview) — every other
portal page and the login page rendered the deployment-static brand only,
so saving a tenant's branding changed nothing outside the editor. Fixed by
`app.core.branding.get_request_branding(request, db)`: resolves
`load_branding(db, tenant.id)` (or the static `get_brand()` fallback when
`request.state.tenant` is `None` — platform hosts, unresolved-tenant error
contexts) exactly ONCE per request, memoized on `request.state.branding`.
`app.core.templating.render()` is the single place that reads it back into
the template context — it sets `context["brand"]` from
`request.state.branding` unless the caller's own context already defines
`brand` (route-level override still wins; see that module's docstring for
the full precedence: explicit context > per-request tenant override >
static global). No route changed to pick this up.

Three call sites populate `request.state.branding` for the whole app,
independent of how many features/routers exist (seam decision documented in
`app.core.branding`'s module docstring, including the two rejected
per-router/per-route shapes): `app.core.web_deps.require_web_auth` (covers
every authenticated `/admin/*` page — one seam, since every such route
already depends on it) and the two pre-auth `GET`/`POST /admin/login`
routes (`app.features.auth.web`), which never reach `require_web_auth`.

Cost: one extra DB read (`resolve_value` inside `load_branding`) per
authenticated web request that didn't already need one — mitigated to
"one per request" (not one per render) by the memoization above; fully
removing it is the phase-3 settings-cache ticket
(`docs/superpowers/phase2-backlog.md`'s "Added during phase 2a execution"
section) — not needed to ship this fix.

`GET`/`POST /admin/settings/branding` (`app.features.settings.web`) still
calls `load_branding` directly for its own live-preview render (its context
explicitly sets `brand`, which is why the precedence rule above lets a
route override the per-request value) — it renders the CURRENT effective
branding, and its own render context's `brand` key SHADOWS both the
per-request tenant override and the process-global static `brand` template
global for that one response only (the static global stays available to
every other template unchanged). The same route is where
`templates/admin/settings/branding.html`'s `custom_css` preview block uses
`| safe` — the one real `| safe` usage in this app's templates, immediately
preceded by a `SANITIZER:` comment pointing at `sanitize_branding_css`,
which is what makes it safe (see the CLAUDE.md template-escaping rule and
`test_safe_filter_only_used_with_a_sanitize_comment_nearby`).

Write path: `POST /admin/settings/branding` composes the submitted
sub-fields (`name`/`tagline`/`logo_url`/`primary_color`/`accent_color`/
`custom_css`) back into the `ui_branding` dict and calls the SAME
`settings_service.update_setting(db, tenant, "branding", "ui_branding",
raw)` the generic per-key editor (`POST /admin/settings/{domain}/{key}/edit`)
and the JSON `PUT /settings/{domain}/{key}` API all call — one write path,
three presentation surfaces (generic web editor, friendly branding editor,
JSON API), each ending in the same audit event
(`settings.update`, domain/key only, never the value).

### Display settings: tenant timezone + date/datetime formats

Three specs registered on a new `SettingDomain.display`
(`app/features/settings/spec.py`), resolved through the same
tenant→platform→spec-default resolver every other setting uses — no
dedicated screen or bespoke storage; they auto-appear in the registry-driven
`/admin/settings` index like any other spec:

- `timezone` (string, default `"UTC"`) — an IANA zone name.
- `date_format` (string, default `"%Y-%m-%d"`) — a strftime pattern.
- `datetime_format` (string, default `"%Y-%m-%d %H:%M"`) — a strftime
  pattern.

**Write-loud / read-degrade validator split.** `SettingSpec` gained a new
field, `validator: Callable[[object], None] | None`, run after
type-coercion/`allowed`/range checks. The two call sites that consult it
behave in deliberately OPPOSITE ways for the SAME check:

- **Write path** — `settings_resolver.validate_spec_value` runs the
  validator LAST and lets its `ValueError` propagate as a `BadRequestError`
  (a clean 400, whether the write came from the JSON `PUT
  /settings/{domain}/{key}` API or the generic admin editor). An admin
  typing `Foo/Bar` into `date_format` (no `%` directive, so
  `_validate_strftime` raises before even calling `strftime`) or an unknown
  zone name into `timezone` (`_validate_timezone` — `ZoneInfo(...)` raising
  `ZoneInfoNotFoundError`) gets rejected before anything is written.
- **Read path** — `settings_resolver.resolve_with_source` (which
  `resolve_value` delegates to) catches the SAME `ValueError` and silently
  degrades to the spec default, `source="default"` — same fallback
  treatment as a coercion failure or an `allowed`-set miss. A row that
  passed validation at write time but is later corrupted (a raw DB edit, a
  future migration that reuses the column) or a zone whose tzdata went
  missing from the runtime image can never surface as a validation error
  mid-render; it just silently reverts to UTC / the default format string.

This asymmetry is intentional, not an inconsistency: a write is a single
request the caller can retry with better input, so it fails loud; a read
happens on every page render for every user of that tenant, so it must
never be the thing that turns a stored-data problem into a 500.

**Per-request seam (mirrors branding).** `app.core.display.DisplaySettings`
(`timezone: ZoneInfo`, `date_format`, `datetime_format`) is resolved by
`get_request_display(request, db)` at most once per request and memoized on
`request.state.display` — the identical shape to
`request.state.branding`/`get_request_branding` above, including the same
single warming call site: `app.core.web_deps.require_web_auth` (every
authenticated `/admin/*` page). `load_display` additionally wraps the
resolved timezone string in `ZoneInfo(...)` with its own
try/except-to-`_UTC` fallback — belt-and-braces alongside the resolver's own
validator-driven degrade, covering the case where a value that validated
fine at write time (tzdata present then) can't be loaded at read time
(tzdata missing now).

**Filter fallback invariant.** Templates never read `request.state.display`
directly; they consume it ONLY through the two Jinja filters registered in
`app.core.templating` — `local_datetime` and `local_date` (`@pass_context`,
so they can reach `request.state` without the caller threading it through).
Both call a shared `_context_display(context)` helper that returns
`request.state.display` if the current render already warmed it, or
`default_display()` (spec defaults, UTC) if not — a render that never went
through `require_web_auth` (an error page, a pre-auth page) still gets a
formatted timestamp, never an `AttributeError`/`UndefinedError`. This is the
one and only consumption point: `tests/architecture/test_web_conventions.py
::test_timestamp_renders_go_through_local_filters` fails the build on any
Jinja expression referencing a `*_at`-named attribute that doesn't also
apply one of these two filters — see CLAUDE.md's hard-rules entry.

**Migration note (data loss on downgrade).** `alembic/versions/
20260718_0006_display_setting_domain.py` widens the
`ck_domain_settings_domain` CHECK constraint from `('auth', 'audit',
'branding', 'custom_fields')` to add `'display'`. Its `downgrade()` DELETES
every `domain_settings` row with `domain = 'display'` before restoring the
narrower constraint (any surviving row would violate it) — a real,
documented data-loss-on-downgrade: rolling back this migration on a
database with tenant-customized timezones/formats permanently discards
those overrides, silently reverting every tenant to UTC/spec-default
formatting with no way to recover the deleted rows short of a backup
restore.

**API boundary.** The JSON API is deliberately untouched: every response
stays ISO-8601 UTC, unchanged before and after this feature. Display
formatting is a web-portal presentation concern only; API consumers do
their own localization.

### Cross-feature UI composition (values-panel pattern)

See CLAUDE.md's Extension points entry for the rule; concretely: the party
detail page needs to show/edit a party's custom-field values, but `parties`
may never import `custom_fields`. `custom_fields/web.py` owns
`GET`/`POST /admin/custom-fields/party/{party_id}/values-panel` and the
partial they render (`templates/admin/custom_fields/_values_panel.html`);
`templates/admin/parties/detail.html` references only the URL
(`hx-get=".../values-panel" hx-trigger="load"`) — composition happens
entirely in the browser via htmx's lazy-load-on-render, zero Python import.
The panel's own form posts back to the SAME web route (not the JSON API's
`PUT /custom-fields/{entity_type}/{entity_id}/values` — an htmx form always
sends `Accept: text/html`, and the JSON route would still return
`application/json` regardless, which htmx would swap in as literal text),
which calls the same `custom_fields_service.set_values` the JSON API uses.

### Error negotiation: branded HTML with a JSON fallback

`app.core.errors._negotiate` is the single JSON-vs-HTML decision point for
every error response (FastAPI exception handlers and the CSRF/tenant/
rate-limit ASGI middleware all route through it). Rule: a request "prefers
HTML" iff `"text/html" in request.headers["accept"]` — htmx sends
`Accept: text/html, */*`, so htmx error responses get the branded page too
(a valid swap target), while a JSON API client (`Accept: application/json`)
always gets the byte-identical envelope
(`{"code", "message", "details", "request_id"}`) unchanged from the
API-only phase. Every status this app has a dedicated template for
(400/401/403/404/409/422/500, plus a special `csrf_failed` page regardless
of its 403 status) renders that template with exactly the envelope's
`code`/`message`/`request_id` fields; a status outside that map still gets
a branded page via the `>=500`/else fallback — never a raw stack trace or
blank page. **Fallback**: if `render_error` itself raises (a broken
template, a missing asset during render), `_negotiate` catches it, logs
`"Error-page render failed; falling back to JSON envelope"`, and returns
the plain JSON envelope instead — an error page can never itself 500 an
error response into an unhandled crash.

### `/static` and `/health` bypass (recap)

Both bypass `TenantResolverMiddleware` entirely before any DB query — see
"Static-asset bypass" and "Health bypass" above (unchanged by the portal
work, listed here for the reader looking for portal-adjacent behavior in
one place).

## Web-portal module provenance

Same convention as the model provenance table below — owner and port
source-of-truth for the modules phase 2b introduced. "ST" = `dotmac_starter`
(the pre-consolidation single-tenant starter), "SUB" = `dotmac_sub`,
"native" = no upstream port.

| Module | Purpose | Port SoT |
|---|---|---|
| `app/core/templating.py` | Jinja2 environment + `render()`, `static_asset_url` cache-busting | ST (`app/templates.py::_asset_version`/`_static_asset_url`); the `brand`/branding-DB-override wiring is native to this phase |
| `app/core/branding.py` | `get_brand()` (static) + `load_branding()` (DB overlay) + `sanitize_branding_css` | SUB (`app/services/branding_config.py::get_brand`) for the static layer; ST (`app/services/branding.py::get_branding`/`sanitize_branding_css`) for the DB-overlay + sanitizer, adapted from ST's single-tenant "one row, no tenant_id" model to this app's tenant-scoped resolver |
| `app/core/web_deps.py` | `require_web_auth`, `WebAuthRedirect`, `safe_next_url`, `is_secure_request` | ST (`app/web/deps.py`), routed through this app's `authenticate_request` shared seam (native adaptation — ST had no bearer/cookie seam to share) |
| `app/core/identity.py` | `normalize_email`, `person_display_name` — the single-owner Party-invariant helpers | native (closes the SOT gap tracked from 2a-T6/T7; no upstream port — see "Known dual-writer: Parties" below) |

## Model provenance table

Every model class in `app/` (ORM `Base` subclasses — `grep -rn "class .*Base"
app/` to re-enumerate; excludes Pydantic `BaseModel`/`BaseSettings` schema
classes, which aren't persisted tables), its owner (`core` | the feature
package name), and its port source-of-truth. "native" means designed for
this repo, no upstream port. This is criterion 1 of the SOT-complete
criteria (`docs/superpowers/specs/2026-07-17-starter-consolidation-design.md`)
made concrete — every model has exactly one declared owner.

| Model | Table | Owner | Port SoT |
|---|---|---|---|
| `Tenant` | `tenants` | core | native (dotmac_starter_mt, ADR-0001) |
| `TenantDomain` | `tenant_domains` | core | native (dotmac_starter_mt, ADR-0001) |
| `Party` | `parties` | core | native (spec amendment 2026-07-17; supersedes the earlier bare `Person`, which was `dotmac_starter`-derived) |
| `PartyPerson` | `party_persons` | core | native (spec amendment 2026-07-17) |
| `PartyOrganization` | `party_organizations` | core | native (spec amendment 2026-07-17) |
| `Role` | `roles` | core | dotmac_sub (`app/models/rbac.py`, tenant-adapted) |
| `PartyRole` | `party_roles` | core | dotmac_sub (`app/models/rbac.py::PersonRole`, tenant-adapted + renamed for Party) |
| `AuthSession` | `auth_sessions` | core | dotmac_sub (`app/models/auth.py`, tenant-adapted) |
| `AuditEvent` | `audit_events` | core | dotmac_sub (`app/models/audit.py`, tenant-adapted) |
| `DomainSetting` | `domain_settings` | core | dotmac_starter (`app/models/domain_settings.py`, tenant-adapted), with `CheckConstraint` restored from dotmac_sub |
| `UserCredential` | `user_credentials` | auth | dotmac_sub (`app/models/auth.py`, tenant-adapted; `email` column dropped 2b.1-T3 — see "Auth credentials" ownership row and the F2 resolution note below) |
| `CustomFieldDefinition` | `custom_field_definitions` | custom_fields | dotmac_erp (`app/models/finance/automation/custom_field.py`, generalized: string `entity_type` registry instead of a finance-only enum, `tenant_id` instead of `organization_id`) |

`Party.custom_fields` and `DomainSetting`'s split-policy shape are
columns/behavior on the rows above, not separate tables, so they don't get
their own provenance row — they're called out in the sections above instead.

## Mutable-resource ownership list

SOT-complete criterion 1 ("every mutable resource, decision, and state
transition has one named owner") applied at the *resource* level, not just
the model level — one service-layer function (or, where two legitimately
exist, both named with the invariant that keeps them consistent) owns every
write:

| Resource | Owning write path(s) |
|---|---|
| Tenants | `app.features.tenants.service.create_tenant` (platform-only; no update/delete service yet) |
| Tenant domains | none — no write path exists yet (rows would be inserted by a future custom-domain feature) |
| Parties (person/org identity + profile) | **Dual writer**, see below: `app.features.parties.service.create_person_party` / `create_organization_party` / `update_person_party` / `update_organization_party` (the `/parties` API + `/admin/parties/{id}/edit` web flow), **and** `app.features.auth.service.register` (the `/auth/register` flow) |
| `Party.display_name` projection | owner: parties+auth services via `core/identity` helpers (recompute-on-write) — `app.features.parties.service.create_person_party`/`update_person_party` and `app.features.auth.service.register` all call `app.core.identity.person_display_name`; `update_organization_party`/`create_organization_party` reassign `legal_name` directly (no helper needed — `legal_name` IS the display name). Recomputed on every create AND update, never write-once again (Task 5 closed the SOT gap; see below). Repair: re-save (call the relevant update function — it recomputes from the current subtype fields, no separate repair script needed) |
| `Party.email` (the login identity) | **Single column, single authority as of 2b.1-T3 (finding F2, resolved)**: owner is parties+auth writers via `app.core.identity.normalize_email` — same dual-writer/shared-invariant shape as `display_name` above (`create_person_party`/`update_person_party`/`create_organization_party`/`update_organization_party` and `auth/service.py::register` all call it). `app.features.auth.service.login` READS this column directly (join by `party_id` to find the credential row) instead of a second `UserCredential.email` copy, which is GONE — see the F2 resolution note under "Known dual-writer: Parties" below. No repair path needed: there is only ever one column now, so there is nothing to drift or re-sync. |
| Party role grants | `app.features.rbac.service.assign_role` (the `POST /rbac/role-grants` JSON API **and** the `POST /admin/role-grants` web form both call this same function) **and** `app.features.auth.service._assign_first_user_admin` (auto-assigns the tenant's first registered user the `admin` role — a second, narrower writer of the same table; see the RBAC follow-up in `docs/superpowers/phase2-backlog.md`) |
| Roles | `app.features.rbac.service.create_role` (`POST /rbac/roles` API **and** `POST /admin/roles` web form), and implicitly `_assign_first_user_admin` (creates the tenant's `admin` role on first use if it doesn't exist yet) |
| Auth credentials | `app.features.auth.service.register` (the only writer of the `password_hash` row — no credential-update/password-reset path yet, phase 2c). `Party.email` is a SEPARATE resource with its own row above (Parties) — `UserCredential` carries no email of its own as of 2b.1-T3 (F2): `login()` resolves `Party` by email first, then `UserCredential` by `party_id` only. |
| Auth sessions | `app.features.auth.service.login` (issues, via `POST /auth/login` and `POST /admin/login`'s `web_login`) **and** `web_logout` (revokes — sets `revoked_at`, via `POST /admin/logout`, CSRF-protected as of 2b.1-T5/F7; the JSON API has no logout/revoke route of its own yet) |
| Audit events | `app.core.audit.write_audit_event` — the only function that constructs an `AuditEvent`; called from `rbac/router.py` + `rbac/web.py` (role/grant writes) and `settings/router.py` + `settings/web.py` (setting writes, including the `ui_branding` branding editor) |
| Domain settings rows | `app.core.settings_resolver.upsert_by_key` (tenant writes, via `settings/service.py::update_setting` — called by the JSON `PUT /settings/{domain}/{key}` API, the generic web editor `POST /admin/settings/{domain}/{key}/edit`, **and** the friendly branding editor `POST /admin/settings/branding`, all three ending in the same function and the same `settings.update` audit event) and `ensure_by_key` (platform-default seeding only, via `settings/seed.py::seed_platform_defaults`, idempotent — never overwrites an existing row) |
| `ui_branding` setting specifically | same writer as above (`update_setting`, domain=`branding`, key=`ui_branding`) — no separate write path; read by `app.core.branding.load_branding`, the merge/sanitize layer documented in "Branding pipeline" above |
| Custom field definitions | `app.features.custom_fields.service.create_field` / `update_field` / `deactivate_field` (soft-delete only — no hard delete); each has a JSON API route (`custom_fields/router.py`) and an `/admin/custom-fields` web route (`custom_fields/web.py`) calling the same function |
| Custom field values | `app.features.custom_fields.service.set_values` (the only writer of any entity's `custom_fields` JSONB column) — called by the JSON `PUT /custom-fields/{entity_type}/{entity_id}/values` API **and** the web values-panel (`POST /admin/custom-fields/party/{party_id}/values-panel`, see the composition pattern above) |
| Display formats (timezone/date_format/datetime_format) | owner: `settings` (display domain) — same `update_setting`/`upsert_by_key` write path as every other setting, via the generic web editor and the JSON `PUT /settings/display/{key}` API; no dedicated write path. Consumers: the `local_datetime`/`local_date` Jinja filters ONLY (`app.core.templating`) — no service reads these specs directly |

### Known dual-writer: Parties (auth register vs. parties service)

Two service functions independently construct a `Party` + `PartyPerson`
row: `auth/service.py::register` (the `/auth/register` self-service signup
flow, which also creates the `UserCredential` and first-admin role grant in
the same transaction) and `parties/service.py::create_person_party` /
`create_organization_party` / `update_person_party` /
`update_organization_party` (the tenant-admin `/parties` API and the
`/admin/parties/{id}/edit` web flow, Task 5). This is a **deliberate, not
accidental** dual writer — one flow is "a person signs themselves up," the
other is "an admin manages a contact/customer record" — flagged here per
SOT-complete honesty rather than silently left implicit. The writers
themselves stay two; what changed (Task 5) is that the INVARIANTS both must
preserve are no longer hand-duplicated at each call site — they're
implemented once in `app.core.identity` and both writers call the same
functions:

- **Email is lowercased at the write boundary**, via
  `app.core.identity.normalize_email` — `auth/service.py::register` and
  `parties/service.py`'s create/update functions all call this one function
  instead of each writing its own `.lower()`. Both must agree because the
  `parties` table's uniqueness index is `lower(email)`-based — a
  mixed-case write from either path that skipped normalization would still
  be rejected by the DB constraint, but a *read*-side comparison
  (`login()`'s `Party` lookup) that skipped it would silently fail to
  match. A new writer of `Party.email` now has an obvious single function
  to call rather than a convention to remember and replicate.
  **F2 resolution (Phase 2b.1 Task 3, RESOLVED):** until this task,
  `app.features.auth.models.UserCredential` carried its OWN `email` column
  — a write-once copy made at `register()` and never touched again. Once
  `update_person_party` (Task 5) could edit or NULL `Party.email`, that
  copy silently drifted from the real profile email, with no cross-feature
  guard possible (parties cannot reach into auth's table under feature
  independence). The fix removes the second column entirely — migration
  `alembic/versions/20260718_0005_single_email_authority.py` drops
  `user_credentials.email` + its unique constraint, and
  `auth/service.py::login` now resolves `Party` by
  `(tenant_id, normalize_email(email), party_type=person)` FIRST, then
  `UserCredential` by `party_id` only. There is exactly one email column
  system-wide now, so the two can never drift again — see the ownership
  table's `Party.email` row above. Documented, intended consequence: a
  person party with a NULL email cannot log in (the query matches no
  string) — see `login()`'s docstring, and the (struck) backlog entry in
  `docs/superpowers/phase2-backlog.md`.
- **`display_name` derivation** — **closed as of Task 5** (previously the
  tracked SOT gap in `docs/superpowers/phase2-backlog.md`). Both writers
  now call `app.core.identity.person_display_name(first_name, last_name)`
  for the person case (organizations reassign `legal_name` directly — no
  helper needed, `legal_name` IS the display name); `update_person_party`/
  `update_organization_party` (`parties/service.py`) recompute
  `display_name` INSIDE the update, from the just-updated subtype fields,
  so the projection is refreshed on every write, not just at create. See
  the ownership table above (`Party.display_name` projection row) for the
  owner/repair statement.

## Request flow / middleware order

From `app/main.py`'s docstring, outermost to innermost as FastAPI executes
them (Starlette runs the *last-added* middleware first, so the add order in
the source is the reverse of execution order — the list below is execution
order):

1. **ObservabilityMiddleware** — assigns/propagates a request ID
   (`TRUST_INBOUND_REQUEST_ID` gates whether an inbound `X-Request-ID` is
   trusted or a fresh one is generated) and emits structured request logs.
2. **TrustedHostMiddleware** — only mounted when `TRUSTED_HOSTS` is set;
   drops requests to unrecognized `Host` headers before any tenant lookup.
3. **TenantResolverMiddleware** — resolves `request.state.tenant` from the
   `Host` header (see below) and sets it before any route runs.
4. **RateLimitMiddleware** — tenant/client-ip/path-keyed budget check.
5. **CSRFMiddleware** — double-submit cookie/header check for
   browser-cookie flows.

After middleware, `register_error_handlers(app)` installs the exception
handlers, `/health` is registered directly on `app`, and
`mount_features(...)` mounts each enabled feature's routers last.

### Health bypass

`/health` is a liveness check that must not touch the database (container
orchestrators probe it before a DB may even be reachable).
`TenantResolverMiddleware._HEALTH_PATHS` is a frozenset containing `/health`
and `/health/ready`; both are short-circuited before any tenant resolution
(no DB query at all). Today only `/health` is mounted as a route; `/health/ready`
is pre-listed for a future readiness endpoint and currently returns 404 at the
router after bypassing tenant resolution. `/health` is the only route in
`tests/architecture/test_route_guards.py::ALLOWLIST` permitted to carry zero
`require_*` guards. Every other route either carries a `require_*` dependency
or fails the architecture test.

### Static-asset bypass

`/static/*` (the `StaticFiles` mount in `app/main.py`, serving
`static/css/main.css`, vendor JS, etc.) gets the same before-resolution
short-circuit as `/health`, via `_is_static_path()`: `path == "/static"` or
`path.startswith("/static/")` — plain string checks, deliberately no regex.
Before this bypass existed, `TenantResolverMiddleware.dispatch` opened a
`SessionLocal()` for every static-asset request same as any other route; with
the DB unreachable, that raised and turned a should-be-200 static asset into
a 500 — verified as a real repro (`/static/css/main.css` 500s with the DB
down) and fixed alongside the branded HTML error pages in plan 2b Task 2.
`tests/unit/test_tenant_middleware.py` covers both the exact/prefix bypass
(`/static`, `/static/css/main.css`) and the near-miss paths that must NOT
bypass (`/staticevil`, `/static2/x` — a bare `startswith("/static")` without
the trailing-slash check would wrongly match both).

## Tenant resolution

`TenantResolverMiddleware._resolve()`, in order:

1. **Custom domain** — exact match in `tenant_domains.domain` where
   `verified_at IS NOT NULL`, joined to an active, non-deleted `tenants` row.
2. **Subdomain** — `host` stripped of the `.` + `PLATFORM_ROOT_DOMAIN`
   suffix (rejecting nested subdomains) looked up against `tenants.slug`.
3. **Root domain** — `host == PLATFORM_ROOT_DOMAIN` → `request.state.tenant
   = None` (platform context; only `/platform/*` and `/health` are valid
   here — see `_is_platform_path`).
4. **Unknown host** — 404, except for platform paths and `/health`.

## The three-role DB model

Three Postgres roles, three connection URLs (`DATABASE_URL`,
`PLATFORM_DATABASE_URL`, `MIGRATION_DATABASE_URL`), created by the initial
Alembic migration (`alembic/versions/20260504_0001_initial_tenant_schema.py`):

- **`app_user`** (`DATABASE_URL`) — the FastAPI request-path role for
  tenant-scoped routes. RLS-enforced, cannot bypass. `app.core.db.get_db`
  runs `SELECT set_config('app.current_tenant', :id, true)` per request
  (transaction-scoped — the next pooled connection starts with no setting).
  RLS policies read that setting via `app_current_tenant_id()`, which
  treats unset/malformed values as `NULL`, so a forgotten tenant scope
  fails closed (zero rows) rather than leaking.
- **`platform_api`** (`PLATFORM_DATABASE_URL`) — used by `app.core.db.get_platform_db`
  for platform-wide routes (tenant provisioning). Explicit grants, **no**
  `BYPASSRLS`. Falls back to `DATABASE_URL` if unset (local dev only).
- **`app_admin`** (`MIGRATION_DATABASE_URL`) — `BYPASSRLS`. Used only by
  `alembic upgrade` and `scripts/deploy.sh`'s pre-migration `pg_dump`
  backup — never by request-handling code. Migrations never run on
  container boot: the Dockerfile `CMD` only starts `uvicorn`;
  `scripts/deploy.sh` runs `alembic upgrade heads` as a one-off container
  step before recreating the app service.

Every tenant-scoped table gets `tenant_id UUID NOT NULL REFERENCES
tenants(id)`, a composite unique for anything unique-per-tenant, and
`ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + a
`USING/WITH CHECK` policy on `tenant_id = app_current_tenant_id()`, applied
in the same migration that creates the table.

## Feature-mount sequence

1. `app/main.py` imports `FEATURE_MODULES` from `app/features/__init__.py`
   — a plain list of dotted module paths (currently `tenants`, `auth`,
   `parties`, `rbac`, `settings`, `custom_fields`, `web`).
2. `app.core.features.load_manifests(FEATURE_MODULES)` imports each
   `<module>.feature` submodule via `importlib` (so core never statically
   imports `app.features`) and collects its `feature: FeatureManifest`
   (`name`, `routers`, `web_routers`, `nav`, `core: bool`,
   `enabled_by_default: bool` — see "Capability model" above for the
   `web_routers`/`nav` fields).
3. `app.core.features.mount_features(app, manifests=..., disabled=settings.disabled_feature_set, web_enabled=settings.web_enabled)`
   mounts each enabled manifest's `routers` via `app.include_router(...)`
   unconditionally, then its `web_routers` ONLY `if web_enabled`, skipping
   the whole manifest if its name is in `DISABLED_FEATURES` or its
   `enabled_by_default` is `False`. `app/main.py` separately gates the
   `/static` `StaticFiles` mount on the same `settings.web_enabled` flag,
   and calls `install_surface_globals(manifests, disabled, web_enabled)` to
   populate the `enabled_features`/`nav_items` Jinja globals once, at
   startup (see "Capability model" above).
4. Mount failures in a `core: True` feature re-raise (fails startup); a
   failure in a non-core feature is logged and skipped (fault isolation) —
   the app still boots without it.
5. `tests/architecture/test_feature_manifests.py` guarantees every package
   under `app/features/` on disk is registered in `FEATURE_MODULES` and that
   each manifest's `name` matches its package name — so a feature can never
   silently go unmounted or be mounted twice under a different name. The
   same test module's `test_nav_items_paths_exist_in_web_routers` guarantees
   nav↔route coherence (see "Capability model" above).
6. Separately, still inside `lifespan` and gated by `settings.seed_on_startup`,
   each enabled manifest's optional `seed` hook is dispatched via
   `asyncio.to_thread` (it does sync DB I/O); a seed is DEFERRED and
   NON-FATAL — a failure is caught, logged (`Feature %s seed skipped: %s`),
   and swallowed rather than propagated, so an unreachable DB at boot can
   never take startup down (seeds are idempotent; the next boot retries).

## Error handling

`app/core/exceptions.py` defines a `DomainError` hierarchy:
`NotFoundError` (404), `BadRequestError` (400), `ConflictError` (409),
`UnauthorizedError` (401), plus FastAPI's own `RequestValidationError`
(422) and an unhandled-exception catch-all (500). `app/core/errors.py`
maps every one of these to the same JSON envelope:

```json
{"code": "not_found", "message": "...", "details": null, "request_id": "..."}
```

`request_id` is pulled from `app.core.logging.request_id_var`, the same
context var `ObservabilityMiddleware` populates — so every error response
is correlatable with the structured request log line. Services raise
`DomainError` subclasses and let them bubble; routers never construct
`HTTPException` themselves for domain-level failures (see
`test_routers_do_not_issue_direct_queries` — routers stay thin; the
corollary is that error translation is centralized in `app/core/errors.py`,
not scattered per-router).

## Conflict handling: savepoints preserve RLS context (2b.1-T2, finding F3)

`get_db` (`app.core.db`) owns the request's outer transaction and issues
`SET LOCAL app.current_tenant` on it once, for RLS. The pre-2b.1 convention
at every expected-conflict site (duplicate email/slug/role-grant, etc.) was
`try: db.flush() except IntegrityError: db.rollback(); raise
ConflictError(...)` — but a bare `db.rollback()` rolls back that ENTIRE
outer transaction, discarding the `SET LOCAL` along with it. Any DB access
the caller's `except ConflictError` handler performed afterwards (a web
handler re-rendering a form from an already-loaded ORM object, or
re-querying a list for the re-render) then ran with no tenant context set —
under `FORCE ROW LEVEL SECURITY` that fails closed: either an
`ObjectDeletedError` re-loading an expired attribute, or a silently empty
result set (500s or blank re-renders, invisible on SQLite since it can't
enforce RLS at all — this is why the canary requires Postgres).

`app.core.db.conflict_savepoint(db)` is the fix, a context manager around
`Session.begin_nested()` (a `SAVEPOINT` scoped INSIDE the outer
transaction): on clean exit it commits the SAVEPOINT (a no-op release, not
the outer `COMMIT`); on any exception it rolls back ONLY the SAVEPOINT —
leaving the outer transaction and its `SET LOCAL` fully intact — then
re-raises unchanged. Every conflict site (parties create/update ×2 each,
`rbac` `create_role`/`assign_role`, `tenants` create, `auth` register,
`custom_fields` create) follows the same shape:

```python
try:
    with conflict_savepoint(db):
        db.add(row)
        db.flush()
except IntegrityError:
    raise ConflictError(...)
```

**The mutation must happen INSIDE the `with` block, never before it** —
entering a nested transaction auto-flushes any already-pending/dirty
objects on the session before the SAVEPOINT is actually established, so a
`db.add(row)` issued before `conflict_savepoint` would let THAT auto-flush
emit the conflicting statement with no savepoint yet in place to protect
the outer transaction, reintroducing the exact bug this helper exists to
fix.

Enforcement: `tests/architecture/test_no_feature_rollback.py` bans a bare
`db.rollback()` anywhere in `app/features/*/service.py` (this is also
CLAUDE.md's hard rule). `tests/test_conflict_rls_context.py` is the
Postgres canary — a duplicate-email person edit and a duplicate role grant,
both via the web flow, asserting a 200 re-render WITH correct data (grants
list still populated; edit form re-renders with the field error) rather
than the pre-fix 500/empty-render. This canary was RED against pre-2b.1
`main` by construction (the bug is invisible on SQLite, where these tests
cannot even run).

## Testing model

- **Unit** (`tests/unit/`, `tests/architecture/`) — in-memory SQLite, no
  network, no RLS. Fast; run with `make test-unit`. Covers CRUD/UoW/query
  helpers, error envelopes, feature registry, logging, tenant middleware
  logic, and the static architecture governance checks (thin routers, route
  guards including the tiered auth-guard test, feature registration, web
  template/import conventions
  (`tests/architecture/test_web_conventions.py`), and the per-route
  non-admin sweep (`tests/unit/test_admin_route_sweep.py`) — see CLAUDE.md's
  "Web portal (admin UI)" section for what each of these checks.
- **Integration** (`tests/*.py` at the top level —
  `test_cross_tenant_isolation.py`, `test_auth_tenant_claim.py`,
  `test_rbac_audit_isolation.py`, `test_security_middleware.py`,
  `test_party_isolation.py`, `test_settings_isolation.py`,
  `test_custom_fields_isolation.py`, `test_web_auth_isolation.py`,
  `test_admin_portal_e2e.py`) — require a real, migrated Postgres, because
  SQLite cannot enforce RLS. The first eight are the tenancy canaries: two
  tenants, cross-tenant read/write attempts must come back empty/404.
  `test_web_auth_isolation.py` is `test_auth_tenant_claim.py`'s cookie-path
  mirror (2b-T3): a tenant A cookie replayed against tenant B's host must
  redirect to login, never reach the dashboard, since
  `authenticate_request`'s tenant-claim check runs identically for both
  the bearer and cookie paths (the shared seam — see "Admin portal" above);
  it also proves logout only revokes the calling tenant's own session.
  `test_admin_portal_e2e.py::test_admin_portal_end_to_end_canary` is the
  phase's proof canary — one test function drives the ENTIRE portal purely
  through cookies/HTML forms (register → cookie login with the CSRF header
  bridge → create a party → define + set a custom field via the
  values-panel → view settings → a second tenant's cookie jar confirms RLS
  isolation holds across every one of those pages, not just the API layer →
  logout revokes the session server-side, not just the client cookie). Run
  with `make test-db-up && make test-integration && make test-db-down`
  (disposable Postgres via `docker-compose.test.yml`, trust auth,
  localhost-only, throwaway). `TEST_DB_PORT` (and the other `TEST_DB_*`
  Make vars) are `?=`-overridable if the default port is taken.
- **CI** (`.github/workflows/ci.yml`) — four jobs: `quality` (matrix over
  lint/lint-imports/type-check/security, `fail-fast: false`), `unit`
  (`tests/unit` + `tests/architecture` with coverage), `integration` (drives
  `docker-compose.test.yml` directly rather than a `services:` block,
  because the `env:` context isn't available there), and `docker-build`
  (builds the prod image, boots it with a deliberately unreachable
  `DATABASE_URL`, and health-gates `/health`).

## Deploy

`docker-compose.yml` (prod) requires a published `APP_IMAGE`, no bind
mounts, resource limits (`APP_MEM_LIMIT`, `APP_PIDS_LIMIT`), and a
container healthcheck against `/health`. `docker-compose.dev.yml` is an
overlay adding a local build + throwaway Postgres (`make docker-dev`).
`scripts/deploy.sh <tag>` is the only production migration path: verify
image on registry → `pg_dump` backup → pin `APP_IMAGE` in `.env` → pull →
`alembic upgrade heads` (one-off container) → recreate `app` → health gate
(retries/interval/timeout all config knobs) → auto-rollback to the previous
pin on health-gate failure (migrations are not auto-reverted; new revisions
must stay backward-compatible with the previous release).
