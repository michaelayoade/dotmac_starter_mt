# ADR-0024: Applications compose by synchronizing data

**Status:** Accepted
**Date:** 2026-08-13
**Decision owner:** Michael
**Scope:** FLEET-WIDE. Applies to every Dotmac application and installable
module.
**Relates to:** ADR-0006 (independently released modules), ADR-0008
(declaration registries), ADR-0010 (thin adapters), ADR-0014 (idempotency),
ADR-0021 (independent application planes), ADR-0023 (dual-plane module
persistence)

## Context

Dotmac applications need to exchange customer, subscriber, ticket, work-order,
commercial and operational facts. That does not make them one application and
does not transfer ownership of those facts. A direct database read, a shared
model import, or an importer that assigns another application's status makes
the receiver depend on the sender's deployment and creates a second decision
path.

Installable modules have a different composition boundary. A product installs
a pinned module into its own assembly and database. The module may therefore
own tables in that installation, but it must remain independently releasable:
it cannot import the product or a sibling module, and two applications never
share one installation's module rows.

The failure mode is the same in both cases: a convenient integration silently
becomes authority. The result cannot be repaired reliably because nobody can
say which writer wins.

## Decision

### 1. Every application is an independent authority boundary

Each application owns its repository, runtime, database, migrations, sessions,
authorization and domain decisions. Cross-application integration uses
versioned APIs and webhooks to synchronize data. It never uses:

- another application's database connection, schema, table, ORM model or
  filesystem;
- a foreign key into another application's database;
- a shared session, cookie or request guard; or
- an imported mirror as the only copy of an authoritative fact.

An external identifier is provenance, not authority and not a substitute for a
local key.

### 2. Every module is independently releasable and locally installed

An installable module owns one package, manifest, namespace and migration
lineage. It may depend only on its declared base surfaces, such as the kernel or
published UI package. It never imports the consuming assembly or another
business module.

The consuming assembly is the composition root. It pins the module, composes
its lineage, supplies product declarations, and owns any relation between the
module and product data. A second application installs its own copy and owns
its own rows. Sharing module code does not mean sharing module persistence.

When two modules need coordination, the assembly connects their published
contracts or records an event/command. Neither module reaches into the other's
implementation.

### 3. Synchronization carries observations; the local owner makes decisions

An Integrator connector authenticates provider ingress, deduplicates it, and
maps the provider-specific payload into a versioned, provider-neutral
capability message. The receiving product authenticates the Integrator,
validates that typed message and records the local observation. The observation
records at least the source declaration, external identity, observed version or
fingerprint, and observation time.

A resolver or reconciler then does one of two explicit things:

1. updates a rebuildable local projection of a remotely owned resource; or
2. submits a command to the local owning service, which applies its own policy
   and lifecycle guards.

The importer does not assign an authoritative status, permission, entitlement
or lifecycle field directly. An illegal or conflicting remote transition is a
visible reconciliation result, not a silent overwrite.

If an observed remote request creates work for the receiver, the receiver may
create a new locally owned record with a provenance link. From that point its
lifecycle is local; synchronizing the two records does not make either
application a writer of the other's state.

Outbound delivery occurs after the local transaction through an outbox or an
equivalent durable delivery seam. A local decision transaction never waits on
another application.

### 4. Shared behavior contains no product or provider switch

Shared module and kernel execution paths do not branch on product names,
provider names, plan names, deployment modes, nullable-scope shortcuts, or
boolean switches such as `is_sub`, `is_erp` or `platform=`. Architecture and
adoption evidence may of course name the real products it proves.

Products declare only provider-neutral domain ports and accepted capability
contract versions. Provider identities, capabilities, endpoints, secret
references and wire mappings are connector-plugin declarations inside the
Integrator. Adding a provider installs a plugin and binding; it does not edit a
product, module, kernel or Integrator-core conditional tree. Secret values are
materialized only through the Integrator's approved secret seam.

Configuration does not choose business authority. The owning system is named
in an accepted contract or ADR; configuration only selects how an authorized
adapter reaches it.

### 5. Ticket ownership follows the local workflow, not the word “ticket”

`dotmac-ticketing` is installed independently in each adopter. The shared
package owns the product-neutral lifecycle mechanism; it does not create a
fleet-wide ticket database.

- Sub owns operational customer, subscriber and service tickets.
- ERP may own internal back-office, project and employee-support tickets.
- The vendor control plane owns vendor-support tickets about accounts,
  deployments and licences.
- A ticket owned by another application does not become a ticket row in the
  receiver merely for display or foreign-key compatibility. If it creates
  legitimate local work, the receiver creates a separately owned ticket with
  an opaque Integrator correlation reference. Otherwise its receipt and
  transport evidence remain in the Integrator.

