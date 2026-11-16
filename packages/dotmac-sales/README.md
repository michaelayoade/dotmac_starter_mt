# dotmac-sales

The sales authority from a qualified Lead through an immutable accepted Quote.
It owns Pipelines, Stages, Leads, append-only origin evidence, Quote authoring,
discount history, Quote lifecycle and exactly-once acceptance.

Acceptance publishes `sales.accepted-quote.v1` with an
`AcceptedQuoteHandoffV1`. The handoff contains a stable subject reference, exact
lines and totals, and a canonical snapshot digest. It never contains or creates
a subscriber, SalesOrder, project, work order, invoice or service. The consuming
product owns every consequence beyond that boundary.

Services mutate and flush a caller-owned session. Acceptance reuses the kernel
idempotency ledger and default transactional outbox adapter. Source authority
and port deltas are in `EXTRACTION.toml`, the sales inventories and ADR-0033.

Status: audit-complete and implementation-candidate. Sub remains authoritative
until backfill, shadow comparison, reconciliation and writer cutover pass.

## Integration checklist

- Pin `dotmac-sales` in the consuming assembly and register `dotmac_sales.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`, `IDEMPOTENCY_LEDGER_V1.name`, `OUTBOX_RELAY_V1.name`.
- Add `dotmac_sales.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: all tables have forced rls and tenant policy](../../tests/test_sales_isolation.py#L209). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/test_sales_isolation.py::test_all_tables_have_forced_rls_and_tenant_policy -q
```

Run this example in the hosted Postgres integration environment (`make test-integration` supplies the database configuration).
