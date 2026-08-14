"""The permission decision is ONE decision, however the actor was authenticated.

`dotmac_kernel.deps.authorize_party` is to authorization what
`authenticate_request` is to authentication: the single seam both the bearer API
and any cookie-rendered surface reach. `permission_guard` is the route-level
adapter over it, parameterised by the only two things the surfaces actually
disagree about — how the actor is proved, and what a refusal looks like.

These tests exist because of a concrete gap. The Workspace assembly
(`dotmac_workspace`, ADR-0021's third plane) authenticates with its OWN session
cookie, `dmws_session`, deliberately not the `access_token` every product portal
reads. Its adoption blocker B1 recorded that neither existing seam fit:
`require_permission` was welded to the bearer header and answered a browser with
a bare 401, while `require_web_auth` read the wrong cookie and hardcoded the
`"admin"` role instead of consulting a declared permission. The only remaining
move was for the assembly to hand-roll the role query — which is how a plane
falls behind a kernel security fix, the failure ADR-0015 recorded against
academy.

So the test that matters here is the PARITY one: a party carrying a foreign
cookie name reaches exactly the decision the bearer surface reaches, on the same
declared code, without the kernel ever learning that cookie's name.

App-builder pattern from `tests/unit/test_permissions.py`.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from dotmac_kernel import (
    FeatureManifest,
    PermissionCatalogue,
    PermissionSpec,
    UndeclaredPermissionError,
    install_permissions,
)
from dotmac_kernel.deps import (
    authenticate_request,
    authorize_party,
    get_db,
    permission_guard,
    require_permission,
)
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.models import (
    Party,
    PartyPerson,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
    UserCredential,
)
from dotmac_kernel.permissions import PERMISSION_CODE_ATTR
from dotmac_kernel.security import hash_password
from dotmac_kernel.web_deps import WebAuthRedirect
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.features.auth.web import router as auth_web_router

PASSWORD = "correct horse battery staple"

# A cookie name the kernel does not know and must never need to know. This is
# `dotmac_workspace`'s real one — the whole point of the seam is that the
# assembly owns this string.
FOREIGN_COOKIE = "dmws_session"
FOREIGN_LOGIN = "/workspace/login"


def _workspace_auth(request: Request, db: Session = Depends(get_db)) -> Party:
    """Stands in for `dotmac_workspace.launcher.guard.require_workspace_auth`.

    Reads ITS OWN cookie, then defers to the kernel's one token/session seam.
    It re-implements no validation — that is the property B1's sibling test
    (`test_launcher_is_not_authorization.py`) pins in the Workspace repo.
    """
    token = request.cookies.get(FOREIGN_COOKIE)
    if token is None:
        raise WebAuthRedirect(request.url.path, login_path=FOREIGN_LOGIN)
    party = authenticate_request(request, db, token=token)
    if party is None:
        raise WebAuthRedirect(request.url.path, login_path=FOREIGN_LOGIN)
    return party


def _permission_denied(request: Request) -> Exception:
    """The portal's AUTHORIZATION refusal.

    Deliberately a 403 and NOT a redirect to login. By the time this runs the
    actor is authenticated — `_workspace_auth` succeeded — so telling them to
    sign in is advice they cannot act on: at best a confusing bounce, at worst a
    loop, because the login page finds a valid session and sends them straight
    back. Unauthenticated is the other seam's job, and `_workspace_auth` already
    raises its own redirect before authorization is consulted.
    """
    return HTTPException(status_code=403, detail="Forbidden")


def _m(name: str, *specs: PermissionSpec) -> FeatureManifest:
    return FeatureManifest(name=name, permissions=specs)


def _install_reports_catalogue() -> None:
    """`probe.reports` is held by `auditor` — deliberately NOT by `admin`, so a
    surface that quietly fell back to an admin check would fail rather than
    coincidentally pass."""
    install_permissions(
        PermissionCatalogue.from_manifests(
            [
                _m(
                    "probe",
                    PermissionSpec(code="probe.reports", default_roles=("auditor",)),
                )
            ]
        )
    )


def _build_app(db: Session, tenant: Tenant) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_web_router)

    # The SAME declared code, reached two ways. Nothing else differs.
    @app.get("/probe/api/reports")
    def _api(_: Party = Depends(require_permission("probe.reports"))) -> dict:
        return {"ok": True}

    @app.get("/probe/portal/reports")
    def _portal(
        _: Party = Depends(
            permission_guard(
                "probe.reports",
                authenticated_party=_workspace_auth,
                denied=_permission_denied,
            )
        ),
    ) -> dict:
        return {"ok": True}

    @app.get("/probe/portal/ghost")
    def _ghost(
        _: Party = Depends(
            permission_guard(
                "probe.ghost",
                authenticated_party=_workspace_auth,
                denied=_permission_denied,
            )
        ),
    ) -> dict:
        return {"ok": True}

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return app


@pytest.fixture()
def seam_client(db: Session, tenant_row: Tenant) -> TestClient:
    _install_reports_catalogue()
    return TestClient(_build_app(db, tenant_row), raise_server_exceptions=False)


def _actor_with_role(db: Session, tenant: Tenant, email: str, role_slug: str) -> None:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=email,
        email=email,
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Test", last_name="User"))
    db.add(
        UserCredential(
            tenant_id=tenant.id,
            party_id=party.id,
            password_hash=hash_password(PASSWORD),
        )
    )
    role = Role(tenant_id=tenant.id, slug=role_slug, name=role_slug.title())
    db.add(role)
    db.flush()
    db.add(PartyRoleGrant(tenant_id=tenant.id, party_id=party.id, role_id=role.id))
    db.commit()


def _session_token(client: TestClient, email: str) -> str:
    resp = client.post(
        "/admin/login",
        data={"username": email, "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    return resp.cookies["access_token"]


# ── The parity proof (Workspace blocker B1) ─────────────────────────────────


@pytest.mark.parametrize(
    ("role_slug", "api_status", "portal_status"),
    [
        ("auditor", 200, 200),  # holds the declared default_role
        ("admin", 403, 403),  # does not — admin is not magic here
    ],
)
def test_a_foreign_cookie_reaches_the_same_decision_as_the_bearer_header(
    seam_client: TestClient,
    db: Session,
    tenant_row: Tenant,
    role_slug: str,
    api_status: int,
    portal_status: int,
) -> None:
    """ONE declared code, ONE actor, two authentications — one decision.

    Both surfaces refuse with 403, because both are answering the same
    AUTHORIZATION question about an already-authenticated actor. `denied` exists
    so a portal can render a branded refusal rather than a JSON one — not so it
    can bounce a signed-in user back to login.
    """
    email = f"{role_slug}@example.com"
    _actor_with_role(db, tenant_row, email, role_slug)
    token = _session_token(seam_client, email)

    api = seam_client.get(
        "/probe/api/reports",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert api.status_code == api_status

    seam_client.cookies.clear()
    seam_client.cookies.set(FOREIGN_COOKIE, token)
    portal = seam_client.get("/probe/portal/reports", follow_redirects=False)
    assert portal.status_code == portal_status

    allowed = {api.status_code == 200, portal.status_code == 200}
    assert len(allowed) == 1, (
        "the two surfaces disagreed on the SAME declared code — the permission "
        "decision has forked, which is exactly what one seam exists to prevent"
    )


def test_an_authenticated_but_unauthorized_party_is_refused_not_redirected(
    seam_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    """The correction that matters most here.

    This actor IS signed in — they simply lack the permission. Redirecting them
    to login would be advice they cannot act on, and the login page would find a
    valid session and send them back. The two questions get two answers: "who
    are you?" redirects, "may you?" refuses.
    """
    _actor_with_role(db, tenant_row, "admin@example.com", "admin")
    token = _session_token(seam_client, "admin@example.com")
    seam_client.cookies.clear()
    seam_client.cookies.set(FOREIGN_COOKIE, token)

    resp = seam_client.get("/probe/portal/reports", follow_redirects=False)
    assert resp.status_code == 403
    assert "location" not in resp.headers


def test_an_unauthenticated_foreign_cookie_never_reaches_the_permission_check(
    seam_client: TestClient,
) -> None:
    seam_client.cookies.clear()
    resp = seam_client.get("/probe/portal/reports", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith(f"{FOREIGN_LOGIN}?next=")


def test_the_cookie_surface_fails_closed_on_an_undeclared_code(
    seam_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    """`probe.ghost` is referenced but declared by nothing. The cookie surface
    gets the SAME fail-closed backstop the bearer surface has — it must raise,
    never fall through to the handler, and never be mistaken for a routine
    refusal the user could resolve by logging in again."""
    _actor_with_role(db, tenant_row, "ghost@example.com", "auditor")
    token = _session_token(seam_client, "ghost@example.com")
    seam_client.cookies.clear()
    seam_client.cookies.set(FOREIGN_COOKIE, token)

    resp = seam_client.get("/probe/portal/ghost", follow_redirects=False)
    assert resp.status_code == 500
    assert resp.json() != {"ok": True}


# ── The seam itself ─────────────────────────────────────────────────────────


def test_authorize_party_is_a_predicate_over_an_established_party(
    db: Session, tenant_row: Tenant
) -> None:
    """No request, no header, no cookie — that is what authentication-neutral
    means. A service or a template's optional slot can ask the same question."""
    _install_reports_catalogue()
    _actor_with_role(db, tenant_row, "auditor@example.com", "auditor")
    party = db.query(Party).filter(Party.email == "auditor@example.com").one()

    assert (
        authorize_party(db, tenant=tenant_row, party=party, code="probe.reports")
        is True
    )


