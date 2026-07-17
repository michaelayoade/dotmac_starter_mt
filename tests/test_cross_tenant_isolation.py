"""Cross-tenant isolation canaries.

These tests are the load-bearing invariant for the whole architecture. If they fail,
something is wrong at the routing, application, or RLS layer — and the failure mode
is "data leak between customers", which is unacceptable.

Every new tenant-scoped table MUST add a parallel test in this file (or sibling).

The tests run against a real Postgres because RLS doesn't exist in SQLite. Set
`TEST_DATABASE_URL` to a disposable Postgres before running.

Renamed from the old `/people` API surface to `/parties` (Task 7) — same
isolation semantics, same assertions, new endpoints (`POST /parties/people`,
`GET /parties`, `GET /parties/{id}`, `DELETE /parties/{id}`).

Final-review Group 2: every `/parties` route now carries a per-route
`Depends(require_role("admin"))` guard (mirroring settings/rbac/
custom-fields), so these canaries authenticate as a tenant admin via
`_register_and_login` — same helper shape as
`tests/test_settings_isolation.py` / `tests/test_custom_fields_isolation.py`
(register the first user in the tenant, who is auto-assigned the `admin`
role by `app.features.auth.service._assign_first_user_admin`, then log in
for a bearer token).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import client_for

_PASSWORD = "correct horse battery staple"


def _register_and_login(client: TestClient, email: str) -> str:
    register = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": _PASSWORD,
            "first_name": "Admin",
            "last_name": "User",
        },
    )
    assert register.status_code == 201, register.text

    login = client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def test_person_party_created_in_tenant_a_invisible_to_tenant_b(
    app_client: TestClient,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    a_token = _register_and_login(a, "party-admin-a@tenant-a.example.com")
    resp = a.post(
        "/parties/people",
        headers={"Authorization": f"Bearer {a_token}"},
        json={
            "email": "alice@tenant-a.example.com",
            "first_name": "Alice",
            "last_name": "A",
        },
    )
    assert resp.status_code == 201, resp.text
    party_id = resp.json()["id"]

    # From tenant B's subdomain, GET by exact ID must 404.
    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_token = _register_and_login(b, "party-admin-b@tenant-b.example.org")
    assert (
        b.get(
            f"/parties/{party_id}", headers={"Authorization": f"Bearer {b_token}"}
        ).status_code
        == 404
    )
    # And listing must not include the party.
    listing = b.get("/parties", headers={"Authorization": f"Bearer {b_token}"}).json()
    assert party_id not in [p["id"] for p in listing]


def test_person_party_delete_from_other_tenant_returns_404(
    app_client: TestClient,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    a_token = _register_and_login(a, "party-admin-a@tenant-a.example.com")
    resp = a.post(
        "/parties/people",
        headers={"Authorization": f"Bearer {a_token}"},
        json={
            "email": "bob@tenant-a.example.com",
            "first_name": "Bob",
            "last_name": "B",
        },
    )
    assert resp.status_code == 201
    party_id = resp.json()["id"]

    # Delete from tenant B context — must 404.
    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_token = _register_and_login(b, "party-admin-b@tenant-b.example.org")
    assert (
        b.delete(
            f"/parties/{party_id}", headers={"Authorization": f"Bearer {b_token}"}
        ).status_code
        == 404
    )

    # Party still exists in tenant A.
    a2 = client_for(TestClient(app_client.app), tenant_a.slug)
    assert (
        a2.get(
            f"/parties/{party_id}", headers={"Authorization": f"Bearer {a_token}"}
        ).status_code
        == 200
    )


def test_email_can_be_reused_across_tenants(
    app_client: TestClient,
    tenant_a,
    tenant_b,
):
    """Same email in two tenants is two distinct parties — see ADR D1."""
    a = client_for(app_client, tenant_a.slug)
    a_token = _register_and_login(a, "party-admin-a@tenant-a.example.com")
    r1 = a.post(
        "/parties/people",
        headers={"Authorization": f"Bearer {a_token}"},
        json={
            "email": "shared@example.com",
            "first_name": "A",
            "last_name": "User",
        },
    )
    assert r1.status_code == 201

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_token = _register_and_login(b, "party-admin-b@tenant-b.example.org")
    r2 = b.post(
        "/parties/people",
        headers={"Authorization": f"Bearer {b_token}"},
        json={
            "email": "shared@example.com",
            "first_name": "B",
            "last_name": "User",
        },
    )
    assert r2.status_code == 201

    # And they have different IDs.
    assert r1.json()["id"] != r2.json()["id"]
