# Documents and Records product-first source audit

- **Audit date:** 2026-08-19
- **Decision:** ADR-0049
- **Method:** read tracked content at the exact Git revisions below (`git show
  <revision>:<path>` and `git grep <revision>`), so unrelated dirty checkout state
  did not enter the evidence.

## Pinned repositories

| Repository | Audited revision | Documents evidence | Records evidence |
| --- | --- | --- | --- |
| `dotmac_erp` | `0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf` | qualifying HR handbook/version/acknowledgement source; fragmented generated, employee, fleet, discipline and finance attachments | no record declaration, file plan, schedule versions, holds or disposition owner |
| `dotmac_sub` | `91c1ec477b3af37931424bced856a16bbc2c6d3f` | qualifying immutable quote snapshot/content-addressed export delta; mutable LegalDocument anti-source | cleanup TTLs only; no records authority |
| `dotmac_crm` | `c64b5aa0f7902b52e7ef73cf26f3f88687ed849d` | mutable LegalDocument copy and domain attachments; requirement/retirement input only | no records authority |
| `dotmac-academy` | `71b87b2abfb1dc5c1db540faca257004a8c2bf9c` | course/manual content, not controlled-document runtime; the complete tree has no application/runtime package or model candidate | no implementation |
| `dotmac_academy_app` | `40423a07a4eaa6172a36997f3276cb7a79dda343` | generated certificates/reports are domain outputs, not a controlled-document owner | no implementation |
| `dotmac_workspace` | `c72fe304d3c8b2a2741d111379e4c4ab0af5da57` | no implementation | no implementation |
| `dotmac_vendor_control_plane` | `e6b2bbee815cf9fd3ce99ceed0ff3a1f5763f057` | digest-bound contract/licence outputs remain their domain owners; no controlled library/version owner | a design-doc legal-hold mention only; no persistence or service authority |
| `dotmac_starter_mt` | `68939275174885fa9d673c1778e6fd03b11b9f2f` | Files, Approvals, rendering and Durable Timers are existing adjacent owners; no Documents package before this slice | no Records package before this slice |

The Academy partial-clone limitation was closed on 2026-08-23 before its typed
product-writer row was added. The pinned tree contains only training manuals,
their build scripts/templates, figures and script tests; it has no `app/`,
`src/`, database model, migration or runtime service. The two unavailable
promisor objects encountered while checking source-relevant content resolve in
the complete filename tree to `figures/final/FE-08-04.png` and
`templates/dotmac-academy.tex`, neither a runtime implementation. The
separately deployed Academy application was also fully inspected at its own
pinned revision. Academy is therefore valid inventory-only negative evidence
for Records; its manual prose about retention does not become an operational
writer.

## Documents disposition

### Mandatory base: ERP HR handbook

Paths:

- `app/models/people/hr/handbook.py`
- `app/services/people/hr/handbook_service.py`
- `alembic/versions/20260128_add_hr_handbook_models.py`

Behavior worth porting:

- stable `document_code` across numbered versions;
- previous-version relationship and one active issue;
- checksum, byte length, media type, effective/expiry dates;
- exact-version employee acknowledgement evidence;
- tags and policy applicability metadata.

Corrections required during extraction:

- query-then-increment version allocation is not concurrency safe;
- a “version” row also acts as stable identity, so metadata and state mutate in
  place;
- paths own bytes instead of opaque Files ids;
- `date.today()`/`datetime.now()` make lifecycle decisions ambient;
- activation is not exact-content approval-bound;
- the four-state HR vocabulary is insufficient for controlled review.

ERP `GeneratedDocument`, `EmployeeDocument`, `VehicleDocument` and
`CaseDocument` prove broad demand but are not additional owners to merge. They
are domain attachment/cutover surfaces.

### Mandatory delta: Sub quote exports

Paths:

- `app/services/sales/quote_documents.py`
- `alembic/versions/471_quote_documents_and_delivery.py`
- `tests/test_quote_documents_and_delivery.py`
- `tests/architecture/test_quote_document_delivery_boundary.py`

Behavior worth porting:

- typed immutable source snapshot at the generation boundary;
- stable fingerprint and content-addressed replay;
- opaque stored-file relationship;
- exact artifact audit evidence;
- owner-command boundary separating generation from delivery.

The quote's business meaning, renderer HTML/payment details and delivery remain
Sub/domain/renderer/delivery concerns. They do not enter Documents.

### Explicit anti-sources

