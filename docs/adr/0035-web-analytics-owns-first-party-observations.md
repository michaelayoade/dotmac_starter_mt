# ADR-0035: Web analytics owns first-party observations, not attribution

- **Status:** Accepted
- **Date:** 2026-08-18
- **Owners:** `dotmac-web-analytics` maintainers and each adopting product
- **Source evidence:** `docs/inventories/web-analytics-sources.md`

## Context

Dotmac's public websites need first-party measurement without turning a browser
event into customer identity, sales attribution or a second connector control
plane. The fleet audit found provider aggregates, CRM identity-bearing chat
tracking, campaign pixels and generic dashboards, but no qualifying
first-party analytics owner. The initial implementation is consequently
greenfield-after-inventory under ADR-0006's product-first rule.

The dangerous alternatives are already present in the evidence: copying GA4's
daily aggregates would call provider output first-party evidence; copying the
CRM widget would persist fingerprints, raw IP and full URLs; letting each
website invent event names and dictionaries would make neither privacy nor
reporting reproducible; and attaching revenue/customer state would create a
second sales/billing decision path.

## Decision

`dotmac-web-analytics` is an optional, tenant-plane-only module. It owns one
versioned lifecycle from accepted collection command through immutable
observation, classification evidence, deterministic projection, explicit
expiry/deletion and drift repair.

It is reusable code installed independently by each application. It is not a
fleet analytics database. Each assembly owns its runtime, sessions,
authorization, property configuration and rows. Applications synchronize
typed data through APIs/webhooks and the Integrator; none reads another
application's tables.

### 1. Ownership

The module owns:

- local analytics property and stream identity;
- the versioned first-party collection protocol;
- accepted page-view and product-declared event observations;
- property-scoped, rotatable pseudonymous visitor evidence;
- versioned deterministic sessionisation and effective visitor/session views;
- append-only bot/filter classification evidence;
- canonical path, sanitised referrer and allowlisted campaign-marker evidence;
- rebuildable route/source/device/event aggregates and observational funnels;
- raw-observation expiry, privacy deletion, full projection rebuild and drift
  repair;
- event-identity deduplication, changed-content conflict evidence and transport
  provenance.

It does not own websites/content (`dotmac-sites`), forms/submissions
(`dotmac-forms`), campaigns (`dotmac-campaigns`), external provider metrics
(`dotmac-media-observations`), customer identity, leads, orders, invoices,
revenue, official acquisition attribution, consent policy, provider credentials
or connector transport. It supplies no dashboard framework.

### 2. One contract, no adopter branches

Local and remote websites submit the same versioned collection command. A local
collector and an Integrator-facing collector are thin adapters around one
service. Both must perform origin verification and rate limiting before the
service is callable; the remote route is not a privileged bypass. Integrator
records delivery/replay evidence and transports the command, while this module
alone validates its analytics meaning and content fingerprint.

No website, hostname, route, property key, origin, event code, consent mode,
classifier choice or retention period is hardcoded in the package. Those are
typed assembly inputs. Provider identity is provenance data, never an `if
provider == ...` behaviour branch. A browser SDK, if built later, is a separate
versioned protocol client and contains no decision engine.

### 3. Typed event declaration registry

Products declare event vocabulary members as immutable `EventDeclaration`
objects in an `EventDeclarationRegistry`. A declaration contains a stable code,
schema version and bounded typed attribute specifications. Recording requires a
registry lookup; an unknown event code, unknown attribute, wrong scalar type,
oversized value or excess attribute count is refused before persistence.

The wire command carries a code only as a reference to that installed
declaration. It never accepts an unrestricted event-name string or metadata
dictionary as a contract. Page views use the same validation path with a
module-owned core declaration. Product declarations do not become module
enums: the open membership belongs to adopters, while the registry is the
single authority for validity.

### 4. Privacy by construction

The collection command has no fields for name, email, phone, subscriber,
customer, lead, form value, request body, raw IP or raw user agent. Attribute
names reserve common identity and secret vocabulary and declarations cannot
make those names legal.

URLs are parsed before persistence. The module stores a canonical origin/path
and only explicitly allowed acquisition keys after per-key validation. It never
stores the original query string or fragment. Referrers receive the same
canonicalisation; user info is refused. Values that resemble email addresses,
credentials or bearer/API tokens are rejected even when an adopter attempts to
place them in an otherwise allowed string attribute.

Visitor input is opaque and short-lived. A keyed pseudonymizer supplied by the
assembly derives a digest whose domain contains tenant, property and key
version. Only that digest and key version persist. The same browser token sent
to two properties therefore produces unrelated visitor evidence. Device
evidence is a coarse declared class; the module neither accepts nor assembles a
fingerprint from browser characteristics.

Every command records the effective consent/privacy-policy version, consent
state, adopter decision, Global Privacy Control and Do Not Track inputs.
Consent is evaluated at submission time: a page-load decision is evidence, not
permission that survives a later change. The product/kernel consent facility
decides whether collection is lawful; the analytics adapter records the
decision and rejects a command that is not eligible.

