# ADR 0004 — Platform Control Plane Security

**Status:** Accepted
**Date:** 2026-07-30
**Supersedes:** ADR-0001's "platform admin" sketch (the `require_platform_admin`-as-a-tenant-role
description under its resolver failure modes) and the interim unauthenticated
`require_platform` stub it was implemented as.
**Extends:** ADR-0001 multi-tenancy, ADR-0002 model-placement rule, ADR-0003 deployment profiles.

## Context

Until the control-plane security work
(`docs/superpowers/plans/2026-07-18-control-plane-security.md`, adoption review
`docs/superpowers/reviews/2026-07-18-adoption-review.md`), the platform surface was
not actually secured: `require_platform` authenticated nobody (it only asserted
`request.state.tenant is None`), the tenant-resolver middleware forwarded
`/platform/*` on ANY host (a `startswith("/platform/")` branch), `create_tenant`
wrote only the `tenants` row (no owner, no audit), and the first person to
register in a fresh tenant was race-promoted to admin. No mechanism to
authenticate a platform actor existed at all — `authenticate_request` is hard
tenant-bound and returns `None` when `request.state.tenant is None`.

This ADR records the decisions implemented in Tasks 1–5 of that plan. The
as-built reference is `docs/ARCHITECTURE.md`; the ASVS mapping is
`docs/SECURITY.md`.

## Decisions

### D1. Separate platform identity — not tenant Party rows

Platform actors get their OWN identity tables: `platform_admins` and
`platform_sessions` (`app/core/models_platform.py`, migration
`alembic/versions/20260730_0007_platform_identity.py`). They are platform
catalog tables like `tenants`: no `tenant_id`, no RLS, GRANTed to
`platform_api` + `app_admin` only and explicitly REVOKEd from `app_user` in the
same migration — a tenant-scoped application session cannot even SELECT a
platform credential row (asserted by the RLS catalog audit,
`tests/test_rls_catalog.py`).

**Alternatives considered and rejected:**

- **Tenant-`Party` platform actors.** `Party`/`AuthSession` are tenant-bound
  *by design*: composite `(tenant_id, ...)` FKs, a tenant-claim JWT, RLS
  policies keyed on `app.current_tenant`. A platform actor exists with no
  tenant context at all; reusing the tenant model would mean punching holes in
  the tenant-claim checks that every tenant token relies on.
- **A fake "platform tenant" row.** Would let the tenant machinery "work", but
  every isolation invariant (RLS canaries, tenant-claim checks, per-tenant
  uniqueness) would then have a magic tenant that means something entirely
  different — a standing source of confused-deputy bugs and one slug collision
  away from a real tenant impersonating the control plane.

### D2. OIDC is deliberately NOT the default; `require_platform_admin` is the one replaceable seam

A starter cannot assume an IdP exists — local password auth against
`platform_admins` is the default. `app.core.platform_auth.require_platform_admin`
is the single guard every `/platform/*` route (except pre-auth login, which uses
`require_platform_host`) depends on, and the single seam a project replaces to
adopt OIDC.

**The swap contract:** a replacement guard must keep (1) the exact-host check
(`require_platform_host` — the surface 404s off the platform root host) and
(3) the active-admin check, and may replace only (2) the token-validation step
(bearer JWT `aud="platform"` + live `platform_sessions` row) with IdP token
validation. It must return the authenticated platform actor and raise 401 on
any authentication failure. Nothing else in the codebase validates a platform
credential, so the swap is one function.

### D3. Exact-host platform routing, defense-in-depth, layer-distinguishable envelopes

Platform routes resolve ONLY on `PLATFORM_ROOT_DOMAIN`. Two independent layers
enforce this:

- **Middleware** — `app.core.middleware.tenant._is_platform_path` is host-exact:
  the old `startswith("/platform/")` branch is deleted, so a `/platform/*`
  request on a tenant or unknown host 404s in the middleware like any other
  unresolved path (before-proof: on the old code, an unauthenticated
  `GET /platform/tenants` from an unknown host returned 200).
- **Guard** — `require_platform_admin`/`require_platform_host` re-check the
  host themselves, so a middleware regression alone cannot re-expose the
  surface.

The deny-by-default canaries (`tests/test_platform_auth_denies.py`) pin the two
layers INDEPENDENTLY: the middleware refusal is asserted via its distinct
`tenant_not_found` envelope code, so the guard's defense-in-depth 404 cannot
mask a middleware regression (sensitivity-proven RED with the `startswith`
branch temporarily restored).

### D4. `aud="platform"` token separation — structural rejection both directions

One signer, two token populations that can never cross:

- Tenant tokens (`app.core.security.issue_access_token`): `sub` = party id,
  `tenant_id` claim, no `aud` claim.
- Platform tokens (`app.core.platform_auth.issue_platform_token`): `sub` =
  platform-admin id, `aud="platform"`, no tenant claim.

`require_platform_admin` rejects any token whose `aud` is not exactly
`"platform"`; `app.core.deps.authenticate_request` requires a `tenant_id` claim
that platform tokens do not carry. Each surface structurally rejects the other
population's tokens — no shared session table, no shared claim shape to
confuse. Both sides additionally require a live server-side session row
(`auth_sessions` / `platform_sessions`), so revocation works independently per
surface.

