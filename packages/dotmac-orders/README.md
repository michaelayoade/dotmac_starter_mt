# dotmac-orders

`dotmac-orders` owns the tenant-scoped customer order, immutable accepted line
snapshots, the readiness decision over a finite set of coverage observations,
and the identity of per-line fulfillment requests.

The module is provider-neutral and imports no business sibling. Assemblies
supply an already allocated order reference, opaque customer/price/terms/
specification references, coverage observations, and delivery acknowledgements.
Cross-owner work leaves transactionally through the kernel outbox.

Submission writes the whole commercial snapshot and freezes it in one caller-
owned transaction. PostgreSQL deferred constraints refuse a committed order
whose header differs from its line totals, an unfrozen snapshot, or a coverage
gate whose counts/state differ from its immutable obligations and receipts.
Acceptance is a separate lifecycle decision over that already-frozen checkout
record. Cancellation after downstream fulfillment acceptance returns a typed
recorded refusal instead of raising away its evidence.

`get_order_snapshot` returns the frozen commercial provenance, lifecycle
actors/instants, finite coverage membership and received resolutions;
`get_order_timeline` exposes the append-only official transition trail.
Fulfillment contracts include their stable identity, publication count and
downstream acceptance evidence. ORM rows are not part of the public surface.

Coverage receipts are observations, not financial decisions. An explicitly
contracted coverage owner must decide the external meaning and supply an
opaque, versioned resolution fact. Orders only deduplicates that fact against
its registered finite set and derives whether its own fulfillment precondition
is complete. It imports no sibling owner and never infers coverage from a
receivable position or allocation arithmetic.

Billing `0.1.0a1` deliberately publishes no coverage contract, and Orders does
not ask it to add one: allocation and coverage stay internal to Billing. That
leaves an explicit adoption boundary. Before Sub cutover, a different named
owner must supply an eligibility-resolution fact that can be translated
mechanically into `RecordCoverageResolutionCommand`, or the financial gate must
be removed from the adopted Orders profile. An assembly may not infer the
decision from a Billing position, allocation, or balance.

The existing `dotmac-sales` candidate is the upstream accepted-Quote owner,
not an Orders implementation. Its V1 handoff deliberately stops before
SalesOrder and still lacks several immutable fields required here; the handoff
must become mechanically translatable before either module is adopted together.

Version `0.1.0a1` is an `audit-complete` package candidate. It is not adopted
until Dotmac Sub switches authority and retires its displaced local writers.
It is deliberately absent from the module release allowlist until that adopter
supplies the shadow-parity and retirement evidence in `EXTRACTION.toml`.
