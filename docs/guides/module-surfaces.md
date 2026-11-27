# Module surfaces: the author contract

- **Goal:** ship a module's API, pages, permissions, settings and background jobs so any assembly can serve them without module-side changes.
- **Contract:** kernel module contract 2 (`ModuleManifest`), extended by `jobs` and the assembly-side `surface_selection`.
- **Authority:** [ADR-0006](../adr/0006-white-label-product-foundation.md) (2026-08-25 amendment), [ADR-0008](../adr/0008-manifest-declaration-registries.md). This guide is the author-facing table; the decision lives there.

## One way for every module

| Surface element | How a module ships it | Who consumes it |
|---|---|---|
| JSON/machine API | `api_routers` on the manifest. Every route has `require_permission` (staff) or exact-scope machine auth (service callers). | kernel `create_app`, only when the assembly selects the module's API routers |
| Server-rendered pages | `web_surfaces=(WebSurfaceContribution(code=..., facet='staff_admin', routers=(relative router,), navigation=(WebNavItem(route_name=...),), templates=TemplatePackage(namespace='<module>'), supported_ui_contract_versions={1}, browser_capabilities=(htmx 2,)),)`. Other audiences use their own facet codes. | `WebSurfaceRegistry`; the assembly declares the facets it serves and selects the surface |
| Permissions | `PermissionSpec` on the manifest, stamped on every route. Navigation visibility derives from the same stamp. Hiding is never authorization. | kernel boot validation; the access-admin role editor |
| Navigation | `WebNavItem` (region, group, order, **named route**). No paths and no prefixes. | the facet shell |
| Settings pages | `setting_domains` plus `SettingSpec`s. The settings-admin package renders every declared domain generically. A custom screen is your own surface in nav region `settings`. | settings-admin |
| Background jobs | `jobs=(JobSpec(...),)`, see below. | the job runner in `dotmac-durable-timers` |
| Document templates, imports, reports | Not available yet. Each is added to the manifest in the same change as its first consumer (ADR-0008). | document-rendering, imports, reporting |
| Assembly selection | `ProductAssemblySpec.surface_selection`, see below. The module has no say. | kernel `create_app` |

## Rules

1. A module never names a prefix, host, brand or another module's facet. Routes are facet-relative. Navigation uses route names.
2. Modules never import each other. Cross-module flows use typed contracts, outbox events, or structural `Protocol` ports bound by the assembly's `providers` mapping.
3. **Selection is the safety control, at module and surface granularity.** A new module, or a new surface, adds nothing to an assembly that has set a selection; the assembly opts in. A new route inside a module or surface the assembly already selected DOES mount, so every assembly commits a route-inventory snapshot test (below). Named router sets are a later contract-generation item and a known limitation.
4. A non-web channel (e-mail, PDF, mobile) gets its own contract, never a facet.
5. Contract 1 (`web_routers`, `nav`) is frozen. No new contract-1 surfaces. A contract-1 manifest still loads and cannot declare `jobs` or `web_surfaces`.

## Declaring jobs

```python
from dotmac_kernel import JobSpec

jobs = (
    JobSpec(
        code="invoices.sweep",
        handler="my_module.jobs:sweep",   # import path, resolved at boot
        plane="tenant",                   # required: "tenant" or "platform"
        interval_seconds=900,             # exactly one of interval_seconds / cron
        # cron="*/15 2-4 * * 1-5",        # five fields, evaluated in the tenant timezone
        max_attempts=5,
        backoff_seconds=(30, 120, 600),   # the last value repeats
    ),
)
```

- The handler is `handler(session, scope, run_context) -> JobOutcome`. **It must be idempotent**: delivery is at-least-once and a lease can expire mid-run.
- `ModuleRegistry` refuses, at boot: a duplicate job code in one module, a malformed cadence, an unknown plane, and a handler that does not import or does not accept three positional arguments.
- `concurrency` is `"single"`, the only mode: one run of a job at a time per scope.
- The plane must be real: a `platform` job needs declared `platform_tables`, a `tenant` job needs tenant tables (or a stateless module). `ModuleRegistry.jobs(module_planes=...)` returns only jobs whose plane the assembly installs.
- The handler must be a plain synchronous function (no coroutines, classes or builtins) defined in your own package, never the kernel, the standard library or another module.
- Jobs require module contract 2. The runner is a separate process and is not part of the kernel.

## Selecting surfaces (assemblies)

```python
from dotmac_kernel import ProductAssemblySpec, SELECT_ALL, SurfaceSelection

ProductAssemblySpec(
    ...,
    surface_selection=SurfaceSelection(
        surfaces={("template_studio", "staff")},   # (module code, surface code)
        api_routers={"template_studio"},           # modules whose api_routers are served
    ),
)
# or, explicitly: surface_selection=SELECT_ALL
```

- With a selection set the assembly is **fail-closed**: anything not named is not mounted. A surface that targets a facet the assembly does not declare stays inert until selected.
- An entry naming an absent module, surface, or a module with no API routers refuses the boot.
- `None` (the default) is the compatibility mode: every enabled module's API routers and surfaces are mounted, and `create_app` logs a warning. Declare a selection. The default will become a refusal in a later contract generation.
- Contract-1 `web_routers` mount only when `(module, "legacy")` is selected, valid only for a module that has `web_routers`.
- `None` becomes a fatal startup error in production before the Wave 0 kernel release is tagged. Add the selection in the PR that bumps your kernel pin.

### Commit a route-inventory snapshot

```python
from dotmac_kernel.testing.route_inventory import route_inventory

def test_mounted_routes_match_the_committed_snapshot() -> None:
    assert [list(r) for r in route_inventory(app)] == json.loads(SNAPSHOT.read_text())
```

A pin bump that adds, removes or renames a mounted route then fails in the pin PR.

### Composition checks `create_app` makes

`create_app` refuses two routes with the same method and path shape, a composed browser surface route under `/health`, `/docs`, `/redoc`, `/openapi.json`, `/static` or `/api` (and `/platform` when enabled), and a v2 route with an empty path. On a `/` facet the facet prefix no longer shields module routes from these.

## Prove it with the conformance kit

```python
from dotmac_kernel.testing.surface_conformance import assert_surface_conformance
from my_module import module

def test_my_surfaces_conform() -> None:
    assert_surface_conformance(module)
```

The kit boots the module under a `/admin` facet and a `/` facet and checks:

- it boots under both, with identical relative routes;
- every web route and every `api_routers` route carries the kernel permission dependency (matched by function identity, not by name; machine-scope for API routes). Name genuinely public entry routes (login, reset) with `public_routes=(WebRouteRef(...),)`; they must be declared facet `entry_routes`;
- every mutation carries CSRF protection;
- no template hard-codes an absolute URL in `href`, `action`, `formaction`, `src`, `srcset`, `poster` or `hx-*` (including `//host`; `/static/` is allowed). The kit reads template files only: an unqualified `TemplateResponse("x.html")` or a path string in Python is not scanned;
- every `WebNavItem` names a route the surface owns;
- template namespace isolation: every package template resolves only through `<namespace>/` and does not reference its own files un-namespaced.

It never reads a database or issues a request. Pass `extra_modules=` for dependencies `capability_provisions=` when a surface needs browser capabilities with security requirements, or `spec_for_prefix=` to run every check inside a whole product-shaped assembly (its module set, authentication profile and admission permission).

## New stateful modules

A module that owns tables needs a row in `dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` (schema `mod_<short_code>`, migration prefix, branch label) merged **before** its source (hard rule 14, `make allocation-gate`). A stateless module declares neither.
