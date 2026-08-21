# ADR 0003 — One Starter, Composable Deployment Profiles

**Status:** Accepted (Amended 2026-07-30, 2026-08-21)
**Date:** 2026-07-18
**Supersedes:** ADR-0002's 2026-07-18 deployment-positioning amendment for new development
**Extends:** ADR-0001 multi-tenancy and the module/plugin control-plane directive

> **Amendment 2026-07-30 (vendor control-plane ownership rulings C1–C7).** While designing the
> vendor control-plane domain foundation
> (`docs/superpowers/plans/2026-07-30-vendor-control-plane-domain-foundation.md`), seven
> ownership ambiguities between this ADR, the deployment-profiles plan, and the kernel-boundary
> plan were surfaced and ruled on by Michael. The load-bearing clarifications:
> - **The topology diagram below expresses LOGICAL composition, not a monorepo requirement**
>   (ruling C5). The vendor control plane is a **separate maintained repository** — recommended
>   `dotmac_vendor_control_plane` — that pins released kernel/module versions. It is not a
>   package directory inside this repo.
> - **The kernel owns reusable protocols and primitives only.** Fleet `Deployment`,
>   provisioning requests/steps/approvals, desired/observed state, and fleet history are owned
>   by the vendor control plane, not the kernel/starter (ruling C1). Support-access enforcement
>   contracts are kernel; the request/consent/break-glass workflow is the control plane (C2).
>   Fleet/support/maintenance/incident admin surfaces belong to the control-plane portal; the
>   kernel supplies only auth + portal-composition machinery (C7).
> - **A release-channel pin never authorizes a deployment.** It is desired state only under
>   `vendor automatic` update authority; under `customer-approved`/`offline bundle` it is an
>   offer (rulings C3, consistent with the update-authority axis already in this ADR).
> - **The `ProvisioningProvider` protocol (protocol + typed plan/apply/observe results + stable
>   errors + fake + contract suite) moves into the kernel alpha** (ruling C6); the control plane
>   consumes it and never defines a local replacement.
> See the plan's "Ownership rulings" section for the full text.

