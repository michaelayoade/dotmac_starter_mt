# Document rendering — extraction dossier

> **Implementation addendum — 2026-08-19.** ADR-0017 P11 is now met and
> ADR-0030 § 6 explicitly authorizes `dotmac-document-rendering`. The Sub and
> ERP audits are complete, and the machine-checked dossier now lives at
> `packages/dotmac-document-rendering/EXTRACTION.toml` beside the stateless
> `0.1.0a1` package. This file remains the pre-implementation reasoning record;
> where its old moratorium/status wording conflicts with that package dossier,
> the package dossier and accepted ADRs are current.

Dated 2026-08-14. **Dossier CONTENT, not a dossier.** There is no package
directory, no `EXTRACTION.toml` at a package root, no module, no model, no
migration and no namespace allocation. Document rendering is capability area
**P8a**, recorded as gap-listed in `docs/inventories/billing-sources.md` § 3,
and ADR-0017 decision 2's moratorium holds it. ADR-0020 § 6 and the *"What this
amendment does not do"* paragraph of its 2026-08-14 amendment both restate that
no package, namespace, lineage or dossier lands until the gate opens.

This document exists so that when the gate does open, the TOML is a
transcription rather than a rediscovery. The field names below are exactly
those of `packages/dotmac-files/EXTRACTION.toml`.

Evidence: `docs/inventories/document-rendering-sources.md`.
Contracts: `docs/superpowers/specs/2026-08-14-document-rendering-contracts.md`.

**Honesty note on `status`.** `audit-complete` records that the audit of the
**named source repository is complete** — `dotmac_sub` was read in full for
this capability. `dotmac_erp`'s `DocumentGeneratorService` and
`GeneratedDocument`, which
`docs/inventories/template-studio-source-audit.md` names as document
generation's qualifying source with 10 integration tests, have **not** been
read. That is the first `next_action`, and it is not glossed over. `status` is
never `approved`; nothing here is approved.

---

## 1. Does this module need persistence at all?

**Recommendation: no. Ship it stateless.** This is the load-bearing
recommendation of the dossier, so the argument is given in full rather than
asserted.

### 1.1 Rendering is a pure function of an immutable fact

`InvoiceDocumentFactV1` is immutable and complete by contract (spec § 3): the
renderer's only inputs are the fact, a template version and a renderer version.
A pure function has nothing to persist. If the fact is *not* sufficient, the
correct repair is to fix the fact — which is producer-side work in billing
(spec § 8, R1–R3) — not to give rendering a table to compensate with.

### 1.2 The provenance already has two durable owners

A render's provenance is fully covered without a third writer:

| Provenance | Owner | Fields |
|---|---|---|
| Physical | `dotmac-files` (ADR-0022) | provider code, immutable storage key, byte length, declared + detected media type, SHA-256, physical state `available/missing/deletion_pending/purged` |
| Semantic | the official-artifact relation, recorded by billing (spec § 6) | `renderer_code`, `renderer_version`, `template_code`, `template_version`, `projection_contract_version`, `projection_digest`, `checksum_sha256`, `byte_length` |

ADR-0022 § 2 already assigns the relation: *"A ticket, **invoice**, subscriber,
work order, message, or import run stores an opaque file UUID and **owns its
relation**, visibility, permissions, legal hold, retention rule, and audit
vocabulary."* A third table in the rendering module would be a **second writer
of the same provenance**, which is the failure the source-of-truth standard
exists to prevent.

### 1.3 A render-job table would be a second at-most-once owner

Hard rule 23 and ADR-0014: *"`dotmac_kernel.idempotency` owns the ledger, the
engine, the conflict rule and the retention sweep; `messaging.process_once` …
are adapters over it, not a second mechanism."* A render-job table with
`queued/processing/completed/failed` is exactly a second mechanism.

