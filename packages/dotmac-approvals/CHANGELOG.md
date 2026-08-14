# Changelog — dotmac-approvals

All notable changes to the `dotmac-approvals` distribution. This package follows
[Semantic Versioning](https://semver.org). Nothing has been published: the
package is absent from `.github/release-modules.json` until its live Postgres
migration and catalog gate has passed.

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
