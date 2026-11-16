# dotmac-web-analytics

Privacy-first, first-party website measurement for Dotmac applications.

The package owns append-only observations, property-scoped pseudonymous
visitor/session evidence, filter decisions, deterministic projections,
retention/privacy deletion, rebuild and drift repair. It does not own websites,
forms, campaigns, provider analytics, customer identity, attribution, revenue,
consent policy, connector transport or dashboard presentation.

Every adopter installs its own tenant-plane copy and supplies explicit
property, origin, event-registry, privacy, classifier, sessionisation and
retention policy. There are no adopter hostnames or event vocabularies in the
package.

See `COMPATIBILITY.md`, ADR-0055 and
`docs/inventories/web-analytics-sources.md` in the Starter repository.

## Native computation candidate

The unreleased `0.2.0a1` candidate includes a private Rust `_native` extension behind the existing public Python API. Authorization, tenant isolation, transactions and persistence retain their current owners. Installing a matching wheel needs no compiler. Building from source requires the exact Rust and build-tool pins in the packaged `build.py`, plus a C linker; the source archive includes locked Cargo inputs. No Python computation fallback is installed. Hosted qualification and publication remain separate from this source change.

## Integration checklist

- Pin `dotmac-web-analytics` in the consuming assembly and register `dotmac_web_analytics.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_web_analytics.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: live catalog contract proves every declared tenant table](../../tests/test_web_analytics_isolation.py#L207). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_web_analytics_isolation.py::test_live_catalog_contract_proves_every_declared_tenant_table -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
