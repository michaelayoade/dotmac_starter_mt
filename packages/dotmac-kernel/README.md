# dotmac-kernel

The DotMac platform kernel: the multi-tenant FastAPI foundation that product
assemblies (the reference `app/` in this repo, `dotmac_sub`, `dotmac_erp`, the
vendor control plane) build on. Import package: `dotmac_kernel`.

It provides configuration, the RLS database/session layer, the identity and
tenancy models (Party family, tenants, roles, auth sessions, platform admins),
security primitives, platform-admin auth, the middleware stack (security
headers, tenant resolver, rate limiting, CSRF, observability), errors,
templating, the settings resolver, the feature-manifest registry, and the
audit write side.

Boundary: the kernel never imports a product assembly (`app`, feature packages)
— assemblies consume the kernel, not the reverse. Enforced by the repo's
import-linter contracts.

Status: `0.x` pre-release (the current version is `pyproject.toml`'s
`[tool.poetry] version`; per-release notes in `CHANGELOG.md`), extracted from
`dotmac_starter_mt` app/core in the kernel-boundary program. The public surface
(`__all__` / `COMPATIBILITY.md`), `ProductAssemblySpec`/`create_app`, and the
`dotmac_kernel.testing` contract-test kit shipped in Tasks 2–6.

## Integration checklist

- Pin `dotmac-kernel` in the consumer and import the public contracts from `dotmac_kernel`.

### Executable reference

[Example: upgrade preserves rows security and historical unknowns](../../tests/test_kernel_audit_history_migration.py#L120). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_kernel_audit_history_migration.py::test_upgrade_preserves_rows_security_and_historical_unknowns -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
