"""TDD for the Parties admin screens (Task 4): list/create/detail/delete.

App-builder pattern from `tests/unit/test_web_login.py` — bare `FastAPI()` +
`register_error_handlers` + the REAL `app.features.auth.web.router` (so we
can log in and get a cookie) and `app.features.parties.web.router` mounted,
`get_db` overridden to the in-memory SQLite `db` fixture, a thin middleware
standing in for `TenantResolverMiddleware`. `require_web_auth` is NOT
overridden — every guarded route is exercised through the real cookie-auth
seam, same spirit as `test_web_login.py`'s dashboard tests.

RLS doesn't exist on SQLite — cross-tenant isolation is proven separately by
the Postgres canary `tests/test_party_isolation.py`; this file only proves
route wiring, guards, fragment-vs-full rendering, filter threading, and the
create/delete flows.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from dotmac_kernel.deps import get_db
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.models import (
    Party,
    PartyOrganization,
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
from app.features.parties.web import router as parties_web_router

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
    app.include_router(parties_web_router)

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


def _make_person(db: Session, tenant: Tenant, name: str, email: str) -> Party:
    first, _, last = name.partition(" ")
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=name,
        email=email,
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name=first, last_name=last or "Doe"))
    db.flush()
    return party


def _make_organization(db: Session, tenant: Tenant, name: str) -> Party:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.organization,
        display_name=name,
        email=None,
    )
    db.add(party)
    db.flush()
    db.add(PartyOrganization(party_id=party.id, legal_name=name))
    db.flush()
    return party


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_index_without_cookie_redirects_to_login(web_client: TestClient) -> None:
    resp = web_client.get("/admin/parties", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/admin/login?next=")


def test_create_form_without_cookie_redirects_to_login(web_client: TestClient) -> None:
    resp = web_client.get("/admin/parties/create", follow_redirects=False)
    assert resp.status_code == 302


def test_detail_without_cookie_redirects_to_login(
    web_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    party = _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()
    resp = web_client.get(f"/admin/parties/{party.id}", follow_redirects=False)
    assert resp.status_code == 302


def test_delete_without_cookie_redirects_to_login(
    web_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    party = _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()
    resp = web_client.post(f"/admin/parties/{party.id}/delete", follow_redirects=False)
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# GET /admin/parties — full page vs htmx fragment, search + filter threading
# ---------------------------------------------------------------------------


def test_index_full_page_renders_shell_and_table(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()

    resp = web_client.get("/admin/parties", cookies={"access_token": token})
    assert resp.status_code == 200
    assert 'aria-label="Admin navigation"' in resp.text  # full shell
    assert 'aria-current="page"' in resp.text
    assert "Ada Lovelace" in resp.text
    assert 'name="q"' in resp.text


def test_index_htmx_request_returns_table_fragment_only(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()

    resp = web_client.get(
        "/admin/parties",
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Ada Lovelace" in resp.text
    assert 'aria-label="Admin navigation"' not in resp.text  # no shell
    assert "<html" not in resp.text.lower()


def test_index_search_filters_by_display_name_or_email(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    _make_organization(db, tenant_row, "Widget Co")
    db.commit()

    resp = web_client.get(
        "/admin/parties",
        params={"q": "Ada"},
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Ada Lovelace" in resp.text
    assert "Widget Co" not in resp.text


def test_index_party_type_filter(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    _make_organization(db, tenant_row, "Widget Co")
    db.commit()

    resp = web_client.get(
        "/admin/parties",
        params={"party_type": "organization"},
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Widget Co" in resp.text
    assert "Ada Lovelace" not in resp.text


def test_index_garbage_party_type_degrades_to_unfiltered(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    """Bogus party_type (e.g., stale bookmark) degrades gracefully — returns
    200 with unfiltered list, not 422.
    """
    token = _login(web_client, provisioned_admin["email"])
    _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    _make_organization(db, tenant_row, "Widget Co")
    db.commit()

    resp = web_client.get(
        "/admin/parties",
        params={"party_type": "bogus"},
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Ada Lovelace" in resp.text
    assert "Widget Co" in resp.text


def test_index_pagination_second_page(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    for i in range(25):
        _make_person(db, tenant_row, f"Person {i:02d}", f"person{i:02d}@example.com")
    db.commit()

    page1 = web_client.get(
        "/admin/parties",
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert page1.status_code == 200
    assert "Page 1 of 2" in page1.text

    page2 = web_client.get(
        "/admin/parties",
        params={"page": 2},
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert page2.status_code == 200
    assert "Page 2 of 2" in page2.text


# ---------------------------------------------------------------------------
# GET /admin/parties/create
# ---------------------------------------------------------------------------


def test_create_form_renders_both_tabs(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get("/admin/parties/create", cookies={"access_token": token})
    assert resp.status_code == 200
    assert 'hx-post="/admin/parties/people"' in resp.text
    assert 'hx-post="/admin/parties/organizations"' in resp.text
    assert 'name="first_name"' in resp.text
    assert 'name="legal_name"' in resp.text


# ---------------------------------------------------------------------------
# POST /admin/parties/people
# ---------------------------------------------------------------------------


def test_create_person_success_redirects_to_detail(
    web_client: TestClient, provisioned_admin: dict, db: Session
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/parties/people",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/admin/parties/")
    assert resp.headers["hx-redirect"] == location

    party = db.query(Party).filter(Party.email == "grace@example.com").one()
    assert party.person_profile.first_name == "Grace"


def test_create_person_validation_error_rerenders_200(
    web_client: TestClient, provisioned_admin: dict, db: Session
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/parties/people",
        data={"first_name": "", "last_name": "Hopper", "email": "grace@example.com"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "first_name" in resp.text
    assert db.query(Party).filter(Party.email == "grace@example.com").first() is None


def test_create_person_duplicate_email_rerenders_200_with_conflict(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_person(db, tenant_row, "Existing Person", "dup@example.com")
    db.commit()

    resp = web_client.post(
        "/admin/parties/people",
        data={"first_name": "New", "last_name": "Person", "email": "dup@example.com"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "already registered" in resp.text.lower()


# ---------------------------------------------------------------------------
# POST /admin/parties/organizations
# ---------------------------------------------------------------------------


def test_create_organization_success_redirects_to_detail(
    web_client: TestClient, provisioned_admin: dict, db: Session
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/parties/organizations",
        data={"legal_name": "Acme Widgets Ltd."},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/admin/parties/")

    party = db.query(Party).filter(Party.display_name == "Acme Widgets Ltd.").one()
    assert party.organization_profile.legal_name == "Acme Widgets Ltd."


def test_create_organization_validation_error_rerenders_200(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/parties/organizations",
        data={"legal_name": ""},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "legal_name" in resp.text


# ---------------------------------------------------------------------------
# GET /admin/parties/{id}
# ---------------------------------------------------------------------------


def test_detail_renders_person_fields(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    party = _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()

    resp = web_client.get(f"/admin/parties/{party.id}", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "Ada Lovelace" in resp.text
    assert "ada@example.com" in resp.text
    assert 'id="custom-fields-panel"' in resp.text


def test_detail_unknown_party_is_404(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get(
        "/admin/parties/00000000-0000-0000-0000-000000000000",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /admin/parties/{id}/delete
# ---------------------------------------------------------------------------


def test_delete_redirects_to_index_and_removes_party(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    party = _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()
    party_id = party.id

    resp = web_client.post(
        f"/admin/parties/{party_id}/delete",
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/parties"
    assert resp.headers["hx-redirect"] == "/admin/parties"

    follow_up = web_client.get(
        f"/admin/parties/{party_id}", cookies={"access_token": token}
    )
    assert follow_up.status_code == 404


def test_delete_unknown_party_is_404(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/parties/00000000-0000-0000-0000-000000000000/delete",
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET/POST /admin/parties/{id}/edit (Task 5)
# ---------------------------------------------------------------------------


def test_edit_form_without_cookie_redirects_to_login(
    web_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    party = _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()
    resp = web_client.get(f"/admin/parties/{party.id}/edit", follow_redirects=False)
    assert resp.status_code == 302


def test_edit_form_renders_person_fields(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    party = _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()

    resp = web_client.get(
        f"/admin/parties/{party.id}/edit", cookies={"access_token": token}
    )
    assert resp.status_code == 200
    assert f'hx-post="/admin/parties/{party.id}/edit"' in resp.text
    assert 'name="first_name"' in resp.text
    assert 'value="Ada"' in resp.text
    assert 'value="Lovelace"' in resp.text
    assert 'name="legal_name"' not in resp.text


def test_edit_form_renders_organization_fields(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    party = _make_organization(db, tenant_row, "Widget Co")
    db.commit()

    resp = web_client.get(
        f"/admin/parties/{party.id}/edit", cookies={"access_token": token}
    )
    assert resp.status_code == 200
    assert 'name="legal_name"' in resp.text
    assert 'value="Widget Co"' in resp.text
    assert 'name="first_name"' not in resp.text


def test_edit_form_unknown_party_is_404(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get(
        "/admin/parties/00000000-0000-0000-0000-000000000000/edit",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


def test_edit_person_success_redirects_and_recomputes_display_name(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    party = _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()

    resp = web_client.post(
        f"/admin/parties/{party.id}/edit",
        data={
            "first_name": "Ada",
            "last_name": "Byron",
            "email": "ada@example.com",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    detail_url = f"/admin/parties/{party.id}"
    assert resp.headers["location"] == detail_url
    assert resp.headers["hx-redirect"] == detail_url

    db.expire_all()
    updated = db.get(Party, party.id)
    assert updated.display_name == "Ada Byron"
    assert updated.person_profile.last_name == "Byron"


def test_edit_person_validation_error_rerenders_200(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    party = _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()

    resp = web_client.post(
        f"/admin/parties/{party.id}/edit",
        data={"first_name": "", "last_name": "Byron", "email": "ada@example.com"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "first_name" in resp.text

    db.expire_all()
    unchanged = db.get(Party, party.id)
    assert unchanged.display_name == "Ada Lovelace"


def test_edit_person_duplicate_email_rerenders_200_with_conflict(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    _make_person(db, tenant_row, "Existing Person", "dup@example.com")
    party = _make_person(db, tenant_row, "Ada Lovelace", "ada@example.com")
    db.commit()

    resp = web_client.post(
        f"/admin/parties/{party.id}/edit",
        data={"first_name": "Ada", "last_name": "Lovelace", "email": "dup@example.com"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "already registered" in resp.text.lower()


def test_edit_organization_success_redirects_and_recomputes_display_name(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    party = _make_organization(db, tenant_row, "Widget Co")
    db.commit()

    resp = web_client.post(
        f"/admin/parties/{party.id}/edit",
        data={"legal_name": "Widget Co International"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    detail_url = f"/admin/parties/{party.id}"
    assert resp.headers["location"] == detail_url

    db.expire_all()
    updated = db.get(Party, party.id)
    assert updated.display_name == "Widget Co International"
    assert updated.organization_profile.legal_name == "Widget Co International"


def test_edit_organization_validation_error_rerenders_200(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    party = _make_organization(db, tenant_row, "Widget Co")
    db.commit()

    resp = web_client.post(
        f"/admin/parties/{party.id}/edit",
        data={"legal_name": ""},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "legal_name" in resp.text


def test_edit_submit_unknown_party_is_404(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/parties/00000000-0000-0000-0000-000000000000/edit",
        data={"first_name": "A", "last_name": "B", "email": "a@example.com"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 404
