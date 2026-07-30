"""Web (cookie) auth tenant-isolation canary.

Mirrors `tests/test_auth_tenant_claim.py` (the bearer-token version) for the
cookie path: provision an admin (`provision_owner`, `tests/conftest.py` —
registration no longer grants admin, Task 2) + web-login on tenant A's
host, then replay the
resulting `access_token` cookie against tenant B's host. The shared
`authenticate_request` seam (`app.core.deps`) checks the token's `tenant_id`
claim against `request.state.tenant` — resolved from tenant B's host by
`TenantResolverMiddleware` — so the session must fail closed: a redirect
to `/admin/login`, never the dashboard, and no tenant A data anywhere in
the response body.

Requires a real Postgres (RLS aside, `AuthSession`/`Party`/`Role` rows must
actually exist per-tenant — this exercises the full stack, not a mock).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import client_for, provision_owner

PASSWORD = "correct horse battery staple"


def test_web_login_cookie_from_tenant_a_rejected_on_tenant_b_host(
    app_client: TestClient,
    admin_session: Session,
    tenant_a,
    tenant_b,
) -> None:
    a = client_for(app_client, tenant_a.slug)

    provision_owner(admin_session, tenant_a, "web-canary@tenant-a.example.com")

    login = a.post(
        "/admin/login",
        data={"username": "web-canary@tenant-a.example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert login.status_code == 302
    assert login.headers["location"] == "/admin"
    token = login.cookies["access_token"]

    # Sanity: the cookie DOES work on tenant A's own host.
    dashboard_a = a.get("/admin", cookies={"access_token": token})
    assert dashboard_a.status_code == 200
    assert "Dashboard" in dashboard_a.text

    # Replay the SAME cookie against tenant B's host.
    b = client_for(TestClient(app_client.app), tenant_b.slug)
    rejected = b.get("/admin", cookies={"access_token": token}, follow_redirects=False)
    assert rejected.status_code == 302
    assert rejected.headers["location"].startswith("/admin/login")

    # Zero data leakage: no dashboard markup, no tenant A identity data, in
    # the redirect response body.
    assert "Dashboard" not in rejected.text
    assert "web-canary" not in rejected.text
    assert str(tenant_a.id) not in rejected.text


def test_web_logout_only_revokes_the_calling_tenants_session(
    app_client: TestClient,
    admin_session: Session,
    tenant_a,
    tenant_b,
) -> None:
    """Defense-in-depth companion: logging out while the cookie is replayed
    on tenant B's host must not silently succeed against tenant A's session
    (the lookup is tenant-scoped) and must not affect tenant A's ability to
    keep using its own, still-valid session."""
    a = client_for(app_client, tenant_a.slug)
    provision_owner(admin_session, tenant_a, "logout-canary@tenant-a.example.com")

    login = a.post(
        "/admin/login",
        data={"username": "logout-canary@tenant-a.example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    token = login.cookies["access_token"]

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    # F7: logout is now POST, so it's CSRF-checked — capture a csrf_token
    # cookie from a safe GET first and present it as the header, same
    # double-submit bridge every other mutating-request canary in this
    # suite replicates (see e.g. test_admin_portal_e2e.py's `_web_login`).
    # Without this the POST would 403 before ever reaching
    # `auth_service.web_logout`, which would make the assertion below pass
    # for the wrong reason (blocked by CSRF, not "tenant-scoped lookup found
    # nothing under tenant B").
    login_page_b = b.get("/admin/login")
    csrf_b = login_page_b.cookies.get("csrf_token")
    assert csrf_b, "CSRFMiddleware did not set a csrf_token cookie on the login GET"
    b.post(
        "/admin/logout",
        cookies={"access_token": token},
        headers={"x-csrf-token": csrf_b},
        follow_redirects=False,
    )

    # Tenant A's session must still be valid — tenant B's logout call
    # (tenant-scoped lookup finds nothing under tenant B) must not have
    # revoked it.
    still_valid = a.get("/admin", cookies={"access_token": token})
    assert still_valid.status_code == 200
    assert "Dashboard" in still_valid.text
