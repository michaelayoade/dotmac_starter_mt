"""Task 8, deliverable 4: the phase's PROOF canary — a full admin-portal
clickthrough over real Postgres/RLS, driven entirely through the HTTP
surface a browser would use (cookies + HTML forms), not the JSON API.

Register an admin via the API on tenant A, web (cookie) login, dashboard
200, create a person party via the web form, define a custom field via the
web form, set its value via the values panel, list settings — then a FRESH
login on tenant B's host sees NONE of it, and logout kills the cookie (a
subsequent guarded GET redirects to login).

CSRF cookie-ordering convention (`test_settings_isolation.py`'s docstring,
`test_custom_fields_isolation.py`'s inline comment on the same subject):
`CSRFMiddleware` (`app.core.middleware.csrf`) double-submit-checks any
non-safe method the moment a `csrf_token` cookie exists on the request.
Every existing Postgres canary dodges this by doing every mutation on a
client BEFORE that client's first GET. This test cannot do that — the whole
point is dashboard GET (200) THEN mutate via forms — so instead it
replicates the browser-side bridge (`static/js/csrf.js`) directly in the
test client: capture the `csrf_token` cookie value from the very first GET
response (`GET /admin/login`) and attach it as the `x-csrf-token` header on
every subsequent mutating request on that client. The cookie never rotates
once set (`CSRFMiddleware` only sets it when absent from the request), so
one captured value is valid for the whole session.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import client_for

PASSWORD = "correct horse battery staple"


def _register_admin(client: TestClient, email: str) -> None:
    """First registered user of a tenant auto-gets the "admin" role
    (`app.features.auth.service._assign_first_user_admin`) — this doubles as
    "register the tenant's admin", same convention as every other isolation
    canary in this suite.
    """
    resp = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "first_name": "Admin",
            "last_name": "User",
        },
    )
    assert resp.status_code == 201, resp.text


def _web_login(client: TestClient, email: str) -> str:
    """Cookie-based web login, replicating the browser's CSRF header bridge.

    `GET /admin/login` first — a safe method, so `CSRFMiddleware` sets the
    `csrf_token` cookie on the response with no check performed. The
    following `POST /admin/login` DOES carry cookies by then (the just-set
    `csrf_token`), so it must present a matching `x-csrf-token` header or
    the double-submit check 403s. Returns the captured csrf token so the
    caller can reuse it for every further mutating request on this same
    client (see module docstring: the cookie never rotates once set).
    """
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


def test_admin_portal_end_to_end_canary(
    app_client: TestClient, tenant_a, tenant_b
) -> None:
    # -----------------------------------------------------------------
    # Tenant A: register, web login, dashboard.
    # -----------------------------------------------------------------
    a = client_for(app_client, tenant_a.slug)
    admin_email_a = "portal-admin-a@tenant-a.example.com"
    _register_admin(a, admin_email_a)
    csrf_a = _web_login(a, admin_email_a)

    dashboard_resp = a.get("/admin")
    assert dashboard_resp.status_code == 200
    assert "Dashboard" in dashboard_resp.text

    # -----------------------------------------------------------------
    # Create a person party via the web form (hx-post form, CSRF header
    # replicated by hand — see module docstring).
    # -----------------------------------------------------------------
    create_resp = a.post(
        "/admin/parties/people",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@tenant-a.example.com",
        },
        headers={"x-csrf-token": csrf_a},
        follow_redirects=False,
    )
    assert create_resp.status_code == 302, create_resp.text
    party_url = create_resp.headers["location"]
    party_id = party_url.rsplit("/", 1)[-1]

    detail_resp = a.get(party_url)
    assert detail_resp.status_code == 200
    assert "Ada Lovelace" in detail_resp.text

    # -----------------------------------------------------------------
    # Define a custom field via the web form.
    # -----------------------------------------------------------------
    field_resp = a.post(
        "/admin/custom-fields",
        data={
            "entity_type": "party",
            "field_code": "nickname",
            "field_name": "Nickname",
            "field_type": "TEXT",
            "display_order": "0",
        },
        headers={"x-csrf-token": csrf_a},
        follow_redirects=False,
    )
    assert field_resp.status_code == 302, field_resp.text

    # -----------------------------------------------------------------
    # Set its value via the values panel.
    # -----------------------------------------------------------------
    panel_get_resp = a.get(f"/admin/custom-fields/party/{party_id}/values-panel")
    assert panel_get_resp.status_code == 200
    assert 'name="nickname"' in panel_get_resp.text

    panel_post_resp = a.post(
        f"/admin/custom-fields/party/{party_id}/values-panel",
        data={"nickname": "Ada the Enchantress"},
        headers={"x-csrf-token": csrf_a},
    )
    assert panel_post_resp.status_code == 200
    assert "Ada the Enchantress" in panel_post_resp.text

    # -----------------------------------------------------------------
    # List settings.
    # -----------------------------------------------------------------
    settings_resp = a.get("/admin/settings")
    assert settings_resp.status_code == 200
    assert "max_per_entity" in settings_resp.text

    # -----------------------------------------------------------------
    # Tenant B: a FRESH login (own TestClient -> own cookie jar) sees NONE
    # of the above — new party, new custom field definition, new value.
    # -----------------------------------------------------------------
    b = client_for(TestClient(app_client.app), tenant_b.slug)
    admin_email_b = "portal-admin-b@tenant-b.example.org"
    _register_admin(b, admin_email_b)
    _web_login(b, admin_email_b)

    b_parties_resp = b.get("/admin/parties")
    assert b_parties_resp.status_code == 200
    assert "Ada Lovelace" not in b_parties_resp.text

    b_fields_resp = b.get("/admin/custom-fields", params={"entity_type": "party"})
    assert b_fields_resp.status_code == 200
    assert "Nickname" not in b_fields_resp.text
    assert "nickname" not in b_fields_resp.text

    # -----------------------------------------------------------------
    # Logout kills the cookie: the client-side cookie is cleared AND the
    # server-side session is revoked (the two are different mechanisms —
    # both are proven, same rigor as
    # tests/unit/test_web_login.py::test_logout_clears_cookie_and_redirects).
    # -----------------------------------------------------------------
    access_token = a.cookies.get("access_token")
    assert access_token

    logout_resp = a.get("/admin/logout", follow_redirects=False)
    assert logout_resp.status_code == 302

    # (1) The client's own cookie jar no longer sends a live access_token —
    # a plain guarded GET redirects to login.
    after_logout_resp = a.get("/admin", follow_redirects=False)
    assert after_logout_resp.status_code == 302
    assert after_logout_resp.headers["location"].startswith("/admin/login")

    # (2) Even if a stale client somehow still held the OLD token value, the
    # server-side AuthSession is revoked — reusing it explicitly must also
    # redirect, not merely "the cookie jar happened to forget it".
    stale_cookie_resp = a.get(
        "/admin", cookies={"access_token": access_token}, follow_redirects=False
    )
    assert stale_cookie_resp.status_code == 302
    assert stale_cookie_resp.headers["location"].startswith("/admin/login")
