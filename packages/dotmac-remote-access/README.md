# dotmac-remote-access

Tenant remote-access requests, exact Approval binding, finite grants,
provider-neutral activation/revocation intents and immutable execution
observations. Network Control executes device commands; Integrator transports
them. This module stores no credential, endpoint or provider configuration.

## Integration checklist

- Pin `dotmac-remote-access` in the consuming assembly and register `dotmac_remote_access.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_remote_access.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: admission binds exact approval and emits provider neutral intent](../../tests/unit/test_remote_access.py#L48). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_remote_access.py::test_admission_binds_exact_approval_and_emits_provider_neutral_intent -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
