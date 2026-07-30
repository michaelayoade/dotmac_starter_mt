# Deployment Profiles, Tenant Lifecycle, and Commercial Platform Implementation Plan

> **Status:** Accepted target plan; not implemented. ADR-0003 is the decision authority.
> This plan follows the control-plane-security program and the manifest-driven module
> control-plane directive. It must not be represented in README/ARCHITECTURE as current
> runtime behavior until the corresponding completion gates pass.
>
> **Amendment 2026-07-30 (ownership rulings C1–C7 — lane assignment).** This plan's workstreams
> were not labelled by lane, which the vendor control-plane design surfaced as ambiguities.
> Michael's rulings assign them:
> - **Workstream 11's `Deployment`/`ProvisioningRequest`/`ProvisioningStep`/`SupportAccessGrant`
>   tables and the durable deployment workflow are owned by the vendor control plane** (a
>   SEPARATE `dotmac_vendor_control_plane` repository, ruling C5), NOT the starter/kernel
>   (rulings C1, C2). The kernel owns reusable **protocols/primitives only**.
> - **The `ProvisioningProvider` (protocol + typed plan/apply/observe results + stable errors +
>   fake + parametrized contract suite) moves into the kernel alpha** (ruling C6) — see the
>   kernel-boundary plan's amended Tasks 3/5. Fleet workflows and cloud-specific operations stay
>   OUT of the kernel.
> - **Workstream 2's `tenant_entitlement_grants` is written ONLY by the product data plane**,
>   which verifies a signed/versioned delivery the control plane initiates and acknowledges the
>   applied version/digest (ruling C4).
> - **Workstream 12's fleet/support/incident admin surfaces belong to the vendor-control-plane
>   portal** (ruling C7), on the kernel's portal-composition machinery.
> - **Release channels/waves never authorize a deployment**; a pin is desired state only under
>   `vendor automatic` update authority (ruling C3).
> Full text: `docs/superpowers/plans/2026-07-30-vendor-control-plane-domain-foundation.md`
> § "Ownership rulings".

## Goal

Make one versioned platform kernel safely produce vendor SaaS, dedicated single-tenant,
self-hosted/on-prem, OEM, built-in-web, external-frontend, and API-only products through
thin assemblies, declarative profiles, and provider composition. Add an end-to-end tenant
lifecycle and global-ready primitives, with entitlements as the common commercial access
foundation while subscriptions, billing, metering/rating, signed licensing, and
jurisdiction providers remain independently optional.

## Program dependencies

Existing ERP/subscriber-management adoption follows the companion
`2026-07-18-existing-product-adoption.md` plan. It is an incremental assembly/adapter
track, not a prerequisite rewrite and not permission to share product databases.

Complete first:

1. `2026-07-18-control-plane-security.md` — real platform identity, atomic tenant
   provisioning, RLS-active development, one transaction authority, security baseline.
2. Module-control-plane steps 2-3 — `ModuleManifest`/`ModuleRegistry` and canonical
   permissions/audit actions. This plan's Workstream 2 is the single implementation of
   module-control-plane step 4 (tenant entitlements + `require_capability`); do not build
   the earlier `tenant_module_entitlements` sketch separately. Typed flags (step 5) may
   proceed in parallel but must complete before the shared admin surfaces.
3. Capability hardening's list-query, impact-preview, and WCAG work where the new admin
   surfaces consume those contracts.

## Parallel delivery lanes and integration gates

The platform foundation and vendor control plane are separate release units and may be
developed concurrently. Parallel means consumer-driven contract feedback against versioned
pre-releases; it does not mean copying provisional starter code or implementing duplicate
identity, permission, tenant, entitlement, audit, settings, lifecycle, outbox/job,
observability, or error frameworks in the control-plane product.

### Lane A — `dotmac_starter_mt` platform foundation

Deliver in this order:

1. merge the accepted architecture/docs and complete control-plane security/RLS-active
   development before exposing any commercial or fleet administrator surface;
2. create an explicit publishable kernel boundary, compatibility/version policy,
   `ProductAssemblySpec`, assembly bootstrap, module registry, and a reference empty/thin
   assembly proving the starter is consumable rather than copied;
3. publish canonical permission/audit, entitlements/capability, typed flags/settings,
   Money/locale, lifecycle command/history, outbox/inbox/job, provider, health/telemetry,
   support-access, API/error, and migration contracts in dependency order;
4. provide fake/in-memory providers and contract-test kits so product teams can develop
   without cloud, payment, DNS, licence-signing, or telemetry credentials; and
5. publish signed pre-release/stable packages and base images with lock metadata,
   changelog, compatibility/migration declaration, SBOM/provenance, consumer update PRs,
   and reference profile tests.

The starter does not own commercial accounts, vendor invoices, fleet deployments, support
cases, or ISP domain models. It owns only reusable contracts and optional generic modules
whose product neutrality is proven.

