"""RBAC and audit isolation canaries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.models import Party, PartyType
from tests.conftest import client_for, provision_and_login

PASSWORD = "correct horse battery staple"


def test_cross_tenant_role_assignment_returns_404(
    app_client: TestClient,
    admin_session: Session,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    a_token = provision_and_login(
        admin_session, tenant_a, a, "admin-a@tenant-a.example.com"
    )
    role_id = _create_role(a, a_token, "support")["id"]

    # A plain (role-less) person party in tenant B — created directly via the
    # admin engine; registration is policy-closed by default (Task 2).
    b_party = Party(
        tenant_id=tenant_b.id,
        party_type=PartyType.person,
        display_name="User B",
        email="user-b@tenant-b.example.org",
    )
    admin_session.add(b_party)
    admin_session.commit()
    b_party_id = str(b_party.id)

    response = a.post(
        "/rbac/role-grants",
        headers={"Authorization": f"Bearer {a_token}"},
        json={"party_id": b_party_id, "role_id": role_id},
    )
    assert response.status_code == 404


def test_roles_from_tenant_a_invisible_to_tenant_b(
    app_client: TestClient,
    admin_session: Session,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    a_token = provision_and_login(
        admin_session, tenant_a, a, "roles-a@tenant-a.example.com"
    )
    _create_role(a, a_token, "editor")

    a_roles = a.get("/rbac/roles", headers={"Authorization": f"Bearer {a_token}"})
    assert a_roles.status_code == 200
    # "admin" was created by `provision_owner` when the tenant's owner was
    # provisioned; "editor" is the one we made via the API.
    assert {role["slug"] for role in a_roles.json()} == {"admin", "editor"}

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_token = provision_and_login(
        admin_session, tenant_b, b, "roles-b@tenant-b.example.org"
    )
    b_roles = b.get("/rbac/roles", headers={"Authorization": f"Bearer {b_token}"})
    assert b_roles.status_code == 200
    b_slugs = {role["slug"] for role in b_roles.json()}
    assert b_slugs == {"admin"}
    assert "editor" not in b_slugs


def test_audit_events_from_tenant_a_invisible_to_tenant_b(
    app_client: TestClient,
    admin_session: Session,
    tenant_a,
    tenant_b,
):
    a = client_for(app_client, tenant_a.slug)
    a_token = provision_and_login(
        admin_session, tenant_a, a, "audit-a@tenant-a.example.com"
    )
    _create_role(a, a_token, "audited-role")

    a_events = a.get(
        "/rbac/audit-events", headers={"Authorization": f"Bearer {a_token}"}
    )
    assert a_events.status_code == 200
    assert [event["action"] for event in a_events.json()] == ["role.create"]

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    b_token = provision_and_login(
        admin_session, tenant_b, b, "audit-b@tenant-b.example.org"
    )
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
    a_token = provision_and_login(
        admin_session, tenant_a, a, "retention-a@tenant-a.example.com"
    )
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


def _create_role(client: TestClient, token: str, slug: str) -> dict[str, object]:
    response = client.post(
        "/rbac/roles",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": slug, "name": slug.replace("-", " ").title()},
    )
    assert response.status_code == 201, response.text
    return response.json()
