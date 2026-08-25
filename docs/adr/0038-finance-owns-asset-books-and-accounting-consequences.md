# ADR-0038: Finance owns asset books and accounting consequences

**Status:** Accepted  
**Date:** 2026-08-18

## Context

ERP has the fleet's only production-used fixed-assets accounting stack, but it
mixes physical lifecycle and carrying-value state, leaves important child tables
without direct RLS, controls transactions inside services and has accounting
paths that tests only prove through mocks. Assets and Finance therefore cannot
be extracted as one aggregate: “fully depreciated” answers an accounting
question, not whether equipment is in service, damaged, assigned or disposed.

The product-first audit is recorded in
[`finance-asset-accounting-sources.md`](../inventories/finance-asset-accounting-sources.md).

## Decision

`dotmac-finance` is the tenant-only owner of fixed-asset books, depreciation,
impairment and reversal, revaluation balances, accounting derecognition and the
immutable balanced consequences of those decisions.

V1 has these boundaries:

1. A book references a physical asset by opaque UUID plus source version and
   evidence. It has no sibling-module import or foreign key. Assets remains the
   owner of identity, state, condition, location, custody, maintenance and the
   operational disposal workflow.
2. Account, cost-centre and period references are opaque. Finance validates and
   persists balanced subledger consequences but owns no chart of accounts,
   fiscal period, journal entry, cash or payment. A local idempotent reconciler
   projects immutable consequences into the application's general ledger.
3. Book and consequence mutation occur in the caller's transaction. Services
   flush and never commit, roll back or construct sessions.
4. Every table is directly tenant-scoped with forced RLS and tenant-composite
   child relationships. Calculated depreciation lines, accounting events,
   consequences and consequence lines are append-only. A deferred database
   constraint refuses an empty or unbalanced consequence.
5. Depreciation methods are a closed implemented vocabulary: straight-line,
   declining-balance and double-declining. Unsupported methods fail. Units of
   production cannot land until a typed usage-measurement owner and parity
   contract exist.
6. Impairment, reversal, revaluation and derecognition require independent
   requester/approver identities, current book versions and immutable evidence.
   They never change a physical asset state.

The independent lineage is `fn`, branch `finance`, schema `mod_finance`. The
reference assembly builds and proves the package but does not compose it.

## Consequences

ERP is the first cutover because it supplies the qualifying implementation.
Authority does not move when this package is built or published: backfill,
shadow comparison, a sealed switch and retirement of ERP's competing writers
are mandatory. Backoffice is the second candidate consumer.

The first release intentionally does not claim a complete general ledger or a
complete IAS 16 implementation. Component accounting, annual useful-life and
residual-value review workflow, tax books, foreign-exchange policy and journal
period controls remain explicit later slices. This keeps one coherent owner
without creating a second incomplete accounting engine beside ERP.
