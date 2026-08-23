# dotmac-orders

`dotmac-orders` owns the tenant-scoped customer order, immutable accepted line
snapshots, the reasoned fulfillment-eligibility decision over a finite accepted
set and explicitly addressed owner observations, and the identity of per-line
fulfillment requests.

The module is provider-neutral and imports no business sibling. Assemblies
supply an already allocated order reference, opaque customer/price/terms/
specification references, eligibility observations, and delivery acknowledgements.
Cross-owner work leaves transactionally through the kernel outbox.

Submission writes the whole commercial snapshot and freezes it in one caller-
owned transaction. PostgreSQL deferred constraints refuse a committed order
whose header differs from its line totals, an unfrozen snapshot, or a coverage
gate whose counts/state differ from its immutable obligations and receipts.
Acceptance is a separate lifecycle decision over that already-frozen checkout
record. Cancellation after downstream fulfillment acceptance returns a typed
recorded refusal instead of raising away its evidence.

`get_order_snapshot` returns the frozen commercial provenance, lifecycle
actors/instants, finite eligibility membership and received resolutions;
`get_order_timeline` exposes the append-only official transition trail.
Fulfillment contracts include their stable identity, publication count and
downstream acceptance evidence. ORM rows are not part of the public surface.

Coverage receipts are observations, not financial decisions. Sales freezes the
finite fulfillment-eligibility requirement membership with the accepted Quote;
the owner of each external fact supplies an opaque, versioned resolution
addressed to one registered requirement. Orders alone evaluates the set and
returns a reasoned `FulfillmentEligibilityDecisionV1`. It imports no sibling
owner and never infers eligibility from a receivable position, allocation
arithmetic or a balance.

Billing `0.1.0a1` deliberately publishes no coverage contract, and Orders does
not ask it to add one: allocation and coverage stay internal to Billing. An
accepted settlement, an accountable order waiver, and deliberately extended
credit remain distinct owner facts. An adopter may translate an explicitly
addressed fact into `RecordCoverageResolutionCommand`; it may not calculate a
resolution from Billing positions. Sub's existing finite funding receipts are
the first cutover source. A greenfield product must bind equivalent explicit
evidence producers before enabling its financial requirements.

The existing `dotmac-sales` candidate is the upstream accepted-Quote owner,
not an Orders implementation. Its V1 handoff deliberately stops before
SalesOrder and now carries the explicit minor-unit contract, accepted terms,
price/specification provenance, component taxes and finite eligibility
membership required for a product assembly to build `SubmitOrderCommand`
without a live catalogue read or a new commercial decision.

Version `0.1.0a1` is an `audit-complete` package candidate. It is not adopted
until Dotmac Sub switches authority and retires its displaced local writers.
It is deliberately absent from the module release allowlist until that adopter
supplies the shadow-parity and retirement evidence in `EXTRACTION.toml`.
