"""Phase 2b.1 Task 3 canaries (F2): `Party.email` is the single email
authority — the credential table's own former email column no longer
exists, `login()` resolves the party by
`(tenant, normalize_email(email), party_type=person)` and then finds the
credential row by `party_id`.

Drives the SAME cross-tenant admin-portal provisioning/login pattern as
`tests/test_admin_portal_e2e.py` (provision the tenant's admin via
`provision_owner` — registration no longer grants admin, Task 2 — then
web-login via the cookie form, CSRF header bridge captured off the first
`GET /admin/login`), then edits the admin's OWN party email through
`POST /admin/parties/{party_id}/edit` (the only writer of `Party.email`
post-registration — there is no JSON `PATCH /parties/{id}` route yet, see
`docs/superpowers/phase2-backlog.md`).

RED capture (pre-fix, the now-removed credential-local email column still
the login source): logging in with the NEW post-edit email 401s (that
column still has the OLD email) and logging in with the OLD email still
succeeds — the exact drift finding F2 describes. Post-fix (this task): the
reverse — NEW email succeeds, OLD email 401s, in a single atomic
`Party.email` column.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Party
from tests.conftest import client_for, provision_owner

PASSWORD = "correct horse battery staple"


def _provision_admin(admin_session: Session, tenant, email: str) -> str:
    """Provision the tenant's admin (`provision_owner`, `tests/conftest.py` —
    registration no longer grants admin, Task 2) and return the new party's
    id — needed to drive the party-edit web form below.
    """
    provision_owner(admin_session, tenant, email)
    party_id = admin_session.scalars(
        select(Party.id).where(Party.tenant_id == tenant.id, Party.email == email)
    ).one()
    return str(party_id)


def _web_login(client: TestClient, email: str) -> str:
    """Real cookie-based web login — `GET /admin/login` (captures the
    `csrf_token` cookie CSRFMiddleware sets on a safe method) THEN
    `POST /admin/login` with the credentials, exactly like
    `test_admin_portal_e2e.py::_web_login`. Returns the csrf token for reuse
    on every further mutating request on this same client.

    An earlier version of this test only did the GET and never actually
    authenticated — every subsequent `/admin/parties/{id}/edit` POST 302'd
    to `/admin/login?next=...` (an UNauthenticated redirect, not the edit
    succeeding) and silently never touched `Party.email` at all. Caught by
    inspecting the redirect `Location` header instead of trusting a bare
    302 status code — this helper now actually logs in, and callers should
    keep asserting `location` points at the intended page, not just that a
    3xx came back.
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


def _json_login(
    client: TestClient, email: str, *, csrf_token: str | None = None
) -> int:
    """`POST /auth/login` (bearer JSON API — simplest way to probe
    success/failure without threading portal cookies) — returns the status
    code so callers can assert 200 or 401 without unpacking the body.

    `csrf_token`: `CSRFMiddleware` double-submit-checks ANY non-safe method
    the moment the client carries ANY cookies at all (see
    `app.core.middleware.csrf`), not just requests to `/admin/*` — so once a
    test client has done a `GET /admin/login` (picking up the `csrf_token`
    cookie) on its way to editing a party, every subsequent POST on that
    SAME client — including this plain JSON API call — needs the header too,
    or it 403s before ever reaching the login logic. Pass the captured token
    once the client has one; omit it for calls made before any cookie
    exists.
    """
    headers = {"x-csrf-token": csrf_token} if csrf_token else None
    resp = client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}, headers=headers
    )
    return resp.status_code


def _edit_party_email(
    client: TestClient, csrf_token: str, party_id: str, *, new_email: str | None
) -> None:
    """`POST /admin/parties/{party_id}/edit` — the only writer of
    `Party.email` after registration. `new_email=None` submits the form with
    a blank email field (`PersonPartyUpdate(email=None)` — the NULL-email
    canary).
    """
    resp = client.post(
        f"/admin/parties/{party_id}/edit",
        data={
            "first_name": "Admin",
            "last_name": "User",
            "email": new_email or "",
        },
        headers={"x-csrf-token": csrf_token},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    # Must land on the party detail page, not `/admin/login?next=...` — an
    # unauthenticated redirect is ALSO a 302 and would otherwise silently
    # pass as "the edit succeeded" while never having touched `Party.email`.
    assert resp.headers["location"] == f"/admin/parties/{party_id}", resp.headers[
        "location"
    ]


def test_login_after_portal_email_change_uses_new_email_old_email_401s(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    a = client_for(app_client, tenant_a.slug)
    old_email = "authority-old@tenant-a.example.com"
    new_email = "authority-new@tenant-a.example.com"
    party_id = _provision_admin(admin_session, tenant_a, old_email)

    # Old email logs in fine before the edit.
    assert _json_login(a, old_email) == 200

    csrf = _web_login(a, old_email)
    _edit_party_email(a, csrf, party_id, new_email=new_email)

    # NEW email now logs in ... (csrf_token passed from here on — this same
    # client picked up the csrf_token cookie via `_web_login` above, so
    # every subsequent POST needs the header, see `_json_login`'s docstring)
    assert _json_login(a, new_email, csrf_token=csrf) == 200
    # ... and the OLD email no longer does — no drift between Party.email
    # (what the portal shows/edited) and the login identity.
    assert _json_login(a, old_email, csrf_token=csrf) == 401


def test_cross_tenant_same_email_login_unaffected(
    app_client: TestClient, admin_session: Session, tenant_a, tenant_b
) -> None:
    """Two different tenants can each hold the SAME email address (the
    parties uniqueness index is per-tenant, `(tenant_id, lower(email))`) —
    each tenant's login only ever sees its own party, no cross-tenant leak
    now that login resolves via a tenant-scoped Party query.
    """
    shared_email = "shared-authority@example.com"
    a = client_for(app_client, tenant_a.slug)
    _provision_admin(admin_session, tenant_a, shared_email)

    b = client_for(TestClient(app_client.app), tenant_b.slug)
    _provision_admin(admin_session, tenant_b, shared_email)

    assert _json_login(a, shared_email) == 200
    assert _json_login(b, shared_email) == 200


def test_nulled_party_email_disables_login(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    """Nulling a person party's email via the portal now disables login for
    that party outright (the login query is `Party.email ==
    normalize_email(email)` — a NULL row simply never matches any query
    string). Intended/documented behavior (see `login()`'s docstring and
    `docs/ARCHITECTURE.md`), not a bug: there is no other identity to log in
    with once the single email column is cleared.
    """
    a = client_for(app_client, tenant_a.slug)
    email = "authority-null@tenant-a.example.com"
    party_id = _provision_admin(admin_session, tenant_a, email)

    assert _json_login(a, email) == 200

    csrf = _web_login(a, email)
    _edit_party_email(a, csrf, party_id, new_email=None)

    assert _json_login(a, email, csrf_token=csrf) == 401
