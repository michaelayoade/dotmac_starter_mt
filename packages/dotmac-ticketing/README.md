# dotmac-ticketing

A product-neutral ticket lifecycle. ERP, Sub, the vendor control plane and
future products consume one module and declare their own variants on top.

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

## Subjects

There are no `subscriber_id` / `project_id` columns. A ticket has many subjects
(Sub's has six, ERP's five), so they live in **product-owned link tables** with
real foreign keys, generated into the product's own migration:

```python
from dotmac_ticketing import link_subject

def upgrade() -> None:
    link_subject(
        table_name="sub_ticket_subscriber",
        subject_table="subscribers",
        subject_column="subscriber_id",
        on_delete_subject="RESTRICT",   # required — no safe default
    )
```

The helper emits the table, both foreign keys (the ticket side composite on
`(tenant_id, ticket_id)`), the indexes, `ENABLE`+`FORCE` row-level security, the
isolation policy and the grants. Hand-writing that is where the RLS policy goes
missing.

**Ordering:** the generated FK targets `mod_tkt.tickets`, so this module's
lineage must run before any product migration that calls the helper.

## Status

`0.1.0a1` ships the lifecycle, the vocabulary registry, the two tables and the
linking helper. Routers, schemas and admin screens land with the first adopter's
surface. First adopter: the vendor control plane — greenfield on tickets, so it
proves the module lineage with no cutover risk.
