# Existing Product Adoption Plan — dotmac_erp and dotmac_sub

> **Status:** Accepted migration direction; discovery and implementation not started.
> ADR-0003 owns the platform/reuse decisions. This plan applies them to existing products
> without a rewrite or shared production database.
>
> **2026-08-02 execution split:** the shared decisions and prohibited approaches in this
> document remain in force. Current executable sequencing is now maintained separately in
> [`2026-08-02-dotmac-sub-kernel-improvements.md`](2026-08-02-dotmac-sub-kernel-improvements.md)
> and
> [`2026-08-02-dotmac-erp-kernel-improvements.md`](2026-08-02-dotmac-erp-kernel-improvements.md),
> based on the live product SOT maps and released kernel `0.1.0a7`. Where an older discovery
> statement here conflicts with those products' checked-in as-built documentation, the
> product documentation is authoritative.

## Recon basis

Reviewed fetched `origin/main` snapshots on 2026-07-18:

- `dotmac_erp` at `318a6e0d` — multi-tenant ERP using `organization_id` application
  scoping across roughly 240 model files, with mature finance, currency/tax, feature-flag,
  outbox, API, Jinja/HTMX, task, integration, and licensing behavior.
- `dotmac_sub` at `ec6ee30a` — subscriber-management/ISP platform with deep catalog,
  subscription, billing, collections, usage, customer-service state, provisioning,
  RADIUS/OLT/ACS/network operations, customer/admin/reseller web surfaces, and mobile/API
  consumers. Its subscribers are ISP customers, not platform tenants.

These products contain battle-tested behavior that may inform or implement optional
platform contracts. They are not code donors to copy wholesale into the kernel.

## Decision

Both products can adopt the platform, incrementally:

```text
dotmac_erp = platform kernel + ERP product assembly + ERP domain modules
dotmac_sub = platform kernel + dedicated-one-tenant assembly + ISP domain modules
vendor control plane = platform kernel + commercial/fleet product assembly
```

No big-bang rewrite, schema replacement, repository merge, or simultaneous UI/API cutover
is authorized by this decision. The first objective is contract compatibility and shared
release consumption; deep data-model convergence happens only when its risk and value are
proven independently.

This is not a proposal for a permanent `isp` Git branch of the starter or a new ISP
application. `dotmac_starter_mt` continues as the kernel/module/package source;
`dotmac_sub` remains the ISP product and adopts released contracts through a temporary
migration branch and incremental adapters. The vendor commercial/deployment control plane
is a third, thin product assembly: it consumes the kernel but does not turn fleet,
contract, vendor-invoice, or infrastructure credentials into universal kernel concerns.

Run two coordinated delivery tracks:

1. **Platform track:** finish control-plane security, publish package boundaries and
   `ProductAssemblySpec`, then module/entitlement and lifecycle/provisioning contracts.
2. **ISP adoption track:** immediately characterize current `dotmac_sub`, record model and
   authority mappings, declare its assembly, and add adapter seams. Replace identity,
   tenancy, entitlements, billing/lifecycle, or deployment behavior only after the matching
   platform contract is released and its parity tests pass.

The tracks can proceed concurrently at those seams; product code must not import an
unreleased starter branch or copy provisional kernel files. Each integration advances by a
versioned dependency update PR with product tests and an independently reversible cutover.

## Deployment topology and onboarding other ISPs

ERP and subscriber management are separate product assemblies and, by default, separate
deployments/data planes with separate databases, migrations, scaling, backups, failure
boundaries, and release cadence. They share signed kernel/module releases and contracts,
not a production database. They may share an external IdP/SSO, API gateway/portal shell,
vendor account reference, observability backend, and contracted events/APIs.

Other ISP operators can be onboarded in two safe stages:

### Stage A — dedicated deployment per ISP (recommended first)

Each ISP receives a dedicated `subscriber_management` deployment with exactly one
platform `Tenant`. The ISP's subscribers, organizations, resellers, subscriptions,
billing accounts, devices, and services remain product-domain records beneath that tenant.
This gives strong operational/data isolation while the legacy ISP schema is adapted to the
kernel. OEM branding, local domains, licensing, update channel, identity, and integrations
are profile/provider configuration.

### Stage B — shared multi-ISP SaaS (later explicit program)

One deployment may host multiple ISP tenants only after a complete tenant-safety program.
Every ISP-owned table and operation—subscriber/party, catalog, subscription, invoice,
payment, usage, RADIUS, NAS/OLT/ONT/ACS, IPAM, topology, tickets, files, tasks, caches,
webhooks, search, exports, and provider credentials—must carry or derive tenant context,
enforce composite tenant relationships and RLS/partition policy, and pass cross-ISP
isolation and noisy-neighbor tests. Platform/global catalogs must be explicitly classified.

The mapping is always:

```text
ISP operator/company = platform Tenant
ISP subscriber/customer = product Party/subscriber within that Tenant
```

Never model each ISP subscriber as a platform tenant. A hybrid fleet may run larger or
regulated ISPs as dedicated deployments while smaller ISPs share a multi-tenant SaaS
deployment; both consume the same product assembly and kernel versions.