Sub has that table, and it has exactly the failure ADR-0014 predicts. Nothing
reserved-before-the-effect is safe: `InvoicePdfExport` moves to `processing`
and commits **before** the render runs
(`app/services/billing_invoice_pdf.py:1353-1356`), so a crashed worker leaves a
`processing` row nothing finishes. The workaround is
`maybe_finalize_stalled_export` plus `STALE_EXPORT_SECONDS = 20`, a wall-clock
heuristic that guesses whether someone else is still working. ADR-0014's whole
point is that there is *"no 'in progress' state to get stuck in"*.

The correct shape needs no table: `dotmac_kernel.idempotency` with scope
`document.render` and a key over `(invoice_id, fact_version, media_type,
template_code, template_version, renderer_code, renderer_version)`, plus
`dotmac_kernel.messaging.enqueue_event` for the post-commit trigger. Both
already exist and are already adopted surface, so using them is not new
facility work under ADR-0017.

### 1.4 Statelessness is the *enforcement mechanism* for two invariants

This is the argument that makes it a recommendation rather than a preference.

- **"Rendering failure never rolls back the issued invoice."** A module that
  imports no `sqlalchemy`, no `dotmac_kernel.db` and holds no `Session`
  **cannot open a transaction, and therefore cannot roll one back.** That is a
  structural guarantee. A stateful renderer can only offer a test.
- **"A stored file never becomes the only copy of invoice truth."** A renderer
  with no storage cannot be a copy of anything. The repair path (re-render from
  the fact) is then the *only* path, so it cannot rot unused — which is exactly
  how Sub's regeneration path became untested (`is_export_cache_valid` has zero
  test references).

### 1.5 A stateless module cannot repeat the ADR-0023 blocker

ADR-0023 exists because `dotmac-ticketing 0.1.0a1` *"was built tenant-only"*
and its named cutover-2 adopter, the vendor control plane, is platform-only —
*"a **current adoption blocker**, and it was found the same way the fleet finds
most of them: a module was designed against the security context of the
product that happened to be audited first."*

ADR-0020 A6 makes the vendor control plane a **platform-plane** billing
adopter and ADR-0020 § 6 makes it the **recommended first billing adopter**,
because it is greenfield on invoicing. A stateless renderer has no plane to get
wrong: `scope` is a value on the contract (`TenantScope | PlatformScope`,
`dotmac_kernel.cache`), carried and echoed, never a column. The module that
cannot repeat ADR-0023's mistake is the one with no tables.

### 1.6 A named reconciler is what makes statelessness *sufficient*

The objection a stateful design raises is *"then what remembers whether this has
been done?"*

The answer is the assembly-owned **`InvoiceArtifactReconciler`**
(contracts spec § 6.7–6.12), which reads issued billing fact versions lacking a
valid official artifact, invokes the stateless renderer, stores bytes through
`dotmac-files`, and records the relation through a **typed billing command** —
writing no module table directly. It converges **from state**, on a schedule.
Billing's `document.fact.issued` outbox event is a wake-up signal that makes
convergence earlier; it is never the mechanism.

That splits into three acts with three owners: the assembly **decides** to
establish the relation, billing **records** it, the renderer **produces** the
bytes. The renderer participates in exactly one of the three, and the durable
"has this been done?" lives in the relation row plus
`dotmac_kernel.idempotency` — both already existing, already adopted surface.

So the reconciler is not something statelessness has to survive. It is what
removes the last reason to build the render-job table § 1.3 rejects.

**Required before Vendor CP cutover**, together with its suppressed-event
canary — because Vendor CP is greenfield on invoicing and will be the first
deployment where an issued invoice can exist with no artifact, and the first
where a convergence bug has no legacy path to hide behind.

### 1.7 And it costs nothing to be wrong later

Adding persistence to a stateless module is additive. Removing it from a
stateful one requires a lineage retirement. Under a moratorium whose exit is
*"the kernel's migration lineage runs in a product database in production"*,
proposing a new lineage is the most expensive possible shape and the one
ADR-0017 most directly refuses. ADR-0006 § 5 refuses speculative second-plane
work on top.

### 1.8 The dual-plane position, if 1.1–1.7 are ever wrong

