# dotmac-support-access

`dotmac-support-access` owns temporary support-access requests, exact approval
binding, consent or break-glass admission, finite grant descriptors, immediate
revocation, expiry and append-only workflow evidence.

It stores no token, credential, endpoint or standing application role. The
kernel/adopting product enforces the descriptor; Approvals remains the decision
owner and is referenced only by immutable evidence.

## Integration checklist

- Pin `dotmac-support-access` in the consuming assembly and register `dotmac_support_access.module` there.
- Bind the prerequisites declared by the manifest: `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_support_access.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: support access platform catalog and privileges](../../tests/test_support_access_platform_isolation.py#L90). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_support_access_platform_isolation.py::test_support_access_platform_catalog_and_privileges -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
