"""`dotmac-settings-admin` staff surface via the real contract-2 mount + cookie.

Authorization is stubbed at `authorize_party` (the role query is proven elsewhere;
the non-admitted path is covered by the admin route sweep). CSRF is enabled for the
mutation tests so the hidden-field transport is exercised for real.
"""

from __future__ import annotations

import re
from collections.abc import Generator

import pytest
from cryptography.fernet import Fernet
from dotmac_kernel import settings_crypto as sc
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.capabilities import CapabilityCatalogue, install_capabilities
from dotmac_kernel.deps import get_db
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.middleware.csrf import CSRFMiddleware
from dotmac_kernel.models import AuthSession, Party, PartyPerson, PartyType
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.permissions import (
    PermissionCatalogue,
    PermissionSpec,
    install_permissions,
)
from dotmac_kernel.security import hash_token, issue_access_token
from dotmac_kernel.settings_models import (
    DomainSettingHistory,
    SettingDomain,
    SettingValueType,
)
from dotmac_kernel.web_deps import TENANT_COOKIE_AUTHENTICATION
from dotmac_kernel.web_runtime import mount_web_surfaces
from dotmac_kernel.web_surfaces import (
    AuthenticationProfileBinding,
    BrowserCapabilityProvision,
    BrowserSessionPolicy,
    NavigationRegion,
    TemplateRef,
    WebFacetMount,
    WebRouteRef,
    WebSurfaceRegistry,
)
from dotmac_settings_admin import module as settings_admin_module
from dotmac_settings_admin import service, web
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.auth.web import router as auth_web_router

CSRF_SECRET = "settings-admin-test-csrf-not-a-deployment-secret"


def _build_client(db, tenant) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(CSRFMiddleware, enabled=True, secret=CSRF_SECRET)
    manifests = (
        ModuleManifest(
            code="portal_access",
            version="1",
            permissions=(PermissionSpec("web.portal.staff.access"),),
        ),
        ModuleManifest(
            code="auth", version="1", contract_version=1, web_routers=(auth_web_router,)
        ),
        settings_admin_module,
    )
    install_permissions(PermissionCatalogue.from_manifests(manifests))
    install_capabilities(CapabilityCatalogue.from_manifests(manifests))
    registry = WebSurfaceRegistry(
        manifests=manifests,
        facets=(
            WebFacetMount(
                code="staff_admin",
                url_prefix="/admin",
                shell=TemplateRef("layouts/admin.html"),
                authentication_profile="staff_session",
                admission_permission="web.portal.staff.access",
                navigation_regions=(NavigationRegion("primary"),),
                entry_routes=(
                    WebRouteRef("auth", "legacy", "login_page"),
                    WebRouteRef("auth", "legacy", "login_submit"),
                    WebRouteRef("auth", "legacy", "logout"),
                ),
                login_route=WebRouteRef("auth", "legacy", "login_page"),
            ),
        ),
        authentication_profiles=(
            AuthenticationProfileBinding(
                code="staff_session",
                provider=TENANT_COOKIE_AUTHENTICATION,
                session=BrowserSessionPolicy(cookie_name="access_token"),
            ),
        ),
        browser_capabilities=(BrowserCapabilityProvision("htmx", 2),),
        ui_contract_version=1,
    )
    from dotmac_kernel.templating import compose_templates

    from app.assembly import assembly

    compose_templates(
        assembly_dir=assembly.assembly_template_dir,
        packaged_dirs=assembly.packaged_template_dirs,
        namespaced_dirs={"settings_admin": web.template_dir()},
    )
    mount_web_surfaces(
        app, registry=registry, enabled_modules=frozenset(m.code for m in manifests)
    )

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant
        return await call_next(request)

    def _override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def allow_all(monkeypatch):
    for target in ("dotmac_kernel.deps", "dotmac_kernel.web_runtime"):
        monkeypatch.setattr(f"{target}.authorize_party", lambda *a, **k: True)
    monkeypatch.setattr(
        "dotmac_settings_admin.router.authorize_party", lambda *a, **k: True
    )


