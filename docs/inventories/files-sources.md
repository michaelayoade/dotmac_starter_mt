# Stored-file and import source inventory

**As of:** 2026-08-13  
**Starter:** `d737131` (`origin/main` at last synchronization)  
**ERP:** `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`  
**Sub:** `4ca42f3c110e85c66d0d9900b7412ce234a1979a`  
**CRM:** `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d`  
**Academy:** `5072e4a5fca9438d3838421989179b1779ef4bc7`  
**Vendor CP:** `89848017d6b87e82dd4d6ffd0b2c9eaed5f9fee8`  
**Workspace:** `159e78c0efc1d782d1dbf370ccdd64e48e2ff8b6`

This is the ADR-0006 product-first evidence for `dotmac-files`. The audit
separated three capabilities that product code often combines:

| Capability | Owner selected | Product source |
|---|---|---|
| Stored bytes, validation, provider key, checksum and physical lifecycle | `dotmac-files` optional module | Sub lifecycle/provider contract, ERP policy checks, CRM spoofing checks |
| Attachment meaning, visibility, retention policy and domain relation | The attaching domain | Existing ticket, billing, field-service, vendor, subscriber and messaging services |
| Parsing, mapping, dry-run/apply and row outcomes | future `dotmac-imports` plus the importing domain | Sub import-run ledger and ERP finance import base |

## Mechanical census and source choice

The audit searched storage, upload, attachment, document and import names and
then read the service, model, migration, caller and test paths behind the hits.

- **ERP:** 79 relevant files, 26 application files making storage/upload calls,
  and 21 import-base related files. `app/services/storage.py` has the mature S3
  byte operations; `app/services/file_upload.py` has the broadest extension,
  size, MIME, magic-byte, checksum and key-policy behaviour, including PNG,
  JPEG, GIF and WebP signatures for the formats its avatar surface accepts
  (the current avatar policy does not enable that stronger check). Its policy
  table is domain-specific and therefore becomes a caller-supplied
  `FilePolicy`, not a central registry. `finance/import_export/base.py` is the
  broadest CSV/XLS/XLSX
  parser, preview, mapping and batch implementation, but it is entangled with
  finance mutations and rollback semantics and is not file lifecycle code.
- **Sub:** 42 relevant files, 20 storage call files and 12 import-run/parser
  files. `object_storage.py` supplies the strongest typed provider protocol,
  error taxonomy, streaming result, retry and bucket behaviour.
  `file_storage.py` and `models/stored_file.py` supply durable metadata,
  content-addressed/trusted keys, staged upload/deletion, and the explicit
  orphan-reconciler requirement. The older commit/hard-delete methods are
  rejected; only the newer flush-only staged contract is preserved. Sub's
  subscriber/public ownership columns are also rejected: they describe domain
  attachments and cannot express Starter's mandatory tenant scope.
- **CRM:** 23 relevant files, 14 storage call files and 28 import/CSV references.
  `upload_validation.py` supplies the strongest content-versus-declared MIME
  spoofing canaries. Its `HTTPException` is replaced by module-domain errors.
- **Academy:** no generic owner. Account avatar upload writes PNG, JPEG, GIF or
  WebP directly to local disk and records a public path, making it the selected
  second consumer after the source-product cutover. In-memory generated email
  attachments are delivery payloads, not durable stored files.
- **Vendor CP and Workspace:** no qualifying stored-file lifecycle owner.
  Vendor CP is nevertheless a real platform-plane consumer candidate: its
  licensing transport already exports exact offline bundle bytes to an
  authenticated operator and records the handoff. ADR-0023 therefore sources
  no new lifecycle behavior from Vendor CP; it uses the shared physical engine
  and keeps the bundle relation, authorization and delivery attempt in
  licensing. Workspace still has no selected file slice.

No production malware/ClamAV scanner was found in the audited scope. Version
`0.1.0a1` therefore does not claim malware scanning. A future scanner seam must
be sourced from a real adopter and must make quarantine/clear state explicit;
an unchecked `scan_status` column would only manufacture assurance.

## Preserved and rejected behaviour

Preserved in the initial module:

- provider-neutral put/open/exists/delete/list contract and typed failures;
- streaming admission with a configured maximum, immutable generated keys,
  SHA-256, display-only original name, and declared-versus-detected media type;
- PDF, CSV, legacy `.xls`, OpenXML `.xlsx`, PNG, JPEG, GIF and WebP signature
  recognition;
- explicit tenant metadata with same-migration forced RLS, plus a separate
  tenant-free platform table granted to platform roles and revoked from
  `app_user`;
- one persistence-free physical engine selected by required `TenantScope` or
  `PlatformScope`, with disjoint trusted object prefixes;
- pending-first idempotent deletion, missing-object reconciliation, and orphan
  cleanup for an object left behind by a failed DB transaction.

Rejected from the shared owner:

