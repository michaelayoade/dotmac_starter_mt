"""Auth tenant-claim canaries.

These deliberately keep exercising `/auth/register` (a registered user is a
plain user — no role needed to hit `/auth/me`), so the tenant's
`auth.registration_policy` is explicitly opened first via
`open_registration` (`tests/conftest.py`) — registration defaults to
closed since control-plane security Task 2.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import client_for, open_registration


def test_jwt_issued_for_tenant_a_rejected_on_tenant_b(
    app_client: TestClient,
    admin_session: Session,
    tenant_a,
    tenant_b,
):
    open_registration(admin_session, tenant_a)
    a = client_for(app_client, tenant_a.slug)
    register = a.post(
        "/auth/register",
        json={
            "email": "alice-auth@tenant-a.example.com",
            "password": "correct horse battery staple",
            "first_name": "Alice",
            "last_name": "Auth",
        },
    )
    assert register.status_code == 201, register.text

    login = a.post(
        "/auth/login",
        json={
            "email": "alice-auth@tenant-a.example.com",
            "password": "correct horse battery staple",
        },
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    assert (
        a.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    rejected = b.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert rejected.status_code == 401
