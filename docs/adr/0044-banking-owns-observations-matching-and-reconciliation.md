# ADR-0044: Banking owns observations, matching and reconciliation

- Status: Accepted
- Date: 2026-08-19
- Deciders: Michael
- Supersedes: none
- Related: ADR-0006 (product-first extraction), ADR-0024 (application
  independence), ADR-0031 (sealed cutovers),
  `docs/inventories/banking-sources.md`

## Context

ERP has the qualifying banking implementation, but it mixes reusable statement,
matching and reconciliation behavior with GL foreign keys, bank-specific
fetchers and file shapes. Sub has collection-account and operational top-up
reconciliation behavior, but neither is the general bank-statement owner.

A reusable module must let a tenant create and maintain its real bank accounts
without shipping a vendor/bank catalogue or requiring the adopter's GL schema.

## Decision

`dotmac-banking` is the tenant-only owner of configured institutions/accounts,
immutable statement and cash-account observations, configurable matching policy,
explicit match decisions/allocations, and reconciliation snapshots.

Bank accounts are normal tenant CRUD data. Institution codes, account types,
identifiers and opaque cash-account references are supplied by the tenant or
application. Provider names, formats and credentials are not module vocabulary.

Bank/provider I/O belongs in Integrator connectors or product adapters. The
module accepts normalized typed observations and records provenance. It never
queries another product, imports Accounting/Tax/Payroll, or writes a journal.
Accounting remains the owner of cash-ledger truth and receives approved banking
consequences through an assembly adapter.

A suggestion is read-only. Only explicit acceptance creates allocations, and
their total must equal the statement line. A reconciliation is a versioned
snapshot requiring a zero difference and a distinct approver.

## Consequences

- `mod_banking` is a separately composed tenant schema with forced RLS.
- Sub collection-account routing and operational top-up reconciliation remain
  product owners and may reference a configured banking account opaquely.
- ERP cuts over first only after shared accounting exists and ADR-0031's sealed
  switch retires its local statement/matching/reconciliation writers.
- A new provider requires no banking-module change.

## Alternatives rejected

**Port ERP wholesale.** This would make provider and GL schemas part of the
shared owner.

**Use a fixed bank catalogue.** It would turn changing institution/provider
facts into code releases and prevent tenant-defined accounts.

**Let accounting own bank statements.** Cash-ledger decisions and external bank
observations are different facts. Keeping them separate makes drift observable
and reconciliation meaningful.
