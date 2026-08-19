# Vendor CP recomposition — capability gap and source inventory

**As of:** 2026-08-19
**Starter:** `origin/main` `fb9aea0`
**Vendor CP:** `main` `2c4d88a`, plus the unmerged branches named per row
**Sub:** working tree, `app/` as checked in
**ERP:** working tree, `app/` as checked in
**Academy app / CRM / Workspace / Integrator:** working trees as checked in

Characterization for the programme that recomposes the vendor control plane as a
thin assembly over Starter-owned modules. **Facts, not a mandate** — the
dispositions this inventory supports are recorded in
[ADR-0033](../adr/0033-the-vendor-control-plane-composes-existing-owners.md).

This is step 2 of the extraction procedure in
[`module-extraction-sources.md`](module-extraction-sources.md), taken for six
candidate capabilities at once because the question asked of all six was the
same: *does a qualifying owner already exist?*

## Headline finding

**Three of the six candidates need a new owner. Three do not.**

| # | Candidate | Disposition | Why |
|---|---|---|---|
| 1 | `dotmac-commercial-agreements` | **build — product-first extraction** | Vendor `contracts/` is a qualifying implementation; no Starter owner exists |
| 2 | `dotmac-licensing` | **build — product-first extraction** | Vendor `licensing/` is a qualifying issuer; the kernel owns only the receiver half |
| 3 | `dotmac-deployment-control` | **build — part extraction, part evidenced greenfield** | Vendor V6 slices are a tested reference for the receipt half; the plan/rollout half has no source in any repository |
| 4 | Brand / Profile | **build — product-first extraction** | Sub `BrandProfile` is a qualifying implementation; kernel `branding` is a six-key resolver, not a profile owner |
| 5 | `dotmac-support-access` | **DO NOT BUILD** | ADR-0029 already names three owners for exactly this decision |
| 6 | `dotmac-notifications` | **DO NOT BUILD** | ADR-0006 § 5c and three taken dossiers already placed all six of its decisions |

Rows 5 and 6 are the reason this inventory was taken across all six candidates
rather than three: both read as gaps from the Vendor journey, and both dissolve
on contact with the checked-in record. Building either would create a second
owner of an already-owned decision, which is the failure mode hard rule 24 and
ADR-0006 § 5 exist to prevent.

---

## 1. `dotmac-commercial-agreements` — build, product-first

### The qualifying source

`dotmac_vendor_control_plane` at `main` `2c4d88a`:

| Piece | Path | Lines |
|---|---|---|
| Service | `src/vendor_cp/contracts/service.py` | 658 |
| Models | `src/vendor_cp/contracts/models.py` | 116 |
| Schemas | `src/vendor_cp/contracts/schemas.py` | 95 |
| Router | `src/vendor_cp/contracts/router.py` | 161 |
| Manifest | `src/vendor_cp/contracts/feature.py` | 18 |
| Migration | `alembic/versions/v004_contracts.py` | — |
| Design | `docs/design/contract-service.md` | — |
| Decision | `docs/adr/0003-product-qualified-commercial-identity.md` | — |

**1,050 LOC across two tables.** This is the "Tier B" case recorded in Knowledge
`vendor-cp-capability-gaps-and-composable-plan`: the implementation exists in
exactly one product, so extraction has no shadow phase and no second writer to
retire — only a migration of authority out of the Vendor assembly into a module
the Vendor then pins.

### Why no existing Starter owner qualifies

| Candidate owner | Why it is not this | Evidence |
|---|---|---|
| `dotmac-subscriptions` | ruling **A2(a)**: vendor↔operator legal commercial contracts are explicitly distinct from recurring offer/price/subscription versions | Knowledge `vendor-cp-capability-gaps-and-composable-plan`; `a2-commercial-offer-source-audit.md` |
| `dotmac-entitlement-allocation` | owns allocations and balances; consumes an agreement fact, does not own the agreement | `entitlement-allocation-sources.md` |
| `dotmac-billing` | ADR-0020 scopes it to operational receivables; an agreement is upstream of any invoice | ADR-0020 |
| `dotmac-approvals` | ADR-0026 § 6: approvals decide approval, never the transition. Activation is the agreement's transition | ADR-0026 |
| `dotmac-release-catalog` | owns published release evidence; an agreement *references* releases | `product-manifest-sources.md` |

