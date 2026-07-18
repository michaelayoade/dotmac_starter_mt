# Phase 2 backlog (from phase 1 final review, 2026-07-17)

Carried out of the phase-1 whole-branch review and per-task review cycles. Each item
was explicitly triaged "phase-2 ticket" — none blocks the phase-1 merge.

## Features (spec-scoped)

- **Core parity:** auth hardening (MFA/TOTP, refresh rotation, password reset, lockout,
  API keys) — still open (now explicitly phase 2c, per the 2b completion criteria).
  ~~RBAC parity (incl. mounting `GET /rbac/roles` —
  `rbac/service.py::list_roles` exists, currently uncalled; add an explicit tenant filter
  when wiring)~~ — **delivered 2a-T2**: `GET /rbac/roles` mounted with explicit tenant
  filter + pagination. ~~settings-as-data~~ — **delivered 2a-T3..T5**: spec registry +
  resolver + tenant admin API (`app/core/settings_models.py`/`settings_resolver.py` +
  `app/features/settings/`). ~~**Branding** (`ui_branding` setting spec)~~ — **delivered
  2b-T2/T7**: `app.core.branding.load_branding` is the consumer
  (`/admin/settings/branding`); the no-orphan-settings allowlist is now EMPTY.
- ~~**Custom fields feature package**~~ — **delivered 2a-T8..T10**: port SoT dotmac_erp
  `finance/automation` custom-field module, generalized (string entity_type registry,
  tenant_id + RLS, domain exceptions, settings-driven per-entity limit) in
  `app/features/custom_fields/`. Runtime-field requirement demonstrated by the
  `eye_color` e2e canary (`tests/test_custom_fields_isolation.py`) — zero migrations
  between defining and using a field.
- After core parity lands: archive `dotmac_starter` with a pointer README.

## Architecture / correctness follow-ups

- **Lazy engine construction in `app/core/db.py`** — engines currently build at import
  time from DATABASE_URL; blocks importing the app without env, makes
  `validate_settings`' "DATABASE_URL is required" branch unreachable, and forced the
  unit-test env pin.
- **`get_uow` tenant context** — `app/core/unit_of_work.py::get_uow` yields sessions
  without RLS `set_config`; either take `Request` and apply the same context as `get_db`,
  or mark it loudly platform/maintenance-only. Zero callers today.
- **Feature fault isolation vs. reality** — `load_manifests` imports every feature module
  (including disabled ones) before `mount_features` filters; either skip imports for
  disabled features and wrap non-core imports in try/except, or correct the docs
  (`app/core/features.py` docstring, `.env.example`, ARCHITECTURE.md) to say
  "mount-time only".
- **Auth→RBAC coupling** — `auth/service.py::_assign_first_user_admin` writes Role/
  PersonRole rows directly; belongs behind an rbac-owned function. Invisible to
  import-linter since the six identity models moved to core (ADR-0002).
- **Engine hardening port delta** — spec lists sub's `statement_timeout`/`lock_timeout`/
  `idle_in_transaction_session_timeout` connect args; not ported in phase 1
  (documented deviation).
- **Governance additions:** static check that every new tenant-scoped table ships an RLS
  policy in its migration; test that `alembic/env.py` + `tests/unit/conftest.py` model
  imports cover all feature model modules (a forgotten import makes autogenerate propose
  dropping tables).

## Smaller tickets

- `LOG_LEVEL` setting for `setup_logging()` (currently fixed INFO default).
- Share one health-path constant between tenant middleware (`_HEALTH_PATHS`) and the
  rate-limit bypass (currently only literal `/health`) before mounting `/health/ready`.
