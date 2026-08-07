"""Task 8, deliverable 3: per-route non-admin sweep (security-review follow-up).

An authenticated but NON-admin person-party cookie must get a 302 (never
200 or 500) on every MUTATING `/admin/*` route. `require_web_auth`
(`dotmac_kernel.web_deps`) requires the "admin" role today — that requirement was
previously proven for exactly one route
(`tests/unit/test_web_login.py::test_dashboard_rejects_non_admin_party`,
`GET /admin`). This file generalizes that proof to EVERY mutating admin
route, parametrized, so a future route that forgets to sit behind
`require_web_auth` (or sits behind something weaker) fails immediately
instead of silently allowing a non-admin party to reach a mutation.

Routes are enumerated from the REAL production app (`app.main.app.routes`)
at collection time — not a hardcoded list — so a new web.py mutation is
swept automatically. `POST /admin/login` is skipped: it is genuinely
pre-auth (see `tests/architecture/test_route_guards.py`'s
`MUTATING_ALLOWLIST` for the same route, same reasoning).

SCOPE LIMITATION: The `/admin` path-prefix enumeration assumption means a
future non-admin portal surface escapes this sweep entirely until its routes
are added to `_admin_mutating_routes()` — extend the prefix pattern in the
same task that adds such a surface.

Requests are executed against a purpose-built minimal app (same pattern as
`tests/unit/test_web_login.py`'s `web_client` fixture) that mounts every
`/admin/*` web router with `get_db` overridden to the in-memory SQLite `db`
fixture and a thin middleware standing in for `TenantResolverMiddleware` —
the production app's own middleware opens its own DB connection outside
dependency injection (`dotmac_kernel.middleware.tenant._resolve` calls
`SessionLocal()` directly) and cannot run against SQLite. Because
`app.include_router(router)` adds no extra prefix
(`dotmac_kernel.features.mount_features`), the paths on this minimal app are
byte-identical to the production app's — enumerating routes from one and
executing requests against the other is safe.
"""

from __future__ import annotations

import re
from collections.abc import Generator

import pytest
from dotmac_kernel.deps import get_db
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.models import AuthSession, Party, PartyPerson, PartyType, Tenant
from dotmac_kernel.security import hash_token, issue_access_token

# Installed MODULE web routers are swept too — the sweep enumerates from the
# production app, so a module route omitted here would 404 on this minimal
# app and read as a failure rather than as a missing mount.
from dotmac_template_studio.web import router as template_studio_web_router
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.features.auth.web import router as auth_web_router
from app.features.custom_fields.web import router as custom_fields_web_router
from app.features.parties.web import router as parties_web_router
from app.features.rbac.web import router as rbac_web_router
from app.features.settings.web import router as settings_web_router
from app.features.web.web import router as web_router
from app.main import app as production_app

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Mirrors tests/architecture/test_route_guards.py::MUTATING_ALLOWLIST's
# ("POST", "/admin/login") entry — same route, same "can't require login to
# reach the login form" reasoning.
#
# ("POST", "/admin/logout") is ALSO skipped (F7): this sweep's assertion is
# "a non-admin cookie gets a 302 to /admin/login because require_web_auth's
# admin-role check rejected it before the route body ran". Logout carries
# NO such check by design (`app.features.auth.web`'s module docstring —
# session self-termination must not require role/auth-tier) and its own
# success path ALSO redirects to `/admin/login` with a 302 — so a non-admin
# cookie would coincidentally satisfy this test's literal assertions for an
# entirely unrelated reason (it logged out successfully, not "was blocked").
# Skipping it explicitly keeps this sweep honest: it only proves what it
# says it proves for routes that are actually guarded by `require_web_auth`.
_SKIP = {("POST", "/admin/login"), ("POST", "/admin/logout")}

_DUMMY_UUID = "00000000-0000-0000-0000-000000000000"
# Path params typed `int` on some route — see `_concrete_path`.
_NUMERIC_PARAMS = frozenset({"version"})
_PARAM_RE = re.compile(r"\{(\w+)\}")


