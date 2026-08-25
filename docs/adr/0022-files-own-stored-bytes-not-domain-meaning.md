# ADR-0022: Files own stored bytes, not domain meaning

**Status:** Accepted  
**Date:** 2026-08-13  
**Decision owner:** Michael  
**Relates to:** ADR-0006 (module extraction and independent lineages), ADR-0010
(thin adapters), ADR-0014 (transactional versus external effects), ADR-0017
(adoption is the scarce resource), ADR-0018 (enforceable guards), ADR-0023
(dual-plane modules), ADR-0024 (applications synchronize data; module
installations do not share rows)

## Amendment, 2026-08-19: a declared record transfers compliance authority

ADR-0033 narrows § 2's statement that every source domain owns legal hold and
retention. The source domain owns attachment meaning, access and retention
until it explicitly declares one exact immutable artifact as a managed record.
From declaration onward, `dotmac-records` is the sole writer of retention,
hold, preservation and disposition state for that record. The source domain
retains business meaning and must consult Records before deletion.

This is an authority handoff, not shared policy. `dotmac-files` still owns the
physical object lifecycle and cannot decide whether content may be destroyed.
Records issues a digest-bound deletion authorization only after deterministic
eligibility, approval and hold rechecks; Files performs physical deletion;
Records records final evidence only after Files confirms the resulting state.
Attachments never declared as records remain under the source domain's
retention and access authority exactly as this ADR originally decided.

## Amendment, 2026-08-13: one physical lifecycle, two persistence planes

ADR-0023 supersedes this ADR's original tenant-only persistence shape. The
ownership decision is unchanged: `dotmac-files` still owns stored bytes and
their repairable physical lifecycle, while domains own what a file means and
who may use it. The module now serves two real security contexts without
inventing a nullable or sentinel tenant:

| | Tenant plane | Platform plane |
| --- | --- | --- |
| model/table | `TenantStoredFile` / `stored_files` | `PlatformStoredFile` / `platform_stored_files` |
| scope | required `TenantScope(tenant_id)` | required `PlatformScope()` |
| object prefix | `tenants/<tenant-id>/files/` | `platform/files/` |
| isolation | `tenant_id NOT NULL`, composite constraints, forced RLS | no `tenant_id`, no RLS, platform grants, `REVOKE ALL` from `app_user` |

Both planes use the same persistence-free physical engine for validation,
signatures, checksums, immutable keys, streaming, provider actions and boundary
checks. Database lifecycle functions require a kernel `Scope` and dispatch to
one explicitly declared table. No row, foreign key or object prefix crosses the
planes, and `ModuleManifest.platform_tables` makes the platform classification
an audited declaration rather than an inference from a missing column.

Vendor Control Plane is candidate cutover 3 after ERP and Academy. Its first
coherent slice is the exact offline licence bundle handed to an authenticated
operator: licensing owns issuance, export authorization, delivery attempts and
the bundle-to-file relation; `dotmac-files` owns only the immutable bytes and
physical state. The stored copy is adoption only when Vendor CP actually needs
the exact handed-off artifact as durable evidence. Regenerating deterministic
bytes and storing them merely to count a consumer is prohibited.

This amendment raises the module's kernel floor from the namespace-allocation
release `0.1.0a53` to `0.1.0a54`, where declared platform tables and their
live-catalog contract became supported.

## Amendment, 2026-08-13: adopters share the contract, never storage authority

ADR-0024 applies to files as strongly as it applies to tickets. Each adopter
installs its own `fi` lineage, owns its own metadata rows, supplies its own
configured provider binding and authorizes reads locally. Two applications do
not share a `mod_files` schema, object key authority or provider credential.

If one application needs file metadata or bytes held by another, it obtains
them through that application's versioned API/webhook contract. A local copy is
an explicitly provenance-bearing projection with its own retention/repair
policy; it is not a direct read of the other application's database or object
prefix. Provider-specific SDK and wire mapping remain in the consuming
assembly's `StorageProvider` adapter. `dotmac-files` contains no product or
provider branch.

## Context

Starter has `python-multipart` because its web assembly can decode form bodies,
but it has no owner for object keys, validation, metadata, streaming, deletion,
or drift repair. ERP, Sub and CRM each built overlapping storage services, while
each product also mixed in its own attachment and import semantics. Keeping the
gap in the kernel would make every deployment inherit stateful object-storage
behaviour; copying one product service into Starter would create a fourth owner.

The product-first inventory in
[`files-sources.md`](../inventories/files-sources.md) found a qualifying source:
Sub has the strongest provider and staged lifecycle contract, ERP has the
strongest admission policy coverage, and CRM has the strongest MIME-spoofing
proof. It also found that storage and import are not one capability.

## Decision

### 1. `dotmac-files` is the optional physical-file owner

It owns one opaque tenant-scoped file identity, a provider binding and trusted
immutable object key, original filename as display metadata only, byte length,
declared and detected media types, SHA-256, and the repairable physical states
`available`, `missing`, `deletion_pending`, and `purged`.

Its provider contract owns put, streamed open, existence, idempotent deletion,
and tenant-prefix listing. Vendor SDKs and credentials remain provider adapters
installed by the consuming assembly; the module does no network work at import
time and the kernel gains no object-store behaviour.