### Lane B — vendor control-plane product assembly

After the first assembly/kernel pre-release exists, create a separate maintained product
repository or assembly package that pins it. Deliver vertically:

1. scaffold the product assembly, platform-admin surface, product branding, compatibility
   test, and local development profile without inventing replacement core primitives;
2. add vendor `CommercialAccount`, contacts/legal entities, catalog/offers/contracts,
   deployment inventory, support plan, and audit-facing read models using manual/fake
   providers first;
3. implement an administrator-driven flow that records an approved order and a pending
   deployment, using fake provisioning and manual invoice/payment confirmation;
4. add licence issuance/key-custody integration and entitlement projection after the
   kernel verifier/evaluator contracts publish;
5. add durable provisioning, DNS/TLS, telemetry heartbeat, support/incident, maintenance-
   wave, invoice/payment, and reconciliation modules one provider at a time; and
6. enable public signup, automatic payment, trials, self-service domains, and automated
   production activation only after the internal golden path and failure/compensation
   scenarios pass.

The control plane owns commercial/fleet state and infrastructure orchestration credentials;
it does not import ISP/ERP ORM models or query their databases. It projects signed/local
entitlements and integrates through deployment callbacks, versioned APIs/events, and
external IDs.

### Contract cadence and gates

Use tagged pre-releases (for example an `a`/`rc` package), not a mutable Git branch/path or
copied source, for cross-repository integration. Each contract change carries tests,
compatibility classification, migration notes, and a control-plane consumer PR. Promote a
contract to stable only after both its kernel tests and at least one assembly consumer test
pass.

The shared gates are:

1. **Foundation gate:** platform admin identity, RLS/session, transaction, audit, and
   package boundary are secure and the empty assembly boots.
2. **Manual commercial gate:** a platform administrator creates an account, contract,
   support plan, entitlements, and pending deployment entirely with fake/manual providers.
3. **Licence gate:** the control plane signs a deployment-bound licence; a reference data
   plane verifies it and produces explainable local capability decisions.
4. **Provisioning simulation gate:** forced failures at every step resume without duplicate
   infrastructure, premature activation, or billing start.
5. **Sandbox ISP gate:** one real reference provider provisions, deploys, migrates, creates
   the single ISP tenant/owner, configures DNS/TLS, verifies backup/health, and activates.
6. **Pilot gate:** a non-production/internal ISP completes upgrade, rollback, support
   access, incident, export, suspension/recovery, licence renewal, and termination drills.
7. **Production gate:** security/privacy/restore reviews, runbooks/on-call/SLOs, support and
   EOL policy, commercial reconciliation, and customer acceptance are complete before the
   first external ISP is onboarded automatically.

Lane A may stay one or two contract increments ahead of Lane B. Lane B must never depend on
unreleased internals, and Lane A must not declare a contract generally reusable until the
consumer reveals that it is usable without product-specific imports or assumptions.

## Authority and dependency rules

| Concern | Authority | Must not become |
|---|---|---|
| Installed capability | `ModuleManifest` registry | DB row claiming missing code exists |
| Tenant commercial access | Entitlement evaluator | feature-local plan-name checks |
| Actor authorization | Permission evaluator | feature flag or subscription check |
| Rollout | Typed feature-flag evaluator | security boundary |
| Runtime behavior | `SettingSpec` resolver | second plugin config store |
| Tenant lifecycle | Tenant transition service + history | billing webhook or overloaded status |
| Cross-module delivery | Transactional outbox + idempotent inbox | untracked best-effort callback |
| Payment settlement | Billing provider snapshot | synchronous request-time dependency |
| Usage | Immutable meter events + rebuildable aggregate | mutable counter with no provenance |
| Usage price | Rating result against immutable price version | meter aggregate doing arithmetic ad hoc |
| Money/FX | Exact money value + immutable FX snapshot | float or current-rate recomputation |
| Tax/jurisdiction | Versioned jurisdiction policy/tax result | country `if` statements in features |
| Translation | Stable message ID + versioned locale catalog | translated label as stored/API code |
| Offline license | Signed license verifier | private signing key inside deployments |

The request-time capability path is:

```text
installed + deployment-enabled + migrated + dependencies + healthy
  + tenant entitlement + actor permission + applicable quota
  = effective capability decision
```

Every decision returns `allowed`, `reason`, `source`, and a version useful for support and
cache invalidation.

## Workstream 1 — Deployment profile registry

Produce:

- a publishable/versioned platform-kernel boundary containing only cross-project
  invariants, with optional first-party modules remaining outside that kernel;
- `ProductAssemblySpec` for product-owned modules, pinned kernel/module versions,
  providers, brand, policy packs, supported profiles, and compatibility ranges;