A vendor control plane may link an account that owns ERP and ISP subscriptions, licenses,
and deployments, but each product projects entitlements into its own data plane. Cross-
product workflows use versioned APIs/events and explicit external IDs, never cross-database
joins or shared ORM models.

## Product mappings

| Platform concept | dotmac_erp | dotmac_sub |
|---|---|---|
| Deployment topology | Separate shared multi-tenant SaaS or dedicated ERP data plane | Dedicated one-tenant per ISP initially; shared multi-ISP only after the explicit tenant-safety program |
| Platform tenant | Existing ERP `Organization` maps to `Tenant` identity/context | The DotMac/operator deployment is one `Tenant` |
| Product customer/party | ERP customers, suppliers, employees, contacts as Party roles/projections | Subscribers, contacts, organizations, resellers as ISP Party roles/projections |
| Must not become tenant | Customer/supplier/employee rows | Every ISP subscriber/account/organization |
| Product modules | GL/AP/AR, inventory, assets, HR/payroll, expense, procurement, public sector, fleet | Catalog, subscriptions, billing, usage, collections, provisioning, RADIUS/OLT/ACS, topology, support, reseller/customer portals |
| Valuable contract sources | Organization scoping, finance Money/FX/tax, flags, outbox, approvals, licensing, admin UI | Subscription lifecycle, service enforcement, billing/dunning, usage, provisioning jobs, provider adapters, web/mobile contracts |
| Built-in web | Retain existing ERP Jinja/HTMX while converging service contracts | Retain admin/customer/reseller web; mobile/field apps remain API clients |

`Organization`/`Subscriber`/`Party` convergence requires an explicit identity and data
mapping ADR per product. Similar names are not sufficient evidence that records have the
same lifecycle or authority.

## Migration principles

1. **Characterize before changing.** Pin current API/OpenAPI, database, lifecycle,
   permission, audit, billing, and critical web behavior with contract tests.
2. **Adopt seams before schemas.** Product assemblies, module manifests, provider
   protocols, error/audit contracts, and release automation land before tenant-column or
   identity-table migrations.
3. **One writer at every cutover.** Prefer adapters and read projections. Any temporary
   dual-write requires an outbox, provenance, drift detector, repair, and a dated removal
   gate.
4. **Expand/contract only.** Add compatible columns/tables/projections, backfill and
   reconcile, switch the single writer, observe, then remove legacy structures in a later
   release.
5. **Kernel never imports products.** ERP/ISP code implements kernel protocols or emits
   contracted events; reusable behavior moves upward only after it is product-neutral and
   has at least two real consumers.
6. **Preserve surface choice.** Existing Jinja/HTMX pages and separate frontends continue
   while JSON/web adapters converge on shared services.
7. **No automatic fleet mutation.** Kernel fixes publish releases; product update PRs,
   migration/profile/lifecycle tests, and staged deployment remain mandatory.

## Phase 0 — Adoption inventory and ownership ledger

For each product, inventory and name the current authority for:

- tenant/organization/operator identity and request context;
- person/customer/subscriber/employee/supplier identity and credentials;
- roles, permissions, feature flags, settings, audit codes, and module availability;
- session/transaction ownership, tasks, outbox/inbox, webhooks, and provider jobs;
- product/subscription/billing/usage/Money/FX/tax/licensing lifecycles;
- files, notifications, search, observability, secrets, domains, and integrations;
- JSON/web/mobile contracts and authentication flows; and
- migrations, deployment, backups, restore, rollback, data retention, and purge.

Produce a collision ledger: `reuse`, `adapt`, `product-owned`, `migrate later`, or
`retire`. Every duplicate authority needs a cutover and removal gate before implementation.

## Phase 1 — Contract characterization and product assemblies

- Pin OpenAPI snapshots and representative generated-client tests.
- Pin golden lifecycle scenarios and database invariants for critical money/service paths.
- Declare `erp` and `subscriber_management` `ProductAssemblySpec`s with their current
  modules, providers, brand, surfaces, Python/database compatibility, and deployment
  profile.
- Build both unchanged applications through the platform release/assembly pipeline.
- Add a compatibility matrix proving a kernel release can be proposed to both products
  without source copying.

No production behavior or schema changes in this phase.

## Phase 2 — Adopt low-coupling kernel contracts

Adopt in small vertical slices:

- structured errors/request IDs/log/trace conventions;
- settings and typed flag declaration contracts through adapters;
- manifest-declared module, permission, audit, meter, and entity codes;
- shared lifecycle command metadata, outbox/inbox, idempotency, and job-state shapes;
- provider interfaces for notifications, files, secrets, telemetry, identity, FX/tax,
  billing, licensing, and provisioning; and
- version/build/health/readiness and release provenance contracts.

Existing implementations remain behind adapters until parity and failure-mode tests prove
replacement is safer than coexistence.

## Phase 3 — Identity, authorization, and capability bridge

### dotmac_erp

- Map ERP `Organization` to platform tenant context without immediately renaming 240+
  model references.
- Add a `TenantContext`/organization adapter so new kernel guards and existing explicit
  `organization_id` filters agree.
