# dotmac-work-orders

`dotmac-work-orders` owns physical work execution: a work order, its guarded
lifecycle, assignment history, execution events, work logs, notes and evidence
references. It is tenant-only and installs its own `wo` Alembic lineage in
`mod_workorders`.

The module does not decide why work exists or what completing it means to the
rest of a product. Sub keeps subscriber, ticket, project and official-timeline
authority. It validates fiber/splice and other product prerequisites before it
asks this owner to complete, then reacts to the committed outcome through its
own outbox and reconcilers. The module stores evidence metadata and an opaque
artifact reference; `dotmac-files` (when adopted) owns bytes.

## The boundary

| Owned here | Owned by the adopting product |
|---|---|
| work-order identity and execution status | subscriber, project, task and ticket links |
| product-neutral header updates | product subject links and completion consequences |
| assignment/unassignment history and current projection | technician/vendor roster and eligibility |
| mobile event identity and timestamps | dispatch scoring, routes, ETA and location tracking |
| work logs, notes and evidence references | inventory/material issue and topology/as-built records |
| generic evidence completion contract | product-specific completion prerequisites and consequences |

Subject associations are product-owned link tables with real foreign keys. The
shared table deliberately has no `subscriber_id`, `project_id` or `ticket_id`.
Cross-application work is synchronized through typed APIs/webhooks; an adopter
never reads another application's `mod_workorders` schema.

All retryable commands use `dotmac_kernel.idempotency`. Client command ids stay
on domain rows as correlation evidence, but the module does not create another
at-most-once ledger or store parallel request fingerprints.

## Vendor execution is deliberately unresolved

Sub's internal crews execute `WorkOrder`. External vendors execute a separate
`InstallationProject` commercial flow with quotes, POs, AP and as-built review,
and no work order. This package ports the qualifying internal-crew owner. It
does not infer that `InstallationProject` is now a work order or that the work
order must absorb vendor commercials. That cut requires its own explicit
owner/cutover decision.

See [`EXTRACTION.toml`](EXTRACTION.toml) and
[`docs/inventories/work-orders-sources.md`](../../docs/inventories/work-orders-sources.md).
