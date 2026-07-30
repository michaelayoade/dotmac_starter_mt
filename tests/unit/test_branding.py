"""TDD for the branding pipeline (Task 2).

`dotmac_kernel.branding.get_brand()` resolves deployment-static identity:
built-in defaults < brand.json < same-named env var (ported from
`dotmac_sub:app/services/branding_config.py`, same precedence/caching/
`BRAND_CONFIG_PATH` override).

`load_branding(db, tenant_id)` layers a per-tenant DB override
(`resolve_value(db, SettingDomain.branding, "ui_branding", ...)`, ported from
`dotmac_starter:app/services/branding.py::get_branding`) on top of the static
brand -- the split documented in `app/core/templating.py`: the `brand`
template global is the static part, `load_branding` is for routes that need
the tenant override.

`sanitize_branding_css` is a verbatim port of
`dotmac_starter:app/services/branding.py::sanitize_branding_css`; the test
cases below are ported from that repo's
`tests/test_branding_service.py::test_sanitize_branding_css_*`.
"""

from __future__ import annotations

import json

import pytest
from dotmac_kernel import branding as branding_module
from dotmac_kernel.branding import get_brand, load_branding, sanitize_branding_css
from dotmac_kernel.settings_models import SettingDomain

# Import for the side effect: registers branding/ui_branding into the
# resolver registry (app.features.settings.spec, imported transitively).
from app.features.settings import spec as _settings_spec  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_brand_cache():
    """`get_brand()` is `lru_cache`d for the process lifetime; tests that
    change `BRAND_CONFIG_PATH` or brand-prefixed env vars must clear it
    before AND after so cache state never leaks between tests."""
    branding_module.get_brand.cache_clear()
    yield
    branding_module.get_brand.cache_clear()


# ---------------------------------------------------------------------------
# get_brand(): defaults < brand.json < env
# ---------------------------------------------------------------------------


def test_get_brand_returns_repo_brand_json_values() -> None:
    """The real repo-root brand.json (no BRAND_CONFIG_PATH override) wins
    over the built-in Python defaults."""
    brand = get_brand()
    assert brand["name"] == "Starter"
    assert brand["primary_color"] == "#206a07"
    assert brand["accent_color"] == "#06b6d4"
    assert brand["support_email"] == "support@example.com"
    assert brand["app_url"] == "https://example.com"
    assert brand["tagline"]


def test_get_brand_falls_back_to_defaults_when_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setenv("BRAND_CONFIG_PATH", str(missing))
    brand = get_brand()
    assert brand["name"] == branding_module._DEFAULTS["name"]
    assert brand["primary_color"] == branding_module._DEFAULTS["primary_color"]


def test_get_brand_reads_brand_config_path_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    custom = tmp_path / "brand.json"
    custom.write_text(json.dumps({"BRAND_NAME": "Acme Co"}), encoding="utf-8")
    monkeypatch.setenv("BRAND_CONFIG_PATH", str(custom))
    brand = get_brand()
    assert brand["name"] == "Acme Co"
    # Untouched keys still fall back to built-in defaults, not the repo file.
    assert brand["primary_color"] == branding_module._DEFAULTS["primary_color"]


def test_get_brand_env_var_wins_over_brand_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    custom = tmp_path / "brand.json"
    custom.write_text(json.dumps({"BRAND_NAME": "From File"}), encoding="utf-8")
    monkeypatch.setenv("BRAND_CONFIG_PATH", str(custom))
    monkeypatch.setenv("BRAND_NAME", "From Env")
    brand = get_brand()
    assert brand["name"] == "From Env"


def test_get_brand_ignores_blank_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    custom = tmp_path / "brand.json"
    custom.write_text(json.dumps({"BRAND_NAME": "From File"}), encoding="utf-8")
    monkeypatch.setenv("BRAND_CONFIG_PATH", str(custom))
    monkeypatch.setenv("BRAND_NAME", "   ")
    brand = get_brand()
    assert brand["name"] == "From File"


def test_get_brand_malformed_json_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    bad = tmp_path / "brand.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("BRAND_CONFIG_PATH", str(bad))
    brand = get_brand()
    assert brand["name"] == branding_module._DEFAULTS["name"]


def test_get_brand_is_cached_until_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    custom = tmp_path / "brand.json"
    custom.write_text(json.dumps({"BRAND_NAME": "First"}), encoding="utf-8")
    monkeypatch.setenv("BRAND_CONFIG_PATH", str(custom))
    assert get_brand()["name"] == "First"

    custom.write_text(json.dumps({"BRAND_NAME": "Second"}), encoding="utf-8")
    # No cache reset -- stale value still returned.
    assert get_brand()["name"] == "First"

    branding_module.reset_brand_cache()
    assert get_brand()["name"] == "Second"


# ---------------------------------------------------------------------------
# load_branding(): static get_brand() + per-tenant DB override
# ---------------------------------------------------------------------------