### D5. CLI-only admin bootstrap — no HTTP self-registration, ever

`scripts/create_platform_admin.py` is the ONLY way a platform admin comes into
existence (idempotent upsert-by-email, password prompted, `--inactive`
supported). Running it requires direct platform/migration DB credentials
(`MIGRATION_DATABASE_URL`/`PLATFORM_DATABASE_URL`) — the same trust boundary as
running migrations. There is deliberately no HTTP self-registration path for
the control plane: any such endpoint would be a standing
first-to-find-it-owns-the-fleet race, the exact bug class D7 removed from the
tenant surface.

### D6. Atomic audited tenant provisioning; `SET LOCAL` on the platform session

`POST /platform/tenants` (`app.features.tenants.service.provision_tenant`) is
ONE transaction on the platform session: tenant row → `SET LOCAL
app.current_tenant` → owner `Party(person)` + `PartyPerson` + `UserCredential`
→ `admin` `Role` + `PartyRole` grant → two audit events
(`platform.tenant.create`, `platform.tenant.owner_provision`, both naming the
platform actor's email in `details` — platform admins are not tenant parties,
so `actor_party_id` stays NULL). Any failure rolls the whole transaction back:
a tenant without a login-able owner can never persist
(`tests/test_tenant_provisioning.py`).

The `SET LOCAL` idiom matters and is the sanctioned pattern for any future
platform-session code that must write tenant-scoped rows: `platform_api` has no
`BYPASSRLS` and the owner-side tables are FORCE-RLS, so tenant context is
established explicitly on the current transaction —
`db.execute(select(func.set_config("app.current_tenant", str(tenant.id), True)))`
— the same `set_config(..., is_local := true)` idiom `get_db` uses. See
`docs/ARCHITECTURE.md` § "Transaction authority".

`UserCredential` moved from the `auth` feature to `app/core/models.py`
(PORT-DELTA, Task 2): `tenants` cannot import the `auth` feature, so the model
joined the other identity models under ADR-0002's placement rule; all hashing
stays in `app.core.security`.

### D7. Registration policy default `closed`; first-registrant bootstrap deleted

`auth.registration_policy` is an explicit tenant-scoped `SettingSpec` (string,
`{open, closed}`, default `closed`); `register()` reads it and returns 403
`registration_closed` when closed. The race-prone
`_assign_first_user_admin` check-then-insert bootstrap is DELETED — registering
NEVER grants a role, under any policy; provisioning is the only
owner/admin-creation path.

### D8. Platform auth routes mount directly in `main.py` — not a feature manifest

`POST /platform/auth/login` and `/logout` live in `app/core/platform_auth.py`
with a router included directly in `app/main.py`. The manifest/capability model
exists for TENANT capabilities (per-tenant enablement, admin-portal surfaces);
the platform control plane must exist even with every feature disabled
(`DISABLED_FEATURES=*`, `WEB_ENABLED=false`) or there would be no way to
operate the deployment at all.

### D9. One transaction authority — UnitOfWork deleted

`app/core/unit_of_work.py` was a second, zero-consumer transaction authority
and was deleted under the stronger source-of-truth rule (zero consumers →
delete), together with its `ConcurrencyConflict`. `app/core/db.py` is the one
transaction authority — boundaries (`get_db`/`get_platform_db`/
`platform_session`) own commit/rollback; services only mutate and flush —
enforced by the AST-based governance test
`tests/architecture/test_session_authority.py`. Full contract:
`docs/ARCHITECTURE.md` § "Transaction authority".

## Consequences

- An unauthenticated request cannot reach any `/platform/*` route from any
  host; middleware and guard are each independently sufficient and
  independently tested.
- Operating a deployment now requires the CLI bootstrap step before the first
  `POST /platform/tenants` — documented in the README quickstart. This is a
  BREAKING change (0.8.0): platform routes require auth, provisioning requires
  owner credentials in the payload, and self-registration is closed by default.
- Tenant tokens and platform tokens are structurally non-interchangeable; a
  future OIDC adoption replaces exactly one function (D2's swap contract).
- Password hashing for both populations is Argon2id with legacy
  upgrade-on-login, and login is constant-work on the miss paths on both
  surfaces (`docs/SECURITY.md` for the full baseline: security headers/CSP,
  bounded rate-limit store contract, ASVS 5.0 L2 mapping).
- The platform-table grant model (`app_user` revoked) is a permanent catalog
  invariant enforced by `tests/test_rls_catalog.py` — any future platform
  table must be added to that test's allowlist with the same grants.

## References

- `docs/superpowers/plans/2026-07-18-control-plane-security.md` — the delivery plan (non-authoritative intent; this ADR + code are authoritative)
- `docs/ARCHITECTURE.md` — as-built reference (model provenance, ownership, transaction authority)
- `docs/SECURITY.md` — ASVS 5.0 L2 mapping, CSP rationale, rate-limit store seam
- `app/core/platform_auth.py`, `app/core/models_platform.py`, `scripts/create_platform_admin.py`
- `tests/test_platform_auth_denies.py`, `tests/test_tenant_provisioning.py`, `tests/test_rls_catalog.py`, `tests/architecture/test_session_authority.py`
- `docs/adr/0001-multi-tenant-architecture.md`, `0002-starter-consolidation.md`, `0003-unified-deployment-profiles.md`
