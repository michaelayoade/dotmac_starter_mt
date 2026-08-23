# UI component and browser-behaviour inventory — 2026-08-19

This is product-first extraction evidence, not proof of adoption. It records the
exact sources used for the list, recent-activity and generic form-behaviour
candidates added to Starter. Product repositories are unchanged by this slice.

## Surveyed revisions

- `dotmac_sub` — `91c1ec477b3af37931424bced856a16bbc2c6d3f`
- `dotmac_crm` — `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`
- `dotmac_erp` — `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`

## List and backend projection contracts

Sub's `app/services/list_query.py` is the qualifying production source. It has
30 `ListDefinition` declarations across 27 production importers and owns search,
declared filters, sort, page size, canonical query strings and page metadata.
ERP and CRM have no equivalent typed `ListDefinition`/`ListQuery`/`PageMeta`
contract. Sub's older 612-line `templates/components/data/data_grid.html` still
has six live callers and represents a parallel renderer to reconcile during
cutover, not evidence that it is already retired.

The kernel candidate ports only request normalization, canonical URLs,
`PageMeta`, state/freshness, semantic status, KPI cohort and backend-decided
action values. Product services continue to own storage queries, authorization,
counts, row projection, action eligibility, bulk effects and export delivery.
`dotmac-ui` imports no kernel; its inert renderer consumes the public value
shape through stock Jinja.

## Recent activity

Sub and CRM's `templates/components/data/recent_activity_panel.html` are
byte-identical at blob `5319b6d…`, with 11 and 10 live template references
respectively. The shared candidate replaces only raw-palette markup with a
token-native, display-only `ActivityItem`. Each product remains the owner of
the official timeline, event inclusion/order, wording, permissions, URLs and
formatted time labels.

## Form behaviours

Sub and CRM have byte-identical, product-loaded copies of:

- `static/js/form-validation.js` — blob `927558…`;
- `static/js/repeatable-fields.js` — blob `8fa3c5…`;
- `static/js/unsaved-changes.js` — blob `6f32bf…`.

The repeatable-fields file combines reusable add/remove/reorder mechanics with
invoice floating-point tax/money calculations and customer-contact role/primary
policy. Only the generic mechanics qualify. The candidate also omits phone and
currency formatting, treats server validation as authoritative, exposes neutral
`dmui:*` events, and fixes the source teardown defect where `removeEventListener`
created a new bound function instead of removing the installed handler.

Sub/ERP's identical `csv-parser.js` belongs to the tabular import front end and
`dotmac-imports`, not to the design system. CRM's copied alert and modal have no
live caller and remain deletion candidates rather than extraction sources.

## Adoption gates

No local owner retires in Starter. Each product must pin a released package,
exercise the installed asset/template through its real loader, prove DOM,
keyboard, accessibility and failure parity, and delete only the renderer or
generic mechanics the shared contract actually replaces. Domain decisions and
official timelines do not move with the UI.
