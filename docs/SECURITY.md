# Security posture

Verification target: **OWASP ASVS 5.0, Level 2** — mapped honestly below.
"Met" claims name the enforcing test; anything not met says so and points at
where the work is tracked (`docs/superpowers/phase2-backlog.md`, the
"2c-auth" section, unless noted). This document never claims-met-but-not.

## Architecture-level controls

| Control area | Status | Evidence / notes |
|---|---|---|
| Multi-tenant isolation (ASVS V4 access control) | **Met** | Postgres FORCE RLS on every tenant-scoped table; dynamic catalog audit `tests/test_rls_catalog.py` (RLS + FORCE + policy + grants + composite FKs + metadata parity, sensitivity self-tested); per-feature isolation canaries `tests/test_*_isolation.py` |
| Platform control-plane authentication | **Met** | Separate platform identity (`platform_admins`/`platform_sessions`), host-exact routing, `aud="platform"` token separation; deny-by-default canaries `tests/test_platform_auth_denies.py` (middleware and guard layers pinned independently) |
| Tenant provisioning integrity | **Met** | One-transaction provisioning with audit trail; atomicity canaries `tests/test_tenant_provisioning.py` |
| Route authorization coverage | **Met** | Every route carries a guard (`tests/architecture/test_route_guards.py`); mutating routes need an auth-tier guard; non-admin sweep `tests/unit/test_admin_route_sweep.py` |
| One transaction authority | **Met** | `tests/architecture/test_session_authority.py` (AST-based, sensitivity self-tested) |
| DB roles least-privilege | **Met** | `app_user`/`platform_api` have no BYPASSRLS/superuser; platform-private tables revoked from `app_user` — asserted by the catalog audit above |

## V2 Authentication (password + session)

| Control | Status | Evidence / notes |
|---|---|---|
| Password storage: Argon2id | **Met** | `dotmac_kernel/security.py` — argon2id, OWASP cheat-sheet parameters (m=19MiB, t=2, p=1); `tests/unit/test_security_baseline.py::TestPasswordStorage` |
| Legacy-hash migration | **Met** | PBKDF2 hashes verify and upgrade-on-login; `TestLoginHardening::test_legacy_hash_upgrades_on_successful_login` |
| User-enumeration resistance (timing) | **Met (login seam)** | Constant-work miss paths on tenant login AND platform login (dummy-hash verification, counter-asserted — `test_unknown_email_burns_a_dummy_verification`). Register still discloses duplicate email via 409 under an `open` policy — acceptable while the default policy is `closed`; revisit in 2c |
| Self-registration policy | **Met** | `auth.registration_policy` (default `closed`); 403 canaries in `tests/test_tenant_provisioning.py`; registered users receive no roles |
| Password complexity/length minimums | **Partial** | Length 8–256 enforced at the schema; no composition/breached-password checks — 2c-auth |
| MFA | **Not met — deferred** | 2c-auth backlog |
| Password reset / credential update flow | **Not met — deferred** | No reset path exists at all (deliberate: no half-secure email flow); 2c-auth |
| Account lockout / throttling per account | **Partial** | Global per-IP/tenant/route rate limiting only (below); per-ACCOUNT lockout is 2c-auth |
| Session revocation | **Met** | Server-side session rows (`auth_sessions`, `platform_sessions`), logout revokes; `tests/test_web_auth_isolation.py`, `tests/test_platform_auth_denies.py::test_platform_logout_revokes_the_session` |
| Session/token rotation, absolute + idle timeouts | **Partial** | Fixed TTL (`JWT_TTL_SECONDS`) with server-side expiry; no rotation or idle timeout — 2c-auth |

## V3/V13/V14 Web hardening

| Control | Status | Evidence / notes |
|---|---|---|
| CSRF | **Met** | Double-submit cookie + header bridge; every mutating template form is hx-*; `tests/test_security_middleware.py`, `tests/architecture/test_web_conventions.py` |
| Security response headers | **Met** | `SecurityHeadersMiddleware` (outermost): nosniff, DENY, referrer-policy, permissions-policy, HSTS-on-TLS, CSP; `tests/unit/test_security_baseline.py::TestSecurityHeaders`. Known limit: the last-resort unhandled-exception 500 (ServerErrorMiddleware) bypasses user middleware and carries no headers |
| Content-Security-Policy | **Met** | Computed-strict default (below); overridable via `CONTENT_SECURITY_POLICY` |
| Rate limiting | **Met (single-process)** | Bounded LRU store, route-template keys, hash-bucketed unmatched paths (`tests/unit/test_security_baseline.py::TestBoundedRateLimitStore`). Multi-process deployments must swap the store (seam below) |
| Output encoding / template escaping | **Met** | Jinja2 autoescape; `| safe` requires a nearby sanitize comment (`test_web_conventions.py`) and there are **zero** usages — tenant-supplied `custom_css` was retired 2026-08-13 (ADR-0006 D8), so no response carries tenant-authored CSS |
| Host-header integrity | **Met** | Tenant resolution is exact-host; `TrustedHostMiddleware` in prod (`TRUSTED_HOSTS` prod-required by `validate_settings`) |
| Secrets hygiene | **Met** | Dev-default secrets are prod-fatal (`validate_settings`); no secret values in repo |

## Content-Security-Policy rationale

The default CSP is computed from this codebase's actual asset inventory
(audited 2026-07-30, control-plane security Task 5):

```
default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline';
img-src 'self' data: https:; font-src 'self'; connect-src 'self';
object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

- Every script is a local `/static` file (htmx, Alpine, components.js,
  csrf.js). The one inline `<script>` block base.html used to carry was
  moved into `components.js` precisely so `script-src` needs no
  `'unsafe-inline'`.
- `'unsafe-eval'` is required by the standard Alpine.js build (`new
  Function` expression compilation). Swapping to Alpine's CSP build would
  remove it at the cost of rewriting `x-data` expressions — tracked as a
  nice-to-have, not a gap.
- Fonts are **vendored** (`static/fonts/`, latin subsets) per the
  cross-Dotmac no-CDN standard — no `fonts.googleapis.com`/
  `fonts.gstatic.com` origins anywhere.
- `style-src 'unsafe-inline'` is **not** for tenant CSS: `custom_css` was
  retired 2026-08-13 (ADR-0006 D8) and no tenant-authored `<style>` block is
  emitted anywhere. It remains for first-party inline `style="..."` attributes
  (the platform screens set `var(--dmui-*)` that way) and Alpine's `x-show`
  toggling. Recovering `style-src 'self'` needs those converted first and is a
  separate slice.
- `img-src https:` exists because tenant branding may point `logo_url` at
  an external image.

`tests/unit/test_security_baseline.py::test_strict_csp_has_no_external_origins`
pins the no-external-origins property.

## Rate-limit store swap seam

`dotmac_kernel/middleware/rate_limit.py` defines the `RateLimitStore` protocol;
the shipped `MemoryStore` is process-local and LRU-bounded
(`RATE_LIMIT_MAX_KEYS`). A multi-process/multi-node deployment provides a
Redis-backed implementation of the same `hit()` contract and passes it to
`RateLimitMiddleware`; the `RATE_LIMIT_REDIS_URL` knob is reserved for that
wiring. No redis dependency ships with the starter
(contracts-not-implementations).

## Reporting

This is a template repository. Projects built from it should replace this
section with their own security contact and disclosure policy.
