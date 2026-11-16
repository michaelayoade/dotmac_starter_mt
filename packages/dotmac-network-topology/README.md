# dotmac-network-topology

Tenant-only owner of declared and observed links, forwarding decisions,
resolved paths, reachability, coverage gaps, and rebuildable topology
projections. Inputs are typed opaque identity/observation references. It owns
neither device inventory, fiber physical state, incidents, nor commands.
Status: audit-complete `0.1.0a1` candidate; no adopter yet.

## Native computation candidate

The unreleased `0.2.0a1` candidate includes a private Rust `_native` extension behind the existing public Python API. Authorization, tenant isolation, transactions and persistence retain their current owners. Installing a matching wheel needs no compiler. Building from source requires the exact Rust and build-tool pins in the packaged `build.py`, plus a C linker; the source archive includes locked Cargo inputs. No Python computation fallback is installed. Hosted qualification and publication remain separate from this source change.

## Integration checklist

- Pin `dotmac-network-topology` in the consuming assembly and register `dotmac_network_topology.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_network_topology.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: topology selects minimum cost and rebuilds unreachable projection](../../tests/unit/test_network_suite_services.py#L194). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_network_suite_services.py::test_topology_selects_minimum_cost_and_rebuilds_unreachable_projection -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