def _admin_mutating_routes() -> list[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in production_app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/admin"):
            continue
        for method in route.methods or set():
            if method not in _MUTATING_METHODS:
                continue
            if (method, route.path) in _SKIP:
                continue
            routes.add((method, route.path))
    return sorted(routes)


def _concrete_path(path_template: str) -> str:
    """Fill any `{param}` placeholder with a syntactically-valid dummy value
    (a well-formed UUID for anything named `*_id`, a plain string
    otherwise). `require_web_auth` rejects the request as a sub-dependency
    before the endpoint's own path-param validation runs, so the exact
    value never matters for this test's assertion — but using well-formed
    values keeps the sweep robust even if that dependency-resolution
    ordering ever changes.
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.endswith("_id"):
            return _DUMMY_UUID
        # An int-typed param (e.g. a template `version`) must be filled with
        # something that parses as one. A non-numeric placeholder makes the
        # route 404 before any guard runs, which would read as "the route is
        # unguarded" when it is really "the sweep addressed nothing".
        if name in _NUMERIC_PARAMS:
            return "1"
        return "dummy"

    return _PARAM_RE.sub(_replace, path_template)


@pytest.fixture()
def non_admin_party(db: Session, tenant_row: Tenant) -> Party:
    """A person-type `Party` with no role grant at all — the "authenticated
    but not admin" actor this sweep exists to exercise. Mirrors
    `tests/unit/test_web_login.py::non_admin_party`.
    """
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Second User",
        email="second@example.com",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Second", last_name="User"))
    db.flush()
    return party


@pytest.fixture()
def non_admin_cookie(db: Session, tenant_row: Tenant, non_admin_party: Party) -> str:
    """A valid, unexpired access token for `non_admin_party`, backed by a
    real `AuthSession` row — same construction as
    `test_web_login.py::test_dashboard_rejects_non_admin_party`.
    """
    token, expires_at = issue_access_token(non_admin_party.id, tenant_row.id)
    db.add(
        AuthSession(
            tenant_id=tenant_row.id,
            party_id=non_admin_party.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    db.flush()
    return token


@pytest.fixture()
def sweep_client(db: Session, tenant_row: Tenant) -> TestClient:
    """Every `/admin/*` web router, mounted on a bare FastAPI app — see
    module docstring for why this stands in for the production app.
    """
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_web_router)
    app.include_router(web_router)
    app.include_router(parties_web_router)
    app.include_router(rbac_web_router)
    app.include_router(settings_web_router)
    app.include_router(custom_fields_web_router)
    app.include_router(template_studio_web_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(("method", "path"), _admin_mutating_routes())
def test_non_admin_cookie_gets_redirected_not_200_or_500(
    sweep_client: TestClient,
    non_admin_cookie: str,
    method: str,
    path: str,
) -> None:
    resp = sweep_client.request(
        method,
        _concrete_path(path),
        cookies={"access_token": non_admin_cookie},
        follow_redirects=False,
    )
    assert resp.status_code == 302, (
        f"{method} {path} returned {resp.status_code} for a non-admin party "
        "cookie (expected 302 — require_web_auth's admin-role check must "
        "reject this before the route body ever runs)"
    )
    assert resp.headers.get("location", "").startswith("/admin/login")


def test_sweep_covers_at_least_one_route_per_admin_feature() -> None:
    """Guards against the parametrize list silently going empty (e.g. a
    refactor that changes every route's prefix away from `/admin`) — a
    parametrize over an empty list still "passes" with zero test cases,
    which would hide a total loss of coverage. Pin the expected feature
    prefixes so a missing one is loud.
    """
    routes = _admin_mutating_routes()
    prefixes = {
        "/admin/parties",
        "/admin/roles",
        "/admin/role-grants",
        "/admin/settings",
        "/admin/custom-fields",
    }
    seen = {
        prefix for prefix in prefixes for _, path in routes if path.startswith(prefix)
    }
    assert seen == prefixes, f"Missing mutating-route coverage for: {prefixes - seen}"
