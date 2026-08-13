"""Train 3: resolved brand data becomes same-origin token CSS.

The assembly-owned presentation adapter is the only code allowed to know both
the kernel branding resolver and ``dotmac-ui``. These tests exercise the real
route without an authenticated cookie: the stylesheet must be available to the
pre-auth login page, while ``require_tenant`` still prevents it becoming an
unscoped cross-tenant asset.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

from dotmac_kernel.deps import get_db
from dotmac_kernel.models import Tenant
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import upsert_by_key
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.features.presentation import service
from app.features.presentation.web import router


def _client(db: Session, tenant: Tenant) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


def test_brand_stylesheet_is_public_tenant_scoped_css(
    db: Session, tenant_row: Tenant
) -> None:
    response = _client(db, tenant_row).get("/branding/theme.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Host"
    assert response.headers["x-dotmac-brand-projection"] == "generated"
    assert "--dmui-color-brand-" in response.text
    assert "--dmui-color-brand-500-rgb:" in response.text
    assert "access-control-allow-origin" not in response.headers
    for forbidden in ("@import", "url(", "javascript:", "<"):
        assert forbidden not in response.text


def test_brand_stylesheet_is_deterministic_and_uses_both_resolved_seeds(
    db: Session, tenant_row: Tenant
) -> None:
    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"primary_color": "#112233", "accent_color": "#445566"},
        tenant_id=tenant_row.id,
    )
    db.flush()
    client = _client(db, tenant_row)

    first = client.get("/branding/theme.css")
    second = client.get("/branding/theme.css")

    assert first.status_code == second.status_code == 200
    assert first.text == second.text
    assert "#112233" in first.text
    assert "#445566" in first.text
    assert "--dmui-color-accent-500-rgb:" in first.text


def test_generation_failure_serves_package_defaults_without_logging_brand_input(
    db: Session,
    tenant_row: Tenant,
    monkeypatch,
    caplog,
) -> None:
    hostile_input = "not-a-colour-do-not-log"
    monkeypatch.setattr(
        service,
        "load_branding",
        lambda _db, _tenant_id: {
            "primary_color": hostile_input,
            "accent_color": "#445566",
        },
    )

    with caplog.at_level(logging.ERROR):
        response = _client(db, tenant_row).get("/branding/theme.css")

    assert response.status_code == 200
    assert response.headers["x-dotmac-brand-projection"] == "package-defaults"
    assert "--dmui-" not in response.text
    assert hostile_input not in response.text
    assert hostile_input not in caplog.text
    assert "brand projection failed" in caplog.text.lower()


def test_a_missing_tenant_cannot_fetch_a_brand_stylesheet(db: Session) -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db

    response = TestClient(app, raise_server_exceptions=False).get(
        "/branding/theme.css", headers={"Host": "unknown.example"}
    )

    assert response.status_code == 404


def test_the_exact_platform_host_gets_only_package_defaults(
    db: Session, monkeypatch
) -> None:
    from app.features.presentation import web

    monkeypatch.setattr(web.settings, "platform_root_domain", "platform.example")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db

    response = TestClient(app, raise_server_exceptions=False).get(
        "/branding/theme.css", headers={"Host": "platform.example"}
    )

    assert response.status_code == 200
    assert response.headers["x-dotmac-brand-projection"] == "package-defaults"
    assert "--dmui-" not in response.text
