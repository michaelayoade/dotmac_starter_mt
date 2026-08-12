# As-built inventories (F0)

These documents record **what exists today**, not what should exist. They are the
evidence base for ADR-0006 (white-label product foundation) and the input to the
F1–P1 programme steps.

Read them with two cautions:

1. **Facts go stale.** Each file carries an as-of date and the commit it was taken
   at. An inventory that disagrees with the code is wrong, not authoritative —
   re-run it rather than trusting it.
2. **An inventory is not a mandate.** Recording that two products implement the
   same-looking table does not authorise extracting a shared component. ADR-0006
   § "The extraction rule" governs that: same contract, named owner, migration
   path — similarity alone is explicitly insufficient. Once extraction is
   approved, the 2026-08-08 product-first amendment additionally requires the
   qualifying product implementation and tests to be the starting point rather
   than rebuilding the behaviour beside them.

Authority hierarchy is unchanged: `docs/ARCHITECTURE.md` is as-built truth for
this repo, `docs/adr/` holds decisions, and these inventories are characterization
across repos.

## Index

| Inventory | Scope | File |
|---|---|---|
| Starter surfaces | `dotmac_starter_mt` routes, UI surfaces, templates, CSS/static, navigation, brand leakage | `starter-surfaces.md` |
| Sub surfaces | `dotmac_sub` portals, templates, CSS/static, navigation, branding mechanisms, kernel adoption | `sub-surfaces.md` |
| ERP + vendor control plane surfaces | `dotmac_erp` and `dotmac_vendor_control_plane`, same shape | `erp-vendor-surfaces.md` |
| Branding and settings | all three repos: brand sourcing/precedence, the brand-field union, tenant custom CSS and its CSP consequence, settings facilities and specs | `branding-settings.md` |
| Migrations and table collisions | all four repos: Alembic topology, revision-ID and table-name collision analysis, namespacing, RLS coverage | `migration-collisions.md` |
| Idempotency sources | ERP's 3 and Sub's 3 idempotency mechanisms, the kernel baseline they extend, defects not to carry forward, and the tests available to port | `idempotency-sources.md` |
| Tenancy characterization | ERP's 398-table `organization_id` catalog and its two-layer isolation, Sub's already-provisioned operator tenant, the GUC divergence, and why ERP's RLS coverage is unmeasurable from source | `tenancy-characterization.md` |
| Product-first module sources | shared distributions plus the ERP/Sub code and tests that must be audited before shared implementation | `module-extraction-sources.md` |
| Consent and suppression sources | Sub's complete do-not-contact ledger, ERP's total absence of one, the marketing/transactional scope rule, defects not to carry forward, and why the extracted owner belongs in the kernel | `consent-suppression-sources.md` |
| Delivery and outbox sources | why Sub's notification queue is the kernel outbox built twice, the bounce→consent feedback loop that exists in neither product, and what a delivery owner is left owning | `delivery-outbox-sources.md` |
| Channel policy sources | why the § 5c channel-policy owner resolves into a settings document with a typed reader rather than a fifth subsystem | `channel-policy-sources.md` |
| Template Studio source audit | ERP document templates vs Sub notification templates vs the package across identity, versioning, publication, placeholder syntax, scoping, seeding, delivery, traceability, permissions and cutover shape — the audit `EXTRACTION.toml` blocks on, plus the six-owner capability map it resolves to | `template-studio-source-audit.md` |
| UI surfaces | the ERP/Sub `src/css/` fork, drift file by file, class usage, the button vocabulary — **plus the 2026-08-11 correction that only ERP still runs its copy**, which supersedes the promotion steps | `ui-surface-inventory.md` |
| Ticket module sources | Sub/CRM/ERP ticket implementations, why their vocabularies cannot merge and why that does not block the capability, the product-neutral core vs the per-product variant seam, the lifecycle-class mechanism that makes the vocabulary open, and the subject-linkage decision | `ticket-sources.md` |
| Sub lineage dispositions | the ten kernel/Sub table-name collisions measured against released a40, grouped by what each actually needs — stamp, adopt, reconcile, or union — plus the four ways the measurement was wrong first and the self-check that caught them | `sub-lineage-dispositions.md` |
| Application-portfolio sources | the two mechanical sweeps proving no repository owns a tenant's connected-application portfolio, why the vendor CP's `allocations` is a different unit despite being the nearest neighbour, and the concept-to-owner table the directory module must not absorb | `application-portfolio-sources.md` |
| Federated identity (OIDC) sources | ERP's 409-line Authorization Code + PKCE implementation, its `FederatedIdentity` binding and four boundary tests, the algorithm allowlist and the refuse-unlinked-identity rule that make it the parity suite — plus the unresolved question of whether `OIDC_ENABLED` is true in any production deployment, which decides `product-first` vs `greenfield-after-inventory` | `oidc-sources.md` |
| Party module sources | CRM/ERP/Sub identity models audited for a composable party module: why Sub is the only qualifying source and ERP/CRM are requirement inputs, the `party_roles` name-vs-meaning collision that blocks extraction, the tenancy delta and the one unique constraint that is a cross-tenant leak, and why gate 5 (Sub's own cutover) is currently binding | `party-module-sources.md` |
| `audit_events` disposition | the measured Group E union and implemented R1 integration contract: retained forensic columns, distinct event/persistence time, the closed polymorphic actor identity, immutable audit semantics, coordinated integration branches, and dependency-ordered releases | `audit-events-disposition.md` |
| **Fleet decomposition matrix** | the standing ERP/CRM/Sub/vendor-CP capability matrix PRODUCT_VISION § 2 requires — owner, competing writers, consumers, migration owner, authority overlap, target layer and retirement condition per capability, plus the frozen duplication baseline and the sequencing it implies (duplication orders the work; every domain still resolves to kernel, UI, or a Starter module). The vendor control plane joined on 2026-08-12 as a **consumer assembly, not a fourth monolith**, and brings the six capabilities the matrix cannot measure because nothing in the fleet implements them | `fleet-decomposition-matrix.md` |
| **Entitlement allocation sources** | the vendor CP's `allocations` audited as the single qualifying source, and the three couplings to `contracts` the extraction must cut — FK, direct model read, event-type literal — resolved by a typed `ContractSnapshot` port. Records the finding that cutting them is the FIRST step rather than a consequence, which makes the extraction A2-neutral | `entitlement-allocation-sources.md` |
| **Fact-level decomposition** | declared fact ownership extracted from Sub's SOT registry and ERP's `sot_relationships.py`, plus a **direct-import reachability heuristic** over it — explicitly not an ownership measure — and the 28 duplicated tables with no detected edge, held as a manual triage queue | `fleet-fact-level-decomposition.md` |

## Cross-repo scale, at a glance

Captured 2026-08-02. Counts are enumerated mechanically; see each file for method.
Persisted-state duplication is measured separately and kept frozen in
[`fleet-decomposition-matrix.md`](fleet-decomposition-matrix.md).

| | starter | Sub | ERP | vendor CP |
|---|---|---|---|---|
| Routes | 65 | 3,183 | ~2,278 effective (3,015 decorators) | 40 |
| Templates | 34 (all kernel package data) | 718 | 865 | 0 |
| Base layouts | 1 | — | 17 | 0 |
| CSS toolchain | Tailwind **v4 CSS-first** | Tailwind **v4 CSS-first** | Tailwind **v3.4.19 + JS config** | none |
| Design tokens | 27, colour/font/animation only | 90 role-named (+82 dead) | 77, **value-named** | none |
| Navigation | manifest-derived | hardcoded per portal | 100% hardcoded | manifest-derived |
| Branding | **stub — cannot show a logo or colour** | 4-layer engine, platform/reseller/organization | 25-column model + runtime CSS generator | none (kernel's) |
| Brand fields consumed | ~2 of 74 | ~35 | ~40 | 0 |
| Competing settings stores | 3 (+1 unwired) | 17 | 15 | — |
| Tenant raw CSS | sanitised, preview-only | **none** (token pipeline) | **verbatim, on a public page** | — |
| CSP | strict, 10 directives | **none at all** | 1 directive, `unsafe-inline` + CDN | kernel's |
| Kernel adoption | is the kernel | `0.1.0a8` pinned, 0 imports (slice S2) | **none at all** | `0.1.0a8`, 9 modules, allowlist test-enforced |
| Tenant isolation | RLS + dynamic catalog audit | no `tenant_id`, no RLS by design | point-in-time sweep migrations only | platform catalog, grants |

Three readings that matter for the programme:

1. **The reference implementation is not the most advanced one.** Sub already has
   the brand-precedence model and the role-named token vocabulary the foundation
   needs; the starter has neither. ADR-0006 § 3 therefore adopts Sub's model
   rather than inventing one, and U1's token vocabulary should start from Sub's
   `design-system.css`, not the starter's 27 colour tokens.
2. **ERP is the outlier on every axis** — no kernel, no import boundary, an older
   CSS toolchain, value-named tokens, and its own licence scheme. It is a
   greenfield adoption target, not a repo with fork drift to reconcile.
3. **Scale is asymmetric by more than an order of magnitude.** Any "extract the
   shared component" plan sized against the starter's 34 templates will be wrong
   by ~20–25× against Sub's 718 or ERP's 865.

## Coverage of the F0 deliverable

| F0 deliverable | Where |
|---|---|
| Foundation ADR | `docs/adr/0006-white-label-product-foundation.md` |
| Module/package ownership map | ADR-0006 § 2 (target) + the per-repo inventories (current owners) |
| Complete UI-surface inventory | `starter-surfaces.md`, `sub-surfaces.md`, `erp-vendor-surfaces.md` |
| Template, CSS, static-asset, navigation inventory | same three |
| Branding/settings inventory across starter, ERP, Sub | `branding-settings.md` |
| Migration/table collision inventory | `migration-collisions.md` |
| Kernel-restatement sweep (adoption recon) | `kernel-restatement-sweep.md` |
| Supported product-profile matrix | ADR-0006 § 4 |
| Brand precedence decision | ADR-0006 § 3 |
| Module vs theme vs product-facet terminology | ADR-0006 § 1 |
