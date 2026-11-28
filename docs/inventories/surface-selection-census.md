# Surface-selection census (kernel-consuming assemblies)

Dated 2026-11-27. Facts read from local checkouts of each repository at the
commit named; not a mandate. Context: `ProductAssemblySpec.surface_selection=None`
is a development convenience only and becomes a **fatal startup error in
production** (this change). Every consumer therefore adds an explicit selection in
the PR that bumps its kernel pin. Until that bump its own `main` is unaffected,
because each repository pins an older kernel that has no such field.

| Assembly | Where | Read at | Pinned kernel | `surface_selection` today | Action in the pin-bump PR |
|---|---|---|---|---|---|
| Reference app (this repo) | `app/assembly.py` | this branch | workspace | `SELECT_ALL`, explicit | none |
| `consumer-boot` fixtures (2 specs) | `scripts/consumer_boot_check.sh` | this branch | workspace wheel | `SELECT_ALL`, explicit (this change) | none |
| Floor probe | `scripts/floor/probe.py` | this branch | workspace | builds a spec, never `create_app` | none |
| Runtime-seam probe (2 specs) | `scripts/check_kernel_runtime_composition_seam.py` | this branch | workspace | none; `environment="development"` so a warning only | none required; add `SELECT_ALL` if it ever runs as production |
| ERP v2 | `src/erp_v2/assembly.py:111` | `main` `0c9dacf` (PR #88 pending) | `0.1.0a107` | none on `main`; #88 adds literal `api_routers` and 17 `(code, "legacy")` pairs | merge #88 with the pin bump |
| Sub-thin | `src/sub_thin/assembly.py:47` | `main` `7a6abed` | `0.1.0a107` | none | add an explicit selection (its packaged modules declare no `api_routers`; confirm per module at the bump) |
| Sub (full) | `app/composition.py:116` | `main` `c7707880` | `0.1.0a97` | none | add an explicit selection at its next kernel bump; confirm whether it runs production |
| Academy | `app/assembly.py:118` | `main` `fc8d15b` | `0.1.0a38` | none | add an explicit selection at its next kernel bump |
| Vendor control plane | `src/vendor_cp/assembly.py:189` | worktree `03f580e` of `dotmac_platform_control_plane` (not a main checkout) | `0.1.0a100` | none | add an explicit selection at its next kernel bump; main not read |

Notes:

- "none" means no `surface_selection` keyword in the file at that commit. It was
  found with `git grep "surface_selection"`, which returned nothing for those
  repositories.
- Pins are the `dotmac-kernel` line in each `pyproject.toml`, not what is
  deployed.
- Test-only specs in this repository run under the default `dev` environment and
  are unaffected.
- A selection is one line. `SELECT_ALL` states "everything the installed modules
  declare" and is behaviour-identical to today; a product that wants less lists
  `(module, surface)` pairs and the module codes whose `api_routers` it serves,
  and commits a route-inventory snapshot test
  (`dotmac_kernel.testing.route_inventory`).

## `platform_api_modules` at the pin bump

`/platform` is the operator plane. A module's `api_routers` may enter it only
when the assembly lists the module in `ProductAssemblySpec.platform_api_modules`
(a keyword-only field), every such route carries the kernel's own
`dotmac_kernel.platform_auth.require_platform_admin` (matched by identity), and
the path starts with a static first segment the kernel's platform routes do not
use. A consumer-local platform guard refuses the boot (loud, not silent). A
module's contract-1 `web_routers` may not enter `/platform` at all.

| Assembly | Check at the pin bump |
|---|---|
| Reference app (this repo) | lists `{"tenants"}` (done) |
| Vendor control plane | `[I]` unread for `/platform` module APIs: if any, list the module and guard with `require_platform_admin` |
| Sub (full) | `[I]` unread: same check |
| ERP v2 | `[I]` unread: same check; its legacy surfaces must not sit under `/platform` |
| Sub-thin, Academy | `[I]` unread: same check |

