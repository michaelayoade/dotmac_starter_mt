# Changelog

## 0.1.0a4

- **Breaking:** require a `catalog_price` contract line to name `(offer_version, price_key)` and
  equal that published `OfferVersionPrice` (charge model, amount, currency,
  scale). `ContractLineInput` gains an optional `price_key`: required for a
  catalog-priced offer version, refused for a contract-priced one. `price_key` is required with no
  deprecation period: a caller that built catalog-priced lines without a key
  now fails. `price_key` is now part of the contract-version replay
  fingerprint. `record_contract_version` refuses a mismatch with
  `contracts.catalog_price_key_required`, `contracts.catalog_price_not_found`,
  `contracts.catalog_price_mismatch` or `contracts.price_key_not_allowed`.
- `su_0004_catalog_price_line` repeats the rule in PostgreSQL on both planes: a
  nullable `price_key` column, a composite foreign key to the price it names
  (MATCH SIMPLE, so a contract-priced line is skipped) and an INSERT trigger for
  the cross-table equality. Contract lines were already immutable. Existing
  lines all have a NULL key, so the foreign key validates immediately; the
  upgrade logs, and never repairs or backfills, the catalog-priced lines that
  pre-date the rule (grandfathered; see
  `docs/inventories/subscriptions-catalog-price-exceptions.md`).
- `su_0005_line_coverage_overlap` refuses, on both planes, an occurrence whose
  rating coverage `[rating_coverage_start, rating_coverage_end)` overlaps another
  non-cancelled occurrence of the same (tenant, contract, contract line key), so
  one contract line is never charged twice for an instant, including the same
  coverage recorded under a new contract version. It is a BEFORE INSERT OR UPDATE
  trigger on the su_0001 pattern (advisory lock, then `exclusion_violation`): no
  extension, nothing in `public`, not deferrable, and it refuses to run under
  REPEATABLE READ. A correction may overlap its own correction chain (Billing
  reads a correction as a replacement) and a superseded occurrence stops
  blocking; a correction must replace an occurrence of the same contract line,
  and a new unique `(tenant, corrects_occurrence_id)` gives an occurrence at most
  one successor. A trigger cannot be `NOT VALID`, so existing overlapping current
  occurrences or duplicate corrections make the upgrade refuse before any DDL.
- `su_0003` and earlier keep their released bytes.

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
