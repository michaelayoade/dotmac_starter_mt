# S0 — dotmac_sub adoption preparation (design / tests-design only)

> **Status:** preparation, not execution. This document is checked-in intent produced in
> `dotmac_starter_mt` to make a *later* `dotmac_sub`-repo adoption lane fast and safe. It
> maps Sub's `subscriber_management` product onto the kernel's **proposed** `ProductAssemblySpec`
> so that when kernel Task 3 lands and the `0.1.0a1` alpha publishes, the Sub lane already knows
> which modules, providers, branding, surfaces, migrations, adapter seams, conformance runs, and
> characterization gates it must declare and pin.
>
> **This document does NOT:** define a local/copied `ProductAssemblySpec`; author any adapter,
> provider, or migration code; transfer any Sub transaction or source-of-truth boundary; or make
> any runtime change in either repository. The `ProductAssemblySpec` type is **kernel-owned**
> (kernel Task 3, `docs/superpowers/plans/2026-07-18-kernel-boundary.md`) and Sub will consume it
> from a *released, pinned* kernel version — never a copied type, local substitute, or
> product-specific fork.

## Inputs consumed (authoritative sources)

- `docs/superpowers/plans/2026-07-18-kernel-boundary.md` — Task 3 `ProductAssemblySpec` fields.
- `docs/superpowers/reviews/2026-07-18-kernel-surface-audit.md` — the audited kernel public
  surface, the re-planned Task 3 field set (`name`, `modules`, `settings_overrides`, `branding`,
  `providers`, `web_enabled`, `disabled_modules`, `assembly_template_dir`, `assembly_migrations`),
  the `ProvisioningProvider` seam (ruling C6), migration-authority split, and template/static
  override precedence.
- `docs/adr/0003-unified-deployment-profiles.md` — deployment-profile, provider-axis, commercial
  separation, and single-tenant-is-a-topology decisions.
- `docs/superpowers/plans/2026-07-18-existing-product-adoption.md` — the accepted (not started)
  `dotmac_sub` adoption direction: dedicated one-tenant assembly, characterize-before-changing,
  seams-before-schemas, one-writer cutovers.
- Cross-Dotmac standard `dotmac-platform-kernel-product-assemblies`: Sub is a thin assembly
  pinning versioned kernel releases; ISP operator = platform tenant; subscribers = product parties;
  separate data planes; dedicated-per-ISP before shared multi-ISP.

## Boundary invariants this preparation must never violate

1. **Sub stays authoritative** for operational subscriber, subscription, billing, collections,
   usage, service-enforcement, RBAC-assignment, provisioning-job, RADIUS/OLT/ONT/ACS, IPAM,
   topology, ticket, device, and network state. The kernel is a *transport/foundation*, never a
   parallel authority for any of these.
2. **Adapters wrap existing Sub owners.** Every seam named below is Sub implementing a kernel
   *protocol* or emitting a *contracted event* around its current owning service/transaction — it
   never moves the transaction boundary or the source-of-truth into the kernel.
3. **ERP and ISP remain separate data planes** — separate databases, migrations, backups, release
   cadence, failure domains. Kernel sharing implies shared *contracts*, never a shared schema or
   cross-database join.
4. **One tenant per Sub deployment initially** (Stage A, dedicated-per-ISP). The operator
   deployment is one platform `Tenant`; subscribers are product parties *inside* it. No ISP
   subscriber is ever modelled as a platform tenant.
5. **Consume, do not copy.** Sub pins a released kernel version and imports `dotmac_kernel.*`; it
   creates no local `ProductAssemblySpec`, provider protocol, or kernel type.

---

## 1. `subscriber_management` → proposed `ProductAssemblySpec` field mapping

