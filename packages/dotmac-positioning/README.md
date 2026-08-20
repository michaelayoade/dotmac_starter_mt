# dotmac-positioning

`dotmac-positioning` is the tenant-scoped owner of provider-neutral tracked-unit
position evidence, bounded collection grants, source assignments, current and
trail projections, retention, and neutral geofence entry/exit facts over an
explicit product-selected set of opaque fences.

Products own every subject link and business consequence. They map their local
subjects to opaque tracked-unit IDs, supply purpose and validation policy as
typed inputs, authorize endpoints, and decide what an entry or exit means. The
module contains no product vocabulary, external-system catalogue, transport,
map presentation, or transaction boundary.

Observation ingest does not choose geofences. A product resolves its own work,
fleet, attendance, or other context and calls the typed geofence-evaluation
contract with only the relevant opaque ids in the same transaction. Unselected
fences are untouched, and stale observations cannot regress fence state.
The current-position projection has a published idempotent repair operation
using the same capture-time, accuracy, receipt-time, and observation-id ordering
as live ingest.

The first implementation is extracted product-first from Dotmac Sub under
Starter ADR-0039. Each adopting application installs its own `po` lineage and
owns its own `mod_pos` rows; applications never share this database.

Consuming assemblies compose the installed lineage with
`dotmac_positioning.versions_dir()`; they do not reach into a source checkout.
They bind and live-verify the module's declared tenant-catalogue and database-role
prerequisites, run online sessions as RLS-enforced `app_user`, and retain their
own transaction authority. The module has one atomic tenant-only installation
shape, so ADR-0028 correctly rejects a redundant `ModulePlaneSelection`; plane
selection belongs only to a module that declares multiple supported sets. The
package provides no single-tenant, product, provider, or superuser bypass.
