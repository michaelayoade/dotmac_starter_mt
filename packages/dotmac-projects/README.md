# dotmac-projects

`dotmac-projects` owns reusable tenant project structure: projects, tasks,
templates, dependency graphs, deterministic schedule constraints, and task
assignment membership.

It does not know what a project is *about*. A consuming product owns subscriber,
customer, buildout, quote, order, work-order, ticket, finance, location, and
provider relations in its own schema and invokes its own services for those
consequences. See ADR-0051 and `docs/inventories/projects-sources.md`.

The module is tenant-only. Every table lives in `mod_projects`, carries a
non-null `tenant_id`, uses composite foreign keys for internal relationships,
and is protected by enabled and forced PostgreSQL row-level security.

The package is currently `audit-complete`: Sub and ERP are named candidates,
but neither consumes a released contract yet. Package supply is not an
authority cutover.

## Composition

An adopter imports `dotmac_projects.module`, adds
`dotmac_projects.versions_dir()` to Alembic `version_locations`, binds the
module's declared prerequisites, and installs the tenant plane in its own
database. Services mutate and flush; the adopter's transaction boundary commits
or rolls back.
