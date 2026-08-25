# Changelog

## 0.1.0a3

- Port `dotmac_sub`'s complimentary and sponsored billing treatment as a
  product-neutral owner on both declared planes (ADR-0006 product-first;
  sources and split recorded in
  `docs/inventories/subscription-billing-treatments-extraction.md`).
- Add `subscription_billing_arrangements` and `subscription_billing_grants`,
  and their `platform_*` counterparts, in the additive `su_0003` revision.
  `su_0001` and `su_0002` shipped in 0.1.0a2 and keep their released bytes.
- Record the seven ported reasons — `internal_service`, `staff_benefit`,
  `partner_service`, `community_support`, `commercial_concession`,
  `sponsored_service`, `other_approved` — as an OPEN declared registry owned by
  this module (ADR-0008). The backing column is a plain string with no CHECK, so
  a product declaring an eighth reason needs neither a module release nor a
  migration. Sub's three PostgreSQL ENUM types are deliberately not ported.
- Make the lifecycle structural rather than procedural: a mandatory end date is
  `NOT NULL`; the approved recurring ceiling is `> 0`; sponsored treatment
  requires a sponsor or cost-centre reference; approval terms are frozen by
  trigger with `active -> revoked` as the only permitted update; overlapping
  open arrangements on one contract line are refused under an advisory lock;
  grants are append-only; and a NEW contract version for a contract with an open
  arrangement is refused — the product-neutral port of Sub's
  `protect_subscription_billing_treatment_terms`.
- Keep the contract line's real, strictly positive price and apply the waiver as
  a bounded non-cash grant. One CHECK relates the contracted amount, the approved
  ceiling and the foregone amount, so a zero-price concealment, a zero grant, and
  a grant above either bound are all unrepresentable. The grant tables carry no
  customer-money column at all.
- Publish `NonCashGrantOutputV1` with the exact foregone-revenue evidence and
  stop there. Sub's entitlement repair, billing-anchor advance, invoice
  suppression and sponsor/expense posting stay with their owners.
- Add `preview_billing_arrangement`, `approve_billing_arrangement`,
  `revoke_billing_arrangement`, `resolve_billing_arrangement` (`standard`,
  `effective`, `protected_drift`) and `record_non_cash_grant`. The product
  supplies the approval horizon as an `ApprovalPolicySnapshot`; the module
  records and enforces the snapshot and never reads a product setting.

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
