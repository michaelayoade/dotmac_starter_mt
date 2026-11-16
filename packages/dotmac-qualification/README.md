# dotmac-qualification

Owns time-bounded eligibility evidence and the resulting service-qualification
decision. Positioning and network systems supply observations only; this module
does not own coordinates, topology, reachability, provisioning, or service
lifecycle. The tenant-only `qu` lineage owns `mod_qual`.

## Warrant verification

A product that promotes a pin, address or any other consequence on the strength
of a qualification asks `verify_warrant(db, scope=..., decision_id=...,
subject_reference=..., at=...)`. It returns the decision facts only if the
decision is `ELIGIBLE`, unexpired at the caller's instant and about exactly that
subject reference; otherwise it raises `WarrantRefused` with a closed reason.
The read carries no rationale or evidence fact, so nothing about location is
exposed. The module still stores no coordinates or address.

## Integration checklist

- Pin `dotmac-qualification` in the consuming assembly and register `dotmac_qualification.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_qualification.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: time bounded evidence supports one owned decision](../../tests/unit/test_qualification_service.py#L60). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_qualification_service.py::test_time_bounded_evidence_supports_one_owned_decision -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
