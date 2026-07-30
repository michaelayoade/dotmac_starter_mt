"""Platform-surface deny-by-default canaries (control-plane security Task 1).

These are the load-bearing proofs that the platform control plane is actually
secured: an unauthenticated request must not reach ANY `/platform/*` route
from ANY host, and the two token populations (tenant `aud`-less tokens,
platform `aud="platform"` tokens) must never cross surfaces.

Independence of the two layers matters and is pinned separately:
- MIDDLEWARE: `/platform/*` on a host that is not the platform root domain is
  not a platform-valid path at all — 404 before any route runs
  (`dotmac_kernel.middleware.tenant._is_platform_path`, host-exact after Task 1;
  the pre-fix `startswith("/platform/")` branch let ANY host through, which
  is the captured "before" RED of `test_unknown_host_cannot_reach_platform_routes`).
- GUARD: even a request that reaches the route needs a live platform-admin
  bearer token (`dotmac_kernel.platform_auth.require_platform_admin`) — host
  re-checked (defense-in-depth), `aud="platform"` claim, live
  `platform_sessions` row, `is_active` admin.

Import discipline: no top-level `app.*` imports — the app engine binds
DATABASE_URL/PLATFORM_DATABASE_URL at first import, which must happen inside
a test/fixture AFTER the autouse `_set_database_url` fixture has pinned the
TEST_* URLs (see tests/conftest.py).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    PLATFORM_ADMIN_EMAIL as _ADMIN_EMAIL,
)
from tests.conftest import (
    PLATFORM_ADMIN_PASSWORD as _ADMIN_PASSWORD,
)
from tests.conftest import (
    platform_login as _platform_login,
)

_PLATFORM_HOST = "localhost"  # conftest pins PLATFORM_ROOT_DOMAIN=localhost


@pytest.fixture
def tenant_token(admin_session, tenant_a) -> str:
    """A valid TENANT access token (no `aud` claim) for tenant_a, built via
    direct admin-engine inserts so this fixture stays independent of the
    registration policy (Task 2 closes open registration by default)."""
    from dotmac_kernel.models import (
        AuthSession,
        Party,
        PartyPerson,
        PartyType,
        UserCredential,
    )
    from dotmac_kernel.security import hash_password, hash_token, issue_access_token

    party = Party(
        tenant_id=tenant_a.id,
        party_type=PartyType.person,
        display_name="Token User",
        email="token-user@tenant-a.example.com",
    )
    admin_session.add(party)
    admin_session.flush()
    admin_session.add(
        PartyPerson(party_id=party.id, first_name="Token", last_name="User")
    )
    admin_session.add(
        UserCredential(
            tenant_id=tenant_a.id,
            party_id=party.id,
            password_hash=hash_password(_ADMIN_PASSWORD),
        )
    )
    token, expires_at = issue_access_token(party.id, tenant_a.id)
    admin_session.add(
        AuthSession(
            tenant_id=tenant_a.id,
            party_id=party.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    admin_session.commit()
    # Cleanup rides on tenant_a's own teardown (DELETE FROM tenants cascades).
    return token


def test_unknown_host_cannot_reach_platform_routes(app_client: TestClient) -> None:
    """MIDDLEWARE layer: `/platform/*` on an unknown host is 404 — the
    pre-Task-1 `startswith("/platform/")` branch passed ANY host straight
    through to the (then-unauthenticated) route; that 200 is the captured
    "before" proof this canary exists to prevent.

    The body's error code is asserted, not just the status: the guard's own
    host check ALSO 404s (code `not_found`), so a bare status assertion
    could not tell the two layers apart and restoring the `startswith`
    branch would go undetected (defense-in-depth hiding a regression). The
    middleware's refusal is `tenant_not_found` — this canary demands THAT
    layer answered."""
    resp = app_client.get("/platform/tenants", headers={"Host": "nowhere.invalid"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "tenant_not_found", resp.text


def test_root_host_unauthenticated_is_401(app_client: TestClient) -> None:
    """GUARD layer: right host, no token → 401, never a tenant listing."""
    resp = app_client.get("/platform/tenants", headers={"Host": _PLATFORM_HOST})
    assert resp.status_code == 401, resp.text


def test_tenant_token_is_rejected_on_platform_surface(
    app_client: TestClient, tenant_token: str
) -> None:
    """A valid TENANT token must never authenticate a platform request —
    pinned via the `aud` claim (tenant tokens carry none)."""
    resp = app_client.get(
        "/platform/tenants",
        headers={
            "Host": _PLATFORM_HOST,
            "Authorization": f"Bearer {tenant_token}",
        },
    )
    assert resp.status_code == 401, resp.text


def test_platform_token_is_rejected_on_tenant_host(
    app_client: TestClient, platform_admin, tenant_a
) -> None:
    """A platform token on a TENANT host is 404 — the platform surface does
    not exist there (host-exact routing), regardless of credential validity."""
    token = _platform_login(app_client)
    resp = app_client.get(
        "/platform/tenants",
        headers={
            "Host": f"{tenant_a.slug}.{_PLATFORM_HOST}",
            "Authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 404, resp.text


def test_platform_login_rejects_bad_password(
    app_client: TestClient, platform_admin
) -> None:
    resp = app_client.post(
        "/platform/auth/login",
        json={"email": _ADMIN_EMAIL, "password": "wrong-password"},
        headers={"Host": _PLATFORM_HOST},
    )
    assert resp.status_code == 401, resp.text


def test_platform_login_is_404_off_the_root_host(
    app_client: TestClient, platform_admin, tenant_a
) -> None:
    """The login endpoint itself does not exist off the platform root host."""
    resp = app_client.post(
        "/platform/auth/login",
        json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
        headers={"Host": f"{tenant_a.slug}.{_PLATFORM_HOST}"},
    )
    assert resp.status_code == 404, resp.text


def test_cli_admin_logs_in_and_lists_tenants(
    app_client: TestClient, platform_admin, tenant_a
) -> None:
    """Happy path: CLI-created admin → login → authenticated tenant listing."""
    token = _platform_login(app_client)
    resp = app_client.get(
        "/platform/tenants",
        headers={
            "Host": _PLATFORM_HOST,
            "Authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 200, resp.text
    slugs = {row["slug"] for row in resp.json()}
    assert tenant_a.slug in slugs


def test_platform_logout_revokes_the_session(
    app_client: TestClient, platform_admin
) -> None:
    token = _platform_login(app_client)
    headers = {"Host": _PLATFORM_HOST, "Authorization": f"Bearer {token}"}
    assert app_client.get("/platform/tenants", headers=headers).status_code == 200
    # The GET above set a csrf_token cookie in the client jar; a jar-holding
    # client POSTing without the CSRF header is exactly what CSRFMiddleware
    # exists to block (403). A pure bearer API client carries no cookies —
    # model that here; the CSRF-vs-bearer interplay is pinned by
    # tests/test_security_middleware.py, not this canary.
    app_client.cookies.clear()
    assert app_client.post("/platform/auth/logout", headers=headers).status_code == 204
    assert app_client.get("/platform/tenants", headers=headers).status_code == 401
