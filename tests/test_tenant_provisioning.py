"""Atomic tenant provisioning canaries (control-plane security Task 2).

One `POST /platform/tenants` call (as an authenticated platform admin) must
produce a WORKING tenant — tenant row, login-able owner (party + person
subtype + credential), `admin` role grant, and a two-event audit trail
naming the platform actor — or NOTHING AT ALL: a failure anywhere in the
provisioning transaction rolls the whole thing back. A tenant without a
login-able owner is an unusable half-state that must never persist
(registration is policy-closed by default; see the registration canaries
at the bottom).

Import discipline: no top-level `app.*` imports — see
tests/test_platform_auth_denies.py's module docstring.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import open_registration, platform_login

_PLATFORM_HOST = "localhost"
_OWNER_PASSWORD = "owner-provision-password"


@pytest.fixture
def provisioned_slugs(admin_session):
    """Track slugs provisioned over HTTP (committed by the app) and delete
    their tenants (cascade) on teardown."""
    slugs: list[str] = []
    yield slugs
    if slugs:
        admin_session.execute(
            text("DELETE FROM tenants WHERE slug = ANY(:slugs)"),
            {"slugs": slugs},
        )
        admin_session.commit()


def _provision(
    client: TestClient,
    token: str,
    slug: str,
    owner_email: str,
    **overrides,
):
    payload = {
        "slug": slug,
        "name": f"Tenant {slug}",
        "owner_email": owner_email,
        "owner_password": _OWNER_PASSWORD,
        **overrides,
    }
    return client.post(
        "/platform/tenants",
        json=payload,
        headers={"Host": _PLATFORM_HOST, "Authorization": f"Bearer {token}"},
    )


def test_provisioning_creates_a_working_tenant_atomically(
    app_client: TestClient, platform_admin, admin_session, provisioned_slugs
) -> None:
    token = platform_login(app_client)
    provisioned_slugs.append("prov-atomic")
    resp = _provision(app_client, token, "prov-atomic", "owner@prov-atomic.example.com")
    assert resp.status_code == 201, resp.text
    tenant_id = resp.json()["id"]

    # Owner can log in on the tenant host IMMEDIATELY (credential + party).
    app_client.cookies.clear()
    login = app_client.post(
        "/auth/login",
        json={"email": "owner@prov-atomic.example.com", "password": _OWNER_PASSWORD},
        headers={"Host": "prov-atomic.localhost"},
    )
    assert login.status_code == 200, login.text
    owner_token = login.json()["access_token"]

    # ... and holds the admin role (admin-guarded endpoint answers 200) —
    # this request runs on the app_user session AS the new tenant, so it
    # also proves the provisioned rows are visible under RLS.
    parties = app_client.get(
        "/parties",
        headers={
            "Host": "prov-atomic.localhost",
            "Authorization": f"Bearer {owner_token}",
        },
    )
    assert parties.status_code == 200, parties.text

    # Two audit events under the NEW tenant, naming the platform actor.
    rows = admin_session.execute(
        text(
            "SELECT action, details FROM audit_events "
            "WHERE tenant_id = :tid ORDER BY action"
        ),
        {"tid": tenant_id},
    ).all()
    actions = [r.action for r in rows]
    assert actions == [
        "platform.tenant.create",
        "platform.tenant.owner_provision",
    ], actions
    for row in rows:
        assert row.details["platform_actor"] == platform_admin.email


def test_provisioning_rolls_back_completely_on_late_failure(
    app_client: TestClient, platform_admin, admin_session, monkeypatch
) -> None:
    """Force a failure AFTER the tenant + owner writes (the audit step) —
    the WHOLE transaction must vanish: no tenant row, no owner party."""
    from app.features.tenants import service as tenants_service

    def _boom(*args, **kwargs):
        raise RuntimeError("forced late-provisioning failure")

    monkeypatch.setattr(tenants_service, "write_audit_event", _boom)
    token = platform_login(app_client)
    with pytest.raises(RuntimeError, match="forced late-provisioning failure"):
        _provision(
            app_client, token, "prov-rollback", "owner@prov-rollback.example.com"
        )

    leftover_tenants = admin_session.execute(
        text("SELECT count(*) FROM tenants WHERE slug = 'prov-rollback'")
    ).scalar_one()
    leftover_parties = admin_session.execute(
        text(
            "SELECT count(*) FROM parties "
            "WHERE email = 'owner@prov-rollback.example.com'"
        )
    ).scalar_one()
    assert leftover_tenants == 0
    assert leftover_parties == 0


def test_duplicate_slug_is_a_409_with_no_partial_writes(
    app_client: TestClient, platform_admin, admin_session, provisioned_slugs
) -> None:
    token = platform_login(app_client)
    provisioned_slugs.append("prov-dup")
    first = _provision(app_client, token, "prov-dup", "owner@prov-dup.example.com")
    assert first.status_code == 201, first.text
    second = _provision(app_client, token, "prov-dup", "owner2@prov-dup.example.com")
    assert second.status_code == 409, second.text
    count = admin_session.execute(
        text("SELECT count(*) FROM tenants WHERE slug = 'prov-dup'")
    ).scalar_one()
    assert count == 1
    second_owner = admin_session.execute(
        text(
            "SELECT count(*) FROM parties "
            "WHERE email = 'owner2@prov-dup.example.com'"
        )
    ).scalar_one()
    assert second_owner == 0


def test_platform_listing_is_bounded_and_pageable(
    app_client: TestClient, platform_admin, provisioned_slugs
) -> None:
    token = platform_login(app_client)
    for i in range(3):
        slug = f"prov-page-{i}"
        provisioned_slugs.append(slug)
        assert (
            _provision(app_client, token, slug, f"owner@{slug}.example.com").status_code
            == 201
        )
    headers = {"Host": _PLATFORM_HOST, "Authorization": f"Bearer {token}"}
    two = app_client.get("/platform/tenants?limit=2", headers=headers)
    assert two.status_code == 200 and len(two.json()) == 2
    paged = app_client.get("/platform/tenants?limit=2&offset=2", headers=headers)
    assert paged.status_code == 200 and len(paged.json()) >= 1
    # Out-of-range values are CLAMPED, never a 422 or an unbounded query.
    clamped_low = app_client.get("/platform/tenants?limit=0&offset=-5", headers=headers)
    assert clamped_low.status_code == 200 and len(clamped_low.json()) == 1
    clamped_high = app_client.get("/platform/tenants?limit=99999", headers=headers)
    assert clamped_high.status_code == 200


def test_registration_defaults_closed_and_opens_by_policy(
    app_client: TestClient,
    platform_admin,
    admin_session,
    provisioned_slugs,
) -> None:
    """Registration is CLOSED by default on a freshly provisioned tenant
    (403 `registration_closed`); flipping `auth.registration_policy` to
    `open` admits a PLAIN user — never an admin (the first-registrant
    bootstrap is gone)."""
    token = platform_login(app_client)
    provisioned_slugs.append("prov-reg")
    assert (
        _provision(
            app_client, token, "prov-reg", "owner@prov-reg.example.com"
        ).status_code
        == 201
    )
    register_payload = {
        "email": "joiner@prov-reg.example.com",
        "password": "joiner-password-123",
        "first_name": "Join",
        "last_name": "Er",
    }
    headers = {"Host": "prov-reg.localhost"}
    app_client.cookies.clear()
    closed = app_client.post("/auth/register", json=register_payload, headers=headers)
    assert closed.status_code == 403, closed.text
    assert closed.json()["message"] == "registration_closed"

    from dotmac_kernel.models import Tenant

    tenant = admin_session.query(Tenant).filter_by(slug="prov-reg").one()
    open_registration(admin_session, tenant)
    opened = app_client.post("/auth/register", json=register_payload, headers=headers)
    assert opened.status_code == 201, opened.text

    login = app_client.post(
        "/auth/login",
        json={
            "email": register_payload["email"],
            "password": register_payload["password"],
        },
        headers=headers,
    )
    assert login.status_code == 200, login.text
    joiner_token = login.json()["access_token"]
    # A registered user is a PLAIN user: the admin-guarded endpoint denies.
    denied = app_client.get(
        "/parties",
        headers={**headers, "Authorization": f"Bearer {joiner_token}"},
    )
    assert denied.status_code == 403, denied.text
