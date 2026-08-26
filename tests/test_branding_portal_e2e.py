"""Task 4 (F4) PROOF canary: saving a tenant's branding via the real web
form changes what OTHER portal pages show — the dashboard sidebar and the
tenant's own (pre-auth) login page — not just the branding editor's own
preview, and never leaks into a different tenant's login page.

Uses the shared CSRF bridge from `tests/test_admin_portal_e2e.py`
(`web_login`): the login form submits its hidden `csrf_token` proof, and
the token that helper returns — re-issued AFTER the session cookie exists,
since signed tokens are session-bound — is replayed as the `X-CSRF-Token`
header on every mutating `POST`, exactly as `static/js/csrf.js` does for
htmx. See that module's docstring for the full contract.

Runs against real Postgres/RLS (`app_client`/`tenant_a`/`tenant_b` fixtures,
`tests/conftest.py`) — SQLite-level wiring proofs (no RLS needed to observe
them) live in `tests/unit/test_web_login.py` and `tests/unit/test_branding.py`.
"""

from __future__ import annotations

from dotmac_kernel.middleware.csrf import CSRF_HEADER
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import client_for, provision_owner
from tests.test_admin_portal_e2e import web_login


def test_saved_branding_reflects_on_dashboard_and_own_login_but_not_other_tenant(
    app_client: TestClient, admin_session: Session, tenant_a, tenant_b
) -> None:
    from dotmac_kernel.branding import get_brand

    # -----------------------------------------------------------------
    # Tenant A: provision the admin (registration no longer grants admin,
    # Task 2), web login, save branding via the REAL friendly editor form
    # (the only route this app has for writing ui_branding).
    # -----------------------------------------------------------------
    a = client_for(app_client, tenant_a.slug)
    admin_email_a = "branding-admin-a@tenant-a.example.com"
    provision_owner(admin_session, tenant_a, admin_email_a)
    csrf_a = web_login(a, admin_email_a)

    branding_resp = a.post(
        "/admin/settings/branding",
        data={
            "name": "Acme A Brand",
            "tagline": "",
            "logo_url": "",
            "primary_color": "#112233",
            "accent_color": "",
        },
        headers={CSRF_HEADER: csrf_a},
        follow_redirects=False,
    )
    assert branding_resp.status_code == 302, branding_resp.text

    # Train 3: the same resolved brand is projected into token CSS on a public
    # tenant-scoped route, so it is available to the pre-auth login page too.
    css_a = a.get("/branding/theme.css")
    assert css_a.status_code == 200
    assert css_a.headers["content-type"].startswith("text/css")
    assert css_a.headers["cache-control"] == "private, no-store"
    assert css_a.headers["x-dotmac-brand-projection"] == "generated"
    assert "#112233" in css_a.text
    assert "--dmui-color-brand-" in css_a.text
    assert "--dmui-color-brand-500-rgb:" in css_a.text

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
    # client doing one safe GET — no mutation on it, so it needs no proof.
    # -----------------------------------------------------------------
    login_check_b = client_for(TestClient(app_client.app), tenant_b.slug)
    login_page_b = login_check_b.get("/admin/login")
    assert login_page_b.status_code == 200
    assert "Acme A Brand" not in login_page_b.text
    assert get_brand()["name"] in login_page_b.text

    css_b = login_check_b.get("/branding/theme.css")
    assert css_b.status_code == 200
    assert "#112233" not in css_b.text
    assert css_b.text != css_a.text

    # -----------------------------------------------------------------
    # Tenant B's dashboard (once it has its own admin) is likewise
    # unaffected by tenant A's branding write. The admin is provisioned
    # via the admin engine (no HTTP call), so this client's first request
    # is still `web_login`'s own `GET /admin/login` — which is what mints
    # this client's first CSRF proof.
    # -----------------------------------------------------------------
    fresh_b = client_for(TestClient(app_client.app), tenant_b.slug)
    admin_email_b = "branding-admin-b@tenant-b.example.org"
    provision_owner(admin_session, tenant_b, admin_email_b)
    web_login(fresh_b, admin_email_b)
    dashboard_resp_b = fresh_b.get("/admin")
    assert dashboard_resp_b.status_code == 200
    assert "Acme A Brand" not in dashboard_resp_b.text
