# Vendor dependency reconciliation — the focused `0.1.0a1` alpha vs the vendor first slice

> **Status:** discovery + decision record (D0). No application code is authorized by this
> document. It reconciles the internal conflict between three checked-in plans about what the
> vendor control plane's first executable slice may build against the **focused kernel
> `0.1.0a1` alpha**. It amends nothing by itself; the dated amendment notes it calls for are
> written into
> [`2026-07-30-vendor-control-plane-domain-foundation.md`](../plans/2026-07-30-vendor-control-plane-domain-foundation.md)
> (see § "Plan amendments required"). Produced 2026-07-30 on branch
> `d0-vendor-dependency-reconciliation`.

## The conflict being reconciled

Three checked-in statements disagree about what the vendor control-plane first slice can build
against the alpha:

1. **The first-slice table** (vendor design doc § "Sequencing — the first executable slice")
   lists **steps 0–7** and, in its prerequisite 2, assumes the alpha ships
   *"`ProductAssemblySpec`, `create_app`, `dotmac_kernel.testing`, **module registry/capability
   catalogue, outbox/inbox**, and — for step 6 — a published `ProvisioningProvider` protocol"* —
   i.e. a **fat** alpha.
2. **The revised program sequence** (same doc, § "Revised program sequence") says the alpha
   contains *"`ProductAssemblySpec` AND `ProvisioningProvider`"* — a **focused** alpha — then in
   its step 5 asserts *"Implement executable slice **steps 0–6**"* against it.
3. **The kernel-boundary plan** ([`2026-07-18-kernel-boundary.md`](../plans/2026-07-18-kernel-boundary.md),
   Tasks 2–6, incl. the C6 amendment) defines what `0.1.0a1` **actually** ships, and it is the
   focused alpha: `ProductAssemblySpec`, `create_app`, `dotmac_kernel.testing` (harness +
   fakes), and the `ProvisioningProvider` protocol + `FakeProvisioningProvider` + contract
   suite. It does **not** ship a module/capability registry, outbox/inbox, deployment-profile
   registry, money/FX, signed-licence, entitlement-evaluator, or health/heartbeat contract —
   those remain their own later workstreams.

So the vendor design doc's **prerequisite 2** (fat alpha) and its **step count (0–7)** are
inconsistent with its own **revised sequence** (focused alpha, 0–6) *and* with the
kernel-boundary plan (the authority on what `0.1.0a1` is). The kernel-boundary plan governs
the alpha's surface; the directive is explicit that the alpha must **not** be silently
expanded. This document holds the alpha fixed at its kernel-boundary definition and rules on
what that genuinely unlocks.

## What the focused `0.1.0a1` alpha actually ships

**PROVIDED-BY-`0.1.0a1`** — delivered by kernel-boundary Tasks 2–6 (+ ruling C6). Referenced by
name only; field-level shapes live in the kernel contract, never here.

| Contract (by name) | Kernel-boundary source |
|---|---|
| `ProductAssemblySpec` (frozen: `name`, `modules`, `settings_overrides`, `branding`, `providers`, `web_enabled`, `disabled_modules`, + `assembly_template_dir`, `assembly_migrations`) | Task 3 |
| `create_app(spec) -> FastAPI` (assembly bootstrap, middleware/error/mount/lifespan) | Task 3 |
| `ProductAssemblySpec.providers` — interface-keyed provider mapping (the seam a fake plugs into) | Task 3 |
| `dotmac_kernel.testing` harness (`assembly_test_client`) + `FakeClock`, `FakeSeeder`, in-memory `RateLimitStore`, fake branding loader | Task 5 |
| **`ProvisioningProvider` protocol** + typed `PlanResult`/`ApplyResult`/`ObserveResult` + stable error hierarchy (`dotmac_kernel.providers.provisioning`, product-neutral) | Task 3 (ruling C6) |
| **`FakeProvisioningProvider`** + parametrized `dotmac_kernel.testing.contract` provisioning suite | Task 5 (ruling C6) |
| Kernel persistence + transaction authority (`db.get_db`, `get_platform_db`, `conflict_savepoint`, `platform_session`; `models.Base`/mixins) | in the kernel package (surface audit group A) |
| Audit **write-side** (`audit.AuditEvent`, `write_audit_event`) | in the kernel package (surface audit group A) |
| Basic error handling (`errors.register_error_handlers`, `exceptions.*`) | in the kernel package (surface audit group A) |
| Platform-admin identity + `require_platform_admin` + `platform_auth_router` + exact-host routing | control-plane security plan (**prerequisite**, lands in the kernel before the alpha) |