**Recorded so it is decided in advance, not improvised.** If a named adopter
demonstrates that rendering genuinely must persist — the only candidate the
audit can imagine is a *render-attempt evidence ledger* an auditor demands, and
even that is better answered by the audit facility — then ADR-0023 applies
without exception and without negotiation:

- **One persistence-free behaviour engine.** Selection, projection
  construction and formatting import no persistence at all. If the engine
  imports persistence, the "one behaviour" claim is false and a product cannot
  reuse the guards on the other plane.
- **Two declared planes, declared on the manifest and never inferred.**
  `ModuleManifest.tables` for the tenant plane and
  `ModuleManifest.platform_tables` for the control plane, held to their own
  contracts by the live-catalog gate.

| | tenant plane | platform plane |
|---|---|---|
| `tenant_id` | `UUID NOT NULL` | **absent** |
| isolation | RLS ENABLEd **and** FORCEd, tenant policy, in the same lineage migration | no RLS; `REVOKE ALL` from the tenant app role across **every table and column privilege**; schema `USAGE` + row DML granted to the online platform role |
| uniqueness | composite, includes `tenant_id` | control-plane-wide |
| link helper | tenant-scoped, composite FK | no tenant column, single-column FK, revoke |

- **No foreign key crosses the planes**, in either direction.
- **All four ADR-0023 rejected workarounds are refused by the gate**: a
  nullable `tenant_id`, a sentinel or "fake" tenant, a polymorphic
  `scope_kind` + nullable `scope_id`, and a second module. `platform=True` is
  not a plane declaration.
- A stateful module would then also need a `mod_<short>` schema and one
  migration lineage in the kernel's immutable namespace ledger (hard rule 14).
  **No allocation is proposed and the ledger must not be touched**, because a
  namespace allocated for a module that turns out not to need one is
  immutable debt.

### 1.9 What tenant/platform sameness means for a stateless renderer

ADR-0023's substantive requirement — *"tenant and platform document generation
use the SAME behaviour"* — is satisfied here in the strongest available form:
there is **one** code path, and `scope` is data. The contract suite runs every
case twice, once under `TenantScope(uuid4())` and once under `PlatformScope()`,
and asserts the `projection_digest` is **identical** for facts that
differ only in scope. A renderer that branches on scope would fail that test,
which is the sensitivity proof that the sameness claim is checked rather than
asserted.

---

## 2. Dossier content

