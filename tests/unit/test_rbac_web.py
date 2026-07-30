"""TDD for the RBAC + audit admin screens (Task 6): roles list/create,
role grants, audit log.

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
validation/conflict re-render, and the grantable-parties search + LIKE
escape behavior.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.audit import AuditEvent
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
    db.add(PartyRole(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
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


def _make_role(db: Session, tenant: Tenant, slug: str, name: str | None = None) -> Role:
    role = Role(tenant_id=tenant.id, slug=slug, name=name or slug.title())
    db.add(role)
    db.flush()
    return role


def _make_party(db: Session, tenant: Tenant, name: str, email: str | None) -> Party:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=name,
        email=email,
    )
    db.add(party)
    db.flush()
    return party


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_roles_index_without_cookie_redirects_to_login(web_client: TestClient) -> None:
    resp = web_client.get("/admin/roles", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/admin/login?next=")


def test_role_create_form_without_cookie_redirects_to_login(
    web_client: TestClient,
) -> None:
    resp = web_client.get("/admin/roles/create", follow_redirects=False)
    assert resp.status_code == 302


def test_role_grants_without_cookie_redirects_to_login(web_client: TestClient) -> None:
    resp = web_client.get("/admin/role-grants", follow_redirects=False)
    assert resp.status_code == 302


def test_audit_without_cookie_redirects_to_login(web_client: TestClient) -> None:
    resp = web_client.get("/admin/audit", follow_redirects=False)
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# GET /admin/roles — full page vs htmx fragment, pagination
# ---------------------------------------------------------------------------


def test_roles_index_full_page_renders_shell_and_table(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_role(db, tenant_row, "editor")
    db.commit()

    resp = web_client.get("/admin/roles", cookies={"access_token": token})
    assert resp.status_code == 200
    assert 'aria-label="Admin navigation"' in resp.text
    assert "editor" in resp.text
    assert "admin" in resp.text  # auto-assigned first-user role


def test_roles_index_htmx_request_returns_table_fragment_only(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_role(db, tenant_row, "editor")
    db.commit()

    resp = web_client.get(
        "/admin/roles",
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "editor" in resp.text
    assert 'aria-label="Admin navigation"' not in resp.text
    assert "<html" not in resp.text.lower()


def test_roles_index_pagination_second_page(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    for i in range(25):
        _make_role(db, tenant_row, f"role-{i:02d}")
    db.commit()

    page1 = web_client.get(
        "/admin/roles",
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert page1.status_code == 200
    assert "Page 1 of 2" in page1.text

    page2 = web_client.get(
        "/admin/roles",
        params={"page": 2},
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert page2.status_code == 200
    assert "Page 2 of 2" in page2.text


# ---------------------------------------------------------------------------
# GET/POST /admin/roles/create, /admin/roles
# ---------------------------------------------------------------------------


def test_role_create_form_renders(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get("/admin/roles/create", cookies={"access_token": token})
    assert resp.status_code == 200
    assert 'hx-post="/admin/roles"' in resp.text
    assert 'name="slug"' in resp.text
    assert 'name="name"' in resp.text


def test_role_create_success_redirects_to_index(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/roles",
        data={"slug": "support-agent", "name": "Support Agent"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/roles"
    assert resp.headers["hx-redirect"] == "/admin/roles"

    role = db.query(Role).filter(Role.slug == "support-agent").one()
    assert role.name == "Support Agent"


def test_role_create_validation_error_rerenders_200(
    web_client: TestClient, provisioned_admin: dict, db: Session
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/roles",
        data={"slug": "", "name": "Support Agent"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "slug" in resp.text
    assert db.query(Role).filter(Role.name == "Support Agent").first() is None


def test_role_create_duplicate_slug_rerenders_200_with_conflict(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_role(db, tenant_row, "editor")
    db.commit()

    resp = web_client.post(
        "/admin/roles",
        data={"slug": "editor", "name": "Editor Two"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "already exists" in resp.text.lower()


def test_role_create_writes_audit_event(
    web_client: TestClient, provisioned_admin: dict, db: Session
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    web_client.post(
        "/admin/roles",
        data={"slug": "billing", "name": "Billing"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    event = db.query(AuditEvent).filter(AuditEvent.action == "role.create").one()
    assert event.details["slug"] == "billing"
    assert event.actor_party_id is not None


# ---------------------------------------------------------------------------
# GET/POST /admin/role-grants
# ---------------------------------------------------------------------------


def test_role_grants_form_renders_parties_and_roles(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_party(db, tenant_row, "Ada Lovelace", "ada@example.com")
    _make_role(db, tenant_row, "editor")
    db.commit()

    resp = web_client.get("/admin/role-grants", cookies={"access_token": token})
    assert resp.status_code == 200
    assert 'hx-post="/admin/role-grants"' in resp.text
    assert "Ada Lovelace" in resp.text
    assert "editor" in resp.text


def test_role_grants_search_filters_by_display_name_or_email(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_party(db, tenant_row, "Ada Lovelace", "ada@example.com")
    _make_party(db, tenant_row, "Grace Hopper", "grace@example.com")
    db.commit()

    resp = web_client.get(
        "/admin/role-grants",
        params={"q": "Ada"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert "Ada Lovelace" in resp.text
    assert "Grace Hopper" not in resp.text


def test_role_grants_search_escapes_like_wildcards(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    """A literal "%" in the search term must not act as a LIKE wildcard —
    searching "50%" should not match a party named "50 Cent" or similar
    display names that merely start with "50".
    """
    token = _login(web_client, provisioned_admin["email"])
    _make_party(db, tenant_row, "50% Off Corp", "fifty@example.com")
    _make_party(db, tenant_row, "50 Cent Enterprises", "cent@example.com")
    db.commit()

    resp = web_client.get(
        "/admin/role-grants",
        params={"q": "50%"},
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert "50% Off Corp" in resp.text
    assert "50 Cent Enterprises" not in resp.text


def test_role_grants_success_redirects_and_writes_audit_event(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    party = _make_party(db, tenant_row, "Ada Lovelace", "ada@example.com")
    role = _make_role(db, tenant_row, "editor")
    db.commit()

    resp = web_client.post(
        "/admin/role-grants",
        data={"party_id": str(party.id), "role_id": str(role.id)},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/role-grants"
    assert resp.headers["hx-redirect"] == "/admin/role-grants"

    event = db.query(AuditEvent).filter(AuditEvent.action == "role.grant").one()
    assert event.details["role_id"] == str(role.id)

    listing = web_client.get("/admin/role-grants", cookies={"access_token": token})
    assert "Ada Lovelace" in listing.text
    assert "editor" in listing.text


def test_role_grants_unknown_party_rerenders_200_with_error(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    role = _make_role(db, tenant_row, "editor")
    db.commit()

    resp = web_client.post(
        "/admin/role-grants",
        data={
            "party_id": "00000000-0000-0000-0000-000000000000",
            "role_id": str(role.id),
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "not found" in resp.text.lower()


def test_role_grants_duplicate_rerenders_200_with_conflict(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    from app.core.models import PartyRole

    token = _login(web_client, provisioned_admin["email"])
    party = _make_party(db, tenant_row, "Ada Lovelace", "ada@example.com")
    role = _make_role(db, tenant_row, "editor")
    db.add(PartyRole(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
    db.commit()

    resp = web_client.post(
        "/admin/role-grants",
        data={"party_id": str(party.id), "role_id": str(role.id)},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "already assigned" in resp.text.lower()


def test_role_grants_invalid_uuid_rerenders_200_with_field_error(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/role-grants",
        data={"party_id": "not-a-uuid", "role_id": "also-not-a-uuid"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200


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