- ~~/static/* requests pay tenant resolution (up to 2 DB SELECTs each; 500s when DB down) —
  exempt static prefix in TenantResolverMiddleware (prefix-match, carefully — assigned
  to 2b-T2).~~ — **delivered 2b-T2**: `_is_static_path()` bypasses
  `TenantResolverMiddleware` before any DB query, exact/prefix-matched (`/static`,
  `/static/...`), with near-miss tests (`/staticevil`, `/static2/x`) proving the
  trailing-slash check isn't a bare `startswith`.
- ~~Service payload typing: replace `payload: Any` with concrete Pydantic schemas across
  the four feature services (pairs with mypy tightening).~~ — **delivered 2a-T1/T2**,
  now a standing hard rule enforced by `tests/unit/test_service_typing.py`.
- Test harness: replace private `trans._parent` savepoint-restart idiom with SQLAlchemy
  2.0 `join_transaction_mode="create_savepoint"`.
- deploy.sh: generic ERR trap should also `up -d` the previous image for mid-`up` failures
  (today only the health-gate path restores); qualify `IMAGE_NAME` and rename CI job's
  `IMAGE_TAG` → `IMAGE_REF` when the GHCR publish job is added.
- ~~Service rollback convention: document that after `db.rollback()` (which discards the
  transaction-scoped RLS context) the request must end, never continue.~~ — **RESOLVED
  2b.1-T2 (finding F3)**: rather than documenting the hazard as a convention to remember,
  the bare `db.rollback()` call itself is gone from every feature-service conflict site.
  `app.core.db.conflict_savepoint(db)` wraps the mutation in a `SAVEPOINT`
  (`Session.begin_nested()`) instead — on `IntegrityError` it rolls back only the
  savepoint, leaving the outer transaction and its `SET LOCAL app.current_tenant` fully
  intact, so the caller's `except ConflictError` handler (a web re-render, a re-query)
  keeps working under RLS instead of running context-less. Enforced going forward by
  `tests/architecture/test_no_feature_rollback.py` (bans a bare `db.rollback()` in
  `app/features/*/service.py`) and canaried against real Postgres by
  `tests/test_conflict_rls_context.py` (RED against pre-2b.1 `main` by construction —
  the bug is invisible on SQLite). See `docs/ARCHITECTURE.md`'s "Conflict handling:
  savepoints preserve RLS context" section and CLAUDE.md's matching hard rule.
- Scoping style convention: services relying on RLS alone should say so in a comment
  (persons service style); pick one convention for explicit-vs-RLS-only tenant filters.
- Dangling doc pointers to untracked task reports (Dockerfile, query.py, bump_version.py,
  deploy.sh headers) — commit the reports or strip the references.
- `rbac/web.py::role_grants_submit` re-renders `/admin/role-grants` on every validation/
  conflict failure with `q=None` (`_render_grants_page(request, db, tenant, q=None, ...)`),
  discarding whatever party-search filter was active in the grantable-parties list before
  the failed submit — a cosmetic one-liner (`raw.get("q")` from the submitted form, once
  the template also posts it as a hidden field) not fixed as of 2b-T8/T9 (checked directly
  against the source for this task; still `q=None` on all three failure branches).

## Added during phase 2b execution

- Admin portal governance (tiered guard test, web-conventions checks, non-admin sweep)
  scopes itself to `templates/{admin,auth}` and the `/admin` prefix — see the
  "2b-T8's web-conventions..." SOT-complete gap below; extend both when a non-admin
  portal surface lands.
- ~~`DISABLED_FEATURES` has no per-router granularity: a feature's JSON router and its
  `web.py` router are both registered on the same `FeatureManifest.routers` list, so
  disabling `parties` (etc.) drops its JSON API and its `/admin/parties/*` screens
  together — there is no way to keep one and drop the other short of splitting the
  manifest, which nothing needs yet (documented as-is in README's "Disabling a feature").~~
  — **PARTIALLY RESOLVED 2b.1-T1 (finding F1)**: the manifest split now exists —
  `FeatureManifest.routers` (JSON API) and `FeatureManifest.web_routers` (admin-portal
  HTML) are separate fields, and a NEW, orthogonal switch (`WEB_ENABLED`) mounts/unmounts
  every feature's `web_routers` at once, independent of `DISABLED_FEATURES`. What is
  NOT resolved: `DISABLED_FEATURES=<name>` itself still turns off one named feature's
  `routers` AND `web_routers` together — there is still no way to keep `parties`'s JSON
  API while dropping only its `/admin/parties/*` screens (or vice versa) for that ONE
  feature. `WEB_ENABLED` only ever answers the whole-portal question ("any web at all,
  for every feature"), not a per-feature one — a genuine per-feature/per-surface toggle
  remains open if a future consumer needs it. See `docs/ARCHITECTURE.md`'s "Capability
  model" section.
- `.env.example` had zero entries for the `BRAND_*` static-branding overrides
  `app.core.branding.get_brand()` reads via `os.getenv` (deployment-static identity layer,
  distinct from the per-tenant `ui_branding` DB setting) — a real as-built gap, closed in
  this task (2b-T9) alongside `BRAND_CONFIG_PATH`.
- The mutable-resource ownership table's "Auth sessions" and "Audit events" rows had
  gone stale since 2b-T3/T6 (said "no revoke/logout write path yet" and "called from
  rbac/router.py and settings/router.py only") — corrected in this task (2b-T9):
  `web_logout` revokes sessions server-side, and `rbac/web.py`/`settings/web.py` both
  call `write_audit_event` too.

## Added during phase 2b.1 execution (Michael's post-merge review findings F1–F7)

Michael's post-merge review of 0.6.0 (22192f6) raised seven findings, tracked and closed
by plan `docs/superpowers/plans/2026-07-18-phase2b1-sot-composability.md`. F2 (email
authority) and F4 (portal-wide branding) already had pre-existing backlog entries above,
now struck with resolution notes; F1 and F3 are struck above too. F5, F6, F7 had no prior
backlog entry (net-new findings from the fresh review, not phase-1 carryover) — recorded
here so all seven are discoverable as closed in one place:

- **F5 (dead nav links + broken fragments when a feature is disabled)** — **DELIVERED
  2b.1-T1**: `tests/architecture/test_feature_manifests.py
  ::test_nav_items_paths_exist_in_web_routers` fails the build if a manifest's `NavItem`
  points at a route not mounted in that manifest's `web_routers`; the party detail
  page's custom-fields embed is gated by `{% if 'custom_fields' in enabled_features %}`
  (the optional-slot pattern — see `docs/ARCHITECTURE.md`'s "Capability model" section)
  so `DISABLED_FEATURES=custom_fields` renders the party detail page 200 without the
  panel instead of a broken htmx fragment.
- **F6 (`show_in_form`/`show_in_detail`/`show_in_list` declared but never consumed)** —
  **DELIVERED 2b.1-T5**: `custom_fields_service.list_for_entity(..., visible_in=...)` is
  the single query-level owner; the values-panel form, its detail-only read section, and
  the definitions table's "visible in" badge are the three consumers. See
  `docs/ARCHITECTURE.md`'s "Visibility flags are consumed" subsection.
- **F7 (logout was a CSRF-exempt GET)** — **DELIVERED 2b.1-T5**: `POST /admin/logout`
  only; `GET /admin/logout` removed (BREAKING, in CHANGELOG 0.6.1). Still carries
  `require_tenant` only (no `require_web_auth`) — logout must always succeed even on an
  expired/foreign-tenant cookie; the POST method plus the CSRF header-bridge is what
  stops a FORCED logout now, not a role check.

## Added during phase 2a execution

- Settings: add `sqlite_where` mirrors to the domain_settings partial unique indexes so the
  resolver precedence test can run unstubbed on SQLite.
- Settings: `_normalize_for_db` None-handling for json/boolean types → clean BadRequestError
  at the settings API boundary (owned by T5's validation; verify it landed there).
- Settings cache (Redis) with invalidation on write — phase 3, alongside Celery/Redis
  infra (noted in `app/core/settings_resolver.py`'s module docstring; no caching exists
  yet, every `resolve_value` call hits Postgres). This is also the fix for 2b.1-T4's
  (F4) one-extra-DB-read-per-authenticated-web-request cost (`get_request_branding` ->
  `load_branding` -> `resolve_value`) — request-scoped memoization (landed in T4) avoids
  N reads per request, but every request still pays one; the Redis cache below removes
  even that.
- ~~RBAC: consider `require_user_auth` (not admin) for `GET /rbac/roles` when 2b builds
  role-assignment dropdowns.~~ — **moot as of 2b-T6**: the role-grant web dropdown
  (`/admin/role-grants`) calls `rbac_service.list_roles` directly, server-side — it
  never hits the JSON `GET /rbac/roles` route, so no guard change was needed. The route
  itself still requires `require_role("admin")`, unchanged; revisit only if a future
  JS-driven (not server-rendered) dropdown needs to call it directly from the browser.
- Custom-fields definitions list paginates in-router via Python slice (bounded by
  max_per_entity, default 20); if the bound ever rises materially, push limit/offset into
  list_for_entity via apply_pagination.

## SOT-complete gaps (criteria added to spec 2026-07-17)

- ~~`Party.display_name`: stored projection of subtype fields, write-once, no drift
  detection/repair — when 2b adds update endpoints: single write-owner + idempotent repair,
  or compute-at-read. Still open; explicitly named as a known gap in
  `docs/ARCHITECTURE.md`'s "Known dual-writer: Parties" section.~~ — **delivered 2b-T5**:
  `app.core.identity.person_display_name`/`normalize_email` are the single-owner
  implementations of the invariant both writers (parties service, auth service) call;
  `update_person_party`/`update_organization_party` (new this task) recompute
  `display_name` on every write, so it's no longer write-once — repair is just "re-save"
  (call the update function). `docs/ARCHITECTURE.md`'s ownership table and dual-writer
  section both updated in the same commit. API parity (`PATCH /parties/{id}` JSON route)
  intentionally NOT added this task (brief scoped it web-only) — the service functions
  exist, wiring a JSON route is one line later; noted here so it isn't lost.
- ~~Ownership table: T11's provenance table must name an owner for every mutable resource
  and state transition (not just models) — routes/service functions per resource.~~ —
  **delivered 2a-T11**: `docs/ARCHITECTURE.md` carries both the model provenance table
  (owner + port SoT for all 12 ORM model classes) and the mutable-resource ownership list
  (resource → owning service function, including the parties dual-writer named with its
  shared invariants). Going forward this becomes maintenance, not a one-off: **extend the
  ownership list to new routes/tasks/event handlers as they arrive** — every future task
  that adds a mutable resource or a new writer of an existing one must update the table in
  the same commit, not leave it to a later doc pass.
- External-system contracts: none in the starter yet; when OpenBao/webhooks arrive (2c),
  each must be declared transport vs contracted authority in ARCHITECTURE.md.
- ~~`UserCredential.email` (`app/features/auth/models.py`) duplicates `Party.email` —
  written once at `register`. **The drift surface is now LIVE as of 2b-T5**: `update_person_party`
  can change or explicitly NULL `Party.email` while `UserCredential.email` (the login
  identity) persists unchanged — a person's profile can show no email while login still
  works via the credential copy. A cross-feature guard is not possible under feature
  independence (parties cannot query auth's UserCredential). 2c's email-update flows
  must pick a single write-owner (mirroring the `Party.display_name` resolution above)
  or add a repair path; until then the two columns can silently disagree.~~ —
  **RESOLVED 2b.1-T3 (finding F2)**: rather than picking a write-owner between two
  columns, the second column is gone. Migration
  `alembic/versions/20260718_0005_single_email_authority.py` drops
  `user_credentials.email` + its unique constraint entirely;
  `auth/service.py::login` resolves `Party` by `(tenant_id,
  normalize_email(email), party_type=person)` first, then `UserCredential` by
  `party_id` only. `Party.email` is now the single email column
  system-wide (see `docs/ARCHITECTURE.md`'s ownership table, `Party.email`
  row, and the F2 resolution note under "Known dual-writer: Parties") — no
  repair path needed because there is nothing left to re-sync. Intended,
  documented consequence: NULLing a person party's email now disables login
  for that party outright (canaries: `tests/test_auth_email_authority.py`,
  unit pin: `tests/unit/test_auth_service.py::
  test_login_null_party_email_rejected`).
- Custom fields: deactivating a `CustomFieldDefinition` (`deactivate_field`) leaves any
  already-stored values for that `field_code` sitting in every entity's `custom_fields`
  JSONB column — there is no cleanup path. Orphaned keys are invisible to
  `list_for_entity`/`validate_values` (inactive definitions are excluded by default) but
  are never deleted, so `get_values` can return keys with no active definition behind
  them, and reactivating the definition later resurrects whatever stale value happens to
  still be there.
- Governance-check evasion notes (found auditing this review's own test additions —
  none exploited, but the checks are narrower than they look):
  - `tests/unit/test_service_typing.py`'s Any-ban regex (`r"payload:\s*Any\b"`) only
    matches a parameter literally named `payload` — a service function typed
    `data: Any` or `updates: Any` evades it entirely.
  - `tests/architecture/test_no_orphan_settings.py`'s orphan-matcher treats any quoted
    string literal matching a spec's `key` anywhere in `app/` (outside settings/the
    resolver) as "consumed" — a coincidental literal (e.g. an unrelated dict key or
    docstring example that happens to share the setting's name) would satisfy it without
    the setting actually driving behavior.
  - `tests/architecture/test_route_guards.py::test_every_route_has_a_guard` accepts ANY
    `require_*`-prefixed dependency name, so it cannot distinguish tenancy
    (`require_tenant`) from authentication (`require_user_auth`/`require_role`) — this is
    exactly how the Group 2 parties gap (mutations reachable with only a resolved tenant,
    no auth) passed the architecture suite for two tasks. ~~Proposal for 2b: a tiered
    guard test...~~ — **delivered 2b-T8**:
    `test_mutating_routes_require_an_auth_tier_guard` requires every POST/PUT/PATCH/DELETE
    route to carry a guard from the hand-built `AUTH_GUARD_NAMES` set (`require_user_auth`,
    `require_role`, `require_web_auth`, `require_platform` — deliberately not a
    `require_`-prefix match), unless allowlisted (`MUTATING_ALLOWLIST`: the two register/
    login pre-auth routes). Note: `test_every_route_has_a_guard`'s original looser
    behavior (any `require_*` counts) is UNCHANGED and still runs alongside the new,
    stricter test — the gap this bullet describes is closed by addition, not by editing
    the original check.
- `SettingDomain` (`app/core/settings_models.py`) is duplicated in two places that must
  change together and aren't statically linked: the Python enum and the migration's
  `ck_domain_settings_domain` CHECK constraint (`"domain IN ('auth', 'audit', 'branding',
  'custom_fields')"`, `alembic/versions/20260717_0002_settings_table.py`). A 2b feature
  author adding a new `SettingDomain` member without a companion migration altering the
  CHECK constraint gets an enum member that Python accepts but Postgres rejects at INSERT
  time — see `docs/ARCHITECTURE.md`'s extension-points note.
- `SettingSpec.default = None` is a seed hazard for non-`json` value types:
  `seed_platform_defaults` -> `ensure_by_key` -> `_normalize_for_db` calls `str(value)`
  for `string`/`integer` specs, so a `string`/`integer` spec declared with `default=None`
  seeds the literal text `"None"` (not a real null) and a `boolean` spec with
  `default=None` seeds `"false"` silently. Only `json`-typed specs handle `None`
  correctly (stored as `value_json IS NULL`, which then fails the
  `ck_domain_settings_value_alignment` CHECK — loud, not silent). No spec declares
  `default=None` today; a future spec author should not assume a `None` default is safe
  for anything but `json`.
- 2b-T8's web-conventions template checks and non-admin sweep scope themselves to
  `templates/{admin,auth}` and the `/admin` path prefix; a future non-admin portal
  surface (anticipated by `require_web_auth`'s docstring) escapes all four checks
  until their globs/prefixes are extended — extend them in the same task that adds
  such a surface.

## From the 2b final whole-branch review (2026-07-18)

- **GET-tier guard gap (untracked→tracked):** the tiered auth-guard test covers MUTATING
  routes only; a future `GET /admin/...` guarded by `require_tenant` alone would serve
  tenant data unauthenticated and pass the build. Every current GET carries
  require_web_auth (verified route-by-route). 2c ticket: extend the tiered test to GETs
  under /admin (or any web prefix).
- ~~**Portal-wide tenant branding (untracked→tracked):** `load_branding` (per-tenant
  ui_branding override) is consumed ONLY by the branding editor's own preview — the rest
  of the portal renders the static brand. Phase-3 ticket (behind the settings cache):
  wire load_branding portal-wide; ALSO tighten its merge to an allowlist of known brand
  keys (currently merges arbitrary override keys; harmless today, admin-only writer).~~ —
  **RESOLVED 2b.1-T4 (finding F4)**: `app.core.branding.get_request_branding(request, db)`
  resolves `load_branding`/`get_brand()` ONCE per request, memoized on
  `request.state.branding`; `app.core.templating.render()` injects it as the `brand`
  context key for every web render unless the route already set its own (see that
  module's and `app.core.branding`'s docstrings for the 3-call-site wiring:
  `require_web_auth`, `GET`/`POST /admin/login`). `load_branding`'s merge is now
  allowlisted to `_KNOWN_BRAND_KEYS` (`name`, `tagline`, `logo_url`, `primary_color`,
  `accent_color`, `custom_css`) — an unknown override key is dropped, not merged. Cost:
  one extra DB read per authenticated web request (the settings-cache phase-3 ticket
  immediately below removes it; not needed to ship this fix).
- **Platform-admin surface:** the 2b plan's scope-deviation note said this was
  "backlogged" — this is now that entry. Tenant CRUD screens need a platform-scoped
  surface (require_platform hardening included — it's a documented stub that counts as
  an auth-tier guard today).
- 2c ticket batch (small): ~~`DISABLED_FEATURES=web` pin test~~ — **DELIVERED 2b1-T1**:
  `tests/unit/test_web_surface.py` (`_inspect_app({"DISABLED_FEATURES": "web"})`) pins
  that `DISABLED_FEATURES=web` drops only `GET /admin`, not other features' `/admin/*`
  or JSON routes; move route-level `write_audit_event` calls into services (4
  hand-mirrored sites today); cross-tenant values-panel HTTP probe (tenant B → tenant
  A's URL → 404); ~~`login()` uses `identity.normalize_email` (read-path
  consistency)~~ — **DELIVERED 2b1-T3**: `auth/service.py::login` calls
  `normalize_email(payload.email)` before querying `Party` (single email authority,
  finding F2); post-login redirect preserves the query string; Google Fonts CDN
  dependency documented for airgapped consumers.
- Cleanup candidate (harmless, from 2b1-T1): ~15 dead `active_nav` context keys still
  set in `web.py` render sites (`app/features/web/web.py`,
  `app/features/parties/web.py`, `app/features/rbac/web.py`,
  `app/features/settings/web.py`, `app/features/custom_fields/web.py`) — sidebar
  highlighting moved to path-based matching (`templates/components/sidebar.html`,
  `templates/layouts/admin.html`), so no template reads `active_nav` anymore; the
  context keys are inert and can be deleted whenever one of those files is next
  touched.

## Display/locale settings (user rule, 2026-07-18 — "everything by settings: datetime etc, all")

Runtime/display behavior becomes tenant-configurable via settings-as-data: a `display`
SettingDomain (timezone default UTC, date_format, datetime_format; page sizes where they
matter), one core formatting helper (e.g. app/core/formatting.py) consumed by every
template/service that renders datetimes — no hardcoded strftime/timezone literals
anywhere (reviewers flag them like hardcoded ports). Each spec needs a real reader
(no-orphan-settings enforces). Scheduled as the FIRST task of the next plan (before or
alongside 2c auth hardening); the portal's audit/list timestamps are the initial
consumers.
- `UnitOfWork.savepoint()` (unused by any request path) shares the `begin_nested()`
  auto-flush ordering hazard fixed across services in 2b1-T2 — re-audit + docstring
  ordering note before it is ever wired in (2b1-T2 review).

## Verified gaps (2026-07-18 capability sweep — Michael's checklist: impact preview +
## confirm, granular RBAC, list_query, Carbon/WCAG UI SoT, no-orphan codes)

Planned as `docs/superpowers/plans/2026-07-18-capability-hardening.md` (runs after the
display-settings plan merges). Evidence from a dual sweep of this repo + dotmac_erp
(fleet pattern source). Summary verdicts:

- **Impact preview + confirm — PARTIAL.** Confirms exist (`hx-confirm` on party delete,
  custom-field deactivate) but every one is static copy; no computed "affects N records"
  preview anywhere (party delete cascades `party_roles` via DB `ondelete="CASCADE"`
  silently; deactivate copy promises "values are kept" without counting them). ERP has
  the service-side half (`bulk_actions.py::can_delete -> (bool, reason)` guard idiom) but
  no count-based preview UI either — the preview endpoint pattern is net-new.
- **Granular RBAC — ABSENT.** Role-name matching only; the only guard string in the app
  is `"admin"` (hardcoded at every `require_role` call site + `auth/service.py:268,271`
  + the portal gate). No permission table, no per-action codes. ERP's shape to port:
  colon-namespaced permission codes (`fleet:vehicles:manage`), `Permission`/
  `RolePermission` tables, read/manage guard pairs, admin bypass (`app/web/fleet.py`,
  `scripts/seed_rbac.py`, `app/web/deps.py::has_permission`). Prerequisite for the
  phase-3 "portal role loosening" thread.
- **list_query — PARTIAL.** `app/core/query.py` has `apply_pagination`/`apply_ordering`/
  `escape_like`, but no unified params/envelope schema; `apply_ordering` has NO caller
  (dead helper — wire it or remove it); `GET /tenants` is unbounded; custom-fields
  definitions paginate by in-Python slice after fetching the full list; page-size
  literals (`PAGE_SIZE=20` etc.) are per-file constants, not settings (violates the
  everything-by-settings rule). ERP SoT to port:
  `app/services/finance/platform/list_helpers.py` (`ListParams.from_request` with
  page/limit-clamp/search/`-`-prefixed sort/filters + `ListResult.pagination_context()`).
- **Carbon/WCAG UI SoT — PARTIAL (and NOT Carbon).** Zero Carbon usage in this repo AND
  in dotmac_erp (verified) — the fleet UI SoT is Tailwind + design tokens (here: v4
  `@theme` in `static/css/src/main.css`; ERP: CSS vars in base.html + design-system
  rule doc). Real WCAG gaps here: no skip link, no `:focus-visible` styling, no
  `sr-only` utility, icon-only delete button has `title` but no `aria-label`
  (`table_macros.html:130-137`), `<main>` lacks a skip target id, no documented
  conformance target. Strengths: form macros pair label/for-id, toast region has
  `role="status" aria-live`, SVGs `aria-hidden`.
- **No-orphan codes — PARTIAL.** Settings keys (orphan test) and nav/feature manifests
  (coherence tests) are governed; custom-field types are enum+DB-check constrained. But
  audit action strings (`"settings.update"`, `"role.create"`, `"role.grant"`) and role
  slugs are free-form literals — any route can invent either; envelope error codes are
  funnel-constrained by typed exceptions but have no canonical-list test. No such
  governance exists in ERP either — net-new, extending the starter's own
  no-orphan-settings precedent.

## 2c-auth

- Constant-time login: credential/party misses short-circuit without a dummy hash compare
  (pre-existing before 2b1-T3, unchanged by it) — 2c adds a dummy-verify on the miss path
  so timing doesn't distinguish "no account" from "wrong password" (2b1-T3 review).
- Migration round-trip (upgrade→downgrade→upgrade) has no automated enforcement; consider
  a CI/integration step exercising the last migration's cycle.
