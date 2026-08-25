# ADR-0033: Media observations own provider reports, not attribution

**Status:** Accepted
**Date:** 2026-08-18
**Decision owner:** Michael
**Source dossier:**
[`media-observations-sources.md`](../inventories/media-observations-sources.md)

## Context

Dotmac products need one reusable account of what external advertising and
social-media systems said about their entity hierarchy, configuration state and
aggregate performance. The current qualifying implementation is in
`dotmac_mkt`: it maps several provider payload shapes into a common campaign /
group / advertisement hierarchy and daily metrics. It is tested, but it mutates
the latest row in place, fixes providers and metrics in enums, stores counts as
decimal values, and mixes provider transport with the media model.

CRM and Sub prove the most important negative boundary. CRM currently uses
campaign replies and external advertising fields to write Lead source and ROI
projections. Sub now owns immutable Lead-origin evidence and the sales-to-service
lifecycle. Neither concern can move into a media observation collector without
creating a second acquisition authority.

Integrator already owns connector installation, credentials, wire mapping,
verification, polling, retries, checkpoints and raw transport evidence. A media
module importing a provider SDK or the Integration module would duplicate that
control plane and make the package provider-specific.

Michael directed the module to be built, validated and released product-first,
then on 2026-08-18 explicitly paused product adoption. This decision records
both instructions. The package and namespace may be built and validated under
that owner direction, but the repository's closed release policy requires a
module's publication entry to land with first-adopter proof. The specified
execution order likewise puts release after Backoffice shadow/cutover. The
pause therefore leaves the validated package intentionally unpublished rather
than silently reordering the programme.

## Decision

### 1. One tenant-plane fact owner

`dotmac-media-observations` owns provider-neutral, immutable observations of:

- external media entity state and provider-declared parent relationships;
- versioned node and metric declarations;
- aggregate metric values over explicit half-open time windows;
- provider-reported spend, impressions, reach, clicks, engagements and
  conversion claims;
- source time, receipt time, normalization version, opaque installation
  reference and opaque transport-receipt reference;
- replay, conflict and restatement evidence; and
- rebuildable effective entity, hierarchy and metric projections plus drift and
  reconciliation evidence.

V1 is tenant-only. Every table carries `tenant_id NOT NULL`, tenant-composite
identity, and forced RLS. The module declares no platform plane because the
inventory found no real platform-plane media observer.

### 2. Facts are append-only; projections are disposable

Declarations, observation envelopes, receipt links, entity facts, hierarchy
facts, metric periods, metric facts and reconciliation evidence are append-only.
Online roles receive no update/delete privilege and a database trigger refuses
those operations for every role, including the migration owner.

Effective entity, hierarchy and metric rows are projections. They may be
replaced and must be exactly rebuildable from immutable facts. Source time,
restatement depth and stable source identity provide deterministic ordering;
receipt arrival order never decides current state.

### 3. Domain identity excludes transport identity

An observation identity is `(tenant, installation reference, source system,
source observation id)` across every observation kind. A connector namespaces
an upstream identifier before handing it over when the upstream source does not
provide a globally unique event identity. The content fingerprint covers the
kind, complete normalized fact and source time. A transport receipt reference is
provenance attached to the fact; it is not part of domain identity or content
fingerprint.

The same identity and fingerprint is an idempotent replay, including through a
second receipt. The same identity with different content is a conflict. A
provider correction uses a new observation identity linked to the fact it
restates. It never updates the original row.

### 4. Provider and metric vocabularies remain open

The package contains no provider enum, SDK import, endpoint, credential, webhook
logic or provider conditional. Node and metric codes are versioned data
declarations. Fixed enums describe only provider-neutral semantic traits such as
value type, observation kind and lifecycle disposition.

Every metric definition states an exact value type, unit and semantic trait.
Counts accept integers only. Money records exact decimal amount, ISO currency,
minor-unit scale and the exactly equivalent integer minor units. Durations are
integer quantities with a declared unit. Ratios use decimal values and retain
whether they were provider-reported or derived by a reporting projection.