### The `offer_versions` trap, restated

`a2-commercial-offer-source-audit.md` and Knowledge both record that Vendor's and
Sub's `offer_versions` are **not** the same shape — 5 business columns against
~18 plus 6 relationships, embedded price against a separate price table, no
parent against an FK to `catalog_offers`. A table-name collision is a prompt to
compare columns, not evidence of a shared contract. **The same caution applies to
this extraction**: `contracts` in Vendor and `templates/procurement/contracts` in
ERP share a word and nothing else — ERP's are supplier procurement documents.
ERP is not a source for this module and is not listed as one.

### What the extraction must generalise

Vendor's `ContractService` writes through the platform outbox and names its
counterparty by `vendor_accounts.id`. ADR-0019 § 1 and ruling **A3** both refuse
retiring `vendor_accounts` into kernel `Party`. The module therefore takes an
**opaque counterparty reference** (see § 7 below), and the Vendor assembly binds
it to its own account id. That is the one typed product seam this extraction adds.

---

## 2. `dotmac-licensing` — build, product-first

### The qualifying source

`dotmac_vendor_control_plane` at `main` `2c4d88a`:

| Piece | Path | Lines |
|---|---|---|
| Issue/lifecycle service | `src/vendor_cp/licensing/service.py` | 452 |
| Signing | `src/vendor_cp/licensing/signer.py` | 275 |
| Projection | `src/vendor_cp/licensing/projection.py` | 736 |
| Transport | `src/vendor_cp/licensing/transport.py` | 616 |
| Revocation | `src/vendor_cp/licensing/revocation.py` | 289 |
| Operations | `src/vendor_cp/licensing/ops.py` | 274 |
| Models | `models.py` 147 + `delivery_models.py` 283 + `revocation_models.py` 66 | 496 |
| Schemas / router / manifest | `schemas.py` 207, `router.py` 326, `feature.py` 18 | 551 |
| Migrations | `v006_licences` … `v011_product_identity` | 6 lineage steps |
| Design | `docs/design/licence-service.md` | — |

**3,689 LOC across roughly ten tables** — the single largest Tier B capability in
the fleet.

### The two-sided boundary that makes this a gap

The kernel already owns the **receiver** half and must not own the issuer half:

| Half | Owner today | Evidence |
|---|---|---|
| Verify a signed envelope, project grants, keep the replay record, import revocation lists | `dotmac_kernel.licensing` + the assembly's `licensing` feature (migrations `a002`, `a003`) | `CLAUDE.md` § Layout; ADR-0006 **D2**, **D4** |
| Issue, sign, version, revoke, supersede, and record acknowledgement of a licence | **nobody in Starter** — only Vendor | this inventory |

ADR-0006 **D2** makes kernel WS8 the sole target licence protocol and **D4**
makes the `dotmac-*` licence schema IDs permanent protocol identifiers. The new
module is therefore constrained to *issue what the kernel already verifies* — it
introduces no second format.

### ERP's `app/licensing/` is a compared-and-rejected source

| Piece | Path | Lines |
|---|---|---|
| Enforcement | `dotmac_erp/app/licensing/enforcement.py` | 208 |
| Validator | `dotmac_erp/app/licensing/validator.py` | 86 |
| Schema | `dotmac_erp/app/licensing/schema.py` | 70 |
| Fingerprint | `dotmac_erp/app/licensing/fingerprint.py` | 54 |
| State | `dotmac_erp/app/licensing/state.py` | 55 |
| Tests | `dotmac_erp/tests/test_licensing/` (4 files) | 551 |

**1,030 LOC, and every line of it is receiver-side** — validate, enforce,
fingerprint, hold state. It issues nothing and signs nothing. Knowledge
`dotmac-kernel-build-once-scope-map` already records it as *"a second
incompatible licence format in the fleet"*. It is therefore a **retirement
target for the kernel receiver** (a separate, already-recorded obligation), not
a source for the issuer module. Listing it as a source would repeat the
`offer_versions` error in the opposite direction: same word, opposite side of
the protocol.

### Signing material

