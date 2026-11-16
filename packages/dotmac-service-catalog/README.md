# dotmac-service-catalog

Owns stable technical service specifications and plan families, their
effective-dated published versions, typed characteristic values, and declared
eligibility inputs. One family can group many specifications, so a speed or
access variant does not require another commercial offer. It owns no offer,
price, discount, contract, subscription, billing cycle, tax, or fixed
recurrence.

The tenant-only `sc` lineage owns `mod_svc_cat`; services require an explicit
`TenantScope` and mutate/flush inside the caller's transaction.

An adopter publishes values such as `DOWNLOAD_MBPS`, `UPLOAD_MBPS`,
`ACCESS_TYPE`, and `AGGREGATION` on a `ServiceSpecificationVersion`. Those
codes remain product-declared technical semantics; the package only validates
their declared primitive type and preserves the applied version and source
evidence.

## Integration checklist

- Pin `dotmac-service-catalog` in the consuming assembly and register `dotmac_service_catalog.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_service_catalog.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: spec family characteristics and eligibility are one technical contract](../../tests/unit/test_service_catalog_service.py#L65). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_service_catalog_service.py::test_spec_family_characteristics_and_eligibility_are_one_technical_contract -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
