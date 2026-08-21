# Changelog — dotmac-document-rendering

## 0.1.0a1 — UNRELEASED

- Adds immutable `InvoiceDocumentFactV1`, template, projection, request, result,
  renderer, PDF-engine, and typed error contracts.
- Adds deterministic template selection and semantic projection with exact
  decimal-string money, explicit currency/minor units, timezone-bound dates,
  ordered rows, omission decisions, and source-field correspondence.
- Adds a self-contained HTML renderer and a persistence-free PDF engine adapter.
- Adds reusable renderer conformance checks covering repeated projection,
  projection integrity, complete-byte integrity, provenance, and HTML semantic
  correspondence.
- Records the completed Sub and ERP product-first audit and keeps the zero-
  consumer package unpublished until the first assembly cutover is ready.
