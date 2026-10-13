# Changelog — dotmac-document-rendering

## 0.1.0a2 — UNRELEASED

- Adds an independent V2 frozen Billing-shaped contract and complete semantic
  projection while preserving the V1 public contract and golden.
- Retains actual persisted fact identity, recording time, producer digest,
  structured parties, all identifiers and ordered exact line provenance.
- Adds explicit missing-due-date/series-version handling and inert qualified
  HTTPS payment presentation without live lookup or URL fabrication.
- Uses Kernel103 shared cv1 domains for V2 semantic digests; bytes keep SHA-256.
- Isolates Decimal encoding/display from ambient precision, rounding and traps,
  retaining exact admitted digits and caller context. Oversized coefficients,
  exponents and scaled Money integers receive typed permanent refusals.
- Adds bounded strict in-memory PDF parsing and ordered text correspondence,
  rejecting encrypted, incomplete and active/external output. PDF annotations
  remain outside the bounded profile; production engine qualification is pending.
- Source candidate only: release, install-back, Sub qualification and product
  reconciler adoption remain separate work.

## 0.1.0a1 — published

- Adds immutable `InvoiceDocumentFactV1`, template, projection, request, result,
  renderer, PDF-engine, and typed error contracts.
- Adds deterministic template selection and semantic projection with exact
  decimal-string money, explicit currency/minor units, timezone-bound dates,
  ordered rows, omission decisions, and source-field correspondence.
- Adds a self-contained HTML renderer and a persistence-free PDF engine adapter.
- Adds reusable renderer conformance checks covering repeated projection,
  projection integrity, complete-byte integrity, provenance, and HTML semantic
  correspondence.
- Records the completed Sub and ERP product-first audit. Published and
  install-verified; publication did not establish product adoption.
