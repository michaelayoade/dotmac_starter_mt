# dotmac-customers

`dotmac-customers` owns tenant customer account identity, the narrow customer
profile, and typed references to Party identities owned elsewhere.

It does not own authentication, contact reachability, addresses, qualification,
offers, prices, subscriptions, service lifecycle, billing, or network state.
The `cu` lineage owns `mod_customers`; every service requires `TenantScope` and
flushes inside the caller's transaction.
