# Dotmac product vision — build once, compose every product

**Status:** Accepted north-star direction
**Decision owner:** Michael
**Last updated:** 2026-08-12

This document owns the strategic **why** and the target product shape for
`dotmac_starter_mt`. It does not claim that the target is already implemented.
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md) remains the as-built source of truth;
the [ADRs](adr/) own individual decisions and sequencing.

## The vision in one sentence

`dotmac_starter_mt` exists to turn the capabilities proven in `dotmac_erp`,
`dotmac_crm`, and `dotmac_sub` into one governed ecosystem of a universal
`dotmac-kernel`, a shared `dotmac-ui`, independently versioned domain modules,
and thin product assemblies—so Dotmac builds each capability once and composes
it across every app that needs it.

## Why we are building Starter

Dotmac's production systems grew as standalone applications. ERP, CRM, and Sub
each solved a real operational need, but the monolith boundary also caused
platform mechanics, presentation patterns, and overlapping business decisions
to be implemented repeatedly. A security fix, permission rule, migration
contract, table component, or domain correction then has to be found, ported,
and verified in several places. The copies drift, and the organisation pays for
the same decision more than once.

Starter is the convergence point for ending that drift. It is not merely a
template to clone, and it is not a new mega-application that absorbs every
domain. It is the package source, composition contract, reference assembly, and
conformance system for a reusable Dotmac application platform.

The three existing applications are therefore **source monoliths**: they remain
the live systems and authorities from which proven behaviour is characterized
and extracted, but they are not the final software boundaries.

| Source monolith | Primary evidence and extraction role | Target product shape |
|---|---|---|
| `dotmac_erp` | finance, people/workforce, payroll, inventory, procurement, assets, expenses, projects, and other back-office capabilities | a Backoffice/ERP profile assembled from domain modules |
| `dotmac_crm` | acquisition, leads, opportunities, campaigns, engagement, and relationship workflows | an Engagement/CRM profile assembled from domain modules |
| `dotmac_sub` | operational customer/subscriber state, service lifecycle, feasibility, provisioning, network/device operations, outages, tickets, work orders, and the official operational timeline | an ISP Operations/Sub profile assembled from domain modules |

These rows identify extraction sources, not permission to move a decision
blindly. Exact overlaps are adjudicated before code moves. A duplicate of an
operational fact in CRM is normally retired in favour of Sub's authority; it is
not automatically promoted into a fourth shared implementation.

## The target architecture

```text
TRANSITIONAL SOURCES
  dotmac_erp        dotmac_crm        dotmac_sub
       \                 |                /
        \--- product-first vertical extraction ---/
                            |
PLATFORM ECOSYSTEM          v
  dotmac-kernel       universal application invariants
  dotmac-ui           shared presentation contract and assets
  dotmac-<domain>     independently versioned business capabilities
                            |
                            v
THIN PRODUCT ASSEMBLIES / PROFILES
  Backoffice/ERP   Engagement/CRM   ISP Operations/Sub   future Dotmac apps
```

Product names may remain as market-facing profiles or SKUs. What disappears is
the assumption that each product name requires a separate implementation of
everything beneath it.

### `dotmac-kernel`: invariants every product must share

The kernel owns universal application mechanics that must be corrected exactly
once: tenancy and isolation, identity seams, sessions, authorization and
capability evaluation, settings resolution, audit, transaction rules, module
registration, migration composition, lifecycle contracts, observability,
security headers, and stable API/error conventions.

The kernel must stay narrow. Finance policy, subscriber lifecycle, sales
pipeline semantics, payroll, inventory valuation, and other business decisions
do not become kernel features merely because several products might use them.

### `dotmac-ui`: one presentation system

`dotmac-ui` owns semantic tokens, accessible primitives, layouts, forms,
tables, navigation composition, feedback states, and packaged assets. It has no
business authority and does not read a database. Domain modules contribute
surfaces through the versioned UI contract; assemblies choose which facets to
mount and how they are branded.

### Domain modules: the reusable business system

An optional business capability belongs in a focused `dotmac-<domain>` module,
not in a residual ERP, CRM, or Sub core. A stateful module owns its models,
service decisions, permissions, events, schema namespace, and migration
lineage. Its API, web routes, jobs, and webhooks are thin adapters around that
owner.

Candidate families include finance, inventory, procurement, workforce,
billing, subscriber lifecycle, network operations, ticketing, work orders, and
engagement. These are a decomposition map, not pre-approved package boundaries:
each boundary still needs a named decision owner, product evidence, a coherent
contract, and a cutover dossier.

Modules do not import one another to create a distributed monolith. The
assembly composes them; cross-domain collaboration uses declared capabilities,
stable IDs, APIs, and events.

### Product assemblies: composition, not another core

A thin product assembly pins the kernel, UI, modules, providers, theme/brand,
policies, facets, and deployment profile. It may own genuinely product-specific
composition, but it must not copy, monkey-patch, or restate a platform or domain
decision.

The full ERP is therefore rebuilt **from Starter's released platform and domain
modules**, not rebuilt as another standalone monolith inside Starter. The same
is true of CRM and Sub. Their repositories can remain independent deployment
and release units while their reusable implementation moves behind shared,
versioned contracts.

## What “build once” means

Build once means one canonical implementation, contract, behaviour suite, and
migration lineage for a capability. It does **not** mean one deployment, one
release cadence, or a shared production database.

For every business fact or state transition in a bounded context, there is one
named authoritative writer:

1. An assembly may install the owning module locally; that module owns the
   local state and decisions.
