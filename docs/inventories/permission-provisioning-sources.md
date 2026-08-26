# Permission provisioning source inventory

Date: 2026-08-26

This inventory records the product-first evidence for a reusable permission
provisioning contract. It distinguishes three authorities that existing code
frequently collapses:

1. a module declares that a permission decision exists;
2. an assembly decides which product roles receive baseline grants; and
3. an operator may add local grants that a baseline must not erase.

The inventory is repository-local evidence for implementation. It is not a
release, adoption, deployment, or production-state claim.

## Audited revisions

| Repository | Revision |
| --- | --- |
| `dotmac_starter_mt` | `b17c9af728ad5642d43bbd64cc49bce1428b5baf` |
| `dotmac_erp` | `623c6d588c9b6df4ed9a162d5b0f07155c0f7314` |
| `dotmac_sub` | `1a3edf0eb567fe02665606d368f8f342536f548c` |
| `dotmac_workspace` | `dfbec84103fded5fa08a9c37fd6159674dabd0fa` |
| `dotmac_academy_app` | `a5e25e4e829350e503e66a03d73739529ba7da7f` |

## Qualification result

No audited product contains a complete, tested permission planner and
transactional applicator. The reusable contract therefore has to compose the
best existing sources rather than copy one implementation wholesale:

- Starter owns manifest declaration, duplicate-owner refusal, route metadata,
  and boot-time rejection of undeclared permission references.
- Sub's `app/services/rbac_catalog.py` is the strongest typed persistence
  owner: normalized natural keys, row locking, idempotent operations, audit,
  and versioned events.
- Sub's permission-copy migrations demonstrate explicit authorization
  transitions that preserve known holders.
- Starter's explicit admin shadow is the first assembly-owned role profile;
  ERP Expense is the named first persistence cutover from a standalone seed to
  a deployable permission migration.

The greenfield portion is a deterministic, previewable plan. No audited source
has one.

## Starter/kernel

Sources:

- `packages/dotmac-kernel/src/dotmac_kernel/permissions.py`
- `packages/dotmac-kernel/src/dotmac_kernel/deps.py`
- `packages/dotmac-kernel/src/dotmac_kernel/app_factory.py`
- `packages/dotmac-kernel/src/dotmac_kernel/models.py`
- `tests/unit/test_permissions.py`
- `tests/unit/test_permission_seam.py`
- `tests/architecture/test_permission_seam_is_single.py`

`PermissionCatalogue` already gives each code one module owner and mounted
routes carry exact permission metadata. The persistence model has tenant roles
and party-to-role grants but no stored permission or role-to-permission row.

The current `PermissionSpec.default_roles` is a compatibility binding, not the
target ownership model. It lets a reusable module name product roles and
forbids a permission with no initial role. Both properties conflict with the
assembly-owned model: an operator-only permission is legitimate, and an
installable module cannot know an adopter's role algebra.

## ERP

Sources:

- `app/models/rbac.py`
- `app/services/rbac.py`
- `app/api/rbac.py`
- `app/authz/expense.py`
- `app/authz/payment_execution.py`
- `app/authz/profile.py`
- `app/startup.py`
- `scripts/seed_rbac.py`
- `scripts/deploy.sh`
- `alembic/versions/20260311_seed_ap_permissions.py`
- `alembic/versions/20260723_seed_driver_fleet_rbac.py`
- `alembic/versions/20260826_provision_expense_permissions.py`
- `tests/test_rbac_services.py`
- `tests/migrations/test_driver_fleet_rbac.py`
- `tests/migrations/test_expense_permission_provisioning.py`
- `tests/architecture/test_authz_declarations_are_pure.py`
- `tests/architecture/test_money_routes_reject_read_permissions.py`

ERP persists a global permission catalogue and role-permission links. Its
legacy `ROLE_PERMISSIONS` dictionary is an untyped assembly profile. The normal
deploy runs Alembic and never runs `seed_rbac.py`, so a permission declared only
in the seed can ship dark. The candidate implementation at the audited revision
makes Expense and payment-execution declarations import-light, validates their
references at startup, and adds a self-contained Alembic migration for Expense's
35 permission keys and baseline grants.

That migration is additive: it preserves unknown permissions, descriptions,
roles, memberships, direct grants, and role-permission links; refuses inactive
desired state; and makes downgrade a no-op instead of guessing what another
owner intended. ERP's schema still records no owner or source on a
role-permission link. Global role names and the admin/token-scope bypass are
product behavior and must not become kernel policy. GitHub Actions run
`32969624189` passed the unit, migration, PostgreSQL integration, Docker,
security, type, lint, and pre-commit jobs for the exact audited revision. ERP
PR #370 subsequently merged that revision. This is not a release, deployment,
shared-package adoption, or production claim.

