# The vendor control plane composes; it does not own

**Date:** 2026-08-12
**Status:** plan (non-authoritative intent — see `CLAUDE.md` docs hierarchy)
**Measured against:** starter `5ec756e`, vendor CP `eb667fa` (2026-08-04),
ERP `0f4b1698`, CRM `c64b5aa0`, Sub `9f6f9f36`

## What this document is, and is not

**It is a capability inventory.** These are the capabilities the vendor control
plane needs that decomposing ERP/CRM/Sub will never produce, with what exists
today for each and where it is missing.

**It is not a package map.** An earlier revision named twelve distributions as
though the boundaries were settled. Four are genuinely unadjudicated, and one of
them — the offer catalogue — turned out to be a different question entirely once
the two implementations were compared rather than counted. Package names below
are candidate boundaries; the ones marked **A1–A4** must not create a table
until they are ruled on. ADR-0019 is the reason to be strict about this: *a
shared distribution freezes whatever boundaries it ships with, and every
consumer then pins them.*

## The ruling this plan implements

> The composable principles apply. It must consume composable parts from Starter.
> — Michael, 2026-08-12

Every capability the vendor control plane needs resolves to `dotmac-kernel`,
`dotmac-ui`, or a Starter module. `dotmac_vendor_control_plane` keeps its
assembly spec, its config, its deny-case tests and its console composition —
and nothing else. It owns no domain table that a second vendor/OEM deployment
would have to rebuild.

This does **not** contradict ruling C1 (2026-07-30). C1 forbids the **kernel**
from acquiring fleet tables and becoming a fleet authority. An optional,
independently versioned Starter module that nobody is forced to install is not
the kernel. C1's substance — the kernel stays product-neutral, fleet workflow
does not leak into every data plane — is preserved by putting the tables in
modules a vendor assembly opts into.

## The gate that governs everything below

**ADR-0017 § 2 is a moratorium on new kernel facilities**: none is started until
the kernel's migration lineage runs in a product database in production. Its one
exception is deliberately narrow — a facility a *live adoption is blocked on
today*. The vendor control plane is not a live adoption and has no production
deployment, so it does not qualify; ADR-0017 names "a product will need this"
as explicitly not demand.

So the order is not negotiable:

- **Now:** Wave 0 (unblock the assembly) and the source audits. Neither writes a
  kernel facility or a module table.
- **After the lineage gate clears** (Sub's kernel migration lineage running in a
  production database): implementation, as **vertical consumer slices** — never
  four unconsumed kernel primitives, which is exactly the a15–a21 pattern
  ADR-0017 promoted from retrospective observation to standing measure.

## Why this plan exists at all

The fleet decomposition matrix measures ERP, CRM and Sub. The vendor control
plane was **unmeasured, not absent** until 2026-08-12. Four of the eight modules
in its own domain map (`dotmac_vendor_control_plane:docs/design/domain-foundation.md`
§ "Domain map") have no row in the measured families and no qualifying source in
any product. Decomposing the monoliths will never produce them.

So the work runs in two directions at once:

- **Build** capabilities that exist nowhere in the fleet — as Starter parts from
  day one, so they are never an extraction later.
- **Extract** the ~7,900 LOC the vendor CP has already built locally, for which
  it is itself the qualifying product-first source.

## Tier A — no complete reusable owner

Not "greenfield". Three of these six have a real partial owner, which makes them
completion or extraction work rather than design work — a distinction that
changes who audits them and what the first commit looks like.

### A-i — genuinely source-free

Nothing in ERP, CRM, Sub, the vendor CP or the kernel implements these.

