# Changelog

## 0.1.0a2

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
