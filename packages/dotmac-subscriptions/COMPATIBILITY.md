# dotmac-subscriptions — compatibility and public API

The supported Python surface is exactly the curated
`dotmac_subscriptions.__all__`. Underscore-prefixed names and names not exported
there are private. `models`, `service`, and migration internals may be imported
by the package itself but are not separate compatibility surfaces.

The package version in `pyproject.toml`, `dotmac_subscriptions.__version__`, and
the manifest version move together. The three output contracts have independent
wire versions carried both in their type name and `contract_version` field:

- `RatedObligationOutputV1` is the subscriptions-to-assembly pre-tax rating
  fact. Adding an optional field is additive; removing, renaming, changing the
  meaning of a field, or changing its identity/fingerprint rules requires a new
  contract type.
- `CommercialEntitlementProjectionV1` is money-free commercial intent. It is
  never a grant or a product-state command and follows the same versioning
  rule.
- `NonCashGrantOutputV1` (added in `0.1.0a3`) is exact foregone-revenue
  evidence for one approved service period: the arrangement, contract line,
  occurrence, half-open period, treatment, declared reason, the strictly
  positive contracted amount, the approved ceiling and the foregone amount. It
  is never customer money and never an entitlement, anchor, invoice
  suppression or accounting posting — an adopter maps it to those decisions in
  its own owners. Same versioning rule.

`BillingCadence`, `Interval`, `ExactAmount`, command/result dataclasses, domain
errors, link helpers, publisher protocols/fakes, `DurableTimerPort`, and the
query/service names in `__all__` are supported for the current pre-release
series.

`BillingTreatmentReasonDeclaration` and
`SubscriptionVocabularyRegistry.from_manifests(..., reason_declarations=...)`
are the supported way to widen the non-standard treatment reason vocabulary.
The seven codes in `PORTED_BILLING_TREATMENT_REASONS` are owned by this module
and are never removed; a product declaring an additional code owns it, and one
code never has two owners. The database column is a plain string on purpose, so
widening the vocabulary is not a schema change (ADR-0008).

`SubscriptionVocabularyRegistry` gained a THIRD field,
`billing_treatment_reasons`, in `0.1.0a3`. It is **defaulted, not required**:
`0.1.0a2` published the dataclass with two fields, so
`SubscriptionVocabularyRegistry(charge_models, obligation_sources)` — positional
or keyword — is a released construction and keeps working unchanged on upgrade.
A registry built that way declares no treatment reasons, so
`require_billing_treatment_reason` refuses every code, which is the correct
answer for a caller that never opted into treatments; `from_manifests` is the
supported way to obtain the seven ported reasons. Adding a REQUIRED constructor
argument to an already-released dataclass is a breaking change and is not done
inside a pre-release series.

Callers supply an explicit `TenantScope` or `PlatformScope`, exact
decimals, currency, timezone, cadence, provenance, and product link; no product
or deployment default is part of the contract.

The runtime floor is `dotmac-kernel>=0.1.0a94`. Although `a89` allocated the
subscriptions lineage, `a94` is the first kernel contract on which product
manifests can declare the open `charge_models` and `obligation_sources`
vocabularies consumed by `SubscriptionVocabularyRegistry`. An older floor could
import much of the package but could not configure its guarded write paths
through the supported manifest surface.

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