The kernel Task 3 spec (per the surface audit's re-planned Task 3) is:

```
ProductAssemblySpec(
    name, modules, settings_overrides, branding, providers,
    web_enabled, disabled_modules, assembly_template_dir, assembly_migrations,
)
```

Sub's *eventual* assembly declaration (authored later, in the Sub repo, against the pinned kernel)
maps as follows. Every Sub-internal fact that this preparation cannot see from the kernel side is
tagged **INPUT-NEEDED** and must be resolved by reading the actual `dotmac_sub` repo in the later
lane.

### `name`
- Value: `"subscriber_management"` (the assembly code fixed by ADR-0003 and the existing-product-
  adoption plan). Stable, language-neutral, one per product.

### `modules: Sequence[FeatureManifest]`
- The kernel accepts today's `FeatureManifest` objects (`dotmac_kernel.features.FeatureManifest`).
  Sub declares its domain modules as manifests (JSON `routers`, `web_routers`, `nav`), the same
  shape the reference assembly's seven modules use.
- **Kernel-provided identity/tenancy modules Sub inherits, does NOT re-declare:** the kernel owns
  `Tenant`/`Party`/`Role`/`PartyRole`/`AuthSession`/`UserCredential`, platform auth, and the base
  RLS/roles migrations. Sub's assembly consumes these; it must not ship a second copy.
- **Candidate Sub domain modules** (to be declared as manifests) — from the existing-product-
  adoption product-mappings row for `dotmac_sub`: catalog, subscriptions, billing, collections,
  usage/metering, provisioning, RADIUS/OLT/ACS network operations, IPAM/topology, support/tickets,
  reseller portal, customer/self-care portal, and the financial/payment-routing owner.
- **INPUT-NEEDED (S-1):** the exact current module/package decomposition of `dotmac_sub` — Sub's
  code is organized as `app/services/**` typed-manifest writers and domain packages, not as
  `app/features/<name>` kernel manifests. The one-to-one (or one-to-many) mapping from Sub's
  ~229 registry services / 28 domains onto declared `FeatureManifest`s must be read from the repo.
  Knowledge indicates owners like `auth.subscriber_assignments`, `auth.rbac_catalog`,
  `auth.permission_gate`, `financial.payment_routing`, `sessions.radius_resolution`,
  `network.device_projection`, `auth.staff_provisioning` — but the full manifest boundary is a
  Sub-internal fact.
- **INPUT-NEEDED (S-2):** which Sub surfaces are true `FeatureManifest` modules vs. worker/CLI/
  mobile-API-only concerns (the latter are adapters over services, not manifest `web_routers`).

### `settings_overrides: Mapping[str, object]`
- Purpose: deployment-static overrides of kernel `Settings` fields and registered `SettingSpec`
  defaults for the dedicated-ISP deployment (DB URL family, `WEB_ENABLED`, CSP/security posture,
  forwarded-proxy trust, tenant base domain, display defaults, etc.), each an overridable knob per
  the "everything by config" rule.
- Sub-relevant overrides likely include: single-tenant topology defaults, ISP-operator branding
  source, telemetry policy (ADR-0003 axis), and any Sub `SettingSpec`s it registers via the kernel
  read/declare contract (`register_specs`/`SettingSpec`/`resolve_value`).
- **INPUT-NEEDED (S-3):** Sub's current configuration surface — env/settings names Sub reads today
  and which must be expressed as kernel `settings_overrides` vs. Sub-owned settings specs. Sub has
  its own settings/flags authority that must be reconciled against the kernel settings contract
  *through an adapter* (Phase 2 of the adoption plan), not replaced in S0.

### `branding: BrandSpec | None`
- The kernel owns `_DEFAULTS` + the loader/sanitizer; the *assembly* provides the branding file
  (`brand.json`, `BRAND_CONFIG_PATH`-overridable) and per-tenant DB `ui_branding` wins at runtime.
- Sub declares an ISP-operator `BrandSpec` (or `None` to accept kernel defaults + `brand.json`).
  For OEM/white-label ISPs this is the branding-authority axis from ADR-0003.
- **INPUT-NEEDED (S-4):** Sub's current branding/theming assets and whether the dedicated-ISP
  profile brands per-operator at assembly build time (`BrandSpec`) or per-tenant at runtime
  (`ui_branding` DB) — a Sub product decision.

### `providers: Mapping[str, object]` (interface-keyed)
- Empty in the reference assembly today; the seam workstream-5 fills it. The audit's ruling C6
  pulls **one** provider forward into the alpha: `ProvisioningProvider`
  (`dotmac_kernel.providers.provisioning`, protocol + `PlanResult`/`ApplyResult`/`ObserveResult`
  + error hierarchy). That is the only provider protocol Sub can bind against at alpha.
- Sub's provider needs (all *future*, gated on kernel publishing each seam — do **not** invent them
  locally): commercial authority (entitlements/licensing), notifications, files/object-storage,
  secrets, telemetry, identity (OIDC/SAML for some ISPs), FX/tax, billing/payment, DNS/TLS/ingress.
  See ADR-0003 provider-axis table.
