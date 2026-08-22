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

The public input is `InvoiceDocumentFactV1`. Every value needed to reproduce an
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
