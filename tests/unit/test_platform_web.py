"""The platform administration surface: plane separation, guards, and writes.

The load-bearing property here is not "the screens render" — it is that the two
administrative planes cannot be crossed. A tenant admin holds a perfectly valid
session cookie; it must buy them nothing on `/platform/*`, and the surface must
not even appear to exist off the platform host.
"""

from __future__ import annotations

import pytest
from dotmac_kernel import platform_auth
from dotmac_kernel.config import settings
from dotmac_kernel.db import get_db, get_platform_db
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.flag_models import FeatureFlagOverride
from dotmac_kernel.models_platform import PlatformAdmin, PlatformSession
from dotmac_kernel.platform_web import PLATFORM_COOKIE
from dotmac_kernel.platform_web import router as platform_router
from dotmac_kernel.security import hash_password, hash_token
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

_HOST = "localhost"


@pytest.fixture(autouse=True)
def _root_domain(monkeypatch):
    monkeypatch.setattr(settings, "platform_root_domain", _HOST)


@pytest.fixture()
def platform_admin(db) -> PlatformAdmin:
    row = PlatformAdmin(
        email="ops@platform.example.com",
        password_hash=hash_password("platform-password"),
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def client(db) -> TestClient:
    """The platform router on a bare app.

    Same reasoning as `test_admin_route_sweep`'s minimal app: the production
    app's tenant middleware opens its own connection outside dependency
    injection and cannot run against SQLite. Both DB dependencies are overridden
    to the one test session, since this surface uses the platform session while
    the fixtures write through the same connection.
    """
    app = FastAPI()
    app.include_router(platform_router, prefix="/platform")
    register_error_handlers(app)
    app.dependency_overrides[get_platform_db] = lambda: db
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, follow_redirects=False)


def _session_cookie(db, admin: PlatformAdmin) -> str:
    token, expires_at = platform_auth.issue_platform_token(admin.id)
    db.add(
        PlatformSession(
            admin_id=admin.id, token_hash=hash_token(token), expires_at=expires_at
        )
    )
    db.flush()
    return token


# ── Plane separation ────────────────────────────────────────────────────────


def test_no_cookie_redirects_to_the_platform_login(client) -> None:
    response = client.get("/platform", headers={"Host": _HOST})
    assert response.status_code == 302
    assert response.headers["location"].startswith("/platform/login")


def test_a_tenant_session_cookie_buys_nothing_here(client, db, party_row) -> None:
    """The cross-plane property.

    A tenant admin's `access_token` is a real, valid session — for the OTHER
    plane. It is presented here under the platform cookie name, which is the
    strongest form of the attempt, and it must still bounce: the token's
    audience is not one a `PlatformSession` can hold.
    """
    from dotmac_kernel.security import issue_access_token

    tenant_token, _expires = issue_access_token(
        str(party_row.id), tenant_id=str(party_row.tenant_id)
    )
    client.cookies.set(PLATFORM_COOKIE, tenant_token)
    response = client.get("/platform", headers={"Host": _HOST})
    assert response.status_code == 302
    assert response.headers["location"].startswith("/platform/login")


def test_the_surface_does_not_exist_off_the_platform_host(client) -> None:
    """404, not 401 — saying "unauthorized" would confirm the surface is there
    for anyone probing a tenant's domain."""
    assert client.get(
        "/platform", headers={"Host": "acme.example.com"}
    ).status_code == (404)
    assert (
        client.get("/platform/login", headers={"Host": "acme.example.com"}).status_code
        == 404
    )


def test_a_platform_admin_reaches_the_inventory(client, db, platform_admin) -> None:
    client.cookies.set(PLATFORM_COOKIE, _session_cookie(db, platform_admin))
    response = client.get("/platform", headers={"Host": _HOST})
    assert response.status_code == 200
    assert "Platform administration" in response.text


# ── Login / logout ──────────────────────────────────────────────────────────


