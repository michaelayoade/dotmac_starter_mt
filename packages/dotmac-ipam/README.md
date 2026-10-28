# dotmac-ipam

Tenant-only owner of IP pools, blocks, addresses, assignments, delegated and
routed prefixes, utilization facts, collision prevention and repair evidence,
extracted from Sub's proven IPAM code (0.2.0a1).

Product identity is deliberately opaque. The module has no Subscriber,
Subscription, Service, NAS, OLT, VLAN, provider-client, or credential foreign
key; `site_ref` and `device_ref` are unvalidated opaque ids. Session
occupancy, subscription lifecycle truth and policy windows arrive as typed
arguments (`OccupiedAddress`, `RepairFacts`, `Cooldown`). Commands take
`(db, *, tenant_id, command)`, are flush-only (the caller owns the
transaction), and run inside a command scope: a `before_flush` guard refuses
any write to a module-mapped row made outside one.

`dotmac_ipam.read_models` re-exports the mapped classes under Sub's names for
same-database READERS only (an explicit widening of "snapshots, never ORM
instances"); the public command/query surface still returns immutable
snapshots.

Status: `0.2.0a1` extraction re-cut, declared and unpublished. `0.1.0a1` must
not be installed. Sub remains the qualifying source and first cutover (ADR-0038
wave 1); no product has adopted this package and no authority has moved.