`signer.py` is ported for its behaviour, never its key handling. Hard rule 20
(ADR-0009) governs: the module holds a **key identifier and a signing-material
reference by name**, and private material is installed by the product through
`dotmac_kernel.secret_sources.SecretSource`. No key, no fixture key, and no
test key is checked in, logged, repr'd, or written to Knowledge.

---

## 3. `dotmac-deployment-control` — build, part extraction, part evidenced greenfield

**This row is the one with a partial source, and the split is recorded exactly.**

### 3a. The receipt / acknowledgement half — a tested reference exists

Not on `main`. On two **unmerged, superseded** Vendor branches:

| Branch | Piece | Lines |
|---|---|---|
| `feat/v6-slice2-applied-state-admission` (`c9ed074`) | `src/vendor_cp/licensing/admission.py` | 495 |
| " | `src/vendor_cp/licensing/admission_models.py` | 210 |
| `feat/v6-slice1-deployment-credentials` (`4e6de3a`) | `src/vendor_cp/licensing/credentials.py` | 428 |
| " | `src/vendor_cp/licensing/credential_models.py` | 176 |
| `design/v6-deployment-credentials` | `docs/design/deployment-credentials.md` | 723 |
| tests | `test_applied_state_admission.py` 396, `test_deployment_credentials.py` 455, `test_admission_concurrency.py` 238, migration rehearsals +336 | 1,425 |

**~1,300 LOC implementation, ~1,425 LOC tests, a 723-line design document.**

**Its status is recorded honestly:** these branches were never merged and never
deployed. Their migration slots `v011`/`v012` were subsequently **reused by
different work on `main`** (`v011_product_identity`, `v012_approvals_shadow_readonly`),
which is positive evidence that the V6 line was abandoned rather than merely
pending. Under hard rule 24 this is a **tested reference implementation, not a
production-used one**. It is the best available source and it is used as one —
behaviour and parity tests are ported — but this inventory does not claim
production provenance it does not have, and `EXTRACTION.toml` records
`source_mode = "reference-first"` rather than `"product-first"` for this half.

`credentials.py` is ported **only** for deployment-target identity and
possession proof. Provider credentials are out of scope by the module's own
boundary and stay with the Integrator.

### 3b. The plan / rollout half — no source in any repository

Knowledge `vendor-cp-managed-email-collaboration-control-plane` describes
uncommitted Vendor source implementing *"v015 immutable fleet desired state,
v016 bundles/plans/approval, v017 signed dispatch/verified receipt projection"*.

**Searched and not found:**

| Where | Result |
|---|---|
| every local and remote branch of `dotmac_vendor_control_plane` | no `v015`–`v017` revision, on any ref |
| `git stash list` | empty |
| `git fsck --lost-found` dangling commits (5) | dated 2026-07-31 – 2026-08-04, all pre-V6-slice-2; none contains fleet desired state |
| `git reflog --all` filtered on fleet/desired/rollout/deployment | only the V6 slice-1/slice-2 line above |
| worktree `/private/tmp/dotmac-vendor-managed-collab` | directory gone; registration pruned |
| ERP, Sub, CRM, Academy app, Workspace, Integrator, Backoffice — grep for `desired_state`, `DesiredState`, `rollout`, `Rollout`, `deployment_target`, `DeploymentTarget` | see § 3c |

The source is **not recoverable from any checked-in Dotmac repository**.

### 3c. Sub's UISP control — compared, and rejected as a source

The only production desired/observed reconciliation implementation in the fleet:

| Piece | Path | Lines |
|---|---|---|
| Model | `dotmac_sub/app/models/uisp_control.py` | 136 |
| Schemas | `dotmac_sub/app/schemas/uisp_control.py` | 61 |
| Admin surface | `dotmac_sub/app/web/admin/network_uisp_control.py` | 258 |
| Adjacent | `router_management.py` 387, `ont_observation.py` 143 | 530 |

`UispDeviceIntent` carries `desired_state`, `observed_config`,
`desired_revision`, `last_observed_at` and a status lifecycle, with
`UispConfigSnapshot` keyed by `desired | observed` source. The **shape** is the
one deployment control needs.

**The subject is not.** It is a subscriber's network device managed through a
third-party NMS — not a licensed Dotmac application deployment. Applying ADR-0006
§ 5's rule (*a name collision is a prompt to compare columns, not evidence of a
shared contract*) in the direction it is less often applied: a **shape** match is
not a contract match either. Sub's implementation is recorded here as a
**pattern reference** — the desired/observed/revision/snapshot decomposition is
adopted — and explicitly **not** as an extraction source. No Sub code is copied,
and Sub is not a consumer of this module.