Any transfer of one of those ownership boundaries needs an accepted ADR with a
shadow, cutover, repair and legacy-writer retirement plan.

### 6. The Integrator is the sole external connector control plane

**Decision amendment — 2026-08-13.** Every Dotmac product retires direct
provider clients, provider credentials, provider webhook verification,
connector scheduling, checkpoints and delivery retries from its application
runtime. Those responsibilities belong to the independently deployed Dotmac
Integrator. Product applications expose versioned, capability-specific domain
ports and receive typed, authenticated observations or commands from that
runtime; they do not know which provider implementation produced them.

"Independently deployed" is a runtime boundary, not a code-location exception
to Starter's layering. The Integrator has two first-party artifacts:

- `dotmac-integration` is the independently versioned, stateful Starter module.
  It owns the reusable registry, connector definitions and installations,
  immutable configuration revisions, capability bindings, secret references,
  ingress receipts, outbound delivery, retries, checkpoints, run/record
  outcomes, repair evidence, and its own `mod_*` schema and migration lineage.
- `dotmac_integrator` is a thin assembly repository and deployment. It pins
  `dotmac-kernel`, the exact `dotmac-integration` release and connector plugin
  distributions, composes them, and supplies deployment configuration. It does
  not implement a second registry, retry engine or persistence model.

Products do not each compose `dotmac-integration`; doing so would duplicate
credential ownership, provider-account rate limiting, backoff and idempotency.
The thin Integrator assembly is the module's authoritative runtime and products
reach it over their provider-neutral ports. Neither artifact owns product
business state, imports a product ORM model or writes a product database. A
product domain owner remains the only writer of local decisions.

`dotmac-integration-client` is reusable HTTP transport policy—retry,
idempotency, request correlation and circuit breaking. It is not the control
plane, connector registry, configuration store or a second domain owner. The
production-used integration platform in `dotmac_sub` is the mandatory
product-first source for the `dotmac-integration` module extraction; provider
branches are removed from the source products as each capability cuts over.

A receiving product stores a remote projection only when a named local reader
needs remote state and a named reconciler can rebuild it. Foreign-key
convenience, dropdown display and migration inertia are not sufficient. When
only correlation is needed, the product stores an opaque Integrator reference
on its own record and the external payload/timeline remains outside its
operational domain schema.

### 7. External systems integrate through a reusable connector-plugin SPI

The `dotmac-integration` module supplies a single versioned SPI and generic
package-metadata discovery; the `dotmac_integrator` runtime loads independently
released connector distributions through it. The module core and thin assembly
contain no fixed provider enum, import list or `if provider == ...` branch. A
connector distribution registers itself through package metadata and publishes
a manifest containing:

- a stable connector code and SPI version range;
- the capability contract versions it implements, such as
  `ticket.observation.v1` or `inventory.availability.v1`;
- a JSON-schema configuration contract containing secret REFERENCES, never
  secret values;
- its supported ingress, polling and delivery modes; and
- factory entry points for only those declared capability handlers.

The `dotmac-integration` module owns the generic plugin registry,
installation/configuration revisions, capability bindings, secret
materialization boundary, inbox/outbox, idempotency, retries, checkpoints,
health, audit evidence and repair commands.
Each `(installation, capability)` has exactly one active connector binding;
duplicate ownership or an incompatible SPI/capability version refuses startup
or activation. Connector plugins translate provider wire formats and perform
provider I/O. They do not import product code, open product databases, make
business decisions, persist a second delivery ledger, or implement their own
retry/checkpoint engine.

Products publish provider-neutral capability ports. Adding or replacing an
external system therefore installs/configures a connector plugin and binds its
capabilities; it does not release ERP, Sub, the kernel, or a business module.
Connector plugins may be deployed in isolated workers, but their registration,
configuration and delivery evidence remain in the one Integrator module
installation composed by `dotmac_integrator`. `dotmac-integration-client` may
implement reusable HTTP transport inside that SPI; it does not become the
plugin registry or orchestration owner.

This decision concerns external **application/data integration**. A typed
resource driver used locally by one owning application—such as an object-store
`StorageProvider` beneath `dotmac-files`—is not thereby an Integrator connector.
It remains behind its owning module's published seam, carries no product-domain
payload or cross-application authority, and is still forbidden from appearing
as a hardcoded conditional in shared execution paths.

## Enforcement and evidence

- Import-linter contracts `Modules must not import the assembly` and `Modules
  are independent of each other` enforce the package direction.
- `ModuleManifest` plus the composed migration gate enforce one namespace and
  lineage per installed stateful module.
