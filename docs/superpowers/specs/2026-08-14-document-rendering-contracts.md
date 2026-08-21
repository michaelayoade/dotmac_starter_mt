# Document rendering — contracts, invariants and the renderer port

> **Implementation addendum — 2026-08-19.** ADR-0030 subsequently authorized
> `dotmac-document-rendering`, and Billing's frozen producer spelling is
> `document_profile_code`. Package `0.1.0a1` implements the stateless
> selection → semantic projection → renderer pipeline and conformance kit under
> `packages/dotmac-document-rendering/`; its public contracts and
> `EXTRACTION.toml` are now the as-built source. Kernel a86 exposes the same
> canonical algorithm as persistence-free `dotmac_kernel.fingerprints` while
> `dotmac_kernel.idempotency` keeps its compatibility re-export. The proposal banner below is
> retained as historical status, not rewritten into proof of current behavior.

> **Review status: PROPOSED — not reviewed, not frozen.** Appearing in the tree
> does not freeze a contract. `RenderedDocumentV1` and `DocumentProjectionV1`
> are this workstream's proposals; the consumer reading of
> `InvoiceDocumentFactV1` is a proposal that requires agreement with the Billing
> workstream, whose producer-side spec
> (`docs/superpowers/specs/2026-08-14-billing-authority-profile-contract.md`)
> carries the same banner. **§ 6, the official-artifact relation, is a named
> decision gate and is written as a proposal for agreement, not a settled
> answer.**

Dated 2026-08-14. Non-authoritative (`docs/superpowers/specs/` per `CLAUDE.md`'s
documentation hierarchy). This document freezes nothing and **authorizes
nothing.**

Document rendering is capability area **P8a** in
`docs/inventories/billing-sources.md` § 3, recorded there as *"missing; needed
when the selected invoice authority renders documents locally"*. P8a is
gap-listed, ADR-0017 decision 2's moratorium holds it, and ADR-0020 § 6 and its
2026-08-14 amendment both restate that nothing here starts implementation.
There is no package directory, no `EXTRACTION.toml` at a package root, no
model, no migration and no lineage.

- Source evidence: `docs/inventories/document-rendering-sources.md`
- Dossier content: `docs/inventories/document-rendering-extraction-dossier.md`
- Billing's producer side: `.../2026-08-14-billing-authority-profile-contract.md`

---

## 1. Why this is a separate workstream

Invoice *semantics*, document *rendering*, and physical *bytes* have three
different owners, and folding rendering into billing would collapse them:

| Concern | Owner |
|---|---|
| Invoice totals, tax, currency, lifecycle, legal identity | `dotmac-billing` |
| Invoice / receipt / credit-note number allocation | the numbering facility (P4), invoked by billing |
| Immutable `InvoiceDocumentFactV1` | `dotmac-billing` |
| Template selection and versioned presentation input | **rendering (this document)** |
| PDF / HTML generation and formatting | **rendering (this document)** |
| Physical bytes, checksum, storage, deletion | `dotmac-files` (ADR-0022) |
| **Which artifact is the official one for an invoice** | **§ 6 — a decision gate.** Recorded by billing; the act of establishing it owned by the assembly's `InvoiceArtifactReconciler` |
| Converging issued facts to artifacts, and repairing drift | the assembly's `InvoiceArtifactReconciler` (§ 6.7–6.12) — **required before Vendor CP cutover** |
| Email / SMS / channel delivery | delivery / Integrator transport (ADR-0024 § 6) |
| Accounting treatment | ERP (ADR-0020 § 2) |

ADR-0020 A5 states the billing half exactly: billing *"emits **immutable
document facts** and stops. The assembly connects a rendering owner (P8a, still
a genuine gap) to `dotmac-files`; billing does not import `dotmac-files`, per
A1."*

**Template Studio is not the invoice-content authority, and that is already
checked in.** `docs/inventories/template-studio-source-audit.md` § *Audit
outcome, part 1* concludes *"`kind=document` cannot be served by this
package… Drop documents from the package's scope"*, and its part-2 capability
map lists **document generation** as a separate owner whose boundary is
*"Document generation produces an artifact and its provenance record. It does
not own what an invoice or an offer letter *means*."* That audit also directs
each row to carry **its own `EXTRACTION.toml`** rather than inheriting Template
Studio's. This workstream is that row.

### The never-list

Rendering **never**:

- recalculates totals, tax, FX, balance or coverage;
- changes invoice lifecycle or payment status;
- allocates invoice, credit-note or receipt numbers independently;
- imports billing models or queries billing tables;
- stores bytes through a second storage implementation;
- records which artifact is the official one for an invoice;
- sends email or calls an external delivery provider;
- treats Template Studio as the invoice-content authority.

Every item is enforced by a named check in § 9.

---

## 2. The three-stage pipeline

Rendering is **three** stages, because determinism, the money guards and the
P9 boundary all live at the seams between them.

```text
InvoiceDocumentFactV1  (immutable, produced by billing)
        │
        │  stage 1 — SELECTION      (pure)
        │    document_profile_code -> template_code + template_version
        │    via assembly-declared bindings (§ 4.1)
        ▼
        │  stage 2 — PROJECTION     (pure)
        │    apply formatting; fix ordering; record every template decision
        ▼
DocumentProjectionV1   ← the canonical semantic projection. THE determinism artifact.
        │
        │  stage 3 — ENGINE         (the DocumentRenderer port)
        ▼
RenderedDocumentV1     (media type, byte length, sha256, payload, projection)
        │
        │  the ASSEMBLY, never this module
        ▼
dotmac_files.prepare_upload -> stage_file      (ADR-0022)
        │
        ▼
billing.record_official_document(...)          (§ 6 — proposed)
```