| # | Capability | Evidence of absence | Candidate home |
|---|---|---|---|
| **G1** | **Release catalogue** — digest-addressed `ReleaseArtifact`, `ReleaseChannel`, `ChannelPin`, `ArtifactSelection`, provenance/SBOM/signature | Zero hits for `release.channel` or `sbom` in any repo including the kernel. Sub's `OltFirmwareImage`/`OntFirmwareImage` (~277 LOC device-firmware admin) is the nearest thing and is a different domain | module |
| **G2** | **Fleet desired state** — `Deployment`, `DeploymentDesiredState`, `DeploymentBinding`, `InfraResourceRef`, `DeploymentChangeRequest`, plan + `plan_hash`, timeline projector | Nothing. Every "deployment" hit in the products is CI/scripts, not an entity. The vendor's 4 measured `fleet-deployment` tables are credentials and applied-state receipts — not the fleet | module |
| **G6** | **Update authority** — vendor-automatic / customer-approved / offline (ruling C3) | Nothing | kernel, **closed union** — see below |

**G6 is a closed, exhaustively enforced protocol union, not an ADR-0008 open
registry.** The registry pattern is for vocabularies whose members belong to
modules and which products must extend without a kernel change. Update authority
is the opposite: a safety property with exactly three legal values, every one of
which fleet code must branch on exhaustively, where a fourth value invented by a
product is a silent authorization hole rather than a feature. Extensibility here
requires deliberate approval; it is not the default.

### A-ii — a partial owner exists; this is completion, not design

| # | Capability | What already exists | What is missing |
|---|---|---|---|
| **G3** | **Durable resumable run engine** | Kernel `providers/provisioning.py` — 355 LOC of protocol: plan/apply/observe/cancel, typed steps, `plan_hash`, stable errors, plus a fake and a parametrized contract suite | Persistence and a driver. No run/step/evidence store, no resume, no compensation. Sub's 27 ad-hoc `*Run`/`*Job` model classes are motive, not a contract |
| **G4a** | **Support-access enforcement** (kernel half, ruling C2) | Sub's audited admin impersonation — 20 files, act-as for customer and reseller portals | Grant claims, verification, enforcement decision and audit hooks as a contract a data plane consumes. No consent, TTL ceiling, break-glass or incident binding exists in the fleet |
| **G5a** | **Health authentication** (kernel half) | The vendor CP's own V6 applied-state handshake — `DeploymentCredentialService`, challenge/possession, timeline-based eligibility, admission | The heartbeat envelope and freshness contract themselves. Sub/CRM heartbeats are internal process liveness, not a cross-deployment envelope |

Their module-side counterparts — **G4b** support-access workflow (request →
consent → break-glass → grant → session → revoke) and **G5b** health ingest +
`FleetHealthRollup` — are source-free, and depend on the kernel halves above.

**A4 (ruled): the health part stays separate from the fleet part.** The design's
invariant is that no mutating consumer of health exists. Across a package
boundary that is a dependency direction anyone can check; inside one package it
is a convention.

## Tier B — exists only in the vendor CP, in no monolith

The vendor CP is the qualifying product-first source and the only implementation,
so these carry no shadow phase, no drift window and no second writer to retire.

| Local feature | LOC | Tables | Candidate destination |
|---|---:|---:|---|
| `licensing` (issuance, signing, delivery, revocation, deployment credentials, applied-state admission) | 4,996 | 10 | **`dotmac-licence-issuance`** — a separate distribution from the kernel's licence *verifier*, because issuance holds a signing key that must never be installable into a product data plane. Package boundaries keep that true without relying on review |
| `contracts` | 972 | 2 | **`dotmac-commercial-contracts`** — vendor↔operator agreements (**A2a**) |
| `approvals` (versioned policy + content-bound approvals) | 493 | 2 | **`dotmac-approvals`** — boundary likely, source unaudited (**A1**) |
| `allocations` | 404 | 2 | **`dotmac-entitlement-allocation`** |
| `offers` (immutable priced offer versions) | 395 | 1 | **detached pending audit** (**A2b**) |
| `accounts` | 332 | 1 | **not** kernel `Party` (**A3**) |
| `provisioning` (contract laboratory, fakes only) | 258 | 0 | absorbed by G2 + G3 |
| `console` | 51 | 0 | stays — composition is what an assembly is for |
| **Total domain code** | **7,901** | **18** | |
| Tests | 6,473 | | port with the code they prove |

