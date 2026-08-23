# dotmac-subscriptions — compatibility and public API

The supported Python surface is exactly the curated
`dotmac_subscriptions.__all__`. Underscore-prefixed names and names not exported
there are private. `models`, `service`, and migration internals may be imported
by the package itself but are not separate compatibility surfaces.

The package version in `pyproject.toml`, `dotmac_subscriptions.__version__`, and
the manifest version move together. The two output contracts have independent
wire versions carried both in their type name and `contract_version` field:

- `RatedObligationOutputV1` is the subscriptions-to-assembly pre-tax rating
  fact. Adding an optional field is additive; removing, renaming, changing the
  meaning of a field, or changing its identity/fingerprint rules requires a new
  contract type.
- `CommercialEntitlementProjectionV1` is money-free commercial intent. It is
  never a grant or a product-state command and follows the same versioning
  rule.

`BillingCadence`, `Interval`, `ExactAmount`, command/result dataclasses, domain
errors, link helpers, publisher protocols/fakes, `DurableTimerPort`, and the
query/service names in `__all__` are supported for the current pre-release
series. Callers supply an explicit `TenantScope` or `PlatformScope`, exact
decimals, currency, timezone, cadence, provenance, and product link; no product
or deployment default is part of the contract.

`list_effective_offers` returns `OfferCatalogPage`, `OfferCatalogItem` and typed
`OfferCatalogPrice` rows. The read is half-open at `effective_at`, chooses one
latest effective published version per stable published offer, sorts by
normalized name/code/id, and caps each page at 100 rows. Search treats `%` and
`_` as literal input through the kernel query helper. Exact prices are owner
facts, never localized display strings; product adapters remain responsible for
facets, eligibility, availability and actions.

Persistence compatibility is owned by the `subscriptions` Alembic lineage.
Assemblies select tenant, platform, or both declared planes and bind the
manifest prerequisites. They may not create a parallel migration or repoint the
`mod_subscriptions` schema. Billing, collections, durable timers, orders, and
product assemblies remain peer packages and are connected only by assembly
adapters over the published contracts.

The package is still pre-release. An adopter must pin an exact released
version, run its selected lineage, pass the package contract suite, and retain
an assembly mapping test. A breaking change requires a new output-contract
version and an explicit migration/cutover path; publishing another local owner
beside an existing product implementation is never a compatibility strategy.
