# Catalog variant decomposition implementation plan

- **Goal:** stop tax status, negotiated prices, complimentary service and
  technical characteristics from creating duplicate product/offer rows by
  moving each decision to its named owner, migrating authority explicitly and
  retiring the parallel Sub paths.
- **Authorized:** 2026-08-23 by Michael. Architecture and migration choices may
  be made to reach the goal, subject to repository safety, CI and release
  controls.
- **Decision sources:** ADR-0045 and its 2026-08-23 amendment,
  `docs/ARCHITECTURE.md`, each package `EXTRACTION.toml`, and the adopting
  product's checked-in source-of-truth documents. This file is execution intent,
  not proof of as-built behavior.

## Fixed decisions

- VAT is one configured tax code. `dotmac-tax` must determine any number of
  ordered, independently reported taxes for one source fact.
- Tax classifications are effective-dated and tax-specific. Party, supply and
  place classification rows carry basis, evidence, publisher and source
  version. A zero amount does not erase `zero_rated`, `exempt` or
  `out_of_scope` identity.
- ERP remains the first statutory-policy/reporting cutover. Sub containment may
  land first, but Sub does not become a parallel tax owner.
- Product/service catalogue rows own sellable and technical shape, not tax
  policy, a customer's negotiated amount or the reason service is non-cash.
- A negotiated contract line always carries the actual positive unit price. A
  catalogue may publish a genuine reference rate, but no fake rate is required:
  a dedicated-negotiated offer version may be price-less under an explicit
  declared charge model, and the contract service refuses activation without a
  line price.
- Complimentary and sponsored service are positive-price contract lines plus a
  non-cash grant. The product-neutral arrangement/grant lifecycle belongs in
  `dotmac-subscriptions`; Sub retains only its product entitlement consequence,
  and Accounting retains sponsor/foregone-revenue postings.
- No legacy field or guard is removed until backfill, shadow comparison and the
  named read/write cutover are proven. No fallback calculator or second writer
  survives retirement.

## Task list

The initial product-first inventory is pinned to Starter
`5876ffd0bce17172fa2dc6ac6d09b48d877fadf8`, Sub
`943bc59f8e4ca0849c7de578bc9dbc17c57b116f`, ERP
`0dc07e4b6dd36260c9510a7115dbdc656e2a19a5`, and CRM
`60daaa2dd305696636632f48505ab784110a55d2`. Each later adopter slice refreshes
its own coordinate before implementation.

### A. Establish evidence and containment

- [x] A1. Pin exact Starter, Sub and ERP revisions used for implementation and
  refresh the product-first inventories before each cross-repository slice.
- [x] A2. Add Sub regression canaries proving automated recurring billing and
  prepaid renewals honor the effective customer exemption policy during the
  containment window.
- [x] A3. Repair Sub's recurring and prepaid tax resolution so one compatibility
  policy service owns selection until `dotmac-tax` cutover; preserve the newer
  one-off/manual consumers that already honor customer exemption and remove
  duplicate determination paths.
- [x] A4. Correct the offer-form language and input semantics so inclusive price
  is not confused with taxable/exclusive price; backfill or flag ambiguous
  historical operator choices for review.
- [x] A5. Route bulk tariff changes through the same plan-change owner and
  enforce service type, billing mode, region and open billing-treatment guards.
- [x] A6. Add money-impact reconciliation queries and operator remediation for
  customers previously charged under the exemption or inclusive-label defects;
  do not silently rewrite issued invoice evidence.

### B. Make `dotmac-tax` genuinely composable

- [x] B1. Write canaries for multiple tax components, explicit ordering,
  compounding, tax-specific classifications and treatment snapshots.
- [x] B2. Add `TaxDeterminationSet` plus component links while retaining a1
  single-component replay compatibility.
- [x] B3. Add effective-dated, append-only `TaxSubjectClassification` rows for
  party/supply/place refs with evidence and source-version idempotency.
