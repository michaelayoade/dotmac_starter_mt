"""Hosted PostgreSQL acceptance for `dotmac-access-admin` (Wave 0 lane D).

Real migrated database, real `app.main` composition, RLS on. Authored for the
hosted CI job; it is not run on a workstation.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.deps import authorize_party
from dotmac_kernel.models import Party, PartyRoleGrant, Role
from dotmac_kernel.permissions import (
    PermissionCatalogue,
    PermissionSpec,
    active_permissions,
    install_permissions,
)
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tests.conftest import client_for, provision_and_login, provision_owner

BASE = "/access-admin"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _actions(admin_session: Session, tenant, entity_id: str | None = None) -> list[str]:
    admin_session.rollback()
    if entity_id is None:
        stmt = text(
            "SELECT action FROM audit_events WHERE tenant_id = :t "
            "ORDER BY created_at, id"
        )
        params = {"t": str(tenant.id)}
    else:
        stmt = text(
            "SELECT action FROM audit_events WHERE tenant_id = :t "
            "AND entity_id = :e ORDER BY created_at, id"
        )
        params = {"t": str(tenant.id), "e": entity_id}
    return [row[0] for row in admin_session.execute(stmt, params).all()]


def _create_user(client: TestClient, token: str, email: str) -> dict:
    resp = client.post(
        f"{BASE}/users",
        headers=_h(token),
        json={"email": email, "first_name": "New", "last_name": "Person"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _role_id(client: TestClient, token: str, slug: str) -> str:
    roles = client.get(f"{BASE}/roles", headers=_h(token)).json()
    return next(r["id"] for r in roles if r["slug"] == slug)


def test_user_creation_provisions_without_password_material_and_issues_recovery(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    admin = provision_and_login(
        admin_session, tenant_a, c, "admin@tenant-a.example.com"
    )
    body = _create_user(c, admin, "new.hire@tenant-a.example.com")
    flat = str(body).lower()
    assert "password" not in flat and "secret" not in flat
    assert body["recovery_intent_ref"]
    assert body["user"]["is_active"] is True and body["user"]["roles"] == []

    admin_session.rollback()
    event = admin_session.execute(
        text(
            "SELECT payload::text FROM outbox_events WHERE tenant_id = :t "
            "AND event_type = 'access_admin.recovery_requested.v1'"
        ),
        {"t": str(tenant_a.id)},
    ).one()
    assert body["user"]["id"] in event[0]
    assert "hash" not in event[0].lower() and "password" not in event[0].lower()
    # The new person cannot sign in with any password we know of.
    login = c.post(
        "/auth/login",
        json={
            "email": "new.hire@tenant-a.example.com",
            "password": "correct horse battery staple",
        },
    )
    assert login.status_code in (400, 401, 403)
    actions = _actions(admin_session, tenant_a, body["user"]["id"])
    assert "access_admin.user.create" in actions
    assert "access_admin.credential.provisioned" in _actions(admin_session, tenant_a)


def test_duplicate_email_is_refused(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    admin = provision_and_login(
        admin_session, tenant_a, c, "admin@tenant-a.example.com"
    )
    _create_user(c, admin, "dup@tenant-a.example.com")
    again = c.post(
        f"{BASE}/users",
        headers=_h(admin),
        json={"email": "DUP@tenant-a.example.com", "first_name": "A", "last_name": "B"},
    )
    assert again.status_code == 409


def test_a_revoked_users_live_session_fails_on_the_next_request(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    admin = provision_and_login(
        admin_session, tenant_a, c, "admin@tenant-a.example.com"
    )
    leaver_email = "leaver@tenant-a.example.com"
    leaver = provision_and_login(admin_session, tenant_a, c, leaver_email)
    # Live: the leaver's token works.
    assert c.get(f"{BASE}/users", headers=_h(leaver)).status_code == 200
    leaver_id = next(
        u["id"]
        for u in c.get(f"{BASE}/users?q=leaver", headers=_h(admin)).json()
        if u["email"] == leaver_email
    )

    done = c.post(f"{BASE}/users/{leaver_id}/leaver-revoke", headers=_h(admin))
    assert done.status_code == 200, done.text
    assert done.json()["grants_revoked"] == 1
    assert done.json()["sessions_revoked"] >= 1

    # The very next request on the same token fails authentication.
    assert c.get(f"{BASE}/users", headers=_h(leaver)).status_code == 401
    admin_session.rollback()
    remaining = admin_session.scalar(
        select(PartyRoleGrant.id).where(PartyRoleGrant.party_id == UUID(leaver_id))
    )
    assert remaining is None
    active_sessions = admin_session.execute(
        text(
            "SELECT count(*) FROM auth_sessions "
            "WHERE party_id = :p AND revoked_at IS NULL"
        ),
        {"p": leaver_id},
    ).scalar_one()
    assert active_sessions == 0


def test_leaver_revoke_is_audited_per_grant_and_in_summary(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    admin = provision_and_login(
        admin_session, tenant_a, c, "admin@tenant-a.example.com"
    )
    created = _create_user(c, admin, "two.roles@tenant-a.example.com")
    uid = created["user"]["id"]
    admin_session.rollback()
    extra = Role(tenant_id=tenant_a.id, slug="extra-role", name="Extra")
    admin_session.add(extra)
    admin_session.commit()
    for slug in ("admin", "extra-role"):
        granted = c.post(
            f"{BASE}/grants",
            headers=_h(admin),
            json={"party_id": uid, "role_id": _role_id(c, admin, slug)},
        )
        assert granted.status_code == 201, granted.text
    result = c.post(f"{BASE}/users/{uid}/leaver-revoke", headers=_h(admin))
    assert result.status_code == 200 and result.json()["grants_revoked"] == 2
    actions = _actions(admin_session, tenant_a, uid)
    assert actions.count("access_admin.grant.add") == 2
    assert actions.count("access_admin.grant.revoke") == 2
    assert actions.count("access_admin.user.leaver_revoke") == 1


def test_every_grant_and_revoke_writes_an_audit_event(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    admin = provision_and_login(
        admin_session, tenant_a, c, "admin@tenant-a.example.com"
    )
    created = _create_user(c, admin, "grantee@tenant-a.example.com")
    uid = created["user"]["id"]
    role = c.post(
        f"{BASE}/roles", headers=_h(admin), json={"slug": "support", "name": "Support"}
    )
    assert role.status_code == 201, role.text
    role_id = role.json()["id"]

    granted = c.post(
        f"{BASE}/grants", headers=_h(admin), json={"party_id": uid, "role_id": role_id}
    )
    assert granted.status_code == 201 and granted.json()["warnings"] == []
    twice = c.post(
        f"{BASE}/grants", headers=_h(admin), json={"party_id": uid, "role_id": role_id}
    )
    assert twice.status_code == 409
    revoked = c.delete(f"{BASE}/grants/{uid}/{role_id}", headers=_h(admin))
    assert revoked.status_code == 204
    missing = c.delete(f"{BASE}/grants/{uid}/{role_id}", headers=_h(admin))
    assert missing.status_code == 404

    actions = _actions(admin_session, tenant_a, uid)
    assert actions.count("access_admin.grant.add") == 1
    assert actions.count("access_admin.grant.revoke") == 1
    assert "access_admin.role.create" in _actions(admin_session, tenant_a)


def test_a_role_cannot_reference_an_undeclared_permission(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    admin = provision_and_login(
        admin_session, tenant_a, c, "admin@tenant-a.example.com"
    )
    bad = c.post(
        f"{BASE}/roles/composition-check",
        headers=_h(admin),
        json={"permission_codes": ["access_admin.users.read", "nope.invented"]},
    )
    assert bad.status_code == 400
    assert "nope.invented" in bad.text
    ok = c.post(
        f"{BASE}/roles/composition-check",
        headers=_h(admin),
        json={"permission_codes": ["access_admin.users.read", "rbac.roles.read"]},
    )
    assert ok.status_code == 200
    assert ok.json() == {
        "permission_codes": ["access_admin.users.read", "rbac.roles.read"],
        "persisted": False,
    }


def test_code_roles_are_read_only_and_their_slugs_cannot_be_taken(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    admin = provision_and_login(
        admin_session, tenant_a, c, "admin@tenant-a.example.com"
    )
    roles = c.get(f"{BASE}/roles", headers=_h(admin)).json()
    admin_role = next(r for r in roles if r["slug"] == "admin")
    assert admin_role["kind"] == "code" and admin_role["read_only"] is True
    assert "access_admin.users.read" in admin_role["permission_codes"]
    taken = c.post(
        f"{BASE}/roles", headers=_h(admin), json={"slug": "admin", "name": "Mine"}
    )
    assert taken.status_code == 409
    rename = c.patch(
        f"{BASE}/roles/{admin_role['id']}", headers=_h(admin), json={"name": "X"}
    )
    assert rename.status_code == 409


def test_the_six_erp_roles_and_admin_staff_still_resolve(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    erp_roles = (
        "finance_preparer",
        "finance_approver",
        "treasury",
        "payroll_approver",
        "payroll_finalizer",
        "bank_reconciler",
    )
    specs = [PermissionSpec(code="erp.web.access", default_roles=("admin", "staff"))]
    specs += [
        PermissionSpec(code=f"erp.{slug}.act", default_roles=(slug,))
        for slug in erp_roles
    ]
    previous = active_permissions()
    merged = PermissionCatalogue(
        [
            *(("erp_access", spec) for spec in specs),
            *(
                (previous.owner(code) or "", spec)
                for code in previous.codes()
                if (spec := previous.spec(code)) is not None
            ),
        ]
    )
    install_permissions(merged)
    try:
        c = client_for(app_client, tenant_a.slug)
        admin = provision_and_login(
            admin_session, tenant_a, c, "admin@tenant-a.example.com"
        )
        for slug in (*erp_roles, "staff"):
            admin_session.add(Role(tenant_id=tenant_a.id, slug=slug, name=slug))
        admin_session.commit()
        listed = {
            r["slug"]: r for r in c.get(f"{BASE}/roles", headers=_h(admin)).json()
        }
        for slug in ("admin", "staff", *erp_roles):
            assert listed[slug]["kind"] == "code" and listed[slug]["read_only"]
        holder = _create_user(c, admin, "treasurer@tenant-a.example.com")["user"]["id"]
        granted = c.post(
            f"{BASE}/grants",
            headers=_h(admin),
            json={"party_id": holder, "role_id": listed["treasury"]["id"]},
        )
        assert granted.status_code == 201
        admin_session.rollback()
        party = admin_session.get(Party, UUID(holder))
        assert party is not None
        assert authorize_party(
            admin_session, tenant=tenant_a, party=party, code="erp.treasury.act"
        )
        assert not authorize_party(
            admin_session, tenant=tenant_a, party=party, code="erp.finance_approver.act"
        )
    finally:
        install_permissions(previous)


def test_you_cannot_revoke_or_deactivate_your_own_account(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    email = "self@tenant-a.example.com"
    admin = provision_and_login(admin_session, tenant_a, c, email)
    me = next(
        u["id"]
        for u in c.get(f"{BASE}/users?q=self", headers=_h(admin)).json()
        if u["email"] == email
    )
    assert (
        c.post(f"{BASE}/users/{me}/leaver-revoke", headers=_h(admin)).status_code == 409
    )
    assert c.post(f"{BASE}/users/{me}/deactivate", headers=_h(admin)).status_code == 409
    assert c.get(f"{BASE}/users", headers=_h(admin)).status_code == 200


def test_deactivate_is_refused_while_grants_remain_then_reactivate(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    admin = provision_and_login(
        admin_session, tenant_a, c, "admin@tenant-a.example.com"
    )
    uid = _create_user(c, admin, "soon.gone@tenant-a.example.com")["user"]["id"]
    granted = c.post(
        f"{BASE}/grants",
        headers=_h(admin),
        json={"party_id": uid, "role_id": _role_id(c, admin, "admin")},
    )
    assert granted.status_code == 201
    assert (
        c.post(f"{BASE}/users/{uid}/deactivate", headers=_h(admin)).status_code == 409
    )
    assert (
        c.post(f"{BASE}/users/{uid}/leaver-revoke", headers=_h(admin)).status_code
        == 200
    )
    # Leaver revoke already deactivated; reactivate restores the flag only.
    back = c.post(f"{BASE}/users/{uid}/reactivate", headers=_h(admin))
    assert back.status_code == 200
    assert back.json()["is_active"] is True and back.json()["roles"] == []
    regrant_inactive = c.post(f"{BASE}/users/{uid}/deactivate", headers=_h(admin))
    assert regrant_inactive.status_code == 200
    refused = c.post(
        f"{BASE}/grants",
        headers=_h(admin),
        json={"party_id": uid, "role_id": _role_id(c, admin, "admin")},
    )
    assert refused.status_code == 409


def test_every_route_requires_its_permission(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    c = client_for(app_client, tenant_a.slug)
    provision_owner(
        admin_session, tenant_a, "norole@tenant-a.example.com", role_slug="viewer"
    )
    login = c.post(
        "/auth/login",
        json={
            "email": "norole@tenant-a.example.com",
            "password": "correct horse battery staple",
        },
    )
    token = login.json()["access_token"]
    for method, path, body in (
        ("get", f"{BASE}/users", None),
        (
            "post",
            f"{BASE}/users",
            {"email": "a@b.co", "first_name": "A", "last_name": "B"},
        ),
        ("get", f"{BASE}/roles", None),
        ("post", f"{BASE}/roles", {"slug": "zz", "name": "ZZ"}),
        ("post", f"{BASE}/roles/composition-check", {"permission_codes": []}),
    ):
        response = getattr(c, method)(
            path, headers=_h(token), **({"json": body} if body else {})
        )
        assert response.status_code == 403, (method, path)
    unauthenticated = c.get(f"{BASE}/users")
    assert unauthenticated.status_code == 401
