# Original receipt verification context

The demonstrated ingress and polling gap was between a detached engine
preparation and the shared inbox writer: configuration revision and manifest
pin were discarded before the receipt was stored. Reading the current binding
at product delivery cannot recover those historical facts after rotation.

`dotmac_integration.execution.ReceiptVerificationOrigin` records the original
installation UUID, capability-binding UUID, connector key, manifest SHA-256,
configuration-revision UUID (or the prepared no-config state), and engine mode
(`ingress` or `poll`). It contains no configuration, secret reference, request
material, provider account or credential-generation claim. The first two UUIDs
are the receipt's existing identity columns; four additive columns hold the
remaining values. The internal `receipt_verification_origin` accessor reads
only the receipt and returns `None` for unknown history.

`PreparedIngress` and `PreparedPoll` supply this frozen value from their own
phase-one context. Poll preparation now carries the installation manifest pin
as ingress already did. `record_batch` supplies it to the existing
`receive_verified` owner. A retry with identical context remains idempotent.
Changed context, including an unknown historical context versus a new recorded
one, raises `ReceiptVerificationOriginConflict`, translated by the batch seam
to `EventVerificationOriginConflict`. The caller's existing transaction boundary
rolls back the whole batch; it never overwrites or deletes a committed receipt.
Concurrent insert losers retain the existing typed uniqueness-race refusal,
and a retry against the visible winner explicitly compares original context.

The additive `ig_0016_receipt_origin` follows published
`ig_0015_descriptor_contract`. Integration remains platform-only: no tenant
column, no RLS, existing platform DML grants and no `app_user` privileges. Shape
checks distinguish the all-null unknown tuple from recorded context. A final
AFTER UPDATE trigger freezes receipt UUID, installation, binding and all origin
columns while the receipt is retained, including legacy nulls and mutations
made by earlier BEFORE triggers.
The INSERT guard checks local installation/binding/connector and original
configuration-revision affinity; it never derives a manifest or revision from
current configuration. Downgrade locks the table and refuses to erase recorded
original context on currently retained receipts. Existing bounded receipt
retention is unchanged; deletion and DDL authority remain the pre-existing
trust boundary. This does not promise perpetual receipt history.

This records original verification context only. Neither a manifest nor a
configuration revision proves merchant identity, a credential generation,
authentic archival provider evidence, or cash qualification. There is no
historical backfill and no change to published capability messages,
ProductObservation v1, delivery semantics, settlement or cash writers. An
assembly still needs independently accepted finite namespace mappings and
provider proof before using any receipt as financial evidence.

Hosted canaries cover real ingress verification/normalization and polling
owners, exact retries, configuration rotation, whole-batch conflict rollback,
true concurrent first-origin retention, explicit legacy unknown, tenant-role
denial, role-specific immutable guards, a late BEFORE-trigger mutation,
guard sensitivity, and the actual predecessor/forward/downgrade lifecycle.
Local validation is limited to pinned Ruff and stdlib AST; it is not runtime
acceptance or publication evidence.
