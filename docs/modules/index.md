# What each module does

- Start here to understand the packages without reading their technical contracts.
- This guide covers all **104 reusable packages** in the current checkout, plus the nine application features.
- Each entry explains **what it does**, **when to use it** and **its boundary**.
- Groups below are reading aids, not new registries, product bundles or automatic installations.

## What is enabled in the reference app?

- The app declares nine [application features](reference-features.md).
- It also composes Template Studio and Ticketing, using the kernel and shared UI.
- Feature switches can omit routes. Packages listed here are not all active in the app.
- The [assembly](../../app/assembly.py) owns selection. The generated [catalog](../MODULE_CATALOG.md) records installation and evidence state.

## Browse by purpose

- [Shared foundations](foundation.md): 7 packages.
- [Finance and money](finance.md): 14 packages.
- [Sales, customers and subscriptions](commerce.md): 7 packages.
- [People, work and shared workflows](work.md): 11 packages.
- [Content, documents and analytics](content.md): 17 packages.
- [Service delivery and physical resources](services.md): 10 packages.
- [Network operations](network.md): 10 packages.
- [Conversations and external integrations](integration.md): 16 packages.
- [Vendor platform and deployment](platform.md): 12 packages.

## Check maturity before choosing a package

- A source implementation, a published release and adoption by a product are different facts.
- `audit-complete` describes reviewed boundaries; it does not prove production adoption.
- Candidate consumers are proposed users, not completed deployments.
- Check the exact version, contract, persistence plane and evidence in the catalog and package dossier.
- Some packages are adapters or stateless facilities rather than database-backed business modules.
- The summaries explain purpose; package contracts and the [architecture reference](../ARCHITECTURE.md) govern exact behavior.

## Common distinctions

- **Accounting** posts balanced journals; **Billing** tracks customer invoices and receivables.
- **Payments** correlates payment intent and evidence; provider **connectors** translate external operations.
- **Files** stores bytes; **Documents** controls versions; **Records** governs retention.
- **People** manages employment; **Workforce** manages operational availability and dispatch.
- **Template Studio** renders templates; campaign and integration owners coordinate delivery.
- The **Licensing package** issues grants; the reference **licensing feature** receives them.

## Keeping this guide current

- Add or revise the explanation when a package responsibility changes.
- Keep one entry per distribution and link to its own README and extraction dossier.
- Read the generated catalog for changing versions and evidence; do not hand-edit it.
- [Back to documentation](../index.md).
