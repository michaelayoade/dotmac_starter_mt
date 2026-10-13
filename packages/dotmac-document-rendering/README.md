# dotmac-document-rendering

`dotmac-document-rendering` turns a complete immutable billing fact into a
canonical semantic document and then into complete HTML or PDF bytes. It is a
stateless optional module: it owns no tables, migrations, sessions, clocks,
network calls, retry ledger, or stored artifacts.

The ownership boundary is deliberate:

- Billing owns invoice meaning, numbering, frozen facts, corrections, and the
  official-artifact relation.
- This package owns template selection, semantic projection, exact formatting,
  renderer provenance, and typed rendering outcomes.
- `dotmac-files` owns opaque bytes at rest.
- The consuming assembly owns bindings, orchestration, idempotency, retry, and
  reconciliation.

The published V1 input is `InvoiceDocumentFactV1`. Every value needed to reproduce an
issued document is carried by value; a renderer never looks an invoice,
customer, setting, brand asset, tax policy, FX observation, or payment account
up while rendering.

## Composition

An assembly constructs a `TemplateCatalog` from immutable
`DocumentTemplateV1` artifacts and explicit `DocumentProfileBinding` rows, then
calls `render_document` with a `DocumentRenderer`. The package includes a pure
`DeterministicHtmlRenderer`, a credential-free `InMemoryDocumentRenderer` fake,
and a `PdfDocumentRenderer` adapter around an explicitly installed local
`PdfEngine`.

Consumers should run `renderer_contract_violations` against every renderer
implementation. An empty result is the reusable conformance pass.

The package is currently audit-complete but unadopted. Supply does not move
authority; the first real product cutover remains a separate composition and
retirement change.

## Frozen Billing fact successor (source candidate)

`0.1.0a2` adds a separate V2 surface; V1 constructors, validation, canonical
wire form, hashes and output remain unchanged. V2 carries Billing's persisted
fact UUID, exact version and recording instant, with the original Billing
presentation-model digest retained separately from the renderer's semantic
projection digest and the `sha256:` bytes checksum. It uses the shared
`dotmac_kernel.semantic_encoding` cv1 domain encoding, requiring published
Kernel `>=0.1.0a103`.

An assembly maps the public `StoredInvoiceDocumentFactV1` query result by value
into V2. Preserve opaque profile versions, structured addresses, trading names,
all registered and tax identifiers, frozen timezone, ordered exact line amounts
and `price_source_version`. Recorded line provenance is the stored parent fact
and line number. Billing V1 freezes neither a numbering series version nor
price origin tags: leave the series version absent and do not invent catalog,
manual or rated provenance. A missing due date is an explicit omission; do not
invent payment terms. Verified due-date bases freeze payment-terms code and
version, which the map preserves; unknown/unverified bases may omit that
evidence. No financial amount is recomputed.

Bank fields and payment narrative come from the fact. Billing's current public
V1 has no payment URL. Linked V2 examples qualify a future source-owned frozen
HTTPS value; they do not establish a Billing V1 mapping. Never reinterpret
narrative as a link or look up live `brand.app_url`. Links are escaped inert
presentation values and the renderer performs no fetch.

This successor is unpublished source. Sub Kernel98 qualification, renderer
release and install-back proof, exact product pinning, artifact reconciliation,
parity and retirement of displaced writers remain future work. No Billing or
Numbering runtime, migration, product writer or official-artifact authority
changes here.

V2 PDF success requires a strict in-memory structural check and complete
ordered extracted text correspondence within the bounded PDF profile. It rejects
blank, incomplete, encrypted and active/external documents, and permits supported
compressed streams under decoded-content budgets. PDF annotations are refused;
a frozen payment URL is visible text only. This qualifies output from a trusted
composed engine; it does not prove visual fidelity, accessibility, a hard CPU
deadline or the absence of transient allocations. Actual PDF engine and product
qualification remain pending.

V2 hashes and financial text use an isolated Decimal context, preserving every
admitted digit independently of caller precision, rounding or traps. Coefficients,
absolute exponents and scaled Money integer digits are bounded at 4,096; values
beyond those bounds fail with a typed refusal. The document currency contract
and Kernel103 cv1 encoding stay unchanged, and caller contexts are preserved.
