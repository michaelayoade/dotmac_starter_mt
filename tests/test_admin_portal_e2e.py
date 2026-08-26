"""Task 8, deliverable 4: the phase's PROOF canary — a full admin-portal
clickthrough over real Postgres/RLS, driven entirely through the HTTP
surface a browser would use (cookies + HTML forms), not the JSON API.

Provision an admin on tenant A (`provision_owner`, `tests/conftest.py` —
registration no longer grants admin, Task 2), web (cookie) login, dashboard
200, create a person party via the web form, define a custom field via the
web form, set its value via the values panel, list settings — then a FRESH
login on tenant B's host sees NONE of it, and logout kills the cookie (a
subsequent guarded GET redirects to login).

CSRF bridge (shared by every portal canary in this directory).
`fix(security): CSRF by declared transport` moved validation out of
`CSRFMiddleware` and into a `require_csrf` dependency that
`dotmac_kernel.web_runtime` attaches to EVERY composed browser-surface
route — so an unsafe browser request is checked whether or not it happened
to carry a cookie, and the pre-auth `POST /admin/login` is protected too
(it previously was not). Tokens are HMAC-signed and SESSION-BOUND: the
signature covers the browser session cookies present when the token was
issued, so the token handed out by a pre-auth `GET /admin/login` stops
being valid the instant login sets `access_token`.

`csrf_proof` and `web_login` below replicate exactly what a browser does,
and the other canaries (`test_auth_email_authority`,
`test_branding_portal_e2e`, `test_conflict_rls_context`,
`test_web_auth_isolation`) import them from here rather than each keeping a
private copy that can drift:

  * the login page's native `<form method="post">` carries its proof in the
    hidden `csrf_token` field (`templates/auth/login.html`), so `web_login`
    submits that transport — the one a browser with no JS would use;
  * every mutation afterwards is `hx-post`/`hx-delete`, whose proof travels
    as the `X-CSRF-Token` header copied off the cookie by
    `static/js/csrf.js` — so the tests send that header;
  * a token is re-issued by any safe GET on a composed browser route, which
    is how a browser silently picks up the rotated cookie after login.
    Call `csrf_proof` again after ANYTHING that changes `access_token`.

Cookie name is `csrf_token` here and `__Host-csrf_token` in production
(`dotmac_kernel.middleware.csrf`); these clients are non-production, and the
constants are imported rather than spelled out so a rename cannot rot this
silently.
"""

from __future__ import annotations

import re

from dotmac_kernel.middleware.csrf import CSRF_COOKIE, CSRF_FORM_FIELD, CSRF_HEADER
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import client_for, provision_owner

PASSWORD = "correct horse battery staple"

#: The hidden field a native (JS-free) form submit sends as its proof.
_HIDDEN_CSRF_FIELD = re.compile(rf'name="{CSRF_FORM_FIELD}"\s+value="([^"]+)"')


def csrf_proof(client: TestClient, path: str = "/admin/login") -> str:
    """Return a CSRF token bound to this client's CURRENT session cookies.

    A safe GET on a composed browser route is what makes `CSRFMiddleware`
    issue (or rotate) the signed cookie — the same thing a browser gets for
    free by navigating. Because the token is session-bound, this MUST be
    re-called after login/logout: the previously held token is no longer
    valid once the `access_token` cookie changes.
    """
    page = client.get(path)
    assert page.status_code == 200, page.text
    token = client.cookies.get(CSRF_COOKIE)
    assert token, f"no {CSRF_COOKIE} cookie after a safe GET on {path}"
    return token


def web_login(
    client: TestClient,
    email: str,
    password: str = PASSWORD,
    *,
    landing: str = "/admin",
) -> str:
    """Cookie login through the real form; returns a POST-LOGIN csrf token.

    `POST /admin/login` is CSRF-protected now, so the proof rendered into
    the login page's hidden field is submitted with the credentials, as a
    browser without JS would. The returned token is re-issued AFTER the
    session cookie exists, and is what every subsequent mutating request on
    this client sends as the `X-CSRF-Token` header.
    """
    login_page = client.get("/admin/login")
    assert login_page.status_code == 200, login_page.text
    hidden = _HIDDEN_CSRF_FIELD.search(login_page.text)
    assert hidden, "the login form rendered no hidden csrf_token proof"

    login_resp = client.post(
        "/admin/login",
        data={
            "username": email,
            "password": password,
            CSRF_FORM_FIELD: hidden.group(1),
        },
        follow_redirects=False,
    )
    assert login_resp.status_code == 302, login_resp.text
    assert login_resp.headers["location"] == landing, login_resp.headers["location"]
    assert "access_token" in login_resp.cookies
    # Session-bound: the pre-auth token above died with this login.
    return csrf_proof(client)


def test_admin_portal_end_to_end_canary(
    app_client: TestClient, admin_session: Session, tenant_a, tenant_b
) -> None:
    # -----------------------------------------------------------------
    # Tenant A: provision the admin, web login, dashboard.
    # -----------------------------------------------------------------
    a = client_for(app_client, tenant_a.slug)
    admin_email_a = "portal-admin-a@tenant-a.example.com"
    provision_owner(admin_session, tenant_a, admin_email_a)
    csrf_a = web_login(a, admin_email_a)

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
        headers={CSRF_HEADER: csrf_a},
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
            # F6: the values panel's edit form now only renders inputs for
            # show_in_form fields (`web.py::party_values_panel`'s
            # `visible_in="form"` query) — a raw form POST that omits an
            # unchecked checkbox (as this one deliberately does to simulate
            # a real browser submit) means `show_in_form` would otherwise
            # default to False (see `_raw_from_form`), and the assertion
            # below (`name="nickname"` present on the panel) would fail.
            "show_in_form": "true",
            "show_in_detail": "true",
        },
        headers={CSRF_HEADER: csrf_a},
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
        headers={CSRF_HEADER: csrf_a},
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
    provision_owner(admin_session, tenant_b, admin_email_b)
    web_login(b, admin_email_b)

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

    # F7 THE assertion: GET /admin/logout no longer exists (the exact
    # forced-logout CSRF vector) — FastAPI 405s a known path/wrong method.
    get_logout_resp = a.get("/admin/logout", follow_redirects=False)
    assert get_logout_resp.status_code == 405

    # F7 THE assertion: POST /admin/logout WITHOUT the CSRF header 403s —
    # this is the actual proof that logout is no longer forgeable (a
    # third-party page can trigger a cookie-carrying POST, e.g. via an
    # auto-submitting form, but it cannot read this tenant's `csrf_token`
    # cookie to mint a matching header). `a`'s cookie jar already carries
    # both `access_token` and a LIVE, session-bound `csrf_token` (re-issued
    # by `web_login`'s post-login safe GET), so this genuinely exercises a
    # missing proof on a fully-cookied session, not "no cookies at all".
    forged_logout_resp = a.post("/admin/logout", follow_redirects=False)
    assert forged_logout_resp.status_code == 403
    assert forged_logout_resp.json()["code"] == "csrf_failed"

    # The session must still be very much alive after the forged attempt
    # above — a blocked logout must not have revoked anything.
    still_logged_in_resp = a.get("/admin", follow_redirects=False)
    assert still_logged_in_resp.status_code == 200

    # F7: logout is now POST (was a CSRF-exempt GET) — reuse `csrf_a`,
    # which `web_login` issued AFTER the session cookie existed (a
    # pre-auth token would no longer verify), same bridge as every other
    # mutation in this canary.
    logout_resp = a.post(
        "/admin/logout", headers={CSRF_HEADER: csrf_a}, follow_redirects=False
    )
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
