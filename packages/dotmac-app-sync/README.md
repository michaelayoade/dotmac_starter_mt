# dotmac-app-sync

Provider-neutral contracts for synchronizing facts between independently
deployed Dotmac applications. A source writes its local durable outbox, its
transport authenticates to the destination, and the destination validates one
versioned envelope before delegating atomic deduplication and local resolution
to its own receiver.

The package owns no HTTP client, route, database, session, authorization,
provider integration, domain state or consequence. Products declare their own
capabilities and payload schemas, authenticate peers in their own adapters, and
implement `SyncReceiver` inside their own transaction boundary.

The initial contract proof covers Sub → ERP billing observations, ERP → Sub
ledger observations, ERP → Academy eligibility observations, and Academy → ERP
completion observations. These are reusable flow shapes, not pairwise runtime
branches.

`0.1.0a1` is declared but intentionally not release-allowlisted. A real product
pilot must exact-pin the source build and prove one destination-owned receiver
before the stateless-adapter release lane is opened.