- `DeploymentProfileSpec` declarations for profile defaults and allowed providers;
- `DeploymentProfileRegistry` with uniqueness/dependency validation;
- provider protocols for commercial authority, provisioning, identity, secrets,
  telemetry, update policy, storage, ingress, DNS, and TLS;
- startup validation with a human-readable effective-profile report;
- a ban on feature-level deployment-mode branching, enforced by architecture tests.

The default reuse model is assembly-based, not copy-and-edit. DotMac-owned products may
share the workspace/monorepo kernel directly; independently released/on-prem/OEM products
pin signed package/base-image releases. Product overrides use declared providers,
manifests, events, templates, and policy extension points—never copied or monkey-patched
kernel files.

Profiles compose independent axes (topology, operator, connectivity, commercial
authority, identity, branding, domain/ingress, update, telemetry). A convenient profile name must not
erase those underlying decisions.

## Workstream 2 — Entitlements core

Create an `entitlements` module that owns:

- capability definitions sourced from module manifests;
- tenant grants with source (`manual`, `contract`, `subscription`, `license`, `trial`),
  explicit allow/deny, validity dates, limit values, priority, reason, and optimistic
  version;
- `EntitlementDecision` and `QuotaDecision` result types;
- `require_entitlement`/`require_capability` integration;
- support-facing explanation/history;
- cache versioning and invalidation;
- audit actions and impact preview for grant/revoke/change.

No feature may check `tenant.plan`, provider customer state, or raw license claims.

Representative tables:

```text
tenant_entitlement_grants
entitlement_change_history
```

Manifest capability declarations remain code authority; rows grant declared capability
codes and may never invent new ones.

This store replaces the directive's provisional `tenant_module_entitlements` table.
Module availability is represented by a manifest-declared capability such as
`inventory.use`, so module and finer capability grants cannot drift into two authorities.

The evaluator implements ADR-0003's deterministic conflict rules: active deny/revocation,
source precedence, emergency override scope/expiry, validity and grace, declared limit
combine strategy, winning rule, effective limit, and versioned explanation. Tests cover
conflicting sources, expiry boundaries, revocation, cache invalidation, and each limit
combination strategy.

## Workstream 3 — Tenant lifecycle orchestration

Create related, independently authoritative state machines for tenant operations,
subscription/commercial state, provisioning/provider jobs, domains, and licenses. Do not
add one overloaded status that mixes payment, health, access, and data-retention concerns.

Produce:

- versioned transition policies and legal-transition tables;
- idempotent commands carrying actor, tenant, correlation, causation, and idempotency IDs;
- a shared transactional outbox and idempotent consumer inbox contract;
- service-owned audit emission and lifecycle history;
- retryable job state with observed provider state, error classification, backoff, and
  operator repair/reconcile actions;
- explicit compensation for partial external work; and
- separate restriction, read-only, suspension, termination, retention/legal-hold, and
  purge policies.

The onboarding orchestrator supports manual-contract, self-service paid/trial, on-prem
license, and OEM-delegated flows. It coordinates tenant/owner bootstrap, commercial
approval, entitlements, initial modules/resources, domain requests, notifications, and
onboarding progress without placing provider calls inside the tenant database transaction.

The offboarding orchestrator coordinates effective cancellation date, final
invoice/refund, entitlement restriction, export/read-only window, session/API-key/webhook
and integration revocation, domain/certificate removal, module/provider cleanup,
retention/legal hold, backup expiry, and idempotent purge. Payment failure or module
disablement can never implicitly delete tenant data.

## Workstream 4 — Internationalization and global commercial primitives

Add common primitives before catalog/billing implementations:

- stable message IDs, versioned locale catalogs, locale fallback, pluralization, RTL, and
  user -> tenant -> deployment locale resolution;
- shared web/email/PDF/notification formatting using locale plus the existing independent
  timezone/date-format settings;
- ISO 4217 currency codes and exact `Money`/minor-unit or decimal-scale rules with no
  binary floats;
- distinct transaction/invoice, tenant functional, and provider settlement currencies;
- immutable FX snapshots containing provider, rate type/value, effective time,
  source/target, and rounding policy;
- versioned jurisdiction policies for legal entity/merchant, tax registrations, customer
  location evidence, tax treatment, exemptions/reverse charge, invoice requirements,
  fiscal rules, retention/privacy/consent, and data residency; and
- provider protocols for FX and tax with reconciliation and replayable internal snapshots.

Canonical API values, timestamps, codes, audit actions, and stored facts remain
language-neutral. Locale, timezone, currency, jurisdiction, legal entity, and data
residency are never inferred from each other.

## Workstream 5 — Product catalog, pricing, and subscriptions (optional)

Create a `subscriptions` module only for deployments that sell time-bound plans. It owns:

- products, offers, plans, immutable plan/price versions, supported currencies, and
  jurisdiction availability;
