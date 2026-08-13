# `dotmac-files` adoption: ERP, Academy, then Vendor Control Plane

**Status:** execution plan; not evidence that either product has adopted the
module  
**Decision:** ADR-0022  
**Dossier:** `packages/dotmac-files/EXTRACTION.toml`  
**Order selected by Michael:** ERP cutover 1, Academy cutover 2, Vendor Control
Plane cutover 3

## Exit condition

The work is complete only when all three assemblies pin the same exact released
`dotmac-files` version, compose its `fi` lineage, use a product-owned provider
adapter, keep domain attachment authorization in the product, and have retired
their prior physical-file lifecycle owner or direct byte path. Until then
`contract_consumers = []` remains correct.

No product may use a relative path dependency, copy the module migration, add a
second session factory, or keep a permanent dual writer.

## Release gate

1. Finish Starter validation for `dotmac-files 0.1.0a1` and its minimum
   `dotmac-kernel 0.1.0a54` dependency.
2. Publish both exact versions to the approved package registry through the
   normal branch/review/release workflow.
3. Prove the wheel contains the `fi` migrations and is importable in a clean
   consumer environment.
4. Keep PDF/CSV/XLS/XLSX and passive image admission in `dotmac-files`; keep
   semantic parsing, image transformation, and domain mutation out.

## Cutover 1 — ERP

ERP is first because it is a qualifying source product for the production S3
adapter and admission-policy behavior. Its current adoption ledger classifies
`dotmac_kernel.db` and stateful lineages as `defer-db`, so the following E8 gate
is mandatory before file adoption begins:

- approve one Organization-to-Tenant mapping with no parallel tenant writer;
- establish one transaction authority and one request-scoping GUC contract;
- compose independent migration lineages without copying their revisions; and
- prove ERP's existing organization isolation is not weakened.

After E8:

1. Add exact kernel/files pins and compose the `fi` lineage. Run the composed
   migration gate before any live migration.
2. Port `app/services/storage.py` behind an ERP-owned implementation of
   `StorageProvider`. It may configure MinIO and credentials, but it must not
   decide file policy or domain authorization.
3. Add a two-directional ratchet over imports/calls to
   `app.services.file_upload` and direct `S3StorageService` usage, including a
   sensitivity proof. The baseline may fall only when the recorded count is
   lowered in the same change.
4. Migrate in coherent domain slices. Start with avatars because their accepted
   image formats supplied the new admission canaries, then branding, support,
   finance/expense, PM, HR/people, and careers. Import endpoints use the shared
   byte admission only; their parsers and row mutations remain product-owned.
5. For each slice, expand the domain table with a tenant-composite opaque file
   relation, import existing provider objects into `mod_files.stored_files`,
   and verify tenant mapping, key, byte length, media type, and SHA-256.
6. Shadow authenticated reads against the same provider object. Run missing
   object and orphan reconciliation in report-only mode before enabling repair.
7. Switch the domain read/write path once parity is exact. Replacement and
   deletion use `deletion_pending` plus the idempotent worker phases; they do
   not delete an old object inside the domain transaction.
8. Reduce the old upload facade to a temporary delegating compatibility adapter
   while its ratchet burns down. Delete `app/services/file_upload.py` as an
   owner and remove the old `S3StorageService` API when the count reaches zero;
   retain only the assembly provider adapter.

ERP acceptance evidence includes fresh-database and upgrade migrations,
cross-organization RLS isolation, provider replay/conflict behavior, each
domain's authorization canary, key/size/checksum/read parity, reconciliation,
and a sensitivity proof for the retirement ratchet.

## Cutover 2 — Academy

Academy's coherent slice is account avatars. It already uses the kernel DB/RLS
request boundary, but it still has a single local Alembic lineage and pins an
older kernel. Its adoption therefore begins only after the exact release exists:

1. Upgrade the exact kernel pin to the module's supported floor and reconcile
   the kernel changelog. Compose the `fi` lineage and replace the current
   one-root/one-head migration assumption with the fleet composed-lineage gate.
2. Add a configured Academy-owned local-filesystem `StorageProvider`. Its root
   is outside the public static tree; only a guarded Academy route streams an
   authorized avatar target.
3. Add `people.avatar_file_id` as a nullable UUID with a composite
   `(tenant_id, avatar_file_id)` reference to
   `mod_files.stored_files(tenant_id, id)`. The Academy migration declares a
   cross-lineage `depends_on` rather than chaining `down_revision` to `fi`.
4. Move upload orchestration out of `app/web/account.py` into an Academy service.
   The route validates/authorizes/delegates/renders. The Academy policy is one
   MiB and permits PNG, JPEG, GIF, and WebP; the shared module verifies the
   actual signature instead of trusting only the multipart content type.
5. Import each `avatar_path` object under its immutable tenant key, stage its
   metadata, write the opaque relation, and verify bytes plus SHA-256. Keep the
   legacy read fallback only while a measured nonzero backlog remains.
6. Switch the profile and shell reads to the guarded file route. Remove direct
   `static/avatars` writes, delete verified legacy objects through an explicit
   cleanup operation, then drop `avatar_path` after the fallback count reaches
   zero.

Academy acceptance evidence includes cross-tenant avatar isolation, spoofed
image rejection, one-MiB boundary behavior, guarded streaming, failed-DB-write
orphan repair, replacement/deletion retry, fresh and upgraded composed
migrations, and a sensitivity proof that direct avatar disk I/O is prohibited.

## Cutover 3 — Vendor Control Plane

Vendor CP uses the platform plane. It remains a platform-only assembly and must
not manufacture a tenant, set a tenant GUC, or link its records to a product
database. The first coherent slice is the exact offline licence bundle handed
to an authenticated operator, if retaining those exact bytes is accepted as a
durable audit requirement:

1. Pin kernel `>=0.1.0a54` and the same exact `dotmac-files` release as ERP and
   Academy. Compose the `fi` lineage with Vendor CP's platform lineages.
2. Install a Vendor-CP-owned `StorageProvider` adapter and configure its
   credentials through the assembly's secret/provider seams.
3. Add a licensing-owned relation from the export/delivery attempt to
   `mod_files.platform_stored_files.id`. It owns the business meaning,
   authorization and retention permission; it contains no tenant id.
4. Render the already-frozen issuance bundle once, call `prepare_upload` with
   `PlatformScope()`, stage the metadata in the same caller-owned transaction
   as the licensing relation/attempt, then stream the exact stored target to
   the response after the database phase ends.
5. Preserve the existing distinction between an exported handoff and a merely
   generated bundle. The delivery attempt remains the official evidence that
   bytes crossed the process boundary; the platform file proves which bytes.
6. Remove the direct render-to-response path once parity proves the downloaded
   bytes and digest are exact. Do not create a Vendor-CP-local generic storage
   facade beside `dotmac-files`.

Vendor acceptance evidence includes a platform-session lifecycle test,
byte/digest equality with the exported bundle, authenticated streaming,
idempotent replay, platform orphan/deletion recovery, absence of `tenant_id`
and RLS on the platform table, `REVOKE ALL` from `app_user`, and a provider
boundary canary proving a platform reconciliation cannot enumerate or delete a
tenant prefix. If exact-byte retention is not a real requirement, this slice is
not implemented merely to claim adoption; another genuine Vendor CP artifact
must be selected first.
