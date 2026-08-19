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
| Reusable map UI sources | Sub live-map/playback, CRM degraded/list behavior and ERP geofence editing; narrows the portable contract to an accessible provider-neutral frame and records every provider, viewport, data and domain seam that stays product-owned | `map-ui-sources.md` |
| Ticket module sources | Sub/CRM/ERP ticket implementations, why their vocabularies cannot merge and why that does not block the capability, the product-neutral core vs the per-product variant seam, the lifecycle-class mechanism that makes the vocabulary open, and the subject-linkage decision | `ticket-sources.md` |
| Billing sources | the money-domain code across all four repos — Sub's 66 tables / ~74k LOC / 174 test files against ERP's 32 AR/AP tables and the kernel's zero, what the kernel already supplies, ten missing capability areas plus the external lineage gate, ADR-0020's receivables/module rulings, and the non-conformances the extraction must not carry forward | `billing-sources.md` |
| Billing extraction dossier | the `dotmac-billing` dossier fields, the dual-plane persistence design, an eight-slice Sub retirement inventory with the ratchet proving each, and for every recorded non-conformance the corrected revision-1 shape and the shadow measurement that must precede cutover | `billing-extraction-dossier.md` |
| Billing parity tests | the verified Sub and ERP tests the extraction must preserve, each with the behaviour it proves and the defect it guards — including which port **split** between billing meaning and Integrator transport, which port with an **inverted** assertion, and the areas with no adequate source test | `billing-parity-tests.md` |
| Subscription sources | the A2b Vendor-CP/Sub audit of offers, prices, recurring contracts, cadence, proration and obligations, **revised 2026-08-15** for ADR-0030: Sub's ADR-0007 stack is shadow-only while the legacy money path holds the defects, the cadence cursor has eleven writers and no canonical owner, recurrence is not repairable from the contract, and Vendor CP is **not** a source of structural immutability — it grants `UPDATE, DELETE` on `offer_versions` to the online API role | `subscriptions-sources.md` |
| Sub lineage dispositions | the ten kernel/Sub table-name collisions measured against released a40, grouped by what each actually needs — stamp, adopt, reconcile, or union — plus the four ways the measurement was wrong first and the self-check that caught them | `sub-lineage-dispositions.md` |
| Application-portfolio sources | the two mechanical sweeps proving no repository owns a tenant's connected-application portfolio, why the vendor CP's `allocations` is a different unit despite being the nearest neighbour, and the concept-to-owner table the directory module must not absorb | `application-portfolio-sources.md` |
| Federated identity (OIDC) sources | ERP's 409-line Authorization Code + PKCE implementation, its `FederatedIdentity` binding and four boundary tests, the algorithm allowlist and the refuse-unlinked-identity rule that make it the parity suite — plus the unresolved question of whether `OIDC_ENABLED` is true in any production deployment, which decides `product-first` vs `greenfield-after-inventory` | `oidc-sources.md` |
| **External identity sources** | the 2026-08-14 six-repository sweep of the TWO capabilities routinely confused — the OIDC protocol client and the local verified-subject→local-identity binding — finding that ERP owns one half (`federated_identities`, no tenant column, global `(issuer, subject)` uniqueness) and Sub the other (`authentication_bindings`, no issuer/subject at all) and **neither has both or has RLS**; supersedes `oidc-sources.md`'s open gate by finding a host-independent disqualifier (ERP's signature and claim validation are monkeypatched out of every test), which rules the OIDC package `greenfield-after-inventory` | `external-identity-sources.md` |
| Party module sources | CRM/ERP/Sub identity models audited for a composable party module: why Sub is the only qualifying source and ERP/CRM are requirement inputs, the `party_roles` name-vs-meaning collision that blocks extraction, the tenancy delta and the one unique constraint that is a cross-tenant leak, and why gate 5 (Sub's own cutover) is currently binding | `party-module-sources.md` |
| **People directory sources** | the five-repository audit for Backoffice's first vertical replacement slice: kernel Party remains identity; ERP is the qualifying source for employee lifecycle, departments, positions and temporal assignments; Sub/CRM are projection inputs; payroll/auth/attendance/GL stay outside; and the ERP-authority-retirement/Backoffice-runtime cutover is stated explicitly | `people-directory-sources.md` |
| `audit_events` disposition | the measured Group E union and implemented R1 integration contract: retained forensic columns, distinct event/persistence time, the closed polymorphic actor identity, immutable audit semantics, coordinated integration branches, and dependency-ordered releases | `audit-events-disposition.md` |
| **Kernel persisted-runtime dependencies** | the fleet audit of kernel audit/settings storage, with the platform-only `platform_audit_log.v1` ruling and a 2026-08-16 implementation addendum; tenant audit remains unnamed and settings remains the wrong prerequisite instrument | `kernel-persisted-runtime-dependencies.md` |
| **Approvals released-migration divergence** | the exact a1–a4 tag/digest census for the three meanings shipped as `ap_0001`, why the exception is closed rather than permission to edit again, and the six PostgreSQL upgrade cases that preserve rows and persistence-plane intent | `approvals-released-migration-divergence.md` |
| **Fleet decomposition matrix** | the standing ERP/CRM/Sub/vendor-CP capability matrix PRODUCT_VISION § 2 requires — owner, competing writers, consumers, migration owner, authority overlap, target layer and retirement condition per capability, plus the frozen duplication baseline and the sequencing it implies (duplication orders the work; every domain still resolves to kernel, UI, or a Starter module). The vendor control plane joined on 2026-08-12 as a **consumer assembly, not a fourth monolith**, and brings the six capabilities the matrix cannot measure because nothing in the fleet implements them | `fleet-decomposition-matrix.md` |
| **Approvals/workflow A1 source audit** | the exact 24-row decomposition of the former `governance-workflow` measurement bucket, the ERP-vs-Vendor approval behavior comparison, the `dotmac-approvals` source ruling and mandatory port deltas — accepted by ADR-0026 on 2026-08-14 with three recorded corrections (routing stays in ERP, policy codes are data, Vendor CP's cutover is a capability gain), and no package or namespace created — plus the separate automation/forms/domain/retirement dispositions | `approvals-workflow-source-audit.md` + `approval-workflow-dispositions.toml` |
| **WhatsApp/Meta connector sources** | the two fleet implementations of Meta webhook ingress (Sub's `whatsapp_runtime` + `meta_inbox_webhooks` as the qualifying source; CRM's larger `meta_webhooks` as requirement input only, being fused with CRM domain decisions), the ingress-vs-egress scope split for an INGRESS-only connector, the database coupling to remove during the port, the ingress-edge MIRROR shadow plan (Meta configures one callback endpoint and per-WABA overrides move it rather than duplicate delivery, so dual subscription is not available), and the BLOCKING finding that `modes` is decorative across the whole SPI — ingress and poll have no hook at all and delivery is invoked without checking the mode | `whatsapp-connector-sources.md` |
| **WhatsApp/Meta ingress connector dossier** | the PRE-REGISTERED `EXTRACTION.toml` for `dotmac-connector-whatsapp` — coordinates authorized 2026-08-15, the distribution itself still NOT authorized: the three remaining gates (the ADR-0030 § 6 amendment, Team 4's secret resolver, and a published `dotmac-integration` implementing SPI 1.1 — none exists, so no `integration_floor` is nameable and the connector lane refuses the entry), the port/not-port disposition surface by surface, the parity dispositions (the request BODIES port, the assertions do not), the fourteen findings a future connector must NOT inherit (request-digest event identity, the circular handshake, silent drops, the fallback secret that is not rotation), and the provenance of the fixture corpus in `tests/fixtures/meta_whatsapp/` | `whatsapp-connector-dossier.md` |
| **Entitlement allocation sources** | the vendor CP's `allocations` audited as the single qualifying source, and the three couplings to `contracts` the extraction must cut — FK, direct model read, event-type literal — resolved by a typed `ContractSnapshot` port. Records the finding that cutting them is the FIRST step rather than a consequence, which makes the extraction A2-neutral | `entitlement-allocation-sources.md` |
| **Fact-level decomposition** | declared fact ownership extracted from Sub's SOT registry and ERP's `sot_relationships.py`, plus a **direct-import reachability heuristic** over it — explicitly not an ownership measure — and the 28 duplicated tables with no detected edge, held as a manual triage queue | `fleet-fact-level-decomposition.md` |
| Cloud commerce owner sources | the ADR-0030 portfolio pass: a verdict-by-owner table across six repositories for the seven Cloud business owners. **Superseded in part 2026-08-15** — written before the per-owner dossiers, it wrongly rules Fulfillment product-first and credits Vendor CP with immutable publication; where it and a per-owner dossier disagree, the dossier wins | `cloud-commerce-owner-sources.md` |
| **Numbering ERP adoption slice** | the read-only adoption analysis for `dotmac-numbering`'s first candidate consumer, and the source of the defect that produced kernel a66: `allocate` writes the kernel at-most-once ledger at REQUEST time with no `PrerequisiteSpec` covering it, so an adopter running its own tenant lineage migrates cleanly and fails on the first allocation. Also the bank-statement format grammar change (`STMT2026-00001` -> `STMT-2026-00001`), which is customer-visible and undecided | `numbering-erp-adoption-slice.md` |
| **Durable timers sources** | the ADR-0030 step 6 revalidation: product-first with a source SPLIT no earlier dossier records — Sub contributes identity, generation and supersede/cancel; the CLAIMING engine already exists in `dotmac_kernel.messaging.relay` (`FOR UPDATE SKIP LOCKED`, lease reclaim, backoff, dead-letter, both planes, proven on real Postgres) and must be reused rather than re-implemented. Also: Sub's own registry declares the facility `SHADOWING`, one scheduled family has no consumer, the 200-timer fire batch is one transaction with no dead-letter, and the whole suite runs on SQLite | `durable-timers-sources.md` |
| **Billing source variance** | the ADR-0030 step 7 revalidation, and the one that changes the plan: both flagship capabilities are **not the live path in their own repository** — Sub's ADR-0007 obligation stack is `SHADOWING` by declaration and has never raised an invoice, and ERP's `coverage.py` (called "the single highest-value port in the programme") has zero references under `app/`. `source_mode` becomes greenfield-after-inventory; two of six contracts are freezable now, which unblocks `dotmac-document-rendering`; 125 float-on-money casts where the dossier records one | `billing-source-variance.md` |
| **Numbering source revalidation** | the 2026-08-15 recheck of `numbering-sources.md` against current ERP/Sub heads. Every mandatory path is byte-identical to its pin, so the diff is empty — and the dossier's *description* of that unchanged code was still wrong: Sub's credited "monotonic reconciliation" is dead code with no caller. Also: 16 ERP callsites cannot pass a business date, so the required-`reference_date` contract breaks three quarters of the cutover surface; there is not one real-database numbering test in either product; and five new ERP defects including a backdated allocation that rewinds the counter. Where it and `numbering-sources.md` disagree, this file wins | `numbering-source-variance.md` |
| Numbering sources | the ERP-base/Sub-delta verdict for `dotmac-numbering`: ERP's date-aware series model and 41-caller parity suite as the mandatory base, Sub's conflict-safe first-use and monotonic reconciliation as a mandatory port delta, and the five ERP defects — including a query-then-insert first-use race and an operator reset that can reuse a committed number | `numbering-sources.md` |
| Collections sources | Sub's live and target dunning stacks as the only qualifying source, **revised 2026-08-15** for ADR-0030: four §1 properties absent from both stacks and therefore built not ported — exposure membership, immutable policy versions, throttle-as-a-request (throttle writes RADIUS columns inline today), and a persisted owner-side refusal — plus CRM's parallel delinquency classifier and ERP's unfiltered overdue sweep | `collections-sources.md` |
| **Orders sources** | Sub as the only qualifying source (24 caller files, a `SELECT FOR UPDATE` funding gate) against ERP's 2 callers and all-mock suite and CRM's strict subset; the three findings that define the module — **no source keeps an accepted line immutable, none snapshots a price version, none has tenant isolation** — and the live authority bypass where a generic order update can manufacture funding and trigger provisioning | `orders-sources.md` |
| **Fulfillment sources** | the finding that **Sub has no saga engine** — `saga_executions`/`provisioning_step_executions` exist only in migrations and FK to `ont_units`/`olt_devices` — so the ruling is greenfield on `dotmac_kernel.providers.provisioning`, which already supplies plan/apply/observe/cancel, operation-id idempotency, resumable `PARTIAL` and a conformance kit; plus the eleven ISP participants and the one-function/three-structural-coupling seam | `fulfillment-sources.md` |
| **Domains sources** | the greenfield verdict **proven** by an eleven-repository sweep across six term families with every false positive named (the fleet has no DNS client at all), and the `TenantDomain` boundary settled by a Postgres grant rather than convention — it is a platform-plane routing catalogue that tenant-plane Domains physically cannot write | `domains-sources.md` |
| **Hosting sources** | the greenfield verdict proven by a per-term census returning zero panel/account/quota implementations, qualified by three Sub precedents the fresh module must match and Sub retains (reason-scoped multi-lock suspension, receipted consequence attempts, an observation table that is never authoritative for desired state), and the three-way suspension boundary between Sub's ISP subscription, Vendor CP's agreement column and a hosting service aggregate | `hosting-sources.md` |
| **Provider capability and conformance** | five rulings — the connector framework already exists in `dotmac-integration`, PSP is product-first with a complete audit, registrar/DNS/panel are greenfield — plus the finding that **zero Blesta code exists in the fleet**, the four capability surfaces specified as shapes, the conformance-kit obligations, and the blocker that the assembly has no secret resolver so no installation can reach `enabled` | `provider-capability-sources.md` |
| **Media observations sources** | Mkt's qualifying external campaign/group/advertisement hierarchy and daily aggregate metric behavior; CRM's attribution writers as negative evidence; Sub's authoritative immutable Lead-origin boundary; Integrator's transport-only contract; Backoffice/Sub candidate status and the explicit adoption pause | `media-observations-sources.md` |
| **Campaigns sources** | Sub as the qualifying outbound-campaign lifecycle and test source; CRM tracking/correlation parity plus provider/scheduler/Lead coupling defects; dotmac_mkt's editorial and advertising hierarchy preserved outside the owner; Backoffice as independent reuse proof; and the explicit Sub-first resolution of the broader programme's Backoffice-first sequence | `campaigns-sources.md` |

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