- one-time, recurring, tiered, volume, and usage price declarations as needed;
- plan-to-entitlement/limit mappings;
- tenant subscription lifecycle;
- trials, renewal, grace, scheduled upgrade/downgrade, cancellation, proration policy,
  grandfathering, and effective dates; and
- projection of current subscription terms into entitlement grants.

Editing a marketed plan or price creates a new immutable version. Existing tenants retain
their contracted version until an explicit migration/change.

Subscriptions do not collect money. Invoiced enterprise SaaS may use this module with
manual/ERP billing and no payment-provider integration.

## Workstream 6 — Usage metering, quotas, and rating (optional)

Create `metering` where quantitative limits or usage pricing exist:

```python
record_usage(
    tenant_id=tenant.id,
    meter="storage.bytes",
    quantity=uploaded_size,
    idempotency_key=upload_id,
)
```

Requirements:

- manifest-declared meter codes and units;
- immutable, idempotent usage events;
- rebuildable period aggregates;
- atomic quota reservation/consumption for hard limits;
- late-event, correction, reversal, and closed-period policy;
- retention/export policy;
- separate operational quota and billable-usage decisions.

Where usage is priced, a rating service applies the tenant's immutable price version to a
closed usage period and creates replayable charge lines. It owns tier/volume evaluation,
minimums/commitments/overages, currency/rounding, late corrections, credits, and rating
version provenance. Metering measures; rating prices; invoicing bills; settlement records
money movement.

Metering may feed entitlements, billing, both, or neither.

## Workstream 7 — Billing, invoicing, payments, and collections (optional)

Create a provider-neutral `billing` module only where the product owns or embeds a money
workflow:

- provider customer/payment/invoice references;
- invoice lifecycle (draft/open/paid/void/uncollectible), immutable lines, numbering,
  taxes, discounts, credits, refunds, and final invoices;
- idempotent signed webhook inbox with deduplication, retry, replay, and ordering policy;
- normalized billing/subscription events and provider reconciliation jobs;
- payment attempts, failed-payment retries, dunning, chargeback, grace, recovery, and
  provider-outage policy;
- transactional outbox for provider-side mutations; and
- explicit adapters for provider-owned invoicing, internal invoicing, or manual/ERP
  invoicing, naming the authority in each deployment profile.

The billing/payment provider is authority for settlement. The internal invoice and
subscription snapshots are application authorities. A versioned commercial policy maps
billing outcomes to subscription state and then entitlement restrictions; request
handlers never call the provider to decide access.

## Workstream 8 — Signed licensing (optional)

Create `licensing` for commercial self-hosted/OEM distribution:

- asymmetric signed license documents;
- issuer, subject, product/edition, capabilities, limits, validity, grace, and optional
  deployment constraints;
- public verification keys in deployments; private signing keys only in the vendor/OEM
  licensing authority;
- issuer/key IDs, verification-key rotation, deployment/cluster binding where contracted,
  and explicit HA/node-count semantics;
- offline verification and explicit clock/rollback/expiry/grace behavior;
- renewal and revocation-list import suitable for connected and air-gapped deployments;
- projection of verified claims into entitlement grants;
- no mandatory phone-home unless the product contract explicitly requires it.

Perpetual on-prem may use license + entitlements without subscriptions or billing. Annual
on-prem may use an expiring license while subscription/billing live only in the vendor
control plane.

## Workstream 9 — OEM delegation

Add, only when an OEM profile is selected:

- partner/OEM identity and administration;
- delegated license or entitlement issuance with bounded capabilities;
- signed brand/module packs;
- partner-to-customer provisioning policy;
- compatibility range and upgrade-channel constraints;
- audit chain identifying vendor, partner, tenant, and actor authorities.

An OEM cannot grant capabilities outside its delegated ceiling.

## Workstream 10 — Tenant domains, DNS, TLS, and ingress

Evolve the existing `TenantDomain` read model into a safe lifecycle without moving host
resolution out of core:

- split exact platform host, tenant base domain(s), and custom-domain target in the
  deployment profile; retain backward-compatible mapping from `PLATFORM_ROOT_DOMAIN`
  during migration only;
- define `IngressProvider`, `DnsVerificationProvider`, and `TlsProvider` protocols, with
  Nginx/controller, Caddy/Traefik, Kubernetes cert-manager, managed-load-balancer, and
  manual/customer-PKI implementations selected by profile;
- add normalized domain desired state, random TXT ownership challenges, primary/canonical
  domain policy, certificate status/expiry, observed provider state, last error, retry,
  and optimistic version;
- add an idempotent reconciler and repair action rather than making admin requests wait on
  DNS propagation or ACME;
- activate routing only after ownership and TLS gates pass; never authorize on CNAME
  presence alone and never issue on first request for an arbitrary host;
- enforce global uniqueness, IDNA/lower-case normalization, reserved-name checks,
  takeover cooldown, issuance/rate limits, renewal, suspension, and safe removal;