def test_load_branding_returns_static_brand_when_no_override(db, tenant_row) -> None:
    branding = load_branding(db, tenant_row.id)
    assert branding["name"] == get_brand()["name"]
    assert branding["primary_color"] == get_brand()["primary_color"]


def test_load_branding_merges_tenant_override(db, tenant_row) -> None:
    from dotmac_kernel.settings_resolver import upsert_by_key

    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"name": "Acme Tenant Brand", "primary_color": "#112233"},
        tenant_id=tenant_row.id,
    )

    branding = load_branding(db, tenant_row.id)
    assert branding["name"] == "Acme Tenant Brand"
    assert branding["primary_color"] == "#112233"
    # Keys absent from the override still come from the static brand.
    assert branding["support_email"] == get_brand()["support_email"]


def test_load_branding_is_scoped_per_tenant(db, tenant_row) -> None:
    from dotmac_kernel.models import Tenant
    from dotmac_kernel.settings_resolver import upsert_by_key

    other = Tenant(slug="other", name="Other Tenant")
    db.add(other)
    db.flush()

    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"name": "Tenant A Brand"},
        tenant_id=tenant_row.id,
    )

    assert load_branding(db, tenant_row.id)["name"] == "Tenant A Brand"
    assert load_branding(db, other.id)["name"] == get_brand()["name"]


def test_load_branding_sanitizes_custom_css_in_override(db, tenant_row) -> None:
    from dotmac_kernel.settings_resolver import upsert_by_key

    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"custom_css": ".ok { color: red; }\n</style><script>alert(1)</script>"},
        tenant_id=tenant_row.id,
    )

    branding = load_branding(db, tenant_row.id)
    assert branding["custom_css"] == ""


def test_load_branding_ignores_unknown_override_keys(db, tenant_row) -> None:
    """Task 4 / F4 review follow-up: `load_branding` only merges keys in
    `_KNOWN_BRAND_KEYS` -- an unrecognized key in the stored `ui_branding`
    dict (stale shape, hand-crafted via the raw-JSON generic editor, a
    future field not yet wired) must never leak into the render context.
    """
    from dotmac_kernel.settings_resolver import upsert_by_key

    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"name": "Acme Tenant Brand", "evil_injected_key": "<script>alert(1)</script>"},
        tenant_id=tenant_row.id,
    )

    branding = load_branding(db, tenant_row.id)
    assert branding["name"] == "Acme Tenant Brand"
    assert "evil_injected_key" not in branding


def test_known_brand_keys_matches_the_branding_editor_form_fields() -> None:
    """Pin the allowlist to the exact fields `app.features.settings.web`'s
    `_branding_form`/`branding_submit` expose -- if the editor ever grows a
    field, this test forces a conscious allowlist update in the same task.
    """
    assert branding_module._KNOWN_BRAND_KEYS == frozenset(
        {"name", "tagline", "logo_url", "primary_color", "accent_color", "custom_css"}
    )


# ---------------------------------------------------------------------------
# get_request_branding(): request-scoped, memoized resolution (Task 4 / F4)
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, tenant=None):
        self.tenant = tenant


class _FakeRequest:
    def __init__(self, tenant=None):
        self.state = _FakeState(tenant=tenant)


def test_get_request_branding_resolves_tenant_override(db, tenant_row) -> None:
    from dotmac_kernel.branding import get_request_branding
    from dotmac_kernel.settings_resolver import upsert_by_key

    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"name": "Acme Tenant Brand"},
        tenant_id=tenant_row.id,
    )

    request = _FakeRequest(tenant=tenant_row)
    branding = get_request_branding(request, db)
    assert branding["name"] == "Acme Tenant Brand"
    assert request.state.branding is branding


def test_get_request_branding_falls_back_to_static_when_no_tenant(db) -> None:
    """No tenant on `request.state` -- platform hosts, unresolved-tenant
    error contexts -- falls back to the deployment-static `get_brand()`,
    never a DB read."""
    from dotmac_kernel.branding import get_request_branding

    request = _FakeRequest(tenant=None)
    branding = get_request_branding(request, db)
    assert branding == get_brand()
    assert request.state.branding == get_brand()


def test_get_request_branding_memoizes_one_load_branding_call_per_request(
    monkeypatch: pytest.MonkeyPatch, db, tenant_row
) -> None:
    """Call-count spy: `load_branding` must be called exactly once per
    request even if `get_request_branding` is invoked more than once on the
    same request (e.g. `require_web_auth` warms the cache, a route calls it
    again) -- the second call must return the cached
    `request.state.branding` without a second DB read."""
    from dotmac_kernel import branding as branding_mod

    calls: list[int] = []
    real_load_branding = branding_mod.load_branding

    def _spy_load_branding(db_arg, tenant_id):
        calls.append(1)
        return real_load_branding(db_arg, tenant_id)

    monkeypatch.setattr(branding_mod, "load_branding", _spy_load_branding)

    request = _FakeRequest(tenant=tenant_row)
    first = branding_mod.get_request_branding(request, db)
    second = branding_mod.get_request_branding(request, db)

    assert len(calls) == 1
    assert first is second


