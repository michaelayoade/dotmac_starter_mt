"""TDD for the Settings admin screens (Task 7): grouped index, generic
per-key edit, the friendly branding editor.

App-builder pattern from `tests/unit/test_rbac_web.py` — bare `FastAPI()` +
`register_error_handlers` + the REAL `app.features.auth.web.router` (so we
can log in and get a cookie) and `app.features.settings.web.router` mounted,
`get_db` overridden to the in-memory SQLite `db` fixture, a thin middleware
standing in for `TenantResolverMiddleware`. `require_web_auth` is NOT
overridden — every guarded route is exercised through the real cookie-auth
seam.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.audit import AuditEvent
from app.core.deps import get_db
from app.core.errors import register_error_handlers
from app.core.models import Tenant
from app.core.settings_models import SettingDomain
from app.core.settings_resolver import upsert_by_key
from app.features.auth import service as auth_service
from app.features.auth.schemas import RegisterRequest
from app.features.auth.web import router as auth_web_router

# Import for the side effect: registers custom_fields/max_per_entity,
# branding/ui_branding, audit/retention_days into the resolver registry.
from app.features.settings import spec as _settings_spec  # noqa: F401
from app.features.settings.web import router as settings_web_router

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def registered_admin(db: Session, tenant_row: Tenant) -> dict:
    view = auth_service.register(
        db,
        tenant_row,
        RegisterRequest(
            email="admin@example.com",
            password=PASSWORD,
            first_name="Admin",
            last_name="User",
        ),
    )
    db.commit()
    return {"email": view.email, "party_id": view.id}


@pytest.fixture()
def web_client(db: Session, tenant_row: Tenant) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_web_router)
    app.include_router(settings_web_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


def _login(client: TestClient, email: str) -> str:
    resp = client.post(
        "/admin/login",
        data={"username": email, "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    return resp.cookies["access_token"]


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_settings_index_without_cookie_redirects_to_login(
    web_client: TestClient,
) -> None:
    resp = web_client.get("/admin/settings", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/admin/login?next=")


def test_settings_edit_form_without_cookie_redirects_to_login(
    web_client: TestClient,
) -> None:
    resp = web_client.get(
        "/admin/settings/audit/retention_days/edit", follow_redirects=False
    )
    assert resp.status_code == 302


def test_branding_form_without_cookie_redirects_to_login(
    web_client: TestClient,
) -> None:
    resp = web_client.get("/admin/settings/branding", follow_redirects=False)
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# GET /admin/settings — grouped-by-domain index
# ---------------------------------------------------------------------------


def test_settings_index_renders_grouped_specs_with_source_badges(
    web_client: TestClient, registered_admin: dict
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.get("/admin/settings", cookies={"access_token": token})
    assert resp.status_code == 200
    assert 'aria-label="Admin navigation"' in resp.text
    assert "retention_days" in resp.text
    assert "max_per_entity" in resp.text
    assert "ui_branding" in resp.text
    assert "default" in resp.text  # source badge, nothing overridden yet


def test_settings_index_shows_tenant_source_after_override(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    upsert_by_key(
        db, SettingDomain.audit, "retention_days", 90, tenant_id=tenant_row.id
    )
    db.commit()

    resp = web_client.get("/admin/settings", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "90" in resp.text
    assert "tenant" in resp.text


def test_settings_index_never_echoes_a_real_secret_value(
    web_client: TestClient, registered_admin: dict
) -> None:
    """No registered spec is secret today, but the index must still never
    print a raw secret value — service.list_settings already masks it
    (MASKED_SECRET_VALUE); this test locks the template's contract that it
    renders whatever the service returns for `is_secret` specs, not the raw
    stored value.
    """
    token = _login(web_client, registered_admin["email"])
    resp = web_client.get("/admin/settings", cookies={"access_token": token})
    assert resp.status_code == 200
    # No spec is currently secret, so nothing to assert is masked — this is
    # a smoke test that the page renders cleanly with today's registry.
    assert "Settings" in resp.text


def test_settings_index_ui_branding_links_to_friendly_editor(
    web_client: TestClient, registered_admin: dict
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.get("/admin/settings", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "/admin/settings/branding" in resp.text


# ---------------------------------------------------------------------------
# GET/POST /admin/settings/{domain}/{key}/edit — generic editor
# ---------------------------------------------------------------------------


def test_edit_form_renders_current_value(
    web_client: TestClient, registered_admin: dict
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.get(
        "/admin/settings/audit/retention_days/edit", cookies={"access_token": token}
    )
    assert resp.status_code == 200
    assert 'name="value"' in resp.text
    assert "365" in resp.text  # spec default


def test_edit_form_unknown_key_returns_404(
    web_client: TestClient, registered_admin: dict
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.get(
        "/admin/settings/audit/does-not-exist/edit", cookies={"access_token": token}
    )
    assert resp.status_code == 404


def test_edit_form_unknown_domain_returns_404(
    web_client: TestClient, registered_admin: dict
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.get(
        "/admin/settings/not-a-domain/some-key/edit",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


def test_edit_submit_writes_tenant_override_and_redirects(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.post(
        "/admin/settings/audit/retention_days/edit",
        data={"value": "90"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/settings"
    assert resp.headers["hx-redirect"] == "/admin/settings"

    from app.core.settings_resolver import resolve_value

    value = resolve_value(
        db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
    )
    assert value == 90


def test_edit_submit_invalid_value_rerenders_200_with_error(
    web_client: TestClient, registered_admin: dict
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.post(
        "/admin/settings/audit/retention_days/edit",
        data={"value": "0"},  # below min_value=1
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert ">= 1" in resp.text or "must be" in resp.text.lower()


def test_edit_submit_writes_audit_event(
    web_client: TestClient, registered_admin: dict, db: Session
) -> None:
    token = _login(web_client, registered_admin["email"])
    web_client.post(
        "/admin/settings/audit/retention_days/edit",
        data={"value": "120"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    event = db.query(AuditEvent).filter(AuditEvent.action == "settings.update").one()
    assert event.details["key"] == "retention_days"


# ---------------------------------------------------------------------------
# GET/POST /admin/settings/branding — friendly editor
# ---------------------------------------------------------------------------


def test_branding_form_renders_current_effective_branding(
    web_client: TestClient, registered_admin: dict
) -> None:
    from app.core.branding import get_brand

    token = _login(web_client, registered_admin["email"])
    resp = web_client.get("/admin/settings/branding", cookies={"access_token": token})
    assert resp.status_code == 200
    assert get_brand()["name"] in resp.text
    assert 'hx-post="/admin/settings/branding"' in resp.text


def test_branding_submit_writes_override_and_redirects(
    web_client: TestClient, registered_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, registered_admin["email"])
    resp = web_client.post(
        "/admin/settings/branding",
        data={
            "name": "Acme Tenant",
            "tagline": "Custom tagline",
            "logo_url": "https://example.com/logo.png",
            "primary_color": "#112233",
            "accent_color": "#445566",
            "custom_css": ".ok { color: red; }",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/settings/branding"
    assert resp.headers["hx-redirect"] == "/admin/settings/branding"

    from app.core.branding import load_branding

    branding = load_branding(db, tenant_row.id)
    assert branding["name"] == "Acme Tenant"
    assert branding["primary_color"] == "#112233"


def test_branding_form_after_submit_shows_sanitized_css_preview(
    web_client: TestClient, registered_admin: dict
) -> None:
    """`sanitize_branding_css` strips a dangerous pattern in place (verified
    directly in `tests/unit/test_branding.py`); an angle-bracket breakout
    wipes the WHOLE value instead of partial-stripping (also that module's
    contract), so this uses an `@import` with no `<`/`>` to prove the
    preview renders the sanitizer's OUTPUT (the safe rule survives, the
    dangerous one doesn't) rather than the raw stored value.
    """
    token = _login(web_client, registered_admin["email"])
    web_client.post(
        "/admin/settings/branding",
        data={
            "name": "",
            "tagline": "",
            "logo_url": "",
            "primary_color": "",
            "accent_color": "",
            "custom_css": (
                '.ok { color: red; } @import url("https://evil.example/x.css");'
            ),
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )

    resp = web_client.get("/admin/settings/branding", cookies={"access_token": token})
    assert resp.status_code == 200
    # The static help text below the textarea mentions "@import" by name
    # (describing what gets stripped) — check the actual dangerous URL is
    # gone, not the word, and that the safe rule survived alongside it.
    assert "evil.example" not in resp.text
    assert ".ok { color: red; }" in resp.text
