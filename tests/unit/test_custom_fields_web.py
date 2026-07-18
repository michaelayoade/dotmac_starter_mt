"""TDD for the Custom Fields admin screens (Task 7): definitions list/
create/edit/deactivate, and the cross-feature UI composition pattern (the
party detail page's values panel — GET renders definitions+values, POST
saves them, both as htmx fragments owned entirely by this feature).

App-builder pattern from `tests/unit/test_rbac_web.py` — bare `FastAPI()` +
`register_error_handlers` + the REAL `app.features.auth.web.router` (so we
can log in and get a cookie), `app.features.custom_fields.web.router`, AND
`app.features.parties.web.router` (only to prove the parties detail page
carries the composition wiring — no Python import between the two features,
just a shared route contract this test exercises end to end).
"""

from __future__ import annotations

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.errors import register_error_handlers
from app.core.models import Party, PartyType, Tenant
from app.features.auth import service as auth_service
from app.features.auth.schemas import RegisterRequest
from app.features.auth.web import router as auth_web_router
from app.features.custom_fields import service as cf_service
from app.features.custom_fields.models import CustomFieldDefinition, CustomFieldType
from app.features.custom_fields.schemas import CustomFieldCreate
from app.features.custom_fields.web import router as custom_fields_web_router
from app.features.parties.web import router as parties_web_router

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def registered_admin(db: Session, tenant_row: Tenant) -> dict:
    view = auth_service.register(
        db,
        tenant_row,
        RegisterRequest(
            email="admin@example.com",
            password=PASSWORD,
            first_name="Admin",
            last_name="User",
        ),
    )
    db.commit()
    return {"email": view.email, "party_id": view.id}


@pytest.fixture()
def web_client(db: Session, tenant_row: Tenant) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_web_router)
    app.include_router(custom_fields_web_router)
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


def _make_field(db: Session, tenant: Tenant, **overrides) -> CustomFieldDefinition:
    defaults: dict = {
        "entity_type": "party",
        "field_code": "loyalty_tier",
        "field_name": "Loyalty Tier",
        "field_type": CustomFieldType.TEXT,
    }
    defaults.update(overrides)
    field = cf_service.create_field(db, tenant.id, CustomFieldCreate(**defaults))
    db.flush()
    return field


