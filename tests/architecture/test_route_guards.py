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
    # GET/POST /admin/login (app.features.web.web) are genuinely pre-auth —
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
    # GET /admin/logout must succeed even with a missing/expired/garbled
    # cookie (clearing a stale cookie is not an auth failure) — it carries
    # only `Depends(require_tenant)` (tenant-scoped session revocation), no
    # `require_web_auth`. Same reasoning as `/admin/login` above.
    ("GET", "/admin/logout"),
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
