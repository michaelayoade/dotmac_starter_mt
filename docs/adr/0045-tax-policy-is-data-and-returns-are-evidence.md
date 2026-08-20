# ADR-0045: Tax policy is data and returns are evidence

- Status: Accepted
- Date: 2026-08-19
- Deciders: Michael
- Supersedes: none
- Related: ADR-0006 (product-first extraction), ADR-0024 (application
  independence), ADR-0031 (sealed cutovers),
  `docs/inventories/tax-sources.md`

## Context

ERP has the qualifying general tax engine, but hardcodes authority/tax
vocabularies, national rates/calendars and report formats. Sub owns important
billing tax and withholding source facts. Tax law, rates, filing calendars and
forms change independently of application releases, and different tenants may
be subject to different policy versions.

## Decision

`dotmac-tax` is the tenant-only owner of configured tax policy, effective-dated
determinations, statutory report snapshots, filing obligations and return
lifecycle evidence.

Authorities, jurisdictions, tax codes, recognition bases, rates, thresholds,
bands, recoverability, report boxes, multipliers and due dates are governed
rows. No country member or statutory value is an enum/default in Python.
Determinations consume exact typed source facts and snapshot the chosen
rule/version and amounts. Equal-ranked overlapping rules fail closed.

Applications retain ownership of invoices, payments, payroll and other taxable
events. They submit deduplicated facts over local service/API/outbox boundaries;
the tax module reads no sibling/application tables. Taxpayer identity validation
and authority portals remain Integrator/product transports. Accounting receives
approved consequences through an opaque adapter rather than a tax-side journal
writer.

Reports snapshot configured boxes. Returns have explicit prepare, approve,
file, accept/reject and amend transitions with separation of duties and an
append-only timeline. An amendment points to the superseded return and never
rewrites filed evidence.

## Consequences

- `mod_tax` is a separately composed tenant schema with forced RLS.
- Current law changes are policy-data changes with provenance and review, not
  emergency application releases.
- Dotmac's cash-received/paid VAT policy and zero-VAT credit-note rule are
  configured source/policy facts, never hidden engine defaults.
- Payroll consumes an evidenced determination through application orchestration;
  neither shared package imports the other.
- ERP cuts over first after accounting exists; Sub continues to own its source
  facts and retires only competing determination/reporting paths.

## Alternatives rejected

**Port ERP tax enums and seeds.** This would make today's jurisdiction policy
the permanent API and silently stale when law changes.

**Let each product calculate tax.** Parallel calculators create irreconcilable
rule versions, reports and return evidence.

**Make the tax module a government-portal client.** Transport credentials and
wire formats belong to connector plugins; combining them with the decision
owner prevents independent composition.
