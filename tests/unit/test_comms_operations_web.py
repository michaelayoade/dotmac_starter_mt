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
RESTORED = "communication.suppression.transactional_restored"


def _person(
    db: Session,
    tenant: Tenant,
    email: str,
    *,
    admin: bool,
    role_slug: str = "admin",
) -> Party:
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
        role = Role(tenant_id=tenant.id, slug=role_slug, name=role_slug.title())
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
                AuditEvent.tenant_id == tenant_id, AuditEvent.action == RESTORED
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


def _suppression_id(db: Session, tenant: Tenant, address: str = "jane@example.com"):
    row = service.get_suppression(db, tenant.id, channel="email", address=address)
    assert row is not None
    return row


def _restore(client: TestClient, suppression_id, reason: str = "mailbox fixed", **kw):
    return client.post(
        f"/admin/comms/suppressions/{suppression_id}/restore",
        data={"reason": reason, **kw},
        follow_redirects=False,
    )


def _may_send(db: Session, tenant: Tenant, category: str) -> bool:
    return consent.may_send(
        db,
        tenant.id,
        channel="email",
        address="jane@example.com",
        category=category,
    )


@pytest.fixture(autouse=True)
def _marketing_vocabulary():
    previous = consent._reset_registries_for_tests(marketing=["promo"])
    yield
    consent._reset_registries_for_tests(marketing=previous[0], numeric=previous[1])


