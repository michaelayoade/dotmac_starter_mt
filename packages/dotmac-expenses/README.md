# dotmac-expenses

`dotmac-expenses` owns tenant expense requests, incurred-expense claims,
category and receipt meaning, effective-dated limit evaluation, guarded
submission/decision transitions, and derived reimbursement eligibility.

It does not own Party/employee identity, approval quorum, stored bytes,
numbering, work orders/projects, advances/cards, AP/GL/tax, bank details,
payment initiation, settlement or payment coverage. Receipt rows reference an
opaque file UUID. Approval transitions carry an opaque decision reference.
An eligible claim is ready to be presented to Finance; it is not evidence of
payment.

The package is an installable tenant-plane module. Its `ex` lineage owns
`mod_expenses`. Services require an explicit `TenantScope`, mutate and flush,
and never commit or roll back. Draft revisions replace the complete request or
claim line snapshot through that same service owner; submitted lines cannot be
edited. See `EXTRACTION.toml`, ADR-0047 and
`docs/inventories/expenses-sources.md` for the product-first boundary.

## Integration checklist

- Pin `dotmac-expenses` in the consuming assembly and register `dotmac_expenses.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`, `PARTY_PERSON_CATALOG_V1.name`.
- Add `dotmac_expenses.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: live catalog proves forced rls and composite contract](../../tests/test_expenses_isolation.py#L110). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_expenses_isolation.py::test_live_catalog_proves_forced_rls_and_composite_contract -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