**NOT-YET** — named kernel/platform contracts the vendor slice depends on that `0.1.0a1` does
**not** ship. Each names the future workstream that provides it (WS = deployment-profiles plan
workstream; "program WS" = kernel program directive workstream).

| Contract (by name) | Provided by (future workstream) |
|---|---|
| `ModuleManifest` / `ModuleRegistry` + declared capability codes / capability catalogue | deployment plan WS1 / module control-plane directive steps 2–3 |
| Transactional **outbox** + idempotent **inbox** + idempotent command envelope + lifecycle history | deployment plan WS3 / program WS4 |
| `DeploymentProfile` / `DeploymentProfileSpec` / `DeploymentProfileRegistry` (profile-code validity) | deployment plan WS1 (ADR-0003 § Profile contract) |
| Money / exact-value + immutable FX snapshot + jurisdiction/tax policy primitives | deployment plan WS4 |
| Signed licence issuer/verifier contract + revocation list + offline bundle | deployment plan WS8 |
| Entitlement evaluator + `tenant_entitlement_grants` + `EntitlementDecision`/`QuotaDecision` (data-plane side of projection) | deployment plan WS2 |
| Health/readiness contract + heartbeat envelope + OpenTelemetry instrumentation | program WS5 / deployment plan WS11 |
| Support-access grant/claim/verification contract + diagnostic bundle contract | program WS5 / deployment plan WS11 |
| Canonical permissions + canonical audit-action catalogue (the *names*, not the write mechanism) | program WS3 |
| Common API/error conventions + versioned OpenAPI policy | program WS5 / deployment plan WS13 |
| Provider protocols **other than** `ProvisioningProvider` — `CommercialAuthority`, `TelemetryProvider`, `IngressProvider`, `DnsVerificationProvider`, `TlsProvider`, secret/identity provider | deployment plan WS1/WS10/WS11 (general workstream-5 provider fill; only `ProvisioningProvider` was pulled forward by C6) |

## Step-to-contract matrix (vendor steps 0–7)

Each step lists the contracts it depends on with each marked **P** (PROVIDED-BY-`0.1.0a1`) or
**N** (NOT-YET → named workstream). "Control-plane-owned" logic (state machines, `plan_hash`,
approval policy, `ArtifactSelection` pure function, freshness classification) is the vendor
control plane's own code — it is not a kernel contract and so is never a blocker on its own; it
is noted only where it is the substance of a step.

### Step 0 — Assembly scaffold; platform-admin surface; dev profile with fakes only
Owner: assembly. Gate: empty-assembly boot + D4.
- `ProductAssemblySpec`, `create_app` — **P** (Task 3)
- `dotmac_kernel.testing` harness — **P** (Task 5)
- `require_platform_admin` / platform-admin surface — **P** (prerequisite)
- `ProductAssemblySpec.providers` mapping (holds only fakes → D4) — **P** (Task 3)
- `FakeProvisioningProvider` + provisioning contract suite — **P** (Task 5, C6)
- `DeploymentProfileRegistry` — **not required at step 0**: the "dev profile with fakes" is the
  spec's `providers` map carrying the fake, not the full profile contract. The D4 gate ("only
  fake providers resolvable; a real-cloud reference fails startup") is satisfied by the
  providers map alone.

### Step 1 — Customer account (`AccountService`, `POST /accounts`)
Owner: `AccountService`. Gate: Foundation gate.
- assembly (`ProductAssemblySpec`/`create_app`) — **P**
- kernel persistence + transaction authority — **P**
- `require_platform_admin` (route guard) — **P**
- audit write-side (`write_audit_event`) — **P** (canonical audit-action *names* are **N** — program WS3)
- emits **no** domain event (accounts are not a producer in the event catalogue); no allocation,
  no money field → outbox/inbox, money/FX not on the critical path.