### 3d. Disposition

Greenfield for the plan/rollout half, authorized by the absence audit in § 3b,
under hard rule 24's clause *"a greenfield shared implementation requires
checked-in evidence that no qualifying product implementation exists"* — this
section is that evidence. Ruled by Michael, 2026-08-19: *"if we don't have it on
any of our repos, let's build."*

---

## 4. Brand / Profile — build, product-first

### The qualifying source

`dotmac_sub`:

| Piece | Path | Lines |
|---|---|---|
| Model | `app/models/branding.py::BrandProfile` | 81 |
| Profile service | `app/services/brand_profiles.py` | 378 |
| Theme | `app/services/brand_theme.py` | 161 |
| Config | `app/services/branding_config.py` | 112 |
| Storage | `app/services/branding_storage.py` | 73 |
| Public resolution | `app/services/public_branding.py` | 31 |
| Schemas | `app/schemas/branding.py` | 61 |
| Behaviour proof | `tests/test_brand_profiles.py`, `test_customer_branding.py`, `test_branding_storage.py`, `test_web_system_branding.py` | — |

**897 LOC in production.** `BrandProfile` already carries scope identity
(`scope_type`/`scope_id`), display and product names, legal name and address,
logo / dark-logo / favicon references, support email and phone, sender
presentation (`from_email`, `from_name`), and host mapping (`portal_domain`,
`app_url`) — fifteen of the intended contract's fields.

Secondary sources, compared: CRM `branding.py` / `branding_state.py` /
`branding_assets.py`; ERP `email_branding.py`, `branding_assets.py`,
`email_profile.py`, with the `test_branding_no_raw_css.py` and
`test_branding_raw_css_route_canaries.py` guards — those guards are the
ADR-0006 **D8** rule already enforced in a product and are ported as parity tests.

### Why no existing owner qualifies

| Candidate owner | Owns | Does not own |
|---|---|---|
| `dotmac_kernel.branding` | resolution of **six** keys — static `brand.json`/env, overlaid by one tenant `ui_branding` domain setting; `RETIRED_BRAND_KEYS` enforces D8 | a profile identity independent of a tenant, host→profile mapping, file references, enabled surfaces, mobile build metadata |
| `dotmac-ui` | the design tokens and the compiled stylesheet (ADR-0006 U1) | any data; it is dependency-free by contract |
| `dotmac_kernel.profiles` | `DeploymentProfileSpec` — modules, provider seams, locale/currency/legal/residency (WS1) | anything presentational; it is a composition declaration |
| `dotmac-files` | stored bytes (ADR-0022) | what a byte range means |
| `dotmac_kernel.display` | tenant timezone and date/datetime formats | brand identity |

The gap is real and narrow: **a named, addressable brand profile that is not a
tenant setting**. It is what lets one released artifact appear as Dotmac Academy
and as NDIC Academy. A per-tenant six-key settings overlay cannot express that,
because the profile has to be selectable by host before any tenant is resolved.

### Plane

Genuinely dual-plane under ADR-0023, with a named assembly on each side **today**:
Sub (tenant — the 897 LOC above) and the Vendor control plane (platform — branded
deployment intent). This is the ADR-0023 case, not a speculative second plane.

### Constraints carried from the checked-in record

- ADR-0006 **D8** — no tenant-supplied raw CSS, fleet-wide. Sub's
  `primary_color`/`secondary_color` are **value-named** and must generalise to
  published `dotmac-ui` **role** tokens (hard rule 16). This is the one place
  the extraction changes the source's model rather than porting it.
- `dotmac-files` stores the logo/icon bytes; the profile holds references.
- Native mobile brands stay separate signed builds from shared source; the
  profile holds **build-profile metadata references** only.

---

## 5. `dotmac-support-access` — DO NOT BUILD

### The decision already has owners

[ADR-0029](../adr/0029-access-is-requested-approved-and-issued-by-three-owners.md)
(Accepted, 2026-08-14) names three:

| Owner | Owns |
|---|---|
| `dotmac-approvals` | the approval request and the decision evidence — who approved what content under which policy revision, and whether it is still valid |
| `dotmac-application-access` | **desired access: grant-set issuance, delivery, acknowledgement, drift against applied state, and revocation** |
| the assembly | routing and reaction — selecting the policy revision, calling access when an approval event arrives |

Every concept the support-access candidate would own maps onto that table:

| Candidate concept | Existing owner |
|---|---|
| Access requests; exact target and requested scope | `dotmac-application-access` (the content-bound access subject) |
| Approval evidence | `dotmac-approvals` (ADR-0026 § 2 digest binding) |
| Time-bounded grants; activation; revocation; expiry | `dotmac-application-access` |
| Immutable audit evidence | `dotmac_kernel.audit` |
| Emergency / break-glass classification | an **attribute of the request**, not a second owner |

Building a fourth owner would recreate precisely the two-owners-for-one-decision
conflict ADR-0029 was written to resolve.

### What the audit found in the products

Knowledge `vendor-cp-capability-gaps-and-composable-plan` records *"support-access
enforcement — Sub's audited admin impersonation (20 files) is a real source for
the enforcement half."* **Measured, that overstates it.**

| Claimed piece | What is actually there |
|---|---|
| impersonation | `app/services/auth_flow.py::issue_impersonation_access_token`, plus an `is_impersonation` boolean carried on the portal session (`customer_portal_session.py`, `customer_context.py`) |
| `app/services/support.py` (4,036 LOC) | **support tickets** — owned by `dotmac-ticketing`, not access |

There is **no access-request table, no approval linkage, no time-bounded grant
record, and no revocation ledger** in Sub. It is a token flag, not an
implementation of the contract. ERP has nothing. So there is no qualifying
source for a support-access module *and* no gap that needs one.

### Disposition

Do not build. **Extend `dotmac-application-access`** — still unbuilt and
unauthorized, deferred by ADR-0021 § 5 until the kernel has a generic
signed-document mechanism, and by ADR-0017's adoption gate — to carry
time-bounded grants and a break-glass classification when it is built. That
extension is an amendment to ADR-0029, not a new module, and it is **not part of
this programme**.

---

## 6. `dotmac-notifications` — DO NOT BUILD

### Already decomposed, already placed

[ADR-0006 § 5c](../adr/0006-white-label-product-foundation.md) decomposed the
author → render → decide eligibility → route → send → prove capability into
owners, and three of the required per-owner dossiers have since been taken. Two
of them *shrank their own owner*:

| § 5c owner | Dossier | Outcome | Where it landed |
|---|---|---|---|
| template studio | `template-studio-source-audit.md` | unchanged | `dotmac-template-studio` 0.2.0a2 |
| consent / suppression | `consent-suppression-sources.md` | unchanged | `dotmac_kernel.consent` (0.1.0a34) |
| delivery / outbox | `delivery-outbox-sources.md` | **shrank — not a queue** | `dotmac_kernel.delivery` (0.1.0a35) |
| channel policy | `channel-policy-sources.md` | **dissolved — it is a setting** | `dotmac_kernel.channel_policy` |
| document generation | not yet taken | — | — |
| product domain | — | unchanged | stays in ERP and Sub |

### Every candidate concept already has an owner

| Candidate concept | Existing owner | Evidence |
|---|---|---|
| Notification intent | the product domain (§ 5c row 6), emitted as a typed kernel outbox payload | ADR-0014 |
| Recipient / channel decision inputs | `dotmac_kernel.channel_policy` | `channel-policy-sources.md` |
| Consent and suppression references | `dotmac_kernel.consent` + `consent_models` | `consent-suppression-sources.md` |
| Deduplication | `dotmac_kernel.idempotency` (ADR-0014, hard rule 23) | ADR-0014 |
| Template / version reference | `dotmac-template-studio` | ADR-0006 § 5c |
| Delivery state and receipts | `dotmac_kernel.delivery` + `delivery_models` + `delivery_providers` | `delivery-outbox-sources.md` |
| Retry eligibility | the kernel outbox (`attempts`, `available_at`, lease reclaim, dead-letter) | `delivery-outbox-sources.md` |
| Provider transport, credentials, wire retries | `dotmac-integration` / the Integrator assembly (ADR-0024) | ADR-0024 |

