# Changelog — dotmac-payables

## 0.1.0a2 — unreleased

- Supplier-issued document numbers are optional on supplier invoices and
  credit notes. Absent or blank input is stored as NULL; internal document
  numbers remain required, and supplier-document duplicate checks apply only
  when a number is supplied.
- Adds `pa_0002_optional_supplier_doc`, which makes both persisted
  `supplier_document_number` columns nullable.

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32479337788`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Product-first supplier-liability owner extracted from ERP behavior.
- Invoice, credit, obligation, credit-application, settlement-observation and
  accounting-consequence/receipt contracts.
- Independent `pa` migration lineage in `mod_payables` with forced RLS.
