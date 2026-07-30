"""Every mounted route must carry an auth/tenancy guard dependency."""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import app

# Routes that are intentionally unauthenticated.
#
# Decision procedure: a route is only allowlisted if it carries NO `require_*`
# dependency at all (checked via `poetry run python -c` walking `app.routes`
# through the same `_guard_names` logic below). Router-level `dependencies=[...]`
# DO show up in `route.dependant.dependencies`, so a route mounted under a router
# that declares `Depends(require_tenant)` is already guarded and must NOT be
# allowlisted merely because it has no *route-level* guard.
ALLOWLIST = {
    # No tenant/auth context exists yet when this is called (used by infra
    # liveness/readiness checks and by the tenant resolver itself before a
    # tenant is known) — carries zero require_* dependencies, confirmed by
    # inspecting app.routes.
    ("GET", "/health"),
    # NOTE: POST /auth/login is intentionally NOT here — it is mounted under
    # `app.features.auth.router`, whose router-level
    # `dependencies=[Depends(require_tenant)]` already appears in its
    # `route.dependant.dependencies` (confirmed: guard names for this route
    # are {'get_db', 'require_tenant'}), so it already satisfies the guard
    # check without needing an allowlist entry.
    #
    # GET/POST /admin/login (app.features.auth.web) are genuinely pre-auth —
    # you cannot require a login to reach the login form — and are listed
    # here explicitly (Task 3 brief) even though both routes also carry a
    # route-level `Depends(require_tenant)` (tenant-scoping the login page
    # itself: the login target is always a specific tenant, resolved from
    # the Host header) which would independently satisfy the guard-name
    # check on its own, same as the `/auth/login` case above. The explicit
    # entries make the "no auth required here" intent load-bearing and
    # future-proof rather than an accident of `require_tenant`'s naming.
    ("GET", "/admin/login"),
    ("POST", "/admin/login"),
    # NOTE: POST /admin/logout (F7 — was GET, a CSRF-exempt safe method; see
    # `app.features.auth.web`'s module docstring) is intentionally NOT here.
    # It carries `Depends(require_tenant)` — which independently satisfies
    # this any-`require_*` check on its own — so it needs no allowlist entry
    # for THIS test. It IS listed in `MUTATING_ALLOWLIST` below, for the
    # stricter auth-tier check, with its own comment.
}


def _guard_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            names.add(getattr(dep.call, "__name__", ""))
        stack.extend(dep.dependencies)
    return names


def test_every_route_has_a_guard() -> None:
    missing: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if (method, route.path) in ALLOWLIST:
                continue
            if not any(n.startswith("require_") for n in _guard_names(route)):
                missing.append(f"{method} {route.path}")
    assert not missing, "Unguarded routes:\n" + "\n".join(sorted(missing))


# ---------------------------------------------------------------------------
# Task 8: tiered guard test.
#
# `test_every_route_has_a_guard` above accepts ANY `require_*`-prefixed
# dependency name, so it cannot distinguish TENANCY (`require_tenant` — "this
# request resolved to a known tenant") from AUTHENTICATION ("this request
# carries a verified, logged-in actor"). That gap is exactly how a mutation
# reachable with only a resolved tenant (no authenticated actor) passed the
# architecture suite for two tasks — see
# `docs/superpowers/phase2-backlog.md`'s "Governance-check evasion notes".
# This test closes that hole for every MUTATING (POST/PUT/PATCH/DELETE)
# route: it must carry a guard from the explicit AUTH_GUARD_NAMES set below,
# not merely something starting with `require_`.
# ---------------------------------------------------------------------------