def test_authorize_party_refuses_a_party_without_the_declared_role(
    db: Session, tenant_row: Tenant
) -> None:
    _install_reports_catalogue()
    _actor_with_role(db, tenant_row, "admin@example.com", "admin")
    party = db.query(Party).filter(Party.email == "admin@example.com").one()

    assert (
        authorize_party(db, tenant=tenant_row, party=party, code="probe.reports")
        is False
    )


def test_authorize_party_raises_on_an_undeclared_code_rather_than_returning_false(
    db: Session, tenant_row: Tenant
) -> None:
    """The distinction is load-bearing. `False` means "asked and refused"; an
    undeclared code means the question is not real, and a caller that treated it
    as a refusal would silently mask a typo behind a plausible 403."""
    install_permissions(PermissionCatalogue(()))
    _actor_with_role(db, tenant_row, "someone@example.com", "admin")
    party = db.query(Party).filter(Party.email == "someone@example.com").one()

    with pytest.raises(UndeclaredPermissionError):
        authorize_party(db, tenant=tenant_row, party=party, code="probe.nothing")


def test_permission_guard_stamps_the_code_whoever_built_it() -> None:
    """The stamp is what `create_app` reads back off every mounted route. A
    guard an ASSEMBLY built must carry it too, or that assembly silently loses
    the boot-time declaration check and a typo'd code becomes a runtime 500."""
    dep = permission_guard(
        "probe.reports",
        authenticated_party=_workspace_auth,
        denied=_permission_denied,
    )
    assert getattr(dep, PERMISSION_CODE_ATTR) == "probe.reports"


def test_require_permission_is_the_bearer_binding_of_the_same_factory() -> None:
    """`require_permission` holds no decision of its own any more."""
    assert (
        getattr(require_permission("probe.reports"), PERMISSION_CODE_ATTR)
        == "probe.reports"
    )
