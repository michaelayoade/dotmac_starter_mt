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
| `dotmac_backoffice` | `fcdd8270262dea2a78d0d4d8c4116c1e8b7b3b2d` | no implementation | no implementation |
| `dotmac-academy` | `71b87b2abfb1dc5c1db540faca257004a8c2bf9c` | course/manual content, not controlled-document runtime; the local partial clone lacked one promised blob, but the tracked path census exposed no runtime package/model candidate | no implementation |
| `dotmac_academy_app` | `40423a07a4eaa6172a36997f3276cb7a79dda343` | generated certificates/reports are domain outputs, not a controlled-document owner | no implementation |
| `dotmac_workspace` | `c72fe304d3c8b2a2741d111379e4c4ab0af5da57` | no implementation | no implementation |
| `dotmac_vendor_control_plane` | `e6b2bbee815cf9fd3ce99ceed0ff3a1f5763f057` | digest-bound contract/licence outputs remain their domain owners; no controlled library/version owner | a design-doc legal-hold mention only; no persistence or service authority |
| `dotmac_starter_mt` | `68939275174885fa9d673c1778e6fd03b11b9f2f` | Files, Approvals, rendering and Durable Timers are existing adjacent owners; no Documents package before this slice | no Records package before this slice |

The Academy partial-clone limitation is non-blocking for the source ruling: its
available tree identifies manuals/content rather than an application-level
document/records aggregate, while the separately deployed Academy app was
fully inspected. Academy remains a candidate consumer only after a future
runtime inventory; it is not credited as negative proof for Records by itself.

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