### Step 2 — Commercial contract: draft → submit → approve → activate (`ContractService`)
Owner: `ContractService` (+ `ApprovalPolicyService`, `ContractTransitionPolicy` — control-plane-owned). Emits `contract.submitted`/`.approved`/`.activated`. Gate: Manual commercial gate.
- assembly, persistence, `require_platform_admin`, audit write — **P**
- Money / FX snapshot / jurisdiction policy primitives (contract lines carry currency, amounts,
  legal entity) — **N** (WS4)
- Transactional outbox + inbox (`contract.activated` must be emitted transactionally and
  consumed by `AllocationService`/`FleetDesiredStateService`) — **N** (WS3 / program WS4)
- contract line pins an *immutable offer version* → depends on the offer/catalogue primitives
  in WS4 — **N**

### Step 3 — Deployment intent (`FleetDesiredStateService`, `→ intent_recorded`)
Emits `deployment.intent_recorded`. Gate: Manual commercial gate.
- assembly, persistence, `require_platform_admin`, audit write — **P**
- Transactional outbox (emits `deployment.intent_recorded` to timeline/fleet view) — **N** (WS3)
- `DeploymentProfileRegistry` — the intent carries a profile code; strict validity is deferred
  to step 5's `plan_ready` guard, but a non-billable intent still emits its event via the
  outbox — **N** (WS1 for profile validity; WS3 is the hard blocker)
- billable path depends on step 2 (contract active); the internal/non-billable flag sidesteps
  the contract but not the outbox.

### Step 4 — Exact release + entitlement selection (`ArtifactSelectionService` + `AllocationService`)
Emits `allocation.staged`; selection is a pure function. Gate: determinism + digest-only tests.
- assembly, persistence, audit write — **P**
- `ArtifactSelection` pure function (`ReleaseCatalogService`/`ChannelPin` are control-plane-owned) — **P substance** (no kernel contract beyond persistence)
- `ModuleManifest` / capability catalogue (`CapabilityCatalogueReader` validates every
  allocation line's capability code against the target product's manifest catalogue) — **N** (WS1)
- Transactional outbox + inbox (`allocation.staged` reacts to `contract.activated` via inbox
  and is emitted via outbox) — **N** (WS3)
- signed-licence contract is **not** needed here (issuance is out of the first slice) — staging only.

### Step 5 — Reviewed preview (`DeploymentChangeService` + `DeploymentApprovalService`)
`→ plan_ready → approved`. Emits `deployment.plan_ready`/`.plan_approved`. Gate: D3 (`plan_stale`).
- assembly, persistence, `require_platform_admin` (approver identity) — **P**
- `plan_hash` computation, approval-echoes-hash, `plan_stale` refusal — **P substance** (control-plane-owned; D3 is a governance test, not a kernel contract)
- Transactional outbox — **N** (WS3)
- `DeploymentProfileRegistry` (the `plan_ready` guard requires "profile code valid") — **N** (WS1)
- allocation snapshot resolvable + artifact selection → depends on step 4 (which is itself **N** on WS1/WS3).

### Step 6 — Audited fake-provider provisioning (`DeploymentRunner`)
`→ applying → applied → active`. Emits `deployment.run_*`/`.activated`. Gate: provisioning-simulation (forced failure at every step).
- **`ProvisioningProvider` protocol + `FakeProvisioningProvider` + contract suite — P (Task 3/5, C6 — pulled forward *specifically* for this step)**
- assembly, persistence, `dotmac_kernel.testing` harness — **P**
- Transactional outbox + per-deployment serialization / compare-and-set on `(plan_hash, approval_id)` (`run_started`/`succeeded`/`failed`/`resumed`/`activated` events) — **N** (WS3)
- depends on step 5 (approved plan) — **N** (upstream)
- `applied → active` verification gate needs: health observed fresh (**N**, WS5/WS11) +
  licence/entitlement acknowledged (**N**, WS8 licence + WS2 evaluator) + contracted activation
  condition (step 2). The **provider execution** (plan/apply/observe against the fake) is
  unlocked; the **full run lifecycle + activation gate** is not.

### Step 7 — Observed health in a read-only fleet view (`ObservedHealthIngestService` + `FleetHealthRollupProjector`)
Freshness only. Emits `health.*`. Gate: no mutating consumer of health.
- assembly, persistence — **P**
- Health/readiness contract + heartbeat envelope + OTel (`POST /ingest/heartbeats` schema +
  freshness windows come from the kernel heartbeat-envelope contract) — **N** (WS5/WS11)
