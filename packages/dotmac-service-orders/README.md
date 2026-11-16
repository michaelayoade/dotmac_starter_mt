# dotmac-service-orders

Owns the **service-delivery order** — the thing that stands between a sold
commercial order and a service a customer can actually use — and the
**activation-readiness decision** made about it.

It owns one decision: *may this order activate now, and on the strength of
which facts?* It does not own the commercial order (`dotmac-orders`), the saga
that drives delivery (`dotmac-fulfillment`), the field work (`dotmac-work-orders`),
or the realized service and its lifecycle (`dotmac-services`).

Readiness is decided from **normalized observations the caller supplies**, never
from reaching into another owner's tables. The caller turns whatever its own
owners report into typed `ReadinessCheck` values; this module decides from them
and keeps the decision and its evidence append-only.

The tenant-only `so` lineage owns `mod_serviceorders`.

## Service location

Install, reconnect and change orders are opened through
`open_service_order_v2`, which requires an opaque `service_location_ref`. The
module holds the token and nothing it resolves to: no address text, no
coordinates, no qualification evidence. The composing product keeps those, and
verifies a qualification warrant before it promotes a location. V1
(`open_service_order`) is unchanged for every other order type.

## Integration checklist

- Pin `dotmac-service-orders` in the consuming assembly and register `dotmac_service_orders.module` there.
- Bind the prerequisites declared by the manifest: `TENANT_SCOPE_CATALOG_V1.name`, `MODULE_DATABASE_ROLES_V1.name`.
- Add `dotmac_service_orders.versions_dir()` to the assembly’s Alembic version locations and apply the declared lineage on its configured plane before calling persistence APIs.
- Resolve the declared Starter package dependencies with the assembly pins: `dotmac-kernel`.

### Executable reference

[Example: opening is idempotent on the request key](../../tests/unit/test_service_orders_service.py#L88). This uses the repository’s fixtures and assertions.

```sh
poetry run pytest tests/unit/test_service_orders_service.py::test_opening_is_idempotent_on_the_request_key -q
```

This example is covered by the hosted unit and architecture suite (`make test-unit`).
