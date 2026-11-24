# Access administration source inventory

Date: 2026-11-24

Product-first evidence for `dotmac-access-admin` (Starter parity programme, Wave 0
lane D). It is repository-local evidence, not a release, adoption or production
claim. The permission model is inventoried in
[`permission-provisioning-sources.md`](permission-provisioning-sources.md).

## Sources

- Starter reference `app/features/rbac` (`service.py`, `router.py`, `web.py`): role
  create, list and grant; no revoke, no user lifecycle, one audit action per
  create/grant, web screens guarded by authentication only.
- Kernel `identity_storage`, `credential_lifecycle`, `permissions`,
  `permission_provisioning`, `deps`, `session_runtime`: the Party-first lock
  order, grant removal, session revocation, secret-free provisioning, the
  declared-permission catalogue and the `default_roles` evaluator.
- ERP v2 `access.py` and `access_cli.py`: six finance/payroll roles, SoD role
  pairs, plan/apply/revoke receipts. The CLI stays cutover tooling.
- Sub `app/services/rbac_catalog.py`: persistence owner for roles and
  permissions; not an HTTP surface.

## Findings that shaped the package

1. Authorization today is `PermissionSpec.default_roles`: a party satisfies a
   permission by holding a role with a listed slug. There is no stored
   role-to-permission row, so a runtime-composed role cannot be honoured. The
   package validates a composition against the declared catalogue and does not
   persist one.
2. A tenant role with the slug of a code role would inherit that code role's
   permissions, so such slugs are refused.
3. `authenticate_request` and `login` do not consult `Party.is_active`; revoking
   sessions and grants is what cuts access. Deactivate is therefore refused while
   grants remain, and leaver revoke removes grants and sessions together.
4. `user_credentials` has no reset flag or version column; a provisioned
   credential is unusable because its hash is of material nobody was told.
5. The recovery intent is a refs-only kernel outbox row
   (`access_admin.recovery_requested.v1`); delivery is the account/notification
   lanes' concern.
