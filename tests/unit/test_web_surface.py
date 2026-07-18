"""TDD for phase 2b.1 Task 1: the manifest capability model — `web_routers`/
`nav` on `FeatureManifest`, and the `WEB_ENABLED` surface switch (findings
F1, F5).

F1 pin: the verified repro on main was `DISABLED_FEATURES=web` leaving 30
`/admin` routes mounted — `web` only ever owned the dashboard shell, so
disabling it was never going to be "API-only". This file proves the ACTUAL
API-only switch instead: `WEB_ENABLED=false` must mount ZERO `/admin`
routes and NO `/static` mount, while the JSON API (`/health`, `/auth/login`,
...) stays fully reachable. A fresh subprocess is required for the
WEB_ENABLED cases — `app.main` builds the module-level `app` object at
IMPORT time from the process-global `settings` singleton, so an in-process
monkeypatch can't retroactively un-mount routes already registered on the
shared `app` (same reasoning/pattern as
`tests/unit/test_lifespan_seed.py::test_import_app_main_works_with_settings_feature_disabled`).

F5 pin: `DISABLED_FEATURES=custom_fields` must not leave a dead sidebar
link or a broken fragment — the party detail page's values-panel div (the
literal 404 repro: an unconditional `hx-get`) must not render at all when
the feature is off, and the sidebar must not link to it either.

Nav derivation: `nav_items` (the sidebar's ONLY source, see
`templates/components/sidebar.html`) is built entirely from ENABLED
manifests' `.nav` sequences by `app.core.templating.install_surface_globals`
— proven here by injecting a temporary manifest and observing its nav item
appear, then disappear when that manifest is dropped/disabled.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.errors import register_error_handlers
from app.core.features import FeatureManifest, NavItem, load_manifests
from app.core.models import Party, PartyType, Tenant
from app.core.templating import install_surface_globals, render
from app.core.web_deps import require_web_auth
from app.features import FEATURE_MODULES
from app.features.parties.web import router as parties_web_router

_PLACEHOLDER_DATABASE_URL = "postgresql+psycopg://x:x@127.0.0.1:59999/x"

# ---------------------------------------------------------------------------
# F1 pin: WEB_ENABLED=false -> zero /admin routes, no /static mount, JSON
# routes intact.
# ---------------------------------------------------------------------------

_INSPECT_APP = textwrap.dedent(
    """
    import json

    from fastapi.routing import APIRoute
    from starlette.routing import Mount

    import app.main as m

    admin_routes = sorted(
        r.path
        for r in m.app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/admin")
    )
    static_mounts = sorted(
        r.path for r in m.app.routes if isinstance(r, Mount) and r.name == "static"
    )
    has_health = any(
        isinstance(r, APIRoute) and r.path == "/health" for r in m.app.routes
    )
    has_login_api = any(
        isinstance(r, APIRoute) and r.path == "/auth/login" for r in m.app.routes
    )
    print(
        json.dumps(
            {
                "admin_routes": admin_routes,
                "static_mounts": static_mounts,
                "has_health": has_health,
                "has_login_api": has_login_api,
            }
        )
    )
    """
)


def _inspect_app(env_overrides: dict[str, str]) -> dict:
    env = {
        **os.environ,
        "DATABASE_URL": _PLACEHOLDER_DATABASE_URL,
        "SEED_ON_STARTUP": "false",
        **env_overrides,
    }
    result = subprocess.run(  # noqa: S603 # nosec B603 — fixed argv, no shell, no untrusted input
        [sys.executable, "-c", _INSPECT_APP],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_web_enabled_false_yields_zero_admin_routes_and_no_static_mount() -> None:
    """The F1 repro, inverted: `WEB_ENABLED=false` is the real API-only
    switch (`DISABLED_FEATURES=web` never was — it only ever dropped the
    dashboard shell, see `app/features/web/feature.py`'s docstring)."""
    data = _inspect_app({"WEB_ENABLED": "false"})
    assert data["admin_routes"] == [], data["admin_routes"]
    assert data["static_mounts"] == []
    assert data["has_health"] is True
    assert data["has_login_api"] is True


def test_web_enabled_default_true_still_mounts_admin_routes_and_static() -> None:
    """Control for the pin above — a harness bug that always reports zero
    routes would make the false-case pass for the wrong reason."""
    data = _inspect_app({})
    assert len(data["admin_routes"]) > 0
    assert data["static_mounts"] == ["/static"]
    assert data["has_health"] is True
    assert data["has_login_api"] is True


def test_disabled_features_web_still_leaves_other_admin_routes_mounted() -> None:
    """Documents the (intentional, now-clarified) behavior F1 used to
    conflate: `DISABLED_FEATURES=web` drops ONLY `GET /admin` (the dashboard
    shell) — every other feature's own `/admin/*` routes stay mounted. This
    is not a bug; `WEB_ENABLED=false` (tested above) is the real
    API-only switch."""
    data = _inspect_app({"DISABLED_FEATURES": "web"})
    assert "/admin" not in data["admin_routes"]
    assert any(p.startswith("/admin/parties") for p in data["admin_routes"])


def test_combined_case_web_disabled_plus_feature_disabled() -> None:
    """Task 1 Task 3 combined-case pin: `WEB_ENABLED=false` +
    `DISABLED_FEATURES=custom_fields` together must yield zero admin routes,
    JSON API intact, no error. Proves both switches compose correctly."""
    data = _inspect_app({"WEB_ENABLED": "false", "DISABLED_FEATURES": "custom_fields"})
    assert data["admin_routes"] == [], data["admin_routes"]
    assert data["static_mounts"] == []
    assert data["has_health"] is True
    assert data["has_login_api"] is True


# ---------------------------------------------------------------------------
# F5 pin: DISABLED_FEATURES=custom_fields -> party detail 200 without the
# values-panel div; sidebar lacks the Custom Fields entry.
# ---------------------------------------------------------------------------


def _fake_party() -> SimpleNamespace:
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000000",
        display_name="Jane Doe",
        party_type=PartyType.person,
        email="jane@example.com",
        person_profile=SimpleNamespace(first_name="Jane", last_name="Doe"),
        organization_profile=None,
    )


