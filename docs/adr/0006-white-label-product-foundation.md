# ADR 0006 — White-label product foundation: packages, modules, themes, brands

**Status:** Accepted
**Date:** 2026-08-02
**Extends:** ADR-0003 (composable deployment profiles), ADR-0002 (starter
consolidation), ADR-0001 (multi-tenancy)
**Owns:** the package split, the module/theme/brand/facet terminology, the brand
precedence chain, and the extraction rule for the white-label foundation
programme (F0)
**Does not own:** module/plugin mechanics (the module control-plane directive),
deployment/commercial authority (ADR-0003), artifact distribution (ADR-0005)

## Context

ADR-0003 decided that every Dotmac deployment shape composes from one kernel,
versioned modules, a product assembly, and a deployment profile. It did not
decide **how presentation is shared**. In practice each product has grown its own
templates, its own CSS, its own navigation, and its own notion of "brand", so the
same table, the same form, and the same empty state exist several times over, and
white-labelling a product means editing its source.

The programme objective is:

> Any Dotmac product can be assembled from trusted, versioned modules; presented
> through a consistent accessible UI; branded without source changes; configured
> without hardcoding; and released as a tested SaaS, dedicated, on-premises,
> offline, or OEM deployment.

Three failure modes have to be designed out before any code moves:

1. **Extraction from similarity.** Two templates that look alike are not evidence
   of a shared contract. Extracting them produces a component with two owners and
   no authority — the exact drift ADR-0003 exists to prevent.
2. **Conflating presentation with capability.** If a "portal" is modelled as a
   module, every audience needs its own copy of every capability. If a theme can
   carry behaviour, a branding change becomes a code change with a code change's
   risk.
3. **Conflating display brand with legal identity.** A white-label build that can
   silently change who the data controller is, or who the support contact is, is
   a compliance defect, not a feature.

This ADR fixes the vocabulary and the ownership boundaries so F1–P1 have a
stable frame. It changes no runtime behaviour.

**One F0 finding reframes the work ahead and belongs here rather than buried in
an inventory.** The starter's branding system is largely *inert*: of its brand
keys, only `name` changes anything a user sees. `brand.mark` is rendered by two
templates but appears in no key map, default, or allowlist, so every deployment
shows the same hardcoded letter; `logo_url` is editable, persisted, and read by
no template; `primary_color`/`accent_color` paint swatches in the branding
editor's preview while the real colours are compile-time Tailwind classes. The
practical position is that **the portal cannot currently display a customer logo
or colour at all**. U2 is therefore not "improve branding" — it is "branding is a
stub; build it" — and the token system U1 produces is the prerequisite that makes
a runtime colour possible in the first place. Evidence:
`docs/inventories/starter-surfaces.md`.

## Decision

### 1. Four distinct concepts, with four different authorities

Do not use these words interchangeably. Each has a different authority, a
different lifecycle, and a different blast radius.

| Concept | What it is | Authority | May contain | May NOT contain |
|---|---|---|---|---|
| **Module** | A versioned unit of **capability** | `ModuleManifest` + `ModuleRegistry` (installed code) | routes, services, models, migrations, declarations, capability codes | another module's imports; presentation primitives owned by the UI system |
| **Theme** | A versioned unit of **presentation** | `ThemeManifest` (installed trusted code) | token defaults, packaged assets, declared template/slot overrides | business logic, data, migrations, routes, DB reads |
| **Brand** | **Data** describing display and contact identity | `BrandProfile` (resolved at runtime) | names, logos, colours chosen from allowlisted tokens, URLs, sender/footer presentation | code, Jinja, JavaScript, CSS text, capability grants |
| **Product facet** | A **surface** presenting modules to one audience | the deployment profile (which facets mount) | layout, navigation, and route composition for that audience | capability decisions of its own |

A **product facet** is the concept the codebase has been missing. Staff
administration, tenant administration, customer self-service, reseller, vendor,
field technician, public/signup, email, document, and API-only are *facets*, not
modules and not deployments. A module contributes surfaces to one or more facets;
a deployment profile selects which facets are mounted; a facet never owns a
capability decision. This is what makes "the customer portal needs the same
invoice view as the staff portal" a composition question rather than a
duplication question.

**Corollary — the two version axes.** A module declares both the module contract
version it was built against (kernel-owned, `ModuleManifest.contract_version`)
and the UI contract version it renders against (`dotmac-ui`-owned). They evolve
independently: a UI component library revision must not force every module to
re-declare its capability contract, and vice versa.

### 2. Package ownership map

```text
dotmac-kernel          invariants that must be corrected exactly once
  tenancy, sessions, transactions
  identity, authorization, audit
  settings and branding RESOLUTION
  module registry and lifecycle
  capability/entitlement decisions
  migration orchestration
  app/product composition

dotmac-ui              the presentation system (no business logic, no DB)
  semantic design tokens
  Jinja/HTMX component library
  layouts and navigation primitives
  forms, tables, feedback states
  accessibility contracts
  packaged static assets

dotmac-module-sdk      development and conformance tooling (never a runtime dep)
dotmac-template-studio a MODULE — the first stateful one, not a kernel facility
dotmac-theme-*         trusted packaged themes, where tokens are insufficient

product assemblies     exact pins + selected modules + theme/brand + provider
                       bindings + deployment profile
```

**Dependency direction is one-way and enforced:**

```text
assembly → module → dotmac-ui → dotmac-kernel
```

- The kernel never imports `dotmac-ui`, a module, a theme, or an assembly.
- `dotmac-ui` never imports a module or reads a database. It renders what it is
  given.
- A module never imports another module (already enforced by import-linter;
  cross-module UI composition uses the htmx-fragment pattern, not an import).
- A theme never imports a module and never reads the database.
- `dotmac-module-sdk` is a development/test dependency only. A product that ships
  with the SDK in its runtime image is a packaging defect.

**Ownership rulings that follow, and that later steps may not relitigate:**

- **Settings remain one kernel-owned facility.** Modules contribute
  `SettingSpec` declarations. A module that creates its own settings table,
  resolver, or `.env` convention is a defect, not a variation.
- **Branding RESOLUTION is kernel-owned; branding DATA is not code.** A theme
  supplies token *defaults*; a `BrandProfile` supplies *data*; the kernel
  resolves and reports the effective source. No component reads brand config
  directly.
- **Template Studio is a module, not a kernel facility.** It is stateful,
  optional, and has its own lifecycle. The kernel's own operational templates
  (error pages, the login screen) are code and stay in the kernel/UI packages —
  they are not Template Studio content. **Narrowed by the 2026-08-10 amendment
  (§ 5b):** it owns *what the message says* and nothing else — not document
  generation, not consent, not routing, not delivery.
- **Navigation is derived, never authored twice.** A facet's navigation is
  composed from module manifest declarations. A hardcoded link list in a template
  is a defect. This is already the admin portal's design — the sidebar renders
  from the manifest-derived `nav_items` global, and
  `test_nav_items_paths_exist_in_web_routers` fails a nav entry with no mounted
  route — but note that no test yet fails a template that *adds* its own
  hardcoded links. Generalising the rule to every facet needs that missing check,
  which is F2 work, not an existing guarantee.

### 3. Brand precedence

Brand resolution is **per field**, most specific wins:

```text
generic kernel default
  < vendor/deployment brand
  < OEM/reseller brand
  < tenant/operator brand
```

This chain is **not a proposal — three of its four layers already exist and work
in `dotmac_sub`**, whose `BrandProfile.scope_type` is constrained to
`platform | reseller | organization` (`app/models/branding.py:28-29`), backed by
a `brand.json` shared with its Flutter apps and a runtime `/branding/theme.css`
with contrast gating and a fail-safe. The decision is therefore to **adopt Sub's
proven model**, adding the generic kernel-default layer beneath it and the two
rules below — not to design a new one. Sub's implementation is the reference; the
starter's is the stub (see below).

