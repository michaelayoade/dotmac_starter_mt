# dotmac-money-flows

Tenant-plane composition of money flows over the published owners. The package
holds instance state only: a state, the policy and definition version it was
decided under, an optional deadline, a reason code and opaque references to the
owners' records. It stores no amount, currency or balance, owns no timer, lease,
retry or scanner, and never commits or rolls back.

This release skeleton declares the manifest, the instance-table allowlist, the
`mf` migration lineage (the Settle instance table) and the event-type constants.
Flow behaviour arrives separately.

Only an assembly imports this package. The Starter reference app does not
compose it; the intended consumer is the Sub thin-client deployment.
