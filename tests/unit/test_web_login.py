"""TDD for the web login/logout/dashboard flow (Task 3).

App-builder pattern from `tests/unit/test_settings_api.py`: bare `FastAPI()`
+ `register_error_handlers` + the REAL `app.features.auth.web.router`
(login/logout) and `app.features.web.web.router` (dashboard) mounted,
`get_db` overridden to the in-memory SQLite `db` fixture, and a thin
middleware standing in for `TenantResolverMiddleware`. `require_web_auth`
and `require_user_auth` are NOT overridden — this exercises the real cookie
auth path end to end (form -> service -> shared `authenticate_request` seam
-> dashboard), same spirit as `tests/unit/test_deps_auth.py` for the bearer
side.

Login/logout moved from `app.features.web.{web,service}` to
`app.features.auth.{web,service}` per Task 3 review's required fix (see
`.superpowers/sdd/task-3-report.md`'s fix note) — both routers are mounted
here since the flow under test spans both (login sets the cookie the
dashboard route then reads).

RLS doesn't exist on SQLite — cross-tenant isolation is proven separately by
the Postgres canary `tests/test_web_auth_isolation.py`.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.errors import register_error_handlers
from app.core.models import (
    Party,
    PartyPerson,
    PartyRole,
    PartyType,
    Role,
    Tenant,
    UserCredential,
)
from app.core.security import hash_password
from app.core.settings_models import SettingDomain
from app.core.settings_resolver import upsert_by_key
from app.core.web_deps import safe_next_url
from app.features.auth.web import router as auth_web_router

# Import for the side effect: registers branding/ui_branding into the
# resolver registry (`load_branding` calls `resolve_value` against it).
from app.features.settings import spec as _settings_spec  # noqa: F401
from app.features.web.web import router as web_router

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def provisioned_admin(db: Session, tenant_row: Tenant) -> dict:
    """A provisioned admin — party + person + credential + "admin" role
    grant, built directly on core models. Registration no longer grants any
    role (control-plane security Task 2: admins are provisioned, not
    registered), so this is the SQLite mirror of
    `tests/conftest.py::provision_owner`.
    """
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Admin User",
        email="admin@example.com",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Admin", last_name="User"))
    db.add(
        UserCredential(
            tenant_id=tenant_row.id,
            party_id=party.id,
            password_hash=hash_password(PASSWORD),
        )
    )
    role = Role(tenant_id=tenant_row.id, slug="admin", name="Admin")
    db.add(role)
    db.flush()
    db.add(PartyRole(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
    db.commit()
    return {"email": party.email, "party_id": party.id}


@pytest.fixture()
def non_admin_party(db: Session, tenant_row: Tenant) -> Party:
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
def web_client(db: Session, tenant_row: Tenant) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_web_router)
    app.include_router(web_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# _safe_next_url matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/ok", "/ok"),
        ("/ok?x=1", "/ok?x=1"),
        ("//evil.example.com", "/admin"),
        ("http://evil.example.com", "/admin"),
        ("https://evil.example.com/admin", "/admin"),
        ("/x?u=http://evil.example.com", "/admin"),
        ("", "/admin"),
        (None, "/admin"),
    ],
)
def test_safe_next_url(raw, expected) -> None:
    assert safe_next_url(raw) == expected


# ---------------------------------------------------------------------------
# GET /admin/login — unguarded, always 200
# ---------------------------------------------------------------------------


def test_login_page_renders_200(web_client: TestClient) -> None:
    resp = web_client.get("/admin/login")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert 'name="username"' in resp.text
    assert 'name="password"' in resp.text
    assert 'name="next"' in resp.text


def test_login_page_sanitizes_next_query_param(web_client: TestClient) -> None:
    resp = web_client.get("/admin/login?next=http://evil.example.com")
    assert resp.status_code == 200
    assert 'value="/admin"' in resp.text
    assert "evil.example.com" not in resp.text


# ---------------------------------------------------------------------------
# POST /admin/login
# ---------------------------------------------------------------------------


def test_post_bad_credentials_rerenders_200_no_cookie(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    resp = web_client.post(
        "/admin/login",
        data={"username": provisioned_admin["email"], "password": "wrong-password"},
    )
    assert resp.status_code == 200
    assert "Invalid username or password" in resp.text
    assert "access_token" not in resp.cookies


def test_post_missing_fields_rerenders_200_with_error(web_client: TestClient) -> None:
    resp = web_client.post("/admin/login", data={"username": "", "password": ""})
    assert resp.status_code == 200
    assert "required" in resp.text.lower()
    assert "access_token" not in resp.cookies


def test_post_good_credentials_redirects_and_sets_cookie(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    resp = web_client.post(
        "/admin/login",
        data={"username": provisioned_admin["email"], "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin"
    assert resp.headers["hx-redirect"] == "/admin"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    # Plain http TestClient request -> not secure -> no Secure flag.
    assert "Secure" not in set_cookie


def test_post_good_credentials_honors_safe_next(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    resp = web_client.post(
        "/admin/login",
        data={
            "username": provisioned_admin["email"],
            "password": PASSWORD,
            "next": "/admin/parties",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/parties"


def test_post_good_credentials_rejects_unsafe_next(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    resp = web_client.post(
        "/admin/login",
        data={
            "username": provisioned_admin["email"],
            "password": PASSWORD,
            "next": "http://evil.example.com",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin"


# ---------------------------------------------------------------------------
# GET /admin — guarded dashboard
# ---------------------------------------------------------------------------


def test_dashboard_without_cookie_redirects_to_login(web_client: TestClient) -> None:
    resp = web_client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/admin/login?next=")


def test_dashboard_with_valid_admin_cookie_renders_200(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    login = web_client.post(
        "/admin/login",
        data={"username": provisioned_admin["email"], "password": PASSWORD},
        follow_redirects=False,
    )
    token = login.cookies["access_token"]

    resp = web_client.get("/admin", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "Dashboard" in resp.text
    assert 'aria-current="page"' in resp.text


def test_dashboard_rejects_non_admin_party(
    web_client: TestClient,
    db: Session,
    tenant_row: Tenant,
    non_admin_party: Party,
) -> None:
    from app.core.models import AuthSession
    from app.core.security import hash_token, issue_access_token

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

    resp = web_client.get(
        "/admin", cookies={"access_token": token}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/admin/login")


# ---------------------------------------------------------------------------
# POST /admin/logout (F7 — was GET; see app.features.auth.web's docstring).
# This fixture's bare FastAPI() app carries no CSRFMiddleware, so these
# calls need no `x-csrf-token` header — the real CSRF-enforcement proof
# lives in `tests/test_security_middleware.py` (unit) and
# `tests/test_admin_portal_e2e.py` (Postgres, full stack).
# ---------------------------------------------------------------------------


def test_logout_clears_cookie_and_redirects(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    login = web_client.post(
        "/admin/login",
        data={"username": provisioned_admin["email"], "password": PASSWORD},
        follow_redirects=False,
    )
    token = login.cookies["access_token"]

    resp = web_client.post(
        "/admin/logout", cookies={"access_token": token}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"
    # Pin the htmx redirect header (test_web_login.py:183's pattern) — a
    # plain 302 Location is not enough for the topbar's `hx-post` Sign Out
    # control; without `HX-Redirect` htmx would swap the followed
    # redirect's body into the topbar instead of navigating.
    assert resp.headers["hx-redirect"] == "/admin/login"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    assert 'access_token=""' in set_cookie or "Max-Age=0" in set_cookie

    # The revoked session no longer authenticates the dashboard.
    resp2 = web_client.get(
        "/admin", cookies={"access_token": token}, follow_redirects=False
    )
    assert resp2.status_code == 302


def test_logout_without_cookie_still_redirects(web_client: TestClient) -> None:
    resp = web_client.post("/admin/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"


def test_logout_get_is_removed(web_client: TestClient) -> None:
    """F7's exact vector: `GET /admin/logout` must no longer exist — a
    CSRF-exempt safe method that mutated session state (forced-logout
    CSRF). FastAPI returns 405 for a known path/wrong method, not 404."""
    resp = web_client.get("/admin/logout", follow_redirects=False)
    assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Task 4 / F4: per-request tenant branding shows up portal-wide, not just in
# the branding editor's own preview -- login page (2 call sites, pre-auth)
# and the authenticated dashboard (via `require_web_auth`, call site 1/3).
# Real cross-tenant/Postgres proof lives in
# `tests/test_branding_portal_e2e.py`; these are the fast SQLite-level
# wiring proofs.
# ---------------------------------------------------------------------------


def test_login_page_reflects_tenant_saved_branding(
    web_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"name": "Acme Tenant Brand"},
        tenant_id=tenant_row.id,
    )
    db.commit()

    resp = web_client.get("/admin/login")
    assert resp.status_code == 200
    assert "Acme Tenant Brand" in resp.text


def test_login_post_failure_rerender_reflects_tenant_saved_branding(
    web_client: TestClient, db: Session, tenant_row: Tenant, provisioned_admin: dict
) -> None:
    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"name": "Acme Tenant Brand"},
        tenant_id=tenant_row.id,
    )
    db.commit()

    resp = web_client.post(
        "/admin/login",
        data={"username": provisioned_admin["email"], "password": "wrong-password"},
    )
    assert resp.status_code == 200
    assert "Acme Tenant Brand" in resp.text


def test_dashboard_sidebar_reflects_tenant_saved_branding(
    web_client: TestClient, db: Session, tenant_row: Tenant, provisioned_admin: dict
) -> None:
    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"name": "Acme Tenant Brand"},
        tenant_id=tenant_row.id,
    )
    db.commit()

    login = web_client.post(
        "/admin/login",
        data={"username": provisioned_admin["email"], "password": PASSWORD},
        follow_redirects=False,
    )
    token = login.cookies["access_token"]

    resp = web_client.get("/admin", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "Acme Tenant Brand" in resp.text


def test_login_page_uses_static_brand_when_no_tenant_override(
    web_client: TestClient,
) -> None:
    """No `ui_branding` override saved yet -- the login page still shows
    SOMETHING sane (the deployment-static brand), matching the pre-Task-4
    behavior for a tenant that never touched branding."""
    from app.core.branding import get_brand

    resp = web_client.get("/admin/login")
    assert resp.status_code == 200
    assert get_brand()["name"] in resp.text