# The dependency-callable names that prove an AUTHENTICATED actor (or, for
# `require_platform`, a deliberately no-tenant platform-admin context) — NOT
# derived from a `require_` prefix match, hand-built precisely because
# `require_tenant` also starts with `require_` and must NOT count as one of
# these:
#   - "require_user_auth" (app.core.deps): bearer-token JSON API guard —
#     validates the token/session and returns the authenticated Party.
#   - "require_role" (app.core.deps): `require_role(role_slug)` returns a
#     closure literally named `_dependency` (see that function), so this
#     exact string never appears in `_guard_names` — it is listed here for
#     documentation/future-proofing only (e.g. if a later refactor names the
#     closure via `functools.wraps`). The REAL enforcement for every
#     `Depends(require_role(...))`-guarded route comes from that closure's
#     own `Depends(require_user_auth)` sub-dependency, which DOES surface
#     under its own name via `_guard_names`'s recursive walk.
#   - "require_web_auth" (app.core.web_deps): cookie-based web-portal guard
#     — requires a valid session cookie AND the "admin" role.
#   - "require_platform_admin" (app.core.platform_auth): THE platform guard
#     (control-plane security Task 1) — host must equal the platform root
#     domain exactly, bearer token must be a live platform session with
#     `aud="platform"`, and the admin must be active. It replaced the old
#     unauthenticated `require_platform` stub, which asserted only "no
#     tenant resolved" and authenticated NOBODY. NOTE: `require_platform_host`
#     (same module) is NOT in this set on purpose — it is the pre-auth
#     platform-surface check (host only, no actor), the platform analogue of
#     `require_tenant`.
AUTH_GUARD_NAMES = {
    "require_user_auth",
    "require_role",
    "require_web_auth",
    "require_platform_admin",
}

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Mutating routes that are genuinely pre-auth — the "you cannot require a
# login to log in" family. Every entry needs its own justifying comment;
# there is no blanket allowlist-by-pattern (mirrors `ALLOWLIST` above).
MUTATING_ALLOWLIST = {
    # JSON API register (`app.features.auth.router`): the very first request
    # a not-yet-a-user makes. Guarded only by router-level
    # `Depends(require_tenant)` — correct, since no party/credential exists
    # yet to authenticate against.
    ("POST", "/auth/register"),
    # JSON API login (`app.features.auth.router`): same reasoning — this
    # route IS how you become authenticated, so it cannot itself require
    # authentication.
    ("POST", "/auth/login"),
    # Web portal login form submit (`app.features.auth.web`) — the cookie
    # equivalent of the above, already in `ALLOWLIST` for the any-`require_*`
    # check; repeated here for the tiered check for the identical reason.
    ("POST", "/admin/login"),
    # Web portal logout (`app.features.auth.web`, F7 fix — was a CSRF-exempt
    # `GET /admin/logout`). Deliberately carries `require_tenant` ONLY, no
    # auth-tier guard: session self-termination must not require a role/
    # auth-tier check — revoking YOUR OWN session is always allowed for any
    # authenticated cookie, admin or not (matches `POST /admin/login`'s "you
    # cannot require login to log in" reasoning, mirrored here as "you
    # cannot require an authorization check to log out"). CSRF protection —
    # not a role check — is what stops a FORCED logout now; that's exactly
    # what `test_csrf_*` in `tests/test_security_middleware.py` proves, and
    # what makes this route safe to allowlist here.
    ("POST", "/admin/logout"),
    # Platform login (`app.core.platform_auth.platform_auth_router`): the
    # platform counterpart of `/auth/login` — this route IS how a platform
    # admin becomes authenticated, so it cannot require authentication. It
    # still carries `Depends(require_platform_host)` (host-exact: the login
    # endpoint does not even exist off the platform root domain), which
    # satisfies the any-`require_*` check above; this entry exempts it only
    # from the auth-TIER requirement. POST /platform/auth/logout is
    # deliberately NOT here — it carries `require_platform_admin`.
    ("POST", "/platform/auth/login"),
}


def test_mutating_routes_require_an_auth_tier_guard() -> None:
    missing: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method not in _MUTATING_METHODS:
                continue
            if (method, route.path) in MUTATING_ALLOWLIST:
                continue
            if not (_guard_names(route) & AUTH_GUARD_NAMES):
                missing.append(f"{method} {route.path}")
    assert not missing, (
        "Mutating route(s) without an authentication-tier guard "
        "(require_tenant alone does not count — see AUTH_GUARD_NAMES's "
        "comment):\n" + "\n".join(sorted(missing))
    )
