"""K-7 operator surface on real Postgres: RLS isolation, CSRF, audit, grants.

Drives the REAL composed app (`app.main.app`) through the browser login bridge,
so the facet admission, `require_web_permission` guard, CSRF dependency and the
tenant RLS context are all the production ones. Data is seeded through the
admin session (RLS bypassed) using the kernel's own writers.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import uuid

from dotmac_kernel import consent, delivery
from dotmac_kernel.middleware.csrf import CSRF_HEADER
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import client_for, provision_owner
from tests.test_admin_portal_e2e import web_login

CLEARED = "communication.suppression.cleared"
ADDRESS = "jane@example.com"


def _seed_bounce(admin_session: Session, tenant, address: str = ADDRESS) -> None:
    delivery.record_receipt(
        admin_session,
        tenant.id,
        channel="email",
        address=address,
        provider="smtp",
        status="bounced",
        provider_message_id=f"m-{uuid.uuid4().hex[:8]}",
        response_code="550",
    )
    admin_session.commit()


def _audit_count(admin_session: Session, tenant) -> int:
    return admin_session.execute(
        text("SELECT count(*) FROM audit_events WHERE tenant_id = :t AND action = :a"),
        {"t": str(tenant.id), "a": CLEARED},
    ).scalar_one()


def _suppressed(admin_session: Session, tenant, address: str = ADDRESS) -> bool:
    return bool(
        admin_session.execute(
            text(
                "SELECT count(*) FROM communication_suppressions "
                "WHERE tenant_id = :t AND address = :a"
            ),
            {"t": str(tenant.id), "a": address},
        ).scalar_one()
    )


def test_bounce_is_listed_and_clearing_it_is_audited_and_restores_sending(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    provision_owner(admin_session, tenant_a, "ops@tenant-a.example.com")
    _seed_bounce(admin_session, tenant_a)
    a = client_for(app_client, tenant_a.slug)
    csrf = web_login(a, "ops@tenant-a.example.com")

    listing = a.get("/admin/comms/suppressions")
    assert listing.status_code == 200
    assert ADDRESS in listing.text
    receipts = a.get("/admin/comms/receipts", params={"address": ADDRESS})
    assert receipts.status_code == 200
    assert "bounced" in receipts.text

    cleared = a.post(
        "/admin/comms/suppressions/clear",
        data={"channel": "email", "address": ADDRESS, "reason": "mailbox fixed"},
        headers={CSRF_HEADER: csrf},
        follow_redirects=False,
    )
    assert cleared.status_code == 302, cleared.text
    assert not _suppressed(admin_session, tenant_a)
    assert _audit_count(admin_session, tenant_a) == 1
    admin_session.expire_all()
    assert consent.may_send(
        admin_session,
        tenant_a.id,
        channel="email",
        address=ADDRESS,
        category="password_reset",
    )


def test_clear_without_the_csrf_proof_is_refused(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    provision_owner(admin_session, tenant_a, "ops2@tenant-a.example.com")
    _seed_bounce(admin_session, tenant_a)
    a = client_for(app_client, tenant_a.slug)
    web_login(a, "ops2@tenant-a.example.com")
    refused = a.post(
        "/admin/comms/suppressions/clear",
        data={"channel": "email", "address": ADDRESS, "reason": "x"},
        follow_redirects=False,
    )
    assert refused.status_code == 403
    assert _suppressed(admin_session, tenant_a)
    assert _audit_count(admin_session, tenant_a) == 0


def test_a_party_without_the_facet_grant_gets_403_and_nothing_changes(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    provision_owner(
        admin_session, tenant_a, "viewer@tenant-a.example.com", role_slug="viewer"
    )
    _seed_bounce(admin_session, tenant_a)
    a = client_for(app_client, tenant_a.slug)
    # A viewer cannot pass facet admission, so a browser login proof still works
    # but every comms route refuses.
    csrf = web_login(a, "viewer@tenant-a.example.com", landing="/admin")
    for path in (
        "/admin/comms/suppressions",
        f"/admin/comms/receipts?address={ADDRESS}",
    ):
        response = a.get(path, follow_redirects=False)
        assert response.status_code == 403, path
        assert ADDRESS not in response.text
    posted = a.post(
        "/admin/comms/suppressions/clear",
        data={"channel": "email", "address": ADDRESS, "reason": "x"},
        headers={CSRF_HEADER: csrf},
        follow_redirects=False,
    )
    assert posted.status_code == 403
    assert _suppressed(admin_session, tenant_a)
    assert _audit_count(admin_session, tenant_a) == 0


def test_tenant_b_cannot_see_or_clear_tenant_a_suppressions(
    app_client: TestClient, admin_session: Session, tenant_a, tenant_b
) -> None:
    provision_owner(admin_session, tenant_b, "ops@tenant-b.example.org")
    _seed_bounce(admin_session, tenant_a, "alice@tenant-a-customer.example")
    b = client_for(TestClient(app_client.app), tenant_b.slug)
    csrf = web_login(b, "ops@tenant-b.example.org")

    listing = b.get("/admin/comms/suppressions")
    assert listing.status_code == 200
    assert "alice@tenant-a-customer.example" not in listing.text
    receipts = b.get(
        "/admin/comms/receipts", params={"address": "alice@tenant-a-customer.example"}
    )
    assert receipts.status_code == 200
    assert "bounced" not in receipts.text
    assert "No receipts for this address" in receipts.text

    attempt = b.post(
        "/admin/comms/suppressions/clear",
        data={
            "channel": "email",
            "address": "alice@tenant-a-customer.example",
            "reason": "cross-tenant attempt",
        },
        headers={CSRF_HEADER: csrf},
        follow_redirects=False,
    )
    assert attempt.status_code == 302
    assert attempt.headers["location"].endswith("cleared=0")
    # Tenant A's suppression is untouched and nothing was audited for either.
    assert _suppressed(admin_session, tenant_a, "alice@tenant-a-customer.example")
    assert _audit_count(admin_session, tenant_a) == 0
    assert _audit_count(admin_session, tenant_b) == 0