`approvals` is the one to notice: **content-bound approval** — an approval that
dies when its input changes — is what binds a `plan_hash` to a human decision in
G2, so a plan cannot be approved and then quietly re-planned. It is not a
contracts detail, and if it is not its own part the fleet module re-implements it.

## Open adjudications

Four, with the rulings of 2026-08-12 applied. None blocks Wave 0 or the audits.
All must be settled before the part they name creates a table.

### A1 — `dotmac-approvals` source: boundary retained, source unaudited

The module boundary is the likely right one. The *source* is not selected: the
vendor CP's 493 LOC (versioned policy, content-bound) against ERP's 20-table
`governance-workflow`. Hard rule 24 requires inventorying ERP before writing
shared behaviour, so this is a source audit, not a preference. The vendor's
content-binding property is the one the fleet work needs specifically, and the
audit may find ERP lacks it — but that is a finding to produce, not to assume.

### A2 — split in two

**A2a — vendor↔operator commercial contracts: keep distinct.** A vendor↔operator
commercial agreement that gates entitlement allocation is not an ISP customer
quote/order. Sub names its equivalents `billing_contracts`/`billing_contract_lines`
and they collide with nothing; `billing_` is a domain qualifier, not a packaging
prefix. Two modules sharing kernel value objects, with a documented contract
between them.

**A2b — offer catalogue: detached from that distribution, pending audit.** An
earlier revision claimed the two `offer_versions` tables were the same shape
built twice. They are not, and that claim was the strongest argument for merging:

| | Vendor CP (`offers/models.py`) | Sub (`catalog.py`) |
|---|---|---|
| Business columns | 5 | ~18, plus 6 relationships |
| Price | embedded — `amount` + `currency_code` | separate `offer_version_prices` table |
| Parent | none — standalone `offer_code` + `version` | FK to `catalog_offers` |
| Semantics carried | `capability_codes` | service type, access type, price basis, billing cycle, contract term, region zone, usage allowance, SLA profile, policy set, effective dating, status |

They share a **name and a concept** — an immutable, versioned, priced offer —
and almost nothing else. That is a genuine reusable-catalogue question (what is
the product-neutral core of "a versioned priced offer", and does the pricing
structure belong inside it?), and it needs the audit `dotmac-ticketing` got. It
does not belong bundled into the commercial-contracts distribution as though it
were settled.

### A3 — vendor accounts: retirement into `Party` is rejected

The earlier proposal — retire `vendor_accounts` into kernel `Party` at platform
scope — is incompatible with ADR-0019 on three independent counts:

1. **§1** names four concepts with four owners and says plainly that a Party is
   *not* "a customer, a login, or an account". Folding an Account into Party is
   the fusion the ADR calls non-conforming.
2. **§5b** puts the Account on the **PartyRole**, not the Party — precisely so
   "what does this organization owe us *as an operator*" is expressible
   separately from any other capacity it holds toward us.
3. **§6** forbids extending the kernel's identity surface at all until §1–§2
   hold in at least one product.

So `vendor_accounts` most likely becomes a **commercial-account module
referencing Party and PartyRole** — not a kernel identity change and not a
retirement. That adds a unit to the map rather than removing one, which is one
of the reasons the count is not frozen.

### A4 — the health part stays separate from the fleet part: ruled

## Sequencing

### Now — nothing here writes a kernel facility or a module table