`delivery-outbox-sources.md` states the trap explicitly: Sub's `Notification`
queue and the kernel's `OutboxEvent` are *"the same machine, built twice"*, and
porting the queue *"would install the duplicate permanently rather than retire
it."* A `dotmac-notifications` module would be that duplicate a third time.

### Disposition

Do not build. The Vendor assembly composes the existing owners. If a genuine
seam is found during composition, it is an addition to an existing owner and
needs its own dossier — not a new distribution.

---

## 7. Identity boundary — the counterparty question

**Asked, and resolved without conflict.**

| Source | Says |
|---|---|
| ADR-0019 § 1 (fleet-wide, Accepted) | Party / PartyRole / Account / Principal are four concepts; **Party is not an account** |
| ADR-0019 § 5b | Account attaches to PartyRole, so *"what does Acme owe us as an operator"* stays expressible |
| ADR-0019 § 6 | no kernel identity extension until § 1–§ 2 hold in a product |
| Ruling **A3** (Michael, 2026-08-12) | **REJECTED** — `vendor_accounts` must not retire into kernel `Party`; redesign as a commercial-account module referencing Party/PartyRole |
| ADR-0024 (hard rule 28) | an importer never assigns an authoritative field; correlation-only needs use an opaque reference on the local owning record |

These agree. There is **no conflict to stop on**, and the answer is the same in
all of them: the modules in this programme take an **opaque counterparty
reference** and never define a counterparty master. The Vendor assembly binds
that reference to `vendor_accounts.id`; a later commercial-account module may
rebind it without touching any module in this programme.

**No module in this programme creates, owns, or extends counterparty master
data.** That is what "a speculative customers/counterparty owner" would have
been, and it is not built.

---

## 8. Persistence planes, derived from named consumers today

Hard rule 27 (ADR-0023) and ADR-0028: a plane is **declared** because a named
assembly needs it now, never because it could be useful later.

| Module | Tenant plane | Platform plane | Named consumer today |
|---|---|---|---|
| `dotmac-commercial-agreements` | — | ✅ | Vendor CP (control plane). No tenant consumer exists; Sub has no vendor↔operator agreement |
| `dotmac-licensing` | — | ✅ | Vendor CP issues; every licensed deployment *receives* through the kernel, which is not this module |
| `dotmac-deployment-control` | — | ✅ | Vendor CP. A tenant does not plan its own deployment |
| brand profiles | ✅ | ✅ | **both** — Sub (897 LOC, tenant) and Vendor CP (platform, branded deployment intent) |

Three platform-only modules, one genuine dual-plane module. Platform-only follows
the precedent of `dotmac-release-catalog` (`mod_rel`), `dotmac-entitlement-allocation`
(`mod_ealloc`) and `dotmac-integration` (`mod_intg`) — all allocated with an empty
tenant `tables` tuple.

---

## 9. What this programme must not create

Recorded so the next reader can check the outcome against the intent:

- a second product or offer catalogue — `dotmac-release-catalog` owns releases; ruling A2 keeps offers detached
- Vendor-specific billing, ticketing, or branding — `dotmac-billing`, `dotmac-ticketing`, and § 4 above
- a generic workflow runtime — `dotmac-approvals` decides approval; the domain owns its transition (ADR-0026 § 6)
- generic external delivery, connector transport, or a second integration engine — `dotmac-integration` (ADR-0024)
- platform health duplicating Dotmac Observability — ruling **A4** keeps health separate from fleet
- provider clients or provider-specific branches — Integrator connector plugins only
- a speculative counterparty owner — § 7

---

## 10. Sources consulted and found empty

Stated so absence is evidence rather than an untaken search.

| Repository | Searched for | Result |
|---|---|---|
| ERP | commercial agreement lifecycle | `templates/procurement/contracts` — supplier procurement documents, different subject |
| ERP | licence issuance / signing | `app/licensing/` is receiver-side only (§ 2) |
| ERP, Sub, CRM, Academy, Workspace, Integrator, Backoffice | deployment desired state / rollout | only Sub's UISP device control (§ 3c) |
| Sub, ERP | support access grants | § 5 |
| Workspace, Integrator, Backoffice | brand profiles | none |
| CRM | commercial agreements, licensing, deployment | none |