Every row has `tenant_id UUID NOT NULL`, composite tenant uniques, and RLS
ENABLEd and FORCEd in the same `fi` lineage migration. The allocation is
`files / mod_files / fi / files` in the kernel's immutable namespace ledger.
The package is non-core and is built/tested here without being composed into the
Starter assembly.

### 2. Domains own attachment meaning and authorization until record declaration

A ticket, invoice, subscriber, work order, message, or import run stores an
opaque file UUID and owns its relation, visibility, permissions, retention and
audit vocabulary while it remains an ordinary domain attachment. An explicit
record declaration transfers only retention, hold, preservation and
disposition authority to `dotmac-records`; it does not transfer invoice,
employee, contract, ticket or work-order meaning. `stored_files` has no
polymorphic entity columns, public flag, domain ID, or generated public URL.

The application/assembly adapter authorizes the domain operation, validates or
loads the file through `dotmac-files`, and delegates to the domain service.
Modules remain independent; a domain module does not import this module merely
to make a foreign key. Composition can enforce a tenant-composite reference in
the consuming product's migration where the product owns both installed
lineages.

### 3. Admission is not parsing

`dotmac-files` recognizes PDF, CSV, `.xls`, `.xlsx`, PNG, JPEG, GIF, and WebP
well enough to enforce size, extension, declared MIME, and content-signature
policy. This answers whether bytes may enter storage; it does not interpret
them. SVG is deliberately absent from the first contract because it is active
XML content, not an equivalent passive avatar format.

- Generic PDF text/table extraction and spreadsheet row reading belong in
  typed parser adapters used by a future `dotmac-imports` owner.
- Mapping columns, selecting a domain schema, duplicate policy, row validation,
  dry-run/apply, row outcomes, and mutations belong to `dotmac-imports` and the
  importing domain.
- Generated PDFs and documents belong to their rendering/document domain; the
  result may be stored through `dotmac-files` after it is generated.

The later import dossier must start from Sub's durable import-run ledger and
ERP's CSV/XLS/XLSX parser tests. It must not reopen the file lifecycle or create
a second stored-byte owner.

### 4. External effects are repairable, never presented as atomic

An object store cannot participate in PostgreSQL commit. Upload writes a new
immutable tenant key, then stages metadata in the caller's transaction. A failed
transaction may leave an unreferenced object; the tenant-scoped orphan
reconciler removes it after an age threshold. It cannot leave committed
metadata pointing at a partial overwrite because keys are immutable.

Deletion first commits `deletion_pending`. A later worker/reconciler performs
the idempotent provider delete and records `purged`; a crash is a retry, not a
stuck reservation. Missing-object reconciliation preserves metadata and records
the observation rather than silently hard-deleting evidence. Services flush but
never commit or roll back.

### 5. ERP is cutover 1 and Academy is cutover 2; neither is claimed yet

Michael selected ERP and Academy as the first two adopters on 2026-08-13. ERP
goes first because it is a qualifying implementation source: its production S3
service, file-policy table, image/document signatures, and callers supplied
material parts of this contract. The product-first procedure requires the
source product to retire its local owner before a second independent consumer
is counted.

ERP is currently blocked at its already-recorded E8 boundary: its
Organization-to-Tenant mapping, transaction authority, RLS GUC, and composed
lineage decision are not complete. `dotmac_kernel.db` and stateful module
lineages remain `defer-db` in ERP's adoption ledger. The files cutover must not
bypass that gate with a second session factory, a parallel tenant writer, or a
copied migration. After E8, ERP composes the released `fi` lineage, ports its
MinIO service behind the provider protocol, migrates domain-owned path/key
references to opaque file ids, proves key/size/checksum/read parity, and retires
the generic local lifecycle owner.

Academy follows on the same exact release. Its coherent first slice is account
avatars: replace direct `static/avatars` writes with a local provider adapter,
add a domain-owned opaque avatar file relation, backfill and checksum-verify
existing paths, shadow authenticated reads, then remove `avatar_path` and the
legacy files after the fallback count reaches zero. `contract_consumers`
remains empty until those cutovers are real.

### 6. ADR-0017 is narrowed explicitly, not bypassed implicitly

Object storage and import/export were both named in ADR-0017's moratorium.
Michael's 2026-08-13 direction to proceed with `dotmac-files` explicitly lifts
that moratorium for this one optional module after the product inventory and
owner split above. It does **not** declare the kernel adoption gate met, does
not add a kernel facility, does not count ERP or Academy as an adopter, and does
not lift the hold for import/export or any other gap-list item.

## Consequences

- Products can share validation and physical lifecycle without giving a generic
  service authority over domain attachments.
- A MinIO/S3 adapter is still required in an adopting assembly. Credentials are
  assembly configuration/secret sources and never stored in file metadata.
- Malware scanning is not claimed in `0.1.0a1`; the audit found no qualifying
  source. It requires a real scanner adapter and explicit quarantine lifecycle.
- PDF/XLS/XLSX and passive avatar-image admission work now; semantic
  extraction/import and image transformation remain separate work with their
  own owners and transaction models.
- Until ERP passes E8 and cuts over, the package is correctly `audit-complete`,
  not adopted; Academy cannot be counted ahead of the source-product
  retirement.
