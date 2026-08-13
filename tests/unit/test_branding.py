"""TDD for the branding pipeline (Task 2).

`dotmac_kernel.branding.get_brand()` resolves deployment-static identity:
built-in defaults < brand.json < same-named env var (ported from
`dotmac_sub:app/services/branding_config.py`, same precedence/caching/
`BRAND_CONFIG_PATH` override).

`load_branding(db, tenant_id)` layers a per-tenant DB override
(`resolve_value(db, SettingDomain.branding, "ui_branding", ...)`, ported from
`dotmac_starter:app/services/branding.py::get_branding`) on top of the static
brand -- the split documented in `dotmac_kernel/templating.py`: the `brand`
template global is the static part, `load_branding` is for routes that need
the tenant override.

The sanitizer these tests once covered is GONE (ADR-0006 D8, 2026-08-13):
tenant-supplied CSS is refused rather than scrubbed. What replaced it is
proved below — a write is refused, a legacy stored value is inert, and the
value survives for inventory. The old
cases below are ported from that repo's
`tests/test_branding_service.py` sanitizer cases are deleted with it.
"""

from __future__ import annotations

import json

import pytest
from dotmac_kernel import branding as branding_module
from dotmac_kernel.branding import (
    RETIRED_BRAND_KEYS,
    get_brand,
    load_branding,
    reject_retired_brand_keys,
)
from dotmac_kernel.exceptions import BadRequestError
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


_HOSTILE_CSS = ".ok{color:red}\n</style><script>alert(1)</script>"


def _stored_override(db, tenant_row) -> dict:
    """The stored `ui_branding` dict, exactly as the generic settings surface
    returns it — legacy keys included. That surface IS the inventory path; the
    branding module deliberately grows no bespoke export API for it."""
    from dotmac_kernel.settings_resolver import resolve_value

    value = resolve_value(
        db, SettingDomain.branding, "ui_branding", tenant_id=tenant_row.id, default={}
    )
    return dict(value) if isinstance(value, dict) else {}


def _store_legacy_css(db, tenant_row) -> None:
    """Plant a pre-retirement row.

    `upsert_by_key` is the low-level resolver writer used by seeds and
    fixtures; it is NOT reachable from a request. Every request-facing write
    goes through `settings_service.update_setting`, which is where the refusal
    lives, so this plants the row a pre-retirement deployment would have left
    behind without pretending the refusal can be bypassed from outside.
    """
    from dotmac_kernel.settings_resolver import upsert_by_key

    upsert_by_key(
        db,
        SettingDomain.branding,
        "ui_branding",
        {"name": "Legacy Co", "custom_css": _HOSTILE_CSS},
        tenant_id=tenant_row.id,
    )


def test_a_legacy_custom_css_value_is_never_merged_into_branding(db, tenant_row):
    """Inert, not sanitized. `custom_css` is outside the allowlist entirely."""
    _store_legacy_css(db, tenant_row)

    branding = load_branding(db, tenant_row.id)

    assert "custom_css" not in branding
    assert branding["name"] == "Legacy Co", "other keys must still merge"
    assert not any("script" in str(v).lower() for v in branding.values())


def test_a_legacy_value_survives_for_inventory_and_export(db, tenant_row) -> None:
    """Retiring the feature must not destroy the evidence needed to migrate it."""
    _store_legacy_css(db, tenant_row)

    assert _stored_override(db, tenant_row)["custom_css"] == _HOSTILE_CSS


def test_no_legacy_value_means_nothing_to_inventory(db, tenant_row) -> None:
    assert "custom_css" not in _stored_override(db, tenant_row)


def test_a_legacy_row_does_not_break_the_rest_of_the_tenants_branding(
    db, tenant_row
) -> None:
    """The regression CI caught: retirement must not degrade a whole setting.

    Enforcing this through `SettingSpec.validator` looked right and was wrong —
    a validator runs on the READ path too, so a legacy row made the entire
    `ui_branding` value fail validation and resolve to its default, silently
    blanking the tenant's name, tagline and colours. A stored legacy value is
    valid data the reader ignores, not an invalid setting.
    """
    _store_legacy_css(db, tenant_row)

    branding = load_branding(db, tenant_row.id)

    assert branding["name"] == "Legacy Co", "the rest of the override must survive"
    assert "custom_css" not in branding
    assert _stored_override(db, tenant_row)["custom_css"] == _HOSTILE_CSS


def test_a_write_naming_a_retired_key_is_refused_not_dropped() -> None:
    """Silently ignoring it would train an operator to think their CSS is live."""
    with pytest.raises(BadRequestError, match="no longer accepts"):
        reject_retired_brand_keys({"name": "Fine", "custom_css": ".x{}"})

    reject_retired_brand_keys({"name": "Fine", "primary_color": "#112233"})
    reject_retired_brand_keys({})
    reject_retired_brand_keys("not a dict")


def test_the_write_guard_compares_domains_by_value_not_identity(db, tenant_row) -> None:
    """The defect this branch shipped once: `is` instead of `==`.

    `SettingDomain` is an open `str` subclass (ADR-0008), and
    `SettingDomainRegistry.require` returns a FRESH instance, so the domain the
    service holds is EQUAL to but never IDENTICAL with the module-level
    constant. An identity check made the refusal dead code while every test
    still passed.

    This drives `update_setting` the way a route does — through a domain STRING,
    which is what `require` turns into that fresh instance — and additionally
    pins the property directly so the reason is legible without reading the
    service.
    """
    from app.features.settings import service as settings_service

    fresh = SettingDomain("branding")
    assert fresh == SettingDomain.branding
    assert fresh is not SettingDomain.branding, (
        "if this ever becomes an interned singleton the guard below still holds, "
        "but the regression it protects against would no longer be reachable"
    )

    with pytest.raises(BadRequestError, match="no longer accepts"):
        settings_service.update_setting(
            db,
            tenant_row,
            "branding",
            "ui_branding",
            {"name": "Acme", "custom_css": ".evil{}"},
        )


def test_a_permitted_branding_write_still_succeeds(db, tenant_row) -> None:
    """Sensitivity proof: the guard must not refuse everything."""
    from app.features.settings import service as settings_service

    settings_service.update_setting(
        db, tenant_row, "branding", "ui_branding", {"name": "Acme", "tagline": "Hi"}
    )

    assert load_branding(db, tenant_row.id)["name"] == "Acme"


def test_every_retired_key_is_refused(hidden_key=None) -> None:
    """Sensitivity proof: the check follows the set, not one hardcoded name."""
    assert RETIRED_BRAND_KEYS, "an empty retired set would make the guard vacuous"
    for key in RETIRED_BRAND_KEYS:
        with pytest.raises(BadRequestError):
            reject_retired_brand_keys({key: "anything"})


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

    `custom_css` left this set on 2026-08-13 (ADR-0006 D8). The two sets are
    asserted DISJOINT as well, so a retired key can never be quietly re-added
    to the allowlist while still claiming to be refused.
    """
    assert branding_module._KNOWN_BRAND_KEYS == frozenset(
        {"name", "tagline", "logo_url", "primary_color", "accent_color"}
    )
    assert not (branding_module._KNOWN_BRAND_KEYS & RETIRED_BRAND_KEYS)


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
        PartyRoleGrant,
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
    db.add(PartyRoleGrant(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
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
