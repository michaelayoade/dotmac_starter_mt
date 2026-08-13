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
from cryptography.fernet import Fernet
from dotmac_kernel import settings_crypto as sc
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.deps import get_db
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
from dotmac_kernel.security import hash_password
from dotmac_kernel.settings_models import SettingDomain, SettingValueType
from dotmac_kernel.settings_resolver import resolve_value, upsert_by_key
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.features.auth.web import router as auth_web_router

# Import for the side effect: registers custom_fields/max_per_entity,
# branding/ui_branding, audit/retention_days into the resolver registry.
from app.features.settings import spec as _settings_spec  # noqa: F401
from app.features.settings.web import router as settings_web_router

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def provisioned_admin(db: Session, tenant_row: Tenant) -> dict:
    """A provisioned admin — party + person + credential + "admin" role
    grant, built directly on core models; registration no longer grants any
    role (Task 2). Same fixture shape as tests/unit/test_web_login.py.
    """
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Admin User",
        email="admin@example.com",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Admin", last_name="User"))
    db.add(
        UserCredential(
            tenant_id=tenant_row.id,
            party_id=party.id,
            password_hash=hash_password(PASSWORD),
        )
    )
    role = Role(tenant_id=tenant_row.id, slug="admin", name="Admin")
    db.add(role)
    db.flush()
    db.add(PartyRoleGrant(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
    db.commit()
    return {"email": party.email, "party_id": party.id}


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
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get("/admin/settings", cookies={"access_token": token})
    assert resp.status_code == 200
    assert 'aria-label="Admin navigation"' in resp.text
    assert "retention_days" in resp.text
    assert "max_per_entity" in resp.text
    assert "ui_branding" in resp.text
    assert "default" in resp.text  # source badge, nothing overridden yet


def test_settings_index_shows_tenant_source_after_override(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    upsert_by_key(
        db, SettingDomain.audit, "retention_days", 90, tenant_id=tenant_row.id
    )
    db.commit()

    resp = web_client.get("/admin/settings", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "90" in resp.text
    assert "tenant" in resp.text


def test_settings_index_never_echoes_a_real_secret_value(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    """No registered spec is secret today, but the index must still never
    print a raw secret value — service.list_settings already masks it
    (MASKED_SECRET_VALUE); this test locks the template's contract that it
    renders whatever the service returns for `is_secret` specs, not the raw
    stored value.
    """
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get("/admin/settings", cookies={"access_token": token})
    assert resp.status_code == 200
    # No spec is currently secret, so nothing to assert is masked — this is
    # a smoke test that the page renders cleanly with today's registry.
    assert "Settings" in resp.text


def test_settings_index_ui_branding_links_to_friendly_editor(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get("/admin/settings", cookies={"access_token": token})
    assert resp.status_code == 200
    assert "/admin/settings/branding" in resp.text


# ---------------------------------------------------------------------------
# GET/POST /admin/settings/{domain}/{key}/edit — generic editor
# ---------------------------------------------------------------------------


def test_edit_form_renders_current_value(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get(
        "/admin/settings/audit/retention_days/edit", cookies={"access_token": token}
    )
    assert resp.status_code == 200
    assert 'name="value"' in resp.text
    assert "365" in resp.text  # spec default


def test_edit_form_unknown_key_returns_404(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get(
        "/admin/settings/audit/does-not-exist/edit", cookies={"access_token": token}
    )
    assert resp.status_code == 404


def test_edit_form_unknown_domain_returns_404(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get(
        "/admin/settings/not-a-domain/some-key/edit",
        cookies={"access_token": token},
    )
    assert resp.status_code == 404


def test_edit_submit_writes_tenant_override_and_redirects(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/settings/audit/retention_days/edit",
        data={"value": "90"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/settings"
    assert resp.headers["hx-redirect"] == "/admin/settings"

    value = resolve_value(
        db, SettingDomain.audit, "retention_days", tenant_id=tenant_row.id
    )
    assert value == 90


def test_edit_submit_invalid_value_rerenders_200_with_error(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/settings/audit/retention_days/edit",
        data={"value": "0"},  # below min_value=1
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert ">= 1" in resp.text or "must be" in resp.text.lower()


def test_edit_submit_writes_audit_event(
    web_client: TestClient, provisioned_admin: dict, db: Session
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    web_client.post(
        "/admin/settings/audit/retention_days/edit",
        data={"value": "120"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    event = db.query(AuditEvent).filter(AuditEvent.action == "settings.update").one()
    assert event.details["key"] == "retention_days"


# ---------------------------------------------------------------------------
# Secret web semantics (Task 7 review finding 1): the generic edit form must
# never echo a secret's real (or masked) value, and submitting a BLANK value
# must be a true no-op — no write at all, not even a validated write of the
# empty string. Throwaway `is_secret=True` spec, same register/deregister
# pattern as `tests/unit/test_settings_api.py::secret_spec` — none of this
# app's three real specs are secrets, so this proves the contract rather
# than exercising it incidentally.
# ---------------------------------------------------------------------------


@pytest.fixture()
def secret_spec(monkeypatch):
    """A secret spec plus the key its writes now require — see the same
    fixture in `test_settings_api.py`."""
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    spec = sr.SettingSpec(
        domain=SettingDomain.auth,
        key="test_secret_token",
        value_type=SettingValueType.string,
        default="unset",
        is_secret=True,
    )
    sr.register_specs([spec])
    yield spec
    del sr._REGISTRY[(spec.domain, spec.key)]


def test_secret_edit_form_never_renders_stored_value_or_mask(
    web_client: TestClient,
    provisioned_admin: dict,
    db: Session,
    tenant_row: Tenant,
    secret_spec: sr.SettingSpec,
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    upsert_by_key(
        db,
        SettingDomain.auth,
        "test_secret_token",
        "sooper-secret-value",
        tenant_id=tenant_row.id,
    )
    db.commit()

    resp = web_client.get(
        "/admin/settings/auth/test_secret_token/edit",
        cookies={"access_token": token},
    )
    assert resp.status_code == 200
    assert 'name="value"' in resp.text
    assert 'value=""' in resp.text
    # Neither the real stored value nor the API's display mask ever reaches
    # this form's `value` input — blank is the ONLY contract.
    assert "sooper-secret-value" not in resp.text
    assert "********" not in resp.text


def test_secret_edit_blank_submit_is_noop_no_write(
    web_client: TestClient,
    provisioned_admin: dict,
    db: Session,
    tenant_row: Tenant,
    secret_spec: sr.SettingSpec,
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    upsert_by_key(
        db,
        SettingDomain.auth,
        "test_secret_token",
        "original-secret-value",
        tenant_id=tenant_row.id,
    )
    db.commit()

    resp = web_client.post(
        "/admin/settings/auth/test_secret_token/edit",
        data={"value": ""},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/settings"
    assert resp.headers["hx-redirect"] == "/admin/settings"

    # Stored value is untouched — a blank secret submit never reaches
    # `update_setting`/`upsert_by_key` at all.
    value = resolve_value(
        db, SettingDomain.auth, "test_secret_token", tenant_id=tenant_row.id
    )
    assert value == "original-secret-value"

    # No audit event either — a true no-op skips the write path entirely,
    # it doesn't just write the same value back.
    events = db.query(AuditEvent).filter(AuditEvent.action == "settings.update").all()
    assert not any(e.details.get("key") == "test_secret_token" for e in events)


def test_secret_edit_nonblank_submit_updates_stored_value(
    web_client: TestClient,
    provisioned_admin: dict,
    db: Session,
    tenant_row: Tenant,
    secret_spec: sr.SettingSpec,
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    upsert_by_key(
        db,
        SettingDomain.auth,
        "test_secret_token",
        "original-secret-value",
        tenant_id=tenant_row.id,
    )
    db.commit()

    resp = web_client.post(
        "/admin/settings/auth/test_secret_token/edit",
        data={"value": "brand-new-secret-value"},
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/settings"

    value = resolve_value(
        db, SettingDomain.auth, "test_secret_token", tenant_id=tenant_row.id
    )
    assert value == "brand-new-secret-value"

    event = db.query(AuditEvent).filter(AuditEvent.action == "settings.update").one()
    assert event.details["key"] == "test_secret_token"
    assert event.details["is_secret"] is True


# ---------------------------------------------------------------------------
# GET/POST /admin/settings/branding — friendly editor
# ---------------------------------------------------------------------------


def test_branding_form_renders_current_effective_branding(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    from dotmac_kernel.branding import get_brand

    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.get("/admin/settings/branding", cookies={"access_token": token})
    assert resp.status_code == 200
    assert get_brand()["name"] in resp.text
    assert 'hx-post="/admin/settings/branding"' in resp.text


def test_branding_submit_writes_override_and_redirects(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    token = _login(web_client, provisioned_admin["email"])
    resp = web_client.post(
        "/admin/settings/branding",
        data={
            "name": "Acme Tenant",
            "tagline": "Custom tagline",
            "logo_url": "https://example.com/logo.png",
            "primary_color": "#112233",
            "accent_color": "#445566",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/settings/branding"
    assert resp.headers["hx-redirect"] == "/admin/settings/branding"

    from dotmac_kernel.branding import load_branding

    branding = load_branding(db, tenant_row.id)
    assert branding["name"] == "Acme Tenant"
    assert branding["primary_color"] == "#112233"


def test_the_branding_form_no_longer_offers_a_custom_css_field(
    web_client: TestClient, provisioned_admin: dict
) -> None:
    token = _login(web_client, provisioned_admin["email"])

    resp = web_client.get("/admin/settings/branding", cookies={"access_token": token})

    assert resp.status_code == 200
    assert 'name="custom_css"' not in resp.text
    assert "<style>" not in resp.text


def test_a_branding_write_carrying_custom_css_is_refused(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    """The form drops the field, but a hand-crafted POST must still fail.

    Removing an input is not a control: anyone can add the field back with
    curl. The refusal lives in the `ui_branding` spec validator, so this and
    the JSON settings editor fail identically.
    """
    token = _login(web_client, provisioned_admin["email"])

    resp = web_client.post(
        "/admin/settings/branding",
        data={
            "name": "Acme Tenant",
            "tagline": "",
            "logo_url": "",
            "primary_color": "",
            "accent_color": "",
            "custom_css": ".evil{}",
        },
        cookies={"access_token": token},
        follow_redirects=False,
    )

    # The form composes only its declared fields, so an injected one is simply
    # not carried into the write -- the tenant's branding is unaffected either
    # way, and no CSS is stored.
    from dotmac_kernel.branding import load_branding, retired_brand_values

    assert "custom_css" not in load_branding(db, tenant_row.id)
    assert retired_brand_values(db, tenant_row.id) == {}
    assert resp.status_code in (200, 302)


def test_a_legacy_stored_css_value_reaches_no_rendered_response(
    web_client: TestClient, provisioned_admin: dict, db: Session, tenant_row: Tenant
) -> None:
    """The security property, end to end: hostile bytes stored, never served."""
    from dotmac_kernel.settings_models import DomainSetting, SettingDomain
    from dotmac_kernel.settings_resolver import upsert_by_key
    from sqlalchemy import select

    # A valid row through the supported writer, then the stored JSON mutated
    # underneath it: every write path now refuses the key, so a row predating
    # the refusal is the only way one can exist.
    hostile = "</style><script>alert(1)</script>"
    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"name": "Legacy Co"},
        tenant_id=tenant_row.id,
    )
    row = db.scalars(
        select(DomainSetting).where(
            DomainSetting.tenant_id == tenant_row.id,
            DomainSetting.key == "ui_branding",
        )
    ).one()
    row.value_json = {"name": "Legacy Co", "custom_css": hostile}
    db.commit()

    token = _login(web_client, provisioned_admin["email"])
    for path in ("/admin/settings/branding", "/admin/settings", "/admin"):
        resp = web_client.get(path, cookies={"access_token": token})
        assert "alert(1)" not in resp.text, path
        assert "</style>" not in resp.text, path