**Adopt Sub's precedence model and token pipeline; do NOT adopt its defaults
module.** Sub's built-in brand defaults are the real DotMac production identity
(`DotMac`, `Dotmac Technologies`, `support@dotmac.ng`, `noreply@dotmac.ng`, and
the `dotmacpay` payment URL scheme) — which the starter deliberately refuses to
ship. Taking that module wholesale would make every unbranded fork send mail as
`noreply@dotmac.ng` and collide on a mobile URL scheme its own Flutter config
says must be unique per brand. The kernel-default layer this ADR adds beneath
Sub's three must be **generic**, and enforcing that is a two-brand-canary check,
not a code review habit.

Three rules make this safe:

1. **Every resolved field reports its source.** "Effective value + which layer
   supplied it" is part of the contract, not a debugging convenience — support
   cannot answer "why does this deployment show that logo?" without it.
2. **A layer may LOCK a field against lower-precedence override.** An OEM that
   must present its own support identity to its downstream tenants needs the
   tenant layer to be unable to replace it. Locking is explicit and recorded, not
   an emergent property of which layer happens to set a value.
3. **Display brand is separate from legal identity.** Legal vendor,
   data-controller, and support identity are contract-derived data with their own
   authority. They are represented as distinct fields, they are not stylable, and
   a lower-precedence layer may never override them. A white-label build changes
   what the product *looks* like; it must never change who is *legally
   responsible* for it without an explicit contractual change.

**Consequence for tenant-supplied CSS.** Raw tenant CSS cannot satisfy rule 3:
CSS alone can obscure, reposition, or restyle legal, consent, and
security-relevant UI, and sanitisation of the *input* does not restore that
invariant — the sanitiser's job is to block script vectors, not to guarantee that
a footer remains legible. The target is therefore **allowlisted brand tokens for
tenants, trusted packaged CSS for OEM themes** — no user-supplied Jinja, no
arbitrary JavaScript, no theme code loaded from the database.

Two facts about the *current* implementation, verified during F0, that this
decision must not misrepresent (see `docs/inventories/starter-surfaces.md` and
`branding-settings.md`):

- `custom_css` today renders **only inside the branding editor's own preview
  pane** (`templates/admin/settings/branding.html`), never in `base.html`. The
  site-wide exposure is therefore *latent*, not live — it becomes real the moment
  someone "finishes the feature" by wiring it into the base layout, which is
  exactly why the decision is being taken now rather than after.
