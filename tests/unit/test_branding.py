"""TDD for the branding pipeline (Task 2).

`app.core.branding.get_brand()` resolves deployment-static identity:
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

from app.core import branding as branding_module
from app.core.branding import get_brand, load_branding, sanitize_branding_css
from app.core.settings_models import SettingDomain

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
    from app.core.settings_resolver import upsert_by_key

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
    from app.core.models import Tenant
    from app.core.settings_resolver import upsert_by_key

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
    from app.core.settings_resolver import upsert_by_key

    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"custom_css": ".ok { color: red; }\n</style><script>alert(1)</script>"},
        tenant_id=tenant_row.id,
    )

    branding = load_branding(db, tenant_row.id)
    assert branding["custom_css"] == ""


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
