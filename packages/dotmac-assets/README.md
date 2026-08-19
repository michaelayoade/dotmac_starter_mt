# dotmac-assets

`dotmac-assets` owns each tenant's individual durable assets: stable identity,
opaque location, custody assignments, maintenance history, guarded lifecycle,
disposal approval/evidence, and an append-only lifecycle trail.

It treats a vehicle, router, laptop, tool, generator, or other durable unit as
an asset without pulling the product-specific meaning into the module. An
adopting product owns employee/department/warehouse/project relations, vehicle
specifications, GPS links, fuel, incidents, reservations, documents, inventory
parts, authorization, approvals policy, and finance/depreciation consequences.
Those owners refer to the local module asset through product-owned relations.

The module is tenant-only. Its five tables live in `mod_assets`, carry a
non-null `tenant_id`, use composite tenant foreign keys, and have enabled and
forced PostgreSQL row-level security. Lifecycle evidence is append-only by
online-role grants and a database rewrite-refusal trigger.

The package is currently `audit-complete`: ERP is the qualifying source and
first candidate, but it does not yet consume a released contract. Package
supply is not an authority cutover and reuse remains unproven.

## Composition

An adopter imports `dotmac_assets.module`, adds
`dotmac_assets.versions_dir()` to Alembic `version_locations`, binds the
module's declared prerequisites, and installs the tenant plane in its own
database. Services lock, mutate, and flush; the adopter's transaction boundary
commits or rolls back.