- define proxy trust explicitly: preserve validated host, replace untrusted forwarding
  headers, set forwarded scheme, restrict trusted proxy peers, and fail unknown hosts
  closed; and
- support local DNS and customer PKI/manual certificates in on-prem/air-gapped profiles.

The database is desired routing authority, DNS/CA/ingress are contracted external
authorities, and observed state is a rebuildable projection with drift detection. A
custom-domain entitlement may bound count or availability, but billing and subscriptions
remain unrelated unless the selected product commercially prices that capability.

Tenant self-service uses a tenant-authorized `DomainChangeRequest` plus transactional
outbox. A platform-authorized reconciler performs all `TenantDomain`, DNS, certificate,
and ingress writes; `app_user` never receives platform-table mutation grants. The request
and audit history retain tenant actor, platform worker, correlation, desired state,
observed state, and compensation/retry outcome.

## Workstream 11 — Packaging, resilience, and offline operation

Add profile-owned deployment assets under `deploy/`:

- reusable OpenTofu modules for compute/project or account, network/firewall, managed
  PostgreSQL, object storage, DNS, secret references, monitoring, and backup policy;
- encrypted and locked remote IaC state with a separate state boundary and concurrency
  lock per deployment; never parse or mutate state files from application code;
- repeatable cloud-init/Ansible host bootstrap and a preflight contract for supported OS,
  CPU/RAM/disk, ports, time sync, registry access, and backup destinations;
- dedicated-ISP Compose assets that consume a pinned signed image and preserve the current
  separate backup -> migration -> rollout -> health-gate -> rollback sequence;
- SaaS/large-fleet Helm and GitOps configuration behind the same deployment-provider
  contract; Kubernetes remains optional rather than a prerequisite for dedicated tenants;
- isolation policies: separate database and secret boundary per ISP, with account/project
  or cluster isolation when contracted; a Kubernetes namespace alone is never described as
  the same security boundary as a dedicated account/project or cluster;
- on-prem install, preflight, backup, restore, upgrade, rollback, and offline bundle;
- optional on-prem Kubernetes/K3s packaging only where HA or customer standards justify
  its operational cost;
- OEM packaging/branding/delegation configuration; and
- region/data-residency placement, tenant move/restore procedures, recovery objectives,
  backup encryption/retention, legal hold, and proof that purged tenant data expires from
  backups according to policy.

Add a `ProvisioningProvider` and durable deployment workflow owned by the platform control
plane. The signup HTTP transaction only validates and records intent plus an outbox command.
A network-restricted worker owns infrastructure credentials and advances independently
retryable steps:

```text
commercial account/order
  -> pending Deployment + ProvisioningRequest
  -> infrastructure plan and policy approval
  -> apply infrastructure
  -> bootstrap runtime/secrets
  -> deploy pinned artifact and migrate
  -> create tenant/owner invitation
  -> project entitlements and license
  -> reconcile DNS/TLS
  -> health/security/backup-restore checks
  -> activate and emit the contracted billing-start event
```

Model `Deployment`, `DeploymentArtifact`, `ProvisioningRequest`, `ProvisioningStep`,
`InfrastructureResourceRef`, and immutable evidence/history without storing raw provider
credentials. Every request carries idempotency/correlation IDs, serializes concurrent work
per deployment, records desired versus observed state, and supports resume, retry, repair,
and explicit compensation. Activation, destructive cleanup, billable resource creation,
and high-risk production changes use policy-based approval gates. A failed workflow never
creates a second deployment when replayed and never starts recurring billing before the
contracted activation condition.

Begin with the dedicated VM + managed database + Compose provider because it matches the
repository's proven deployment primitive and gives each ISP a clear failure/data boundary.
Add a managed-Kubernetes/Helm/GitOps provider when fleet scale, multi-service scheduling,
high availability, or an operating team's existing platform makes its additional control
plane worthwhile. Both providers implement the same lifecycle and evidence contract, so
choosing Kubernetes later does not change product code or commercial behavior.

Day-two reconciliation covers image and configuration drift, certificate/license renewal,
backup verification, capacity, monitoring-agent health, upgrade waves, suspension,
disaster recovery, export, retention, and termination. Artifact CI signs images/bundles and
publishes SBOM/provenance; it does not receive unrestricted fleet credentials. Provisioning
workers obtain short-lived, least-privilege credentials from the configured secret/identity
provider, and production plans/evidence are retained for audit.

Define and test an on-prem distribution/IP threat model. Never promise source secrecy when
the customer controls root, the container runtime, hypervisor, or physical host. Produce:

- separate `vendor_managed_dedicated`, `customer_controlled_onprem`, optional
  `attested_appliance`, and intentionally source-available/escrow assurance policies;
- a multi-stage runtime image containing no `.git`, build context, repository credentials,
  test/planning material, source maps, package caches, or build secrets; prove sensitive
  inputs did not survive in earlier layers rather than deleting them in a later layer;
