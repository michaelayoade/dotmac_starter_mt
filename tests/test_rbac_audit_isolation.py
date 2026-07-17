"""RBAC and audit isolation canaries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import client_for

PASSWORD = "correct horse battery staple"


def test_cross_tenant_role_assignment_returns_404(
    app_client: TestClient,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    a_token = _register_and_login(a, "admin-a@tenant-a.example.com")
    role_id = _create_role(a, a_token, "support")["id"]

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_person_id = b.post(
        "/auth/register",
        json={
            "email": "user-b@tenant-b.example.org",
            "password": PASSWORD,
            "first_name": "User",
            "last_name": "B",
        },
    ).json()["id"]

    response = a.post(
        "/rbac/role-grants",
        headers={"Authorization": f"Bearer {a_token}"},
        json={"person_id": b_person_id, "role_id": role_id},
    )
    assert response.status_code == 404


def test_roles_from_tenant_a_invisible_to_tenant_b(
    app_client: TestClient,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    a_token = _register_and_login(a, "roles-a@tenant-a.example.com")
    _create_role(a, a_token, "editor")

    a_roles = a.get("/rbac/roles", headers={"Authorization": f"Bearer {a_token}"})
    assert a_roles.status_code == 200
    # "admin" is auto-assigned to the first registered user of the tenant
    # (see auth/service.py::_assign_first_user_admin); "editor" is the one we made.
    assert {role["slug"] for role in a_roles.json()} == {"admin", "editor"}

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_token = _register_and_login(b, "roles-b@tenant-b.example.org")
    b_roles = b.get("/rbac/roles", headers={"Authorization": f"Bearer {b_token}"})
    assert b_roles.status_code == 200
    b_slugs = {role["slug"] for role in b_roles.json()}
    assert b_slugs == {"admin"}
    assert "editor" not in b_slugs


def test_audit_events_from_tenant_a_invisible_to_tenant_b(
    app_client: TestClient,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    a_token = _register_and_login(a, "audit-a@tenant-a.example.com")
    _create_role(a, a_token, "audited-role")

    a_events = a.get(
        "/rbac/audit-events", headers={"Authorization": f"Bearer {a_token}"}
    )
    assert a_events.status_code == 200
    assert [event["action"] for event in a_events.json()] == ["role.create"]

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_token = _register_and_login(b, "audit-b@tenant-b.example.org")
    b_events = b.get(
        "/rbac/audit-events", headers={"Authorization": f"Bearer {b_token}"}
    )
    assert b_events.status_code == 200
    assert b_events.json() == []


def test_audit_events_are_bounded_by_tenant_retention_days_setting(
    app_client: TestClient,
    tenant_a,
    admin_session: Session,
) -> None:
    """`audit/retention_days` (Task 5) — the setting's only consumer:
    `app.features.rbac.service.list_audit_events` drops events older than the
    tenant's effective retention window. Set retention to 1 day via the real
    settings API, insert an event backdated 2 days via `admin_session`
    (RLS-bypassing — the API has no way to backdate `created_at`), then
    confirm the listing excludes it while keeping a recent one.
    """
    a = client_for(app_client, tenant_a.slug)
    a_token = _register_and_login(a, "retention-a@tenant-a.example.com")
    _create_role(a, a_token, "recent-role")  # writes a recent audit event

    set_retention = a.put(
        "/settings/audit/retention_days",
        headers={"Authorization": f"Bearer {a_token}"},
        json={"value": 1},
    )
    assert set_retention.status_code == 200, set_retention.text
    assert set_retention.json()["value"] == 1

    old_event_id = uuid.uuid4()
    admin_session.execute(
        text(
            "INSERT INTO audit_events "
            "(id, tenant_id, action, entity_type, details, created_at) "
            "VALUES (:id, :tenant_id, 'old.event', 'test', '{}'::jsonb, :created_at)"
        ),
        {
            "id": str(old_event_id),
            "tenant_id": str(tenant_a.id),
            "created_at": datetime.now(UTC) - timedelta(days=2),
        },
    )
    admin_session.commit()

    try:
        events = a.get(
            "/rbac/audit-events", headers={"Authorization": f"Bearer {a_token}"}
        )
        assert events.status_code == 200, events.text
        actions = [event["action"] for event in events.json()]
        assert "old.event" not in actions
        assert "role.create" in actions
    finally:
        admin_session.execute(
            text("DELETE FROM audit_events WHERE id = :id"), {"id": str(old_event_id)}
        )
        admin_session.commit()


def _register_and_login(client: TestClient, email: str) -> str:
    register = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "first_name": "Admin",
            "last_name": "User",
        },
    )
    assert register.status_code == 201, register.text

    login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _create_role(client: TestClient, token: str, slug: str) -> dict[str, object]:
    response = client.post(
        "/rbac/roles",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201, response.text
    return response.json()