@pytest.fixture()
def parties_client(db: Session, tenant_row: Tenant) -> TestClient:
    """Only the parties web router mounted — the values-panel div's content
    is never actually fetched by this test (that's `custom_fields`'s own
    route/fragment, proven elsewhere); this only exercises the party
    detail page's own conditional rendering of the composing div."""
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(parties_web_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    # Bypass real cookie auth — this test is about the enabled_features
    # conditional, not the auth seam (already covered by test_parties_web.py).
    app.dependency_overrides[require_web_auth] = lambda: {"party": None}
    return TestClient(app, raise_server_exceptions=False)


def test_party_detail_hides_values_panel_when_custom_fields_disabled(
    parties_client: TestClient, party_row: Party
) -> None:
    install_surface_globals(
        load_manifests(FEATURE_MODULES), disabled={"custom_fields"}, web_enabled=True
    )
    resp = parties_client.get(f"/admin/parties/{party_row.id}")
    assert resp.status_code == 200
    assert 'id="custom-fields-panel"' not in resp.text
    # The hx-get URL itself must not render either — that's the literal F5
    # repro (an unconditional hx-get hitting a 404'd route). The template's
    # explanatory HTML comment mentions "values-panel" in prose, so assert
    # on the actual attribute value, not the bare substring.
    assert 'hx-get="/admin/custom-fields/party/' not in resp.text


def test_party_detail_shows_values_panel_when_custom_fields_enabled(
    parties_client: TestClient, party_row: Party
) -> None:
    install_surface_globals(
        load_manifests(FEATURE_MODULES), disabled=set(), web_enabled=True
    )
    resp = parties_client.get(f"/admin/parties/{party_row.id}")
    assert resp.status_code == 200
    assert 'id="custom-fields-panel"' in resp.text


def _build_admin_shell_app() -> FastAPI:
    app = FastAPI()

    @app.get("/admin")
    def index(request: Request):
        return render(request, "layouts/admin.html", {"page_title": "Test"})

    return app


def test_sidebar_omits_nav_entry_for_disabled_feature() -> None:
    install_surface_globals(
        load_manifests(FEATURE_MODULES), disabled={"custom_fields"}, web_enabled=True
    )
    client = TestClient(_build_admin_shell_app())
    resp = client.get("/admin")
    assert 'href="/admin/custom-fields"' not in resp.text


def test_sidebar_includes_nav_entry_when_feature_enabled() -> None:
    install_surface_globals(
        load_manifests(FEATURE_MODULES), disabled=set(), web_enabled=True
    )
    client = TestClient(_build_admin_shell_app())
    resp = client.get("/admin")
    assert 'href="/admin/custom-fields"' in resp.text


# ---------------------------------------------------------------------------
# Nav derivation: nav_items comes ENTIRELY from manifests, no parallel list.
# ---------------------------------------------------------------------------


def test_nav_items_derive_from_manifest_nav_sequences() -> None:
    temp = FeatureManifest(
        name="temp_feature",
        core=False,
        nav=[NavItem("Temp Link", "/admin/temp")],
    )
    install_surface_globals([temp], disabled=set(), web_enabled=True)
    assert templates_nav_items() == (
        NavItem("Temp Link", "/admin/temp", feature="temp_feature"),
    )


def test_nav_items_reflect_disabling_the_temp_manifests_feature() -> None:
    temp = FeatureManifest(
        name="temp_feature",
        core=False,
        nav=[NavItem("Temp Link", "/admin/temp")],
    )
    install_surface_globals([temp], disabled={"temp_feature"}, web_enabled=True)
    assert templates_nav_items() == ()


def test_nav_items_empty_when_web_disabled() -> None:
    temp = FeatureManifest(
        name="temp_feature",
        core=False,
        nav=[NavItem("Temp Link", "/admin/temp")],
    )
    install_surface_globals([temp], disabled=set(), web_enabled=False)
    assert templates_nav_items() == ()


def templates_nav_items() -> tuple[NavItem, ...]:
    from app.core.templating import templates

    return templates.env.globals["nav_items"]
