# dotmac-expenses

`dotmac-expenses` owns tenant expense requests, incurred-expense claims,
category and receipt meaning, effective-dated limit evaluation, guarded
submission/decision transitions, and derived reimbursement eligibility.

It does not own Party/employee identity, approval quorum, stored bytes,
numbering, work orders/projects, advances/cards, AP/GL/tax, bank details,
payment initiation, settlement or payment coverage. Receipt rows reference an
opaque file UUID. Approval transitions carry an opaque decision reference.
An eligible claim is ready to be presented to Finance; it is not evidence of
payment.

The package is an installable tenant-plane module. Its `ex` lineage owns
`mod_expenses`. Services require an explicit `TenantScope`, mutate and flush,
and never commit or roll back. Draft revisions replace the complete request or
claim line snapshot through that same service owner; submitted lines cannot be
edited. See `EXTRACTION.toml`, ADR-0047 and
`docs/inventories/expenses-sources.md` for the product-first boundary.
