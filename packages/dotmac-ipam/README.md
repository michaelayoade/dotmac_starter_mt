# dotmac-ipam

Tenant-only owner of address spaces, pools, addresses, reservations,
assignments, utilization facts, collision prevention, and repair evidence.

Product identity is deliberately opaque. The module has no Subscriber,
Subscription, Service, NAS, OLT, VLAN, provider-client, or credential foreign
key. Public services accept immutable commands/queries and return immutable
snapshots/results rather than ORM instances.

Status: `0.1.0a1` audit-complete candidate for the `network-suite-v1` cohort.
Sub remains the qualifying source and first cutover; no product has adopted it.