- **S0 rule:** Sub binds only the `ProvisioningProvider` seam at alpha (if its dedicated-deployment
  slice needs it); every other provider stays a Sub-owned internal service behind an adapter until
  the matching kernel seam is *released*. Binding an unreleased seam is prohibited.
- **INPUT-NEEDED (S-5):** which Sub external integrations (payment gateways, RADIUS/OLT/ACS
  controllers, notification transports, DNS) will map to kernel provider protocols vs. remain
  Sub-owned adapters. These stay Sub-owned until a *product-neutral* kernel seam exists with two
  real consumers (adoption-plan migration principle 5).

### `web_enabled: bool`
- Whole-portal switch (`WEB_ENABLED`, default true). Sub keeps its admin/customer/reseller Jinja/
  HTMX portals; mobile/field/API clients are separate adapters. Sub's dedicated-ISP profile is
  `web_enabled=True`. An API-only Sub deployment would set it false without changing the JSON API.
- **INPUT-NEEDED (S-6):** whether Sub's customer and reseller portals are in-scope for the kernel
  `web`/`nav` manifest model at adoption time, or remain Sub-native surfaces initially (adoption
  plan Phase 6 keeps existing web surfaces operational — likely the latter for S0/early phases).

### `disabled_modules: Sequence[str]`
- Per-deployment opt-outs of declared modules (e.g. a dedicated ISP without reseller or without a
  particular billing path). Distinct from `web_enabled`.
- **INPUT-NEEDED (S-7):** which Sub modules are optional per deployment vs. always-on. Ties to
  ADR-0003 `required_modules`/`forbidden_modules` in the eventual `DeploymentProfile` (a *separate*,
  later contract from `ProductAssemblySpec`).

### `assembly_template_dir: str | Path`
- The assembly's own `templates/` directory. The kernel installs a Jinja `ChoiceLoader` of
  `[assembly_templates, kernel_package_templates]`, so a Sub template shadows the kernel's. Sub
  points this at its portal templates.
- **INPUT-NEEDED (S-8):** Sub's template tree layout and which kernel templates (base layout,
  auth, admin shell) Sub overrides vs. inherits.

### `assembly_migrations`
- The kernel ships base migrations `0001–0007` as package data with a stable starting revision; the
  assembly owns its **own** migration directory that `depends_on` the kernel head (multiple Alembic
  version locations), and composes `Base.metadata` from kernel base + assembly models. Sub points
  this at its own migration lineage.
- **Critical boundary:** Sub's existing schema is large and battle-tested. S0 does **not** migrate
  it onto kernel tables. Sub's `assembly_migrations` initially wraps/coexists-with its current
  schema; RLS/tenancy convergence is a *separate, later, staged* program (adoption plan Phase 4 +
  a dedicated tenancy ADR) — never bulk-adding tenant columns in adoption.
- **INPUT-NEEDED (S-9):** Sub's current Alembic lineage (revision graph, head), whether Sub uses
  RLS today or application-scoped tenancy, and how its head will declare `depends_on` the kernel
  head without a schema rewrite. This is the single highest-risk mapping and must be read from the
  Sub repo before any executable adoption.

### Field-mapping summary table

| Spec field | Sub value / intent | Blocking INPUT-NEEDED |
|---|---|---|
| `name` | `"subscriber_management"` | — |
| `modules` | Sub domain manifests (catalog, subscriptions, billing, collections, usage, provisioning, network ops, IPAM/topology, support, reseller, self-care, financial) | S-1, S-2 |
| `settings_overrides` | dedicated-ISP config knobs + Sub `SettingSpec`s via kernel declare contract | S-3 |
| `branding` | ISP-operator `BrandSpec` or `None` (+ runtime `ui_branding`) | S-4 |
| `providers` | only `ProvisioningProvider` at alpha; rest stay Sub-owned adapters until released | S-5 |
| `web_enabled` | `True` (portals retained) | S-6 |
| `disabled_modules` | per-deployment opt-outs | S-7 |
| `assembly_template_dir` | Sub portal templates (ChoiceLoader override) | S-8 |
| `assembly_migrations` | Sub-owned lineage `depends_on` kernel head; no schema rewrite | S-9 |

---

## 2. Inventory of required modules, providers, branding, and admin surfaces