```toml
schema_version = 1
package = "dotmac-document-rendering"
classification = "optional-module"
status = "audit-complete"
source_mode = "product-first"
owner = "Deterministic production of issued-document bytes from immutable, versioned document facts, together with the canonical semantic projection that makes determinism assertable, and template/renderer version provenance. STATELESS by recommendation: no tables, no migration lineage, no namespace allocation, no session. Renders invoice, credit-note and receipt documents; statements are deferred pending a period-fact producer contract. It does NOT own the official-artifact relation (billing records it), the act of converging facts to artifacts (the assembly's InvoiceArtifactReconciler), or the bytes at rest (dotmac-files)."
contract = "Select a versioned template artifact from the document profile a fact was issued under through assembly-declared bindings, build a pure DocumentProjectionV1 from that fact alone -- normalized sections, values, labels, ordering and the template decisions taken, with formatting applied and volatile fields structurally absent -- and produce bytes with a declared media type, exact byte length and SHA-256, plus renderer/template versions, the independently versioned projection digest, and a stable error classification. Determinism is asserted on the projection digest and never on PDF bytes or extracted text; the byte checksum serves storage integrity, which is a different job. NOT: recalculating totals, tax, FX, balance or coverage; changing invoice lifecycle or payment status; allocating invoice, credit-note or receipt numbers independently; importing billing models or querying billing tables; storing bytes through a second storage implementation; recording which artifact is official; sending email or calling an external delivery provider; treating Template Studio as the invoice-content authority."
source_repositories = [
  "dotmac_sub",
  "dotmac_erp",
]
source_paths = [
  "dotmac_sub:app/services/sales/quote_documents.py",
  "dotmac_sub:app/services/billing_invoice_pdf.py",
  "dotmac_sub:app/services/billing_payment_receipts.py",
  "dotmac_sub:app/services/display_format.py",
  "dotmac_sub:app/services/web_billing_statements.py",
  "dotmac_erp:app/services/document_generator.py",
  "dotmac_erp:app/models/automation/generated_document.py",
]
preserved_tests = [
  "dotmac_sub:tests/test_billing_invoice_pdf_storage.py",
  "dotmac_sub:tests/test_quote_documents_and_delivery.py",
  "dotmac_sub:tests/test_customer_portal_billing_routes.py",
  "dotmac_sub:tests/test_display_format.py",
  "dotmac_sub:tests/test_billing_statement_service.py",
  "dotmac_sub:tests/architecture/test_quote_document_delivery_boundary.py",
  "dotmac_erp:tests/integration/services/test_document_generator.py",
]
contract_consumers = []
candidate_consumers = ["dotmac_vendor_control_plane", "dotmac_sub"]
composition_boundary = "ADR-0020 A1 and ADR-0024 section 2: rendering, billing and dotmac-files are peers over dotmac-kernel, wired by the consuming assembly and never by a Python import. Three acts, three owners: the assembly's InvoiceArtifactReconciler DECIDES to establish the official-artifact relation, billing RECORDS it through a typed idempotent command in billing's own tables on billing's own declared planes, and the stateless renderer PRODUCES the bytes. The assembly finds gaps through billing's published read contract rather than a table read, persists bytes through dotmac-files after binding them to the render checksum, and writes no module table directly. Rendering imports no billing package, no dotmac_files, no sqlalchemy, no dotmac_kernel.db and no delivery client. A rendering ENGINE behind the DocumentRenderer port is a typed resource driver used locally by one owning module, not an Integrator connector (ADR-0024 section 7, closing paragraph); it carries no product-domain payload and no cross-application authority. Two applications never share a rendered artifact: each installs its own copy, renders from its own facts, and obtains another application's document only through that application's versioned API."
inventory_evidence = [
  "docs/inventories/document-rendering-sources.md",
  "docs/superpowers/specs/2026-08-14-document-rendering-contracts.md",
  "docs/inventories/template-studio-source-audit.md",
  "docs/inventories/billing-sources.md",
  "docs/adr/0020-billing-owns-operational-receivables.md",
  "docs/adr/0022-files-own-stored-bytes-not-domain-meaning.md",
]
first_cutover = "NONE, and none may be named. P8a is gap-listed and ADR-0017 decision 2's moratorium holds it; ADR-0020 section 6 keeps the gate in force and its 2026-08-14 amendment does not lift it. When the gate opens, the sequence the audit supports is: dotmac_vendor_control_plane is cutover 1, because ADR-0020 section 6 already makes it the recommended first billing adopter on the strength of a live invoicing need and no invoice rows or renderer to migrate, and because a stateless renderer on PlatformScope needs no plane work at all (ADR-0023's ticketing blocker cannot recur in a module with no tables). Its first coherent slice is the CREDIT NOTE, because Sub has no credit-note renderer of any kind and no credit_number generator, so the slice retires nothing and competes with nothing. REQUIRED BEFORE THAT CUTOVER, not after it: the assembly-owned InvoiceArtifactReconciler with all five repair cases and its suppressed-event canary passing on BOTH planes. Vendor CP is greenfield on invoicing, so it is the first deployment where an issued invoice can exist with no artifact and the first where a convergence bug has no legacy path to hide behind; shipping issuance before convergence would put that bug in production with nothing to detect it. dotmac_sub is cutover 2 and is the qualifying product-first source; it adopts only through measured shadow-and-cutover on invoices, and only after billing has landed the issuance snapshots and P4 numbering that make the fact self-sufficient. Receipts follow cutover 2 and require a receipt fact billing does not emit today. Statements are excluded from both."
shadow_and_drift = "Determinism is asserted on DocumentProjectionV1, the canonical semantic projection the renderer emits alongside the bytes -- never on PDF bytes, and never by extracting text from a PDF. Byte checksums serve STORAGE INTEGRITY and are a different job: two renders of one fact must produce an identical projection digest and may legitimately produce different bytes. Shadow: for every issued document, build the projection from the immutable fact beside the legacy Sub renderer and compare section by section, value by value, plus the template decisions taken; the projected table row count must equal the fact's line count, which is what catches the legacy fallback truncation at 7 and 30 items. Projections carry formatting (money, dates, labels) because a formatting change IS a semantic change, and exclude volatile fields (generation timestamps, correlation ids, file ids, checksums) by construction. There is no money tolerance: every mismatch is classified as source defect, known intentional correction, missing evidence, contract defect, or shadow defect. Determinism suite: golden projections compared at a matching projection_contract_version, plus scope invariance (identical digest under TenantScope and PlatformScope), plus four sensitivity fakes -- clock-stamping, line-shuffling and silently-truncating renderers must FAIL, and a layout-only renderer must PASS, because a guard that fires on every stylesheet edit gets silenced. Drift and repair are owned by the assembly's InvoiceArtifactReconciler, which converges from state on a schedule and treats billing's issuance event as a wake-up signal only; it handles missing links, missing objects, checksum mismatches, stale render versions (REPORTED, never repaired -- re-rendering at a new renderer or template version is a derived reproduction and never replaces the official artifact) and partial failures, each with its detection signal and idempotency identity, and each committing through billing's typed command rather than a table write. An orphan object is never adopted into a relation without re-deriving its projection digest. A superseded fact version is rendered and recorded at ITS OWN fact version and cannot become the current official artifact, because officiality is structural per fact version; a cancelled fact that never had an artifact is not rendered at all. The load-bearing proof is a canary that suppresses the wake-up event entirely, asserts the outbox rows remain pending, and requires convergence anyway; its two sensitivity mutations -- moving recording into the event handler, and narrowing the gap query to a recent window -- must both make it fail."
local_copy_retirement = "Sub retires thirteen paths in a fixed order (docs/inventories/document-rendering-sources.md section 7): the invoice f-string renderer and its inline CSS; BOTH silent fallback renderers and the text-lines builder, which are deleted rather than ported because they truncate line items at 7 and 30 and change the document; the duplicated pydyf compatibility shim, which exists in two files; _build_pdf_bytes; the cache metrics read-modify-written into domain_settings; the InvoicePdfExport model, the invoice_pdf_exports table and the Celery task, which retire into dotmac_kernel.idempotency plus the outbox because a status column is a second at-most-once owner; the local-disk and direct-S3 read branches that make export.file_path polymorphic; the receipt renderer and its fallbacks; the fabricated #RCP- receipt reference, which retires into P4 numbering invoked by billing; the render-time application_summary call that recomputes four receipt figures; the three live settings lookups for logo, seller identity and bank details, which retire into fact snapshots; four currency-symbol reimplementations; and eight raw-strftime sites labelled UTC. Ordering is not optional: billing lands the issuance snapshots and numbering FIRST, because until the fact is self-sufficient the module cannot be a pure function of it and none of the invariants hold; the fallbacks are deleted LAST, after the shadow proves the engine path, because deleting them first is a production regression and deleting them never is how they survive forever. Retirement is proven by a two-directional ratchet (ADR-0018, hard rule 25): scripts/document_render_sweep.py plus docs/inventories/document-render-baseline.json count engine call sites, render-path storage writes, render-time money derivations, render-path settings reads, fabricated document numbers, currency-symbol literals and fallback renderer paths, across entry-point FAMILIES (services, web, tasks, scripts, CLI, workers) rather than one directory, failing when any count rises OR falls without the baseline being lowered in the same change, abstaining rather than scoring zero when the sibling repository is not checked out, and carrying a sensitivity proof that writes a temporary violating module under each scanned family and asserts every count rose. Quote documents, the NCC regulatory pack, the billing-documents list projection, document_delivery and the discount report are explicitly NOT retired by this workstream."
next_action = "1. Reach Team 2 / Team 4 agreement on the official-artifact relation and the final shape of InvoiceDocumentFactV1 -- Michael has named these the two principal gates for this batch. The recommendation is that billing RECORDS the relation on its own declared planes through a typed idempotent command, the assembly's InvoiceArtifactReconciler owns the ACT of establishing it, and dotmac-files owns neither (ADR-0022 section 2 forbids a domain relation on a storage row, and the module has no column for it). 2. Read dotmac_erp's DocumentGeneratorService, GeneratedDocument and its 10 integration tests, which docs/inventories/template-studio-source-audit.md names as document generation's qualifying source and which this audit has NOT read; source_paths and preserved_tests carry those entries as placeholders until they are verified, and the ERP source may change which implementation is product-first. 3. Confirm with the billing workstream the seven items requested in the contracts spec section 8 -- notably the payment-instructions snapshot, which closes a live money-misdirection defect where re-printing an old invoice prints today's bank account; the supersedes/superseded_by fact linkage, so the relation chain mirrors the fact chain rather than inventing a second supersession graph; and the issued-facts READ CONTRACT, which is the easy one to forget and the one that makes the reconciler legal rather than a cross-module table read. 4. Ask Team 2 to reword the billing spec's section 2.5 invariant 2 from re-render byte-for-byte-equivalent to semantically equivalent under the canonical semantic projection; the fact-side replay test it names is correct and needs no change. 5. Nothing else starts. P8a stays gap-listed, ADR-0017's moratorium holds, no package directory, EXTRACTION.toml, namespace allocation, model or migration lands, and a module proposal is not a demand-pulled exception."
```

