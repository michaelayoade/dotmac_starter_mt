# Dotmac Starter MT

A Python/FastAPI foundation for building business applications with separate
customer workspaces, shared services and an optional admin portal.

- **Tenant** means a customer workspace with its own users and data.
- PostgreSQL Row-Level Security helps keep tenant data separate.
- The reference app includes authentication, people and organizations, roles,
  settings, branding and custom fields.
- It also composes Template Studio and Ticketing. Other packages are optional;
  being in this repository does not mean they are active in the reference app.

## Start here

- **Try it:** [Run the app and create your first tenant](docs/tutorials/first-tenant.md).
- **Explore the modules:** [What each module does](docs/modules/index.md).
- **Understand it:** [Architecture in plain language](docs/concepts/overview.md).
- **Build a product:** [Features and branding](docs/guides/build-a-product.md).
- **Deploy it:** [Production deployment](docs/guides/deploy.md).
- **Find a topic:** [Documentation index](docs/index.md).

## What to expect

- This is a developer foundation, not a finished ERP or a complete SaaS business.
- Setup needs Python, Poetry, Rust, Node.js and Docker; prerequisites are in the tutorial.
- Tests run on Git-hosted CI. See [Testing and checks](docs/guides/testing.md).
- Current behavior and planned work are distinct. See the
  [product vision](docs/PRODUCT_VISION.md) for the intended direction.
- Existing ERP, CRM and subscriber-management products are being moved toward
  shared packages and thin product assemblies. A package release alone does
  not establish that a product has adopted it.

## Documentation quality

Feature guides have a reusable [template](docs/templates/feature.md).
CI checks local links in changed Markdown and executes selected documented
examples against the real package APIs. See
[documentation maintenance](docs/guides/documentation.md) for scope, limits and
the hosted-only validation policy.

## Reference and contribution

- [Detailed project reference](docs/reference/project-guide.md): setup details,
  route map, database roles, feature switches and deployment notes.
- [Module catalog](docs/MODULE_CATALOG.md): generated package inventory and evidence state.
- [Architecture reference](docs/ARCHITECTURE.md): authoritative implementation details.
- [Configuration](docs/reference/configuration.md): where settings come from.
- [Contributing](CONTRIBUTING.md): development and review rules.
- [Security](docs/SECURITY.md): controls and known limitations.
- [Agent instructions](AGENTS.md): canonical repository rules.

## Quickstart (dev)

- Follow [Your first tenant](docs/tutorials/first-tenant.md) for setup and verification.

## Disabling a feature

- Read [Features and branding](docs/guides/build-a-product.md) before changing startup switches.
- The [detailed reference](docs/reference/project-guide.md#disabling-a-feature)
  explains the difference between feature selection and API-only mode.

## Deployment

- Follow [Production deployment](docs/guides/deploy.md) for configuration, migrations and checks.

## License

TBD.
