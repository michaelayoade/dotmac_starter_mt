"""Unit coverage for `app.core.deps.require_user_auth` (Task 6 review, Minor 3).

`Party` (`party_type` person|organization) replaced the bare `Person` model.
Only `party_type == person` parties can authenticate — `require_user_auth`
has a defense-in-depth check rejecting a token whose `sub` resolves to an
organization-type party (org parties have no credentials and should never
be able to mint a session token, but a stray/garbled token claiming an org
party's id must still be rejected). This exercises the REAL dependency
(not overridden), unlike `tests/unit/test_settings_api.py`'s app-builder,
which overrides `require_user_auth` outright.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_user_auth
from app.core.errors import register_error_handlers
from app.core.models import AuthSession, Party, PartyOrganization, PartyType, Tenant
from app.core.security import hash_token, issue_access_token


@pytest.fixture()
def org_party(db: Session, tenant_row: Tenant) -> Party:
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.organization,
        display_name="Acme Corp",
    )
    db.add(party)
    db.flush()
    db.add(PartyOrganization(party_id=party.id, legal_name="Acme Corp Ltd."))
    db.flush()
    return party


@pytest.fixture()
def org_party_token(db: Session, tenant_row: Tenant, org_party: Party) -> str:
    """A structurally-valid session token whose `sub` is an org party's id."""
    token, expires_at = issue_access_token(org_party.id, tenant_row.id)
    db.add(
        AuthSession(
            tenant_id=tenant_row.id,
            party_id=org_party.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    db.flush()
    return token


@pytest.fixture()
def guarded_app_client(db: Session, tenant_row: Tenant) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/whoami")
    def whoami(party: Party = Depends(require_user_auth)) -> dict:
        return {"id": str(party.id)}

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db

    return TestClient(app)


def test_org_party_token_is_rejected_with_401(
    guarded_app_client: TestClient, org_party_token: str
) -> None:
    resp = guarded_app_client.get(
        "/whoami", headers={"Authorization": f"Bearer {org_party_token}"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"
