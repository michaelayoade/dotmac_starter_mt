"""Web (cookie) auth tenant-isolation canary.

Mirrors `tests/test_auth_tenant_claim.py` (the bearer-token version) for the
cookie path: provision an admin (`provision_owner`, `tests/conftest.py` —
registration no longer grants admin, Task 2) + web-login on tenant A's
host, then replay the
resulting `access_token` cookie against tenant B's host. The shared
`authenticate_request` seam (`dotmac_kernel.deps`) checks the token's `tenant_id`
claim against `request.state.tenant` — resolved from tenant B's host by
`TenantResolverMiddleware` — so the session must fail closed: a redirect
to `/admin/login`, never the dashboard, and no tenant A data anywhere in
the response body.

CSRF is now enforced on every composed browser route, login included, so
both canaries below log in through the shared `web_login` bridge
(`tests/test_admin_portal_e2e.py`). Read the isolation assertions with that
in mind: the rejection this module exists to prove is asserted on a SAFE
`GET`, which `require_csrf` returns from before validating anything — so a
302-to-login there can only be the authentication seam failing closed, and
never a CSRF verdict wearing its costume. The logout canary additionally
asserts its POST came back 302 (logout's always-succeeds contract) for the
same reason: a 403 would mean the request never reached the tenant-scoped
lookup it is supposed to be testing.

Requires a real Postgres (RLS aside, `AuthSession`/`Party`/`Role` rows must
actually exist per-tenant — this exercises the full stack, not a mock).
"""

from __future__ import annotations

from dotmac_kernel.middleware.csrf import CSRF_HEADER
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import client_for, provision_owner
from tests.test_admin_portal_e2e import csrf_proof, web_login


def test_web_login_cookie_from_tenant_a_rejected_on_tenant_b_host(
    app_client: TestClient,
    admin_session: Session,
    tenant_a,
    tenant_b,
) -> None:
    a = client_for(app_client, tenant_a.slug)

    provision_owner(admin_session, tenant_a, "web-canary@tenant-a.example.com")

    # `web_login` asserts the 302 and its `/admin` landing internally.
    web_login(a, "web-canary@tenant-a.example.com")
    token = a.cookies["access_token"]

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

    web_login(a, "logout-canary@tenant-a.example.com")
    token = a.cookies["access_token"]

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    # F7: logout is now POST, so it is CSRF-checked. Replay tenant A's
    # cookie by putting it in tenant B's OWN jar before minting the proof:
    # tokens are session-bound (the signature covers the session cookies on
    # the issuing request), so a token minted before the replayed
    # `access_token` was present would not verify on the POST that carries
    # it, and the POST would 403 before ever reaching
    # `auth_service.web_logout` — making the assertion below pass for the
    # wrong reason (blocked by CSRF, not "tenant-scoped lookup found
    # nothing under tenant B").
    b.cookies.set("access_token", token)
    csrf_b = csrf_proof(b)
    logout = b.post(
        "/admin/logout",
        headers={CSRF_HEADER: csrf_b},
        follow_redirects=False,
    )
    # Logout's contract is "always succeeds" (`app.features.auth.web`), so a
    # 302 here is the positive proof that the proof-carrying request really
    # did reach the handler and the tenant-scoped lookup ran.
    assert logout.status_code == 302, logout.text

    # Tenant A's session must still be valid — tenant B's logout call
    # (tenant-scoped lookup finds nothing under tenant B) must not have
    # revoked it.
    still_valid = a.get("/admin", cookies={"access_token": token})
    assert still_valid.status_code == 200
    assert "Dashboard" in still_valid.text
