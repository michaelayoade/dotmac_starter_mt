"""TDD for the custom fields API (Task 10): definitions CRUD + values
round-trip under `/custom-fields`.

App-builder pattern from `tests/unit/test_settings_api.py` (bare `FastAPI()`
+ `register_error_handlers`, `get_db`/`require_user_auth` overridden via
`app.dependency_overrides`, a thin middleware standing in for
`TenantResolverMiddleware`). RLS doesn't exist on SQLite — tenancy
correctness is the job of `tests/test_custom_fields_isolation.py`'s Postgres
canaries (this file's e2e canary included).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_user_auth
from app.core.errors import register_error_handlers
from app.core.models import Party, PartyPerson, PartyRole, PartyType, Role, Tenant
from app.features.custom_fields.router import router as custom_fields_router

# Import for the side effect: registers custom_fields/max_per_entity with the
# core settings resolver (create_field resolves the per-entity limit via it).
from app.features.settings import spec as _settings_spec  # noqa: F401


@pytest.fixture()
def admin_person(db: Session, tenant_row: Tenant) -> Party:
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Admin User",
        email="admin@acme.test",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Admin", last_name="User"))
    role = Role(tenant_id=tenant_row.id, slug="admin", name="Admin")
    db.add(role)
    db.flush()
    db.add(PartyRole(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
    db.flush()
    return party


@pytest.fixture()
def app_client(db: Session, tenant_row: Tenant, admin_person: Party) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(custom_fields_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[require_user_auth] = lambda: admin_person

    return TestClient(app)


def _create_payload(**overrides) -> dict:
    payload = {
        "entity_type": "party",
        "field_code": "eye_color",
        "field_name": "Eye color",
        "field_type": "TEXT",
    }
    payload.update(overrides)
    return payload


def _create(client: TestClient, **overrides) -> dict:
    resp = client.post("/custom-fields/definitions", json=_create_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# POST /custom-fields/definitions
# ---------------------------------------------------------------------------


def test_create_definition_returns_201_with_full_shape(app_client: TestClient) -> None:
    resp = app_client.post(
        "/custom-fields/definitions",
        json=_create_payload(
            field_type="SELECT",
            field_options={
                "options": [
                    {"value": "brown", "label": "Brown"},
                    {"value": "blue", "label": "Blue"},
                ]
            },
        ),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["entity_type"] == "party"
    assert body["field_code"] == "eye_color"
    assert body["field_type"] == "SELECT"
    assert body["is_active"] is True
    assert "id" in body and "tenant_id" in body


def test_create_definition_duplicate_code_is_conflict(app_client: TestClient) -> None:
    _create(app_client)
    resp = app_client.post("/custom-fields/definitions", json=_create_payload())
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


def test_create_definition_unknown_entity_type_is_bad_request(
    app_client: TestClient,
) -> None:
    resp = app_client.post(
        "/custom-fields/definitions", json=_create_payload(entity_type="widget")
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "bad_request"
    assert "app/features/custom_fields/registry.py" in body["message"]


def test_create_definition_limit_reached_is_bad_request(
    app_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    """`custom_fields/max_per_entity` (Task 5 setting) — set to 1 directly via
    the resolver (the settings router isn't mounted on this bare app), then
    prove the second create for the same entity_type 400s."""
    from app.core import settings_resolver as sr
    from app.core.settings_models import SettingDomain

    sr.upsert_by_key(
        db, SettingDomain.custom_fields, "max_per_entity", 1, tenant_id=tenant_row.id
    )
    db.flush()

    _create(app_client, field_code="field_one")

    resp = app_client.post(
        "/custom-fields/definitions", json=_create_payload(field_code="field_two")
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "bad_request"
    assert "limit reached" in body["message"]


# ---------------------------------------------------------------------------
# GET /custom-fields/definitions (list, entity_type required)
# ---------------------------------------------------------------------------


def test_list_definitions_requires_entity_type_query_param(
    app_client: TestClient,
) -> None:
    resp = app_client.get("/custom-fields/definitions")
    assert resp.status_code == 422


def test_list_definitions_filters_by_entity_type(app_client: TestClient) -> None:
    _create(app_client)
    resp = app_client.get("/custom-fields/definitions", params={"entity_type": "party"})
    assert resp.status_code == 200
    codes = [d["field_code"] for d in resp.json()]
    assert codes == ["eye_color"]


def test_list_definitions_paginates(app_client: TestClient) -> None:
    _create(app_client, field_code="field_a")
    _create(app_client, field_code="field_b")
    _create(app_client, field_code="field_c")

    resp = app_client.get(
        "/custom-fields/definitions",
        params={"entity_type": "party", "limit": 2, "offset": 1},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_definitions_limit_out_of_bounds_is_422(app_client: TestClient) -> None:
    resp = app_client.get(
        "/custom-fields/definitions", params={"entity_type": "party", "limit": 999}
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /custom-fields/definitions/{id}
# ---------------------------------------------------------------------------


def test_get_definition_returns_it(app_client: TestClient) -> None:
    created = _create(app_client)
    resp = app_client.get(f"/custom-fields/definitions/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_definition_missing_is_404(app_client: TestClient) -> None:
    resp = app_client.get(
        "/custom-fields/definitions/00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# PATCH /custom-fields/definitions/{id} — exclude_unset behavior
# ---------------------------------------------------------------------------


def test_patch_only_updates_fields_explicitly_set(app_client: TestClient) -> None:
    created = _create(app_client, help_text="original help")

    resp = app_client.patch(
        f"/custom-fields/definitions/{created['id']}",
        json={"field_name": "Renamed"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["field_name"] == "Renamed"
    # help_text was NOT in the PATCH body -> must be untouched, not reset to
    # null (proves the router builds updates via model_dump(exclude_unset=True)
    # rather than a full model_dump that would send every unset field as None).
    assert body["help_text"] == "original help"


def test_patch_cannot_change_entity_type_or_field_code(app_client: TestClient) -> None:
    """`CustomFieldUpdate` has no `entity_type`/`field_code` fields at all —
    sending them is silently ignored by pydantic (extra fields dropped), not
    merely blocked at the service layer."""
    created = _create(app_client)

    resp = app_client.patch(
        f"/custom-fields/definitions/{created['id']}",
        json={"entity_type": "other", "field_code": "renamed", "field_name": "X"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entity_type"] == "party"
    assert body["field_code"] == "eye_color"
    assert body["field_name"] == "X"


def test_patch_missing_is_404(app_client: TestClient) -> None:
    resp = app_client.patch(
        "/custom-fields/definitions/00000000-0000-0000-0000-000000000000",
        json={"field_name": "X"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /custom-fields/definitions/{id} — soft deactivate
# ---------------------------------------------------------------------------


def test_delete_definition_returns_204_and_soft_deactivates(
    app_client: TestClient,
) -> None:
    created = _create(app_client)

    resp = app_client.delete(f"/custom-fields/definitions/{created['id']}")
    assert resp.status_code == 204
    assert resp.content == b""

    # Still fetchable directly (soft delete) but excluded from the active list.
    get_resp = app_client.get(f"/custom-fields/definitions/{created['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["is_active"] is False

    list_resp = app_client.get(
        "/custom-fields/definitions", params={"entity_type": "party"}
    )
    assert list_resp.json() == []


def test_delete_definition_missing_is_404(app_client: TestClient) -> None:
    resp = app_client.delete(
        "/custom-fields/definitions/00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET / PUT .../values
# ---------------------------------------------------------------------------


def test_get_values_missing_entity_is_404(app_client: TestClient) -> None:
    resp = app_client.get(
        "/custom-fields/party/00000000-0000-0000-0000-000000000000/values"
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_values_round_trip(app_client: TestClient, party_row: Party) -> None:
    _create(
        app_client,
        field_type="SELECT",
        field_options={
            "options": [
                {"value": "brown", "label": "Brown"},
                {"value": "blue", "label": "Blue"},
            ]
        },
    )

    empty = app_client.get(f"/custom-fields/party/{party_row.id}/values")
    assert empty.status_code == 200
    assert empty.json() == {}

    put_resp = app_client.put(
        f"/custom-fields/party/{party_row.id}/values",
        json={"eye_color": "brown"},
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json() == {"eye_color": "brown"}

    get_resp = app_client.get(f"/custom-fields/party/{party_row.id}/values")
    assert get_resp.status_code == 200
    assert get_resp.json() == {"eye_color": "brown"}


def test_put_values_unknown_field_code_is_bad_request(
    app_client: TestClient, party_row: Party
) -> None:
    resp = app_client.put(
        f"/custom-fields/party/{party_row.id}/values",
        json={"never_defined": "x"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "bad_request"
    assert "Unknown custom field" in body["message"]


def test_put_values_invalid_select_option_is_bad_request(
    app_client: TestClient, party_row: Party
) -> None:
    _create(
        app_client,
        field_type="SELECT",
        field_options={
            "options": [
                {"value": "brown", "label": "Brown"},
                {"value": "blue", "label": "Blue"},
            ]
        },
    )

    resp = app_client.put(
        f"/custom-fields/party/{party_row.id}/values",
        json={"eye_color": "green"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


def test_put_values_missing_entity_is_404(app_client: TestClient) -> None:
    resp = app_client.put(
        "/custom-fields/party/00000000-0000-0000-0000-000000000000/values",
        json={},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Guard: no admin role -> 403
# ---------------------------------------------------------------------------


def test_non_admin_person_is_forbidden(
    app_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    non_admin = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Member User",
        email="member@acme.test",
    )
    db.add(non_admin)
    db.flush()
    db.add(PartyPerson(party_id=non_admin.id, first_name="Member", last_name="User"))
    db.flush()
    app_client.app.dependency_overrides[require_user_auth] = lambda: non_admin

    resp = app_client.get("/custom-fields/definitions", params={"entity_type": "party"})
    assert resp.status_code == 403
