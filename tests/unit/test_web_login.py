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
from app.core.models import Party, PartyPerson, PartyType, Tenant
from app.core.web_deps import safe_next_url
from app.features.auth import service as auth_service
from app.features.auth.schemas import RegisterRequest
from app.features.auth.web import router as auth_web_router
from app.features.web.web import router as web_router

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def registered_admin(db: Session, tenant_row: Tenant) -> dict:
    """First registered user in a tenant auto-gets the admin role
    (`app.features.auth.service._assign_first_user_admin`) — reuse that real
    flow rather than hand-building a Party/Role/PartyRole trio, since it's
    also proof `web_login` correctly threads through unchanged register/login
    behavior.
    """
    view = auth_service.register(
        db,
        tenant_row,
        RegisterRequest(
            email="admin@example.com",
            password=PASSWORD,
            first_name="Admin",
            last_name="User",
        ),
    )
    db.commit()
    return {"email": view.email, "party_id": view.id}


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
    web_client: TestClient, registered_admin: dict
) -> None:
    resp = web_client.post(
        "/admin/login",
        data={"username": registered_admin["email"], "password": "wrong-password"},
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
    web_client: TestClient, registered_admin: dict
) -> None:
    resp = web_client.post(
        "/admin/login",
        data={"username": registered_admin["email"], "password": PASSWORD},
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
    web_client: TestClient, registered_admin: dict
) -> None:
    resp = web_client.post(
        "/admin/login",
        data={
            "username": registered_admin["email"],
            "password": PASSWORD,
            "next": "/admin/parties",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/parties"


def test_post_good_credentials_rejects_unsafe_next(
    web_client: TestClient, registered_admin: dict
) -> None:
    resp = web_client.post(
        "/admin/login",
        data={
            "username": registered_admin["email"],
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
    web_client: TestClient, registered_admin: dict
) -> None:
    login = web_client.post(
        "/admin/login",
        data={"username": registered_admin["email"], "password": PASSWORD},
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
# GET /admin/logout
# ---------------------------------------------------------------------------


def test_logout_clears_cookie_and_redirects(
    web_client: TestClient, registered_admin: dict
) -> None:
    login = web_client.post(
        "/admin/login",
        data={"username": registered_admin["email"], "password": PASSWORD},
        follow_redirects=False,
    )
    token = login.cookies["access_token"]

    resp = web_client.get(
        "/admin/logout", cookies={"access_token": token}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    assert 'access_token=""' in set_cookie or "Max-Age=0" in set_cookie

    # The revoked session no longer authenticates the dashboard.
    resp2 = web_client.get(
        "/admin", cookies={"access_token": token}, follow_redirects=False
    )
    assert resp2.status_code == 302


def test_logout_without_cookie_still_redirects(web_client: TestClient) -> None:
    resp = web_client.get("/admin/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/login"
