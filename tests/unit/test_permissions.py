"""Consumer tests for the permission catalogue (`dotmac_kernel.permissions`)
and the guard that consumes it (`dotmac_kernel.deps.require_permission`).

The catalogue DESCRIBES which permission codes exist and which roles hold them
(declared by manifests); the guard is the one place a route references one.
These pin the public contract: stable string codes, one owning module per code,
fail-closed on duplicates and on undeclared references, and — the part that
makes the declaration non-inert — the declared `default_roles` actually
deciding a request.

App-builder pattern from `tests/unit/test_rbac_web.py`: bare `FastAPI()`, the
REAL `app.features.auth.web.router` to obtain a session token, `get_db`
overridden to the in-memory SQLite `db` fixture, a thin middleware standing in
for `TenantResolverMiddleware`. The probe routes are bearer-guarded (that is
what `require_permission` layers on), and the cookie the web login issues is
the SAME token — `authenticate_request` is the one seam behind both flows — so
the tests present it as `Authorization: Bearer ...`.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from dotmac_kernel import (
    DuplicatePermissionError,
    FeatureManifest,
    PermissionCatalogue,
    PermissionSpec,
    UndeclaredPermissionError,
    install_permissions,
)
from dotmac_kernel.deps import get_db, require_permission
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
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.features.auth.web import router as auth_web_router

PASSWORD = "correct horse battery staple"


def _m(name: str, *specs: PermissionSpec) -> FeatureManifest:
    return FeatureManifest(name=name, permissions=specs)


# ── Catalogue mechanics ─────────────────────────────────────────────────────


def test_manifest_permissions_defaults_to_empty() -> None:
    assert FeatureManifest(name="x").permissions == ()


def test_catalogue_declares_and_owns_codes() -> None:
    cat = PermissionCatalogue.from_manifests(
        [
            _m("inventory", PermissionSpec(code="inventory.read")),
            _m("billing", PermissionSpec(code="billing.read")),
        ]
    )
    assert cat.is_declared("inventory.read")
    assert cat.owner("inventory.read") == "inventory"
    assert cat.codes() == {"inventory.read", "billing.read"}
    assert not cat.is_declared("nope.read")
    assert cat.owner("nope.read") is None
    assert cat.spec("nope.read") is None


def test_require_returns_the_spec_and_rejects_an_undeclared_code() -> None:
    spec = PermissionSpec(code="inventory.read", default_roles=("admin", "auditor"))
    cat = PermissionCatalogue.from_manifests([_m("inventory", spec)])
    # The declared spec comes BACK from require() — a caller cannot check
    # declaredness and then read the binding from somewhere else.
    assert cat.require("inventory.read") is spec
    with pytest.raises(UndeclaredPermissionError):
        cat.require("inventory.export")


def test_duplicate_code_across_modules_fails_closed() -> None:
    with pytest.raises(DuplicatePermissionError):
        PermissionCatalogue.from_manifests(
            [
                _m("a", PermissionSpec(code="shared.read")),
                _m("b", PermissionSpec(code="shared.read")),
            ]
        )


def test_one_module_may_repeat_its_own_code() -> None:
    # Same owner declaring a code twice is a redundant declaration, not a
    # conflict — there is still exactly one owning module.
    cat = PermissionCatalogue.from_manifests(
        [
            _m(
                "a",
                PermissionSpec(code="a.read"),
                PermissionSpec(code="a.read", description="again"),
            )
        ]
    )
    assert cat.owner("a.read") == "a"


def test_spec_requires_a_code_and_at_least_one_role() -> None:
    with pytest.raises(ValueError):
        PermissionSpec(code="")
    with pytest.raises(ValueError):
        PermissionSpec(code="x.read", default_roles=())


def test_spec_default_roles_is_normalized_to_a_tuple() -> None:
    assert PermissionSpec(code="x.read", default_roles=["a", "b"]).default_roles == (
        "a",
        "b",
    )


def test_uninstalled_catalogue_declares_nothing() -> None:
    # The fail-closed default: an empty catalogue declares no code, so every
    # reference is undeclared and every guarded route denies.
    assert PermissionCatalogue(()).codes() == frozenset()


# ── The guard consumes the declaration ──────────────────────────────────────


def _build_app(db: Session, tenant: Tenant) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_web_router)

    @app.get("/probe/admin-only")
    def _admin_only(_: Party = Depends(require_permission("probe.admin"))) -> dict:
        return {"ok": True}

    @app.get("/probe/auditor")
    def _auditor(_: Party = Depends(require_permission("probe.audit"))) -> dict:
        return {"ok": True}

    @app.get("/probe/ghost")
    def _ghost(_: Party = Depends(require_permission("probe.ghost"))) -> dict:
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
def probe_client(db: Session, tenant_row: Tenant) -> TestClient:
    install_permissions(
        PermissionCatalogue.from_manifests(
            [
                _m(
                    "probe",
                    PermissionSpec(code="probe.admin", default_roles=("admin",)),
                    PermissionSpec(code="probe.audit", default_roles=("auditor",)),
                )
            ]
        )
    )
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


def _bearer(client: TestClient, email: str) -> dict[str, str]:
    resp = client.post(
        "/admin/login",
        data={"username": email, "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    return {"Authorization": f"Bearer {resp.cookies['access_token']}"}


def test_declared_default_roles_decide_the_request(
    probe_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    """The load-bearing proof that the declaration is not inert: ONE actor
    passes one guard and is forbidden by the other, purely because the two
    specs declare different `default_roles`."""
    _actor_with_role(db, tenant_row, "auditor@example.com", "auditor")
    headers = _bearer(probe_client, "auditor@example.com")
    assert probe_client.get("/probe/auditor", headers=headers).status_code == 200
    assert probe_client.get("/probe/admin-only", headers=headers).status_code == 403


def test_admin_passes_only_the_admin_permission(
    probe_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    _actor_with_role(db, tenant_row, "admin@example.com", "admin")
    headers = _bearer(probe_client, "admin@example.com")
    assert probe_client.get("/probe/admin-only", headers=headers).status_code == 200
    assert probe_client.get("/probe/auditor", headers=headers).status_code == 403


def test_guard_denies_an_undeclared_code_rather_than_allowing_it(
    probe_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    """`probe.ghost` is referenced by a route but declared by nothing. The
    request-time backstop behind `create_app`'s boot check must FAIL, never
    fall through to the handler."""
    _actor_with_role(db, tenant_row, "ghost@example.com", "admin")
    headers = _bearer(probe_client, "ghost@example.com")
    resp = probe_client.get("/probe/ghost", headers=headers)
    assert resp.status_code == 500  # raised, not allowed
    assert resp.json() != {"ok": True}


def test_guard_stamps_the_code_for_boot_time_validation() -> None:
    dep = require_permission("probe.admin")
    assert getattr(dep, PERMISSION_CODE_ATTR) == "probe.admin"
