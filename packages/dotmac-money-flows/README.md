# dotmac-money-flows

Tenant-plane composition of money flows over the published owners. The package
holds instance state only: a state, the policy and definition version it was
decided under, an optional deadline, a reason code and opaque references to the
owners' records. It stores no amount, currency or balance, owns no timer, lease,
retry or scanner, and never commits or rolls back.

It declares the manifest, the instance-table allowlist, the `mf` migration
lineage (the Settle instance table) and the event-type constants, and ships the
first flow, F1 Settle: one confirmed payment becomes accepted cash and is applied
to a bound document, or to open documents oldest-due-first for a deposit. The
caller owns the transaction. The assembly supplies two ports (target resolution
and policy) and, around an owner refusal, rolls back and records the refusal in a
new transaction; a uniqueness violation on the confirmation is a replay.

The second flow, F2 FundedCharge, pays a due prepaid charge from account credit
(invoice and credit draw in one transaction) or holds it `pending` with nothing
in the ledger. The contract type comes from Billing's account terms, the charge
and its invoice command from an assembly-supplied `RatedChargeSource`, and the
service access signal (PREPAID_DEPLETED) leaves as a refs-only outbox event.

Only an assembly imports this package. The Starter reference app does not
compose it; the intended consumer is the Sub thin-client deployment.