- [x] B4. Add rule treatment, sequence and calculation-base policy data; reject
  ambiguous sequences/rules and unsupported inclusive combinations.
- [x] B5. Create additive `tx_0002_multi_tax`; keep released `tx_0001_tax`
  byte-identical and enroll the lineage in the released-migration guard.
- [x] B5a. Refresh ERP/Sub/CRM writer claims at immutable revisions: ERP is the
  qualifying source; Sub and CRM contain typed legacy writers to retire.
- [ ] B6. Complete zero/exempt/out-of-scope, inclusive, replay/conflict,
  cross-tenant RLS, append-only and a1-to-a2 upgrade canaries on PostgreSQL.
- [ ] B7. Run package, unit, architecture, migration and full repository gates
  on Observer from a fresh exact-commit worktree; fix every failure.
- [ ] B8. Publish `dotmac-tax 0.1.0a2`, install it back from the private index,
  register it, tag the peeled commit and merge the generated release record.

### C. Adopt tax without creating another owner

- [ ] C1. Define ERP typed source-fact and accounting-consequence adapters;
  keep tax free of invoice/payroll/GL model imports. The exact current-owner,
  typed-contract, cohort and retirement design is recorded at pinned ERP source
  in `docs/architecture/dotmac-tax-adoption-boundary.md`; implementation remains
  gated on the registry-verified tax a2 release.
- [ ] C2. Backfill ERP tax authorities, jurisdictions, tax codes, rules,
  classifications, report boxes and obligations with provenance and legal/
  finance approval.
- [ ] C3. Shadow ERP determination sets and reports against the qualifying
  engine; compare selected code/rule/version, classification, base, treatment,
  tax/recoverable amounts, boxes, payable totals and obligations.
- [ ] C4. Cut over ERP one fact family at a time, publish Accounting consequences
  through the typed seam, seal the writer switch and retire fixed enums,
  statutory constants, calculators and filing writers.
- [ ] C5. Define Sub's versioned billing-fact adapter/outbox after ERP authority
  is proven. Backfill CustomerTaxPolicy into evidenced classifications and map
  offer/service facts to supply and place refs.
- [ ] C6. Shadow all three Sub resolver outputs against determination sets,
  reconcile mismatches, switch recurring/prepaid/manual reads together, and
  remove `TaxRate`, `CustomerTaxPolicy.vat_exempt`, `with_vat`, `vat_percent`
  and all product-local tax resolver writes.
- [ ] C7. Remove `with_vat`/`vat_percent` from live-offer immutability only in the
  same cutover that makes them unreadable/unwritable compatibility data; then
  remove their columns in a later proven migration.

### D. Move technical variants to `dotmac-service-catalog`

- [x] D1. Validate the current package against its Sub extraction dossier and
  confirm speed, access type and aggregation characteristics have no commercial
  fields or sibling imports. The audit found that the untagged four-table draft
  stored definitions but no versioned values and made one family point at one
  specification; the pre-release root is corrected to stable family/specification
  identities, evidenced effective versions and typed characteristic values.
- [ ] D2. Add the package to the release lane if required, run its Observer
  gates, publish the exact declared version and merge the release record.
- [ ] D3. Compose the exact release in Sub with lineage bindings and no direct
  cross-application/database dependency.
- [ ] D4. Backfill versioned `ServiceSpecification`, `PlanFamily` and
  characteristics from offers; preserve stable mappings and exception evidence.
- [ ] D5. Shadow technical eligibility/display/provisioning reads, reconcile to
  zero unexplained drift, switch the named readers and retire offer-owned speed,
  access-type and aggregation writers/guards.

### E. Finish and release `dotmac-subscriptions`

- [x] E1. Re-audit the stranded subscription package branch against current
  Sub and Starter main; extract only package-owned source/tests and discard the
  unrelated ~200-file branch payload.
- [x] E2. Rebase onto the current kernel allocation (`su` /
  `mod_subscriptions`), preserve one lineage, refresh `EXTRACTION.toml`, and add
  sensitivity-proven architecture/RLS tests.
