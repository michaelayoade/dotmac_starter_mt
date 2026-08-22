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