> **Amendment 2026-08-21 (the ISP path is a controlled replacement programme, not an in-place
> adaptation of `dotmac_sub`).** Governance ADR 0012 — *The Dotmac ISP replacement is one
> controlled programme* — was accepted on 2026-08-20 and merged at the immutable Governance
> revision `68c7a62e2aafd9c236662a5a69d410ea002b4cdb`
> ([ADR 0012](https://github.com/michaelayoade/dotmac_governance/blob/68c7a62e2aafd9c236662a5a69d410ea002b4cdb/docs/adr/0012-dotmac-isp-replacement-programme.md),
> [`programmes/dotmac-isp-replacement.json`](https://github.com/michaelayoade/dotmac_governance/blob/68c7a62e2aafd9c236662a5a69d410ea002b4cdb/programmes/dotmac-isp-replacement.json)).
> That record cites this ADR as a technical source and requires Starter's technical replacement
> ADR to cite the accepted revision before changing any conflicting local first-cutover
> statement. This amendment is that citation.
> - **What changes.** The sentence below — "the ISP path adapts the existing `dotmac_sub`
>   rather than starting another ISP rewrite" — no longer states the accepted direction. The
>   accepted programme (`pgm-dotmac-isp-replacement`) BUILDS an independent thin ISP assembly
>   (`asm-dotmac-isp`, repository `https://github.com/michaelayoade/dotmac-isp`, independent
>   database boundary, authority state `candidate`) and cuts legacy Sub
>   (`asm-dotmac-sub-legacy`, `source-authoritative`) over cohort by cohort. Two tracks —
>   `track-isp-target-build` and `track-isp-sub-cutover` — may advance concurrently.
> - **What survives.** The original sentence was defending against a copied application kept as
>   a long-lived branch and against a second uncontrolled ISP implementation. Both still hold:
>   the target is a THIN assembly over independently released Starter owners, not a from-scratch
>   re-invention of Sub's domain logic, and its behaviour is extracted PRODUCT-FIRST from Sub's
>   proven implementation and tests (ADR-0006's extraction amendment; `AGENTS.md` rule 22).
>   Each application runs its own pinned copy and lineage of a shared module and owns rows only
>   in its own database (ADR-0024).
> - **What in-place Sub work remains allowed.** Only bounded source-track work under a
>   separately approved transition rule (Governance control `ctl-isp-003`, BLOCKED at the cited
>   revision): containment, evidence repair, migration or shadow adapters, or an explicitly
>   justified change that retires one local parallel writer. Permanent new Sub domain logic
>   stays barred, and in-place module adoption inside Sub is neither target adoption nor a
>   cohort cutover.
> - **Concurrency is not concurrent authority.** For each cohort, Sub remains the sole
>   production decision and write owner until its sealed switch (ADR-0031). Shadow paths record
>   and compare observations only; they decide no lifecycle state and feed no production
>   consequence. After the switch the ISP assembly is that cohort's sole authority and the
>   displaced Sub writers and fallbacks ratchet to zero.
> - **Who owns what.** Governance owns programme identity, control/cohort identifiers, ordering
>   and approval state; this repository keeps technical boundaries, reusable implementation and
>   measured inventories. The cohort list and control states are NOT restated here — read the
>   programme record at the cited revision, which is the only place they are normative.
> - **This amendment moves nothing.** It records an accepted direction. It advances no authority
>   state, names no deployment host, opens no cohort, and verifies no control. `ctl-isp-002`
>   (named target runtime database and production deployment owner) and `ctl-isp-003` are both
>   blocked at the cited revision, and every cohort stays blocked behind the full cutover-control
>   set.

## Context

The starter must support several commercial and operational shapes:

- vendor-operated, shared multi-tenant SaaS;
- vendor-operated, dedicated single-tenant deployments;
- customer-operated self-hosted/on-premise deployments, including air-gapped sites;
- OEM/partner-operated and white-labelled deployments; and
- simple single-tenant products.

Maintaining a separate application starter for each shape would duplicate the structural
parts that are hardest to keep correct: tenant context, PostgreSQL RLS, identity, settings,
permissions, audit, module manifests, migrations, API contracts, and security fixes. The
differences are primarily composition, commercial authority, packaging, connectivity, and
operator responsibility — not different domain models.

The existing module/plugin control-plane directive already establishes a single manifest
contract for installed capabilities. This ADR applies that composition model to deployment
topology and commercial controls.

## Decision

`dotmac_starter_mt` is the canonical foundation for all new supported deployment profiles.
A single-tenant deployment provisions exactly one tenant and retains `Tenant`, tenant
context, composite tenant constraints, and RLS. Single tenancy is a topology, not a second
application architecture.

The legacy `dotmac_starter` repository may remain available for existing/simple legacy
uses, but it is not a second strategic foundation for new development. This decision does
not require deleting or archiving that repository; it prevents future capability work and
security architecture from forking by deployment type.

Deployment variation is expressed through:

1. a typed `DeploymentProfile`;
2. installed and enabled `ModuleManifest`s;
3. provider interfaces for commercial authority, provisioning, identity, telemetry,
   updates, secrets, storage, ingress, DNS, TLS, FX, tax, and notifications;
4. explicit tenant, subscription, entitlement, provisioning-job, domain, and license
   lifecycles coordinated through idempotent commands and events;
5. profile-specific deployment assets (Compose/Helm/install/upgrade/backup); and
6. CI matrices that test both profile composition and end-to-end lifecycle scenarios.

Scattered `if deployment_mode == ...` checks in feature code are forbidden. Features call
provider interfaces and capability/entitlement evaluators without knowing the deployment
profile.

## Cross-project reuse and release model

Cloning a starter creates a snapshot; it does not propagate later fixes. New products
therefore compose four explicit layers:

```text
versioned platform kernel
  + versioned first-party/trusted modules
  + product assembly (domain modules, providers, brand, policies)
  + deployment profile and environment configuration
```

The platform kernel owns only the invariants that must be corrected once: tenancy/RLS,
identity seams, authorization/capability evaluation, lifecycle command/event contracts,
settings/flags, module registry, audit, database/session rules, observability, API/error
contracts, and migration orchestration. Billing, subscriptions, metering, licensing,
domains, notifications, files, and product workflows remain optional modules even when
maintained in the same source repository.

A `ProductAssemblySpec` declares module/provider versions and product-owned modules; it
must not copy or monkey-patch kernel code. DotMac-owned products may live as assemblies in
one monorepo for immediate source-level reuse. Independently shipped, on-prem, or OEM
products consume signed, versioned kernel/module packages or base images and pin exact
versions in their lock/build metadata.

A kernel fix is released once, but it reaches deployments through a controlled update:

```text
fix -> kernel/module release -> automated product update PR
    -> product profile + lifecycle + migration tests -> signed image/bundle
    -> staged rollout or customer-approved/offline upgrade
```

No running deployment silently changes because another deployment was fixed. Automated
dependency updates, compatibility ranges, security advisories, SBOM/provenance, migration
gates, and support windows make propagation reliable without bypassing release safety.
Air-gapped/OEM installations receive the same fix through a signed offline update bundle.

Different products (for example ERP and ISP subscriber management) are separate product
assemblies and normally separate data-plane deployments/databases. Sharing the kernel does
not imply shared ORM models, migrations, database access, release cadence, or failure
domain. A vendor account/control plane may link product deployments and commercial
relationships; data planes integrate through versioned APIs/events and external IDs.

The vendor control plane is itself a thin product assembly built on the kernel; it is not
the kernel and its commercial/deployment tables do not belong in every generated product.
The maintained topology is:

```text
dotmac_starter_mt / platform packages   (kernel + optional modules; published, versioned)
  ├── dotmac_vendor_control_plane   (SEPARATE repo — accounts, contracts, fleet, licences, vendor billing)
  ├── dotmac_sub assembly (ISP subscriber and network operations)
  └── dotmac_erp assembly (ERP domains)
```

> Amended 2026-07-30 (C5): this is LOGICAL composition — each assembly is its own maintained
> repository pinning released kernel/module versions, NOT a package directory under this repo.
> The vendor control plane's recommended repository name is `dotmac_vendor_control_plane`.

These are durable products, not long-lived Git branches of one copied application. A
temporary integration branch is normal delivery mechanics, but the ISP path adapts the
existing `dotmac_sub` rather than starting another ISP rewrite. Kernel work continues in
the starter/package source; product work continues in its product repository or assembly
directory. Products pin released kernel/module versions and receive update PRs.

> Amended 2026-08-21: the second sentence is SUPERSEDED for the ISP path. Under accepted
> Governance ADR 0012 (revision `68c7a62e2aafd9c236662a5a69d410ea002b4cdb`) the ISP path builds
> the independent `asm-dotmac-isp` assembly and cuts `dotmac_sub` over cohort by cohort; it does
> not adapt Sub in place. The rest of the paragraph — durable products, not long-lived branches
> of a copied application; kernel work in the starter, product work in the product repository;
> exact version pins and update PRs — is unchanged and applies to the ISP target as written.
> See the 2026-08-21 amendment at the top of this ADR.

The tracks may progress in parallel only across published contracts. Before the first ISP
adoption cutover, the kernel must publish its security/session boundary, module manifest,
product assembly, entitlement, lifecycle command/event, and versioning contracts. While
those stabilize, `dotmac_sub` may add characterization tests, an ownership/mapping ledger,
assembly metadata, and adapters without replacing its working identity, billing, or schema.
Contract-dependent replacement work waits for the corresponding kernel release.

> Amended 2026-08-21: this paragraph's contract-first ordering still holds, but the permitted
> in-place Sub work is now bounded by Governance control `ctl-isp-003` rather than by this
> paragraph's list. Until that transition rule is approved and enforced, treat the narrower
> ADR 0012 set — containment, evidence repair, migration or shadow adapters, or a justified
> change that retires one local parallel writer — as the operative limit.

For a multi-ISP product, the ISP operator is the platform tenant and the ISP's subscribers
are product-domain parties/customers inside that tenant. Dedicated-per-ISP deployments are
the safe first profile for a legacy single-operator ISP app. Shared multi-ISP SaaS requires
an explicit tenant-safety program across every table, worker, cache, object, search index,
provider credential, network operation, export, and webhook; it is not achieved by adding
a tenant row around an otherwise single-tenant schema.

## Infrastructure provisioning and deployment execution

Kubernetes is an execution provider, not the tenant/deployment control plane and not a
prerequisite for onboarding dedicated ISPs. The platform database is the desired-state
authority for `Deployment`, `ProvisioningRequest`, and step/job history. A restricted
provisioning worker—not the signup HTTP request and not a tenant application role—executes
idempotent infrastructure workflows through provider interfaces.

The initial dedicated-ISP profile uses one isolated VM or cloud project/account per ISP,
a managed PostgreSQL database where available, object storage, external secrets, DNS/TLS,
and the existing immutable-image Docker Compose deployment. OpenTofu modules provision
cloud resources with encrypted, locked remote state isolated per deployment; cloud-init or
Ansible performs repeatable host bootstrap. The application deploy step keeps migrations,
backup, health gates, and rollback distinct from infrastructure creation.

This is the default until workload or operations evidence justifies Kubernetes. A later
managed-Kubernetes provider uses Helm and GitOps for regional fleets, normally retaining a
separate database and secrets boundary per ISP. A namespace is useful organization but is
not represented as equivalent to an isolated account/project or cluster. Regulated or
high-risk customers may require a dedicated cluster/account; on-premise profiles may use
Compose first and an optional K3s/other conformant Kubernetes package when high availability
or customer standards require it.

The end-to-end workflow is durable and resumable:

```text
verified signup/contract
  -> commercial account + order + pending deployment
  -> provisioning outbox command
  -> plan/approval/apply infrastructure
  -> bootstrap host/runtime and secrets
  -> deploy pinned signed image and migrate
  -> create the one ISP tenant and owner invitation
  -> project entitlements and install signed license
  -> configure/verify DNS and TLS
  -> health, security, backup/restore, and callback checks
  -> activate service and start recurring/usage billing
```

Each step records desired and observed state, attempts, external resource IDs, evidence,
and compensation/repair instructions. Replayed commands are safe; concurrent workflows are
serialized per deployment. Destructive cleanup, production activation, migrations outside
a proven compatibility window, and billable resource creation may require policy-based
approval. Billing starts from the contracted activation rule, not merely because a signup
form was submitted.

Day-two operations use the same desired-state model for upgrades, certificate and license
renewal, backup verification, drift detection, scaling, suspension, export, disaster
recovery, and termination. CI may build and attest artifacts, but long-lived cloud and
customer credentials belong to the restricted provisioning runner/secret authority rather
than the public application or product repository.

### On-premise source and intellectual-property boundary

No supported design claims that source or executable logic is inaccessible to a customer
who controls the host, hypervisor, root account, container runtime, or physical machine.
OCI images expose their filesystem layers to that operator; Python bytecode or native
compilation may increase reverse-engineering cost but does not create a cryptographic
confidentiality guarantee. Local license enforcement can also be patched by a sufficiently
privileged operator. Contracts, operational controls, and technical hardening address
different risks and must not be represented as interchangeable.

The standard on-prem distribution therefore:

- ships a signed, customer/deployment-bound OCI image or offline bundle by digest, never a
  Git checkout, build context, repository credential, source map, test suite, or `.git`
  history;
- uses a multi-stage build so build tools, source inputs not required at runtime, caches,
  and build secrets never enter any final image layer; merely deleting them in a later
  layer is insufficient;
- minimizes the runtime image, runs non-root with a read-only filesystem and no routine
  shell/debug surface, and verifies artifact signature, provenance, SBOM, and license
  binding before installation;
- keeps license-signing, artifact-signing, provider, support, and fleet credentials outside
  the deployment. It contains public verification material and short-lived deployment
  credentials only;
- may compile selected high-value modules to native artifacts, split especially sensitive
  algorithms into a vendor service, or embed a non-secret signed customer fingerprint for
  leak attribution, while documenting that these measures raise effort rather than prevent
  extraction; and
- is governed by a software licence/MSA that defines permitted users/deployments,
  redistribution and reverse-engineering restrictions where enforceable, audit/support
  terms, breach handling, and data/privacy obligations. Legal terms require counsel for the
  relevant jurisdiction.

Offer explicit assurance tiers instead of one ambiguous `on-prem` promise:

1. **Vendor-managed dedicated:** customer data is isolated, but the customer has no host or
   image access; this is the recommended profile when source confidentiality is mandatory.
2. **Customer-controlled standard on-prem:** signed binary/image distribution, hardening,
   licensing, and contractual protection; residual extraction/tamper risk is accepted.
3. **Hardened appliance or confidential-computing profile:** verified hardware/secure boot,
   measured deployment, remote attestation and conditional key release may reduce host-
   administrator access, but require a separate hardware/provider threat model, recovery
   design, performance test, and support contract; they are not an absolute guarantee.
4. **Source-available escrow/custom:** source access is intentional and controlled by a
   separately priced agreement, escrow trigger, OEM licence, or customer-specific contract.

If a prospective customer demands both unrestricted root/physical control and a guarantee
that they cannot inspect the shipped program, the requirements conflict. The commercial
choice is vendor-managed hosting, a supported attested appliance with stated residual risk,
or accepting/licensing source exposure—not promising an unenforceable technical property.

## Fleet observability, support, and maintenance

Support is a contracted, tenant-aware platform capability, not permanent SSH access. The
telemetry, support-access, and update providers are independent profile axes: an ISP may buy
support while retaining local-only telemetry or customer-controlled maintenance. Support
tier affects response targets, service hours, seats, managed-upgrade eligibility, and
escalation routing; it never bypasses actor permissions, tenant isolation, consent, or
audit. Critical security advisories and supported-version policy remain fleet obligations,
not paid feature flags.

Every deployment emits vendor-neutral, correlated logs, metrics, and traces through a local
OpenTelemetry-compatible collector. Resource identity includes server-assigned product,
deployment, environment, region, version, and component identifiers. It excludes raw
subscriber identity, credentials, tokens, payment payloads, RADIUS secrets, message bodies,
and unrestricted database/query contents. High-cardinality customer/subscriber IDs are not
metric labels. A contracted allowlist, redaction/filtering, sampling, bounded buffering,
retention, encryption, residency, and deletion policy applies before export.

Connected managed deployments use outbound-only mutually authenticated telemetry to a
regional gateway; the gateway derives deployment/tenant scope from authenticated identity
instead of trusting caller-supplied labels. Restricted profiles may export only a signed
fleet heartbeat and aggregate health. Air-gapped profiles retain telemetry locally and
export a customer-approved, redacted, encrypted diagnostic bundle. Failure or disabling of
telemetry never blocks the product's request path.

The health contract separates:

- public liveness: process is alive, with no database/provider access or sensitive detail;
- internal readiness: database connectivity, migration compatibility, required modules,
  queues, cache, storage, and critical provider readiness;
- authenticated diagnostic status: version/config fingerprints, dependency health,
  backlog age, last backup/restore test, certificate/licence expiry, capacity, and drift;
  and
- external synthetic checks: DNS/TLS, tenant host, login/API journey, and selected ISP
  service journeys such as authentication/provisioning, without real subscriber secrets.

Central observability keeps tenant/deployment data isolated at ingestion, storage, query,
dashboard, alert, export, and retention boundaries. Cross-fleet views expose aggregate
health; raw cross-tenant queries require a named platform role, ticket/reason, and audit.
Alerting pages only actionable symptoms tied to customer impact or imminent capacity/data-
protection risk, deduplicates common failures, honors maintenance windows, and links the
deployment, runbook, dashboard, release, and support/incident record.

Support workflow is explicit:

```text
case/ticket -> severity + entitlement/SLA -> diagnostics requested
  -> tenant consent or policy-approved emergency path
  -> time-bounded least-privilege application or infrastructure grant
  -> outbound support channel/session with recording and command/action audit
  -> finding/change/verification -> grant expiry/revocation -> customer-visible summary
```

Application impersonation and host access are separate grants. There are no shared support
passwords, permanent vendor SSH keys, hidden accounts, unapproved tunnels, or invisible
global impersonation. Connected customer-controlled deployments initiate any remote support
channel outbound and can terminate it. Break-glass requires a named incident/ticket,
short expiry, least privilege, two-person approval where practicable, immediate customer
notification unless legally restricted, complete recording/audit, and post-use review.

Diagnostic bundles use a declared schema and include only the requested time window,
versions, sanitized configuration fingerprints, selected health/telemetry, provider job
history, and correlation IDs. They exclude secrets and business records by default, show a
preview/manifest to the customer, are signed and encrypted for the intended support
recipient, expire automatically, and record creation/download/deletion.

Maintenance is desired-state fleet reconciliation. The control plane inventories exact
artifact/config/schema/module versions, support/EOL state, drift, backup/restore evidence,
and security exposure. Releases advance through dev, internal, canary, early-adopter, and
general rings subject to customer maintenance windows and approval policy. Preflight,
backup, compatibility/migration, deploy, readiness/synthetic, observation, wave halt, and
rollback evidence are retained. Database changes remain expand/contract and backward-
compatible across the rollback window.

Each product declares SLIs/SLOs, recovery objectives, alert/runbook ownership, and product-
specific signals. The common kernel covers HTTP latency/error/saturation, job/outbox lag,
database/pool/migration health, certificate/licence expiry, backups, deployment drift, and
telemetry pipeline health; the ISP assembly adds RADIUS/authentication, provisioning,
network-provider, usage ingestion, invoice/collection, and subscriber-service signals.
Incident lifecycle covers detection, acknowledgement, ownership, containment, stakeholder
and status communication, recovery verification, resolution, and blameless follow-up with
tracked corrective work.

## API and web UI model

The application is API-first at the service boundary, not API-only. Domain rules and
transactions live in services; JSON routers, the server-rendered Jinja/HTMX web portal,
workers, CLI commands, and external frontends are adapters over those same services.

The built-in `web` module remains the first-party admin/reference UI and works in every
profile that enables it. `WEB_ENABLED=false`/an API-only profile omits it without changing
the underlying APIs. Separate SPA, mobile, partner, or customer frontends use versioned
JSON APIs, pinned OpenAPI contracts/generated SDKs, stable error codes, and an effective-
capability/bootstrap endpoint describing enabled modules, permissions, flags, locale, and
branding. Frontends never infer access from plan names or call the database/providers
directly.

Cookie/CSRF authentication remains valid for the built-in same-origin web UI. External
frontends use an explicitly configured OAuth/OIDC or bearer-token flow, CORS/origin policy,
and BFF where appropriate. Both surfaces must pass the same authorization, tenant
isolation, lifecycle, and contract tests; web routes may not contain business logic absent
from the API/service path.

## Independent deployment axes

Profile names are conveniences over independent axes, not a single enum that features
branch on:

| Axis | Examples |
|---|---|
| Tenancy topology | shared multi-tenant; dedicated one-tenant |
| Operator | DotMac/vendor; customer; OEM partner |
| Connectivity | online; intermittently connected; air-gapped |
| Commercial authority | SaaS subscription; contract grant; signed license |
| Identity authority | local; OIDC/SAML; OEM IdP |
| Branding authority | vendor; tenant; OEM |
| Domain/ingress authority | vendor-managed; customer-managed; OEM-managed |
| Locale policy | one locale; tenant/user-selectable locale set; OEM locale pack |
| Currency policy | single currency; tenant-selected; transaction-selected |
| Legal/tax authority | vendor merchant; customer entity; OEM/partner entity |
| Data residency | vendor region; customer site; jurisdiction-pinned region |
| UI surface | built-in web + API; API-only; external frontend/BFF |
| Update authority | vendor automatic; customer-approved; offline bundle |
| Telemetry policy | required operational; optional; disabled |

## Profile contract

The target contract is declarative:

```python
DeploymentProfile(
    code="on_prem",
    required_modules={"auth", "parties", "rbac", "settings", "web", "entitlements"},
    forbidden_modules={"billing", "vendor_support"},
    commercial_provider="signed_license",
    provisioning_provider="local_bootstrap",
    identity_provider="local",
    telemetry_provider="disabled",
    update_provider="offline_bundle",
    ingress_provider="nginx_static",
    dns_verification_provider="manual_txt",
    tls_provider="customer_pki",
    default_locale="en",
    supported_locales={"en"},
    allowed_currencies={"USD"},
    legal_authority="customer",
    data_residency="customer_site",
)
```

Profile validation runs at startup and fails before serving if required providers/modules
are missing, forbidden modules are installed/enabled, dependencies are unsatisfied, or the
database migration state is incompatible.

## Standard profiles

### SaaS

Vendor-operated, normally shared multi-tenant. Includes the secured platform control
plane, tenant provisioning, entitlements, optional subscription/billing/metering modules,
customer self-service, central telemetry, and vendor-managed updates.

### Dedicated hosted

Vendor-operated with one tenant per deployment or database. Keeps the same tenant/RLS
model. Commercial access may come from a contract entitlement rather than an in-product
payment provider.

### Self-hosted/on-premise

Customer-operated, one or more tenants. Includes local bootstrap administration, signed
offline licensing where commercially required, local backup/restore and upgrade tooling,
customer-controlled identity/secrets, and optional telemetry. Vendor platform routes,
mandatory phone-home behavior, and SaaS billing are absent.

### OEM

Partner-operated and white-labelled. Adds OEM branding, delegated license/entitlement
authority, partner administration, partner module packs, and an explicit compatibility
and update policy. An OEM deployment may itself be shared multi-tenant or dedicated.

### Single tenant

Not a separate product profile. It is a topology used by dedicated hosted, self-hosted,
OEM, or even vendor-managed SaaS deployments. It always retains the tenant row and RLS.

## Tenant and commercial lifecycle

The platform uses related state machines, not one overloaded `tenant.status` field:

```text
tenant:       requested -> provisioning -> onboarding -> active
                         -> restricted -> suspended -> terminating -> retained -> purged
subscription: pending -> trialing -> active -> grace -> past_due -> cancelled | expired
job:          pending -> running -> complete | retryable_failed | terminal_failed
domain:       requested -> pending_dns -> verified -> pending_tls -> active -> removing
license:      pending -> active -> grace -> expired | revoked
```

These states are correlated but independently authoritative. A failed payment changes
billing/subscription state first; a versioned commercial policy decides whether and when
that projects to entitlement restriction or tenant suspension. It never directly deletes
or corrupts tenant data.

Every cross-module lifecycle operation uses:

- an idempotent command with actor, tenant, correlation, causation, and idempotency IDs;
- one named transaction owner for local state;
- a transactional outbox for committed events and an idempotent inbox for consumers;
- retryable provider jobs with observed state, last error, and next retry;
- explicit compensation for partially completed external work; and
- an explainable lifecycle history and repair/reconcile action.

Representative onboarding flows are profile-specific:

- free/manual-contract: approve contract/grant -> provision tenant and owner -> grant
  entitlements -> activate services/domains;
- self-service paid SaaS: select immutable offer -> create payment/subscription intent ->
  provision according to the product's prepay/trial policy -> grant entitlements;
- on-prem/OEM: validate signed/delegated license -> local/partner bootstrap -> project
  entitlements -> activate locally available services.

Offboarding coordinates cancellation timing, final invoice/refund, entitlement restriction,
read-only/export windows, session/API-key/integration revocation, domain/certificate
removal, external resource cleanup, retention/legal hold, backup expiry, and auditable
purge. Destructive steps require impact preview and are never an implicit consequence of
module disablement or payment failure.

Support access is an explicit lifecycle grant: tenant consent or a time-bounded
break-glass policy, named reason/ticket, least privilege, notification, complete audit,
and automatic expiry/revocation. There is no invisible global impersonation bypass.

## Commercial modules are separate

Licensing/entitlements do not imply billing or subscriptions. These are separate modules
with one-way dependencies:

```text
manual contract ----+
subscription -------+--> entitlement grants --> request-time capability decision
signed license -----+

payment provider --> billing state --> subscription state
usage meter -------> quota decision and, optionally, billing
```

- `entitlements` is the common access-decision foundation. It evaluates grants, limits,
  dates, sources, and reasons. It never calls a payment provider during a request.
- `subscriptions` models plan/version assignment, trials, renewals, grace periods,
  upgrades, downgrades, and cancellation. It is optional.
- `billing` owns provider customer/payment/invoice state and idempotent webhook ingestion.
  It is optional and may be replaced by manual/ERP invoicing.
- `metering` records immutable, idempotent usage and derives quota/billing aggregates. It
  is optional unless a capability has quantitative limits or usage pricing.
- `licensing` verifies signed offline/delegated licenses and projects their claims into
  entitlement grants. It is optional for hosted SaaS and normally required for commercial
  self-hosted/OEM distribution.

Feature code depends only on entitlement/quota decisions. It never checks plan names,
billing-provider status, license payloads, or deployment modes directly.

All module and capability access uses one canonical entitlement store and evaluator.
Module access is a declared capability such as `inventory.use`; there is no parallel
`tenant_module_entitlements` authority. When multiple grants exist, the entitlement
policy is deterministic and versioned:

- an explicit active deny/revocation overrides ordinary allows unless a separately
  authorized emergency override names its scope and expiry;
- expired, not-yet-active, invalid, or revoked grants do not allow access;
- source precedence and manual-override rules are declared centrally, never in features;
- quantitative limits declare their combine strategy (`minimum`, `maximum`, `sum`, or
  highest-priority source) in the capability definition;
- grace behavior is a commercial-policy projection, not an evaluator guess; and
- every decision reports the winning grant/rule, effective limit, reason, source version,
  and cache version.

## Provider interfaces

The target provider seams include:

```python
class CommercialAuthority:
    def entitlement(self, context, capability: str) -> EntitlementDecision: ...

class ProvisioningAuthority:
    def provision(self, request) -> ProvisioningResult: ...

class TelemetryProvider:
    def emit(self, event) -> None: ...
```

Concrete implementations include subscription, signed-license, and contract commercial
authorities; platform, local-bootstrap, and OEM provisioners; and vendor, optional, or
disabled telemetry providers.

## Internationalization, currency, and jurisdiction

Locale, language, timezone, currency, legal entity, tax jurisdiction, and data residency
are independent concepts. The platform must never infer currency or legal jurisdiction
from UI language, timezone, domain suffix, IP address alone, or tenant deployment mode.

Internationalization uses stable message identifiers and versioned locale catalogs.
Canonical domain values, enum/code values, audit actions, API error codes, and stored
business facts remain language-neutral. Resolution is user preference -> tenant default ->
deployment default, with explicit fallback. Web, email, PDF, and notification renderers
share locale-aware pluralization, number/date formatting, Unicode/IDNA handling, and RTL
support. APIs continue returning canonical UTC timestamps, ISO codes, and stable error
codes; translated text is presentation, not authority.

Money uses ISO 4217 currency codes and exact decimal/minor-unit rules—never binary floats.
The model distinguishes transaction/invoice currency, tenant functional currency, and
provider settlement/payout currency. Every conversion records the FX provider, rate type,
rate value, effective timestamp, source/target currencies, rounding policy, and immutable
rate snapshot used by the transaction. Prices and plan versions declare supported
currencies; an invoice has one currency unless a specific provider contract says
otherwise.

Jurisdiction policy is versioned data/provider output, not hardcoded country conditionals
inside billing features. It selects the merchant/legal entity, tax registrations, customer
location evidence, tax-inclusive/exclusive treatment, exemptions/reverse charge, invoice
numbering and required fields, fiscal rules, retention/privacy/consent obligations, and
data-residency constraints. A tax provider may calculate tax, but the internal invoice
snapshot records the applied jurisdiction/policy version and result for audit and replay.
Projects enable only the countries, currencies, locales, and legal entities they actually
support.

## Domains, DNS, TLS, and ingress

Domain routing is provider-composed, not coupled to Nginx. The profile declares:

- one exact platform/control-plane host;
- one or more tenant base domains for `{tenant-slug}.{base-domain}`;
- an optional stable custom-domain CNAME/ALIAS target;
- an ingress provider and TLS provider; and
- whether public ACME, DNS-01, managed certificates, manual certificates, or customer PKI
  is permitted.

Nginx can terminate a static wildcard certificate and proxy tenant hosts, but vanilla
Nginx is not the custom-domain lifecycle authority. Dynamic domains require a controller
around Nginx/certbot or another provider implementation such as Caddy, Traefik,
Kubernetes ingress plus cert-manager, or a managed load balancer/certificate service.

The application owns normalized domain-to-tenant desired state. DNS providers own DNS
records, certificate authorities own issuance, and ingress providers own active proxy/TLS
bindings. An idempotent domain reconciler compares desired and observed state and exposes
drift, retry, and repair. A domain becomes routable only after a random DNS TXT ownership
challenge succeeds and TLS is ready; CNAME presence alone is insufficient proof.

Because `TenantDomain` is platform routing state, tenant self-service never receives
direct platform-table write authority. A tenant-authorized domain command creates an
audited `DomainChangeRequest`/outbox record; a platform-authorized reconciler validates
the tenant/capability/limit and performs the platform, DNS, certificate, and ingress
mutations. The audit chain retains both the tenant actor and platform worker identities.

The domain lifecycle includes requested, pending-DNS, verified, pending-TLS, active,
failed/retry, suspended, removing, and removed states. Domain names are lower-cased,
IDNA-normalized, stripped of ports/trailing dots, globally unique, and rejected when they
collide with reserved/platform names. Unknown or unverified hosts fail closed. The proxy
preserves the validated host, replaces untrusted forwarding headers, sets forwarded
scheme, and is the only trusted forwarded-header peer.

SaaS normally uses wildcard DNS/TLS for tenant subdomains plus automated per-custom-domain
certificates. Dedicated deployments normally bind a small explicit host set. On-prem and
air-gapped profiles may use local DNS and customer PKI/manual certificates with no public
ACME dependency. OEM profiles select partner-owned base domains, DNS, and certificate
providers through the same contract.

## Packaging, plugins, and migrations

Built-in features and trusted installed plugins use the same `ModuleManifest` contract.
Python entry-point plugins are trusted in-process code and are installed only through the
build/deploy supply chain. The admin UI may enable already-installed code; it may not
download packages or run `pip install`.

Plugin migrations remain deploy-time operations. Admin enablement is blocked until the
module is validated, migrated, dependency-complete, and healthy. Disabling a module never
deletes its data; retirement requires an explicit archive/export/delete policy and impact
preview.

Profile-specific assets may differ without forking application code:

```text
deploy/
  saas/        Helm/managed-platform configuration
  dedicated/   dedicated Compose/Helm values
  on-prem/     installer, Compose, backup, restore, upgrade, offline bundle
  oem/         partner packaging, branding, delegated-license configuration
```

## Verification

CI must generate, validate, migrate, boot, and smoke-test at least:

- `saas-multitenant`;
- `dedicated-single-tenant`;
- `onprem-online`;
- `onprem-airgapped` (network-dependency denial test);
- `oem-multitenant`; and
- `api-only`.

Each profile test verifies expected/forbidden modules, provider resolution, migration
state, entitlements, tenant isolation, bootstrap/admin access, external-network policy,
and backup/upgrade hooks where applicable.

An orthogonal lifecycle scenario matrix verifies at least:

- free/manual-contract onboarding and activation;
- trial -> paid conversion;
- failed renewal -> grace -> restriction/suspension -> payment recovery;
- plan upgrade/downgrade with effective-date and proration policy;
- metered usage -> quota -> rating -> invoice -> late correction/credit;
- perpetual on-prem and annual-license expiry/grace/renewal;
- OEM delegated provisioning within its grant ceiling;
- domain verification failure, retry, removal, and takeover resistance; and
- cancellation -> final settlement -> export/retention/legal hold -> purge.

## Consequences

- Security and tenancy fixes land once and flow to every deployment type.
- Distribution-specific complexity stays in manifests, providers, and packaging rather
  than feature business logic.
- Internationalization, multi-currency, tax/jurisdiction, legal-entity, and data-residency
  support remain explicit policies/providers rather than hidden assumptions in features.
- The profile/plugin/commercial contracts add deliberate platform work before every
  profile is production-ready; current configuration flags do not yet implement this ADR.
- A separate starter remains justified only when the technical foundation truly changes
  (for example, an embedded application without PostgreSQL or an event-only worker with
  no HTTP application), not merely because the operator or commercial model changes.

## References

- `docs/superpowers/reviews/2026-07-18-module-control-plane-directive.md`
- `docs/superpowers/plans/2026-07-18-deployment-profiles-commercial-platform.md`
- `docs/superpowers/plans/2026-07-18-existing-product-adoption.md`
- `docs/adr/0001-multi-tenant-architecture.md`
- `docs/adr/0002-starter-consolidation.md`