- Map current users/roles/permissions/flags to canonical declarations and entitlement
  decisions; shadow-evaluate old and new decisions and reconcile every mismatch.
- Decide Party projections for employees, customers, suppliers, and contacts per domain
  ownership—not through a one-table mass merge.

### dotmac_sub

- Provision one platform tenant for the operator deployment initially.
- Keep Subscriber, Subscription, BillingAccount, network service, reseller, and customer
  organization models product-owned.
- Map users/parties/roles without treating each subscriber as a platform tenant.
- Adapt the existing customer-service and billing-enforcement decisions to entitlement/
  lifecycle explanations, shadow-compare, then cut over one decision path at a time.

## Phase 4 — Tenancy enforcement and database convergence

ERP's application-enforced `organization_id` model and the kernel's PostgreSQL RLS model
are not assumed equivalent. Run a separate tenancy ADR and migration program:

1. catalog every organization-scoped, global, subtype, projection, and integration table;
2. add dynamic cross-organization isolation and composite-FK tests;
3. introduce a tenant mapping/context compatible with existing IDs where possible;
4. add RLS policies and least-privilege roles table-family by table-family;
5. shadow/test under real request roles, repair violations, then enforce/FORCE RLS; and
6. remove redundant application-only compatibility paths only after production evidence.

For `dotmac_sub`, one-tenant adoption retains `Tenant`/RLS invariants for new shared
kernel tables, but does not justify bulk-adding tenant columns to every ISP table without a
separate multi-tenant product decision.

The later shared multi-ISP program must add an inventory and migration wave for every
product table, queue/task payload, cache key, object-storage key, search index, metric/log
dimension, provider secret, network command, and integration callback. It must prove
cross-ISP isolation under HTTP, workers, scheduled jobs, retries, exports, support access,
and provider reconciliation—not only ORM queries.

## Phase 5 — Commercial and lifecycle convergence

- Use ERP Money/functional-currency/FX/tax behavior as a candidate implementation/source
  review for global commercial primitives, after extracting product-neutral contracts.
- Use subscriber-management catalog/subscription/billing/usage/dunning/service-state and
  provisioning behavior as candidate implementations/source reviews for lifecycle,
  rating, billing, and provider-job contracts.
- Do not make the kernel depend on ERP GL or ISP network models. Integrate via versioned
  events/adapters such as invoice-posted, payment-settled, entitlement-changed,
  service-activation-requested, and provisioning-result.
- Build reconciliation reports before changing a money or service-enforcement writer.
- Cut over one bounded lifecycle (for example plan change or payment recovery) end to end,
  prove parity/repair/rollback, then repeat.

## Phase 6 — API, web, and external frontend convergence

- Keep each product's current web routes/templates operational.
- Move route-only business logic into shared product services before changing surfaces.
- Converge JSON and web adapters on the same service/lifecycle commands.
- Version APIs and validate existing mobile/field/integration clients against pinned
  OpenAPI contracts.
- Adopt the platform capability/bootstrap response so built-in and separate frontends
  render only available modules/actions without plan-name branching.

No frontend rewrite is required to adopt the kernel.

## Phase 7 — Release propagation and staged rollout

For every kernel/module release:

1. publish signed package/base-image/offline-bundle artifacts with SBOM, provenance,
   compatibility, migrations, and security notes;
2. open automated update PRs for ERP and subscriber-management assemblies;
3. run product unit/integration/e2e, profile, lifecycle, migration, money, network, and
   generated-client tests as applicable;
4. deploy canary/shadow, reconcile decisions and projections, then stage rollout; and
5. retain product-specific rollback and data repair runbooks.

A fix is fleet-propagated only when every maintained compatible assembly has merged or
explicitly deferred the update with owner, reason, risk, and expiry.

## Prohibited approaches

- Rebuilding ERP or subscriber management from the starter in one branch.
- Copying the starter's core directory into either product and calling it shared.
- Making all ISP subscribers separate platform tenants.
- Combining ERP and ISP product databases or migrations merely because they share a
  kernel/customer account.
- Renaming `organization_id` to `tenant_id` without catalog, constraints, RLS, backfill,
  and isolation proof.
- Sharing one production database between products to simulate reuse.
- Letting the kernel import GL/payroll/RADIUS/OLT/subscriber models.
- Dual-writing identity, invoice, payment, subscription, or network state without an
  explicit temporary projection contract and repair path.
- Replacing existing web/mobile surfaces merely to claim API-first architecture.

## Completion criteria

- Both products are declared assemblies consuming pinned platform releases.
- A kernel security/tenancy bug is fixed once and reaches both through automated tested
  update PRs and controlled deployment.
- Product domain logic remains product-owned and import boundaries prevent leakage into
  the kernel.
- ERP organization isolation is at least as strong after every migration slice and reaches
  RLS only through a separately proven staged program.
- Subscriber/customer identity is not confused with deployment tenancy.
- Critical money, entitlement, billing, service-enforcement, and provisioning decisions
  have shadow/parity evidence, reconciliation, repair, and rollback before cutover.
- Existing JSON, Jinja/HTMX, mobile, field, and integration clients remain supported or
  migrate through explicit versioned contracts.
