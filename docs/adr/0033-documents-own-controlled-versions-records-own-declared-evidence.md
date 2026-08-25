# ADR-0033: Documents own controlled versions; Records own declared evidence

**Status:** Accepted  
**Date:** 2026-08-19  
**Decision owner:** Michael  
**Relates to:** ADR-0006 (product-first modules and independent lineages),
ADR-0014 (one at-most-once owner), ADR-0022 (Files own bytes), ADR-0024
(applications synchronize data), ADR-0026 (approval never performs the subject
transition), ADR-0031 (authority cutover evidence)

## Context

The fleet has document-shaped rows but no reusable document-management or
records-management owner. ERP's HR handbook path has stable codes, sequential
versions, checksums, effective dates and exact-version acknowledgements. Sub's
quote export freezes a typed content snapshot and content-addresses the derived
artifact. ERP's other attachments and Sub/CRM's `LegalDocument` rows replace or
delete content in place. No audited product implements version-pinned retention
schedules, multiple legal holds, preservation evidence or conditional
disposition.

Calling the stateless billing renderer `dotmac-documents` would over-claim this
authority. Leaving retention in every source domain after creating a generic
Records module would create parallel compliance writers. Merging controlled
working documents and declared records into one aggregate would make every
document a record and give Records authority before declaration.

The pinned audit and source ruling are in
[`documents-records-sources.md`](../inventories/documents-records-sources.md).

## Decision

### 1. One product surface presents two independent owners

Products may present a single “Documents & Records” navigation/product surface,
but install two packages and two Alembic lineages:

| Owner | Permanent identity | Decision |
| --- | --- | --- |
| `dotmac-documents` | `documents / mod_documents / dc / documents` | controlled document identity, immutable versions, exact-version renditions, document lifecycle, checkout, document-scoped collaboration/access and acknowledgements |
| `dotmac-records` | `records / mod_records / re / records` | declaration snapshots, immutable file-plan/schedule versions, trigger calculation, holds, preservation/custody and disposition |

Both are tenant-only in revision 1. No named product has a platform-plane
Documents or Records need today. Each table has `tenant_id UUID NOT NULL`,
tenant-composite identities and forced RLS. The packages import the kernel and
no product or sibling module; a product assembly composes typed calls and
versioned API/webhook observations.

### 2. Documents owns controlled working identity and exact versions

A `Document` is stable identity independent of filename and folder path.
`DocumentVersion` is immutable and binds explicit authoring time/actor/change
reason and provenance to an opaque Files UUID, SHA-256, media type and byte
length. Version allocation locks the stable document row, checks the caller's
expected current version, allocates the next major/minor pair and advances one
canonical current pointer in the same transaction. Corrections add versions;
they never rewrite old content. The pointer's composite foreign key includes
the stable document id, so it cannot identify another document's version even
inside the same tenant.

Document-type versions pin required metadata and the permitted lifecycle graph.
Documents alone performs `draft → in_review → approved → effective → ...`
transitions. An approval is accepted only when its subject and digest identify
the exact current version; the verdict does not transition the document.
Effective/review times are inputs. Scheduled wake-ups leave as typed Durable
Timer requests bound to the exact version id/checksum; accepted wake-ups must
present that expected version before Documents applies the transition. There is
no due-row clock scan.

Renditions bind an exact source-version checksum to their own opaque Files UUID,
checksum and renderer/extractor version. They never replace the source. The
billing-specific `dotmac-document-rendering` contract remains unchanged.

### 3. Records authority starts only at explicit declaration

A record declaration freezes source owner/type/opaque id/exact version,
optional file UUID/checksum/media/length, series version, retention-schedule
version, declaration actor/time, authority/provenance, sensitivity, restrictions
and required metadata. The source domain keeps business meaning. Every record
has a source artifact; not every source artifact or Document is a record.

Schedule/series definitions are immutable versions. A record pins both.
Publishing a new schedule does not mutate existing obligations. Reassignment,
when added, must be an explicit evidenced Records decision rather than an
update to the pinned columns.

Typed trigger observations are deduplicated by source owner/event identity and
fingerprint. Records accepts only the event type named by the pinned schedule,
calculates dates deterministically and returns a Durable Timer request. It never
infers closure from a date or document body.

### 4. Holds dominate every ordinary consequence

Hold targets identify an exact record, pinned series version or snapshotted
cohort. Multiple holds coexist. Eligibility counts every active target; release
of one target cannot remove another. A hold blocks disposition, physical
deletion, ordinary retention expiry and any future redaction execution. An
ongoing capture rule is case evidence evaluated by a product adapter; it is not
an unbounded ambient query inside Records.

### 5. Disposition is conditional and Files never decides it

A batch freezes item membership and an eligibility fingerprint. Approval binds
the batch digest. A schedule whose approval-policy code contains the declared
four-eye requirement refuses approval by the batch creator. Immediately before
execution, Records rechecks the pinned
schedule, source-state observation and all holds. For destruction it returns an
opaque authorization carrying exact record/file/checksum identity.

`dotmac-files` performs provider deletion only after consuming that
authorization. Records marks the record destroyed only after Files returns a
matching `purged` confirmation. The final event/certificate preserves identity,
checksum, authority and physical-state evidence—not the destroyed content.

### 6. Search, OCR, delivery and provider systems remain projections/transports

Search indexes, OCR text, previews and renderer outputs are rebuildable
observations or renditions. Integrator connectors own scanners, email capture,
OCR/e-signature provider calls and external transport. Delivery owns external
delivery. Inbox owns general conversation. None decides document lifecycle,
record declaration, retention, holds or disposition.

## Product-first ruling and cutover

Documents is product-first: port ERP HR handbook version/effective-date/
acknowledgement behavior and Sub quote-export immutable snapshot and replay
behavior, correcting ERP's query-then-increment race, ambient `date.today()` and
in-place metadata mutation. ERP is the first candidate cutover after its E8
tenant/session/lineage gate; Sub is the second independent consumer candidate.

Records is greenfield-after-inventory. ERP, Sub, CRM, Backoffice, Academy,
Workspace and Vendor Control Plane contain no qualifying authority. ERP is the
first candidate declaration consumer only after a checked-in inventory of its
existing retention/deletion writers. The authority switch must shadow and then
retire each old retention/hold/disposition writer under ADR-0031; the module is
not a parallel fallback.

## Consequences

- The Files amendment is explicit: undeclared attachments remain source-domain
  responsibility; declared records have one compliance writer.
- Documents and Records can release and adopt independently even when a product
  presents them together.
- Revision 1 establishes the full authority spine and persistence vocabulary.
  Provider adapters, web routes, full-text indexing and delivery surfaces are
  consumer work and cannot migrate decisions out of the two owners.
- Both dossiers remain `audit-complete`, not adopted, until a real product pins
  the packages, migrates rows, proves zero unexplained drift and retires the
  prior writer.