Metric periods use `[start, end)` semantics, require aware instants and refuse
partial overlap for the same entity and metric declaration. A restatement reuses
the exact period instead of opening a competing interval.

### 5. Missing and destructive provider state stays observable

A missing parent is an orphan drift finding, never an implicit root. A cycle is
preserved as reported and marked invalid in the effective hierarchy. Provider
archive or deletion is an entity observation; it never deletes local history.
Out-of-order facts remain in history and produce the same projection regardless
of delivery order.

V1 accepts aggregate facts only. Per-person profiles, imported audiences, raw
provider payloads and fields that imply person/contact identity are refused.

### 6. Attribution stays outside

The module may say:

> A provider reported three conversions and NGN 50,000 conversion value for an
> external campaign in a stated period.

It may not say that three Dotmac customers were acquired, that the campaign
caused authoritative revenue, or that a Lead belongs to it. Conversion metrics
carry the explicit `provider_reported` claim label. The package has no Lead,
Party, customer, Quote, Order, billing or official-revenue foreign key or
writer.

Official attribution requires a separately owned resolver over media facts,
first-party web analytics, submissions, Sub's immutable Lead origin, accepted
Orders and billing evidence. This ADR neither names nor implements that owner.

### 7. Integrator owns all provider I/O

A connector plugin translates wire data into the package's normalized typed
commands. Integrator owns credentials, verification, endpoints, raw request
bytes, polling, retries, checkpoints and transport receipt evidence. The media
module stores only opaque installation and receipt references and never
dereferences them.

The package publishes a provider-free normalized-observation conformance kit.
It does not import `dotmac-integration`; an authorized connector separately
conforms to Integration SPI 1.3 and this domain contract. No real media connector
is certified until Michael names the plugin and its exact release.

### 8. Adoption is paused, not implied

The first candidate adopter remains Backoffice and Sub remains the later
independent adopter. Neither is a contract consumer today.

Until Michael resumes adoption, this work must not:

- modify Backoffice, Sub, Mkt, Integrator or a connector repository;
- compose the module into an application;
- shadow or cut over production observations;
- retire a `dotmac_mkt` writer; or
- count either product as a contract consumer.

The dossier stays `audit-complete` with an empty `contract_consumers` list and
the package stays outside the release allowlist until a product is authorized
to perform the first-adopter proof. Publication itself does not transfer domain
authority, but it follows that proof under this repository's release policy.

## Consequences

- Mkt supplies behavior and parity evidence, but none of its provider enums,
  mutable upsert model, silent-zero coercion, provider tasks or destructive
  remote-post reconciliation ports.
- CRM supplies acquisition-reporting requirements and negative evidence; its
  Lead/ROI writers are explicitly excluded.
- Sub remains authoritative for immutable Lead origin and the downstream
  customer lifecycle.
- Backoffice is only a candidate while adoption is paused. Its repository has
  no remote or deployment today, so it could not supply release or production
  adoption evidence even if composition were authorized.
- Future publication requires the exact kernel allocation release, Observer
  unit, architecture, PostgreSQL, concurrency, clean-wheel and sensitivity
  gates plus the authorized first-adopter proof.
- Hardened candidate commit `b30fc32a56bbd0b90fa834b9290c13ba113f03f0`
  passed the exact Observer static, unit/architecture, PostgreSQL, clean-wheel
  and pinned Governance gates, plus all 15 dispatched jobs in CI run
  `32230562002`, including unit coverage, PostgreSQL teardown, Python 3.11/3.12
  floors, consumer boot and Docker smoke. This is candidate validation, not the
  missing first-adopter proof; a later release commit must still pass the
  hosted PR-only Engineering Standards gate and protected release workflow.
- Completion of the larger programme remains open while the adoption pause is
  in force.
