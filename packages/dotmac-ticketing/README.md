# dotmac-ticketing

A product-neutral ticket lifecycle. ERP, Sub, the vendor control plane and
future products consume one module and declare their own variants on top.

They consume independent installations. Each application pins the package,
runs the lineage in its own database and owns its own rows; no application
queries another adopter's `mod_tkt` schema. Cross-application ticket data moves
through Integrator capabilities and versioned product ports. A product keeps a
typed observation or rebuildable projection only for a named local reader
(ADR-0024); correlation alone stays an opaque Integrator reference. A remote
status never writes a local ticket lifecycle directly, and provider-specific
mapping never enters a product or this package.

## The layering

| layer | owner | extensible | decides |
|---|---|---|---|
| **lifecycle class** — `open`/`waiting`/`resolved`/`closed`/`cancelled` | module | **no**, fixed at 5 | does the SLA clock run, does it count as open |
| **status** — 9 standard helpdesk terms | module | **no** | what a UI calls it; which transitions are legal |
| **status reason** | **product declares** | yes | *why* it is in that status; filterable, drives routing |
| **tag** | operator | yes | searchable only, no behaviour |
| **comment** | anyone | — | narrative, never queried for behaviour |

A product extends the **reason** layer and never the status layer. That is what
keeps `lastmile_rerun` — a fiber last-mile field operation — from becoming a
ticket state every other product carries.

The evidence for the split is in the starter's
`docs/inventories/ticket-sources.md`: all sixteen references to Sub's two ISP
statuses put them in a set alongside the standard ones, and not one branches on
them individually. They were never behaving like statuses.

## Two planes, one lifecycle (ADR-0023)

The same capability runs in two security contexts, so the **storage** is split
and nothing else is. The table above — classes, statuses, reasons, tags — is
shared, and the lifecycle engine imports no persistence at all.

| | tenant plane | platform plane |
|---|---|---|
| classes | `TenantTicket`, `TenantTicketComment` | `PlatformTicket`, `PlatformTicketComment` |
| tables | `tickets`, `ticket_comments` | `platform_tickets`, `platform_ticket_comments` |
| `tenant_id` | `NOT NULL` | absent |
| isolation | FORCEd RLS + tenant policy | GRANT platform roles, **REVOKE `app_user`** |
| number unique | per tenant | control-plane-wide |
| link helper | `link_tenant_subject()` | `link_platform_subject()` |

A product data plane (ERP, Sub) uses the tenant plane; a control plane (the
vendor CP) uses the platform plane. **No foreign key crosses them** — they share
a lifecycle, never a row — and the kernel's live-catalog gate refuses one that
does.

The bare table name is the tenant plane, because multi-tenancy is the fleet's
default. The Python classes are explicit on both sides, because a bare `Ticket`
in a product's imports is exactly the ambiguity this removes.

## Subjects

There are no `subscriber_id` / `project_id` columns. A ticket has many subjects
(Sub's has six, ERP's five), so they live in **product-owned link tables** with
real foreign keys, generated into the product's own migration:

```python
from dotmac_ticketing import link_tenant_subject

def upgrade() -> None:
    link_tenant_subject(
        table_name="sub_ticket_subscriber",
        subject_table="subscribers",
        subject_column="subscriber_id",
        on_delete_subject="RESTRICT",   # required — no safe default
    )
```

The tenant helper emits the table, both foreign keys (the ticket side composite
on `(tenant_id, ticket_id)`), the indexes, `ENABLE`+`FORCE` row-level security,
the isolation policy and the grants. Hand-writing that is where the RLS policy
goes missing.

On the control plane, `link_platform_subject()` is the counterpart — a vendor CP
links a ticket to a `vendor_accounts`, `deployments` or licence-delivery row the
same way:

```python
from dotmac_ticketing import link_platform_subject

def upgrade() -> None:
    link_platform_subject(
        table_name="vcp_ticket_vendor_account",
        subject_table="vendor_accounts",
        subject_column="vendor_account_id",
        on_delete_subject="RESTRICT",
    )
```

It emits no tenant column and no RLS — there is no tenant context to populate or
test — and instead GRANTs to the platform roles and `REVOKE`s from `app_user`,
which is what isolates that plane.

**Ordering:** the generated FK targets `mod_tkt.tickets` or
`mod_tkt.platform_tickets`, so this module's lineage must run before any product
migration that calls either helper.

## Status

`0.1.0a1` ships the lifecycle, the vocabulary registry, four tables across two
planes and both linking helpers. Routers, schemas and admin screens land with
the first adopter's surface.

**First adopter: ERP** (Michael's 2026-08-13 direction; ADR-0017's ticketing
amendment). It is where the duplication actually costs something, so the
vocabulary is proven against a live support estate before anything greenfield is
built on it. Only ERP-owned internal work moves; ERPNext/CRM ticket rows are
archived and retired from ERP's operational schema. Remote work creates a
separate ERP-owned ticket only when an ERP workflow explicitly requires it.
The trade is that the programme inherits ERP's E8
Organization-to-Tenant gate as a hard prerequisite. The vendor control plane is
cutover 2 — greenfield, no rows to migrate. Plan:
`docs/superpowers/plans/2026-08-13-ticketing-erp-vendor-cp-adoption.md` in the
starter.
