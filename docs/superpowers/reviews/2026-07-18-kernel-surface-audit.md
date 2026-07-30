# Kernel surface audit — Task 0 (boundary audit)

> **Status:** authoritative facts + classification for the kernel-boundary program.
> Produced 2026-07-30 on branch `kernel-boundary-audit`, executing Task 0 of
> `docs/superpowers/plans/2026-07-18-kernel-boundary.md`. **Audit-only** — no package was
> created, no module moved, no import rewritten, no runtime behavior or version changed.
> The re-planned Task briefs in the final section are authoritative over that plan's original
> Task 1–6 assumptions (per the plan's own "Task 0 output is authoritative" note).

## Method

`scripts/audit_kernel_surface.py` (committed, deterministic — sorted output, no
clock/random/host input, byte-identical on an unchanged tree) parses every `.py` file under
`app/`, `alembic/`, `tests/`, `scripts/` with `ast` and records each import of an `app.core.*`
symbol together with the **area** of the importing file:

| area | paths | meaning for the boundary |
|---|---|---|
| `kernel` | `app/core/**` | the surface being audited |
| `assembly` | `app/main.py`, `app/features/**` | product-composition runtime — the real public-API pressure |
| `alembic` | `alembic/**` | migration runtime — public, but on the migration path not the request path |
| `tests` | `tests/**` | **must not** create public-API obligations on their own |
| `tooling` | `scripts/**` | operator CLIs — public but out-of-band |

A symbol imported by `assembly`/`alembic` is candidate **public API**; one imported only by
`tests`/`tooling` is **not** public; a private-named (`_`-prefixed) symbol crossing a module
boundary into a public area is a **leak**. Regenerate anytime:

```
poetry run python scripts/audit_kernel_surface.py          # markdown tables
poetry run python scripts/audit_kernel_surface.py --json   # machine data
```

## Headline numbers (generated 2026-07-30)

- **28** kernel modules; **94** distinct imported symbols.
- **73** public-runtime symbols (assembly/alembic consumers); **21** test/tooling-only.
- **0** leaked internals — no runtime code imports a `_`-private kernel symbol across a module
  boundary. The only `_`-private cross-module imports (`_is_platform_path`, `_STRICT_CSP`) are
  **test-only**, which is legitimate white-box testing.
- **0** symbols from an `__all__`-declaring module consumed outside that `__all__`.
- **22 of 28** modules declare **no `__all__` at all** yet are runtime-consumed — so the kernel
  today has essentially **no declared public surface**. Establishing one is Task 2's core job;
  this audit supplies the intended contents.
- Import-linter's "core must not import features" contract holds: every `app.features` string in
  `app/core/**` is a comment/docstring; there is no actual feature import (the `features.py`
  registry loads features by `importlib`, never a static import).

## Classification

### A. Deliberate public kernel API (keep; export explicitly in Task 2)

These are the composition and domain primitives a product assembly legitimately needs. Grouped
by the role they play in `create_app(spec)`:

- **App composition:** `config.settings`, `config.validate_settings`, `errors.register_error_handlers`,
  `logging.setup_logging`, `features.{FeatureManifest, NavItem, load_manifests, mount_features}`,
  `templating.{render, install_surface_globals}`.
- **Middleware stack:** `middleware.csrf.CSRFMiddleware`, `middleware.observability.ObservabilityMiddleware`,
  `middleware.rate_limit.RateLimitMiddleware`, `middleware.security_headers.SecurityHeadersMiddleware`,
  `middleware.tenant.TenantResolverMiddleware`.
- **Persistence + transaction contract:** `db.{get_db, get_platform_db, conflict_savepoint, platform_session}`;
  `models.{Base, TimestampMixin, uuid_pk}` (the declarative base + mixins a feature model needs).
- **Identity/tenancy model:** `models.{Tenant, Party, PartyPerson, PartyOrganization, PartyType, Role,
  PartyRole, AuthSession, UserCredential}`, `models_platform.PlatformAdmin`. These are the ADR-0002
  cross-feature identity primitives; they are kernel-owned by design.
- **Auth + guards:** `deps.{get_db, get_platform_db, require_tenant, require_user_auth, require_role}`,
  `web_deps.{require_web_auth, is_secure_request, safe_next_url}`,
  `platform_auth.{require_platform_admin, platform_auth_router}`,
  `security.{hash_password, verify_password, password_needs_rehash, hash_token, issue_access_token}`.
- **Errors:** `exceptions.{NotFoundError, BadRequestError, ConflictError, UnauthorizedError, ForbiddenError}`.
- **Cross-cutting services:** `audit.{AuditEvent, write_audit_event}`, `identity.{normalize_email, person_display_name}`,
  `crud.CRUDManager`, `query.{apply_pagination, escape_like}`, `branding.{load_branding, get_request_branding}`.
- **Settings — READ + DECLARE contract only:** `settings_models.{SettingDomain, SettingValueType}`,
  `settings_resolver.{SettingSpec, register_specs, resolve_value}`. These are the symbols a *non-settings*
  feature imports (auth/custom_fields/rbac call `resolve_value`; any feature declares specs via
  `register_specs`/`SettingSpec`).

### B. Leaked internals — need a wrapper or relocation before the split pins the surface

No leaked *symbols*, but three structural leaks the split must resolve:

1. **Template + static assets are resolved by relative CWD path — the one hard packaging blocker.**
   `templating.py:61` builds `Jinja2Templates(directory="templates")` and `main.py:141` mounts
   `StaticFiles(directory="static")`, both relative to the process working directory. A `pip
   install`ed kernel has no `templates/`/`static/` under CWD. **Fix (Task 1):** ship `templates/`
   and `static/` (incl. the vendored `static/fonts/`) as **package data** and resolve them via
   `importlib.resources`/`Path(__file__)`, not CWD. The `templates` singleton and `static_asset_url`
   must take a resolved package path. Until this is done the kernel wheel cannot render or serve
   assets from an external consumer — this is the empty-assembly-boot proof's real risk (Task 4).
2. **Settings write/admin mechanics live in core only for import-independence.**
   `settings_resolver.{upsert_by_key, ensure_by_key, resolve_with_source, validate_spec_value, get_spec,
   all_specs}` are consumed **only by the `settings` feature package itself** (service/web/router/seed) —
   never by another feature. They sit in core because features can't import each other, not because they
   are kernel API. **Decision (Task 2):** expose them under a deliberately-named narrow surface
   (e.g. `dotmac_kernel.settings` admin/registry API) documented as "for the settings module", kept
   distinct from the read/declare contract in group A — so a consumer can't mistake the write path for
   general kernel API. No relocation needed (they must stay reachable without a feature import); the
   fix is a named, documented boundary + `__all__`.
3. **`templating.templates` and `logging.request_id_var`/`JsonLogFormatter` are import-time singletons.**
   Consumed today directly (tests) and transitively (render). The kernel must expose the *behavior*
   (`render`, `setup_logging`) as public and treat the singletons as internal, so a consumer never
   constructs a second Jinja env (the module docstring already warns against this) — enforce with
   `__all__` in Task 2.

### C. Assembly-owned rather than kernel-owned

- **`FEATURE_MODULES`** (`app/features/__init__.py`) — the list of reference feature packages. This is
  the *reference assembly's* manifest, not the kernel's; it becomes `app/assembly.py`'s concern when
  `main.py` shrinks to `create_app(spec)` (Task 3). The kernel never enumerates features.
- **`brand.json`** at the repo root — deployment-static branding config. The kernel provides `_DEFAULTS`
  + the loader; the **assembly** provides the file (and `BRAND_CONFIG_PATH`). Kernel default set stays
  in `branding.py`; the file is assembly data.
- **The `custom_fields` feature table + its metadata registration** — see Migration ownership below.
- **The seven reference feature packages** (`tenants`, `auth`, `parties`, `rbac`, `settings`,
  `custom_fields`, `web`) — all assembly. They consume kernel API; none is kernel.

## Migration ownership

`alembic/` is a **single shared tree that mixes kernel and assembly ownership** — the split must
address this explicitly. Table → owning model module → creating migration:

| table | owner module | area | created in |
|---|---|---|---|
| `tenants`, `tenant_domains` | `app/core/models.py` | kernel | 0001_initial_tenant_schema |
| DB roles `app_admin`/`app_user`/`platform_api` + `app_current_tenant_id()` + base RLS | (migration only) | kernel | 0001_initial_tenant_schema |
| `domain_settings` | `app/core/settings_models.py` | kernel | 0002_settings_table (+0006 display domain) |
| `parties`, `party_persons`, `party_organizations`, `roles`, `party_roles`, `user_credentials`, `auth_sessions` | `app/core/models.py` | kernel | 0003_party_identity (+0005 single-email) |
| `custom_field_definitions` | `app/features/custom_fields/models.py` | **assembly** | **0004_custom_fields** |
| `platform_admins`, `platform_sessions` | `app/core/models_platform.py` | kernel | 0007_platform_identity |

Findings:

- **13 of 14 tables, all 3 DB roles, the `app_current_tenant_id()` function, and the base RLS/grant
  machinery are kernel-owned.** Migrations 0001–0003, 0005–0007 are entirely kernel.
- **Migration 0004 and the `custom_field_definitions` table are assembly-owned** but live in the shared
  kernel tree, and **`alembic/env.py` statically imports `app.features.custom_fields.models`** to
  populate `target_metadata`. That is the ONE place migration wiring reaches into a feature.
- **Decision (re-plan):** the kernel ships its **base migrations** (0001–0003, 0005–0007) as package
  data with a stable starting revision; an assembly owns its own migration directory that **depends on**
  the kernel's head (Alembic `depends_on`/multiple version locations), and `env.py` becomes an
  assembly-owned file that composes `Base.metadata` from the kernel base + the assembly's own models
  (it already imports both today — the composition is real, only the ownership label is missing). The
  kernel must NOT ship 0004. This is a migration-authority split, tracked as a first-class Task 1/3
  sub-item, not an afterthought.

## Template / static package data + override precedence

- **Package data to ship with the kernel:** `templates/**` (layouts, admin, auth, components, errors)
  and `static/**` — `css/` (compiled `main.css` is a build artifact, gitignored; the Dockerfile
  `css-builder` stage produces it, so the wheel build must run the same step or ship the compiled CSS),
  `js/` (htmx, alpine, components.js, csrf.js — all vendored, no CDN), and `fonts/` (vendored latin
  woff2 + `fonts.css`, added in v0.8.0 for the strict CSP). All must resolve by package path, not CWD
  (leak B1).
- **Template override precedence (target, once packaged):** a consumer assembly must be able to override
  a kernel template. The kernel's Jinja loader should become a `ChoiceLoader` of
  `[assembly_templates, kernel_package_templates]` so an assembly's `templates/admin/foo.html` shadows
  the kernel's — the assembly wins. Not implemented today (single `directory="templates"`); it is a
  Task 3 requirement of `create_app(spec)` (spec may carry an assembly template dir).
- **Static override:** same shape — assembly `/static` mount takes precedence; kernel package static is
  the fallback. `static_asset_url`'s content-hash versioning must read from the resolved package path.
- **Branding precedence (already correct, keep):** built-in `_DEFAULTS` < `brand.json` (repo root,
  `BRAND_CONFIG_PATH`-overridable) < same-named env var < per-tenant DB `ui_branding`. The kernel owns
  the defaults + loader + sanitizer; the assembly owns `brand.json`.

## Kernel runtime dependencies (for Task 1 `pyproject`)

Third-party top-level imports in `app/core/**` (import counts):

- `sqlalchemy` (30), `fastapi` (15) → pulls `starlette` (13), `pydantic` (1) + `pydantic-settings` (1),
  `jinja2` (1), `argon2-cffi` (2).

Therefore the kernel package `install_requires` is: **`fastapi`, `sqlalchemy`, `pydantic`,
`pydantic-settings`, `jinja2`, `argon2-cffi`**. Deliberately **excluded** from the kernel runtime deps:

- **`uvicorn`** — an ASGI server, an assembly/deploy dependency, never imported by `app/core`.
- **`psycopg`** — the DB driver, selected by the `DATABASE_URL` an assembly configures; SQLAlchemy loads
  it by URL, `app/core` never imports it. Assembly/deploy pins it.
- **`python-multipart`** — form parsing pulled in by feature routes (`app/features/*/web.py`), not by
  `app/core`. Assembly dep.

`jinja2` is a kernel dep only because `templating.py` builds the shared env; if a future API-only kernel
split drops the web surface, `jinja2` moves to an extra. Not now — keep it a base dep.

## Re-planned Task briefs (authoritative over the original Task 1–6 assumptions)

**Task 1 — Package split.** Create `packages/dotmac-kernel/` holding today's `app/core/**` (config, db/RLS,
models base + identity models incl. `PlatformAdmin`/`PlatformSession`, security, platform_auth, deps/guards,
web_deps, the five middleware, errors, templating, settings_models + settings_resolver, features registry,
audit, branding, identity, crud, query, logging). **Ship `templates/` + `static/` (incl. `fonts/`) as
package data and resolve them by package path, not CWD (leak B1) — this is the gating fix.** Ship the kernel
**base migrations 0001–0003, 0005–0007** + a kernel `env.py` composition helper; do **not** ship 0004
(assembly). `install_requires` = the six deps above. No behavior change; full suite green; route inventory
diffed empty.

