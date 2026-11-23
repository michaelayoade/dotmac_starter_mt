# Documentation

- **New here?** Follow [Your first tenant](tutorials/first-tenant.md).
- **Need the big picture?** Read [Architecture overview](concepts/overview.md).
- **Looking for exact details?** Open the reference links below.

## Learn

- [Run the app and create your first tenant](tutorials/first-tenant.md).
- The tutorial uses one example workspace, ACME, from setup to your first record.

## Complete a task

- [Choose features and brand your product](guides/build-a-product.md).
- [Add a custom field](guides/custom-fields.md).
- [Apply database migrations](guides/migrations.md).
- [Understand testing and checks](guides/testing.md).
- [Deploy to production](guides/deploy.md).

## Explore the modules

- [What each module does](modules/index.md): short explanations grouped by purpose.
- [Reference app features](modules/reference-features.md): the nine application-owned features.
- Each package entry explains its purpose, when to use it and its boundary.

## Look something up

- [Configuration](reference/configuration.md).
- [Detailed project reference](reference/project-guide.md): routes, roles and advanced notes.
- [Module catalog](MODULE_CATALOG.md): generated from package metadata; do not edit by hand.
- [Environment variables](../.env.example).
- [Make commands](../Makefile).
- [Security posture](SECURITY.md).
- [License status](../README.md#license).

## Understand the design

- [Architecture overview](concepts/overview.md): the main building blocks.
- [Architecture reference](ARCHITECTURE.md): as-built behavior and ownership.
- [Architecture decisions](adr/): decisions, status and dated amendments.
- [Product vision](PRODUCT_VISION.md): intended direction.

## Which document should I trust?

- **Current behavior:** `ARCHITECTURE.md` is authoritative for architecture.
- **Rules:** [AGENTS.md](../AGENTS.md) holds the canonical agent rules;
  [CONTRIBUTING.md](../CONTRIBUTING.md) explains the human workflow.
- **Decisions:** ADRs explain why; check their status and amendments.
- **Evidence:** [inventories](inventories/) record findings at named revisions.
- **Plans:** `superpowers/plans/`, `specs/` and reviews describe intent;
  they do not prove that a feature is implemented.

## Writing a guide

- Give the page one clear purpose and a task-based title.
- Use short bullets, familiar words and exact commands or button names.
- State prerequisites and the directory where commands run.
- Show the expected result and common fixes.
- Link to the next step and the authoritative reference.
- Use real screenshots when available; do not present mockups as product screenshots.
- Update guides with behavior changes. Keep rules and generated catalogs in their owners.
