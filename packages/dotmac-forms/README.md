# dotmac-forms

`dotmac-forms` owns tenant form definitions, immutable published versions and
validated submission/answer snapshots. It was extracted product-first from
ERP's seven-table Forms engine under ADR-0040.

Products retain subject lifecycle and consequences. Files retains stored bytes;
file answers carry opaque references only. Workflow Runtime may carry a form
version reference through an assembly but neither package imports the other.

## Native validation candidate

The unreleased `0.2.0a1` candidate batches structural answer validation in a private Rust extension. Established Python Unicode/Decimal/date/URL/email parsing and every persistence/lifecycle decision retain their owners. Matching wheels require no compiler; source builds require the exact packaged Rust/build-tool pins and a C linker. Hosted qualification and publication remain separate.

## Integration checklist

- Pin `dotmac-forms` in the consuming assembly and register `dotmac_forms.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_forms.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: forms and workflow are cross tenant isolated](../../tests/test_forms_workflow_isolation.py#L87). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_forms_workflow_isolation.py::test_forms_and_workflow_are_cross_tenant_isolated -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
