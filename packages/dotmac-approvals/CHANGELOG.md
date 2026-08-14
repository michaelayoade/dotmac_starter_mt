# Changelog — dotmac-approvals

All notable changes to the `dotmac-approvals` distribution. This package follows
[Semantic Versioning](https://semver.org). The `.github/release-modules.json`
entry landed once the live Postgres migration and catalog gate passed; `0.1.0a1`
and `0.1.0a2` have since been published.

## 0.1.0a3 — 2026-08-14

Corrects a2's plane selector (ADR-0028). **a2 was published**, so an assembly
already on it must add an explicit selection — see the kernel a61 changelog's
"Migrating from a60" for the three-step change. The manifest declares all
three supported installations — tenant-only, platform-only and both — and an
assembly must select one explicitly. `ap_0001` builds exactly that selection;
truthfully binding a tenant catalogue no longer opts the tenant tables in.

Kernel floor is `>=0.1.0a61`. The live PostgreSQL platform-only canary runs the
kernel lineage first, proves `public.tenants` exists, then proves all three
tenant approval tables are absent while all three platform tables are usable.

## 0.1.0a2 — 2026-08-14

**Published, then superseded by a3.** This version incorrectly
used absence of a tenant prerequisite binding as installation intent. Vendor CP
has that provider through its composed kernel lineage, so the premise and the
selector were both wrong.

Makes the module installable in a platform-only assembly (ADR-0027). Found while
starting ADR-0026's cutover 1: the vendor control plane has no tenant catalogue
and never will, so the previous lineage — which demanded `tenant_scope_catalog.v1`
to create any table at all — could not install there.

### Changed

- The manifest declares `module_database_roles.v1` as `requires` and
  `tenant_scope_catalog.v1` as `tenant_requires`, so the tenant plane is
  conditional on the assembly binding a tenant catalogue.
- `ap_0001_approvals` builds the platform plane unconditionally and the tenant
  plane only where that binding exists, and grants schema USAGE to `app_user`
  only when there is something there for the tenant role to reach.
- Kernel floor is `>=0.1.0a60`, the release that added per-plane prerequisites.

No behaviour changes. A built tenant plane is identical to `0.1.0a1`'s.

## 0.1.0a1 — 2026-08-14

The first slice: the contract, both persistence planes, and the parity tests.
Implementation authority is ADR-0017's 2026-08-14 owner-directed exception; the
boundary is ADR-0026.

### Added

- `contracts` — the frozen vocabulary (`ApprovalState`, `DecisionAction`,
  `ApprovalLevel`, `PolicyRevision`, `Actor`, `Evaluation`, `ApprovalEvent`) and
  every typed refusal. No Money, no FX, no subject enum.
- `policy` — the rules, pure and shared by both planes: ordered levels,
  distinct-actor quorum, eligibility, separation of duties, self-approval
  exclusion, MFA, and ordered-level evaluation.
- `models` — six tables on two declared planes. Tenant tables carry
  `tenant_id NOT NULL`, composite identity and FORCEd RLS; platform tables carry
  no tenant column. `(policy_code, version)` is unique per scope, and
  `(request_id, level, actor_id)` is unique so a duplicate vote is impossible
  rather than merely refused.
- `service` — ten explicitly plane-named entry points. Services `add`/`flush`
  and never commit.
- `outbox` — optional adapter writing `approval.requested|approved|rejected|
  cancelled` onto the kernel's transactional outbox. Kept out of `service` so a
  consumer with its own delivery is not forced to install the kernel's.
- `manifest` — declares both plane tuples and the two logical prerequisites
  (`tenant_scope_catalog.v1`, `module_database_roles.v1`).
- The `ap` lineage: `ap_0001_approvals`, schema `mod_approvals`.

### Allocated elsewhere

- `mod_approvals` / prefix `ap` / branch label `approvals` in
  `dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` (kernel `0.1.0a59`), landing
  in the same change as this manifest.

### Not included, deliberately

Threshold/FX routing (stays in the domain — ADR-0026 § 7a), any subject-type
vocabulary, any consuming-domain import, and any execution of an approved
transition.