| # | Work | Why it is not gated |
|---|---|---|
| **W0** | Repin the vendor CP off `dotmac-kernel==0.1.0a9` (the kernel is at `0.1.0a41`) and rewrite its `docs/ARCHITECTURE.md` "still design-only" list. `messaging.{outbox,inbox,envelope}`, `money`, `capabilities`, `entitlements`, `profiles` and `idempotency` all shipped in the intervening 32 alphas; several things it believes are blocked are not | Adopting already-released surface is the opposite of a new facility. ADR-0017 explicitly permits improvements to adopted surface |
| **W1** | **A1 audit** — ERP `governance-workflow` against the vendor's content-bound approvals | Hard rule 24 requires it before any shared behaviour is written |
| **W2** | **A2b audit** — the reusable offer-catalogue core and its boundary; Sub and vendor CP as inputs | Same |
| **W3** | **A3 redesign** — commercial account against Party/PartyRole per ADR-0019 §5b, as a design note, not tables | ADR-0019 §6 blocks the packaging, not the modelling |
| **W4** | `EXTRACTION.toml` dossiers for the Tier B units, following `packages/dotmac-ticketing/EXTRACTION.toml` | A dossier records the source decision; it is not the extraction |

### After the lineage gate — vertical consumer slices

Each slice ships a kernel primitive **with the consumer that exercises it**, so
no facility exists without a product attached. Ordered so each slice's consumer
is already standing when it lands:

| Slice | Kernel side | Consumer side | Proves |
|---|---|---|---|
| **S1** | durable run/step/evidence + resumable driver over the existing `ProvisioningProvider`, on `dotmac_kernel.idempotency` | fleet change request → plan → approved run | G3 + G2 together, and gives ADR-0014's at-most-once owner its first real workload — the row it has never held |
| **S2** | update authority (closed union) | release catalogue: channel, pin, artifact selection | G1 + G6. Authority must exist *before* the first pin, or a pin silently becomes desired state |
| **S3** | support-access enforcement contract | support-access workflow: request → consent → break-glass → grant → session | G4a + G4b |
| **S4** | heartbeat envelope + freshness | health ingest + rollup, with **one real heartbeat producer** | G5a + G5b. A health contract with no producer is the unconsumed-facility failure again, in miniature |

Tier B extractions interleave with these rather than forming a wave of their own:
each is cheap, independent, and unblocks nothing else. Order by independence —
approvals (pending A1), entitlement allocation, commercial contracts, then
licence issuance last, being the largest and most migration-heavy.

## Gates

Each new part is a Starter distribution and inherits the existing machinery, not
a new process: an `EXTRACTION.toml` dossier, one immutable `mod_<code>` schema
and one registered migration lineage (hard rule 14), `ModuleManifest`
declarations with real consumers (hard rule 12), thin adapters (ADR-0010), and a
guard exemption that states an enforceable premise (ADR-0018).

Three additions specific to this plan:

- **No fleet part may be installed by a product data plane.** An import-linter
  contract plus a deny case: the release-catalogue, fleet and licence-issuance
  parts are vendor-assembly-only. This is C1's substance, enforced by packaging.
- **Health has no mutating consumer.** A test that fails if the fleet part
  imports the health part.
- **Update authority is exhaustive.** A test that fails if any branch over the
  union omits a member or accepts an unknown value.

## Corrections applied 2026-08-12

Recorded because each was an error, not a refinement:

- The capability count was written as "five" while the list ran G1–G6. It is six.
- The product-first citation said hard rule 22. Product-first extraction is now
  hard rule **24** (22 is setting inheritance, 23 is at-most-once execution).
- Tier A was called "greenfield". Three of its six have partial owners, which
  makes them completion or extraction work.
- Wave 1 scheduled kernel primitives with no reference to ADR-0017's moratorium.
- A3 proposed retiring `vendor_accounts` into kernel `Party`, which ADR-0019 §1,
  §5b and §6 each independently forbid.
- A2 treated `offer_versions` as one capability on the strength of a name
  collision. The two models are structurally different; the claim was wrong.
- Twelve distributions were named as though the package map were settled.
