# dotmac-fiber-plant

Tenant-only owner of outside-plant structures, cables, strands, splices,
terminations, continuity, field evidence, and controlled as-built changes. It
does not own projects, work orders, tickets, customers, generic device
inventory, provider GIS clients, or notification transport. Status:
audit-complete `0.1.0a1` candidate; no adopter yet.

## Native computation candidate

The unreleased `0.2.0a1` candidate includes a private Rust `_native` extension behind the existing public Python API. Authorization, tenant isolation, transactions and persistence retain their current owners. Installing a matching wheel needs no compiler. Building from source requires the exact Rust and build-tool pins in the packaged `build.py`, plus a C linker; the source archive includes locked Cargo inputs. No Python computation fallback is installed. Hosted qualification and publication remain separate from this source change.

## Integration checklist

- Pin `dotmac-fiber-plant` in the consuming assembly and register `dotmac_fiber_plant.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_fiber_plant.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: fiber continuity uses canonical splice identity](../../tests/unit/test_network_suite_services.py#L380). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_network_suite_services.py::test_fiber_continuity_uses_canonical_splice_identity -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