def test_a_bounce_suppression_is_visible_and_restorable(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    """The 'bounce silently stops password resets' risk, end to end."""
    _bounce(db, tenant_row)
    assert _may_send(db, tenant_row, "password_reset") is False
    _login(client, "admin@example.com")
    row = _suppression_id(db, tenant_row)

    listing = client.get("/admin/comms/suppressions")
    assert listing.status_code == 200
    assert "jane@example.com" in listing.text
    assert "Restore transactional mail; marketing stays blocked" in listing.text
    # Links carry the id, not the address.
    assert f"suppression_id={row.id}" in listing.text
    assert "address=jane" not in listing.text

    receipts = client.get(f"/admin/comms/receipts?suppression_id={row.id}")
    assert receipts.status_code == 200
    assert "bounced" in receipts.text and "550" in receipts.text
    assert "blocks every message" in receipts.text
    assert "no personal data" in receipts.text.lower()

    response = _restore(client, row.id)
    assert response.status_code == 302
    assert response.headers["location"].endswith("restored=1")
    assert "jane@example.com" not in response.headers["location"]
    assert _may_send(db, tenant_row, "password_reset") is True
    # Marketing stays blocked; the row is retained with its provenance.
    assert _may_send(db, tenant_row, "promo") is False
    kept = _suppression_id(db, tenant_row)
    assert (kept.id, kept.scope, kept.reason) == (row.id, "marketing", "bounce")
    assert (
        len(
            service.list_receipts(
                db, tenant_row.id, channel="email", address="jane@example.com"
            )
        )
        == 1
    )

    after = client.get(response.headers["location"])
    assert "not re-sent" in after.text


def test_restore_writes_one_audit_row_naming_actor_reason_and_scopes(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    _bounce(db, tenant_row)
    _login(client, "admin@example.com")
    row = _suppression_id(db, tenant_row)
    _restore(client, row.id, "fixed")
    rows = _audit_rows(db, tenant_row.id)
    assert len(rows) == 1
    event = rows[0]
    assert event.actor_type == "user"
    assert event.actor_id == str(admin.id)
    assert event.entity_type == "communication_suppression"
    assert event.entity_id == str(row.id)
    assert event.details == {
        "reason": "fixed",
        "channel": "email",
        "previous_scope": "all",
        "new_scope": "marketing",
        "suppression_reason": "bounce",
    }
    assert "jane@example.com" not in str(event.details)
    assert "sha256" not in str(event.details)


def test_unsubscribe_then_bounce_restores_to_marketing_only(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    consent.suppress(
        db,
        tenant_row.id,
        channel="email",
        address="jane@example.com",
        scope="marketing",
        reason="unsubscribe",
    )
    _bounce(db, tenant_row)  # escalates the same row to all/bounce
    db.commit()
    assert _may_send(db, tenant_row, "password_reset") is False
    _login(client, "admin@example.com")
    row = _suppression_id(db, tenant_row)
    assert (row.scope, row.reason) == ("all", "bounce")
    assert _restore(client, row.id).status_code == 302
    assert _may_send(db, tenant_row, "password_reset") is True
    assert _may_send(db, tenant_row, "promo") is False  # opt-out survives


def test_complaint_restores_to_marketing_only(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    delivery.record_receipt(
        db,
        tenant_row.id,
        channel="email",
        address="jane@example.com",
        provider="smtp",
        status="complaint",
        provider_message_id="c-1",
    )
    db.commit()
    _login(client, "admin@example.com")
    row = _suppression_id(db, tenant_row)
    assert row.reason == "complaint"
    assert _restore(client, row.id).status_code == 302
    assert _may_send(db, tenant_row, "password_reset") is True
    assert _may_send(db, tenant_row, "promo") is False
    assert _suppression_id(db, tenant_row).reason == "complaint"


@pytest.mark.parametrize(
    ("scope", "reason", "message"),
    [
        ("all", "erasure", "erasure"),
        ("marketing", "unsubscribe", "marketing opt-out"),
    ],
)
def test_erasure_and_marketing_rows_are_refused_unchanged(
    client: TestClient,
    db: Session,
    tenant_row: Tenant,
    admin: Party,
    scope: str,
    reason: str,
    message: str,
) -> None:
    consent.suppress(
        db,
        tenant_row.id,
        channel="email",
        address="jane@example.com",
        scope=scope,
        reason=reason,
    )
    db.commit()
    _login(client, "admin@example.com")
    row = _suppression_id(db, tenant_row)
    page = client.get("/admin/comms/suppressions")
    assert "Restore transactional mail" not in page.text
    response = _restore(client, row.id)
    assert response.status_code == 200  # re-rendered with the refusal
    assert message in response.text
    assert _audit_rows(db, tenant_row.id) == []
    assert _suppression_id(db, tenant_row) == row


def test_restore_without_a_reason_is_refused_and_changes_nothing(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    _bounce(db, tenant_row)
    _login(client, "admin@example.com")
    row = _suppression_id(db, tenant_row)
    response = _restore(client, row.id, "   ")
    assert response.status_code == 200
    assert "needs a reason" in response.text
    assert _audit_rows(db, tenant_row.id) == []
    assert _suppression_id(db, tenant_row).scope == "all"


def test_restoring_an_unknown_suppression_writes_no_audit_row(
    client: TestClient, db: Session, tenant_row: Tenant, admin: Party
) -> None:
    _login(client, "admin@example.com")
    response = _restore(client, UUID(int=7), "x")
    assert response.status_code == 302
    assert response.headers["location"].endswith("restored=0")
    assert _audit_rows(db, tenant_row.id) == []


def test_read_without_manage_gets_403_on_the_post_and_nothing_changes(
    client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    from dotmac_kernel.modules import ModuleManifest
    from dotmac_kernel.permissions import (
        PermissionCatalogue,
        PermissionSpec,
        install_permissions,
    )

    # A deployment that binds read to a `reader` role and manage to `admin`
    # (the autouse fixture reinstalls the real catalogue before the next test).
    install_permissions(
        PermissionCatalogue.from_manifests(
            (
                ModuleManifest(
                    code="comms_operations",
                    version="1",
                    permissions=(
                        PermissionSpec(
                            "comms_operations.suppressions.read",
                            default_roles=("reader",),
                        ),
                        PermissionSpec(
                            "comms_operations.receipts.read", default_roles=("reader",)
                        ),
                        PermissionSpec(
                            "comms_operations.suppressions.manage",
                            default_roles=("admin",),
                        ),
                    ),
                ),
            )
        )
    )
    _person(db, tenant_row, "reader@example.com", admin=True, role_slug="reader")
    _bounce(db, tenant_row)
    row = _suppression_id(db, tenant_row)
    _login(client, "reader@example.com")
    assert client.get("/admin/comms/suppressions").status_code == 200
    denied = _restore(client, row.id)
    assert denied.status_code == 403
    assert _audit_rows(db, tenant_row.id) == []
    assert _suppression_id(db, tenant_row).scope == "all"


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        ("get", "/admin/comms/suppressions", None),
        ("get", "/admin/comms/receipts?address=a@example.com", None),
        (
            "post",
            f"/admin/comms/suppressions/{UUID(int=5)}/restore",
            {"reason": "x"},
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
    kwargs = {"data": data} if data else {}
    response = getattr(client, method)(path, follow_redirects=False, **kwargs)
    assert response.status_code == 403
    assert "jane@example.com" not in response.text
    assert _audit_rows(db, tenant_row.id) == []
    assert _suppression_id(db, tenant_row).scope == "all"


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


def test_reads_and_restore_are_scoped_to_the_tenant(
    db: Session, tenant_row: Tenant
) -> None:
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
    theirs = service.get_suppression(
        db, other.id, channel="email", address="theirs@example.com"
    )
    assert theirs is not None
    assert service.list_suppressions(db, tenant_row.id) == []
    assert (
        service.list_receipts(
            db, tenant_row.id, channel="email", address="theirs@example.com"
        )
        == []
    )
    assert service.get_suppression_by_id(db, tenant_row.id, theirs.id) is None
    assert (
        service.restore_transactional_mail(
            db,
            tenant_row.id,
            suppression_id=theirs.id,
            reason="x",
            actor_party_id=UUID(int=1),
        )
        is False
    )
    assert (
        service.get_suppression(
            db, other.id, channel="email", address="theirs@example.com"
        ).scope
        == "all"
    )


def test_kernel_restore_transactional_keeps_the_row_and_refuses_erasure(
    db: Session, tenant_row: Tenant
) -> None:
    from dotmac_kernel.consent import ConsentError, restore_transactional

    consent.suppress(
        db,
        tenant_row.id,
        channel="email",
        address="e@example.com",
        scope="all",
        reason="erasure",
    )
    with pytest.raises(ConsentError):
        restore_transactional(
            db, tenant_row.id, channel="email", address="e@example.com"
        )
    consent.suppress(
        db, tenant_row.id, channel="email", address="m@example.com", scope="marketing"
    )
    with pytest.raises(ConsentError):
        restore_transactional(
            db, tenant_row.id, channel="email", address="m@example.com"
        )
    assert (
        restore_transactional(
            db, tenant_row.id, channel="email", address="none@example.com"
        )
        is None
    )