- The fleet-decomposition destination guard requires `integration-external` to
  resolve to a Starter module. A deployment or assembly name cannot satisfy
  that code-location invariant.
- ADR-0010's adapter checks keep API and webhook entry points out of the
  decision layer.
- ADR-0014's idempotency ledger and the product-owned inbox/outbox are the
  synchronization primitives; they do not become domain owners.
- Each adopter must add contract tests proving a remote payload cannot bypass
  the local owning service, plus drift detection and idempotent repair for every
  projection.
- An existing direct cross-application writer is retired behind an ADR-0018
  two-directional ratchet with a sensitivity proof.
- Product architecture tests ratchet direct provider clients, provider-named
  routes/tasks/configuration and provider credentials to zero as Integrator
  capabilities adopt them.
- Integrator contract tests discover a temporary connector distribution from
  package metadata, reject undeclared/duplicate capabilities and incompatible
  SPI versions, and prove the core contains no provider catalogue. Every
  connector has replay, retry/idempotency, secret-redaction and sensitivity
  canaries against the shared SPI.

## Consequences

- Applications remain deployable, upgradeable and recoverable without another
  application's database being available.
- Shared modules stay product-neutral and independently publishable.
- The same real-world request may have a remote authoritative record and a
  local work record. Their provenance is explicit; their lifecycles are not
  conflated.
- Integration work includes inbox/outbox, idempotency, drift detection and
  repair. A synchronous ORM shortcut is no longer an admissible substitute.
- ERP's ERPNext/CRM-owned ticket rows are archived and retired from its
  operational schema after required local correlations move to opaque
  Integrator references. The remaining ERP-local rows may cut over to
  `dotmac-ticketing` after the E8 tenancy/composed-lineage gate.

## Alternatives rejected

**One shared database for all applications.** This collapses deployment,
authorization and failure boundaries and lets one application bypass another's
service owner.

**A shared module table used by several applications.** A module is reusable
code installed into an application, not a cross-application datastore.

**Provider branches in the shared module.** They turn the shared owner into a
hardcoded integration catalogue and require a module release whenever a product
adds an application.

**Provider branches in the Integrator module or assembly.** Moving the same
conditional tree to another repository is relocation, not reuse.
Package-metadata discovery plus capability binding keeps both artifacts open
without hardcoding installed providers.

**Integrator engine code owned by the thin assembly repository.** Independent
deployment does not create a fourth reusable-code destination. It would bypass
Starter's module extraction, release, lineage and conformance machinery and
repeat the implementation/deployment conflation already solved by the vendor
control plane. The assembly pins and runs the engine; the Starter module owns
it.

**Last-write-wins status synchronization.** It hides ownership conflicts and
makes ordering or retry determine business state.

## Decision amendment — 2026-08-18 (capability cutover versus plane retirement)

**Capability cutover is not control-plane retirement.** A provider callback or
delivery binding moves independently. Its old receiver, provider-only secret,
retry/checkpoint path and ratchet entry retire after that binding's mirror and
rollback gates. The shared product integration registry, tables, scheduler and
other bindings do not move merely because one capability did.

Before any product-local integration control plane is removed, a
production-derived inventory classifies every live capability as `migrate`,
`retire`, or `retain-temporarily`. A continuing capability has a complete packet:
connector distribution, typed product port, product descriptor, named
reconciler, secret mapping, mirror evidence, rollback plan and local retirement
gate. A retiring capability proves zero traffic and its deletion gate. Temporary
retention names an owner and an explicit exit gate; it is a blocker, never a
fourth terminal disposition.

Teams may build independent packets in parallel. Capabilities **cut over
sequentially**, one binding and rollback boundary at a time. Every staging and
production change carries survivor canaries proving untouched bindings,
configuration digests, financial state, workers and provider paths remain
unchanged. Payment and billing capabilities require their own financial gates
and cannot be absorbed into a messaging or platform tidy-up.

The product-local shared plane reaches **fleet-zero** only when every observed
live capability is migrated or retired, old-path traffic is zero, rollback
windows are closed and the external-connector ratchet has been lowered with a
sensitivity proof. Only then may its shared tables and generic runtime be
removed. A missing or partial production inventory is `unmeasured`, never zero.

Production evidence is complete only when it is bound to the exact deployed
application revision and accounts for the whole committed source surface. A
source-mapped application records one observation for every mapped surface; a
capability-catalogued application records every declared capability, including
explicit zero/absent rows. The product capture must also prove its database
transaction was read-only; a report that merely intends not to write is not
authoritative evidence. An omitted row never means absent. Staging evidence is
selected by the target cohort's owning application, never by file order or by
whichever staging snapshot happens to be listed first.