# ---------------------------------------------------------------------------
# sanitize_branding_css -- verbatim port, test cases ported from
# dotmac_starter:tests/test_branding_service.py
# ---------------------------------------------------------------------------


def test_sanitize_branding_css_strips_dangerous_patterns() -> None:
    css = """
    .ok { color: red; }
    @import url("https://evil.example/x.css");
    .js { background: url("javascript:alert(1)"); }
    .expr { width: expression(alert(1)); }
    .legacy { behavior: url(#default#VML); }
    .data { background-image: url("data:text/html;base64,QQ=="); }
    .cdn { background-image: url("https://cdn.example.com/bg.png"); }
    .relative { background-image: url("/img/bg.png"); }
    """

    sanitized = sanitize_branding_css(css)

    assert ".ok { color: red; }" in sanitized
    assert (
        '.cdn { background-image: url("https://cdn.example.com/bg.png"); }' in sanitized
    )
    assert '.relative { background-image: url("/img/bg.png"); }' in sanitized
    assert "@import" not in sanitized
    assert "javascript:" not in sanitized.lower()
    assert "expression(" not in sanitized.lower()
    assert "behavior:" not in sanitized.lower()
    assert "data:text" not in sanitized.lower()


def test_sanitize_branding_css_rejects_angle_brackets() -> None:
    css = ".ok { color: red; }\n</style><script>alert(1)</script>"
    assert sanitize_branding_css(css) == ""


def test_sanitize_branding_css_none_returns_empty_string() -> None:
    assert sanitize_branding_css(None) == ""


def test_sanitize_branding_css_blank_returns_empty_string() -> None:
    assert sanitize_branding_css("   ") == ""


# ---------------------------------------------------------------------------
# get_request_branding() error handling (Task 4 review: no error-page recursion)
# ---------------------------------------------------------------------------


def test_load_branding_failure_yields_static_branded_error_page(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, db, tenant_row
) -> None:
    """Regression test: when load_branding raises during an authenticated
    request, the error-page render must fall back to the static brand
    (NEVER call get_request_branding again, which would recurse indefinitely
    trying to brand an error while branding fails). The error is logged and
    the request yields a branded 500 page.

    Task 4 review finding: render_error must read request.state only, never
    call get_request_branding (reads only the static brand via the Jinja2
    global). This test pins that no-recursion invariant.
    """
    from collections.abc import Generator

    from dotmac_kernel.deps import get_db
    from dotmac_kernel.errors import register_error_handlers
    from dotmac_kernel.models import (
        AuthSession,
        Party,
        PartyPerson,
        PartyRole,
        PartyType,
        Role,
    )
    from dotmac_kernel.security import hash_token, issue_access_token
    from dotmac_kernel.web_deps import require_web_auth
    from fastapi import Depends, FastAPI, Request
    from fastapi.testclient import TestClient

    # Set up admin party with token.
    party = Party(
        tenant_id=tenant_row.id,
        party_type=PartyType.person,
        display_name="Admin User",
        email="admin@acme.test",
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="Admin", last_name="User"))
    role = Role(tenant_id=tenant_row.id, slug="admin", name="Admin")
    db.add(role)
    db.flush()
    db.add(PartyRole(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
    db.flush()

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

    # Mock load_branding to raise.
    from dotmac_kernel import branding as branding_mod

    monkeypatch.setattr(
        branding_mod,
        "load_branding",
        lambda db_arg, tenant_id: (_ for _ in ()).throw(
            RuntimeError("Database connection failed")
        ),
    )

    # Build test app with error handlers and a guarded route.
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/protected")
    def protected(auth: dict = Depends(require_web_auth)) -> dict:
        return {"party_id": str(auth["party"].id)}

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[None, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db

    client = TestClient(app, raise_server_exceptions=False)

    # Make a request. require_web_auth will call get_request_branding, which
    # will call load_branding, which will raise. The exception handler must
    # catch it and fall back to the static brand (no recursion).
    with caplog.at_level("ERROR"):
        resp = client.get(
            "/protected",
            cookies={"access_token": token},
            headers={"Accept": "text/html, */*"},
        )

    # Assert: 500 error, HTML response branded successfully (not a crash/recursion).
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("text/html")
    # The error page renders the 500 template successfully, proving that
    # render_error fell back to the static brand (from Jinja2 globals) and
    # did NOT call get_request_branding again (which would have raised again,
    # causing recursion). Proof: the 500 template content is present.
    assert "500" in resp.text  # Error page rendered (status number present).
    assert "Server error" in resp.text  # Standard error text from 500.html.
    assert "Traceback" not in resp.text  # No crash/stack trace.

    # Assert: the exception was logged.
    assert "Database connection failed" in caplog.text