def test_login_rejects_bad_credentials_without_enumerating(
    client, platform_admin
) -> None:
    response = client.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "wrong"},
        headers={"Host": _HOST},
    )
    assert response.status_code == 200
    assert "Invalid email or password" in response.text
    assert PLATFORM_COOKIE not in response.cookies

    missing = client.post(
        "/platform/login",
        data={"email": "nobody@example.com", "password": "wrong"},
        headers={"Host": _HOST},
    )
    # Byte-identical to the wrong-password case: the form is not an oracle for
    # which operator accounts exist.
    assert missing.status_code == 200
    assert "Invalid email or password" in missing.text


def test_login_sets_the_platform_cookie(client, platform_admin) -> None:
    response = client.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "platform-password"},
        headers={"Host": _HOST},
    )
    assert response.status_code == 302
    assert PLATFORM_COOKIE in response.cookies


def test_logout_requires_a_session_and_clears_the_cookie(
    client, db, platform_admin
) -> None:
    token = _session_cookie(db, platform_admin)
    client.cookies.set(PLATFORM_COOKIE, token)
    response = client.post("/platform/logout", headers={"Host": _HOST})
    assert response.status_code == 302
    assert response.headers["location"] == "/platform/login"
    session = db.scalars(
        select(PlatformSession).where(PlatformSession.admin_id == platform_admin.id)
    ).one()
    assert session.revoked_at is not None


# ── Feature flag overrides ──────────────────────────────────────────────────


def _authed(client, db, admin) -> TestClient:
    client.cookies.set(PLATFORM_COOKIE, _session_cookie(db, admin))
    return client


def test_setting_a_flag_writes_a_deployment_override(
    client, db, platform_admin
) -> None:
    c = _authed(client, db, platform_admin)
    response = c.post(
        "/platform/flags/template_studio.strict_render",
        data={"action": "set", "value": "off"},
        headers={"Host": _HOST},
    )
    assert response.status_code == 200
    row = db.execute(
        select(FeatureFlagOverride).where(
            FeatureFlagOverride.flag_code == "template_studio.strict_render"
        )
    ).scalar_one()
    assert row.tenant_id is None, "a platform operator sets the DEPLOYMENT scope"
    assert row.value is False
    assert row.updated_by == platform_admin.id


def test_the_kill_switch_is_recorded_as_such(client, db, platform_admin) -> None:
    c = _authed(client, db, platform_admin)
    c.post(
        "/platform/flags/template_studio.strict_render",
        data={"action": "kill"},
        headers={"Host": _HOST},
    )
    row = db.execute(select(FeatureFlagOverride)).scalar_one()
    assert row.kill_switch is True
    # Not also a value — a kill switch is its own state, and the evaluator
    # treats it as outranking every override rather than as "value = false".
    assert row.value is None


def test_clearing_removes_the_override(client, db, platform_admin) -> None:
    c = _authed(client, db, platform_admin)
    c.post(
        "/platform/flags/template_studio.strict_render",
        data={"action": "set", "value": "on"},
        headers={"Host": _HOST},
    )
    c.post(
        "/platform/flags/template_studio.strict_render",
        data={"action": "clear"},
        headers={"Host": _HOST},
    )
    assert db.execute(select(FeatureFlagOverride)).first() is None


def test_an_undeclared_flag_cannot_be_overridden(client, db, platform_admin) -> None:
    """An override may only reference a DECLARED flag — the same rule that
    holds for entitlement grants. Otherwise the table accumulates rows for
    flags that no longer exist and nothing notices."""
    from dotmac_kernel.flags import UndeclaredFlagError

    c = _authed(client, db, platform_admin)
    with pytest.raises(UndeclaredFlagError):
        c.post(
            "/platform/flags/ghost.flag",
            data={"action": "set", "value": "on"},
            headers={"Host": _HOST},
        )


# ── Entitlements ────────────────────────────────────────────────────────────
#
# The entitlement WRITE path is proven against real Postgres in
# `tests/test_platform_entitlements.py`, not here: it calls `set_config` to
# establish RLS tenant context on the transaction (the `provision_tenant`
# idiom), and SQLite has no such function. Faking it in the unit lane would
# mean testing a code path no deployment runs — the same reason tenancy
# correctness has always lived in the Postgres canaries.
