# dotmac-kernel

The DotMac platform kernel: the multi-tenant FastAPI foundation that product
assemblies (the reference `app/` in this repo, `dotmac_sub`, `dotmac_erp`, the
vendor control plane) build on. Import package: `dotmac_kernel`.

It provides configuration, the RLS database/session layer, the identity and
tenancy models (Party family, tenants, roles, auth sessions, platform admins),
security primitives, platform-admin auth, the middleware stack (security
headers, tenant resolver, rate limiting, CSRF, observability), errors,
templating, the settings resolver, the feature-manifest registry, and the
audit write side. It also supplies exact money/FX values and ADR-0016's
portable monetary-coverage arithmetic, query expression, and generated-column
mixin; products continue to own their settings rows and document lifecycles.

Boundary: the kernel never imports a product assembly (`app`, feature packages)
— assemblies consume the kernel, not the reverse. Enforced by the repo's
import-linter contracts.

Status: `0.x` pre-release (the current version is `pyproject.toml`'s
`[tool.poetry] version`; per-release notes in `CHANGELOG.md`), extracted from
`dotmac_starter_mt` app/core in the kernel-boundary program. The public surface
(`__all__` / `COMPATIBILITY.md`), `ProductAssemblySpec`/`create_app`, and the
`dotmac_kernel.testing` contract-test kit shipped in Tasks 2–6.