## Sub

Sources:

- `app/models/rbac.py`
- `app/services/rbac_catalog.py`
- `scripts/seed/seed_rbac.py`
- `alembic/versions/477_quote_send_permission.py`
- `alembic/versions/313_dispatch_granular_permissions.py`
- `alembic/versions/314_retire_coarse_dispatch_permission.py`
- `alembic/versions/315_reseller_granular_permissions.py`
- `tests/test_rbac_catalog_owner.py`
- `tests/architecture/test_rbac_catalog_boundary.py`
- `tests/architecture/test_rbac_seed_parity.py`
- `tests/architecture/test_router_mount_permission_keys.py`
- `tests/test_quote_send_permission_migration.py`

Sub supplies the qualifying persistence-owner behavior. It also demonstrates
why Stage 1 cannot revoke: `replace_seeded_role_permissions` removes every link
outside the desired seed for a seeded role, but `role_permissions` carries no
policy-owner provenance. An operator-added grant on that role is
indistinguishable from an obsolete seed grant.

Direct-principal grant rows record the granting actor, not the policy owner.
`SystemUserRole.source` describes principal-to-role provenance and does not
solve role-to-permission ownership. Sub's wildcard, alias, scope, and permission
grammar behavior remains product-owned.

## Workspace

Sources:

- `src/dotmac_workspace/launcher/guard.py`
- `src/dotmac_workspace/operator/guard.py`
- `src/dotmac_workspace/identity/bootstrap.py`
- `src/dotmac_workspace/operator/service.py`
- `tests/test_launcher_authorization.py`
- `tests/test_operator_routes.py`

Workspace references permission constants consistently, but derives its
bootstrap role from `PermissionSpec.default_roles[0]`. It has no persisted
role-to-permission mapping. This is compatibility evidence only; the reusable
contract must not generalize “first default role is the bootstrap role.”

## Academy

Sources:

- `app/models/rbac.py`
- `app/services/bootstrap.py`
- `tests/services/test_bootstrap.py`

Academy persists roles and memberships but not permissions. Its JSON and web
guards implement two different role algebras: JSON exact-matches `instructor`,
while web guards commonly treat `admin` as a superset. Academy contributes no
planner source, and facet adoption must not choose between those algebras.

## Stage 1 contract

Stage 1 is storage-neutral and additive only:

- `PermissionDefinition`: stable opaque code, description, and owning module.
- `RoleDefinition`: assembly-owned role key, description, and explicit
  create-if-missing policy.
- `RoleGrantProfile`: assembly-owned profile code, positive version, and exact
  role-permission grants, with both keys kept opaque and declared.
- `PermissionState`: existing active and inactive catalogue codes, resolved
  role keys, and effective role-permission links.
- `PermissionPlan`: deterministic normalized definitions, versioned profiles,
  grants, and SHA-256 digest; its diff exposes ordered additions, conflicts,
  and preserved state.

The planner may add a missing permission, a missing grant, and a missing role
whose assembly definition explicitly authorizes creation. An implicit missing
role and every inactive desired role are conflicts in Stage 1. It never
deletes, revokes, renames, reactivates, overwrites a description, removes a
direct grant, or rewrites an existing role. An inactive desired permission is a
conflict. An unknown profile permission is rejected while compiling the plan.

The product adapter applies a clean plan inside the product's migration
transaction. Application startup validates declarations and references but
performs no permission write.

## Stage 2 parity

The implementation must prove that literal, constant, metadata-stamped, and
derived route permission references resolve to declared codes. UI visibility
uses the same reference as its route, but hiding a control is never the guard.
An operator-only permission with no baseline role is valid and must not be
reported as orphaned merely because the assembly profile grants it to nobody.

## Stage 3 prerequisite for revocation

No subtractive reconciler is authorized until role-permission policy ownership
is persisted. A nullable `managed_by` on the effective link is insufficient:
an operator and a baseline profile can both want the same effective grant.

The required shape is a claim ledger keyed by role, permission, owner kind,
owner/profile code, and profile version. The effective role-permission
projection survives while any claim remains. Only then can one profile remove
its obsolete claim without erasing another owner's intent. Permission renames
and retirements remain explicit reviewed migrations.

## Non-claims

- No production permission catalogue or grant was inspected.
- Starter's new planner tests were not executed locally; CI owns their first
  acceptance run. ERP's candidate implementation was exercised by GitHub
  Actions run `32969624189` at the exact audited revision.
- ERP PR #370 is merged; it is not released or deployed by this inventory and
  does not consume the shared package.
- No role-grant provenance exists in the audited products.
- No shared package is released or reuse-proven; ERP is the first merged
  persistence implementation and does not yet consume the shared package.