def _make_party(db: Session, tenant: Tenant) -> Party:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name="Ada Lovelace",
        email="ada@example.com",
    )
    db.add(party)
    db.flush()
    return party


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_index_without_cookie_redirects_to_login(web_client: TestClient) -> None:
    resp = web_client.get("/admin/custom-fields", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/admin/login?next=")


def test_create_form_without_cookie_redirects_to_login(
    web_client: TestClient,
) -> None:
    resp = web_client.get("/admin/custom-fields/create", follow_redirects=False)
    assert resp.status_code == 302


def test_values_panel_without_cookie_redirects_to_login(
    web_client: TestClient,
) -> None:
    resp = web_client.get(
        f"/admin/custom-fields/party/{uuid4()}/values-panel", follow_redirects=False
    )
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# GET /admin/custom-fields — full page vs htmx fragment
# ---------------------------------------------------------------------------


def test_index_full_page_renders_shell_and_table(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    _make_field(db, tenant_row)
    db.commit()

    resp = web_client.get("/admin/custom-fields", cookies={"access_token": token})
    assert resp.status_code == 200
    assert 'aria-label="Admin navigation"' in resp.text
    assert "Loyalty Tier" in resp.text
    assert "loyalty_tier" in resp.text


def test_index_htmx_request_returns_table_fragment_only(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    _make_field(db, tenant_row)
    db.commit()

    resp = web_client.get(
        "/admin/custom-fields",
        cookies={"access_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Loyalty Tier" in resp.text
    assert 'aria-label="Admin navigation"' not in resp.text
    assert "<html" not in resp.text.lower()


def test_index_shows_inactive_fields_with_deactivate_hidden(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    field = _make_field(db, tenant_row)
    cf_service.deactivate_field(db, tenant_row.id, field.id)
    db.commit()

    resp = web_client.get("/admin/custom-fields", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "Loyalty Tier" in resp.text
    assert "inactive" in resp.text.lower()


# ---------------------------------------------------------------------------
# GET/POST /admin/custom-fields/create, "" — definition create
# ---------------------------------------------------------------------------


def test_create_form_renders(web_client: TestClient, registered_admin: dict) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.get(
        "/admin/custom-fields/create?entity_type=party",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert 'hx-post="/admin/custom-fields"' in resp.text
    assert 'name="field_code"' in resp.text
    assert 'name="field_type"' in resp.text


def test_create_submit_success_redirects_to_index(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.post(
        "/admin/custom-fields",
        data={
            "entity_type": "party",
            "field_code": "loyalty_tier",
            "field_name": "Loyalty Tier",
            "field_type": "TEXT",
            "display_order": "0",
            "show_in_form": "true",
            "show_in_detail": "true",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/custom-fields?entity_type=party"
    assert resp.headers["hx-redirect"] == "/admin/custom-fields?entity_type=party"

    field = (
        db.query(CustomFieldDefinition)
        .filter(CustomFieldDefinition.field_code == "loyalty_tier")
        .one()
    )
    assert field.field_name == "Loyalty Tier"


def test_create_submit_select_type_parses_options_text(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.post(
        "/admin/custom-fields",
        data={
            "entity_type": "party",
            "field_code": "tier",
            "field_name": "Tier",
            "field_type": "SELECT",
            "options_text": "gold|Gold\nsilver",
            "display_order": "0",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    field = (
        db.query(CustomFieldDefinition)
        .filter(CustomFieldDefinition.field_code == "tier")
        .one()
    )
    assert field.field_options == {
        "options": [
            {"value": "gold", "label": "Gold"},
            {"value": "silver", "label": "silver"},
        ]
    }


def test_create_submit_validation_error_rerenders_200(
    web_client: TestClient, registered_admin: dict
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.post(
        "/admin/custom-fields",
        data={
            "entity_type": "party",
            "field_code": "",
            "field_name": "",
            "field_type": "TEXT",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200


def test_create_submit_duplicate_code_rerenders_200_with_conflict(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    _make_field(db, tenant_row)
    db.commit()

    resp = web_client.post(
        "/admin/custom-fields",
        data={
            "entity_type": "party",
            "field_code": "loyalty_tier",
            "field_name": "Loyalty Tier Two",
            "field_type": "TEXT",
            "display_order": "0",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "already exists" in resp.text.lower()


# ---------------------------------------------------------------------------
# GET/POST /admin/custom-fields/{field_id}/edit
# ---------------------------------------------------------------------------


def test_edit_form_renders_current_values_and_readonly_immutable_fields(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    field = _make_field(db, tenant_row)
    db.commit()

    resp = web_client.get(
        f"/admin/custom-fields/{field.id}/edit", cookies={"access_token": token}
    )
    assert resp.status_code == 200
    assert "Loyalty Tier" in resp.text
    assert "readonly" in resp.text


def test_edit_submit_updates_field_and_redirects(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    field = _make_field(db, tenant_row)
    db.commit()

    resp = web_client.post(
        f"/admin/custom-fields/{field.id}/edit",
        data={
            "field_name": "Loyalty Tier Updated",
            "field_type": "TEXT",
            "display_order": "1",
            "show_in_form": "true",
            "show_in_detail": "true",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/custom-fields?entity_type=party"

    db.refresh(field)
    assert field.field_name == "Loyalty Tier Updated"
    assert field.display_order == 1


def test_edit_submit_invalid_regex_rerenders_200_with_error(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    field = _make_field(db, tenant_row)
    db.commit()

    resp = web_client.post(
        f"/admin/custom-fields/{field.id}/edit",
        data={
            "field_name": "Loyalty Tier",
            "field_type": "TEXT",
            "validation_regex": "[",
            "display_order": "0",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "not a valid pattern" in resp.text.lower()


def test_edit_unknown_field_returns_404(
    web_client: TestClient, registered_admin: dict
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.get(
        f"/admin/custom-fields/{uuid4()}/edit", cookies={"access_token": token}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /admin/custom-fields/{field_id}/deactivate
# ---------------------------------------------------------------------------


def test_deactivate_soft_deletes_and_redirects(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    field = _make_field(db, tenant_row)
    db.commit()

    resp = web_client.post(
        f"/admin/custom-fields/{field.id}/deactivate",
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/custom-fields?entity_type=party"

    db.refresh(field)
    assert field.is_active is False


# ---------------------------------------------------------------------------
# Cross-feature UI composition: GET/POST .../party/{id}/values-panel
# ---------------------------------------------------------------------------


def test_values_panel_get_renders_definitions_and_current_values(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    _make_field(db, tenant_row)
    party = _make_party(db, tenant_row)
    cf_service.set_values(
        db, tenant_row.id, "party", party.id, {"loyalty_tier": "gold"}
    )
    db.commit()

    resp = web_client.get(
        f"/admin/custom-fields/party/{party.id}/values-panel",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert "Loyalty Tier" in resp.text
    assert "gold" in resp.text
    assert 'hx-post="/admin/custom-fields/party/' in resp.text
    # Fragment, not a full page.
    assert 'aria-label="Admin navigation"' not in resp.text


def test_values_panel_get_no_definitions_renders_empty_message(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    party = _make_party(db, tenant_row)
    db.commit()

    resp = web_client.get(
        f"/admin/custom-fields/party/{party.id}/values-panel",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert "No custom fields are defined" in resp.text


def test_values_panel_post_valid_updates_and_rerenders_fragment(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    _make_field(db, tenant_row)
    party = _make_party(db, tenant_row)
    db.commit()

    resp = web_client.post(
        f"/admin/custom-fields/party/{party.id}/values-panel",
        data={"loyalty_tier": "platinum"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "platinum" in resp.text

    db.refresh(party)
    assert party.custom_fields == {"loyalty_tier": "platinum"}


def test_values_panel_post_invalid_rerenders_200_with_inline_error(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    _make_field(db, tenant_row, is_required=True, field_code="required_field")
    party = _make_party(db, tenant_row)
    db.commit()

    resp = web_client.post(
        f"/admin/custom-fields/party/{party.id}/values-panel",
        data={},  # required_field omitted -> validation failure
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "is required" in resp.text.lower()

    db.refresh(party)
    assert not (party.custom_fields or {}).get("required_field")


def test_values_panel_post_multiselect_getlist(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    _make_field(
        db,
        tenant_row,
        field_code="tags",
        field_name="Tags",
        field_type=CustomFieldType.MULTISELECT,
        field_options={
            "options": [
                {"value": "a", "label": "A"},
                {"value": "b", "label": "B"},
            ]
        },
    )
    party = _make_party(db, tenant_row)
    db.commit()

    resp = web_client.post(
        f"/admin/custom-fields/party/{party.id}/values-panel",
        data={"tags": ["a", "b"]},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200

    db.refresh(party)
    assert party.custom_fields == {"tags": ["a", "b"]}


def test_parties_detail_page_contains_values_panel_hx_get_wiring(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    """Proves the composition pattern's parties-side half: the detail page
    references the custom_fields feature's fragment route by URL only — see
    templates/admin/parties/detail.html.
    """
    token = _login(web_client, registered_admin["email"])
    party = _make_party(db, tenant_row)
    db.commit()

    resp = web_client.get(f"/admin/parties/{party.id}", cookies={"access_token": token})
    assert resp.status_code == 200
    assert f'hx-get="/admin/custom-fields/party/{party.id}/values-panel"' in resp.text
    assert 'hx-trigger="load"' in resp.text
