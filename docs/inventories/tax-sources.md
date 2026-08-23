# Tax extraction audit — determination, reporting and returns

- **As of:** 2026-08-23
- **Starter:** `5876ffd0bce1`
- **ERP:** `0dc07e4b6dd3`
- **Sub:** `943bc59f8e4c`
- **CRM:** `60daaa2dd305`
- **Backoffice:** `fcdd8270262d`
- **Vendor control plane:** `e6b2bbee815c`

This is the product-first source audit for `dotmac-tax`. Tax law changes, so
the package deliberately separates a generic determination/reporting engine
from governed tenant policy data. This document is engineering evidence, not
legal or tax advice.

## Finding

ERP is the only qualifying general implementation. Its finance/tax models and
services implement effective rules, input/output/recoverability calculations,
report snapshots and filing transitions. But it also hardcodes a tax-type enum,
Nigeria-specific seeds/rates/due offsets, and authority-specific report/export
formats. Those are source-product couplings, not the reusable contract.

Sub has a mature, narrower source-fact owner in
`app/services/tax_accounting.py`: billing tax amounts and withholding lifecycle.
It explicitly does not own tax-account mapping or posting. Sub also has legacy
tax-policy and determination writers in `TaxRate`, `CustomerTaxPolicy`, offer
VAT fields, recurring billing and prepaid renewal. Those local decisions must
retire while Sub's source facts remain. `dotmac-tax` never reads Sub's tables.
CRM is not a general engine, but it still computes VAT from an environment
default in `revenue_service_report.py` and stores/recalculates vendor-quote VAT
in `vendor.py`; both are typed legacy writers, not evidence of no writer.
Backoffice and the vendor control plane have no competing tenant tax owner.

## Current-law source check

The audit checked the official Nigeria Revenue Service tax-law index and the
official Nigeria Tax Administration Act 2025 published by NRS. Those sources
confirm why rates, filing calendars, identity requirements and report shapes
must be effective-dated governed data rather than code constants:

- <https://www.nrs.gov.ng/tax-laws/index.html>
- <https://www.nrs.gov.ng/uploads/NIGERIA_TAX_ADMINISTRATION_ACT_2025_8c945071a7.pdf>

The package does not scrape or reinterpret those pages at runtime. A qualified
operator/legal process approves policy rows and explicit obligations; the
engine records which version produced each consequence.

## Target owner

`dotmac-tax` owns tenant-configured authorities, jurisdictions, tax codes,
effective rules and bands; tax-specific, effective-dated party/supply/place
classifications; deterministic ordered determination sets from exact typed
facts; versioned statutory-report definitions/boxes and generated snapshots;
explicit filing obligations/due dates; and prepare/review/file/accept/reject/
amend return evidence.

It does not own invoice/order/payroll facts, taxpayer identity transport,
government portal connectivity, tax payment/remittance transport, GL accounts,
journal entries, fiscal periods, or country vocabulary compiled into code.

## Policy and source-fact boundary

- Authority, jurisdiction, tax-kind code, recognition basis, rate, band,
  recoverability, report box, multiplier, period and due date are data.
- A product submits source identity/version, evidence reference, occurrence
  date, side, recognition basis, money and opaque party/supply/place refs. The
  tax owner resolves evidenced classifications per tax code; direct category
  inputs remain only for a1 compatibility and must agree with owned rows.
- Rule selection fails closed when equally ranked rules overlap.
- One source fact may select multiple tax codes. The immutable determination
  set snapshots net/tax/gross totals, and ordered components snapshot each
  selected rule/version, calculation base, treatment, classifications and
  amounts. A changed source version is a new fact, not an in-place rewrite of
  evidence.
- Standard-rated, zero-rated, exempt and out-of-scope remain distinct evidence
  even when they all produce a zero amount. VAT is one configured tax code;
  custom levies use the same owner and do not require a product migration.
- Report values snapshot configured boxes; return events are append-only.

The approved Dotmac VAT accounting policy remains a product/operator policy
row: recognize cash received/paid, never invoice totals. Credit-note facts carry
zero VAT under the approved source rule. Neither statement becomes a hidden
country default in Python; the evidence references the configured rule version.

## Couplings and defects not ported

1. No `TaxType` enum or fixed authority membership.
2. No embedded statutory rate, threshold, currency, due-day offset, form box,
   report filename, or authority portal endpoint.
3. No direct invoice, payment, payroll, account or journal query.
4. No external tax-identity validation inside determination. Integrator or the
   product records validation observations without overwriting local identity.
5. No sibling import. Payroll may request a tax determination through
   application orchestration, then submit the result as evidenced input.

## Composition and cutover

ERP remains the tax decision/reporting authority until shared accounting exists
and the cutover has replayed source facts, compared rule/version and amounts,
reconciled report boxes/obligations, then sealed one writer switch. Sub remains
owner of its billing/WHT source facts and sends them over a versioned API/outbox
contract. Backoffice is a later exact-pin consumer. No package/table creation is
authority evidence.

The implementation branch's kernel allocation is provisional and must be
rebased/renumbered against concurrent alpha-train allocations before integration.
