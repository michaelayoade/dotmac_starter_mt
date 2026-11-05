"""Operational boundary regressions; execute only on Git-hosted CI."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from dotmac_kernel import app_factory
from dotmac_kernel.api_documentation import DEVELOPMENT, api_documentation_policy
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.features import FeatureManifest, mount_features
from dotmac_kernel.middleware.rate_limit import RateLimitMiddleware
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _spec(**kwargs) -> ProductAssemblySpec:
    return ProductAssemblySpec(
        name="operational_test",
        web_enabled=False,
        platform_surface_enabled=False,
        api_documentation=api_documentation_policy(DEVELOPMENT),
        **kwargs,
    )


@pytest.fixture
def startup(monkeypatch):
    monkeypatch.setattr(app_factory, "_required_setting_errors", lambda: [])
    monkeypatch.setattr(app_factory, "_tenancy_errors", lambda: [])


def test_readiness_is_unavailable_before_startup(startup) -> None:
    app = app_factory.create_app(_spec(readiness_checks=(lambda: [],)))
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_readiness_tracks_dependency_recovery_without_exposing_errors(startup) -> None:
    errors = ["private database address and secret must never be returned"]
    app = app_factory.create_app(_spec(readiness_checks=(lambda: errors,)))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        errors.clear()
        assert client.get("/health/ready").status_code == 200
    assert TestClient(app).get("/health/ready").status_code == 503


def test_readiness_exception_is_unavailable_not_an_error_disclosure(startup) -> None:
    def broken():
        raise RuntimeError("sensitive connection details")

    app = app_factory.create_app(_spec(readiness_checks=(broken,)))
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_running_assembly_refuses_recomposition_before_global_mutation(startup) -> None:
    spec = _spec()
    app = app_factory.create_app(spec)
    with TestClient(app):
        with pytest.raises(RuntimeError, match="assembly"):
            app_factory.create_app(_spec())


def test_superseded_application_cannot_serve_with_another_catalogue(startup) -> None:
    first = app_factory.create_app(_spec())
    app_factory.create_app(_spec())
    assert TestClient(first).get("/health").status_code == 503
    with pytest.raises(RuntimeError, match="assembly"):
        with TestClient(first):
            pass


def test_superseded_application_refuses_websocket_handshakes(startup) -> None:
    first = app_factory.create_app(_spec())
    app_factory.create_app(_spec())
    with pytest.raises(WebSocketDisconnect) as failure:
        with TestClient(first).websocket_connect("/unused"):
            pass
    assert failure.value.code == 1013


def test_failed_construction_also_invalidates_the_old_graph(startup) -> None:
    first = app_factory.create_app(_spec())
    with pytest.raises(RuntimeError, match="api_documentation"):
        app_factory.create_app(ProductAssemblySpec(name="invalid", web_enabled=False))
    assert TestClient(first).get("/health").status_code == 503


def test_seed_failure_blocks_readiness_but_preserves_liveness(startup) -> None:
    def seed():
        raise RuntimeError("missing bootstrap dependency")

    module = FeatureManifest(name="bootstrap_dependency", seed=seed)
    app = app_factory.create_app(
        _spec(modules=(module,), readiness_checks=(lambda: [],))
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_reference_probe_observes_both_roles_without_seeding(monkeypatch) -> None:
    from app import readiness

    observed = []

    class Result:
        def scalar_one(self):
            return 1

        def first(self):
            return None

    class ReadSession:
        def __init__(self, role):
            self.role = role

        def execute(self, statement):
            assert statement.is_select
            observed.append(self.role)
            return Result()

    @contextmanager
    def tenant():
        yield ReadSession("tenant")

    @contextmanager
    def platform():
        yield ReadSession("platform")

    monkeypatch.setattr(
        readiness,
        "resolve_database_runtime",
        lambda: SimpleNamespace(readonly_session=tenant, platform_session=platform),
    )
    monkeypatch.setattr(readiness.settings, "tenancy", "multi")
    monkeypatch.setattr(readiness, "validate_required_settings", lambda db: [])
    assert readiness.readiness_errors() == []
    assert set(observed) == {"tenant", "platform"}


def test_assembly_can_bind_an_existing_rate_limit_store() -> None:
    class Store:
        def hit(self, key, now, *, window_seconds, limit):
            return 1, 0

    store = Store()
    app = app_factory.create_app(_spec(rate_limit_store=store))
    middleware = next(m for m in app.user_middleware if m.cls is RateLimitMiddleware)
    assert middleware.kwargs["store"] is store


def test_selected_optional_mount_failure_aborts_construction(monkeypatch) -> None:
    router = APIRouter()
    app = FastAPI()

    def broken(*args, **kwargs):
        raise ValueError("invalid selected router")

    monkeypatch.setattr(app, "include_router", broken)
    with pytest.raises(ValueError, match="invalid selected router"):
        mount_features(
            app,
            manifests=[FeatureManifest(name="selected", routers=[router], core=False)],
            disabled=set(),
            web_enabled=False,
        )


def test_tenant_resolution_runs_outside_the_event_loop(monkeypatch) -> None:
    from dotmac_kernel.middleware.tenant import TenantResolverMiddleware

    from tests.unit.test_tenant_middleware import _drive, _ok_inner_app

    event_loop_thread = threading.get_ident()
    observed = []

    def resolve(self, host):
        observed.append(threading.get_ident())
        return None

    monkeypatch.setattr(TenantResolverMiddleware, "_resolve", resolve)
    _, status = _drive(TenantResolverMiddleware(_ok_inner_app), "/tenant-resource")
    assert status == 404
    assert observed and observed[0] != event_loop_thread
