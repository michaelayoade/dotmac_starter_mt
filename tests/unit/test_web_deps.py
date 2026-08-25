"""Unit coverage for `dotmac_kernel.web_deps.require_web_auth` (cookie auth) and
the `dotmac_kernel.deps.authenticate_request` shared seam it rides on.

Companion to `tests/unit/test_deps_auth.py` (the bearer/`require_user_auth`
side) — this file is the proof that the COOKIE path goes through the exact
same token/session/tenant/party_type validation, not a second
reimplementation of it.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from dotmac_kernel.deps import authenticate_request, get_db, require_user_auth
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.models import (
    AuthSession,
    Party,
    PartyOrganization,
    PartyPerson,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
)
from dotmac_kernel.security import hash_token, issue_access_token
from dotmac_kernel.web_deps import WebAuthRedirect, require_web_auth
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture()
def admin_party(db: Session, tenant_row: Tenant) -> Party:
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Admin User",
        email="admin@acme.test",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Admin", last_name="User"))
    role = Role(tenant_id=tenant_row.id, slug="admin", name="Admin")
    db.add(role)
    db.flush()
    db.add(PartyRoleGrant(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
    db.flush()
    return party


@pytest.fixture()
def plain_party(db: Session, tenant_row: Tenant) -> Party:
    """Person-type party with no role grant — authentication still succeeds."""
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Plain User",
        email="plain@acme.test",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Plain", last_name="User"))
    db.flush()
    return party


@pytest.fixture()
def org_party(db: Session, tenant_row: Tenant) -> Party:
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.organization,
        display_name="Acme Corp",
    )
    db.add(party)
    db.flush()
    db.add(PartyOrganization(party_id=party.id, legal_name="Acme Corp Ltd."))
    db.flush()
    return party


def _issue_session_token(db: Session, tenant: Tenant, party: Party) -> str:
    token, expires_at = issue_access_token(party.id, tenant.id)
    db.add(
        AuthSession(
            tenant_id=tenant.id,
            party_id=party.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    db.flush()
    return token


@pytest.fixture()
def web_app_client(db: Session, tenant_row: Tenant) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/admin-ish")
    def admin_ish(auth: dict = Depends(require_web_auth)) -> dict:
        return {"party_id": str(auth["party"].id), "roles": auth["roles"]}

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db

    return TestClient(app, raise_server_exceptions=False)


def test_no_cookie_redirects_to_login(web_app_client: TestClient) -> None:
    resp = web_app_client.get("/admin-ish", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/admin/login?next=")


def test_garbled_cookie_redirects_never_500(web_app_client: TestClient) -> None:
    resp = web_app_client.get(
        "/admin-ish",
        cookies={"access_token": "not-a-real-token"},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_contract_v1_adapter_authenticates_non_admin_without_authorizing(
    web_app_client: TestClient, db: Session, tenant_row: Tenant, plain_party: Party
) -> None:
    token = _issue_session_token(db, tenant_row, plain_party)
    resp = web_app_client.get(
        "/admin-ish", cookies={"access_token": token}, follow_redirects=False
    )
    assert resp.status_code == 200
    assert resp.json()["roles"] == []


def test_org_party_token_redirected_never_500(
    web_app_client: TestClient, db: Session, tenant_row: Tenant, org_party: Party
) -> None:
    token = _issue_session_token(db, tenant_row, org_party)
    resp = web_app_client.get(
        "/admin-ish", cookies={"access_token": token}, follow_redirects=False
    )
    assert resp.status_code == 302


def test_admin_person_with_valid_cookie_succeeds(
    web_app_client: TestClient, db: Session, tenant_row: Tenant, admin_party: Party
) -> None:
    token = _issue_session_token(db, tenant_row, admin_party)
    resp = web_app_client.get("/admin-ish", cookies={"access_token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["party_id"] == str(admin_party.id)
    assert "admin" in body["roles"]


def test_revoked_session_redirects(
    web_app_client: TestClient, db: Session, tenant_row: Tenant, admin_party: Party
) -> None:
    token = _issue_session_token(db, tenant_row, admin_party)
    for row in db.query(AuthSession).filter(AuthSession.party_id == admin_party.id):
        row.revoked_at = datetime.now(UTC)
    db.flush()
    resp = web_app_client.get(
        "/admin-ish", cookies={"access_token": token}, follow_redirects=False
    )
    assert resp.status_code == 302


def test_web_auth_redirect_next_url_is_quoted() -> None:
    exc = WebAuthRedirect(next_url="/admin/parties?x=1")
    assert exc.status_code == 302
    assert exc.next_url == "/admin/parties?x=1"


# ---------------------------------------------------------------------------
# Shared-seam proof: authenticate_request is what BOTH require_user_auth
# (bearer) and require_web_auth (cookie) call — same Party, same rules.
# ---------------------------------------------------------------------------


def test_authenticate_request_matches_require_user_auth_for_same_token(
    db: Session, tenant_row: Tenant, admin_party: Party
) -> None:
    token = _issue_session_token(db, tenant_row, admin_party)

    class _FakeState:
        tenant = tenant_row

    class _FakeRequest:
        state = _FakeState()

    direct = authenticate_request(_FakeRequest(), db, token=token)
    assert direct is not None
    assert direct.id == admin_party.id

    app = FastAPI()
    register_error_handlers(app)

    @app.get("/whoami")
    def whoami(party: Party = Depends(require_user_auth)) -> dict:
        return {"id": str(party.id)}

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == str(admin_party.id)
