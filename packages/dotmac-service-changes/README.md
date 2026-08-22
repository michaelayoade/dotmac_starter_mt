# dotmac-service-changes

Owns the **durable customer-initiated change request** — plan change,
relocation, vacation hold and resume — its decision, its evidence, and the
**order** in which the owners it crosses may be reached.

It owns none of those owners. Qualification still decides eligibility, Billing
still raises the fee, Payments still confirms it, Service Orders still delivers
and Service Access still enforces. What lives here is the thing none of them
owns: the request itself, and the record that ties their separate outcomes to
one customer intention.

Two shapes differ deliberately from the Sub original:

- **Checkpoints are rows, not columns.** Sub carried each crossed owner as a
  nullable FK on the request, which cannot say *when* a domain was reached and
  grows a column per new collaborator.
- **Execution advances one declared step at a time.** Sub's execution state was
  written by several handlers with no single guard, so a request could reach
  `fulfillment_released` with no settlement ever recorded.

The tenant-only `sch` lineage owns `mod_servicechanges`.
