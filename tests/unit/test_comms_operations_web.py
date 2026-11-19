"""K-7 operator surface over the consent ledger and delivery receipts (unit).

Route wiring on in-memory SQLite, same app-builder shape as
`tests/unit/test_rbac_web.py` (real auth router for the cookie, real
`require_web_permission` guards, `get_db` overridden). Tenant RLS does not exist
on SQLite: the cross-tenant isolation canary is the Postgres file
`tests/test_comms_operations_pg.py`; here an explicit-`tenant_id` read is still
asserted against a second tenant's rows.
"""

from __future__ import annotations

from collections.abc import Generator
from uuid import UUID

import pytest
from dotmac_kernel import consent, delivery
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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.auth.web import router as auth_web_router
from app.features.comms_operations import service
from app.features.comms_operations.web import router as comms_web_router

PASSWORD = "correct horse battery staple"
CLEARED = "communication.suppression.cleared"


def _person(db: Session, tenant: Tenant, email: str, *, admin: bool) -> Party:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=email.split("@")[0],
        email=email,
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="T", last_name="U"))
    db.add(
        UserCredential(
            tenant_id=tenant.id,
            party_id=party.id,
            password_hash=hash_password(PASSWORD),
        )
    )
    if admin:
        role = Role(tenant_id=tenant.id, slug="admin", name="Admin")
        db.add(role)
        db.flush()
        db.add(PartyRoleGrant(tenant_id=tenant.id, party_id=party.id, role_id=role.id))
    db.commit()
    return party


@pytest.fixture()
def admin(db: Session, tenant_row: Tenant) -> Party:
    return _person(db, tenant_row, "admin@example.com", admin=True)


@pytest.fixture()
def plain_user(db: Session, tenant_row: Tenant) -> Party:
    return _person(db, tenant_row, "plain@example.com", admin=False)


