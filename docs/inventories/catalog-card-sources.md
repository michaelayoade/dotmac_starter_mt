# Catalog discovery card — product-first source inventory

Date: 2026-08-18. This inventory authorizes only a display primitive in
`dotmac-ui` and a typed recurring-offer read in `dotmac-subscriptions`. It does
not authorize a product cutover, a generic commerce owner, or new Inventory or
Assets implementations.

## Ruling

Two live, independent `dotmac-ui` consumers have the same presentation need:

- Workspace at `c72fe304d3c8b2a2741d111379e4c4ab0af5da57` renders connected
  application discovery in
  `src/dotmac_workspace/launcher/web.py::_tile`; the token-native responsive
  card/grid rules are `dmws-tiles` and `dmws-tile*` in
  `src/dotmac_workspace/static/css/workspace.css`.
- Academy at `40423a07a4eaa6172a36997f3276cb7a79dda343` renders two live course
  discovery collections in `templates/public/courses.html`; their responsive
  card/rail styling is in `static/public.css`.

The common contract is deliberately small: title, supporting metadata,
description, optional media with alt text, optional notice, and an optional
paired label/URL action. `dotmac-ui` owns only that markup, accessibility and
role-token CSS. Each product still owns which items exist, who may see them,
their labels/media, state, ordering and actions.

Sub's service catalog is not the component source. Its admin catalog is a dense
filtered operational grid, while customer plan change is a selectable workflow
whose eligibility, delivery mode, price comparison and proration are business
decisions. ERP's inventory and skills catalogs are tables. Sales owns no
catalog: its accepted Quote line carries an opaque `catalogue_ref` and freezes
the selected description, quantity and exact amount. Vendor CP has immutable
offer terms but no matching HTML renderer.

## Commercial read boundary

`dotmac-subscriptions` already owns stable recurring offers and immutable
offer/price versions (ADR-0020/0030). `list_effective_offers` is therefore its
owner read: explicit scope and instant in; one latest effective published
version per stable published offer, exact prices and source provenance out. The
adopting product maps those facts to display strings. Search facets, offer
family, serviceability, eligibility, stock and action authorization remain
product-owned.

This is not a universal product-and-service database. Physical stock and
durable assets are distinct facts:

| Concern | Owner/boundary |
|---|---|
| Recurring offer identity and immutable recurring price terms | `dotmac-subscriptions` |
| Lead, Quote and immutable accepted-Quote handoff | `dotmac-sales`; stops before order, stock, asset, service or invoice |
| Stocked item/SKU, warehouse quantity, lot/serial, receipt/issue/transfer/adjustment/reservation and inventory valuation facts | future ERP-sourced Inventory/Procurement module; existing authority remains `dotmac_erp` |
| Individual durable asset, custody/assignment, location, maintenance, lifecycle, impairment/revaluation/disposal and finance linkage | future ERP-sourced Assets/Fleet module; existing authority remains `dotmac_erp` |
| Discovery card/grid markup and role-token CSS | `dotmac-ui` |

The checked-in fleet decomposition already requires both ERP-sourced modules in
wave 5. They are needed for their domains, not as prerequisites for offer
discovery. Inventory may later supply availability through a typed product
adapter. An inventory item becoming a durable asset is an explicit handoff
between owners, never a shared row or a reason for Sales to maintain either.

## Deferred cutover

This Starter branch adds the package contracts and tests only. It does not edit
Workspace, Academy, Sub, ERP, Sales, an assembly manifest, a migration binding,
or a deployment profile. The `catalog-grid` dossier slice remains
`audit-complete` with zero contract consumers.

A later coordinated cutover must:

1. release the exact `dotmac-ui` candidate;
2. map Workspace and Academy product-owned results into `CatalogItem`;
3. prove loader, escaping, responsive and accessibility parity in each product;
4. retire each overlapping local card renderer and CSS; and
5. update the dossier from zero consumers to the evidence level actually
   achieved.
