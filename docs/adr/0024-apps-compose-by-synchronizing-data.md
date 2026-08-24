# ADR-0024: Applications compose by synchronizing data

**Status:** Accepted. Amended 2026-08-13 (§ 6, the Integrator is the sole
external connector control plane), 2026-08-19 (Context, caller-owned runtime)
and 2026-08-24 (§§ 8–9 below, outbound provider neutrality and connector
completeness). Every amendment is a dated addition; no earlier text is
rewritten.
**Date:** 2026-08-13
**Decision owner:** Michael
**Scope:** FLEET-WIDE. Applies to every Dotmac application and installable
module.
**Relates to:** ADR-0006 (independently released modules), ADR-0008
(declaration registries), ADR-0010 (thin adapters), ADR-0014 (idempotency),
ADR-0017 (adoption is the scarce resource), ADR-0021 (independent application
planes), ADR-0023 (dual-plane module persistence), ADR-0061 (a payout is ERP's
decision and its provider is a binding), ADR-0062 (modules own metric
definitions; assemblies own exporters)

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

**Decision amendment — 2026-08-19 (caller-owned runtime).** A reusable kernel
service that accepts an application's SQLAlchemy `Session` must use only that
session and must not import an eager kernel engine/session owner as a side
effect of an operation. Otherwise an independently assembled adopter gets two
database runtimes even though the service's typed contract appears
caller-owned. Shared transaction mechanics may be private, import-safe helpers
that operate on the supplied session, but they construct no session, own no
boundary, and never commit or roll back the outer transaction. The canonical
public transaction authority remains `dotmac_kernel.db`; moving its pure
SAVEPOINT implementation behind such a helper is not a second authority.

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

## Decision amendment — 2026-08-24 (outbound: what a provider branch actually is, and what a complete connector ecosystem means)

The nine outbound commits (kernel machine credentials through the Flutterwave
v4 and Remita command legs) turned §§ 4, 6 and 7 from a receiving-side rule
into a two-way one: products now *send* through the Integrator, not only
receive from it. Two things that were adequate as principles stopped being
adequate as review instructions. This amendment states both concretely. It adds
nothing to §§ 1–7 and contradicts nothing in them.

Two rulings needed their own records rather than a paragraph here:
[ADR-0061](0061-a-payout-is-erps-decision-and-its-provider-is-a-binding.md)
(who decides a payout, and that `payments.payout.v1` is one contract with
interchangeable bindings behind it) and
[ADR-0062](0062-modules-own-metric-definitions-assemblies-own-exporters.md)
(a module declares metric names; the deployed assembly exports them). Both
extend this decision; neither replaces any part of it.

### 8. A capability contract is one contract, and a provider branch is one of six concrete things

**8.1 One capability, one contract, one payload.** A capability id such as
`payments.payout.v1` or `messaging.send.v1` names a business act, not a
provider's endpoint. There is exactly one of each in the fleet. Forbidden: a
provider-named id (`payments.payout.paystack.v1`); a provider-shaped sibling id
meaning the same act in another provider's vocabulary
(`payments.transfer.v1`); and a per-connector command payload dialect behind a
shared id — which is the same violation with the branch pushed into whichever
product has to build the payload. A `vN` bump records a change in what the
product means, never a change in what a provider exposes.

**8.2 What "products contain no provider branch, credential or client" forbids,
concretely.** § 4 said this as a principle and a reviewer still had to
interpret it. In a product repository — ERP, Sub, Academy, the vendor control
plane — each of the following is a defect, by name:

| Forbidden in a product | Lives instead in |
|---|---|
| `if provider == "paystack"`, `match provider:`, a `Provider` enum, or a provider-keyed dict of behaviours | the connector distribution; selection is a capability binding in the Integrator |
| importing a provider SDK, or writing a provider HTTP client (`paystack`, `flutterwave`, `remita`, `mono`, a Meta Graph call) | the connector distribution, the only artifact that performs provider I/O |
| a provider API key, webhook signing secret or OAuth credential in product config, env, settings rows, or a secret path the product dereferences | the Integrator's manifest-declared secret bindings, materialized only at the dispatch boundary (ADR-0009, § 7) |
| a provider-named route (`/webhooks/paystack`), task, queue, column, setting key, feature flag or table | Integrator ingress at `/ingress/{connector_key}/{capability_id}`; the product receives a typed, provider-neutral observation |
| a provider-named string inside a business decision — status mapping, error-code translation, currency scale, retry eligibility | the connector, which translates its wire vocabulary into the contract's vocabulary before the product sees it |
| a "which provider is configured?" read anywhere on a request path | nowhere. A product that can ask is a product that will branch |

A product legitimately holds the *contract* vocabulary — capability ids,
command payload fields, outcome statuses — plus an opaque Integrator
correlation reference. It never holds the provider's.

**8.3 Configuration selects the adapter; an ADR selects the owner.** Restating
§ 4's last paragraph, because the outbound direction makes it easy to lose:
moving payout traffic between two connectors is a binding change and is
legitimate. Moving *who decides a payout* is not configurable at all, and needs
an accepted ADR — ADR-0061 § 1 is that ADR for payouts.