What Sub's assembly declaration will enumerate (to be finalized against the repo in the later lane).

### Modules
- **Kernel-owned, inherited (declare nothing):** identity/tenancy (`Tenant`, `Party`, `Role`,
  `PartyRole`, `AuthSession`, `UserCredential`), platform auth, settings read/declare contract,
  audit write-side, base RLS/roles.
- **Sub-owned domain modules (declare as `FeatureManifest`s):** catalog · subscriptions · billing ·
  collections/dunning · usage/metering · provisioning-jobs · RADIUS/OLT/ONT/ACS network operations ·
  IPAM/topology · support/tickets · reseller · customer self-care · financial/payment-routing ·
  RBAC-assignment (`auth.subscriber_assignments`/`rbac_catalog`/`permission_gate`).
  *(Exact set + granularity: **S-1**.)*

### Providers
- **Bindable at alpha:** `ProvisioningProvider` (kernel-published, ruling C6) — only if Sub's
  dedicated-deployment slice needs it.
- **Deferred (Sub-owned adapters until the kernel seam is released):** commercial-authority/
  entitlements, licensing, billing/payment, notifications, files/storage, secrets, telemetry,
  identity (OIDC/SAML), FX/tax, DNS/TLS/ingress. *(Mapping: **S-5**.)*

### Branding
- Kernel defaults + loader/sanitizer (inherited). Sub supplies operator `brand.json` / `BrandSpec`;
  per-tenant `ui_branding` at runtime; OEM branding axis for white-label ISPs. *(Decision: **S-4**.)*

### Admin surfaces
- Kernel platform-auth routes (always present). Sub admin portal via `web_routers`/`nav` manifests
  where adopted; customer + reseller portals likely remain Sub-native initially (adoption plan
  keeps existing surfaces operational). CSRF header-bridge, tiered guards, and thin-wrapper web
  rules apply to any surface Sub mounts through the kernel. *(Scope: **S-6**, **S-8**.)*

---

## 3. Adapter boundary definitions (wrap seams; never transfer authority)

For each kernel extension point, the adapter Sub writes, and what stays Sub-owned. In every case
the adapter is a *thin wrapper around Sub's existing owning service and transaction* — it changes
source state or requests reconciliation; it never becomes a parallel decision path.

| Kernel seam | Sub adapter (wraps) | Stays Sub-owned (authority never moves) |
|---|---|---|
| **Settings resolver** (`register_specs`/`SettingSpec`/`resolve_value`) | Sub declares specs and reads values through the kernel contract; an adapter bridges Sub's current settings/flags authority to the kernel read/declare API | Sub's settings **write** authority, flag lifecycle, and any business decision keyed off a setting remain in Sub's owning service. The kernel resolver is a read/declare transport, not the decider. |
| **Feature manifests** (`FeatureManifest`, `NavItem`, `mount_features`) | Sub wraps each domain package as a manifest exposing `routers`/`web_routers`/`nav`; routers/web stay thin per the kernel rule | All business rules stay in Sub `service.py` owners (typed-manifest writers). Manifests are composition metadata + adapters; they hold no domain logic and open no second transaction. |
| **Providers** (`ProvisioningProvider` at alpha; others later) | Sub implements the kernel provider *protocol* around its existing controller/integration service | The provisioning/RADIUS/OLT/billing/notification decision + transaction stay in Sub's owning service. The provider protocol is a transport contract; Sub's job/outbox, idempotency, and compensation remain Sub-owned. |
| **Migrations** (`assembly_migrations`, `depends_on` kernel head) | Sub owns its migration lineage; it declares a dependency on the kernel base head and composes metadata | Sub's schema, RLS/tenancy model, and every domain table stay Sub-owned. The kernel ships only base identity/tenancy/settings/platform tables. No Sub table is moved into the kernel; no bulk tenant-column add in adoption. |
| **Branding** (`BrandSpec`/`load_branding`) | Sub supplies operator branding data | Kernel owns defaults/loader/sanitizer; Sub owns the branding *data* and the per-tenant `ui_branding` write path. |
| **Templating/static** (`ChoiceLoader` override) | Sub points `assembly_template_dir` at its templates; its templates shadow the kernel's | Sub owns its portal templates and static assets; the kernel provides the base layout + component macros as overridable fallback. |
| **Identity/tenancy models** (kernel-owned `Party`/`Tenant`/RBAC) | Sub maps operator → one `Tenant`; subscribers → product parties via an identity mapping (per an explicit identity ADR, later) | Subscriber/customer identity, credentials, subscriber roles (`auth.subscriber_assignments`), and network-ownership remain Sub-owned product state. Subscriber identity is **never** confused with deployment tenancy. |

