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
  they are not Template Studio content.
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

3. **`dotmac_sub` has three writers for logo/favicon/colours** —
   `brand_profiles`, the `comms` settings rows, and
   `web_system_company_info.py` (which writes `domain_settings` by raw SQL,
   bypassing its own spec registry), with
   `sync_platform_brand_from_legacy_settings` as an unfinished one-way
   migration. Consolidating on `BrandProfile` without finishing that migration
   inherits a dangling legacy writer — a direct violation of SOT-complete
   criterion 5.

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
- `packages/dotmac-kernel/COMPATIBILITY.md` (kernel public surface + versioning)