**8.4 Known divergence at amendment.** The SPI has nowhere to declare a command
payload. `dotmac_integration.spi.CapabilityDeclaration` carries
`capability_id`, `config_schema` and `modes`; `DispatchRequest.payload` is an
unvalidated `dict[str, object]`. Configuration has a declared schema and
commands do not — which is precisely why 8.1 was breached without any gate
noticing. Two shipped capability families have each grown two dialects:

- **payments** — Paystack's `{"action", "params"}` envelope, with a provider
  reference DERIVED from the engine idempotency key and a connector-owned wire
  scale that refuses `currency_minor_units`, against Flutterwave's and Remita's
  flat per-capability payload with a PRODUCT-minted reference and a REQUIRED
  `currency_minor_units`;
- **`messaging.send.v1`** — `meta_whatsapp`'s
  `send_text | send_template | send_media` with a `recipient` param, against
  `meta_social`'s `send_direct_message | reply_to_comment` with `recipient_id`
  plus `channel`.

ADR-0061 § 4 records the payments case in full and its § 5 names the closure
sequence. Until a declared command payload exists, 8.1 is a rule the gates
cannot see.

### 9. Connector completeness is Dotmac capability parity, never provider surface parity

**9.1 The definition.** The connector ecosystem is complete when **every
capability the Dotmac ecosystem needs has an implementation, and every
capability that matters has more than one interchangeable provider behind it.**
Completeness is measured against Dotmac's capability contracts, in the
direction of substitutability.

**9.2 What it explicitly is not.** It is not wrapping every endpoint a provider
happens to expose. A provider's catalogue is that provider's product strategy;
importing it wholesale builds unreviewed surface, invents capability ids nobody
asked for (8.1), and — where the surface moves money or publishes on someone's
behalf — ships code whose first execution is also its first review. "The
provider has an endpoint for it" is not a requirement. This is ADR-0017's
scarce-resource rule applied to connectors: the constraint is a consumer, not
an endpoint.

**9.3 The corollary, already in force.** Three outbound surfaces are
deliberately withheld because no product consumer exists. This amendment
records that as compliance, not backlog:

| Withheld | Connector | Present declared surface |
|---|---|---|
| LinkedIn outbound (publish, message, lead write-back) | `dotmac-connector-linkedin` | INGRESS-only: `social.activity.observation.v1`, `marketing.lead.observation.v1`; declares deny-all provider egress |
| Mono writes (payment initiation, account actions) | `dotmac-connector-mono` | POLL-only: `banking.transaction.observation.v1` |
| Flutterwave v4 transfers/payouts | `dotmac-connector-flutterwave` | DELIVERY on `payments.intent.v1` and `payments.refund.v1` only |

Adding any of them is a capability-parity decision under 9.1, taken when a
named product owner asks — and, per 8.1, implemented as the EXISTING contract
(`payments.payout.v1` for the Flutterwave case) rather than a new
provider-shaped id.

**9.4 A withheld capability is declared, not merely absent.** ADR-0032's rule
applies: unobserved is unknown, never absent. A connector that withholds a
surface records it in its `EXTRACTION.toml` — the capability id it would
implement, and the fact that no consumer has asked — so "not built" is a
reviewable statement rather than a silence indistinguishable from an oversight.
`dotmac-connector-flutterwave`'s `withheld_capabilities` is the shape. It named
two invented provider-shaped ids and is corrected to `payments.payout.v1` in
the same change as this amendment, with the previous entry preserved in a
comment beside it.

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

**Added 2026-08-24, and stated as gaps rather than as guards.** ADR-0018
forbids claiming an exemption or a control that has no enforceable premise, so
the amendment's §§ 8–9 are today **review discipline, not automation**, and the
missing machinery is named:

- **8.1 has no gate.** Nothing compares two connectors' payload handling for a
  shared capability id, because there is no declared payload to compare against
  (8.4). A gate becomes possible only once
  `CapabilityDeclaration` can carry a command schema; at that point the natural
  check is that every connector declaring a capability validates against the
  same schema object, driven with a deliberately divergent fake connector as
  its sensitivity proof.
- **8.2's product-side list is partly ratcheted, partly not.**
  `docs/inventories/external-connector-sources.md` and its ratchet already
  count `http_client`, `webhook_surface`, `provider_credential`,
  `connector_task`, `sync_checkpoint` and `delivery_retry` per repository —
  rows 2, 3 and 4 of the table. Rows 1, 5 and 6 (a provider conditional, a
  provider string inside a decision, a "which provider?" read) are **not**
  measured anywhere, and this amendment does not pretend otherwise.
- **9.4 is enforced only where a connector already declares it.**
  `dotmac-connector-flutterwave`'s boundary test asserts
  `withheld_capabilities` is non-empty; no gate requires the key of any other
  connector, so a silently withheld surface stays silent. The dossiers for
  `dotmac-connector-linkedin` and `dotmac-connector-mono` record their withheld
  surfaces in prose in the same change; making that a required field is a
  separate, reviewable ratchet.
- **ADR-0062 § 5 D1 applies here too:** no check stops a module shipping a
  metrics exporter.

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
