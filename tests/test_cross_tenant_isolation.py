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
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import client_for


def test_person_party_created_in_tenant_a_invisible_to_tenant_b(
    app_client: TestClient,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    resp = a.post(
        "/parties/people",
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
    assert b.get(f"/parties/{party_id}").status_code == 404
    # And listing must not include the party.
    listing = b.get("/parties").json()
    assert party_id not in [p["id"] for p in listing]


def test_person_party_delete_from_other_tenant_returns_404(
    app_client: TestClient,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    resp = a.post(
        "/parties/people",
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
    assert b.delete(f"/parties/{party_id}").status_code == 404

    # Party still exists in tenant A.
    a2 = client_for(TestClient(app_client.app), tenant_a.slug)
    assert a2.get(f"/parties/{party_id}").status_code == 200


def test_email_can_be_reused_across_tenants(
    app_client: TestClient,
    tenant_a,
    tenant_b,
):
    """Same email in two tenants is two distinct parties — see ADR D1."""
    a = client_for(app_client, tenant_a.slug)
    r1 = a.post(
        "/parties/people",
        json={
            "email": "shared@example.com",
            "first_name": "A",
            "last_name": "User",
        },
    )
    assert r1.status_code == 201

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    r2 = b.post(
        "/parties/people",
        json={
            "email": "shared@example.com",
            "first_name": "B",
            "last_name": "User",
        },
    )
    assert r2.status_code == 201

    # And they have different IDs.
    assert r1.json()["id"] != r2.json()["id"]
