"""F3 canary — conflict paths must preserve RLS tenant context.

Finding F3 (post-merge review, `docs/superpowers/plans/2026-07-18-phase2b1-
sot-composability.md` Task 2): every conflict site in `app/features/*`
handled an expected `IntegrityError` with a bare `db.rollback()` before
translating it to a `ConflictError`. `db.rollback()` rolls back the WHOLE
transaction `get_db` (`app.core.db`) opened for this request — including
the `SET LOCAL app.current_tenant` it issued for RLS. Any DB access the web
handler performs AFTER catching the `ConflictError` (re-rendering a form
that reads an already-loaded ORM object's attributes, re-querying a list of
recent grants, ...) then runs with no tenant context, and FORCE ROW LEVEL
SECURITY fails closed: either an outright error re-loading an expired
attribute (`ObjectDeletedError` -> unhandled 500) or a silently *empty*
result set, depending on the exact access pattern.

The fix (`app.core.db.conflict_savepoint`) wraps each conflict-prone
mutation in a SAVEPOINT (`Session.begin_nested()`); on `IntegrityError` only
the SAVEPOINT is rolled back — the outer transaction, and the `SET LOCAL`
it carries, survive intact.

Both canaries below assert the FIXED contract (200 re-render, form error
message present, AND real tenant data still visible in that same response).
Run against the pre-fix code, they fail — see `.superpowers/sdd/
task-2-report.md` for the captured RED output/tracebacks.

Requires a real Postgres (FORCE RLS is the whole point — SQLite has none;
`make test-db-up` + `TEST_DATABASE_URL`/`TEST_MIGRATION_DATABASE_URL`, same
as every other canary in this directory).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Role
from tests.conftest import client_for, provision_owner

PASSWORD = "correct horse battery staple"


def _web_login(client: TestClient, email: str) -> str:
    """Cookie-based web login, replicating the browser's CSRF header bridge
    (see `tests/test_admin_portal_e2e.py`'s module docstring for the full
    rationale — copied here rather than imported since none of the existing
    canaries export it as a shared helper)."""
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


def test_duplicate_email_edit_re_renders_with_tenant_context_intact(
    app_client: TestClient, admin_session: Session, tenant_a
) -> None:
    """(a) Web edit-to-duplicate-email conflict — parties.

    Person A (email `dup-target@...`) and Person B (a different email) are
    created via the web forms; editing B's email to collide with A's must
    raise the same `ConflictError("Email already registered")` the service
    layer always raised, but the web handler must re-render the edit form
    at 200 — with Party B's own data still populated, proving `party.
    person_profile.first_name` (etc.) could still be loaded/re-loaded under
    RLS after the conflict.
    """
    a = client_for(app_client, tenant_a.slug)
    admin_email = "conflict-admin@tenant-a.example.com"
    # Provisioned, not registered — registration no longer grants the
    # "admin" role the web login below requires (Task 2).
    provision_owner(admin_session, tenant_a, admin_email)
    csrf = _web_login(a, admin_email)

    create_a = a.post(
        "/admin/parties/people",
        data={
            "first_name": "Party",
            "last_name": "Alpha",
            "email": "dup-target@tenant-a.example.com",
        },
        headers={"x-csrf-token": csrf},
        follow_redirects=False,
    )
    assert create_a.status_code == 302, create_a.text

    create_b = a.post(
        "/admin/parties/people",
        data={
            "first_name": "Party",
            "last_name": "Beta",
            "email": "party-b@tenant-a.example.com",
        },
        headers={"x-csrf-token": csrf},
        follow_redirects=False,
    )
    assert create_b.status_code == 302, create_b.text
    party_b_url = create_b.headers["location"]
    party_b_id = party_b_url.rsplit("/", 1)[-1]

    # Edit B's email to collide with A's -> IntegrityError -> ConflictError.
    edit_resp = a.post(
        f"/admin/parties/{party_b_id}/edit",
        data={
            "first_name": "Party",
            "last_name": "Beta",
            "email": "dup-target@tenant-a.example.com",
        },
        headers={"x-csrf-token": csrf},
        follow_redirects=False,
    )

    assert edit_resp.status_code == 200, (
        "expected a 200 re-render with a form error (RLS tenant context "
        f"must survive the conflict), got {edit_resp.status_code}: "
        f"{edit_resp.text[:2000]}"
    )
    assert "Email already registered" in edit_resp.text
    # Party B's real data rendered in the SAME response — proves the
    # edit.html template's `party.person_profile.first_name`/`last_name`
    # attribute access succeeded post-conflict, under FORCE RLS, with the
    # request's tenant context intact.
    assert "Beta" in edit_resp.text

    # Sanity: Party B is not corrupted at the DB level either.
    detail_resp = a.get(party_b_url)
    assert detail_resp.status_code == 200
    assert "Beta" in detail_resp.text


def test_duplicate_role_grant_re_renders_with_grants_list_populated(
    app_client: TestClient, tenant_a, admin_session: Session
) -> None:
    """(b) Web duplicate role-grant conflict — rbac.

    Granting the same role to the same party twice must raise
    `ConflictError("Role already assigned")`, and the web handler must
    re-render `/admin/role-grants` at 200 with that error AND the recent-
    grants list still populated (non-empty) — proving `list_recent_grants`
    (a fresh query, not a re-load of an already-loaded object) still saw
    tenant data after the conflict, i.e. RLS tenant context was not wiped.
    """
    a = client_for(app_client, tenant_a.slug)
    admin_email = "conflict-rbac-admin@tenant-a.example.com"
    provision_owner(admin_session, tenant_a, admin_email)
    csrf = _web_login(a, admin_email)

    create_resp = a.post(
        "/admin/parties/people",
        data={
            "first_name": "Grant",
            "last_name": "Target",
            "email": "grant-target@tenant-a.example.com",
        },
        headers={"x-csrf-token": csrf},
        follow_redirects=False,
    )
    assert create_resp.status_code == 302, create_resp.text
    party_id = create_resp.headers["location"].rsplit("/", 1)[-1]

    role_resp = a.post(
        "/admin/roles",
        data={"slug": "manager", "name": "Manager"},
        headers={"x-csrf-token": csrf},
        follow_redirects=False,
    )
    assert role_resp.status_code == 302, role_resp.text

    role = admin_session.scalars(
        select(Role).where(Role.tenant_id == tenant_a.id, Role.slug == "manager")
    ).first()
    assert role is not None
    role_id = str(role.id)

    first_grant = a.post(
        "/admin/role-grants",
        data={"party_id": party_id, "role_id": role_id},
        headers={"x-csrf-token": csrf},
        follow_redirects=False,
    )
    assert first_grant.status_code == 302, first_grant.text

    # Grant the SAME role to the SAME party again -> IntegrityError ->
    # ConflictError("Role already assigned").
    dup_grant = a.post(
        "/admin/role-grants",
        data={"party_id": party_id, "role_id": role_id},
        headers={"x-csrf-token": csrf},
        follow_redirects=False,
    )

    assert dup_grant.status_code == 200, (
        "expected a 200 re-render with a form error (RLS tenant context "
        f"must survive the conflict), got {dup_grant.status_code}: "
        f"{dup_grant.text[:2000]}"
    )
    assert "Role already assigned" in dup_grant.text
    # The recent-grants list (a FRESH query, run after the conflict) must
    # still show tenant data — proves `list_recent_grants`/`list_roles`/
    # `list_grantable_parties` did not run under a context-less session.
    assert "Manager" in dup_grant.text
    assert "Grant Target" in dup_grant.text