---

## 3. Field notes

**`package`.** `dotmac-document-rendering` (import
`dotmac_document_rendering`), not `dotmac-documents`. The shorter name would
over-claim into document *management* — storage, retention, legal hold — which
ADR-0022 already gives to `dotmac-files` and to the owning domain. The
capability is rendering, and the name should not be able to grow.

**`classification = "optional-module"`.** Same as `dotmac-files`. Most of the
fleet installs it never: ADR-0020 A6 already records that ERP, CRM, Academy,
Workspace and the Integrator install none of the commercial modules, and a
renderer with no invoice authority to render for has nothing to do.

**`source_mode = "product-first"`.** Hard rule 24. The audit found a qualifying
production implementation, and it is **not the obvious one**: Sub's
`quote_documents.py` gets the structure right — immutable snapshot,
SHA-256 fingerprint of the input, content-addressed replay, one storage owner,
**no silent fallback**, fails closed on missing data — while the larger and
better-known `billing_invoice_pdf.py` has no snapshot, three storage paths,
three renderers and a mutable artifact. `source_paths` lists them in that
order deliberately: the structural source first, the domain source second.

**`contract_consumers = []`.** Empty, and must stay empty until a cutover is
real. ADR-0022 § 5 sets the precedent: *"`contract_consumers` remains empty
until those cutovers are real."* A contract with no consumer is work in
progress, not delivery (ADR-0017 decision 1).

**`inventory_evidence`.** Includes `template-studio-source-audit.md` because
that audit is what **excludes** document generation from template ownership and
directs this row to carry its own dossier — it is a boundary decision this
dossier depends on, not background reading.

**No `mod_*` short code, no lineage, no namespace entry.** A stateless module
allocates nothing in the kernel's immutable namespace ledger (hard rule 14).
That ledger is immutable, so an allocation made "just in case" is permanent
debt for a module that may never have a table.

---

## 4. What this dossier does not do

It does not lift ADR-0017's moratorium, does not claim P11, does not create a
package directory, an `EXTRACTION.toml` at a package root, a namespace, a
lineage, a model or a migration, does not name a real first cutover, and does
not grant this capability the owner-directed exception `dotmac-approvals`
received under ADR-0026. ADR-0020 § 6 remains the gate.