**Non-negotiable:** no adapter dual-writes identity, invoice, payment, subscription, or network
state without an explicit temporary projection contract with outbox, provenance, drift detector,
repair, and a dated removal gate (adoption-plan prohibited-approaches list). S0 authors none of
these; it only *names where they would live*.

---

## 4. Kernel consumer/contract-test invocation plan (`dotmac_kernel.testing`)

Kernel Task 5 builds the kit (`dotmac_kernel.testing` — harness `assembly_test_client(spec, …)`,
fakes `FakeClock`/`FakeSeeder`/in-memory `RateLimitStore`/fake branding loader, and — ruling C6 —
`FakeProvisioningProvider` + a parametrized `dotmac_kernel.testing.contract` provisioning suite).
Sub runs these against its assembly as follows (the invocation *plan* only; no test code here):

1. **Pin the kernel.** Sub's later lane adds the released `dotmac-kernel==0.1.0a1` (exact pin) to
   its dependency/lock metadata. No source copy, no path dep on an unpublished branch.
2. **Boot-conformance run.** Instantiate Sub's `ProductAssemblySpec` and pass it to
   `dotmac_kernel.testing.assembly_test_client(spec, db_url="sqlite in-memory")`; assert the empty
   invariants the kernel guarantees (health 200, platform-auth routes present) plus Sub's declared
   module routes/nav mount. This is Sub's analogue of the kernel's empty-assembly proof.
3. **Provider contract run.** Run `dotmac_kernel.testing.contract`'s provisioning suite against
   Sub's `ProvisioningProvider` implementation (the same suite the kernel runs against
   `FakeProvisioningProvider`) — proving Sub's adapter honors the protocol. No other provider suite
   runs until its seam is released.
4. **Consumer-boot proof (wheel).** Mirror the kernel's `consumer-boot` job: install the released
   kernel wheel into a clean venv, build Sub's assembly, boot against a deliberately-unreachable
   `DATABASE_URL`, poll `/health` 200 — proving Sub consumes the kernel without copying source and
   that package-data assets resolve outside Sub's CWD.
5. **CI wiring.** These runs become required contexts in Sub's CI on the adoption branch, alongside
   Sub's existing suites (the characterization gates in §5). Merge only on green (Michael's rule).
- **INPUT-NEEDED (S-10):** Sub's current test runner/CI topology and Python/DB test matrix, to slot
  the kernel conformance runs in without displacing Sub's existing suites.
- **Gate:** steps 2–4 are executable only *after* the alpha publishes and Task 5's kit ships. Until
  then this section is a plan, not a runnable suite.

---

## 5. Characterization gates to pin BEFORE adoption (provably behavior-preserving)

Per adoption-plan migration principle 1 ("characterize before changing"), Sub must pin these gates
on its current, unchanged application **first**, so any later adoption slice is diffed against a
golden baseline. Each gate is captured on Sub's `main` before the adoption branch diverges, then
re-run and compared on every adoption slice. (Capture happens in the Sub repo — S0 only specifies
the gates.)

### Gate A — OpenAPI contract snapshot
- **Capture:** dump Sub's full generated OpenAPI document (all JSON API routers) to a committed
  golden file; add representative generated-client tests for critical paths (billing, subscription
  lifecycle, provisioning, self-care).
- **Compare:** every adoption slice regenerates OpenAPI and diffs against the golden. A non-additive
  change (removed/renamed path, changed schema, changed error code) fails the gate. Adopting kernel
  error/request-ID conventions must be *additive* behind adapters (adoption plan Phase 2).
- **INPUT-NEEDED (S-11):** Sub's OpenAPI generation entrypoint and its current API versioning
  scheme (mobile/field/reseller clients are pinned consumers that must not break).

### Gate B — Lifecycle behavior characterization
- **Capture:** golden end-to-end scenarios for Sub's critical money/service paths — subscription
  create → activate, invoice → collection/dunning, usage → quota → rating, provisioning job
  (plan/apply/observe), service enforcement, RBAC grant/revoke (`auth.subscriber_assignments`
  idempotent convergence + `subscriber.assignments_changed` event). Record inputs, state
  transitions, emitted events, and audit codes.