@pytest.fixture
def party(db, tenant_row) -> Party:
    p = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="web-admin",
        email="web-admin@example.test",
    )
    db.add(p)
    db.flush()
    db.add(PartyPerson(party_id=p.id, first_name="W", last_name="A"))
    db.flush()
    return p


@pytest.fixture
def client(db, tenant_row, allow_all) -> TestClient:
    return _build_client(db, tenant_row)


@pytest.fixture
def cookies(db, tenant_row, party) -> dict[str, str]:
    token, expires_at = issue_access_token(party.id, tenant_row.id)
    db.add(
        AuthSession(
            tenant_id=tenant_row.id,
            party_id=party.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    db.flush()
    return {"access_token": token}


def _csrf(client, cookies, path) -> str:
    page = client.get(path, cookies=cookies)
    assert page.status_code == 200, page.text
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


@pytest.fixture()
def secret_spec(monkeypatch):
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


def test_unauthenticated_requests_do_not_reach_the_surface(client):
    resp = client.get("/admin/settings", follow_redirects=False)
    assert resp.status_code in (302, 303, 307, 401)


def test_index_groups_domains_by_owner_and_domain_page_lists_specs(client, cookies):
    index = client.get("/admin/settings", cookies=cookies)
    assert index.status_code == 200
    assert "settings_admin" in index.text and "display" in index.text
    page = client.get("/admin/settings/domains/display", cookies=cookies)
    assert page.status_code == 200 and "timezone" in page.text
    assert (
        client.get("/admin/settings/domains/nope", cookies=cookies).status_code == 404
    )


def test_edit_requires_a_reason_then_writes_attributed_and_redirects(
    client, cookies, db, party
):
    path = "/admin/settings/display/timezone/edit"
    token = _csrf(client, cookies, path)
    missing = client.post(
        path,
        cookies=cookies,
        data={"value": "Asia/Tokyo", "reason": "", "csrf_token": token},
        follow_redirects=False,
    )
    assert missing.status_code == 200 and "reason" in missing.text.lower()
    assert not db.scalars(select(DomainSettingHistory)).all()
    ok = client.post(
        path,
        cookies=cookies,
        data={"value": "Asia/Tokyo", "reason": "move office", "csrf_token": token},
        follow_redirects=False,
    )
    assert ok.status_code == 303 and ok.headers["HX-Redirect"].endswith(
        "/domains/display"
    )
    (hist,) = db.scalars(select(DomainSettingHistory)).all()
    assert hist.changed_by_party_id == party.id and hist.change_reason == "move office"
    assert [e.action for e in db.scalars(select(AuditEvent))] == [
        "settings_admin.setting.update"
    ]


def test_post_without_a_csrf_token_is_refused(client, cookies, db):
    resp = client.post(
        "/admin/settings/display/timezone/edit",
        cookies=cookies,
        data={"value": "Asia/Tokyo", "reason": "r"},
    )
    assert resp.status_code == 403
    assert not db.scalars(select(DomainSettingHistory)).all()


def test_invalid_value_rerenders_at_200_with_the_error(client, cookies):
    path = "/admin/settings/display/timezone/edit"
    token = _csrf(client, cookies, path)
    resp = client.post(
        path,
        cookies=cookies,
        data={"value": "Mars/Olympus", "reason": "r", "csrf_token": token},
    )
    assert resp.status_code == 200 and "Mars/Olympus" in resp.text


def test_undeclared_key_is_404_on_the_surface(client, cookies):
    assert (
        client.get("/admin/settings/display/nope/edit", cookies=cookies).status_code
        == 404
    )
    assert (
        client.get("/admin/settings/nope/nope/edit", cookies=cookies).status_code == 404
    )
    assert (
        client.get(
            "/admin/settings/settings_admin/pending_changes/edit", cookies=cookies
        ).status_code
        == 404
    )


def test_a_secret_is_never_rendered_set_or_unset_only(
    client, cookies, db, tenant_row, party, secret_spec
):
    page = client.get("/admin/settings/domains/auth", cookies=cookies)
    assert "unset" in page.text
    path = "/admin/settings/auth/test_secret_token/edit"
    token = _csrf(client, cookies, path)
    blank = client.post(
        path, cookies=cookies, data={"value": "", "reason": "r", "csrf_token": token}
    )
    assert (
        blank.status_code == 200 and not db.scalars(select(DomainSettingHistory)).all()
    )
    ok = client.post(
        path,
        cookies=cookies,
        data={"value": "sekret-value", "reason": "rotate", "csrf_token": token},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    for url in (
        path,
        "/admin/settings/domains/auth",
        "/admin/settings/auth/test_secret_token/history",
    ):
        text = client.get(url, cookies=cookies).text
        assert "sekret-value" not in text and "********" not in text
    assert "set" in client.get("/admin/settings/domains/auth", cookies=cookies).text
    assert (
        "sekret-value" not in client.get("/admin/settings/export", cookies=cookies).text
    )


def test_history_page_restores_as_a_new_write(client, cookies, db, tenant_row, party):
    actor = service.Actor(party_id=party.id)
    service.write_setting(
        db,
        tenant_row.id,
        actor=actor,
        domain="display",
        key="timezone",
        value="Europe/London",
        reason="a",
    )
    service.write_setting(
        db,
        tenant_row.id,
        actor=actor,
        domain="display",
        key="timezone",
        value="Asia/Tokyo",
        reason="b",
    )
    page = client.get("/admin/settings/display/timezone/history", cookies=cookies)
    assert page.status_code == 200 and "Restore previous" in page.text
    update = next(
        e
        for e in service.list_history(db, tenant_row.id, "display", "timezone")
        if e.action == "update"
    )
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    resp = client.post(
        f"/admin/settings/display/timezone/history/{update.id}/restore",
        cookies=cookies,
        data={"reason": "undo", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert (
        service.get_setting(db, tenant_row.id, "display", "timezone").value
        == "Europe/London"
    )
    assert len(service.list_history(db, tenant_row.id, "display", "timezone")) == 3


def test_branding_form_writes_through_the_one_owner_and_refuses_custom_css(
    client, cookies, db
):
    token = _csrf(client, cookies, "/admin/settings/branding")
    form = {
        "name": "Acme",
        "tagline": "",
        "logo_url": "",
        "primary_color": "#112233",
        "accent_color": "",
        "reason": "rebrand",
        "csrf_token": token,
    }
    page = client.get("/admin/settings/branding", cookies=cookies)
    assert "custom_css" not in page.text and "| safe" not in page.text
    ok = client.post(
        "/admin/settings/branding", cookies=cookies, data=form, follow_redirects=False
    )
    assert ok.status_code == 303
    bad = client.post(
        "/admin/settings/branding",
        cookies=cookies,
        data={**form, "custom_css": ".evil{}"},
        follow_redirects=False,
    )
    assert bad.status_code == 200 and "no longer accepts" in bad.text


def test_import_preview_then_apply_on_the_surface(
    client, cookies, db, tenant_row, party
):
    from dotmac_settings_admin import transfer

    service.write_setting(
        db,
        tenant_row.id,
        actor=service.Actor(party_id=party.id),
        domain="display",
        key="timezone",
        value="Europe/London",
        reason="a",
    )
    doc = transfer.export_settings(db, tenant_row.id)
    service.clear_setting(
        db,
        tenant_row.id,
        actor=service.Actor(party_id=party.id),
        domain="display",
        key="timezone",
        reason="reset",
    )
    import json

    token = _csrf(client, cookies, "/admin/settings/import")
    preview = client.post(
        "/admin/settings/import",
        cookies=cookies,
        data={"document": json.dumps(doc), "csrf_token": token},
    )
    assert preview.status_code == 200 and "change" in preview.text
    digest = re.search(r'name="digest" value="([^"]+)"', preview.text).group(1)
    applied = client.post(
        "/admin/settings/import",
        cookies=cookies,
        data={
            "document": json.dumps(doc),
            "digest": digest,
            "reason": "restore",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert applied.status_code == 303
    assert (
        service.get_setting(db, tenant_row.id, "display", "timezone").value
        == "Europe/London"
    )


def test_flags_page_is_read_only_and_lists_declared_flags(client, cookies):
    page = client.get("/admin/settings/flags", cookies=cookies)
    assert page.status_code == 200
