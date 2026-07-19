# Kernel program directive (Michael, 2026-07-18) — six workstreams + milestone 1

Recorded verbatim (structure preserved). Third directive of 2026-07-18; companions:
`2026-07-18-adoption-review.md` (workstream 1 detail), `2026-07-18-module-control-plane-directive.md`
(workstream 3 detail + plugin/flag mechanics), ADR-0003 deployment-profiles work (workstream 5
provider interfaces). This directive turns dotmac_starter_mt into a PUBLISHABLE KERNEL with a
reference application assembly, not just a clone-and-rename starter.

---

Work in dotmac_starter_mt:

**1. Complete the existing control-plane security plan:**
- Dedicated platform administrator identity
- Secure platform sessions
- RLS-active development roles
- Atomic tenant provisioning
- Deny-by-default platform routes
- Audited privileged actions

**2. Establish a publishable kernel boundary:**
- Separate kernel from reference application composition
- Define package names and supported public imports
- Add compatibility/version policy
- Implement ProductAssemblySpec
- Prove an empty reference assembly can boot without copying source

**3. Complete platform composition:**
- ModuleRegistry
- Canonical permissions and audit actions
- Typed feature flags
- Settings registry
- Entitlement and capability evaluator

**4. Publish lifecycle infrastructure:**
- Transactional outbox and inbox
- Idempotent commands
- Durable job/provider contracts
- Retry, compensation and reconciliation
- Tenant lifecycle state machine

**5. Publish operational contracts:**
- Health and readiness
- OpenTelemetry instrumentation
- Support access grants
- Diagnostic bundle contract
- Deployment/provider interfaces
- Common API/error conventions

**6. Establish distribution:**
- Tagged alpha/RC packages
- Signed base images
- Contract-test kit and fake providers
- Changelog and migration compatibility
- Automated consumer update PRs
- SBOM and provenance

**The first starter milestone is:**

> Secure kernel prerelease + ProductAssemblySpec + empty assembly boots + fake provider test kit

---

## Program sequencing (recorded 2026-07-18)

Milestone 1 = workstream 1 (control-plane security plan, EXECUTING on branch `control-plane`)
+ workstream 2 (kernel boundary plan: `docs/superpowers/plans/2026-07-18-kernel-boundary.md`)
+ the fake-provider contract-test kit slice of workstream 6.

Workstream 3 = the module control-plane program steps 2–5 (directive doc governs mechanics).
Workstreams 4–6 planned after milestone 1 lands; the runtime/delivery-standards items from the
adoption review fold into workstreams 5–6.