- non-root/read-only/minimal runtime, no default debug shell or host socket, digest pinning,
  signature/provenance/SBOM verification, vulnerability policy, and customer/deployment
  artifact binding;
- public verification keys only in customer deployments; artifact/license private keys and
  fleet/provider/support credentials remain in separated vendor signing/control systems;
- an explicit classification of shipped Python source, bytecode, compiled native modules,
  vendor-hosted sensitive services, and optional confidential-computing components. Native
  compilation/obfuscation is defence-in-depth and leak deterrence, never a confidentiality
  guarantee against a privileged operator;
- tamper-evident audit and optional non-secret signed customer fingerprinting without hidden
  backdoors or collection outside the telemetry contract; and
- counsel-reviewed MSA/EULA, redistribution/deployment rights, reverse-engineering language
  where enforceable, update/support consequences, escrow/OEM terms, and jurisdiction-
  specific privacy/security obligations.

An `attested_appliance` remains a separate later provider: require documented hardware root
of trust, secure/measured boot, attestation verifier and key-release policy, rollback and
recovery ceremonies, supported hardware matrix, performance/availability tests, and a
published residual-risk statement. Do not label ordinary Compose, Kubernetes, encrypted
disk, Python bytecode, or a signed image as confidential computing.

Add an end-to-end fleet observability and maintenance contract:

- instrument shared HTTP/database/job/outbox/provider paths with correlated OpenTelemetry
  logs, metrics, and traces; retain current request IDs and add trace/span correlation;
- deploy a local collector/agent with outbound-only mTLS, retry/batching, bounded encrypted
  buffering, sampling, allowlisted attributes, redaction/filtering, and a profile-specific
  exporter (`central`, `health_only`, `customer_backend`, `local_only`, or `disabled`);
- authenticate the deployment at the telemetry gateway and attach canonical deployment,
  product, version, environment, region, and tenant scope server-side. Do not trust a raw
  client tenant header and do not put subscriber IDs, credentials, tokens, payloads,
  unbounded URLs/query text, or other high-cardinality/PII values in metric labels;
- add `/health` liveness, protected/internal readiness, authenticated diagnostic status,
  and external black-box tenant-domain/service-journey probes with distinct disclosure and
  availability contracts;
- maintain `DeploymentHeartbeat`, `DeploymentHealthSnapshot`, `SLODefinition`,
  `MaintenanceWindow`, `ReleaseChannel`, `RolloutWave`, `MaintenanceRun`, and immutable
  deployment/version/backup/restore/drift evidence in the control plane;
- build tenant/deployment-isolated metrics, logs, traces, dashboards, retention, export,
  and deletion. Cross-fleet aggregate health is separate from access to raw tenant data;
- route actionable symptom/SLO-burn, backup age/failure, certificate/licence expiry,
  capacity, security exposure, job lag, deployment drift, and telemetry-deadman alerts
  through deduplication, inhibition, maintenance silences, severity/on-call policy, and
  runbook/support-case links;
- implement release rings (internal -> canary -> early adopter -> general), customer
  maintenance/approval policies, automatic wave halt, preflight/backup/migrate/readiness/
  synthetic/observation gates, and rollback evidence; and
- declare common kernel SLIs plus assembly-owned ERP/ISP signals. The ISP assembly owns
  subscriber-service, RADIUS/authentication, provisioning/network-provider, usage,
  invoice/collection, and domain-specific synthetic definitions.

Implement support as a stateful workflow and commercial offering, not an administrator
backdoor. Add `SupportPlan`, `SupportCase`, `SupportSLAClock`, `DiagnosticBundleRequest`,
`SupportAccessGrant`, `SupportSession`, `Incident`, `IncidentUpdate`, `RunbookRef`, and
post-incident action records. Support-plan entitlements control channels, hours, response/
resolution objectives, support seats, managed maintenance, and escalation; they never
override tenant/actor authorization or telemetry consent.

Support access requires a case/incident, purpose, requested scope, tenant consent or a
policy-approved emergency path, start/expiry, least privilege, separate application-
impersonation versus infrastructure grants, outbound customer-controlled channel for
on-prem, full session/action/command audit, visible active-session indicator, immediate
revocation, and customer-facing closure summary. Forbid shared passwords, permanent vendor
SSH keys, hidden accounts, inbound-by-default tunnels, and silent global impersonation.

Diagnostic bundles are locally generated from a versioned allowlist, redacted before
export, previewed/approved by the customer, signed, encrypted to the intended recipient,
time-limited, and deletion-audited. They include only the selected time window and necessary
health/version/config-fingerprint/correlation/provider-job evidence; secrets and business
records are excluded by default. Air-gapped support exchanges signed bundles and response
artifacts through an explicit offline ceremony.

