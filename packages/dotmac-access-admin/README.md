# dotmac-access-admin

Users, roles, grants and leaver revoke for a tenant, over the kernel identity
tables (`parties`, `roles`, `party_role_grants`, `auth_sessions`). The package is
**stateless**: it owns no table, schema or migration lineage.

- **Create** goes through `CredentialLifecycle.provision`. No password material
  is accepted, generated-and-returned, logged or audited; a durable recovery
  intent (`access_admin.recovery_requested.v1`, refs only) is enqueued.
- **Leaver revoke** removes every grant and revokes every session of a person in
  one transaction (one conflict SAVEPOINT) and writes one audit event per grant
  plus a summary event.
- **Roles**: tenant roles are created and renamed here. Roles an assembly
  declares in code (`PermissionSpec.default_roles`) are shown read-only, and a
  tenant role may not take a code role's slug (it would inherit its permissions).
  `POST /access-admin/roles/composition-check` refuses any undeclared permission.
- **Grants/revokes** are audited and carry separation-of-duties warnings from
  pairs the assembly registers with `register_sod_pairs`. Warnings never block:
  enforcement is the tenant dual-control policy, default off.

Surfaces are module contract 2: the JSON API under `/access-admin` and a `staff`
web surface; the assembly picks the facet prefix. Every route carries a declared
permission (`access_admin.*`).
