"""TDD for the audit admin screen (Task 6). The roles and role-grants screens
moved to dotmac-access-admin.

App-builder pattern from `tests/unit/test_parties_web.py` — bare
`FastAPI()` + `register_error_handlers` + the REAL
`app.features.auth.web.router` (so we can log in and get a cookie) and
`app.features.rbac.web.router` mounted, `get_db` overridden to the
in-memory SQLite `db` fixture, a thin middleware standing in for
`TenantResolverMiddleware`. `require_web_auth` is NOT overridden — every
guarded route is exercised through the real cookie-auth seam.

RLS doesn't exist on SQLite — cross-tenant isolation is proven separately
by the Postgres canary `tests/test_rbac_audit_isolation.py`; this file only
proves route wiring, guards, fragment-vs-full rendering, pagination,
and the escaped details rendering.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.deps import get_db
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.models import (
    Party,
    PartyPerson,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
    UserCredential,
)
from dotmac_kernel.security import hash_password
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.features.auth.web import router as auth_web_router
from app.features.rbac.web import router as rbac_web_router

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def provisioned_admin(db: Session, tenant_row: Tenant) -> dict:
    """A provisioned admin — party + person + credential + "admin" role
    grant, built directly on core models; registration no longer grants any
    role (Task 2). Same fixture shape as tests/unit/test_web_login.py.
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
    db.add(PartyRoleGrant(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
    db.commit()
    return {"email": party.email, "party_id": party.id}


@pytest.fixture()
def web_client(db: Session, tenant_row: Tenant) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_web_router)
    app.include_router(rbac_web_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


def _login(client: TestClient, email: str) -> str:
    resp = client.post(
        "/admin/login",
        data={"username": email, "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    return resp.cookies["access_token"]


# ---------------------------------------------------------------------------
# GET /admin/audit — full page vs htmx fragment, pagination boundary,
# actor display name resolution, escaped JSON details
# ---------------------------------------------------------------------------


def _make_audit_event(
    db: Session,
    tenant: Tenant,
    *,
    actor_party_id=None,
    action: str = "role.create",
    details: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant.id,
        actor_party_id=actor_party_id,
        action=action,
        entity_type="role",
        entity_id=None,
        details=details or {},
    )
    db.add(event)
    db.flush()
    return event


def test_audit_index_full_page_renders_shell_and_table(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_audit_event(db, tenant_row, action="role.create", details={"slug": "editor"})
    db.commit()

    resp = web_client.get("/admin/audit", cookies={"access_token": token})
    assert resp.status_code == 200
    assert 'aria-label="Admin navigation"' in resp.text
    assert "role.create" in resp.text
    assert "editor" in resp.text


def test_audit_index_htmx_request_returns_table_fragment_only(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_audit_event(db, tenant_row)
    db.commit()

    resp = web_client.get(
        "/admin/audit",
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert 'aria-label="Admin navigation"' not in resp.text
    assert "<html" not in resp.text.lower()


def test_audit_index_resolves_actor_display_name(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    admin_party_id = provisioned_admin["party_id"]
    _make_audit_event(db, tenant_row, actor_party_id=admin_party_id)
    _make_audit_event(db, tenant_row, actor_party_id=None)
    db.commit()

    resp = web_client.get("/admin/audit", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "Admin User" in resp.text
    assert "System" in resp.text


def test_audit_index_renders_details_as_escaped_json(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_audit_event(db, tenant_row, details={"note": "<script>alert(1)</script>"})
    db.commit()

    resp = web_client.get("/admin/audit", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_audit_index_pagination_boundary(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    for i in range(25):
        _make_audit_event(db, tenant_row, action=f"test.event.{i:02d}")
    db.commit()

    page1 = web_client.get(
        "/admin/audit",
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert page1.status_code == 200
    assert "Page 1 of 2" in page1.text

    page2 = web_client.get(
        "/admin/audit",
        params={"page": 2},
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert page2.status_code == 200
    assert "Page 2 of 2" in page2.text

    page3 = web_client.get(
        "/admin/audit",
        params={"page": 3},
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert page3.status_code == 200
    # Past the last page: no rows, but still a valid 200 (empty state), not
    # a 500/422 — same "out-of-range page degrades gracefully" contract as
    # the rest of this app's paginated listings.
    assert "No audit events found" in page3.text


def test_audit_index_excludes_events_older_than_retention_window(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    recent = _make_audit_event(db, tenant_row, action="recent.event")
    old = _make_audit_event(db, tenant_row, action="old.event")
    db.commit()
    # Backdate directly — no API surface backdates `created_at`.
    db.query(AuditEvent).filter(AuditEvent.id == old.id).update(
        {"created_at": datetime.now(UTC) - timedelta(days=400)}
    )
    db.commit()
    assert recent.id  # keep reference alive for clarity

    resp = web_client.get("/admin/audit", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "recent.event" in resp.text
    assert "old.event" not in resp.text