**Task 2 — Public surface + compatibility policy.** Declare `__all__`/`COMPATIBILITY.md` for the group-A
public API above. Establish the **narrow settings-admin surface** (leak B2) distinct from the read/declare
contract. Mark the import-time singletons internal (leak B3). Governance test: the reference assembly may
import only the declared public names (the repo is its own first consumer). 22 modules gain a declared
surface for the first time.

**Task 3 — `ProductAssemblySpec` + `create_app` + the ProvisioningProvider seam.**
`ProductAssemblySpec(name, modules, settings_overrides, branding, providers, web_enabled, disabled_modules,
+ assembly_template_dir, assembly_migrations)`. `create_app(spec)` does everything `main.py` does today,
driven by the spec, and installs the **`ChoiceLoader` template override** (assembly over kernel) + static
override. `main.py` shrinks to build the reference spec (in `app/assembly.py`, listing the seven modules +
`FEATURE_MODULES`) and call `create_app`.
**Ruling C6 (2026-07-30):** additionally publish the **`ProvisioningProvider` protocol** in the alpha —
`dotmac_kernel.providers.provisioning`: the protocol + typed `PlanResult`/`ApplyResult`/`ObserveResult` +
a stable error hierarchy, product-neutral (no cloud/fleet specifics). It is the one provider seam pulled
forward ahead of the general workstream-5 fill because the vendor control plane's slice depends on it;
every other provider seam stays empty.

