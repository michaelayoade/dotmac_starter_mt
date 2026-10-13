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

## Amendment — 2026-08-23: one fact may produce multiple taxes

The original singular determination shape was too narrow. VAT is one tax code,
not the tax abstraction, and a taxable event may also attract independent or
compound levies. One source fact therefore owns one immutable determination
set containing one selected component per applicable tax code. Components are
ordered by governed calculation sequence; a component may use the source
amount or the source amount plus previously calculated tax. Duplicate
sequences, equal-ranked rules and unsupported inclusive combinations fail
closed. Once a tax code declares candidate rules for a fact signature, a fact
whose effective party, supply and place classifications match none of them also
fails closed; an intended non-charge must be an explicit `exempt` or
`out_of_scope` rule rather than a missing custom-tax component.

Party, supply and place category decisions are effective-dated,
tax-code-specific policy evidence owned by `dotmac-tax`, with basis, approval,
evidence and source-version provenance. A caller may still provide direct
categories for the published a1 compatibility contract, but disagreement with
an owned classification is a conflict. `standard_rated`, `zero_rated`,
`exempt` and `out_of_scope` are distinct treatments; a zero amount never
substitutes for legal identity.

Product catalogues contain no tax flag or statutory percentage. ERP remains
the first statutory-policy/reporting cutover under this ADR. Sub may contain
its existing exemption, inclusive-label and bulk-plan-change defects before
that cutover, but does not become a parallel tax decision owner.

## Amendment — 2026-10-13: explicit immutable policy adjustment

Rates and supported calculation bases are operator-authored policy. A new rule
version alone must not resolve an equal-ranked overlap. Tax a5 adds an
append-only `TaxRuleSupersession` edge that names the predecessor, replacement,
boundary, actor, publisher, reason, evidence and retry-stable source identity.
Replacement preserves applicability and priority, starts within the
remaining predecessor interval (including its start for scheduled corrections), and inherits its end. Only unsuperseded leaves
may be replaced; the tenant/code lock serializes competing authors. This is an
additive tx_0004 migration; published tx_0001–tx_0003 bytes remain unchanged.

Selection suppresses explicit predecessors at and after their boundaries in
the candidate SQL statement. It retains unrelated ambiguity, duplicate sequence
and inclusive/compound refusals. Sealed source replay precedes current policy
selection, so subsequent policy authorship cannot change issued financial facts.
Any published rule or edge prevents downgrade: reverting would reopen band
membership or revive superseded policy.
The new owner command only flushes; the root transaction owns rollback/retry.

Preview reuses the same selector/calculation builder but projects a distinct
advisory contract without financial-fact identity or seals. Current persisted
policy is its scope; unpublished-policy simulation is not implemented here.

Qualifying provenance remains ERP tax policy/reporting and Sub billing source
facts as recorded in `packages/dotmac-tax/EXTRACTION.toml` and
`docs/inventories/tax-sources.md`. This amendment implements the user's
2026-10-13 composable UI policy requirement as an owner API, not a claim of UI,
package publication, product adoption or authority cutover. The source canary
`tests/test_tax_policy_supersession.py` calls the actual public owner to create,
determine, replace, evaluate the boundary and replay unchanged evidence. Hosted
PostgreSQL canaries cover role/RLS, append-only edges, competing authorship and
upgrade behavior. Their presence is not a claim that hosted CI has run.

The same additive migration closes inherited late-band INSERT admission.
Existing rules receive a constant `is_published=true` column value. New rules
build and seal within one owner transaction; only a content-preserving
false-to-true transition is admitted. A deferred constraint refuses unpublished
commit, and band insertion locks the parent and requires its unpublished state.
This avoids an independent seal-row snapshot and adds no persisted draft editor.
Current-policy reads exclude unpublished rows. Old a4 binaries require a policy
write fence at tx_0004 because they do not perform the new transition; a5 retains
the public a4 signatures and DTOs. Upgrade drains rules/bands with a bounded
five-second lock. Downgrade locks rules/bands/edges, disables row-security
filtering for a fail-closed global empty check, and restores the prior trigger
only for an empty store. Caller timeout/security settings are restored on
success and transaction rollback restores failures.
