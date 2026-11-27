# Changelog — dotmac-access-admin

## 0.1.0a1 — unreleased

Source only; not published. Promoted from the reference app's `app/features/rbac`
(Starter parity programme, Wave 0 lane D).

- Users: list/search, create through `credential_lifecycle.provision` (no
  password material handled; a recovery intent is enqueued), deactivate and
  reactivate.
- Leaver revoke: every grant and every session revoked in ONE transaction, one
  audit event per grant plus a summary event.
- Roles: tenant roles are created and renamed; assembly-declared code roles are
  listed read-only. A composition check refuses any permission code no installed
  module declares.
- Grants and revokes, each audited, with separation-of-duties pair WARNINGS
  only (enforcement stays the tenant dual-control policy, default OFF).
- Contract-2 `staff` surface and JSON API.

- Kernel access is through `dotmac_kernel.identity_admin` (never the frozen
  `identity_storage`); the manifest declares
  `runtime_requires=(identity_admin.v1, tenant_audit_log.v1, outbox_relay.v1)`.
- Deactivate keeps grants (the kernel enforces `is_active`); reactivation
  restores them. Leaver revoke removes them.

### Before release

- Raise the kernel floor to the release that publishes `identity_admin`.

- Persisting a role's permission composition needs a kernel role-to-permission
  store and an evaluator that reads it (programme section 1.2); until then the
  composition check is a validator only.
- Reference app cutover: delete `app/features/rbac` once the audit viewer owns
  `/admin/audit` and the `audit` setting domain.