**Task 4 — Empty-assembly boot proof.** `create_app(ProductAssemblySpec(name="empty", modules=()))` boots:
`/health` 200, platform-auth routes present (kernel surface, always), zero module routes/nav. The **consumer
wheel job** (`pip install` the built wheel into a clean venv, generate a 20-line consumer importing only
public names, boot against an unreachable `DATABASE_URL`, poll `/health` 200) is the milestone acceptance
test — and the real proof that leak B1 (package-data paths) is fixed.

**Task 5 — Fake-provider contract-test kit (`dotmac_kernel.testing`).** Package the harness the repo's
`tests/unit/conftest.py` hand-builds; refactor the repo to consume it. Fakes for seams that EXIST at
milestone 1: `FakeClock`, `FakeSeeder`, in-memory `RateLimitStore`, fake branding loader, **and (ruling C6)
`FakeProvisioningProvider` + its parametrized `dotmac_kernel.testing.contract` provisioning suite** (the
vendor control plane's slice step 6 runs against this fake). No fakes for not-yet-existing seams.

**Task 6 — Docs + version + final review + PR.** COMPATIBILITY.md (public surface incl. the settings-admin
sub-surface + the ProvisioningProvider protocol), kernel CHANGELOG, `0.1.0a1` kernel pre-release version,
final review, PR. Reference-assembly version tracks separately.

## Open items surfaced (not in Task 0 scope; flagged for the owner)

- **CI trigger mismatch** — `pull_request`/`push:main` are declared in `ci.yml` but no such run has ever
  fired (every run is `workflow_dispatch`); likely because the default branch was `phase2a` until
  2026-07-30. Must be resolved before auto-CI is trusted as a kernel-alpha merge gate. (Tracked separately.)
- **Compiled CSS is a gitignored build artifact** — the kernel wheel build must run the `npm run css:build`
  step (as the Dockerfile `css-builder` stage does) or ship the compiled `static/css/main.css`, or an
  installed kernel serves no stylesheet. Decide in Task 1.