- The CSP relaxations attributable to branding are **two, and only one of them is
  fully recoverable by this decision** (`docs/SECURITY.md` § "Content-Security-Policy
  rationale" is explicit about both causes):
  - `img-src 'self' data: https:` — the `https:` exists *solely* so tenant
    branding can point `logo_url` at an external image. Moving logos to
    deployment-packaged assets recovers `img-src 'self' data:` outright.
  - `style-src 'self' 'unsafe-inline'` — cited as covering the sanitised
    `custom_css` preview **and** Alpine's `x-show` style toggling. Retiring
    `custom_css` therefore does **not** on its own recover `style-src 'self'`;
    that additionally needs the Alpine dependency addressed (its CSP build, or
    splitting `style-src-elem` from `style-src-attr`). Claiming otherwise would
    set up a hardening promise the change cannot keep.

So `custom_css` is a compatibility seam to be retired on a stated path — justified
primarily by rule 3, with a real but partial CSP dividend.

**What CSS injection actually buys an attacker**, since "it's only CSS" is the
usual reason this gets deferred: `display:none` and `::after{content}` hide or
rewrite legal and consent text; `position:fixed` + `z-index` overlays destructive
controls *same-origin*, so `frame-ancestors 'none'` is irrelevant to it; and
attribute selectors of the form `input[value^="a"]{background:url(https://…)}`
exfiltrate field contents character by character — permitted by the starter's
sanitiser, which allows `http`/`https` URLs, and by `img-src https:`. These are
CSS's *intended* semantics, which is precisely why no sanitiser removes them.

### 4. Supported product-profile matrix

The profiles the foundation must prove, as the cross-product of ADR-0003's
independent axes. These are the CI targets, not marketing names:

| Profile | Tenancy | Operator | Connectivity | UI surface | Update authority |
|---|---|---|---|---|---|
| `saas-multitenant` | shared | vendor | online | web + API | vendor automatic |
| `dedicated-single-tenant` | one tenant | vendor | online | web + API | vendor automatic |
| `onprem-online` | one or more | customer | online | web + API | customer-approved |
| `onprem-airgapped` | one or more | customer | air-gapped | web + API | offline bundle |
| `oem-multitenant` | shared | OEM partner | online | web + API | vendor/OEM agreed |
| `api-only` | any | any | online | API only | any |

Orthogonal to the table, every profile must additionally pass the **two-brand
canary**: the same assembly built twice, differing only in configuration, theme,
and lock — never in source.

### 5. The extraction rule (the F0 gate)

**Nothing is extracted into `dotmac-ui`, a shared module, or the kernel on the
grounds that two implementations look similar.** A candidate for extraction must
present:

1. **Two independent consumers of the same CONTRACT** — the same inputs, the same
   semantics, the same failure behaviour. Similar markup is not a contract.
2. **A named owner** for the extracted unit, per the Dotmac source-of-truth
   standard.
3. **A migration and cutover path**: which consumer moves first, what the shadow
   period is, and how drift is detected afterwards.

Absent all three, the duplication is recorded in the inventory and left in place.
Recording it is the deliverable; removing it is not.

## Inventories (the rest of the F0 deliverable)

The as-built characterization this ADR rests on lives in `docs/inventories/`, and
is deliberately separate from this file: the ADR states decisions, the
inventories state facts, and facts go stale. See `docs/inventories/README.md` for
the index and the as-of date of each.

## Open decisions surfaced by F0 (NOT decided here)

F0's job is to record what exists and to decide the foundation's vocabulary and
boundaries. It surfaced eight questions that are **owner decisions, not
inferences this ADR may make on its own**. Each blocks a named later step. They
are listed with evidence so the decision can be taken on facts rather than
re-derived.

| # | Decision needed | Blocks | Evidence |
|---|---|---|---|
| **D1** | **Namespacing for module tables and Alembic revision IDs.** Postgres schema per module, or an enforced table-name prefix? Plus a revision-ID prefix registry. | F4 (migration orchestrator); any module composition at all | `migration-collisions.md` |
| **D2** | **Which licence format survives** — ERP's own scheme (`dotmac_erp/app/licensing/`, 478 lines) or kernel WS8. Two incompatible formats exist in the fleet today. | ERP adoption; O1 | `erp-vendor-surfaces.md` |
| **D3** | **Does `dotmac-ui` require Tailwind v4 CSS-first, or stay toolchain-agnostic?** Starter and Sub are both v4 CSS-first; ERP is v3.4.19 with a JS config. ERP is the outlier, so "require v4" is a migration cost in exactly one repo. | U1 | all three surface inventories |
| **D4** | **Are the vendor-named licence schema IDs** (`dotmac-licence-envelope/1` and three siblings) **renamed before or never?** They are on the signed wire contract; renaming later is a breaking protocol change. | O1; OEM profile | `starter-surfaces.md` |
| **D5** | **Amend the kernel import allowlists to admit a UI package.** Sub's ledger currently permits `assembly, capabilities, features, money, profiles, providers*` and **no** templating/branding/UI module. A shared UI package cannot be adopted without that amendment. | U1 adoption | `sub-surfaces.md` |
| **D6** | **Is `/admin` (and each facet's prefix) configurable?** It is currently baked into 45+ template literals, the web guard, and the governance tests' path prefix. An OEM that wants `/manage` cannot have it. | U1/U2; OEM profile | `starter-surfaces.md` |
| **D7** | **Which CSP the shared kernel emits.** The three postures are mutually exclusive: the starter ships a strict policy; ERP ships literally one directive (`script-src 'self' 'unsafe-eval' 'unsafe-inline' https://cdn.jsdelivr.net`, via `CSP_ALLOW_UNSAFE: 'true'`) plus a *per-organization runtime* Google Fonts URL that cannot be statically allowlisted; Sub emits **no CSP at all**. A kernel emitting the starter's policy breaks ERP on the first request. | U1 adoption; O1 | `branding-settings.md` |
| **D8** | **Does "no tenant-supplied raw CSS; brand customisation is an allowlisted token set" become an approved cross-Dotmac standard?** This ADR decides it for the foundation; making it fleet-wide is a separate ruling. | U2; ERP/Sub convergence | `branding-settings.md` |

**D1 is the one that blocks everything.** It is verified, not theoretical:
`starter ∩ ERP` collide on `audit_events`, `domain_settings`, `people`,
`person_roles`, `roles`, `user_credentials`; `starter ∩ Sub` collide on
`parties`, `party_roles`. Sixteen of seventeen cross-repo duplicate table names
are **same-name, different-shape**, and they sit almost entirely on
kernel-shaped tables — identity, authn, authz, settings, audit. Three of four
repos use no schema namespacing at all, so collision avoidance today rests
entirely on convention. Unlike a revision-ID clash, which fails loudly at
`ScriptDirectory` load, a same-name/different-shape table collision fails
**quietly in the dangerous direction**: an `add_column` mutates another module's
table, and an ORM class mapped to a differently-shaped existing table reads wrong
rows. No module composition should ship before D1 is answered.

### Decision amendment — 2026-08-02 (D1–D8 resolved)

The owner delegated these eight decisions after reviewing the F0 evidence. The
original questions above are retained as the historical decision record; the
rulings below are normative for F2–P1.

#### D1 — a dedicated Postgres schema per stateful module; registered migration prefixes

Every independently installed stateful module owns one immutable Postgres
schema namespace. The namespace is assigned in the module registry, uses the
form `mod_<short_code>`, and is never inferred from a display name. Module
models, migrations, foreign keys, policies, functions, and raw SQL fully
qualify their schema; they never depend on `search_path`. A stateless module
declares no database namespace.

`public` remains the compatibility namespace for the existing kernel and the
one host assembly. It is not available to installable modules. Existing ERP or
Sub tables do not become module tables merely because their code is extracted:
the extraction's expand/contract migration must create or adopt the assigned
module schema explicitly. Until that cutover is proven, the source product and
the candidate module are not composable in one database.

Each migration owner also receives an immutable, globally unique short prefix
from the registry. Revision IDs use `<prefix>_<sequence>_<slug>` within
Alembic's version-column length; the module lineage has its own base and branch
label, and cross-lineage ordering uses `depends_on`, never `down_revision`.
The existing composed `alembic_version` table remains the migration truth;
manifest-to-branch attribution makes its rows explainable. A composed CI gate
must load every selected version location and reject duplicate revisions,
prefixes, branch labels, schema claims, or table ownership before an image can
be built. The post-migration live-catalog gate applies the kernel RLS/grant
contract across every registered module schema.

##### D1 amendment — 2026-08-13: cross-lineage ordering is LOGICAL

The clause above ("cross-lineage ordering uses `depends_on`") is amended. It
assumed a module could name the revision it needs. A module cannot, because the
answer differs per assembly, and a physical edge is therefore a claim about a
deployment the module has never seen.

**The blocked adopter, which is evidence rather than hypothesis.** Kernel
revision `0001_initial_tenant_schema` creates `public.tenants` unconditionally
as its first table. ERP hosts that same table in its own lineage
(`20260813_tenant_projection`, merged to ERP `main` as `c5f933d9`), so kernel
`0001` can never run there — its lineage rehearsal is pinned permanently at that
collision, and no product-side disposition of `people`, `roles` or
`audit_events` can move it. `dotmac-files` declared
`depends_on = ("0001_initial_tenant_schema",)`, which made stored bytes
un-installable in ERP unless ERP first converged its entire identity, RBAC and
audit estate onto the kernel's. That is a large amount of coupling to buy a
foreign-key target, and none of it is required by what files actually needs.

**Amended rule.** A module lineage declares the logical database EFFECTS it
requires (`ModuleManifest.requires`, `dotmac_kernel.prerequisites`); the
answering lineage declares what it supplies (`MigrationOwner.provides`); and each
assembly binds requirement to provider revision in checked-in, typed
`PrerequisiteBinding`s installed from its Alembic `env.py`. A module lineage may
**not** name a foreign revision directly. Host owners (`kernel`, `assembly`) keep
literal edges: they are the deployment, so naming one of their own revisions
asserts nothing about anybody else's.

**Ordering is preserved, not weakened.** `resolve_depends_on` turns the binding
back into a real `depends_on` edge at script-load time, so Alembic orders exactly
as before. What changes is who authors the edge.

**A binding is a claim, so it is never the only control.** Two guards, neither of
which is a comment:

1. the composed gate rejects a module naming a foreign revision, an unbound
   requirement, a binding to a lineage that does not declare the effect, a
   binding to an uncomposed revision, and a migration requiring more than its
   manifest admits;
2. `require_prerequisites` verifies the real catalog before any DDL, so a
   **stamped** or aliased provider fails against the database — stamping writes
   no columns.

Ordering itself needs no third guard, and an early draft that added one was
wrong twice over. It asserted the bound revision appeared in `alembic_version`,
but that table records each branch's current HEAD, not applied history — so the
check failed against every real database once a lineage advanced past its root.
It was also redundant: `resolve_depends_on` emits a real `depends_on` edge and
Alembic will not run a revision before the one it depends on, while a binding
naming an uncomposed revision is already rejected statically. What only the
database can answer is whether the EFFECTS are present, which is guard 2.

A blanket `IF EXISTS`, a product-specific conditional inside a kernel migration,
and `alembic stamp` remain forbidden and are not bindings.

**Vocabulary is a registry, never an enum** (ADR-0008). The kernel ships
`tenant_scope_catalog.v1` and `module_database_roles.v1` because those are the
two effects a real blocked adopter needed; a product names its own without a
kernel change. A changed contract is a new `.vN`, never a redefinition — every
existing binding was accepted against the old one.

**Status under ADR-0017.** This is a blocked-adopter exception, not a
supply-pushed facility: it was built because ERP is blocked today, the
verification logic is EXTRACTED from ERP's proven `20260813_tenant_projection`
rather than invented (hard rule 22, product-first), and it removes an adoption
blocker instead of adding surface awaiting adopters.

#### D2 — kernel WS8 is the sole target licence protocol

The signed, versioned WS8 licence, applied-state, keyring, and revocation
contracts survive. ERP's existing format is legacy input only: ERP E10 must
inventory it, issue or map replacement WS8 state, shadow-verify the cutover,
and retire the old verifier and writer. There is no permanent dual-authority or
"accept either forever" mode.

#### D3 — `dotmac-ui` is toolchain-agnostic to consumers

`dotmac-ui` may use Tailwind v4 to build its own released assets, but its public
contract is compiled, versioned CSS/assets, semantic tokens, stable component
classes/data attributes, and Jinja/HTMX APIs. A consumer does not run the UI
package through its own Tailwind compiler and does not need the same Tailwind
major. ERP may migrate from Tailwind v3 separately; that migration is not an
adoption prerequisite.

#### D4 — the `dotmac-*` licence schema IDs remain permanent protocol identifiers

The schema IDs identify the software/protocol vendor, not the deployment's
display brand. White-labelling must not rewrite signed protocol identity.
They therefore remain stable for version 1 and may change only for a real,
versioned protocol break with an explicit compatibility window — never as an
OEM cosmetic substitution.

#### D5 — allow `dotmac_ui` through a narrow, explicit consumer boundary

Product architecture allowlists will admit the released top-level `dotmac_ui`
public surface in the same change that introduces a real consumer. This does
not grant modules access to kernel templating or branding internals and does
not permit arbitrary presentation packages. The ledger, import guard, wheel
consumer proof, and supported UI-contract range must move together.

#### D6 — facet prefixes are deployment-static, not tenant-runtime settings

HTML facet mount prefixes are assembly/profile configuration resolved at
startup, with `/admin` retained as the default. Templates and redirects use
named route generation rather than literal prefixes. Prefixes must be absolute,
normalized, unique, non-overlapping, and outside reserved API/platform paths.
They cannot vary by tenant inside one running assembly. Versioned JSON API
prefixes remain protocol contracts and are not rebranded.

#### D7 — kernel owns a composed strict CSP contract, not one fleet product's string

The kernel supplies a deny-by-default CSP baseline and a typed composition
mechanism for requirements declared by trusted installed packages and the
assembly. Modules and tenants cannot contribute raw directives or arbitrary
origins. The resolved policy is deterministic and inspectable, and unsafe
directives, wildcards, CDNs, and runtime per-tenant origins fail the supported
profile gate unless an explicit temporary compatibility ADR names their owner
and retirement condition.

The starter policy is the target posture, not something silently imposed on an
incompatible ERP deployment. ERP must vendor its jsDelivr dependencies and
runtime Google Fonts, remove `unsafe-eval`, and pass report-only canaries before
enforcement. Sub gains the composed policy through the same compatibility
gate. `dotmac-ui` ships self-hosted, CSP-compatible assets.

#### D8 — no tenant-supplied raw CSS is a fleet-wide standard

Tenant/operator brand customization is an allowlisted, typed token set with
contrast and URL validation. Trusted CSS exists only in versioned theme/UI
packages. Starter and ERP stop accepting new raw-CSS writes; existing values
are retained only as migration evidence until they are exported, mapped to
tokens where possible, and explicitly retired. Unrepresentable rules are
reported to the operator and are never silently copied or silently discarded.
Sub's token-only pipeline is the reference posture.

### Decision amendment — 2026-08-08 (product-first extraction)

Section 5 prevents speculative extraction, but its phrase "left in place" did
not say what to do once an extraction is approved and one product already has a
mature, tested implementation. The owner has now resolved that ambiguity:

1. **Inventory the products before writing shared behaviour.** For a candidate
   kernel facility or optional module, ERP, Sub, and any other product named in
   the candidate's scope are searched first. When a production-used,
   sufficiently tested implementation already satisfies most of the agreed
   contract, that implementation is the mandatory reference and initial code
   source. A greenfield implementation is admissible only when the checked-in
   inventory shows that no qualifying product implementation exists.
2. **Copy means a one-time extraction, not a maintained fork.** The proven code
   and its behaviour tests are moved or ported into the shared distribution.
   Product-specific dependencies are cut at typed adapter/provider seams; the
   underlying behaviour is not rewritten merely to look more generic. The
   source product is the first cutover consumer and its local owner/writer is
   then removed or explicitly gated for retirement.
3. **Place the unit at the narrowest shared layer.** Behaviour required by every
   assembly and free of business-domain policy belongs in `dotmac-kernel`.
   Optional business capability belongs in its own independently versioned
   `dotmac-<module>` distribution, with its own manifest and, when stateful,
   its own schema and migration lineage. Product-only behaviour remains in its
   product even if another implementation looks similar.
4. **Preserve proof and make the cutover repairable.** Every shared distribution
   carries an extraction dossier naming the contract, source repositories and
   paths, source tests preserved as parity proof, two contract consumers, the
   owner, first cutover, shadow/drift check, and local-copy retirement gate.
   Existing distributions predating this amendment are recorded as explicit,
   non-growing debt rather than silently treated as conforming.

This amends, rather than relaxes, the original F0 gate. The two-consumer,
named-owner, and migration requirements still decide **whether** a unit may be
shared. Product-first extraction decides **how** it is implemented once that
decision is made. It forbids both failure modes: speculative shared code with no
real consumer, and a second implementation written beside mature product code.

### Decision amendment — 2026-08-18 (vertical replacement cutover)

The product-first amendment says the source product is the first cutover
consumer. That remains the default for an in-place extraction. It was written
before an accepted programme had a different, stricter destination: retire a
legacy product vertically into a clean assembly without first rebuilding the
legacy product around the target architecture.

For an **accepted vertical replacement**, the replacement assembly may be the
first runtime consumer when all of the following hold in checked-in evidence:

1. the legacy product is named as the qualifying implementation source and the
   replacement assembly is named as the destination;
2. composing the module in the source would require a throwaway rewrite of a
   lineage, identity or transaction boundary the accepted programme says to
   retire;
3. the replacement imports data only through a versioned API/webhook, never the
   source database, models or filesystem;
4. the overlap is read-only shadow/reconciliation: the source remains the sole
   writer until one sealed cutover makes the replacement the sole writer and
   fails closed the source mutations; and
5. the source writer, fallback and obsolete tables still have explicit
   ratchets and retirement gates. Moving the runtime without retiring the old
   authority earns no adoption evidence.

In that case the source product is the **first authority retired**, while the
replacement assembly is the first exact-pin runtime consumer. Source code and
tests remain the mandatory extraction base. The evidence ladder is unchanged:
one runtime consumer supports `adopted`; `reuse-proven` still requires two
independent consumers of the same released contract.

The first named use is ERP -> Backoffice, accepted in
`dotmac_backoffice/docs/adr/0001-why-backoffice-exists-and-why-replacement-is-vertical.md`.
ERP cannot truthfully compose kernel Party/tenancy without the very aggregate
redesign that decision rejected. Requiring that detour would improve the
temporary bridge instead of retiring it. The specific source ruling and
cutover gates are in
[`../inventories/people-directory-sources.md`](../inventories/people-directory-sources.md).
This amendment does not authorize a merge, release, deployment, production
mutation or destructive retirement.

**Correction — 2026-08-22.** The historical reference above used the working
name `dotmac_backoffice` for a local composition sketch. It was never a
separate repository or product. The destination is the commercial Dotmac ERP
product (`dotmac-erp`), vertically recomposed from Starter modules; the
historical extraction source is the `dotmac_erp` repository. The vertical
replacement mechanics remain accepted, but the separate-application identity
is withdrawn.

### Live defects found while taking the inventory (owned elsewhere)

F0 is documentation-only and fixed nothing. Three defects surfaced that are
**independent of every decision above** — they want fixing whether or not the
foundation programme proceeds — and they are recorded here so they are not lost
with the agents that found them. All three were verified directly against the
source, not accepted on report.

1. **`dotmac_erp` injects unsanitised tenant CSS on a public page.**
   `organization_branding.custom_css` is appended **verbatim** to the generated
   stylesheet (`app/services/finance/branding.py:250` — `lines.append(branding.custom_css)`,
   no sanitiser at any layer), and rendered `{{ brand.css | safe }}` inside
   `<style>` in `templates/login.html:12` and the **unauthenticated** careers
   portal `templates/careers/base_careers.html:40`, each under an explicit
   `{# nosemgrep: semgrep.safe-on-user-content #}` waiver. The same content is
   also served from `GET /branding/org/{org_id}/css` (`app/api/settings.py:717`).
   This is CSS injection, not script execution — but see the paragraph above on
   what CSS injection actually buys an attacker. The starter's equivalent is
   sanitised *and* preview-only; ERP's is neither.

2. **`dotmac_erp`'s cached settings read path is not tenant-scoped.**
   `app/services/settings_cache.py:310-356`: `get_setting_value(db, domain, key,
   default)` takes **no** organization argument, queries
   `select(DomainSetting).where(domain==…, key==…, is_active)` with **no**
   organization filter, takes `db.scalar()` (an arbitrary matching row), and
   caches it under `f"settings:{domain}:{key}"` — a key with no tenant
   component. ERP's other read path (`resolve_value`) *is* organization-aware,
   so the two disagree: in a multi-organization deployment this one can serve
   and then cache one organization's value for another.

3. **`dotmac_sub` has multiple writers for logo/favicon/colours**, alongside
   `sync_platform_brand_from_legacy_settings` as an unfinished one-way
   migration. Consolidating on `BrandProfile` without finishing that migration
   inherits a dangling legacy writer — a direct violation of SOT-complete
   criterion 5.

   > **Corrected 2026-08-03, on evidence.** The original wording named
   > `web_system_company_info.py` as the third writer and described it as raw
   > SQL bypassing the spec registry. Both halves were wrong: it uses the ORM,
   > and it writes no logo/favicon/colour key at all, so it was never the
   > branding violation. (It *is* a spec-bypassing settings writer — the
   > `company_*` keys have no `SettingSpec` — which is a separate finding.) The
   > actual dangling branding writer, which the inventory missed, is the
   > **generic comms settings form**
   > (`web_system_settings_forms.process_settings_update`): it wrote all 13
   > branding keys with none of the owner's validators, skipped the
   > managed-asset delete leg, reset absent keys to spec defaults, and never
   > projected onto `BrandProfile`. Consolidated on branch
   > `chore/branding-writer-consolidation` (`20ad2a4f7`). This is why an
   > inventory is evidence to act on, not evidence to trust.

Recording them here is the deliverable. Fixing 1 and 2 is `dotmac_erp` work and
should not wait on this programme.

### Conformance gaps (already decided, not yet honoured)

These need no new decision — ADR-0003 already rules on them — but F0 found the
implementation does not comply, so they are recorded as debt rather than
questions:

- **Locale/currency are not independent of brand.** Sub hardcodes 274 `NGN` and
  17 `Africa/Lagos` occurrences in `app/`, and its `money` / `app_datetime` Jinja
  filters are documented as fixed to NGN/WAT. ADR-0003 § "Internationalization,
  currency, and jurisdiction" already forbids this. In F0 terms: locale leakage
  is *larger* than brand leakage, and a two-brand canary would pass while a
  two-*country* deployment would not.
- **RLS is repo-local and mutually incompatible.** The starter enforces it with a
  dynamic catalog audit; ERP enforces it only via two point-in-time sweep
  migrations, so anything created afterwards silently gets none; Sub has no
  `tenant_id` columns and no RLS by design. A module moving between them cannot
  assume the invariant holds.
- **`alembic_version` is one shared, unattributed table** in all four repos —
  nothing records which module owns which head row. F4's orchestrator needs that
  attribution.

### Decision amendment — 2026-08-09 (build once; an extension point is not a licence)

Section 5 is a brake on sharing things that merely LOOK alike. It says nothing
about the opposite failure, which has now happened: implementing a shape in a
product that every product needs, because an extension point made it easy.

The owner's ruling: **a capability a second Dotmac app would otherwise
reimplement is built in the shared layer, not in the product.** This covers
value types, adapters, and reusable functions — not only things that feel like
"framework".

1. **An open extension point is not a licence to use it.** The kernel's
   registries — permissions, capabilities, audit actions, feature flags,
   setting domains, setting value types, setting scopes, secret sources — exist
   so a product can declare vocabulary that is genuinely ITS OWN. Declaring a
   UNIVERSAL shape through one is the forking failure ADR-0008 exists to
   prevent, arrived at from the other direction: nothing is duplicated, and the
   fleet still ends up with two incompatible definitions of one thing.

   The applied test, before declaring anything on a product manifest: *would a
   second app need this?* If yes, it belongs to the shared layer, and the
   product declares nothing.

2. **This governs SAMENESS; Section 5 governs SIMILARITY. Neither relaxes the
   other.** Two dashboards that resemble each other are not one component, and
   Section 5 still refuses them. A JSON list, a held secret reference, an exact
   money amount are one thing with one correct encoding, and a second
   implementation of one is a defect rather than a candidate. When a concrete
   case looks like it satisfies both readings, it is a Section 5 case — the
   two-consumer, named-owner and cutover gate decides, and "build once" is not
   an argument for skipping it.

3. **Mechanism is shared; policy stays in the product.** Gapless sequence
   allocation under concurrency is a mechanism; what an invoice number looks
   like is policy the product declares. A shared unit that cannot be described
   without naming a business rule is in the wrong place.

4. **Adapters are shared as PORTS, not as vendor bindings.** The typed
   protocol, the held-credential contract (ADR-0009), timeout/retry/
   circuit-breaking, the error taxonomy and the test fake are one thing, built
   once. A specific vendor binding ships as its own `dotmac-adapter-*`
   distribution against that port — inside the kernel it would make every
   assembly inherit that vendor's SDK and release cadence.

**First application.** Sub's settings cutover needed a `list` setting value
type. Sub could have declared one on its own manifest and nothing in this ADR
forbade it; ERP would then have declared an incompatible one. `list` shipped as
a kernel built-in instead (`0.1.0a27`), and `dotmac-kernel`'s `COMPATIBILITY.md`
now states the test above at the point where a reader is choosing.
`secret_ref` follows the same route once the per-secret classification lands.

**Note for the product-first extraction amendment.** That amendment (dated
2026-08-08, in flight on `docs/product-first-extraction` at the time of
writing) decides HOW a shared unit is implemented once sharing is agreed —
inventory the products, port the proven implementation, place it at the
narrowest shared layer. This amendment decides WHERE NEW work goes when no
product implementation is the reference. Its point 3 and this amendment's
points 3–4 overlap on placement and should be read as one rule when both have
landed; if they are ever in tension, the placement wording in the product-first
amendment is the more specific and wins.

### Decision amendment — 2026-08-10 (sweep for restatements before a cutover)

The 2026-08-08 amendment governs EXTRACTION: inventory the products before
writing shared behaviour. Adoption needs the mirror, and its absence has now
cost a measurable amount.

Sub's settings cutover produced seven CI failures across eight push cycles.
They presented as unrelated — a duplicate spec, a type error, a schema
validation error, two mocked-session failures, two query budgets — and were one
root cause seven times: **a fact the kernel owns had been expressed a second
time inside the product**, and the two copies had drifted.

Restatement is not code duplication. The implementations differ, which is
precisely why it survives review. What is duplicated is a DECISION — which
column a value type uses, what a cache key contains, which duplicate spec wins
— and it drifts in one direction only: the kernel changes and the product's
copy silently stops agreeing.

So, before a product cuts over to a kernel subsystem:

1. **Sweep for restatements, not call sites.** For each fact the incoming
   subsystem owns, find where the product already answers it: a vocabulary held
   as an enum or CHECK; a decision taken by comparing against a literal name; a
   key format, TTL or invalidation rule; a data-access shape baked into test
   doubles. Call-site inventories do not find these — Sub's recon counted 560
   call sites and 574 specs and found none of the seven.
2. **Record the sweep before the first push.** `docs/inventories/
   kernel-restatement-sweep.md` holds it, dated, per product, as facts rather
   than mandates like every other inventory.
3. **Each restatement becomes one expression or one recorded debt.** The
   surviving expression is an ADAPTER that ASKS the kernel; it does not decide.
   Anything not migrated in the cutover is a shrink-only baseline in an
   architecture test, so it is bounded rather than forgotten.
4. **Enforce with a test per fact, not with review.** Sub's
   `tests/architecture/test_kernel_owned_facts_have_one_expression.py` is the
   pattern: the permitted adapter is allowlisted, the adapter is itself checked
   for delegating, and the remaining restatements are a list that may only
   shrink.

5. **The sweep PARSES. It does not grep.** This is a correction to the first
   version of this amendment, and the correction is the worked example.

   That version cited a striking finding: eleven of `dotmac_erp`'s twenty-nine
   value-type branches were in HR employee filtering, so `SettingValueType` had
   been borrowed as a general "what kind of value is this" vocabulary. **That
   was false.** HR's `value_type` is
   `Literal["uuid", "date", "string", "enum", "bool"]` — an unrelated
   filter-field type that happens to share an attribute name. A grep for
   `value_type ==` matched it, and a design narrative was built on the match.

   The same grep-shaped sweep under-reported twice more within the hour, in
   `dotmac_sub`: it missed setting specs declared through a
   `build_*_specs(setting_spec)` CALLABLE rather than a literal constructor,
   which caused a duplicate spec to be registered and every test to fail at
   import; and it missed `from ... import DEFAULTS as _ALIAS`, because the
   alias was searched for instead of the name.

   Three misses, one cause: a pattern was guessed at where the question needed
   the syntax tree. `scripts/restatement_sweep.py` is the sweep, and a sweep
   reported without it is an estimate.

   A detector can also over-report, which is the same failure one sign away.
   The first parsing run counted 139 "local cache keys" in Sub — nearly all of
   them permission codes like `"settings:manage"` — and counted a
   `query().filter().filter()` chain as three reads. A finding is only a
   finding once the detector has been checked against what it matched.

This does not gate adoption on a clean sweep — a product with restatements
still adopts. It gates adoption on a KNOWN sweep, so the cutover's cost is
visible before it is paid rather than discovered one CI run at a time.

### Decision amendment — 2026-08-10b (audit scope, and the communication capability map)

The 2026-08-08 amendment made ERP and Sub mandatory *sources*. Executing the
first audit it required — Template Studio's, in
`docs/inventories/template-studio-source-audit.md` — exposed a gap in that
amendment and produced a ruling large enough to record here.

Dated `10b` because a separate amendment landed the same day (*sweep for
restatements before a cutover*); the two are independent and neither supersedes
the other. See the closing note for how this composes with the 2026-08-09
build-once ruling rather than restating it.

#### 5a. Product-first sources the implementation; ADR-0003 sets the scope

Product-first extraction answers *"who already built this well?"*. It does **not**
answer *"what should the foundation own?"* — ADR-0003 does, and it makes this repo
the foundation for new SaaS, dedicated, self-hosted, OEM and single-tenant
products that are not downstream of ERP or Sub. An audit run as *"which product's
code do we port?"* silently lets the two existing products set the ceiling on what
a capability may become.

Two consequences bind every future audit:

1. **"These implementations do not share a contract" never establishes "this
   capability has no contract."** The first is a finding about implementations and
   justifies rejecting a particular package shape. It does not license concluding
   the capability is unextractable, and it does not license moving the capability
   to an out-of-scope list.
2. **A capability leaves the foundation's scope only when it encodes genuine
   product-domain meaning** — ERP finance/HR entity semantics, Sub
   subscriber/network policy. It does **not** leave scope because only one product
   has built it so far, because it is entangled with one product's vocabulary (the
   ADR-0008 split applies: generic mechanism, product-declared vocabulary), or
   because it carries heavy native dependencies (that argues for an optional
   `core=False` module with its own extra, not for exclusion).

An audit concluding *"no single owner qualifies"* has found that the unit was
drawn wrong, not that the work stops. It must then decompose to one decision per
owner and re-run the sourcing question per owner.

#### 5b. Template Studio narrows to one decision

The audit disqualified the package's `kind ∈ {notification, document}` merge on
evidence, not preference: the two products disagree on placeholder language,
engine class, identity dimension and missing-variable policy, and the package's
double-brace syntax is the syntax Sub rejects at save time after it leaked a
literal `{{amount}}` to customers. Accordingly:

- `kind=document` is dropped from the package.
- The renderer re-bases on Sub's contract — single-brace, closed
  per-send-context allowlist validated at save time, unresolved-placeholder
  suppression on the live path — carrying Sub's seven renderer tests as parity
  proof.
- The three things neither product has are kept: tenant-scoped RLS from
  migration 1, the `manage`/`publish` permission split, and immutable versioning
  (ported from ERP's proven *workflow-rule* version history, not designed fresh).
- Its contract is now **what the message says**. It renders and returns
  `(subject, body)`. It does not decide whether to send, to whom, or over what.

This supersedes the section 2 ownership map's implicit reading that Template
Studio owns tenant document templates. It remains a module, not a kernel facility.

#### 5c. The communication capability map — five owners, not one

The capability a new product needs on day one is author → render → decide
eligibility → route → send → prove. It decomposes into five foundation owners
plus the products' own domain, and is mostly already built:

| Owner | The one decision it owns | Qualifying source |
|---|---|---|
| `dotmac-template-studio` | what the message says | Sub's renderer |
| consent / suppression | may we contact this address on this channel | Sub's `CommunicationSuppression` |
| channel policy | which channels carry this class of message | Sub's matrix, mechanism only |
| delivery / outbox | queue, retry, provider result, dedupe | Sub's `Notification` + `NotificationDelivery` |
| document generation | render → PDF → durable record | ERP's `DocumentGeneratorService` + `GeneratedDocument` |
| product domain | which messages exist and what they mean | stays in ERP and Sub |

Three of these were nearly mis-scoped as product-specific, and the rulings that
keep them in scope are the point of 5a:

- **Consent is a legal decision, not an ISP one.** Erasure, hard bounce and
  unsubscribe apply to any product that sends email, including an ERP that only
  ever sends invoices and offer letters. Sub's model already holds the hard part:
  scope `marketing` vs `all`, keyed on the **address** rather than the person,
  with unsubscribe never permitting suppression of someone's invoice. A second
  product reimplementing that is a build-once violation whose failure mode is a
  billing incident, not a cosmetic drift.
- **`GeneratedDocument` is the only concrete implementation of the SOT criterion
  *"every projection has provenance"* in either product** — `content_hash`,
  sanitised `context_snapshot`, `superseded_by` lifecycle. That is foundation
  material.
- **Channel policy generalises at the mechanism, not the vocabulary.** It is the
  weakest of the three and its extraction is the least proven; it may not be
  taken before its own dossier shows otherwise.

**Each owner needs its own extraction dossier.** Bundling them under Template
Studio's would repeat exactly the merge error this audit was commissioned to
catch, and the 2026-08-08 amendment's per-distribution dossier requirement
already forbids it.

**Sequencing is a constraint, not a preference: consent must be answerable before
delivery exists.** Otherwise the first product on the new stack sends without a
suppression check and the legal gate is retrofitted behind live traffic.

This amends, rather than relaxes, section 5 and the 2026-08-08 amendment, and it
composes with the 2026-08-09 one as a third axis rather than restating it:

- **Section 5 governs SIMILARITY** — two things that look alike are not one
  contract.
- **2026-08-09 governs SAMENESS** — one thing with one correct encoding must not
  be built twice.
- **2026-08-08 governs SOURCING** — once a unit may be shared, a qualifying
  product implementation is where it comes from.
- **5a governs SCOPE** — what the foundation should own is set by ADR-0003, and
  an audit may not narrow it by reasoning from the products' current shapes.

The Template Studio audit is a case where all four bite in sequence: § 5 refused
the two-kind merge, 5a refused the conclusion that the capability was therefore
unextractable, 2026-08-09 kept consent and document provenance in the shared
layer, and 2026-08-08 named Sub and ERP as the sources for the pieces that
survived.

#### 5d. What the per-owner dossiers changed — six owners became four

5c named six owners and required a dossier per owner before extraction. Three of
those dossiers have now been taken, and two of them shrank their own owner. The
map named the DECISIONS correctly; two of them turned out to already have homes.

| § 5c owner | Dossier outcome | Where it landed |
|---|---|---|
| template studio | unchanged | `dotmac-template-studio` 0.2.0a2 |
| consent / suppression | unchanged | `dotmac_kernel.consent` (0.1.0a34) |
| delivery / outbox | **shrank** — not a queue | `dotmac_kernel.delivery` (0.1.0a35) |
| channel policy | **dissolved** — it is a setting | `dotmac_kernel.channel_policy`, a reader over one `SettingSpec` |
| document generation | not yet taken | — |
| product domain | unchanged | stays in ERP and Sub |

**Delivery is not a queue.** `docs/inventories/delivery-outbox-sources.md` found
Sub's `Notification` and the kernel's `OutboxEvent` to be the same machine built
twice — status, attempts, backoff, worker lease, stale reclaim and dead-letter all
appear in both. Sub's predates the kernel's. ADR-0014 already routes
non-transactional effects to the outbox, and sending an email is the canonical
one. The delivery owner therefore reduces to a receipt table, a provider seam,
and the feedback loop below.

**Channel policy is a setting, not a subsystem.**
`docs/inventories/channel-policy-sources.md` found Sub stores its entire policy as
one JSON document in `domain_settings` and resolves it through ordinary settings
precedence. The kernel already owns the resolver, the scope chain, ADR-0012
inheritance, change history and the admin surface; what was missing was a typed
reader. This owner is therefore **folded into settings** rather than standing
alone — the reason it read as "the weakest of the three" in 5c was that the unit
was drawn one size too large, not that the capability was doubtful.

Sub's legacy per-event shadow setting (`notification_event_<code>_channels`),
which its own docstring calls legacy, is deliberately not ported: a second writer
for one decision on day one is the parallel authority the source-of-truth
standard exists to prevent.

#### 5e. Consent needs a writer, or it answers "yes" forever

Taking the delivery dossier surfaced a defect in the source that changes what
consent is worth in production, and it is recorded here because it is a
FLEET-WIDE lesson rather than a Sub bug.

Verified in `dotmac_sub` at `5d6f115b7`: `DeliveryStatus.bounced` is declared and
never assigned; `SuppressionReason.bounce` and `.complaint` have zero call sites;
exactly one site in the product writes a suppression at all — the campaign
unsubscribe link. Sub's consent ledger is therefore **unsubscribe-only in
practice**, and the `all` scope that protects transactional delivery is designed,
documented and never populated by anything automated.

**The ruling: a consent ledger with no automated writer is not a consent
mechanism.** Any product adopting the consent facility must also wire the
provider feedback loop — a hard bounce or spam complaint suppresses the address
with scope `all` — or it has a table that answers "yes, send" forever. The kernel
now ships that loop (`dotmac_kernel.delivery.record_receipt`), so the obligation
is to route sends through it rather than to reimplement it.

The corollary for adapter authors is stated where it can do damage: only a
PERMANENT failure is `bounced`. A soft bounce recorded as `bounced` permanently
stops that customer's invoices, and the kernel cannot classify the difference
because every provider spells it differently.

### Decision amendment — 2026-08-12 (a second consumer is evidence, not permission)

Section 5 point 1 requires "two independent consumers of the same CONTRACT" as a
condition of extraction, and the 2026-08-08 amendment's point 4 requires a
dossier naming "two contract consumers". Read as written, both make two
consumers a **prerequisite for sharing at all**.

That is now amended, following an owner ruling on 2026-08-12:

> A second consumer proves reuse and constrains generalisation; it does **not**
> determine whether a coherent capability belongs in a module.

The contradiction was found by a build rather than a reading. The release
catalogue is a vendor-side capability whose tables REVOKE `app_user` — a product
data plane is disqualified as a consumer **on purpose**, because it must learn
which artifact to run from a signed licence or a deployment plan rather than by
reading the vendor's catalogue. Its second consumer cannot exist until a second
vendor or OEM control plane does. The same holds for the fleet, fleet-health,
support-access and licence-issuance units. A rule requiring two consumers before
sharing does not delay those modules; it forbids them, and pushes the capability
back into the single assembly this programme exists to thin.

**Three dossier states replace the two-state model. All three permit a shared
module.** The state records the evidence level, never the placement permission:

| State | Means | Requires |
|---|---|---|
| `audit-complete` | The inventory ran and the unit was drawn deliberately. Nothing has adopted it. | An audited `source_mode`, **at least one concrete** candidate consumer, and **zero** contract consumers. |
| `adopted` | One real consumer is on the contract and its first cutover is complete. | An audited `source_mode` and **exactly one** contract consumer. |
| `reuse-proven` | Two or more independent consumers exercise the same contract. | An audited `source_mode` and **two or more** contract consumers. |

`approved` becomes `reuse-proven`. The old name described a permission, which is
precisely what the state no longer grants; keeping it would leave the amended
rule readable as the rule it replaces. Grandfathered pre-rule packages keep
their separate, exact debt map — ADR-0018 requires "grandfathered" to stay
distinguishable from "reviewed and correct", and adding a third correct state
does not change that.

**Why the candidate count is lowered to one rather than dropped.** A dossier with
no named candidate is a package built for nobody, which is the speculative
extraction section 5 exists to stop. One *concrete* candidate — an assembly that
exists and will consume it — is the smallest claim that still carries evidence.
Two remain required to reach `reuse-proven`.

**Why `adopted` is a distinct state, not a loosened `audit-complete`.** The
two-state model let a package with one live consumer keep describing itself as
"nothing has adopted it yet". That is a false statement about the fleet, and it
hides the transition ADR-0017 calls the scarce resource — the first cutover.
Splitting the state surfaces the first adoption in the dossier the moment it
happens, and makes a package that has a consumer while claiming none a build
failure rather than a stale comment.

The ratchet holds in both directions: a package may not sit in a state weaker
than its evidence supports (one consumer forces `adopted`, two force
`reuse-proven`), and may not claim a state stronger than its consumers prove.

### Decision amendment — 2026-08-13 (the presentation system is adoption-led, and may need no theme package)

**The presentation system is completed by a sequence of adoption-led releases,
not by a "design-system completion" project.** Each release earns its place by
being consumed. This amendment states the authorities and the standing rules;
the delivery sequence is an implementation plan, not an architecture decision,
and lives in `docs/superpowers/plans/2026-08-13-presentation-system-programme.md`.

This amends § 1. Naming four concepts was correct; it is NOT a commitment to
build a package per concept. **A complete presentation system may legitimately
ship no structural theme package at all**, if tokens and brand data cover every
real deployment. `ThemeManifest` is therefore not a deliverable. Its trigger is
two deployments needing the same structural presentation difference that tokens
cannot express; until that exists, building it manufactures an authority nobody
asked for — the same failure ADR-0017 names on the kernel side.

#### Composition, and where each authority sits

```text
product / kernel resolver
        │ resolved brand data
        ▼
assembly adapter → dotmac_ui.BrandOverride → same-origin brand CSS
                                             │
dotmac-ui defaults → trusted theme defaults → brand overrides

product templates → published dotmac-ui component contracts
```

- **`dotmac-ui`** owns presentation behaviour: tokens, component markup and CSS,
  and the accessibility contract.
- **Product or kernel services** own brand RESOLUTION. `dotmac-ui` generates CSS
  from resolved data; it never reads a database and never decides precedence.
- **Themes** supply trusted, deployment-static presentation.
- **Brands** supply runtime data.
- **Assemblies** compose all four, and are the only layer permitted to know
  about more than one of them — which is what keeps the dependency direction of
  § 2 acyclic while brand data flows the other way.

The cascade order is fixed: `dotmac-ui` defaults, then trusted theme defaults,
then resolved brand overrides. A product compatibility mapping may occupy the
theme layer temporarily; it may not become a permanent fourth authority.

#### Tenant-supplied CSS is retired, and its removal is not gated

**No tenant may supply CSS, Jinja or JavaScript.** Brand customisation is an
allowlisted token set (D8). A regex denylist is the wrong shape for the problem:
it must enumerate every dangerous construct, while an attacker needs one it
missed.

Retiring such a surface is a **security correction to an existing surface, not a
new facility**, so ADR-0017 does not gate it, and it does not wait for the
feature that replaces it. Retirement is a WRITE-time rule: a value already
stored is data a newer reader ignores, not an invalid setting, and treating it
as invalid would silently degrade the whole record it sits in.

#### Evidence, not permission

Component reuse is evidenced on the ladder this ADR's 2026-08-12 amendment
already defines (`audit-complete` → `adopted` → `reuse-proven`), and evidence is
tracked **per contract slice**. A package may be `reuse-proven` for one contract
— its tokens and compiled assets — while a second contract it also publishes,
such as the component library, has no consumer at all. A dossier that reports
one state for a package publishing two contracts overstates the weaker one.

Consumption by the assembly that OWNS a package is reference proof and never
closes § 5's gate for it. The independent consumers are products, and which
products they are is a sequencing question for the implementation plan, not a
decision this ADR should pin.

#### Completion criteria

The presentation system is complete when: no tenant can supply executable CSS,
Jinja or JavaScript; DotMac and neutral brands run from identical source;
light/dark and the critical facets pass WCAG 2.2 AA; no brand stylesheet or
asset can leak across tenants; every shared component has two RELEASED product
consumers and its superseded local owners deleted; every theme that exists is
installed, exact-pinned, digest-addressed and compatibility-checked; wheels
prove their assets and templates and render on a clean host; and the control
plane manages desired brand and theme state through product APIs, never by
writing to a product database.

Note what is absent: "the component library is finished" and "a theme package
exists" are not criteria. Neither is a measure of the system working.

### Decision amendment — 2026-08-14 (a fourth classification: the stateless protocol adapter)

`EXTRACTION.toml`'s `classification` had three values — `universal-facility`,
`presentation-foundation`, `optional-module` — and all three describe something
a product **installs**. A distribution that speaks an external protocol and
holds nothing fits none of them, and calling it an `optional-module` is not a
harmless approximation: it tells a reader to look for a `ModuleManifest`, a
`mod_*` namespace, a migration lineage and a release-allowlist entry that do not
exist, and it invites the dual-plane and namespace gates to ask questions the
package cannot answer.

`stateless-protocol-adapter` is added for a distribution that a product
**calls** rather than installs. `dotmac-auth-oidc` is the first.

**The classification is GOVERNED, not merely accepted.** A string in a valid-set
is a label; the four properties below are what the word has to mean, and each is
checked generically against any package that claims it — not against a named
package:

1. **No `ModuleManifest`.** Nothing to install, so nothing declares itself
   installable.
2. **No migration lineage.** No `migrations/` tree, and no `short_code` or
   `migration_prefix` (hard rule 14 — stateful and stateless are the only two
   coherent shapes, and this is the stateless one).
3. **No namespace allocation.** No row in `MIGRATION_OWNER_LEDGER`. An
   allocation is permanent once added, and one made for a package that will
   never own a schema is a lie the ledger cannot later retract.
4. **No persistence import.** No ORM, no database driver. A package that grew
   one would have become a module without changing its dossier.

The counterpart matters as much: a package classified any OTHER way must not be
mistaken for this one. The check is keyed on the DECLARED classification, so a
stateful module is simply out of scope rather than accidentally held to a rule
it should fail.

Enforced by `tests/architecture/test_product_first_extraction.py`
(`test_a_stateless_protocol_adapter_holds_no_persistence`), with the sensitivity
proof ADR-0018 requires: the checker is a pure function over a synthetic package
tree, shown to fire on a planted manifest, a planted migrations directory and a
planted ORM import, and shown NOT to fire on a conforming one.

### Decision amendment — 2026-08-15 (a legacy source and its module shadow, disambiguated)

D1 says that until an extraction's cutover is proven, "the source product and
the candidate module are simply not composable in one database." That sentence
is right about the danger and too blunt about the remedy, and a real adoption
found the gap.

The vendor control plane installs `dotmac-entitlement-allocation` in **shadow
mode**: the module's lineage is composed so its schema and migration path are
exercised, while the vendor-local writer stays authoritative until a data
preflight proves every historical row maps to a product. Both sets of tables
therefore exist at once — and because the legacy tables were named `allocations`
and `allocation_entries`, exactly what the module owns in `mod_ealloc`, the
composed live-catalog audit correctly refused the database.

Refusing was right. The two decisions collided because **the names were
ambiguous**, not because shadowing is wrong.

#### What stays forbidden

**Same-named legacy and module tables must not coexist.** `public.allocations`
beside `mod_ealloc.allocations` is refused, and no exemption list may permit it.
This is the original collision class D1 exists to prevent: two tables with one
name, where a mis-set `search_path`, a hand-written query or an ORM mapping
reads the wrong rows. A composition that requires a reader to know which
`allocations` was meant is already broken, whatever the migration state.

#### What is now permitted, narrowly

A **disambiguated** legacy table may coexist temporarily with an **empty,
non-authoritative** module shadow, provided every one of these holds:

1. **The legacy table is renamed, not the module's.** `public.allocations`
   becomes `public.legacy_entitlement_allocations`. The module keeps the clean
   name it will own after cutover, so the end state needs no second rename and
   no reader learns a name that is about to change again.
2. **The rename is a new migration.** The original revision is never edited: it
   already ran in production, and a lineage that rewrites its own history cannot
   be replayed or audited.
3. **Exactly one authoritative writer**, and it is the legacy one. The module
   shadow stays empty and non-authoritative until cutover. Dual-write is not
   shadowing; it is two owners.
4. **No foreign key crosses the planes**, in either direction (ADR-0023 § 4).
   An FK is the one crossing the database itself would enforce, and therefore
   permit.
5. **Every row is preserved.** A rename moves rows; it does not drop and
   recreate.
6. **A named cutover gate and a retirement gate.** The shadow is temporary by
   construction: the dossier records what must be true to cut over, and what
   retires the renamed legacy table afterwards. A shadow with no retirement
   condition is a permanent second copy wearing a temporary label.

#### Why the rename is not a naming trick

Renaming to make a gate pass would be exactly the sort of evasion ADR-0018
forbids. This is the opposite: the ambiguity was itself the defect. Before the
rename a reader cannot tell which `allocations` a query means; after it, the
legacy table says in its own name that it is legacy and scheduled to go. The
gate goes green because the database became honest, not because the check was
weakened.

#### Enforcement, and what enforcement does not reach

**Condition 1 is machine-checked, by the audit that already exists.**
`dotmac_kernel.migrations.catalog.audit_snapshot` reports
`host_schema_squatters` — a module's declared tables found in `public` — and
that is precisely the ambiguity this clarification forbids. It is the check that
refused the vendor database, so it is the one the tests exercise. A second,
parallel predicate was written first and deliberately removed: test-only code
that duplicates a production rule proves nothing about production, and leaves
two definitions to drift apart.

Its sensitivity proof is the pair, not the pass. The audit is shown to refuse
the exact composition that failed in the vendor control plane, and to accept the
same declared tables once the legacy side is renamed — so a squatter check that
degraded to silence fails the suite rather than passing it quietly.

**The other five conditions are NOT machine-checked here, and pretending
otherwise would be worse than saying so.** They are properties of one adopter's
database and cutover, not of a composed manifest, so each names the gate that
owns it:

| Condition | Owning gate |
|---|---|
| One authoritative writer; the shadow takes no writes | the adopter's writer-retirement ratchet (ADR-0018, two-directional), which counts writers and fails when the count rises |
| The module shadow stays empty | the adopter's cutover preflight, which asserts a zero row count before authority moves |
| The rename is a new migration, never an edit | the composed migration gate — an edited revision changes a hash that already ran |
| Every row preserved | the adopter's data preflight, which reconciles counts across the rename |
| No cross-plane foreign key | ADR-0023 § 4, enforced by the kernel gate for FKs whose SOURCE is inside the module schema — a product-owned link table in `public` remains *unmonitored rather than exempt*, and that gap is ADR-0023's own recorded follow-up |
| A named cutover and retirement gate | the module's `EXTRACTION.toml` — `first_cutover`, `shadow_and_drift`, `local_copy_retirement` — which is reviewed, not executed |

Recording the split matters more than closing it. A reader who believes all six
are enforced will skip the five that are not, which is exactly how a shadow
becomes permanent while every check stays green.

## Consequences

- F1–P1 have fixed vocabulary. "Module", "theme", "brand", and "facet" mean one
  thing each, and a design that blurs two of them is rejected on sight rather
  than debated per PR.
- Some duplication survives F0 on purpose. The extraction rule is a brake, and it
  will feel like one; the alternative is a shared component library with no owner.
- Tenant custom CSS acquires a stated retirement path, which is a commitment to
  break something currently supported. The compatibility seam and its removal
  gate are the price of the CSP and legal-identity invariants.
- The two version axes (module contract, UI contract) mean two compatibility
  matrices to maintain. That is the cost of letting the UI system evolve without
  a fleet-wide module re-declaration.
- Nothing in this ADR is implemented by it. It changes no runtime behaviour; it
  constrains what the following steps may build.

## References

- `docs/adr/0003-unified-deployment-profiles.md`
- `docs/superpowers/reviews/2026-07-18-module-control-plane-directive.md`
- `docs/inventories/README.md`
- `docs/inventories/module-extraction-sources.md` (the product-first dossier index)
- `docs/inventories/template-studio-source-audit.md` (evidence for the 2026-08-10
  amendment)
- `packages/dotmac-kernel/COMPATIBILITY.md` (kernel public surface + versioning)