- [x] E3. Permit price-less dedicated-negotiated offer versions under a declared
  open-vocabulary charge model while retaining optional genuine reference-rate
  prices; require currency/minor-units and positive unit price on every active
  negotiated contract line.
- [x] E4. Add immutable contract/version/line price evidence and guarded
  effective dates; no customer-named catalogue row is needed for price alone.
- [ ] E5. Complete Observer gates, publish the package, verify registry install,
  tag the peeled commit and merge the release record.

### F. Migrate negotiated-price variants

- [ ] F1. Inventory dedicated/customer-named offers, their subscriptions,
  currencies, billing periods, discounts and every price consumer. The pinned
  source-path and cutover inventory is recorded in Sub's
  `docs/designs/NEGOTIATED_PRICE_CONTRACT_LINE_MIGRATION.md`; F1 remains open
  until a read-only inventory from an explicitly named production target is
  classified and adjudicated.
- [ ] F2. Backfill a contract line with the actual negotiated price and evidence
  for each subscription; link all lines to the shared offer/specification.
- [ ] F3. Shadow renewal, proration, invoice draft, plan change, reporting and
  revenue outputs against the old offer price.
- [ ] F4. Switch all commercial reads to the contract-line owner, prohibit new
  customer-named offers created only for price, migrate references and retire
  unreferenced duplicate rows without losing historical invoice snapshots.

### G. Extract complimentary/sponsored grants product-first

- [x] G1. Re-audit Sub's `SubscriptionBillingArrangement` and
  `SubscriptionBillingGrant` models, services, trigger and tests as the mandatory
  product-first source. The exact port/product split and a2-to-a3 sequencing are
  recorded in `docs/inventories/subscription-billing-treatments-extraction.md`.
- [ ] G2. Port the product-neutral lifecycle into `dotmac-subscriptions`:
  complimentary/sponsored treatment, seven-value reason vocabulary, mandatory
  end date, maximum recurring amount, approval/revocation evidence and immutable
  price fields while open.
- [ ] G3. Keep positive contracted price on every line and apply a non-cash
  grant up to the approved cap; refuse zero-price concealment and preserve
  foregone-revenue evidence.
- [ ] G4. Keep Sub's entitlement/anchor effect in a thin product adapter and
  keep sponsor receivable/expense/posting decisions in Accounting.
- [ ] G5. Backfill historical zero-price offers and unbilled customers into real
  line prices plus evidenced grants; require operator adjudication where no
  reason, approver, sponsor or end date can be proven.
- [ ] G6. Shadow invoice/renewal/entitlement/revenue outputs, switch the writer,
  block new zero-price offers, migrate references and retire duplicate free rows
  only after historical snapshots remain reachable.

### H. Close the programme

- [ ] H1. Prove no offer clone is required for tax, negotiated price,
  complimentary reason or technical characteristic changes.
- [ ] H2. Add architecture guards that prohibit tax fields/statutory rates on
  catalog rows, negotiated amounts outside contract lines, zero-priced grant
  concealment and bypasses of the plan-change owner.
- [ ] H3. Run focused, unit, architecture, PostgreSQL/RLS, migration, browser and
  full-suite validation on Observer for every exact product/package commit; CI
  remains merge acceptance.
- [ ] H4. Update each repository's as-built ownership map, ADR amendments,
  migration/cutover evidence and package dossiers in the same change as its
  behavior.
- [ ] H5. Refresh durable knowledge with the final owners, exact release runs/
  tags/adoption dossiers and retired paths. Do not record secrets or replace
  checked-in sources of truth with memory.

## Completion definition

The programme is complete only when every tax, negotiated-price,
complimentary/sponsored and technical variant has one named owner; all existing
rows are backfilled or explicitly adjudicated; shadows have zero unexplained
drift; every read and write path uses the new owner; old resolvers/fields/
guards/duplicate-row creation paths are retired; exact releases and adopters
are externally evidenced; and required CI is green.