2. An assembly may use a remote capability binding; the remote service remains
   authoritative and the consumer integrates through a versioned API and/or
   events.
3. It may not run a local writer and a remote writer for the same aggregate.
   Projections and caches identify their source and are repaired from it.

Consequently, composing modules does not authorize cross-application table
access. Separate assemblies normally keep separate databases and integrate
through contracts. Shared tables are not an integration strategy.

“Build once” is complete only when the source product consumes the released
package and its local owner/writer is retired. Copying code into a package while
the monolith keeps an active implementation creates a third copy; it is not
adoption.

## Source-of-truth boundaries survive decomposition

Decomposition changes packaging and deployment boundaries; it does not make
business authority ambiguous.

- Kernel `Party`/identity primitives do not collapse a CRM lead, a Sub
  subscriber, and an ERP financial account into one accidental aggregate.
- CRM owns legitimate acquisition and engagement decisions; it does not become
  a parallel owner of subscriber service, outage, ticket, or work-order state.
- Sub remains authoritative for operational customer, subscriber, service,
  network, device, outage, ticket, work-order, and official-timeline state until
  an approved slice explicitly transfers that authority.
- ERP remains authoritative for its financial, workforce, inventory,
  procurement, asset, and other back-office state until the corresponding
  module cutover completes.
- Collectors and importers record observations. Resolvers derive state. The
  named service or policy owner decides consequences. Reconcilers project and
  repair the result idempotently.

Packaging code never silently transfers authority. Every transfer names the old
owner, new owner, shadow/verification phase, cutover gate, and retirement of the
fallback path.

## The strategic path to full adoption

This is an incremental recomposition, not a big-bang rewrite.

### 1. Prove the platform in a real product

Complete Sub's reference adoption of the kernel migration lineage, as required
by [ADR-0017](adr/0017-adoption-is-the-scarce-resource.md). A package used only
by Starter is still work in progress. Adoption defects discovered by the first
real product are platform work, not reasons to fork the product.

### 2. Make the monoliths countable

Maintain a fleet decomposition matrix across ERP, CRM, and Sub. For each
capability, record its current owner and writers, competing implementations,
consumers, database/migration owner, authority overlaps, target layer, and
retirement condition. Freeze each measured duplication baseline so it can only
shrink.

The matrix lives in
[`docs/inventories/fleet-decomposition-matrix.md`](inventories/fleet-decomposition-matrix.md),
is measured by `scripts/fleet_decomposition_sweep.py`, and is frozen in
`docs/inventories/fleet-decomposition-baseline.json`. Its first measurement
reorders the work below: nine tenths of the fleet's countable duplication is
CRM↔Sub, and most of that retires to an owner Sub already has rather than
extracting into a new package.

### 3. Extract one complete vertical slice

For a demanded capability:

```text
characterize proven behaviour
  -> adjudicate the owner and contract
  -> preserve source behaviour tests
  -> extract at the narrowest correct layer
  -> make the source monolith consume the package
  -> shadow and reconcile
  -> cut over one authoritative writer
  -> delete or hard-gate the local implementation
```

The mature product implementation is the initial source when it satisfies the
contract. Greenfield shared behaviour is allowed only when the inventory proves
that no qualifying implementation exists.

### 4. Prove reuse through another assembly

Where the contract is genuinely shared, adopt the same released module in a
second independent assembly. Product-specific policy stays outside the module
behind declared adapters; the shared behaviour is not forked to accommodate it.

### 5. Recompose and retire the shells

Repeat by domain until ERP, CRM, and Sub contain only thin assembly concerns and
approved product-specific policy. A monolith is fully adopted when it no longer
owns local substitutes for the kernel, UI, or extracted domain modules, and it
can receive their fixes through ordinary tested dependency updates.

Fleet adoption is complete when a capability fix is released once, reaches
every consuming assembly through a tested version update, uses the package's
own migration path, and requires no bespoke port in ERP, CRM, or Sub.

## How we measure progress

The primary metric is adoption, not packages created or code moved.

- released contracts exercised by real product assemblies;
- source-monolith implementations and writers retired;
- authority collisions eliminated and guarded by tests;
- package-owned migrations proven in consumer databases;
- successful drift detection, reconciliation, rollback, and update paths; and
- time for one fix to reach every intended consumer without reimplementation.

A module with no product consumer, or with its source implementation still
active, remains work in progress regardless of its release number.

## Guardrails

- Do not rewrite all three monoliths at once.
- Do not turn Starter into a shared production database or a single deployment.
- Do not create an `erp` module, `crm` module, or `sub` module that merely
  repackages a monolith.
- Do not move optional business policy into the kernel.
- Do not extract because two implementations look similar.
- Do not preserve dual writers after cutover.
- Do not count source copying as adoption.
- Do not let a product assembly become another permanent core.

## Relationship to existing decisions

This vision composes the existing accepted decisions rather than replacing
their implementation detail:

- [ADR-0003](adr/0003-unified-deployment-profiles.md) defines the versioned
  kernel/module/assembly/profile model and independent data-plane deployments.
- [ADR-0006](adr/0006-white-label-product-foundation.md) defines kernel, UI,
  module, theme, brand, facet, product-first extraction, and migration
  namespacing boundaries.
- [ADR-0010](adr/0010-adapters-are-thin.md) keeps delivery surfaces as adapters
  around the decision owner.
- [ADR-0017](adr/0017-adoption-is-the-scarce-resource.md) makes real product
  adoption the metric and sequences Sub as the first kernel-lineage adopter.

Future ADRs decide individual domain boundaries and transfers. They must state
how the decision advances—or explicitly deviates from—this target.
