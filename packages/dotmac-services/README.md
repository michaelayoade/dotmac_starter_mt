# dotmac-services

Owns the tenant service instance and its lifecycle transitions. Customer,
catalogue and qualification identities are opaque references; fulfillment,
network enforcement, subscriptions, rating and billing remain separate owners.
The tenant-only `sv` lineage owns `mod_services`.