@pytest.fixture()
def client(db: Session, tenant_row: Tenant) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_web_router)
    app.include_router(comms_web_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


def _login(client: TestClient, email: str) -> None:
    resp = client.post(
        "/admin/login",
        data={"username": email, "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    client.cookies.set("access_token", resp.cookies["access_token"])


def _audit_rows(db: Session, tenant_id: UUID) -> list[AuditEvent]:
    return list(
        db.execute(
            select(AuditEvent).where(
                AuditEvent.tenant_id == tenant_id, AuditEvent.action == CLEARED
            )
        ).scalars()
    )


def _bounce(db: Session, tenant: Tenant, address: str = "jane@example.com") -> None:
    delivery.record_receipt(
        db,
        tenant.id,
        channel="email",
        address=address,
        provider="smtp",
        status="bounced",
        provider_message_id="m-1",
        response_code="550",
    )
    db.commit()


def test_a_bounce_suppression_is_visible_and_clearable(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    """The 'bounce silently stops password resets' risk, end to end."""
    _bounce(db, tenant_row)
    assert (
        consent.may_send(
            db,
            tenant_row.id,
            channel="email",
            address="jane@example.com",
            category="password_reset",
        )
        is False
    )
    _login(client, "admin@example.com")

    listing = client.get("/admin/comms/suppressions")
    assert listing.status_code == 200
    assert "jane@example.com" in listing.text
    assert "bounce" in listing.text

    receipts = client.get(
        "/admin/comms/receipts",
        params={"channel": "email", "address": "Jane@Example.com"},
    )
    assert receipts.status_code == 200
    assert "bounced" in receipts.text
    assert "550" in receipts.text
    assert "blocks every message" in receipts.text

    cleared = client.post(
        "/admin/comms/suppressions/clear",
        data={
            "channel": "email",
            "address": "jane@example.com",
            "reason": "Customer confirmed mailbox fixed by phone",
        },
        follow_redirects=False,
    )
    assert cleared.status_code == 302
    assert cleared.headers["location"].endswith("cleared=1")
    assert (
        consent.may_send(
            db,
            tenant_row.id,
            channel="email",
            address="jane@example.com",
            category="password_reset",
        )
        is True
    )
    # The receipt history is untouched: clearing consent does not rewrite it.
    assert (
        len(
            service.list_receipts(
                db, tenant_row.id, channel="email", address="jane@example.com"
            )
        )
        == 1
    )


def test_clear_writes_one_audit_row_naming_actor_and_reason(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    _bounce(db, tenant_row)
    _login(client, "admin@example.com")
    client.post(
        "/admin/comms/suppressions/clear",
        data={"channel": "email", "address": "jane@example.com", "reason": "fixed"},
        follow_redirects=False,
    )
    rows = _audit_rows(db, tenant_row.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_type == "user"
    assert row.actor_id == str(admin.id)
    assert row.entity_type == "communication_suppression"
    assert row.details["reason"] == "fixed"
    assert row.details["scope"] == "all"
    assert row.details["suppression_reason"] == "bounce"
    # PII never lands in the trail.
    assert "jane@example.com" not in str(row.details)
    assert "jane@example.com" not in str(row.entity_id)


def test_clear_without_a_reason_is_refused_and_changes_nothing(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    _bounce(db, tenant_row)
    _login(client, "admin@example.com")
    response = client.post(
        "/admin/comms/suppressions/clear",
        data={"channel": "email", "address": "jane@example.com", "reason": "   "},
        follow_redirects=False,
    )
    assert response.status_code == 200  # re-rendered with the error, not redirected
    assert "needs a reason" in response.text
    assert _audit_rows(db, tenant_row.id) == []
    assert (
        service.get_suppression(
            db, tenant_row.id, channel="email", address="jane@example.com"
        )
        is not None
    )


def test_clearing_something_that_is_not_suppressed_writes_no_audit_row(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    _login(client, "admin@example.com")
    response = client.post(
        "/admin/comms/suppressions/clear",
        data={"channel": "email", "address": "nobody@example.com", "reason": "x"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].endswith("cleared=0")
    assert _audit_rows(db, tenant_row.id) == []


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        ("get", "/admin/comms/suppressions", None),
        ("get", "/admin/comms/receipts?address=a@example.com", None),
        (
            "post",
            "/admin/comms/suppressions/clear",
            {"channel": "email", "address": "jane@example.com", "reason": "x"},
        ),
    ],
)
def test_permission_denied_without_the_grant(
    client: TestClient,
    db: Session,
    tenant_row: Tenant,
    plain_user: Party,
    method: str,
    path: str,
    data: dict[str, str] | None,
) -> None:
    _bounce(db, tenant_row)
    _login(client, "plain@example.com")
    response = getattr(client, method)(
        path, follow_redirects=False, **({"data": data} if data else {})
    )
    assert response.status_code == 403
    assert "jane@example.com" not in response.text
    # The mutation never ran.
    assert _audit_rows(db, tenant_row.id) == []
    assert (
        service.get_suppression(
            db, tenant_row.id, channel="email", address="jane@example.com"
        )
        is not None
    )


def test_unauthenticated_requests_are_redirected_to_login(client: TestClient) -> None:
    for path in ("/admin/comms/suppressions", "/admin/comms/receipts"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert "/admin/login" in response.headers["location"]


def test_list_filters_by_address_scope_and_channel(
    db: Session, tenant_row: Tenant
) -> None:
    consent.suppress(
        db,
        tenant_row.id,
        channel="email",
        address="a@example.com",
        scope="all",
        reason="bounce",
    )
    consent.suppress(
        db, tenant_row.id, channel="email", address="b@example.org", scope="marketing"
    )
    consent.suppress(
        db,
        tenant_row.id,
        channel="sms",
        address="+15550100",
        scope="all",
        reason="manual",
    )
    db.commit()

    def addrs(**kw: str | None) -> set[str]:
        return {r.address for r in service.list_suppressions(db, tenant_row.id, **kw)}

    assert addrs() == {"a@example.com", "b@example.org", "15550100"}
    assert addrs(address="EXAMPLE.org") == {"b@example.org"}
    assert addrs(scope="all") == {"a@example.com", "15550100"}
    assert addrs(channel="sms") == {"15550100"}
    assert addrs(address="%") == set()  # LIKE wildcards are escaped
    assert service.count_suppressions(db, tenant_row.id, scope="marketing") == 1


def test_reads_are_scoped_to_the_tenant(db: Session, tenant_row: Tenant) -> None:
    other = Tenant(slug="other-k7", name="Other")
    db.add(other)
    db.commit()
    consent.suppress(
        db,
        other.id,
        channel="email",
        address="theirs@example.com",
        scope="all",
        reason="bounce",
    )
    delivery.record_receipt(
        db,
        other.id,
        channel="email",
        address="theirs@example.com",
        provider="smtp",
        status="bounced",
        provider_message_id="x-1",
    )
    db.commit()
    assert service.list_suppressions(db, tenant_row.id) == []
    assert (
        service.list_receipts(
            db, tenant_row.id, channel="email", address="theirs@example.com"
        )
        == []
    )
    assert (
        service.get_suppression(
            db, tenant_row.id, channel="email", address="theirs@example.com"
        )
        is None
    )
    # And a clear aimed at the other tenant's address does nothing here.
    assert (
        service.clear_suppression(
            db,
            tenant_row.id,
            channel="email",
            address="theirs@example.com",
            reason="x",
            actor_party_id=UUID(int=1),
        )
        is False
    )
    assert (
        service.get_suppression(
            db, other.id, channel="email", address="theirs@example.com"
        )
        is not None
    )
