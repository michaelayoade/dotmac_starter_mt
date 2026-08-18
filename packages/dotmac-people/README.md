# dotmac-people

`dotmac-people` owns a tenant's narrow employment directory: employee
relationships to kernel Party identities, organization catalogues, positions,
temporal assignments, and date-aware reporting resolution.

It does not own identity, credentials, RBAC, payroll, compensation, attendance,
leave, discipline, training, files, notifications, or external provisioning.
See `EXTRACTION.toml` and
`docs/inventories/people-directory-sources.md` for the product-first boundary.

The package is an installable tenant-plane module. Its `pe` migration lineage
owns `mod_people`; services take an explicit `TenantScope`, mutate and flush,
and never commit or roll back.