Sub and CRM `app/models/legal.py` + `app/services/legal.py` store a version
string on one mutable row, replace file bytes in place and expose delete paths.
ERP `GeneratedDocument` stores a path and mutable status. These paths become
retirement inputs, not copied implementations.

**Documents ruling:** `product-first`, with an ERP base and Sub behavioral
delta. ERP is candidate cutover 1; Sub is candidate cutover 2.

## Records disposition

Exact searches covered `legal_hold`, retention schedules/rules, record
declaration, disposition, fixity, preservation copies/checks and custody
transfer. Matches outside this new module were:

- operational cleanup TTLs (Sub/CRM/ERP tasks and account deletion);
- HTTP `Content-Disposition` and UI layout terms;
- AI/content preservation prose;
- Vendor CP design prose saying legal hold is out of its licensing domain.

No product has all—or a qualifying production-used subset—of:

- exact immutable artifact declaration;
- immutable series and retention-schedule versions;
- typed authoritative trigger observations and deterministic cutoff;
- multiple simultaneous holds with independent release;
- batch membership + exact-content disposition approval;
- conditional destructive recheck and Files confirmation;
- fixity/custody evidence.

ERP's HR acknowledgement/version behavior and Sub's immutable quote snapshot are
declaration inputs, not a Records implementation.

**Records ruling:** `greenfield-after-inventory`. ADR-0049 sets the necessary
foundation scope; the audit answers implementation sourcing, not scope.

## ERP Records-writer follow-up — 2026-08-23

The focused retention/deletion/hold inventory required by the Records dossier
was completed against the same immutable ERP revision,
`0f4b1698ddbf27a04f4562ecdaf8b93f19c3debf`. Searches covered retention and
expiry fields, purge/cleanup/delete consequences, legal holds, record series,
preservation, fixity, custody and disposition, then read every source-relevant
match rather than classifying by name.

One overlapping writer exists:

- `app/models/finance/rpt/report_schedule.py` stores a mutable
  `retention_days` beside a generated report's storage path.
- `app/services/finance/rpt/report_scheduler.py` writes and later overwrites
  that policy in `create_schedule` and `update_schedule`; there is no immutable
  schedule version or exact record declaration.
- `app/services/finance/rpt/report_instance.py::cleanup_old_instances` deletes
  terminal report-instance rows by an ambient wall-clock cutoff and commits
  internally. Repository-wide caller search found no caller, so this is a
  dormant destructive consequence, not evidence that physical stored output is
  deleted or that production retention is enforced.

ERP is therefore a `legacy_writer`, not a qualifying Records source and not an
`inventory_only` product. A Records adoption for generated finance reports must
replace the mutable policy with an exact declaration and versioned schedule,
characterize whether terminal rows and stored output currently diverge, keep
destruction disabled during shadowing, and retire both the field writer and
dormant deletion path only after an authorized cutover.

The other deletion matches are explicitly outside this owner: notification,
published-outbox, service-hook and completed-saga cleanup are operational-store
TTL policies owned with those runtimes; feature-flag expiry is lifecycle;
procurement `retention_percentage` is withheld commercial value; and HTTP
`Content-Disposition` is presentation. No legal-hold, record-series,
preservation/custody or disposition implementation exists. Those exclusions
prevent Records from becoming a generic garbage collector for every table with
an expiry.

## Ownership and composition map

| Decision/fact | Owner |
| --- | --- |
| stable controlled identity, immutable version, current pointer, lifecycle | Documents |
| attachment/domain meaning before declaration | source domain |
| record declaration, schedule, hold, preservation, disposition | Records |
| opaque bytes/checksum/physical object state | Files |
| exact-content approval verdict | Approvals |
| scheduled wake-up mechanics | Durable Timers |
| billing HTML/PDF | Document Rendering |
| OCR/scanner/e-signature provider I/O | Integrator/stateless adapter |
| full-text index | rebuildable search projection |
| transport/delivery | Delivery/Integrator |

Documents and Records store opaque ids/checksums and never import sibling
models or create cross-lineage FKs. A product assembly calls both services or
synchronizes versioned observations. A declared Documents version is one valid
Records source shape, not a special database relationship.

## Cutover evidence required

Documents/ERP must backfill stable document + immutable version rows, map Files
ids/checksums, compare version/current/effective/acknowledgement facts, seal the
authority switch under the legacy writer lock, then remove the local lifecycle
writer. Records/ERP must first enumerate every retention/deletion/hold writer,
shadow declarations and eligibility without consequences, prove zero
unexplained drift, switch authority once, and retire those writers. Neither
module may remain a fallback to a legacy owner.