- **Compare:** replay each scenario on every adoption slice; assert identical transitions, events,
  and audit codes. Any kernel lifecycle-command/entitlement adapter must shadow-evaluate old vs.
  new and reconcile every mismatch before cutover (adoption plan Phase 3/5 — one decision path at a
  time, never a big-bang).
- **INPUT-NEEDED (S-12):** the authoritative list of Sub's critical lifecycle scenarios and their
  current owning transactions/events (partly known from knowledge: `financial.payment_routing`,
  `sessions.radius_resolution`, `network.device_projection`, `auth.staff_provisioning`) — full set
  is a Sub-internal fact.

### Gate C — Database (schema / RLS) characterization
- **Capture:** snapshot Sub's current schema (tables, columns, constraints, indexes) and its
  tenancy model — whether Sub enforces RLS today or application-scoped tenancy, and its Alembic head
  + revision graph. Pin cross-tenant / cross-ISP isolation invariants as canary tests on real
  Postgres (SQLite cannot enforce RLS).
- **Compare:** the assembly-migration boundary must be **expand/contract only** — Sub's
  `assembly_migrations` may `depends_on` the kernel head and add compatible structures, but the
  characterization proves no existing table/constraint/isolation property regressed. RLS/tenancy
  convergence is explicitly *out of S0 and out of first adoption* — it is a separate staged tenancy
  ADR + migration program (adoption plan Phase 4); this gate exists to *prove that boundary is
  respected*, i.e. adoption did not silently start rewriting Sub's schema.
- **INPUT-NEEDED (S-13):** Sub's actual tenancy enforcement model, schema snapshot tooling, current
  Alembic head/graph, and existing isolation-test coverage.

### Gate discipline
- All three gates are captured **before** the adoption branch changes anything, live in Sub's repo,
  and become required CI contexts (§4 step 5). A slice merges only when Gates A–C are green *and*
  the kernel conformance runs are green. This is the concrete meaning of "provably
  behavior-preserving" for Sub adoption.

---

## Consolidated INPUT-NEEDED register (requires the actual `dotmac_sub` repo)

| ID | Needed fact | Blocks |
|---|---|---|
| S-1 | Sub module/package decomposition → `FeatureManifest` mapping (~229 services / 28 domains) | `modules`, inventory |
| S-2 | Which surfaces are manifest modules vs. worker/CLI/mobile-API adapters | `modules` |
| S-3 | Sub's config/settings surface → `settings_overrides` vs. Sub-owned specs | `settings_overrides` |
| S-4 | Branding authority: operator `BrandSpec` vs. per-tenant runtime `ui_branding` | `branding` |
| S-5 | Sub external integrations → kernel provider protocols vs. Sub-owned adapters | `providers`, inventory |
| S-6 | Which portals (admin/customer/reseller) adopt kernel `web`/`nav` vs. stay Sub-native | `web_enabled`, surfaces |
| S-7 | Optional-per-deployment modules (feeds later `DeploymentProfile` required/forbidden) | `disabled_modules` |
| S-8 | Sub template tree + which kernel templates are overridden | `assembly_template_dir` |
| S-9 | Sub Alembic lineage/head + current tenancy (RLS vs. app-scoped) + `depends_on` wiring | `assembly_migrations`, Gate C |
| S-10 | Sub CI/test topology + Python/DB matrix for slotting kernel conformance runs | §4 |
| S-11 | Sub OpenAPI generation entrypoint + API versioning scheme | Gate A |
| S-12 | Authoritative critical-lifecycle scenario list + owning transactions/events | Gate B |
| S-13 | Sub tenancy enforcement model + schema-snapshot tooling + isolation coverage | Gate C |

## Execution gate for the later Sub lane

This preparation becomes executable only when **both** hold:
1. Kernel Task 3 (`ProductAssemblySpec` + `create_app`), Task 4 (empty-assembly boot), Task 5
   (`dotmac_kernel.testing` kit incl. `ProvisioningProvider` fake + contract suite), and Task 6
   (published `0.1.0a1`) have landed and the alpha is published.
2. The INPUT-NEEDED register above is resolved by reading `dotmac_sub`.

Until then, no `ProductAssemblySpec` is authored anywhere but the kernel, and Sub changes no
runtime behavior, schema, or authority boundary.
