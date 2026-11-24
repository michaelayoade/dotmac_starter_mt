# Architecture overview

- Starter provides shared building blocks for business applications.
- A product chooses those blocks and adds its own business behavior.
- This page introduces the terms. [ARCHITECTURE.md](../ARCHITECTURE.md) remains
  the authority for exact behavior, ownership and implementation status.

## The building blocks

- **Kernel (`dotmac-kernel`):** shared infrastructure, including session boundaries,
  authentication support, settings and module contracts.
- **Module:** an optional package with a declared purpose, public contract and owner.
  Stateful modules also own their database namespace and migrations.
- **Assembly:** the application's selection of modules, policies, branding and adapters.
  The reference selection lives in [app/assembly.py](../../app/assembly.py).
- **Feature:** an application-owned package, such as parties or settings.
  Its manifest declares its routes and other contributions.
- **Tenant:** a customer workspace. A single-tenant deployment still uses tenant context.
- **Platform:** the control plane used to manage tenants; its administrators are
  separate from tenant users.

## How a request reaches the database

- Middleware applies request controls and resolves the tenant from the host.
- A route checks access, validates input and calls a service.
- The service applies business rules and changes data through its provided session.
- The session boundary owns commit and rollback.
- PostgreSQL policies enforce tenant isolation in addition to application checks.
- The browser portal and JSON API delegate to the same business services.
- For exact middleware order and exceptions, read the
  [request-flow reference](../ARCHITECTURE.md#request-flow--middleware-order).

## Three database roles

- `app_user`: tenant requests; Row-Level Security applies.
- `platform_api`: online platform operations with explicit grants.
- `app_admin`: migrations and offline maintenance; never ordinary request traffic.

## Shared packages do not mean a shared product database

- Each product owns its runtime, database, authorization and business decisions.
- Locally installed modules use that product's database and migration composition.
- Independently deployed applications exchange data through versioned APIs and events.
- Installing a package is not proof that a product has adopted its behavior.

## What runs in the reference app?

- Application features include tenants, auth, parties, RBAC, settings, presentation,
  custom fields, licensing and web.
- The assembly also selects UI, Template Studio and Ticketing packages.
- The [module catalog](../MODULE_CATALOG.md) includes other packages and their status;
  it is not a list of features enabled in every deployment.

## Next step

- [Learn what each module does](../modules/index.md).

- [Create your first tenant](../tutorials/first-tenant.md).
- [Explore the detailed architecture](../ARCHITECTURE.md).
