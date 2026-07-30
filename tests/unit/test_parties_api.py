"""Route-level guard coverage for `/parties` (final-review Group 2).

`app.features.parties.router` previously carried only router-level
`Depends(require_tenant)` — every route was reachable by any authenticated
(or even unauthenticated-but-tenant-resolved) caller, unlike the rest of the
tenant-admin surface (settings, rbac, custom-fields), which all guard with a
per-route `Depends(require_role("admin"))`. This file proves the parties
routes now require the same admin role — mirrors
`tests/unit/test_custom_fields_api.py`'s app-builder pattern (bare
`FastAPI()` + `register_error_handlers`, `get_db`/`require_user_auth`
overridden via `app.dependency_overrides`, a thin middleware standing in for
`TenantResolverMiddleware`). RLS doesn't exist on SQLite — tenancy
correctness is `tests/test_cross_tenant_isolation.py`'s job; this file only
covers the role guard.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from dotmac_kernel.deps import get_db, require_user_auth
from dotmac_kernel.models import Party, PartyPerson, PartyRole, PartyType, Role, Tenant
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.features.parties.router import router as parties_router


@pytest.fixture()
def admin_person(db: Session, tenant_row: Tenant) -> Party:
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Admin User",
        email="admin@acmecorp.io",
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
def non_admin_person(db: Session, tenant_row: Tenant) -> Party:
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Member User",
        email="member@acmecorp.io",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Member", last_name="User"))
    db.flush()
    return party


@pytest.fixture()
def app_client(db: Session, tenant_row: Tenant, admin_person: Party) -> TestClient:
    app = FastAPI()
    from dotmac_kernel.errors import register_error_handlers

    register_error_handlers(app)
    app.include_router(parties_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[require_user_auth] = lambda: admin_person

    return TestClient(app)


def _person_payload(**overrides) -> dict:
    payload = {
        "email": "ada@example.com",
        "first_name": "Ada",
        "last_name": "Lovelace",
    }
    payload.update(overrides)
    return payload


def test_create_person_party_as_admin_succeeds(app_client: TestClient) -> None:
    resp = app_client.post("/parties/people", json=_person_payload())
    assert resp.status_code == 201, resp.text


def test_create_organization_party_as_admin_succeeds(app_client: TestClient) -> None:
    resp = app_client.post("/parties/organizations", json={"legal_name": "Acme Corp"})
    assert resp.status_code == 201, resp.text


def test_list_parties_as_admin_succeeds(app_client: TestClient) -> None:
    resp = app_client.get("/parties")
    assert resp.status_code == 200, resp.text


def test_get_party_as_admin_succeeds(app_client: TestClient) -> None:
    created = app_client.post("/parties/people", json=_person_payload()).json()
    resp = app_client.get(f"/parties/{created['id']}")
    assert resp.status_code == 200, resp.text


def test_delete_party_as_admin_succeeds(app_client: TestClient) -> None:
    created = app_client.post("/parties/people", json=_person_payload()).json()
    resp = app_client.delete(f"/parties/{created['id']}")
    assert resp.status_code == 204, resp.text


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("POST", "/parties/people", _person_payload()),
        ("POST", "/parties/organizations", {"legal_name": "Acme Corp"}),
        ("GET", "/parties", None),
        (
            "GET",
            "/parties/00000000-0000-0000-0000-000000000000",
            None,
        ),
        (
            "DELETE",
            "/parties/00000000-0000-0000-0000-000000000000",
            None,
        ),
    ],
)
def test_non_admin_person_is_forbidden(
    app_client: TestClient,
    non_admin_person: Party,
    method: str,
    path: str,
    json_body: dict | None,
) -> None:
    app_client.app.dependency_overrides[require_user_auth] = lambda: non_admin_person

    resp = app_client.request(method, path, json=json_body)
    assert resp.status_code == 403, resp.text