- deployment-authenticated identity for the heartbeat (scope derived server-side) → depends on
  deployment existence (steps 3/6) — **N**
- Transactional outbox (`health.heartbeat_received` / `health.freshness_changed`) — **N** (WS3)

## Ruling — which steps the focused `0.1.0a1` alpha genuinely unlocks

The alpha is held at its kernel-boundary definition. **It must not be silently expanded** (per
the program directive and the C6 amendment). Applying "if a step needs a contract the alpha
won't ship, it waits":

| Step | Ruling | Blocking NOT-YET contract(s) |
|---|---|---|
| **0** — assembly scaffold + platform-admin + fake dev profile | **UNLOCKED-BY-ALPHA** (definitely) | — |
| **1** — customer account | **UNLOCKED-BY-ALPHA** | — (kernel persistence + audit + guard only; emits no event) |
| **2** — commercial contract lifecycle | **BLOCKED-ON** | Money/FX primitives (WS4) **and** transactional outbox/inbox (WS3) |
| **3** — deployment intent | **BLOCKED-ON** | transactional outbox (WS3); profile-code validity (WS1) deferred to step 5 |
| **4** — release + entitlement selection | **BLOCKED-ON** | capability catalogue / `ModuleManifest` (WS1) **and** outbox/inbox (WS3). *(The `ArtifactSelection` pure function alone is unblocked; `AllocationService` staging is not.)* |
| **5** — reviewed preview | **BLOCKED-ON** | transactional outbox (WS3) + `DeploymentProfileRegistry` (WS1) + upstream step 4. *(`plan_hash`/approval/`plan_stale` logic itself is unblocked.)* |
| **6** — audited fake-provider provisioning | **BLOCKED-ON** | outbox (WS3) + upstream step 5 + `applied→active` gate (health WS5/WS11, signed licence WS8, entitlement evaluator WS2). *(The `ProvisioningProvider` execution seam — the one thing C6 pulled forward for this step — IS provided; the full run lifecycle is not.)* |
| **7** — observed health | **BLOCKED-ON** | health/heartbeat-envelope contract (WS5/WS11) + outbox (WS3) + upstream deployment existence |

**Net:** the focused `0.1.0a1` alpha unlocks **step 0 and step 1 end-to-end**, plus the
**`ProvisioningProvider` execution seam of step 6** (exercisable in isolation against
`FakeProvisioningProvider` via the `dotmac_kernel.testing.contract` provisioning suite). Steps
2, 3, 4, 5 (end-to-end), 6, and 7 each depend on at least one NOT-YET contract and remain
**design-only** — domain modelling, contract examples, and acceptance scenarios — until their
named workstreams publish. This does **not** shrink the vendor design; it sequences it honestly
against the alpha the kernel-boundary plan actually ships.

## Numbering conflict — 0–6 vs 0–7, and the step-7 deferral

- **Canonical step count for the alpha-era slice is 0–6.** The revised program sequence (the
  later, ruling-C6-era statement) supersedes the 0–7 first-slice table on the *count*. The
  0–7 table is retained as the fuller domain enumeration but is annotated so it no longer reads
  as the alpha-era build order.
- **Step 7 (observed application health) is formally deferred.** It is domain #7 in the design's
  in-scope list and appears as row 7 in the first-slice table; the revised 0–6 sequence drops
  it. It is deferred until the **health/readiness + heartbeat-envelope contract (program WS5 /
  deployment plan WS11)** publishes — which is also why the matrix above marks it BLOCKED-ON
  regardless of the numbering. It re-enters the executable sequence, as the slice's next step,
  only when that contract lands and the numbering is re-opened by an explicit amendment.
