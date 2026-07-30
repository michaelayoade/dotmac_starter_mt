"""Task 4 (F4) PROOF canary: saving a tenant's branding via the real web
form changes what OTHER portal pages show — the dashboard sidebar and the
tenant's own (pre-auth) login page — not just the branding editor's own
preview, and never leaks into a different tenant's login page.

Same CSRF-header-bridge pattern as `tests/test_admin_portal_e2e.py` (capture
the `csrf_token` cookie from the first safe `GET`, replay it as the
`X-CSRF-Token` header on the mutating `POST`) — see that module's docstring
for why (`CSRFMiddleware` double-submit-checks the moment a `csrf_token`
cookie exists on the request, and this test does GET-then-mutate on the
same client).

Runs against real Postgres/RLS (`app_client`/`tenant_a`/`tenant_b` fixtures,
`tests/conftest.py`) — SQLite-level wiring proofs (no RLS needed to observe
them) live in `tests/unit/test_web_login.py` and `tests/unit/test_branding.py`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import client_for, provision_owner

PASSWORD = "correct horse battery staple"


def _web_login(client: TestClient, email: str) -> str:
    login_page = client.get("/admin/login")
    assert login_page.status_code == 200
    csrf_token = login_page.cookies.get("csrf_token")
    assert csrf_token, "CSRFMiddleware did not set a csrf_token cookie on the login GET"

    login_resp = client.post(
        "/admin/login",
        data={"username": email, "password": PASSWORD},
        headers={"x-csrf-token": csrf_token},
        follow_redirects=False,
    )
    assert login_resp.status_code == 302, login_resp.text
    assert "access_token" in login_resp.cookies
    return csrf_token


def test_saved_branding_reflects_on_dashboard_and_own_login_but_not_other_tenant(
    app_client: TestClient, admin_session: Session, tenant_a, tenant_b
) -> None:
    from app.core.branding import get_brand

    # -----------------------------------------------------------------
    # Tenant A: provision the admin (registration no longer grants admin,
    # Task 2), web login, save branding via the REAL friendly editor form
    # (the only route this app has for writing ui_branding).
    # -----------------------------------------------------------------
    a = client_for(app_client, tenant_a.slug)
    admin_email_a = "branding-admin-a@tenant-a.example.com"
    provision_owner(admin_session, tenant_a, admin_email_a)
    csrf_a = _web_login(a, admin_email_a)

    branding_resp = a.post(
        "/admin/settings/branding",
        data={
            "name": "Acme A Brand",
            "tagline": "",
            "logo_url": "",
            "primary_color": "#112233",
            "accent_color": "",
            "custom_css": "",
        },
        headers={"x-csrf-token": csrf_a},
        follow_redirects=False,
    )
    assert branding_resp.status_code == 302, branding_resp.text

    # -----------------------------------------------------------------
    # Portal-wide (F4): the dashboard/sidebar — NOT the branding editor
    # itself — now shows the saved name. Before Task 4, `load_branding` had
    # exactly one caller (the editor's own preview) and this would still
    # show the static brand.
    # -----------------------------------------------------------------
    dashboard_resp = a.get("/admin")
    assert dashboard_resp.status_code == 200
    assert "Acme A Brand" in dashboard_resp.text

    # -----------------------------------------------------------------
    # Tenant A's own (pre-auth) login page is branded too — a FRESH client
    # (no cookies yet) hitting tenant A's host sees tenant A's saved name
    # before ever logging in.
    # -----------------------------------------------------------------
    fresh_a = client_for(TestClient(app_client.app), tenant_a.slug)
    login_page_a = fresh_a.get("/admin/login")
    assert login_page_a.status_code == 200
    assert "Acme A Brand" in login_page_a.text

    # -----------------------------------------------------------------
    # Tenant B never saved branding — its login page shows the
    # deployment-static brand, and NEVER tenant A's saved name. A fresh
    # client that ONLY does this one safe GET (no mutation afterwards on
    # this same client — see module docstring re: CSRF cookie ordering).
    # -----------------------------------------------------------------
    login_check_b = client_for(TestClient(app_client.app), tenant_b.slug)
    login_page_b = login_check_b.get("/admin/login")
    assert login_page_b.status_code == 200
    assert "Acme A Brand" not in login_page_b.text
    assert get_brand()["name"] in login_page_b.text

    # -----------------------------------------------------------------
    # Tenant B's dashboard (once it has its own admin) is likewise
    # unaffected by tenant A's branding write. The admin is provisioned
    # via the admin engine (no HTTP call), so this client's first request
    # is still the `_web_login` GET (same CSRF-ordering convention as
    # `tests/test_admin_portal_e2e.py`'s tenant B section).
    # -----------------------------------------------------------------
    fresh_b = client_for(TestClient(app_client.app), tenant_b.slug)
    admin_email_b = "branding-admin-b@tenant-b.example.org"
    provision_owner(admin_session, tenant_b, admin_email_b)
    _web_login(fresh_b, admin_email_b)
    dashboard_resp_b = fresh_b.get("/admin")
    assert dashboard_resp_b.status_code == 200
    assert "Acme A Brand" not in dashboard_resp_b.text