### 5. Immutable observation and replay identity

An event has distinct timezone-aware occurrence and receipt times. Its external
identity is unique within `(tenant, property, stream)`. The service computes a
canonical content fingerprint over protocol version and the complete accepted
command after privacy normalisation.

- Same identity and fingerprint: idempotent replay, returning the original
  observation.
- Same identity and different fingerprint: conflict, with append-only conflict
  evidence; the original observation is not changed.
- A bounded batch uses one savepoint per event. Valid members succeed and
  invalid/conflicting members receive ordered typed results; an infrastructure
  failure aborts the caller-owned transaction. There is no service commit or
  rollback.

Online database roles receive `SELECT` and `INSERT`, never `UPDATE` or `DELETE`,
on raw observations and append-only evidence. Database triggers reject mutation
even if a grant later drifts. Offline `app_admin` is the explicit retention and
privacy authority and performs deletion through the same caller-owned
transaction discipline.

### 6. Classification is new evidence

Bot/filter classification is an append-only record naming classifier code,
version, reasons and the analytical-inclusion decision. Reclassification adds
new evidence; it never rewrites an event. The effective classification is
selected deterministically by classifier version and evidence identity, and a
projection rebuild uses that same rule.

External provider aggregates never enter this ledger as events. Every remote
first-party event records transport kind, source system, source reference and
delivery identity so a repair can distinguish origin from transport replay.

### 7. Sessionisation and projections are reproducible

Sessionisation rules are immutable, versioned declarations. The engine orders
eligible observations by `(occurred_at, received_at, observation_id)` and uses
the declared inactivity boundary. It does not use database arrival order or a
mutable counter. Late and out-of-order events therefore yield the same sessions
after a rebuild as if they had arrived in order. Calendar bucketing receives an
explicit IANA timezone; no process-local timezone is consulted.

Visitor/session views, route/source/device/event aggregates and funnel results
are projections only. Raw observations carry no mutable aggregate counters.
Funnel definitions use declared event codes, an explicit ordering/window and a
version; evaluation is deterministic over the same accepted event order.

Each rebuild writes a versioned projection generation. Readers see one complete
generation, never a partially replaced set. Drift detection computes canonical
digests/counts from retained authoritative observations and compares them with
the active generation. Repair builds a fresh generation, verifies it, then
switches the active generation in the caller's transaction.

### 8. Retention and privacy deletion

Every raw observation has `expires_at`. The package has no default retention
period: each adopter must install an explicit policy before collection can
start. Retention deletes only expired observations. Privacy deletion accepts a
property-scoped visitor digest or a specifically authorised event set; it never
requires or stores customer identity.

Deletion and projection replacement are one logical operation: delete the
authoritative observations, build projections from the retained set, verify,
and activate the new generation in the same transaction. A deletion that
leaves an active aggregate counting removed observations is refused/rolled
back. Deduplication tombstones retain only identity and fingerprint for the
separately declared replay-evidence period, preventing an expired event from
being silently reinserted; they contain no URL, attributes or visitor digest.

### 9. Attribution remains observational

The module may report that a property-scoped anonymous visitor arrived with
campaign marker X, viewed route Y and later emitted a declared
`form_completed` event. It may not say that X acquired customer Z or generated
an order/revenue amount. Forms owns the submission, Sales owns the Lead, Orders
owns the commitment and Billing owns revenue. Client-reported monetary values
are rejected from the core declaration vocabulary and can never be presented as
official revenue.

## Consequences

- The first package release starts `audit-complete`; Backoffice adoption moves
  it to `adopted`, and independent Sub adoption to `reuse-proven`.
- The first implementation is tenant-only. A platform plane needs a real named
  adopter and an ADR amendment; a nullable/fake tenant is forbidden.
- Mkt's GA4 daily aggregates stay external/provider observations. They may
  inform migration comparison but do not seed the event ledger.
- CRM's fingerprint/IP/full-URL tracker is retirement debt. Shadow comparison
  must prove the new property/event counts and operational delivery before its
  overlapping writer is removed; CRM identity/chat behavior itself remains
  CRM-owned.
- Property configuration is an adopter concern and can name any real website.
  Adding a site does not change this package.

## Alternatives rejected

**Port Mkt's GA4 adapter.** It starts from provider-created aggregates, holds
provider credentials and cannot rebuild sessions or privacy deletion from local
observations. It belongs on the external observation side of the boundary.

**Port CRM's chat visitor model.** It intentionally joins browsing to identity
and stores browser fingerprint, raw IP, full URLs and free metadata. That is the
privacy failure this module is designed to prevent.

**Let each adopter accept arbitrary event JSON.** It removes the only authority
that can reject PII, schema drift and incompatible funnels. A dictionary is a
wire container, not a declaration contract.

**Hardcode the first website.** Backoffice and Sub are independent adopters and
the protocol must also serve local and remote websites. A hostname branch would
make the first deployment the permanent product model.

**Update aggregates during deletion.** Incremental decrements cannot reliably
repair late-event session changes, reclassification or funnel membership. A
full retained-observation rebuild is the canonical repair.
