# Changelog

## 0.1.0a2 — UNRELEASED

- Add the bounded `list_effective_offers` owner read for recurring-offer
  discovery. It returns one deterministic effective version per stable offer,
  immutable exact price snapshots, source provenance, total count and explicit
  pagination for tenant or platform scope.
- Keep product/service grouping, availability, eligibility, formatting and UI
  actions outside the recurring-commercial owner.

## 0.1.0a1

- Extract Sub's cadence, contract-version, proration, rating-provenance, and
  recurring-occurrence behavior behind product-neutral typed contracts.
- Add structurally immutable offer/price publication with exact money.
- Add selectable tenant and platform persistence planes with one lifecycle.
- Correct clamped month-end rate-unit counting so 31 January to 28 February
  rates as one calendar month, with an explicit product-source regression test.
- Publish `RatedObligationOutputV1` and
  `CommercialEntitlementProjectionV1` without importing their consumers.