Stages 1 and 2 are total functions with no clock, no session, no network and
no randomness. Stage 3 is the only place a rendering engine appears, and it
sits behind a port with a fake, so a product develops with **no rendering
engine installed** (plan `2026-08-11-billing-subscriptions-collections.md` C5:
*"a product team develops with no PSP, tax, rendering, or storage
credentials"*).

The renderer emits `DocumentProjectionV1` **alongside** the bytes. It is not a
test fixture and not an internal detail — it is a first-class, independently
versioned output of the module, and § 5 specifies it before the bytes because
it is the more important of the two.

---

## 3. `InvoiceDocumentFactV1` — the consumer contract

Produced by billing. Immutable once emitted. Rendering treats it as the
**complete and only** input; anything a document must show and the fact does
not carry is a defect in the fact, never a lookup rendering performs.

Billing's proposed producer-side field list
(`2026-08-14-billing-authority-profile-contract.md` § 2.5) and this consumer
reading agree on everything below except where § 8 says otherwise. Where the
two specs use different names, **this document adopts billing's names** —
notably `document_profile_code` rather than a separate `document_kind`.

### 3.1 Identity, version, scope

| Field | Notes |
|---|---|
| `contract_version` | Major version on the wire. `1`. |
| identity | `(scope, invoice_id, fact_version)` — billing's stated identity. |
| `fact_version` | Increments **only when billing's own facts change** (a correction, a credit note). **Never for a re-render.** This is load-bearing for § 6's uniqueness key and is cited as a dependency, not assumed. |
| `scope` | `TenantScope \| PlatformScope` (`dotmac_kernel.cache`). A required, explicitly typed value — never a nullable `tenant_id`, which ADR-0023 refuses by name. |
| `emitted_at`, `issued_at`, `frozen_at` | tz-aware UTC. |

**Idempotency: two ledgers, two owners, never conflated.** The *fact's*
idempotency is billing's — scope `billing.document.fact`, key
`f"{invoice_id}:{fact_version}"`, `fingerprint = None`. The *render request's*
is rendering's own — scope `document.render`, key over
`(invoice_id, fact_version, media_type, template_code, template_version,
renderer_code, renderer_version)`. Both use `dotmac_kernel.idempotency`
(hard rule 23); neither is a second mechanism.

### 3.2 Issued number and state

`document_number` (allocated by billing at issuance through the bound
`NumberingProvider`, P4, and frozen thereafter) and its series identity;
`document_state ∈ {issued, corrected, cancelled}` — billing lifecycle only,
**no `paid`, no `partially_paid`** (ADR-0016).

Rendering prints the number and never derives, substitutes, or falls back to an
id. Sub's renderer falls back to `invoice.invoice_number or str(invoice.id)` in
five places (`billing_invoice_pdf.py:64, 242, 256, 535, 680`). A document with
no number is not a document; `fact_incomplete` is the answer.

### 3.3 Party snapshots

`seller` and `customer` snapshots — legal name, address, registered/tax
identifiers, contact — **frozen at issuance**, not references.

Sub resolves seller identity **live at render time**
(`resolve_brand(db, subscriber_id=…)` and
`company_info_service.get_company_info(db)`,
`billing_invoice_pdf.py:247-254`), so a rebrand silently rewrites every
historical invoice on the next re-render.

### 3.4 Amounts

Every amount is an `ExactAmount`:

```json
{ "currency": "NGN", "minor_units": 2, "amount": "1234.56" }
```

`amount` is a **JSON string** holding an exact decimal — never a JSON number
(parsed as a float by most parsers), never a float anywhere. `minor_units` is
carried so rendering can place the fraction digits without a currency table it
does not have (§ 7). `dotmac_kernel.money.Currency` already models exactly
`(code, minor_units)` and `Money` already refuses `float`.

Carried: `currency` (ISO-4217 code only — *"how it is displayed is the
renderer's"*), ordered `lines` with explicit `position`, `discounts` as
**exact amounts never a percentage the renderer must apply**, `tax_lines` with
treatment / jurisdiction / rate components / taxable basis / policy identity
and version, `subtotal`, `tax_total`, `total`, and the FX observation snapshot
if one was used, plus the applied price/source versions.

Billing's spec states the rule this workstream most needs, and states it
better than this document would have:

> The renderer performs no arithmetic; if it must add two numbers to draw a
> row, the fact is missing a field and the fix is an additive field, not a
> calculation in the renderer.

Accepted verbatim. § 9 I8 is its structural check.

### 3.5 Terms

`payment_terms` and `due_date`; `payment_instructions` snapshot — **requested,
§ 8 R1**.

### 3.6 Presentation inputs

`locale` (BCP-47) and `timezone` (IANA) — *"billing passes through the values
resolved for the customer; it performs no formatting with them"*. Carried now;
`locale` is **not resolvable against a catalogue until P9** (§ 7).

`document_profile_code` and its version — a declared registry code (ADR-0008)
naming *which kind of document this is as a commercial fact*: tax invoice,
proforma, credit note, receipt.

**The fact carries no template identity, and must not.** Billing's spec gives
the reason, and it is a better reason than the one this workstream first wrote
down: freezing a template into the invoice's immutable snapshot *"makes an old
invoice un-re-renderable after a template change"*. The two specs reached the
same boundary independently. § 4.1 specifies where the binding lives instead,
and § 8 Q1 records that it closes billing's open question 6 in the direction
billing proposed.

### 3.7 Source authority and provenance

`source_authority` (the billing installation, and per ADR-0020 § 3 which
commercial authority issued: `internal` / `provider_owned` / `external_finance`
— **ruled 2026-08-14, ADR-0020 § A7**; this spec's earlier draft proposed
`manual_erp` for the third member and the ruling retired it),
`source_system`, `source_record_id`, `source_record_version`, and the applied
price / source / tax-policy / FX observation versions that produced the
amounts.

A `provider_owned` fact is a projection of an externally owned document.
Rendering may produce a copy from it, but the fact says so; rendering does not
upgrade a projection into an original.

### 3.8 Correction, supersession, reversal

An issued document's snapshot is **immutable**. A correction is a credit note
or a superseding document with its own identity, never a rewrite; `cancelled`
is a state on the fact, not a deletion.

Credit notes follow the accepted rule (`2026-08-14-billing-vendor-cp-sub-cutover.md`
§ *Documents and coverage*): `subtotal = total`, `tax_total = 0`, no tax lines.
Rendering **asserts** that shape rather than formatting whatever arrives — the
one place rendering validates an amount relationship, and it validates rather
than computes: a violation is `fact_shape_invalid`, never a correction.

`supersedes_fact_id` / `superseded_by_fact_id` — **requested, § 8 R7**, so
§ 6's relation chain mirrors the fact chain rather than inventing one.

---

## 4. Template selection — stage 1

### 4.1 The mechanism is the module's; the bindings are the assembly's

Billing's spec proposes that the template binding be *"an assembly declaration
keyed on `document_profile_code`, not a billing column."* Agreed, with one
refinement that makes it testable:

- **The rendering module owns the selector** — a declaration registry
  (ADR-0008) mapping `(document_profile_code, profile_version, media_type)` to
  a `TemplateBinding(template_code, template_version)`, with the rule that an
  unbound profile fails loudly as `template_not_found` and names the assembly
  file as the fix.
- **The assembly owns the bindings.** A product declares which template artifact
  answers which profile code, exactly as `app/migration_bindings.py` already
  has the assembly bind effect→revision while *"a module never names a foreign
  revision"* (hard rule 14). Same shape, same reason: the module owns the
  mechanism and the validation, the composition root owns the facts.

This lets a product ship its own templates without releasing the module, and
lets the module prove the binding is complete without knowing any product's
templates.

### 4.2 A template artifact is versioned and immutable

`template_version` identifies an immutable artifact. Editing a template in
place is not a template change — it is an untracked one. A template change
publishes a new version; old documents keep re-rendering at their recorded
version (§ 6.4).

Sub has no template artifact at all: every renderer is a Python f-string with
inline CSS (`billing_invoice_pdf.py:295-464`), and the entire versioning
mechanism is one hand-edited module constant,
`INVOICE_PDF_TEMPLATE_REFRESHED_AT = datetime(2026, 3, 18, 9, 0, tzinfo=UTC)`
(`:51`).

---

## 5. `DocumentProjectionV1` — the canonical semantic projection

**This is the determinism artifact.** Determinism is *not* asserted on PDF
bytes and *not* asserted by extracting text from a PDF and comparing it. It is
asserted on a canonical semantic projection that the renderer emits as a
first-class output.

### 5.1 Why a projection and not bytes

PDF byte equality is defeated by `/CreationDate`, `/ModDate`, the `/ID` array,
`/Producer`, xref offsets, zlib level and version, and font-subset name
prefixes. A byte comparison either fails constantly or is quietly deleted after
it does. Text extraction is worse than it looks: it makes a PDF text-extraction
library a dependency of every implementation's test suite, it loses ordering
and structure, and it cannot see a decision the template took that produced the
same visible string by a different route.

The projection removes the problem rather than working around it: the renderer
*states* what the document says, in a canonical form, and that statement is
compared.

### 5.2 Shape

```python
# ILLUSTRATIVE. No package exists; nothing here is authorized.

@dataclass(frozen=True, slots=True)
class DocumentProjectionV1:
    projection_contract_version: int      # its OWN version line (§ 5.5)
    invoice_id: UUID
    fact_version: int
    document_profile_code: str
    document_profile_version: int
    template_code: str
    template_version: str
    renderer_code: str
    renderer_version: str
    media_type: str
    template_decisions: tuple[TemplateDecision, ...]   # sorted by key
    sections: tuple[Section, ...]                      # ordered by `position`
    digest: str          # sha256 over the canonical JSON of every field above
```

```python
class TemplateDecision:      # every branch the template took
    key: str                 # "discount_row", "bank_details_block", "tax_label"
    outcome: str             # "shown", "omitted", "vat_with_number"

class Section:
    key: str                 # stable, language-neutral: "header", "line_items", "totals"
    position: int
    blocks: tuple[Block, ...]

# Block is one of:
class LabelledValue:  label_key: str; label_text: str; value: RenderedValue
class Table:          columns: tuple[ColumnSpec, ...]; rows: tuple[tuple[RenderedValue, ...], ...]
class StaticText:     key: str; text: str

class RenderedValue:
    kind: str                # money | date | datetime | quantity | identifier | text
    source_field: str | None # the EXACT fact field this came from (§ 9 I8)
    raw: str                 # canonical: exact decimal / ISO-8601 / ISO-4217 code
    text: str                # the formatted string that appears in the document
    currency: str | None
    minor_units: int | None
    timezone: str | None
    format_code: str | None  # which declared format string was applied
```

`digest` is computed with `dotmac_kernel.fingerprints.fingerprint_of`, also
compatibly re-exported by `dotmac_kernel.idempotency`, which
already pins *"sorted keys and compact separators, meaning dict ordering and
incidental whitespace cannot change the digest."* That is the kernel's
fingerprint mechanism, not a second one (hard rule 23).

### 5.3 What the projection normalizes — formatting is IN

Formatting is **applied and recorded**, not stripped. A formatting change is a
change to what the document says, and must break determinism against a golden
projection:

| Normalized | How |
|---|---|
| Money | `raw` = ISO-4217 code + exact decimal; `text` = the formatted string; `currency` + `minor_units` carried. Until P9, `text` is `"<CODE> <decimal>"` (§ 7). |
| Dates / datetimes | `raw` = ISO-8601 in the **document's** `fact.timezone`; `text` = formatted with the declared `format_code`; `timezone` carried. Never the reader's zone. |
| Labels | `label_key` is stable and language-neutral; `label_text` is the language of the **template version** (§ 7). A translation is therefore a template-version change and a visible digest change. |
| Ordering | explicit `position` on sections, index on table rows. Never dict or iteration order. |

### 5.4 What the projection excludes — volatile and engine fields are OUT

**Excluded by construction, not by a filter** (there is nowhere on the shape to
put them):

- `rendered_at` or any generation timestamp, `correlation_id`, `idempotency_key`
  — these live on `RenderedDocumentV1`.
- `file_id`, `storage_key`, `byte_length`, `checksum_sha256` — storage facts,
  and a projection that carried them could never be compared to a repair
  re-render.
- Engine artifacts: page numbers, page breaks, coordinates, font names, colour,
  CSS. These are *how it looks*, not *what it says*.

The last exclusion has a consequence worth stating plainly: **a pure layout or
CSS change does not break the projection digest.** That is correct — it is not
a change in what the document says. A product wanting layout regressions caught
needs a visual-diff mechanism, which is explicitly out of scope here and must
not be smuggled into the projection, because a projection that fires on every
stylesheet edit will be silenced.

### 5.5 Versioning a projection change

`projection_contract_version` is its **own** integer, independent of
`InvoiceDocumentFactV1`'s `contract_version` and of `renderer_version`.

The digest covers the whole canonical shape, so *any* change to the projection's
structure changes *every* digest. Therefore:

1. **Any change to the projection's shape bumps `projection_contract_version`**
   — adding a section key, adding a `RenderedValue` field, changing a
   `TemplateDecision` key. There is no "additive, no bump" concession, because
   the digest cannot honour one.
2. **A golden projection is stored with the version that produced it**, and the
   determinism suite compares like for like: a golden at version 3 against a
   projection produced at version 3.
3. **Re-recording goldens at a new projection version is a deliberate, reviewed
   act** with a diff a human reads. That is the point of the mechanism, not a
   cost of it.
4. A *renderer* or *template* change that alters a value **without** a
   projection-shape change is caught as a digest mismatch at the same
   projection version — which is exactly the regression this exists to catch.
5. `RenderedDocumentV1` carries both `projection_contract_version` and
   `projection_digest`, so a recorded artifact always says which projection
   line it was verified against.

### 5.6 Determinism and storage integrity are different jobs

Stated explicitly, because conflating them is the easy mistake:

| | Determinism / semantic equivalence | Storage integrity |
|---|---|---|
| Artifact | `projection_digest` (+ `projection_contract_version`) | `checksum_sha256`, `byte_length` |
| Question answered | *do two renders mean the same thing?* | *are these the bytes that were produced?* |
| Compared across | renders, hosts, engine patch versions, time | one handoff: renderer → assembly → `dotmac-files` |
| May legitimately differ between two renders of the same fact | **no** | **yes** |

Consequences that follow from the table and must be specified, not inferred:

- **A repair re-render is verified against `projection_digest`, never against
  `checksum_sha256`.** The byte checksum *will* differ, and that is correct.
- **A byte checksum match is never accepted as evidence of determinism**, and a
  byte checksum mismatch is never treated as a semantic regression.
- `checksum_sha256` uses the format `sha256:<hex>` — **identical to**
  `dotmac_files.PreparedFile.checksum_sha256` (`physical.py:174`) — so the
  assembly's binding check (§ 9 I3) is a string equality, not a format
  negotiation.

### 5.7 The residual risk, stated rather than hidden

A renderer that emits a *correct* projection and *wrong* bytes is not caught by
the determinism suite. The mitigations, honestly ranked:

1. For `text/html` and for the fake, the contract suite asserts
   **correspondence**: every `RenderedValue.text` and every `StaticText.text` in
   the projection appears in the output, and no `Table` row is absent. This is
   cheap and exact.
2. For `application/pdf`, correspondence is **best-effort and explicitly not
   part of the shared contract** — requiring it would make a PDF
   text-extraction library a dependency of every implementation. An
   implementation may add it to its own suite.
3. Human review of the engine adapter, which is small by construction because
   stages 1 and 2 hold all the logic.

This is a real gap and naming it is better than a check that appears to close
it and does not.

---

## 6. The official artifact: the relation, and the reconciler that converges it

> **This section is a proposal for agreement between the Billing (Team 2) and
> Documents (Team 4) workstreams, and Michael has named it one of the two
> principal gates for this batch.** It is not a settled answer. Billing's spec
> § 2.5 currently ends billing's obligation at the outbox and does not include
> this relation, so § 6.6 is a genuine ask.
>
> § 6.7 onward specifies `InvoiceArtifactReconciler`. It is labelled a
> **recommendation** until the teams check it into the authoritative contract.
> **It is required before Vendor CP cutover.**

### 6.1 The statement that needs an owner

> *"This stored file is the official artifact of invoice X at fact version Y."*

That is a **domain** statement. It asserts which bytes are *the invoice* — the
thing a customer was handed, an auditor asks for, and a dispute turns on.

### 6.2 It is not `dotmac-files`'s, and that is settled by an accepted ADR

ADR-0022 § 1 scopes the module to *"stored bytes and their repairable physical
lifecycle"*. § 2 is categorical about the shape:

> `stored_files` has **no polymorphic entity columns, public flag, domain ID,
> or generated public URL.**

and assigns the relation away, naming this domain literally:

> A ticket, **invoice**, subscriber, work order, message, or import run stores
> an opaque file UUID and **owns its relation**, visibility, permissions, legal
> hold, retention rule, and audit vocabulary.

So a storage row must never become the place the fleet learns which PDF is the
official invoice. This is not a preference — it is refused by the accepted
decision, and the module's schema has no column to hold it.

### 6.3 The two real candidates

**Documents (this module).** *For:* it produced the artifact and holds render
provenance. *Against:* it is recommended **stateless** (dossier § 1, confirmed),
so a durable relation would reintroduce a table, a lineage, a namespace
allocation and two persistence planes for exactly one relation — reversing the
recommendation for the smallest possible reason. And, more fundamentally,
rendering has no lifecycle authority: it can truthfully say *"these bytes are a
faithful rendering of fact v3"*, and it cannot say *"and that is the official
invoice"*, because "official" is a predicate over an issuance and correction
chain rendering does not own.

**Billing.** *For:* it owns invoice legal identity, `document_number`,
`document_state`, and the correction/supersession chain that "official" is
defined over; ADR-0022 § 2 names "invoice" literally as the domain that owns
such a relation; it already has a lineage and both persistence planes
(ADR-0020 A2); and it already has the transaction the relation must commit in.
*Against:* ADR-0020 A5 says billing *"emits immutable document facts and
stops."*

**Answering the objection.** A5's sentence is about **deciding presentation** —
it is the sentence that stops billing rendering or importing `dotmac-files`.
Storing an *opaque UUID* is not importing `dotmac-files`; it is precisely the
shape ADR-0022 § 2 designs, and ADR-0022 is the more specific rule about file
relations. Billing knowing *which artifact was issued* is not billing deciding
*what it looks like*.

### 6.4 Recommendation, and the mechanics it commits to

**Recommendation: billing owns the relation**, as rows on billing's own
declared planes (ADR-0023: tenant `tenant_id NOT NULL` + composite unique;
platform no tenant column, no RLS, `REVOKE ALL` from the tenant app role).

| Question | Proposed answer |
|---|---|
| **Where stored** | A billing-owned table per plane. Columns: `invoice_id`, `fact_version`, `media_type`, `file_id` (opaque UUID), `renderer_code`, `renderer_version`, `template_code`, `template_version`, `projection_contract_version`, `projection_digest`, `checksum_sha256`, `byte_length`, `superseded_by_id`, `recorded_at`. |
| **Who writes it** | Billing's own service, called **by the assembly** after `stage_file` succeeds and after the § 9 I3 binding check passes. Rendering never writes it. `dotmac-files` never knows it exists. |
| **Which one is official** | **No boolean.** A mutable `is_official` flag would be a second lifecycle. Instead: a composite unique on `(scope, invoice_id, fact_version, media_type)`. "Official" is *structural* — the official artifact for a fact version is the row that exists for it. There is no un-officialing. |
| **Re-render — does the official artifact change?** | **No.** A re-render at the *same* renderer and template versions is a **repair**: the unique constraint refuses a second row, and the repair is verified by comparing the new `projection_digest` to the recorded one. A re-render at a *different* renderer or template version is a **derived reproduction** — it may be produced and even stored, but it never takes the official row's place. |
| **Correction / supersession** | A correction bumps `fact_version` (billing's rule: it increments only when billing's facts change) or issues a credit note with its own identity. Either way a **new row at the new fact version** is created; the old row stays and is linked through `superseded_by_id`, mirroring the fact chain (§ 8 R7). The old artifact is never deleted and never rewritten — it is what the customer was given. `document_state = cancelled` does not delete it either. |
| **Repair without losing the relation** | The relation row is the **record**; the file is the **copy**. If `dotmac-files` reports `FileState.MISSING`, the row survives untouched. Repair re-renders from `(invoice_id, fact_version)` → the fact, plus the recorded `renderer_version`, `template_version` and `projection_contract_version`; asserts the new `projection_digest` **equals** the recorded one; stages new bytes at a **new immutable key**; and updates **only** `file_id`, `checksum_sha256` and `byte_length`. Every semantic column is immutable after insert. |
| **Repair that does not match** | **Refused and raised.** A projection-digest mismatch means the renderer or template genuinely changed, so the artifact would be a *different document*. Silently storing it would be the mutable-historical-record defect this whole contract exists to prevent. |

The honest consequence of "re-render does not change the official artifact": a
renderer upgrade means old invoices keep their old official artifacts and only
newly issued documents get the new renderer. That is correct, and it is the
whole point.

### 6.5 The defect this closes

Sub's stored invoice PDF is invalidated whenever `invoice.updated_at` moves
past `export.completed_at` (`_is_export_fresh`,
`app/services/billing_invoice_pdf.py:945-955`), and again whenever a hand-edited
module constant moves (`:51`). The artifact therefore tracks the **current
invoice row**, not the issued document: download the same invoice before and
after a payment lands and the two PDFs disagree about what is owed, under the
same invoice number. There is no relation anywhere saying which one was
official, because there is no relation.

### 6.6 What this asks of billing

A table, a `record_official_document(...)` service the assembly calls, and the
`supersedes` / `superseded_by` fact linkage (§ 8 R7). Note what it does **not**
ask: no rendering, no template knowledge, no `dotmac-files` import, no
presentation decision. It is billing *state* about its own document, not a
billing *decision about presentation* — and that distinction is the whole
proposal.

**If billing declines**, the fallback is not "the assembly owns it". An
assembly-owned relation table is assembly-local state with no module owning its
tests, its migration or its drift repair, and it would make the repair path of
§ 9 I5 unowned. The real fallback is that the Documents module becomes
stateful and dual-plane (dossier § 1.8), which reverses a recommendation
Michael has already confirmed — so it should be the outcome of an explicit
ruling, not of a stalemate.

### 6.7 The event is a wake-up signal, not the mechanism

Billing emits a `document.fact.issued` outbox event after its issuance
transaction commits. **That event is an optimisation for latency, and nothing
else.** Convergence is owned by a named reconciler.

If convergence depends on the event arriving, the design is a delivery
guarantee wearing a reconciler's clothes: it works until a relay is paused for
a deploy, a poison message parks a batch, a consumer is rolled back, or an
event type is renamed — and then invoices silently have no artifact and nothing
notices, because the only thing that would have noticed was the event.

§ 6.12's canary is what converts *"the event is an optimisation"* from a claim
in this document into a property CI can lose.

### 6.8 `InvoiceArtifactReconciler` — recommended, owned by the ASSEMBLY

| | |
|---|---|
| **Owner** | the consuming assembly |
| **Reads** | issued billing fact versions that lack a valid official artifact, **through billing's published read contract** — a query on a contract, never a table read (ADR-0024 § 1) |
| **Invokes** | the stateless renderer (stages 1–3) |
| **Stores bytes** | through `dotmac-files`, with the § 9 I3 binding check |
| **Records the relation** | through a **typed billing command** (§ 6.9). Team 2 specifies the command; this section specifies what it must carry |
| **Writes** | **no module tables directly** — not billing's, not `dotmac-files`'s, not its own |
| **Runs** | on a schedule, and on demand. The event merely triggers an earlier pass |

**Why the assembly and nowhere else.** The reconciler is the only actor that
legitimately touches all three surfaces, and ADR-0024 § 2 makes the assembly
the composition root that *"owns any relation between the module and product
data"*. Each alternative breaks an accepted boundary:

- **billing** would have to import `dotmac-files` (ADR-0020 A5, ADR-0022 forbid it);
- **rendering** would have to become stateful, hold a session, and query
  billing — breaking I4, I6 and I7 simultaneously;
- **`dotmac-files`** would have to know what an invoice is (ADR-0022 § 2).

The assembly is not a loophole here; it is the one place the fleet's own rules
already put this.

### 6.9 What the typed billing command must carry

Team 2 owns the command's name and signature. This is the payload and the
semantics the reconciler requires of it.

**Payload.** `invoice_id`, `fact_version`, `media_type` (the uniqueness key);
`file_id` (opaque); `renderer_code`, `renderer_version`, `template_code`,
`template_version`; `projection_contract_version`, `projection_digest`;
`checksum_sha256`, `byte_length`; the reconciler's `idempotency_key`; and
`expected_fact_state` — the `document_state` and `fact_version` the reconciler
**observed** when it started.

**Semantics billing must provide:**

1. **Idempotent by the uniqueness key.** A replay with an identical payload is
   a no-op success. Key: scope `document.artifact.record`, key
   `f"{invoice_id}:{fact_version}:{media_type}"`.
2. **A replay with a *different* payload for the same key is a CONFLICT, not an
   overwrite** — mirroring `dotmac_kernel.idempotency`'s fingerprint rule, where
   *"a key reused with a different fingerprint is a conflict, not a replay"*
   (hard rule 23). Two renderers racing must not silently pick a winner.
3. **`expected_fact_state` is checked, and a stale view is refused.** If the
   fact has been superseded or its state has moved since the reconciler read
   it, billing refuses the command and the reconciler re-reads. This is one of
   two guards against a superseded version becoming official (the other is
   structural — § 6.4's per-fact-version uniqueness).
4. **A separate repair variant** that updates **only** `file_id`,
   `checksum_sha256` and `byte_length`, and **refuses any semantic-column
   change**. Every other column is immutable after insert.

### 6.10 Where the relation is STORED — three acts, three owners

The distinction that makes this coherent:

| Act | Owner |
|---|---|
| **Deciding** to establish the relation — finding the gap, ordering the work, retrying | the assembly's `InvoiceArtifactReconciler` |
| **Recording** the relation | **billing**, through the § 6.9 typed command, in billing's own tables on billing's own declared planes (ADR-0023) |
| **Producing** the bytes the relation points at | the stateless renderer |

So the assembly owns the **act**; billing owns the **record**. The assembly
writing no module tables directly is exactly what keeps this from collapsing
into the assembly-owned relation table § 6.6 rejects — an assembly-local table
with no module owning its tests, migration or drift repair.

**Why a stateless renderer is consistent with this — in fact, why the
reconciler is what makes statelessness *sufficient*.**

A stateless renderer participates in none of the three acts above except the
third. It does not query for gaps (the reconciler does, against billing's read
contract), does not persist the relation (billing does), does not store bytes
(the assembly does, through `dotmac-files`). It receives a projection and
returns a result.

The objection a stateful design would raise is *"then what remembers whether
this has been done?"* — and the answer is the relation row plus
`dotmac_kernel.idempotency`, both of which already exist and are already
adopted surface. A stateful renderer would answer that question by inventing a
render-job table with a status column, which is precisely the second
at-most-once owner ADR-0014 and hard rule 23 forbid, and which Sub already
built and already broke: `InvoicePdfExport` commits `processing` *before* the
render runs (`app/services/billing_invoice_pdf.py:1353-1356`), so a crashed
worker leaves a row nothing finishes, patched over by
`maybe_finalize_stalled_export` and a 20-second wall-clock guess.

The reconciler removes the reason to build that table. Statelessness is not a
constraint the reconciler tolerates — it is a conclusion the reconciler earns.

### 6.11 Repair cases

**First, the operational premise.** Because rendering failure never rolls back
issuance (§ 9 I4), **an issued invoice with no relation row is a normal, valid,
expected state** — not an error. Every reader must handle it. The reconciler's
queue *is* the set of such rows, so a non-empty queue is normal operation; only
**age** in that queue is an alarm. The relation must tolerate absence, and any
reader that treats a missing artifact as a failed invoice has the boundary
backwards.

| # | Case | Detection signal | Idempotency identity | Behaviour when the fact was corrected or superseded |
|---|---|---|---|---|
| 1 | **Missing link** — an issued fact version with no relation row for a media type its profile requires | billing's issued-facts read contract, left-joined to the relation, filtered on the profile's required media types | render: scope `document.render`, key `(invoice_id, fact_version, media_type, template_code, template_version, renderer_code, renderer_version)`. record: scope `document.artifact.record`, key `(invoice_id, fact_version, media_type)` | **Still rendered and recorded — at its own `fact_version`.** A superseded version is still a document that was issued and may be asked for. It **cannot** become "the official artifact" because there is no singular official artifact: § 6.4 makes officiality structural *per fact version*. Superseded versions are additionally **deprioritised** into a backfill lane. **Exception, recommended: a fact that is `cancelled` and never had an artifact is NOT rendered** — manufacturing the first artifact of a document nobody was ever handed creates evidence rather than repairing it. |
| 2 | **Missing object** — the relation row exists but `dotmac-files` reports `FileState.MISSING` | `dotmac_files.service.record_presence` / the files missing-object reconciler, joined to relation rows | same `document.render` key; the record step uses the § 6.9 **repair variant** | Irrelevant to the decision: repair reproduces the artifact *for the fact version it belongs to*. Verified by `projection_digest` equality (§ 6.4), **never** by checksum (§ 5.6). |
| 3 | **Checksum mismatch** — the stored object's observed hash ≠ the recorded `checksum_sha256` | a periodic verification pass that re-hashes objects read through `dotmac-files`. **Sampled, and the sampling rate is a declared policy, not a hidden shortcut** — full re-hashing of every artifact is not affordable and pretending otherwise is how a verification pass gets disabled | same as case 2 | Irrelevant, as case 2. **Never overwrite the object**: keys are immutable and deletion is `deletion_pending`-first (ADR-0022 § 4). Re-render, stage at a **new** key, repair-update `file_id`/`checksum`/`byte_length`, then request deletion of the old object. **The mismatch is recorded as evidence** — corruption is an event, not something to silently heal. |
| 4 | **Stale render version** — the relation records a `renderer_version` or `template_version` older than the assembly's current binding | compare recorded versions against the current binding for that profile version | none — no work is performed | **Do nothing.** This is a **reporting** case, not a repair case. Per § 6.4 a re-render at a different renderer or template version is a derived reproduction and never replaces the official row. The reconciler emits a drift metric and takes no action. **This is the case most likely to be got wrong**, because "stale" sounds like something to fix — and fixing it would rewrite history for every customer already holding the old PDF. If a deployment genuinely must re-issue (a legally defective template), that is a **billing** act: a correction that bumps `fact_version`, not a rendering repair. |
| 5 | **Partial failure** — bytes staged in `dotmac-files` but the billing command never committed | an object under the issued-document prefix, older than a threshold, with no relation row referencing its file id | the same two keys as case 1; a re-drive is a replay, not a duplicate | Apply case 1's rule at the observed `fact_version`. Two safe resolutions: **(a)** re-render, compare `projection_digest` **and** `checksum_sha256` against the orphan, and only then re-drive the record command with the original key; or **(b)** leave it to `dotmac-files`' existing tenant-scoped orphan reconciler, which *"removes it after an age threshold"* (ADR-0022 § 4), and re-drive case 1 from scratch. **Never adopt an orphan object into a relation without re-deriving its projection digest** — that would let arbitrary bytes in the bucket become the official invoice. |

Every case is idempotent by construction, and every one commits its record
through billing's command rather than by writing a table.

### 6.12 The suppressed-event canary

**The load-bearing test.** It proves the event is an optimisation rather than
the mechanism.

**What it seeds.** Issued billing fact versions across **both planes** —
`TenantScope` and `PlatformScope`, since Vendor CP is platform-only — covering:

1. a plain issued invoice with no artifact;
2. a credit note with no artifact;
3. a fact version superseded *after* issuance, with no artifact;
4. a fact version whose earlier render attempt failed with `engine_unavailable`;
5. a fact version whose bytes were staged but whose record command never
   committed (case 5);
6. a fact version with a relation row whose object is then forced to
   `FileState.MISSING` (case 2);
7. a `cancelled` fact version that never had an artifact.

**What it suppresses.** No wake-up signal of any kind reaches the reconciler:
the outbox relay is not run, or `document.fact.issued` is dropped by a null
transport. The canary **asserts the outbox rows are still `pending` at the
end** — otherwise a passing run could mean the events *were* consumed and
everything worked for the wrong reason, which is a green test proving nothing.

**What it asserts**, after a bounded number of reconciler passes:

- every seeded fact version that should have one has **exactly one** relation
  row per required media type, with a `projection_digest` equal to a freshly
  computed projection;
- seed 5's orphan is either adopted with a verified digest **or** removed —
  never adopted unverified;
- seed 6 is repaired with a **new** `file_id` and the **same**
  `projection_digest`, and a **different** `checksum_sha256` (§ 5.6 —
  asserting the checksum *changed* is part of the point);
- seed 3's row exists at **its own** `fact_version` and is not the current
  version's row;
- seed 7 has **no** row;
- both planes converge identically;
- the reconciler is idempotent: one further pass with nothing to do performs
  **zero** writes.

**Sensitivity proofs.** Two mutations, both of which must make the canary
**fail**:

1. **Move relation-recording into the event handler only** — the
   delivery-guarantee shape. With the event suppressed, nothing converges.
   *This is the mutation the canary exists to catch*: it is exactly the design
   error of treating the event as the mechanism.
2. **Narrow the gap query to "issued in the last hour"** — a plausible
   performance optimisation. The seeded backlog is older, nothing converges.

And one **inverse** proof: with the event delivered normally, the canary must
still pass — proving the event path and the reconciler path are not two
mechanisms that mask each other's failures.

---

## 7. The P9 boundary — stated precisely

`docs/inventories/billing-sources.md` P9 is *partial*: *"`display.py` resolves
tenant timezone and date/datetime formats. There are no locale catalogs, stable
message IDs, or currency-display rules."* That is accurate.
`dotmac_kernel.display` gives a `ZoneInfo`, a `date_format` string and a
`datetime_format` string, and its own docstring scopes it to the web portal.

**What rendering can do today, with no P9:**

- place fraction digits correctly, from `minor_units` on the fact;
- render every date and datetime in `fact.timezone` using declared format
  strings, resolved by the **assembly** and passed into the request (never read
  from a session by the renderer);
- print the ISO-4217 alphabetic code as the currency indicator.

**What rendering must NOT do until P9 exists:** choose a currency **symbol** or
its position; choose locale-correct digit grouping; translate any label or
pluralize anything; resolve a `locale` tag against a catalogue, because there
is none.

**The stopgap rule, stated so it can be retired:** until P9 lands, an issued
document renders money as `<ISO-4217 code> <exact decimal>`, and **every
label's language is a property of the template version, not of a lookup**. A
second language is a second template version. Deliberately dumber than a locale
layer, and honest: it produces a document that is correct in every deployment
rather than one subtly wrong in most. Because `label_text` and every
`RenderedValue.text` are *in* the projection (§ 5.3), the day P9 lands and
formatting changes, every golden digest changes — loudly, which is right.

This retires Sub's hardcoded `NAIRA_SIGN = "₦"`
(`billing_invoice_pdf.py:49`), interpolated unconditionally regardless of
`invoice.currency`, and its `"NGN"` literals — both C5 forbidden names.

`fact.locale` is carried from day one anyway: retrofitting a field onto an
immutable fact is a contract major version, and carrying an unused one is free.

---

## 8. Fields requested from billing, and open questions

**Not asserted anywhere above except where marked.** Billing's spec is
**PROPOSED, not frozen**, and its § 2.5 field list is a proposal this document
may align to and argue with.

**R1 — `payment_instructions` snapshot.** Bank name / account name / account
number / sort code as at issuance. Sub resolves these **live at render time**
(`invoice_bank_details_service.get_invoice_bank_details(db, currency=…)`,
`billing_invoice_pdf.py:266` and again `:865`), so re-rendering a two-year-old
invoice prints today's bank account. A money-misdirection defect, not a
cosmetic one. Rendering cannot fix it, because rendering must not read
settings. **Not in billing's current list.**

**R2 — seller/customer snapshots at § 3.3's granularity.** Billing's list
already commits to this; confirmation, not a new ask.

**R3 — a brand/presentation-asset reference frozen at issuance.** Either an
opaque `dotmac-files` file id (the assembly resolves it to bytes) or an
explicit "no logo". Not a settings key resolved at render time, which is what
Sub does (`billing_invoice_pdf.py:141-167`). **Not in billing's current list.**

**R4 — `document_profile_code` covering `receipt`.** Billing's list names tax
invoice, proforma, credit note and statement. Receipts are in this
workstream's scope; if billing emits no receipt fact, receipts leave the first
slice. (Sub's receipt figures are recomputed at print time from live
allocation rows — `billing_payment_receipts.py:127` →
`payments.py:3865-3885` — so a reprint after a reallocation contradicts the
original, which was never stored.)

**R5 — the official-artifact relation, its typed recording command, and an
issued-facts read contract.** Three things, all billing's: the relation table
on both planes (§ 6.4); the idempotent, conflict-refusing,
`expected_fact_state`-checking command with its repair variant (§ 6.9); and a
published read contract exposing *"issued fact versions and their current
state"* so the assembly's reconciler can find gaps **without a table read**
(ADR-0024 § 1). The read contract is easy to forget and is the one that makes
the reconciler legal rather than a cross-module query.

**R6 — `minor_units` on every amount**, not just the ISO code. Without it,
rendering needs a currency table, which is P9's, which does not exist.

**R7 — `supersedes_fact_id` / `superseded_by_fact_id` on the fact.** Billing's
list has `document_state` and describes *"a superseding document with its own
identity"* but carries no explicit linkage field. § 6.4's `superseded_by_id`
chain should **mirror** the fact chain, not be invented independently by a
relation table — otherwise the fleet has two supersession graphs.

### Open questions

**Q1 — the profile/template split: agreed, and it closes billing's Q6.**
Both specs independently concluded that the fact carries
`document_profile_code` and **no** template identity. Billing's open question 6
asks whether rendering needs the template binding to be a billing field.
**Answer: no.** § 4.1 puts the *mechanism* in the rendering module and the
*bindings* in the assembly, keyed on `document_profile_code` — the shape
billing proposed, refined only in where the selector lives. Recorded so
billing's Q6 can be closed rather than left open on this side.

**Q2 — who owns the official-artifact relation?** § 6. **Recommendation:
billing.** Named by Michael as a decision gate for this batch.

**Q3 — are statements in scope?** **Recommendation: no, not in the first
slice.** A statement spans many documents and a period, so its immutable input
is a *period* fact, not a document fact, and stretching
`InvoiceDocumentFactV1` to cover it would repeat the merge error the Template
Studio audit exists to catch. Sub's statement service also recomputes every
balance at request time with no rounding at all
(`web_billing_statements.py:120-129,168-189`), so its arithmetic is billing's
problem before it is rendering's.

### One correction requested in billing's spec — reported, not edited

Billing's § 2.5 invariant 2 reads: *"The fact must be sufficient to re-render
the document **byte-for-byte-equivalent** at any later time."*

**Byte-for-byte equivalence is not achievable and is not the right test** —
§ 5.1. Requested wording: *"semantically equivalent under the canonical
semantic projection (`projection_digest`)"*. Billing's **fact-side** test,
`test_a_historical_invoice_fact_replays_identically` (field-level equality of
the re-emitted fact), is exactly right and needs no change; only the
rendering-side phrasing overclaims. Reported here per the brief rather than
edited in another team's file.

---

## 9. Invariants, each with its check and its sensitivity proof

ADR-0018 and hard rule 25: guards enumerate entry-point families, and **a guard
that cannot fail is not a guard**. Every row carries a proof the detector fires.

### I1 — Issued documents are immutable

**Statement.** A correction produces a credit note, a replacement, or a
superseding fact version. It never edits a historical PDF.

**Check.** (a) The port exposes no operation taking a rendered-document
identity plus a mutation; an architecture test fails on any public symbol
matching `update_|amend_|patch_|overwrite_|edit_` applied to a document.
(b) `dotmac_files.physical.prepare_upload` mints
`storage_key = f"{scope_prefix(scope)}{uuid4()}"` (`physical.py:172-173`) — a
**new immutable key per call** — so a re-render structurally cannot overwrite
an earlier artifact. (c) § 6.4's composite unique refuses a second official
row for the same `(scope, invoice_id, fact_version, media_type)`.

**Sensitivity proof.** A fixture declaring `update_rendered_document(...)` must
fail the naming guard. A test asserting two renders produce two distinct
`storage_key` values must fail if the assembly is rewired to reuse a key.

### I2 — Same fact + template version + renderer version ⇒ same semantic result

**Check.** `projection_digest` equality, at the same
`projection_contract_version`, against a **golden projection checked into the
suite** (§ 5). Not bytes. Not extracted text.

Additionally, the projection is asserted **scope-invariant**: every case runs
twice, once under `TenantScope(uuid4())` and once under `PlatformScope()`, and
the digests must be **identical** for facts differing only in scope. That is
ADR-0023's *"one behaviour"* requirement made checkable in a stateless module.

**Sensitivity proofs — four fakes, three of which must FAIL:**

| Fake | Behaviour | Expected |
|---|---|---|
| `ClockStampingRenderer` | writes `datetime.now()` into a visible `RenderedValue` | **fail** — `raw` changes, digest changes |
| `ShuffledLinesRenderer` | reorders `fact.lines` | **fail** — `position` changes |
| `SilentlyTruncatingRenderer` | drops table rows past the 7th | **fail** — row count changes |
| `LayoutOnlyRenderer` | changes CSS and nothing else | **pass** — proves the projection is not over-sensitive (§ 5.4) |

The suite asserts all four outcomes. The first three prove the comparison
compares something; the fourth prves it does not fire on everything, because a
guard that fires on every stylesheet edit gets silenced and then protects
nothing. `SilentlyTruncatingRenderer` encodes a real production defect: Sub's
fallback cascade truncates line items at **7** (`billing_invoice_pdf.py:788`)
and **30** (`:522`) depending on which native libraries are installed on the
host that served the request.

### I3 — Stored bytes are bound to the render-result checksum

**Check.** The assembly compares `prepared.checksum_sha256 ==
result.checksum_sha256` **and** `prepared.size_bytes == result.byte_length`
before `stage_file`, and refuses otherwise. Formats are identical by
construction (§ 5.6). An architecture test requires every reachable
`stage_file(...)` in a rendering wiring path to carry that comparison in the
same function.

This is **storage integrity, not determinism** (§ 5.6). It is never accepted as
evidence of the latter.

**Sensitivity proof.** A fixture flipping one byte of `result.payload` before
the handoff must make the binding check raise.

### I4 — Rendering failure never rolls back the issued invoice

**Check — structural, not behavioural.** The module imports no `sqlalchemy`, no
`dotmac_kernel.db`, no `dotmac_files` and no billing package; its port
functions take **no `Session`** (§ 10, `RenderRequestV1` carries the fact by
value). An import-linter contract *"Document rendering renders without
persistence"* enforces the forbidden list. **A module that cannot open a
transaction cannot roll one back.** Statelessness is not merely cheaper here —
it is the enforcement mechanism. Additionally the assembly invokes rendering
**after** the issuance transaction commits, through the outbox
(`dotmac_kernel.messaging.enqueue_event`, which *"commits atomically with
whatever state change the caller is making, and is delivered later by the
relay"*). Billing's spec carries the mirror-image test,
`test_issuance_commits_with_a_failing_renderer_bound`.

**Sensitivity proof.** (a) A fake raising `engine_unavailable`; assert the
issued fact is unchanged and still readable, and that the failure produced a
durable repair signal rather than an exception escaping to the issuer. (b) A
deliberately mis-wired variant calling rendering *inside* the issuance
transaction must be caught by the post-commit check — a check that only ever
sees correct wiring proves nothing.

### I5 — Missing or corrupted bytes are repairable by re-rendering

**Check.** The only inputs are the fact plus `(template_code,
template_version, renderer_code, renderer_version,
projection_contract_version)`, all recorded on the § 6.4 relation. A repair
test forces the stored file to `FileState.MISSING` through
`dotmac_files.service.record_presence`, re-renders from the recorded tuple, and
asserts the new **`projection_digest` equals the recorded one** — never the
byte checksum, which will legitimately differ (§ 5.6). Only `file_id`,
`checksum_sha256` and `byte_length` are then updated; the relation is never
lost, because the relation is the record and the file is the copy.

**Sensitivity proof.** (a) A fact with a required field removed must raise
`fact_incomplete`, not render a blank — this is what proves the fact is
genuinely sufficient. (b) A repair pinned to a *different* `renderer_version`
or `template_version` must be **refused**, never silently accepted as the same
document.

### I6 — A stored file never becomes the only copy of invoice truth

**Check.** (a) An architecture test compares field sets: no money, lifecycle,
coverage or payment-status field may appear in `RenderedDocumentV1` or
`DocumentProjectionV1` that is not already owned by the fact — and every
`RenderedValue` of kind `money` must carry a `source_field` resolving to a real
fact field (I8). (b) The module has no reader that loads from `dotmac-files`
(subsumed by I7). (c) I5's repair test **is** the positive proof: if the
document rebuilds from facts, the file was never the only copy.

**Sensitivity proof.** A fixture adding `total_amount` to
`RenderedDocumentV1` must make the field-set comparison fail.

### I7 — The module does not import `dotmac-files`; the assembly wires it

**Check.** The existing import-linter contract *Modules are independent of each
other* plus explicit forbidden edges from the rendering package to
`dotmac_files` and to any billing package (`make lint-imports`). The assembly
is the only place all three names appear — ADR-0020 A1 (*"Every arrow is a
versioned contract carried by the assembly… never a Python import"*) and
ADR-0024 § 2.

A rendering **engine** behind the port is **not** an Integrator connector.
ADR-0024 § 7's closing paragraph settles it: *"A typed resource driver used
locally by one owning application—such as an object-store `StorageProvider`
beneath `dotmac-files`—is not thereby an Integrator connector. It remains
behind its owning module's published seam, carries no product-domain payload or
cross-application authority."* A PDF engine is exactly that shape.

**Sensitivity proof.** A temporary module file containing `import dotmac_files`
must fail `make lint-imports`.

### I8 — Exact money only: format, never recompute

**Check — structural, not grep.** Every `RenderedValue` of kind `money`,
`quantity`, `date` or `datetime` carries `source_field`, naming the **exact
fact field** its `raw` came from. **A computed number has no source field and
therefore cannot be constructed.** A test asserts every such `source_field`
resolves to a real field on the fact and that `raw` round-trips to the same
exact `Decimal`.

Backed by cheaper guards: no `float(` in the module; no `sum(`, `+`, `-`, `*`
applied to any name matching `_amount|_total|_tax|_balance|_due`; no `Decimal`
arithmetic outside the formatting helper — including no `quantize` in a
formatter, since Sub's quote renderer rounds inside `_money`
(`quote_documents.py:321-322`) and a formatter that rounds can change a figure.

**Sensitivity proof.** A fixture template requesting a total that is
`subtotal + tax_total` must fail, because no `source_field` names it. Not
hypothetical: `docs/inventories/billing-sources.md` § 4 records Sub's admin
surface computing `current_balance = balance_due + available_credit` at
`web_subscriber_details.py:385` — credit added to debt, shipped.

### I9 — Rendering never allocates a document number

**Check.** No sequence, counter, `nextval`, `max(...)+1` or `NumberingProvider`
call in the module; `document_number` is read-only on the fact.

**Sensitivity proof.** A fixture allocating a number inside stage 2 must fail.
Not hypothetical: `app/services/billing/payment_receipt_identity.py:15` returns
`f"#RCP-{payment_id.hex[:8].upper()}"`, and since nothing in Sub's `app/` ever
writes `Payment.receipt_number`, that fabricated 32-bit value is the reference
on **every receipt ever issued**.

### I10 — Rendering never delivers

**Check.** No SMTP, HTTP or delivery client; no symbol matching
`send_|deliver_|notify_|email_`. Delivery belongs to the delivery/Integrator
transport (ADR-0024 § 6). The template-studio audit's boundary applies
identically: *"Template studio renders and returns `(subject, body)`. It does
not decide whether to send, to whom, or over what."*

**Sensitivity proof.** A fixture adding `send_invoice_email(...)` must fail.

---

## 10. `RenderedDocumentV1`, `RenderRequestV1`, and the error taxonomy

### `RenderedDocumentV1`

| Field | Notes |
|---|---|
| `contract_version` | `1` |
| `invoice_id`, `source_fact_version` | echoed from the fact |
| `source_fact_fingerprint` | `sha256` over the canonical fact — binds the result to the *exact* input, not merely a version number |
| `projection` | the full `DocumentProjectionV1` |
| `projection_contract_version`, `projection_digest` | hoisted for indexing and for § 6.4's relation |
| `renderer_code`, `renderer_version`, `template_code`, `template_version` | |
| `media_type` | `application/pdf` or `text/html` |
| `byte_length`, `checksum_sha256` | **storage integrity only** (§ 5.6); `sha256:<hex>` |
| `payload` | `bytes \| None` — inline, or `None` when handed to the assembly as a stream |
| `outcome` | `rendered` / `refused` / `failed` |
| `error_code`, `error_class` | below |
| `rendered_at` | **supplied in the request**, never read from the clock |
| `scope` | echoed |
| `idempotency_key`, `request_fingerprint`, `correlation_id` | rendering's own ledger (§ 3.1), not billing's |

Note what is **absent**: no amount, no lifecycle field, no payment status, no
balance. I6 makes that absence a check rather than a convention.

### `RenderRequestV1`

`fact` (**by value**, never an id — a signature taking `invoice_id: UUID` would
require a lookup, a session, and therefore a transaction, which is how I4 and
I6 get broken), `media_type`, `rendered_at`, `max_bytes`,
`deadline_seconds`, `idempotency_key`, `request_fingerprint`, `correlation_id`.

### Error taxonomy

| `error_code` | `error_class` | Meaning |
|---|---|---|
| `fact_incomplete` | permanent | a required field for this profile is missing or blank |
| `fact_version_unsupported` | permanent | renderer does not accept that `contract_version` |
| `fact_shape_invalid` | permanent | e.g. a credit note carrying tax lines (§ 3.8) |
| `template_not_found` | permanent | no binding for `(profile, profile version, media type)` — names the assembly file as the fix |
| `template_invalid` | permanent | the artifact exists and does not compile |
| `media_type_unsupported` | permanent | renderer does not produce that media type |
| `output_too_large` | permanent | exceeded `request.max_bytes` — refused, never truncated |
| `engine_unavailable` | retryable | the engine or its native dependencies are unusable |
| `engine_timeout` | retryable | exceeded `request.deadline_seconds` |

**There is no `fallback` outcome, deliberately.** Sub's renderer cascades
WeasyPrint → a Pillow-drawn PDF → a hand-built text PDF
(`billing_invoice_pdf.py:1296-1309`, `:589`, `:471`), truncating line items at
**all**, **7** and **30** respectively, signalled only by a `logger.info`. A
fallback producing a materially different document is a **wrong** document, not
a degraded one. `engine_unavailable` is a retryable failure; the assembly
retries or repairs through `dotmac_kernel.idempotency` (ADR-0014, hard rule
23) — never a retry engine inside this module, which would be a second
at-most-once owner.

---

## 11. The `DocumentRenderer` port, its fake, and the contract suite

Plan `2026-08-11-billing-subscriptions-collections.md` C5 names
`DocumentRenderer` as one of the seams needing *"protocol + typed results +
stable error taxonomy + in-memory fake + one parametrized contract suite every
implementation must pass"*. C5's 2026-08-14 revision removed
`DocumentStorageProvider` — *"`dotmac-files` owns that contract already
(A5)"* — and billing's spec § 2.5 confirms both are removed from billing's port
list. **Rendering declares one seam, not two.**

```python
# ILLUSTRATIVE. No package exists; nothing here is authorized.

@runtime_checkable
class DocumentRenderer(Protocol):
    code: str
    version: str

    def media_types(self) -> frozenset[str]: ...
    def accepts_contract_versions(self) -> frozenset[int]: ...
    def projection_contract_version(self) -> int: ...

    def render(
        self, projection: DocumentProjectionV1, request: RenderRequestV1
    ) -> RenderedDocumentV1:
        """Produce bytes from an already-built projection.
        Pure: no clock, no network, no session, no randomness."""
```

The port takes the **projection**, not the fact — so an engine adapter cannot
make a presentation decision, and stages 1 and 2 stay in the module where the
guards are.

**The fake.** `DeterministicFakeRenderer` produces `text/html` from the
projection with no engine at all, so a product develops, tests and runs CI with
**no rendering engine installed**. That matters concretely: WeasyPrint needs
cairo and pango as native libraries, and Sub carries a runtime monkey-patch of
`pydyf.PDF.__init__` (`billing_invoice_pdf.py:193-219`) to survive a version
skew between two transitively pinned packages — duplicated near-byte-identically
in `quote_documents.py:520-541`. That fragility must not be a precondition for
running a test suite.

**The parametrized contract suite.** Every implementation, fake included, must
pass: I2's golden-projection determinism plus its four sensitivity fakes; the
tenant/platform scope-invariance of the projection digest; checksum and
byte-length self-consistency in `sha256:<hex>` form; `media_types()` matching
what is produced; `projection_contract_version()` matching the emitted
projection; the § 10 error taxonomy with the correct `error_class` for each;
`fact_incomplete` on a stripped fact; refusal of an unaccepted
`contract_version`; `output_too_large` refused rather than truncated;
correspondence for `text/html` (§ 5.7); and no clock, socket or `Session`
access.

---

## 12. Assembly wiring

The module does not import `dotmac-files` or billing; the assembly composes
all three, and the reconciler is the loop that drives them.

```python
# ILLUSTRATIVE. Nothing here is authorized.

class InvoiceArtifactReconciler:
    """Assembly-owned. Convergence, not delivery (§ 6.7)."""

    def run_once(self, db, *, provider, limit: int) -> ReconcileReport:
        # Gaps come from billing's READ CONTRACT, never a table read.
        for gap in billing.issued_facts_without_artifact(db, limit=limit):
            fact = billing.load_document_fact(db, gap.invoice_id, gap.fact_version)
            if fact.document_state == "cancelled" and gap.never_rendered:
                continue                      # § 6.11 case 1 exception
            self._converge(db, fact=fact, provider=provider)

        for stale in billing.artifacts_with_missing_objects(db, limit=limit):
            self._repair(db, relation=stale, provider=provider)   # cases 2, 3

        return self._report()                 # case 4 is reported, never repaired

    def _converge(self, db, *, fact, provider) -> UUID | None:
        return persist_issued_document(db, fact=fact, provider=provider)


def persist_issued_document(db, *, fact, provider) -> UUID | None:
    binding = documents.select_template(          # stage 1, assembly-declared bindings
        fact.document_profile_code, fact.document_profile_version,
        media_type="application/pdf",
    )
    projection = documents.project(fact, binding)  # stage 2 — pure
    result = documents.render(projection, rendered_at=fact.issued_at)

    if result.outcome != "rendered":
        # The invoice is already issued (I4). A repairable gap recorded for
        # the reconciler — never an exception raised at the issuer.
        return None

    prepared = dotmac_files.prepare_upload(
        provider, scope=fact.scope, policy=ISSUED_DOCUMENT_POLICY,
        original_filename=f"{fact.document_number}.pdf",
        declared_media_type=result.media_type,
        chunks=(result.payload,),
    )
    # I3 — storage integrity. NOT a determinism check (§ 5.6).
    if (prepared.checksum_sha256 != result.checksum_sha256
            or prepared.size_bytes != result.byte_length):
        raise RenderChecksumMismatch(fact.invoice_id, fact.fact_version)

    stored = dotmac_files.stage_file(db, prepared=prepared)

    # § 6.9 — a TYPED BILLING COMMAND. The assembly writes no module table.
    billing.record_official_document(              # PROPOSED, needs agreement
        db,
        invoice_id=fact.invoice_id, fact_version=fact.fact_version,
        media_type=result.media_type, file_id=stored.id,
        renderer_code=result.renderer_code, renderer_version=result.renderer_version,
        template_code=result.template_code, template_version=result.template_version,
        projection_contract_version=result.projection_contract_version,
        projection_digest=result.projection_digest,
        checksum_sha256=result.checksum_sha256, byte_length=result.byte_length,
        expected_fact_state=(fact.document_state, fact.fact_version),
        idempotency_key=f"{fact.invoice_id}:{fact.fact_version}:{result.media_type}",
    )
    return stored.id
```

Five things this shape guarantees structurally: rendering never sees `db`;
`dotmac-files` never sees a fact; billing never sees bytes; the assembly writes
no module table directly, only typed commands; and the only place that knows
all three surfaces is the composition root.

**Note what is absent from `run_once`: any event.** The reconciler converges
from state. An event handler, when one exists, does nothing but call
`run_once` earlier than the schedule would have. § 6.12's canary is what keeps
that true.

---

## 13. What this document does not do

It does not lift ADR-0017's moratorium, does not claim P11, does not create a
package, namespace, lineage, migration, model or `EXTRACTION.toml`, and does
not authorize a first slice. P8a remains gap-listed. Nothing in billing's spec
is frozen by being cited here, and nothing here is frozen by being written.

Its whole effect is: contracts proposed, the determinism artifact specified,
the convergence owner named with its repair cases and its suppressed-event
canary, invariants named with checks and sensitivity proofs, parity tests
inventoried (`docs/inventories/document-rendering-sources.md`), one wording
correction reported to billing, and **two questions put to Michael as gates** —
§ 6's official-artifact relation, and the final shape of
`InvoiceDocumentFactV1` after § 8's requested fields.

One dependency is worth restating on its own, because it is the cheapest thing
to lose: **`InvoiceArtifactReconciler` and its suppressed-event canary are
required before Vendor CP cutover.** Vendor CP is ADR-0020 § 6's recommended
first billing adopter and is greenfield on invoicing, which means it will be
the first deployment where an issued invoice can exist with no artifact — and
the first where a convergence bug has no legacy path to hide behind.
