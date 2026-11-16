# dotmac-service-changes

Owns the **durable customer-initiated change request** — plan change,
relocation, vacation hold and resume — its decision, its evidence, and the
**order** in which the owners it crosses may be reached.

It owns none of those owners. Qualification still decides eligibility, Billing
still raises the fee, Payments still confirms it, Service Orders still delivers
and Service Access still enforces. What lives here is the thing none of them
owns: the request itself, and the record that ties their separate outcomes to
one customer intention.

Two shapes differ deliberately from the Sub original:

- **Checkpoints are rows, not columns.** Sub carried each crossed owner as a
  nullable FK on the request, which cannot say *when* a domain was reached and
  grows a column per new collaborator.
- **Execution advances one declared step at a time.** Sub's execution state was
  written by several handlers with no single guard, so a request could reach
  `fulfillment_released` with no settlement ever recorded.

The tenant-only `sch` lineage owns `mod_servicechanges`.

## Integration checklist

- Pin `dotmac-service-changes` in the consuming assembly and register `dotmac_service_changes.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_service_changes.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: opening is idempotent on the confirmation key](../../tests/unit/test_service_changes_service.py#L92). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_service_changes_service.py::test_opening_is_idempotent_on_the_confirmation_key -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
