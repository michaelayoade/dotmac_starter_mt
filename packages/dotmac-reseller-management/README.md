# dotmac-reseller-management

Tenant-only reseller account identity, hierarchy, delegated commercial
authority, collaborator bindings and lifecycle. The module is product-first
from Sub's reseller account/user boundary.

The owner stores opaque Party-role, member and customer-account references. It
does not own Party or Customer rows, authentication, commissions, payouts,
invoices, catalog decisions, commercial agreements, or entitlements. Lifecycle
facts leave through the kernel outbox; delivery remains another owner's job.

The stateful lineage is `rm`, the schema is `mod_reseller`, and all four tables
carry non-null tenant identity, composite tenant foreign keys and forced RLS.