Define incident states and evidence for detection -> acknowledgement -> ownership ->
containment -> communication -> recovery verification -> resolution -> review/corrective
work. Publish customer-notification/status-page and security-incident policies, SLOs,
RTO/RPO, backup restore-test cadence, support/EOL matrix, patch objectives, escalation tree,
and runbook ownership before selling an SLA.

Air-gapped tests deny network access and prove that runtime assets, migrations, license
verification, documentation, and upgrade artifacts are local. Telemetry must be optional
or disabled in that profile.

## Workstream 12 — Admin and support surfaces

Platform administration:

- effective deployment profile/provider status;
- fleet inventory and heartbeat freshness, product/artifact/config/schema/module versions,
  SLO/error-budget and capacity summary, active alerts/incidents, telemetry mode, backup/
  restore evidence, drift, maintenance window/channel/wave, update/EOL/security exposure,
  and per-deployment runbooks;
- tenant lifecycle state, outstanding jobs, correlation history, retry/repair,
  restrict/suspend/reactivate/terminate/export/hold/purge actions with policy gates;
- installed modules/plugins and dependency/health/migration state;
- tenant entitlements;
- product/plan/subscription state where installed;
- license inventory where installed;
- locale catalogs, supported currencies, FX/tax/jurisdiction/provider status, legal
  entities, data-residency placement, feature flags, integrations, audit, and approvals;
- domain inventory and lifecycle: tenant, normalized host, ownership challenge/status,
  ingress/TLS state, certificate expiry, primary/canonical choice, drift, retry/repair,
  suspend, and remove with impact preview;
- support access using explicit consent or time-bounded break-glass grants, reason,
  notification, session recording/audit, and revocation—never an invisible global
  impersonation bypass;
- support cases/SLA clocks, diagnostic-bundle request/preview/expiry, access requests and
  active sessions, incident timeline/customer communication, maintenance approval/wave
  progress, and customer-visible closure/post-incident summaries.

Tenant administration:

- effective modules and entitlement explanations (usually read-only);
- onboarding progress, lifecycle notices, cancellation/export/retention status;
- settings grouped by owning module;
- user/tenant locale and formatting preferences, permitted currencies, billing/legal
  profile, tax identifiers/exemptions, invoice addresses, and data-residency visibility;
- permitted tenant flags;
- current usage/limits;
- contracted support plan/SLA and channels, case history, deployment health appropriate for
  the tenant, telemetry consent/export mode and retention, maintenance window/update
  approval, diagnostic-bundle preview/approval, active support grants/sessions with a kill
  switch, incidents/status updates, and support data export/deletion requests;
- subscription/invoice/payment UI only when those modules are installed;
- custom-domain add/verify/status/primary/remove UI only when permitted by the profile and
  entitlement; raw provider credentials remain platform/operator-only.

Dangerous changes require impact preview, confirmation, audit, optimistic concurrency,
and configurable two-person approval.

## Workstream 13 — Generator, profile matrix, and lifecycle scenarios

The starter bootstrap/generator selects a profile and providers, produces configuration,
removes template-only planning history from the derived app, and emits a profile ADR.

Generated independent products are thin assemblies that pin kernel/module versions rather
than vendoring their source. Release automation publishes signed packages/base images,
SBOM/provenance, migration/compatibility metadata, changelog, and offline bundles; an
update bot opens product PRs, never mutates running deployments. Each PR runs that
product's supported profile and lifecycle matrix before staged or customer-approved
rollout.

CI generates and tests:

- `saas-multitenant`;
- `dedicated-single-tenant`;
- `onprem-online`;
- `onprem-airgapped`;
- `oem-multitenant`; and
- `api-only`.

At least one matrix product enables the built-in Jinja/HTMX `web` module and another uses
the same versioned APIs through an external-frontend contract test. Both must exercise the
same service-layer authorization and lifecycle behavior. OpenAPI snapshots, compatibility
diffs, generated-client smoke tests, capability/bootstrap responses, OAuth/OIDC/bearer
configuration, CORS/origin policy, and same-origin cookie/CSRF behavior are gated per
surface.

For each: validate config; assert expected/forbidden modules and routes; migrate from an
empty database and the prior release; boot; exercise entitlement decisions; prove tenant
isolation; prove bootstrap/admin access; and validate backup/upgrade hooks where relevant.
Profile tests also send unknown/unverified/verified hosts through the real proxy contract,
exercise DNS/TLS provider fakes and reconciliation, and prove that the air-gapped profile
does not attempt public DNS or ACME access.

An orthogonal scenario matrix runs across the relevant profiles:

- free/manual-contract request -> atomic tenant/owner bootstrap -> entitlements -> active;
- self-service trial -> paid conversion and failed provisioning compensation;
- renewal failure -> grace -> restriction/suspension -> successful recovery;
- upgrade/downgrade/cancellation with effective dates, proration, and grandfathering;
- meter -> quota -> rating -> multi-currency invoice -> payment and late correction/credit;
- jurisdiction/tax-policy version change without rewriting historical invoices;
- perpetual license and annual license expiry/grace/renewal under clock and key rotation;
- OEM delegated provisioning that cannot exceed the partner ceiling;
- domain verification failure/retry/removal/takeover and tenant-to-platform command audit;
- support break-glass grant/expiry/revocation; and
- cancellation -> final settlement -> export/read-only -> legal hold/retention -> purge,
  including provider cleanup and backup-expiry evidence.

## Governance gates

CI fails when:

- a profile references an unknown module/provider;
- a product copies/patches kernel files instead of using a declared extension point, or
  resolves an unpinned/incompatible kernel/module version;
- feature code branches directly on a profile/deployment-mode string;
- a capability/entitlement/license/meter code is undeclared or orphaned;
- a second module-entitlement table/evaluator or feature-local grant precedence appears;
- a feature checks a plan name or billing-provider state;
- a request-time entitlement path performs provider network I/O;
- a lifecycle transition bypasses the transition service, lacks idempotency/audit, or an
  external mutation has no outbox/inbox and reconciliation path;
- payment failure, cancellation, or module disablement directly deletes tenant data;
- a single-tenant profile disables tenant context/RLS;
- an air-gapped profile attempts external network access;
- a domain can activate without ownership proof and TLS readiness, an unknown host reaches
  tenant/platform routes, or forwarded headers are accepted from an untrusted peer;
- a billing webhook lacks signature/dedupe/idempotency coverage;
- money uses binary float, an FX conversion lacks an immutable rate snapshot, or an
  invoice/rating line references a mutable price/tax-policy version;
- locale, currency, timezone, legal jurisdiction, or data residency is implicitly derived
  from another instead of selected by declared policy;
- translated labels become stored/API authority instead of stable codes/message IDs;
- web/API adapters implement business transitions outside the shared service layer, an
  API contract changes without a versioned OpenAPI decision, or an enabled surface lacks
  its authentication/origin/CSRF contract tests;
- an installed plugin is enabled before migration/dependency/health validation;
- disabling/uninstalling a module can implicitly delete data;
- a profile's effective module/provider set changes without an explicit versioned change.

Use AST/runtime/catalog inspection and sensitivity proofs, consistent with the adoption
review's governance-test standard.

## Delivery sequence

1. Deployment profile/provider contracts.
2. Entitlements core and effective-capability explanation.
3. Tenant lifecycle command/event/outbox/inbox and transition contracts.
4. Internationalization, Money/FX, jurisdiction, legal-entity, and residency primitives.
5. Profile packaging skeleton + CI matrix.
6. Domain/ingress provider contracts and safe reconciliation.
7. Product catalog/pricing/subscriptions, if a selling workflow needs them.
8. Metering/quota/rating, where limits or usage pricing require it.
9. Billing/invoicing/payment/collections adapter, where a money workflow is embedded.
10. Signed licensing for self-host/OEM.
11. OEM delegation.
12. Complete lifecycle-aware admin/support/self-service surfaces.
13. Lifecycle scenario automation and generator completion.

Kernel/module release packaging and automated product update PRs begin with Workstream 1
and become mandatory gates in Workstream 13; propagation is not considered solved until a
single test fix can be released and safely adopted by every maintained assembly without
manual source copying.

Do not implement optional commercial modules before a selected deployment profile has a
real consumer. Entitlements is the common foundation; billing, subscriptions, metering,
and licensing remain independently selectable.

## Completion criteria

- One application codebase produces every supported profile without feature-level mode
  branching.
- Maintained products consume one versioned kernel/module source, and a fix propagates by
  automated, tested, auditable release updates rather than manual copy/merge work.
- Single-tenant deployments retain tenant/RLS invariants.
- Entitlement decisions are explainable and independent of provider availability.
- Onboarding, activation, commercial changes, suspension/recovery, support access, and
  offboarding are idempotent, auditable, retryable, and repairable end to end.
- Hosted SaaS, manual-contract SaaS, perpetual on-prem, annual on-prem, and OEM commercial
  flows compose from separate modules without circular dependencies.
- Usage pricing traces from immutable meter event through rating, invoice, settlement,
  commercial policy, and entitlement outcome without losing provenance.
- Locale, timezone, currency, FX, jurisdiction, legal entity, tax, and data residency are
  independently selected, versioned, auditable, and tested across supported combinations.
- Air-gapped operation is tested, not merely documented.
- Tenant domains are ownership-verified, TLS-gated, drift-detectable, and operable through
  managed, customer-managed, and air-gapped ingress providers.
- Every profile and applicable lifecycle scenario passes generation, migration, boot,
  isolation, capability, lifecycle, commercial, domain, and packaging gates in CI.
- Built-in web, API-only, and external-frontend consumers exercise the same service-layer
  decisions while retaining surface-appropriate authentication and security controls.
