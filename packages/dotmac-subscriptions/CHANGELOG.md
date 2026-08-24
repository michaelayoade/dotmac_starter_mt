# Changelog

## 0.1.0a3

- Extract Sub's complimentary and sponsored billing-treatment lifecycle into
  the recurring-commercial owner on both declared persistence planes.
- Keep the strictly positive contracted price on the contract line and record
  each waived service period as an exact append-only non-cash grant against a
  rated occurrence; no zero-price concealment or customer-money consequence is
  introduced.
- Add finite policy-bound approval, seven reason codes, sponsor evidence,
  replay-safe approval/revocation, overlap refusal, prospective revocation and
  protected-drift resolution.
- Freeze contract-version changes while an arrangement remains open and block
  grant creation when offer, price, currency, scale or cadence evidence drifts.
- Add additive `su_0003_billing_treatments` tenant/platform tables, RLS and
  privilege boundaries, append-only grant guards and database term-freeze
  canaries while leaving released a1/a2 migration bytes unchanged.

## 0.1.0a2

- Flush the contract version before adding its lines, so recording a contract
  version no longer depends on the host assembly's `autoflush` setting. With
  `autoflush=False` the line insert reached PostgreSQL before the version it
  references and failed on `fk_contract_lines_version`; the module now orders
  its own writes, as `dotmac-billing` already does for its obligation.
- Add the bounded `list_effective_offers` owner read for recurring-offer
  discovery. It returns one deterministic effective version per stable offer,
  immutable exact price snapshots, source provenance, total count and explicit
  pagination for tenant or platform scope.
- Keep product/service grouping, availability, eligibility, formatting and UI
  actions outside the recurring-commercial owner.
- Add explicit `catalog_price` versus `contract_price` offer-version policy.
  Contract-priced offers may omit a fake reference price under any declared
  product charge model, including Sub's `dedicated_negotiated` model.
- Require every stored reference price and every contract-line unit price to be
  strictly positive; complimentary/sponsored treatment is a separate non-cash
  grant against that positive price.
- Rebase the package-only source and six proof suites onto current kernel main;
  discard the unrelated salvage-branch payload and raise the kernel floor to
  a94 for open charge-model/source declarations.

## 0.1.0a1

- Extract Sub's cadence, contract-version, proration, rating-provenance, and
  recurring-occurrence behavior behind product-neutral typed contracts.
- Add structurally immutable offer/price publication with exact money.
- Add selectable tenant and platform persistence planes with one lifecycle.
- Correct clamped month-end rate-unit counting so 31 January to 28 February
  rates as one calendar month, with an explicit product-source regression test.
- Publish `RatedObligationOutputV1` and
  `CommercialEntitlementProjectionV1` without importing their consumers.
- Require `dotmac-kernel>=0.1.0a94`, the first released kernel contract carrying
  the manifest-owned `charge_models` and `obligation_sources` declarations
  consumed by the write-path vocabulary guard.
