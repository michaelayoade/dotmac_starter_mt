"""`require_capability` behaviour: deny-by-default, explainable, composable.

Unit lane (SQLite, no RLS) — this proves the GUARD's decision logic, not tenancy
isolation. The grant store's own tenant scoping is proven against Postgres by
`tests/test_entitlements_isolation.py`.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.capabilities import (
    CapabilityCatalogue,
    UndeclaredCapabilityError,
    install_capabilities,
)
from dotmac_kernel.deps import get_db, require_capability
from dotmac_kernel.entitlements import grant_entitlement
from dotmac_kernel.models import Tenant
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

_CODE = "probe.use"
_CATALOGUE = CapabilityCatalogue({_CODE: "probe_module"})


@pytest.fixture(autouse=True)
def _restore_catalogue():
    """These tests install a narrow probe catalogue process-wide.

    The unit conftest re-installs the real one before every UNIT test, but the
    architecture suite has no such fixture and runs in the same process — so a
    leaked probe catalogue would make `test_capability_enforcement` see the
    wrong installed set. Restore explicitly rather than relying on collection
    order.
    """
    from dotmac_kernel.capabilities import active_capabilities

    previous = active_capabilities()
    yield
    install_capabilities(previous)


@pytest.fixture()
def gated_client(db: Session, tenant_row: Tenant):
    """A minimal app with one capability-gated route.

    A throwaway app rather than the production one, for the same reason
    `test_admin_route_sweep` builds its own: the real `TenantResolverMiddleware`
    opens its own connection outside dependency injection and cannot run against
    SQLite. The middleware here stands in for it by pinning `request.state.tenant`.
    """
    install_capabilities(_CATALOGUE)
    app = FastAPI()

    @app.middleware("http")
    async def _pin_tenant(request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    @app.get("/gated", dependencies=[Depends(require_capability(_CODE))])
    def _gated() -> dict[str, str]:
        return {"ok": "yes"}

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client


def test_no_grant_is_denied(gated_client) -> None:
    """Deny-by-default: an absent grant row is not an implicit yes."""
    response = gated_client.get("/gated")
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "not_granted"


def test_a_granted_tenant_is_allowed(db, tenant_row, gated_client) -> None:
    grant_entitlement(
        db, tenant_id=tenant_row.id, capability_code=_CODE, catalogue=_CATALOGUE
    )
    assert gated_client.get("/gated").status_code == 200


def test_a_revoked_grant_is_denied_and_says_so(db, tenant_row, gated_client) -> None:
    """`revoked` and `not_granted` are different answers on purpose — an
    operator must be able to tell "never had it" from "had it and lost it"
    without reading the grant table."""
    grant_entitlement(
        db,
        tenant_id=tenant_row.id,
        capability_code=_CODE,
        catalogue=_CATALOGUE,
        granted=False,
    )
    response = gated_client.get("/gated")
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "revoked"


def test_the_denial_names_the_capability_not_a_payment_state(gated_client) -> None:
    """A request-time check never knows — and must never imply — a payment or
    licence status. ADR-0003: the decision is local and explainable."""
    detail = gated_client.get("/gated").json()["detail"]
    assert detail["capability"] == _CODE
    assert detail["error"] == "not_entitled"
    assert set(detail) == {"error", "capability", "reason"}


def test_an_uninstalled_catalogue_denies_rather_than_admits(db, tenant_row) -> None:
    """Fail closed. An empty (never-installed) catalogue must raise, not allow —
    a wiring mistake must not silently entitle every tenant to everything."""
    install_capabilities(CapabilityCatalogue({}))
    app = FastAPI()

    @app.middleware("http")
    async def _pin_tenant(request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    @app.get("/gated", dependencies=[Depends(require_capability(_CODE))])
    def _gated() -> dict[str, str]:
        return {"ok": "yes"}

    app.dependency_overrides[get_db] = lambda: db
    # `raise_server_exceptions=True` (the default) so the guard's exception
    # PROPAGATES here instead of being turned into a 500 — the point is that it
    # raises rather than admits, and a swallowed 500 would prove only that
    # something went wrong.
    with TestClient(app) as client:
        with pytest.raises(UndeclaredCapabilityError):
            client.get("/gated")


def test_the_guard_returns_the_decision_for_limits(db, tenant_row) -> None:
    """The admitting decision carries the grant's `limits`, so a route that
    needs a quota reads it from the same decision that let it in rather than
    issuing a second, possibly-disagreeing read."""
    install_capabilities(_CATALOGUE)
    grant_entitlement(
        db,
        tenant_id=tenant_row.id,
        capability_code=_CODE,
        catalogue=_CATALOGUE,
        limits={"seats": 5},
    )
    app = FastAPI()

    @app.middleware("http")
    async def _pin_tenant(request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    @app.get("/seats")
    def _seats(decision=Depends(require_capability(_CODE))) -> dict[str, object]:
        return {"limits": decision.limits}

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get("/seats").json()["limits"] == {"seats": 5}
