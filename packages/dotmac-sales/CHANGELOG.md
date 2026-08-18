# Changelog — dotmac-sales

## 0.1.0a1 — unreleased

- Product-first port of Sub's Pipeline, Stage, Lead and Quote behavior.
- Versioned, product-neutral `AcceptedQuoteHandoffV1` boundary.
- Exactly-once acceptance through the kernel ledger and transactional outbox.
- Tenant-only `sa` lineage with FORCEd RLS and database-enforced accepted-Quote
  immutability.

This version is not release evidence until all focused Postgres and repo gates pass.
