# dotmac-collections

Tenant-scoped owner of delinquency policy, collection cases, arrangements,
grace, consequence requests and their immutable receipts. It rereads a typed
receivables owner before every decision and asks product owners to apply
consequences. It owns neither receivable money nor product state, delivery,
timers, provider transport, or accounting.

The first release is tenant-only. `dotmac_sub` is the candidate first adopter;
no production authority switch is part of this package change.