- hard-coded ticket/invoice/subscriber/document type tables;
- polymorphic `entity_type/entity_id`, public flags, domain authorization and
  retention decisions;
- public URL generation as authority (delivery adapters may issue bounded
  signed URLs after domain authorization);
- service-layer commit/rollback and HTTP exceptions;
- PDF business interpretation, spreadsheet column mappings, duplicate policy,
  dry-run/apply, row mutation, and import history.

## Cutover evidence required

The executable sequence and acceptance evidence are recorded in
[`2026-08-13-files-erp-academy-vendor-cp-adoption.md`](../superpowers/plans/2026-08-13-files-erp-academy-vendor-cp-adoption.md).

Michael selected ERP and Academy as the first two adopters; neither is a current
consumer. ERP is cutover 1 because product-first extraction requires a
qualifying source product to retire its local owner first. Before that can
start, ERP's E8 decision must establish the Organization-to-Tenant mapping, one
transaction authority, the `app.current_tenant` RLS contract and composed
module lineages. The cutover then inventories every domain-owned key/path,
migrates each relation to an opaque file UUID, verifies provider key, tenant,
length and SHA-256 parity, shadows authenticated reads and reconciliation, and
retires `file_upload.py` plus the old storage-owner surface.

Academy is cutover 2 on the same exact release. Its avatar slice adds a
tenant-composite file relation, imports and checksum-verifies every legacy
`static/avatars` object, shadows its authenticated read path, switches the
relation to the opaque file UUID, and removes direct disk writes and
`avatar_path` only after the fallback count reaches zero. Sub remains important
source evidence and a future adopter, but it is no longer in the first pair.

Vendor Control Plane is candidate cutover 3 on the platform plane. Its selected
slice preserves the exact offline licence bundle handed to an authenticated
operator only when durable byte-for-byte delivery evidence is required.
`LicenceDelivery` and its attempt log remain the business authority; their
domain-owned relation references `platform_stored_files.id`. Platform adoption
must prove `app_user` has no table privilege, no tenant column or RLS exists,
and the platform provider prefix cannot enumerate or delete tenant objects.

## Per-assembly adoption disposition (2026-08-14)

Measured after the ADR-0006 D1 amendment made prerequisites logical. One row per
assembly, because "can it satisfy the prerequisites?" and "should it install the
module?" are different questions and only the first is mechanical.

| Assembly | Satisfies `tenant_scope_catalog.v1` | Satisfies `module_database_roles.v1` | Disposition |
|---|---|---|---|
| Starter | yes — kernel `0001` | yes — kernel `0001` | Composes it; the reference consumer |
| ERP | yes — `20260813_tenant_projection` | yes — `20260814_database_roles` | Adopting; gated on the Seabone ownership cutover |
| Academy | yes — its own `0001_initial_tenant_schema` | yes — same revision | Eligible; needs kernel `a38 → a56` and its first module composition |
| **Vendor control plane** | **yes, truthfully** | **yes, truthfully** | **Does NOT adopt — see below** |

### The vendor control plane must not adopt `dotmac-files`

This is the case that shows why satisfying a prerequisite is not a reason to
install a module.

Vendor CP composes the kernel base lineage wholesale, so kernel `0001` really
does create `public.tenants`, `tenant_domains`, `app_current_tenant_id()` and the
three roles in the control-plane database. It would therefore pass
`require_prerequisites` **honestly** — no lie, no workaround — and receive a
tenant-scoped `mod_files.stored_files` with FORCE RLS that nothing in a
platform-only product will ever write.

Three facts make "just install the platform half" unavailable today:

1. **`fi_0001_stored_files` is atomic across both planes.** One `upgrade()`, no
   conditional; the platform table cannot be created without the tenant table and
   its foreign key to `public.tenants`.
2. **`mod_files` is allocated to exactly one migration owner** (hard rule 14), so
   a second distribution owning a platform-only files schema is not permitted.
3. **Vendor CP has no stored-byte requirement at all.** Licence envelopes are
   JSONB; offline bundles stream from the response. There is no
   `StorageProvider`, no object-store credential, and no storage configuration
   knob anywhere in the assembly.

Fact 3 is the decisive one. Building a platform-plane contract now would add
kernel surface for a consumer that does not exist, which is precisely what
ADR-0017 forbids during the adoption gate. **A capability nobody needs is not
unblocked by making it installable.**

### What would have to change first

If Vendor CP ever acquires real platform-owned stored bytes — signed artefacts
persisted rather than streamed, evidence bundles retained — then, and only then:

- a `platform_file_store.v1` prerequisite naming only the roles and schema
  (no tenant catalogue), and
- a split of `fi_0001` so an assembly can install the platform plane alone,
  which is a contract change to a released module and needs its own ADR.

Until a second real consumer exists, the honest disposition is this row in this
table, not a mechanism. Recorded so the next reader does not mistake "it would
pass the check" for "it should be installed".