- **Caveat carried forward:** "0–6 buildable on the focused alpha" (revised-sequence step 5) is
  itself optimistic — the matrix shows steps 2–6 each need WS1/WS3/WS4/WS8/WS2/WS11 contracts
  the focused alpha does not ship. The canonical *count* is 0–6; the canonical *unlock* is 0–1
  (+ step 6's provider seam). Both facts are recorded so neither the count nor the unlock is
  overstated. See § "Remaining unresolved conflicts".

## Plan amendments required

Three amendment notes remove the conflict. The first two are **written into** the vendor design
doc by this task (see the dated notes now present in that file); the third is **required of**
the deployment-profiles plan and its exact text is given here for whoever amends that doc.

1. **Vendor design doc § "Sequencing — the first executable slice"** — annotate the 0–7 table
   and correct prerequisite 2. Exact text added (dated `Amendment 2026-07-30 (D0 reconciliation)`):
   states that `0.1.0a1` is the **focused** alpha (`ProductAssemblySpec`, `create_app`,
   `dotmac_kernel.testing`, `ProvisioningProvider` protocol+fake+suite) — **not** the fat alpha
   prerequisite 2 named; that the canonical alpha-era count is **0–6** with step 7 deferred; and
   that only steps 0–1 (+ step 6's provider seam) are unlocked by the alpha, the rest waiting on
   named workstreams. Cross-references this reconciliation doc.

2. **Vendor design doc § "Revised program sequence"** — annotate step 5 so "steps 0–6" is not
   read as "all of 0–6 build immediately on the alpha." Exact text added (same dated note):
   points to the step-to-contract matrix here; records that the alpha unlocks 0–1 (+ step 6's
   `ProvisioningProvider` seam) and that steps 2–6 stay design-only pending WS1/WS2/WS3/WS4/WS8.

3. **Deployment-profiles plan
   [`2026-07-18-deployment-profiles-commercial-platform.md`](../plans/2026-07-18-deployment-profiles-commercial-platform.md)
   § "Contract cadence and gates" / Lane B** — *required, not applied by this task.* Suggested
   dated note:

   > **Amendment 2026-07-30 (D0 reconciliation).** Lane B's first slice consumes only the
   > focused kernel `0.1.0a1` alpha (`ProductAssemblySpec`, `create_app`, `dotmac_kernel.testing`,
   > and the `ProvisioningProvider` protocol+fake+contract-suite per ruling C6). The alpha does
   > **not** ship the `ModuleManifest`/capability registry (WS1), transactional outbox/inbox
   > (WS3), `DeploymentProfileRegistry` (WS1), money/FX (WS4), signed-licence (WS8),
   > entitlement-evaluator (WS2), or health/heartbeat (WS11) contracts. Only Lane B slice steps
   > 0–1 (+ step 6's provider seam) are unlocked by the alpha; the Manual-commercial,
   > Licence, and Provisioning-simulation gates each wait on their named workstream contract.
   > See `docs/superpowers/reviews/2026-07-30-vendor-dependency-reconciliation.md`.

The kernel-boundary plan itself needs **no** amendment: its C6 amendment and re-planned Task
3/5 briefs already scope the alpha to exactly the focused surface this reconciliation relies on.

## Remaining unresolved conflicts (needing an owner decision)

1. **Revised-sequence step 5 overstates the unlock.** It says "implement steps 0–6 against the
   alpha," but the dependency table (and the matrix here) show steps 2–6 need WS1/WS2/WS3/WS4/WS8
   contracts the focused alpha does not ship. This doc resolves the *count* (0–6, step 7
   deferred) and the *unlock* (0–1 + step 6's provider seam) from the checked-in docs, but
   whether to (a) re-scope the revised sequence to "build 0–1 now; 2–6 as their workstreams
   land" or (b) expand the alpha to the fat prerequisite-2 surface is an **owner decision**.
   Recommendation: (a) — do not expand `0.1.0a1`; sequence 2–6 behind WS1/WS3/WS4 as they
   publish. (Not applied here — this is a discovery/decision doc; the vendor-doc amendments
   record option (a) as the working assumption pending confirmation.)

2. **Ordering of the connective workstreams for the vendor slice is unstated.** Steps 2–6 are
   gated most universally by **WS3 (outbox/inbox)** and **WS1 (capability catalogue + deployment
   profiles)**; step 2 additionally by **WS4 (money/FX)**. The checked-in docs do not pin the
   publish order of WS1/WS3/WS4 relative to the vendor slice. Flagged for whoever sequences the
   deployment-profiles workstreams: the vendor slice cannot advance past step 1 until at least
   WS3 (and, for step 4, WS1) is tagged.

3. **Step-7 re-entry is not yet numbered.** Once the health/heartbeat-envelope contract
   (WS5/WS11) lands, observed health re-enters as the next slice step. The canonical numbering
   at that point (still "step 7", or renumbered after any WS-gated insertions) must be fixed by
   an explicit amendment then — deliberately left open here.
