# Changelog — dotmac-payables

## 0.1.0a3 — unreleased

- Add narrowly verified historical POSTED, wholly unpaid STANDARD invoice
  admission. Preserve original nullable actors and posting time, record a
  permanent immutable receipt, and recognize the full original obligation
  without approving again or producing duplicate imported GL consequences.
- Require kernel a103 for versioned cv1 semantic evidence fingerprints.

## 0.1.0a2 — unreleased

- Historical or imported supplier invoices and credit notes whose
  supplier-issued document number was never captured may persist that absence
  as NULL. New documents created through the service contracts still require
  the supplier document number; internal document numbers remain required.
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
