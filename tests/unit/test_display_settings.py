"""Display settings domain: spec registration + validator behavior, plus
(Task 2) the per-request resolution + Jinja filters + end-to-end page
render.

Write path (update_setting/validate_spec_value) rejects loudly; read path
(resolve_value) silently degrades a bad stored row to the spec default —
same split the resolver already applies to allowed/min/max violations.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.features.settings.spec  # noqa: F401 — registration side effect
from app.core.deps import get_db
from app.core.display import DisplaySettings
from app.core.errors import register_error_handlers
from app.core.exceptions import BadRequestError
from app.core.models import (
    Party,
    PartyPerson,
    PartyRole,
    PartyType,
    Role,
    Tenant,
    UserCredential,
)
from app.core.security import hash_password
from app.core.settings_models import SettingDomain
from app.core.settings_resolver import (
    get_spec,
    resolve_value,
    upsert_by_key,
    validate_spec_value,
)
from app.core.templating import templates
from app.features.auth.web import router as auth_web_router
from app.features.rbac.web import router as rbac_web_router

PASSWORD = "correct horse battery staple"


class TestDisplaySpecs:
    def test_display_specs_registered_with_expected_defaults(self) -> None:
        assert get_spec(SettingDomain.display, "timezone").default == "UTC"
        assert get_spec(SettingDomain.display, "date_format").default == "%Y-%m-%d"
        assert (
            get_spec(SettingDomain.display, "datetime_format").default
            == "%Y-%m-%d %H:%M"
        )

    def test_timezone_write_rejects_unknown_iana_name(self) -> None:
        spec = get_spec(SettingDomain.display, "timezone")
        with pytest.raises(BadRequestError):
            validate_spec_value(spec, "Mars/Olympus_Mons")

    def test_timezone_write_accepts_real_iana_name(self) -> None:
        spec = get_spec(SettingDomain.display, "timezone")
        assert validate_spec_value(spec, "Europe/London") == "Europe/London"

    def test_format_write_rejects_null_byte(self) -> None:
        spec = get_spec(SettingDomain.display, "date_format")
        with pytest.raises(BadRequestError):
            validate_spec_value(spec, "%Y\x00")

    def test_format_write_rejects_directive_free_string(self) -> None:
        spec = get_spec(SettingDomain.display, "date_format")
        with pytest.raises(BadRequestError):
            validate_spec_value(spec, "yyyy-mm-dd")  # no % directive

    def test_format_write_accepts_strftime_pattern(self) -> None:
        spec = get_spec(SettingDomain.display, "datetime_format")
        assert validate_spec_value(spec, "%d %b %Y %H:%M") == "%d %b %Y %H:%M"

    def test_format_write_accepts_default_date_pattern(self) -> None:
        spec = get_spec(SettingDomain.display, "date_format")
        assert validate_spec_value(spec, "%Y-%m-%d") == "%Y-%m-%d"

    def test_format_write_rejects_platform_specific_directive(self) -> None:
        """`%Q` is not a documented Python strftime directive. On glibc,
        `datetime.strftime` silently passes unknown directives through as
        literal text (no exception) — the old `"%" in fmt` + probe-render
        check wrongly accepted this. The tokenizer must reject it explicitly.
        """
        spec = get_spec(SettingDomain.display, "date_format")
        with pytest.raises(BadRequestError):
            validate_spec_value(spec, "%Q")

    def test_format_write_rejects_escape_only_string(self) -> None:
        """`%%` is the literal-`%` escape, not a real directive — a format
        string consisting ONLY of `%%` renders fine (no exception) but
        contains zero actual strftime directives, so it must be rejected.
        """
        spec = get_spec(SettingDomain.display, "date_format")
        with pytest.raises(BadRequestError):
            validate_spec_value(spec, "%%")

    def test_format_write_rejects_trailing_percent(self) -> None:
        """A dangling `%` at the end of the string (no directive character
        following it) is platform-dependent under the old probe-render
        check (glibc renders it as a literal `%` with no exception) — must
        be rejected explicitly instead of relying on the platform libc.
        """
        spec = get_spec(SettingDomain.display, "date_format")
        with pytest.raises(BadRequestError):
            validate_spec_value(spec, "%Y-%m-%d %")

    def test_format_write_accepts_escape_plus_real_directive(self) -> None:
        """`%%` (escape) alongside a real directive is valid — the
        "at least one real directive" rule only excludes strings with
        ZERO real directives, not strings that merely contain an escape."""
        spec = get_spec(SettingDomain.display, "date_format")
        assert validate_spec_value(spec, "100%% %Y") == "100%% %Y"

    def test_read_path_degrades_bad_stored_timezone_to_default(
        self, db, tenant_row
    ) -> None:
        # Bypass write validation (legacy/hand-edited row) via direct upsert.
        upsert_by_key(
            db,
            SettingDomain.display,
            "timezone",
            "Not/AZone",
            tenant_id=tenant_row.id,
        )
        assert (
            resolve_value(
                db, SettingDomain.display, "timezone", tenant_id=tenant_row.id
            )
            == "UTC"
        )


def _fake_request_ctx(display: DisplaySettings | None) -> dict:
    state = SimpleNamespace()
    if display is not None:
        state.display = display
    return {"request": SimpleNamespace(state=state)}


class TestLocalFilters:
    def test_local_datetime_converts_to_request_timezone_and_format(self) -> None:
        tmpl = templates.env.from_string("{{ value | local_datetime }}")
        display = DisplaySettings(
            timezone=ZoneInfo("America/New_York"),
            date_format="%Y-%m-%d",
            datetime_format="%d %b %Y %H:%M",
        )
        value = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
        out = tmpl.render(value=value, **_fake_request_ctx(display))
        assert out == "18 Jul 2026 08:00"  # UTC-4 in July (EDT)

    def test_local_datetime_treats_naive_as_utc(self) -> None:
        # SQLite (unit DB) returns naive datetimes; models store UTC.
        tmpl = templates.env.from_string("{{ value | local_datetime }}")
        display = DisplaySettings(
            timezone=ZoneInfo("Europe/London"),
            date_format="%Y-%m-%d",
            datetime_format="%H:%M",
        )
        out = tmpl.render(
            value=datetime(2026, 7, 18, 12, 0), **_fake_request_ctx(display)
        )
        assert out == "13:00"  # BST = UTC+1

    def test_local_datetime_falls_back_when_state_not_warmed(self) -> None:
        # Error pages / unauthenticated renders never resolved display —
        # the filter must not raise and must use spec defaults (UTC).
        tmpl = templates.env.from_string("{{ value | local_datetime }}")
        out = tmpl.render(
            value=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
            **_fake_request_ctx(None),
        )
        assert out == "2026-07-18 12:00"

    def test_local_datetime_of_none_is_empty(self) -> None:
        tmpl = templates.env.from_string("{{ value | local_datetime }}")
        assert tmpl.render(value=None, **_fake_request_ctx(None)) == ""

    def test_local_date_uses_date_format(self) -> None:
        tmpl = templates.env.from_string("{{ value | local_date }}")
        display = DisplaySettings(
            timezone=ZoneInfo("UTC"),
            date_format="%d/%m/%Y",
            datetime_format="%Y-%m-%d %H:%M",
        )
        out = tmpl.render(
            value=datetime(2026, 7, 18, 23, 30, tzinfo=UTC),
            **_fake_request_ctx(display),
        )
        assert out == "18/07/2026"


# ---------------------------------------------------------------------------
# End-to-end page test — grants page renders `created_at` through the
# tenant's resolved display settings. App-builder pattern copied from
# tests/unit/test_settings_web.py / test_rbac_web.py: bare FastAPI() +
# register_error_handlers + the real auth web router (login) + rbac web
# router (the grants page lives there, at GET/POST /admin/role-grants —
# NOT /admin/rbac/grants, which doesn't exist; see app/features/rbac/web.py)
# + a thin `_inject_tenant` middleware standing in for
# TenantResolverMiddleware + `get_db` overridden to the in-memory `db`
# fixture. `require_web_auth` is not overridden — the real cookie-auth seam
# is what warms `request.state.display` (Step 5), so this test also proves
# that wiring end-to-end.
# ---------------------------------------------------------------------------


@pytest.fixture()
def provisioned_admin(db: Session, tenant_row: Tenant) -> dict:
    """A provisioned admin — party + person + credential + "admin" role
    grant, built directly on core models; registration no longer grants any
    role (Task 2). The PartyRole grant row created here is also what the
    grants page's recent-grants list renders, so no separate
    rbac.assign_role call is needed.
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
    db.add(PartyRole(tenant_id=tenant_row.id, party_id=party.id, role_id=role.id))
    db.commit()
    return {"email": party.email, "party_id": party.id}


@pytest.fixture()
def web_client(db: Session, tenant_row: Tenant) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_web_router)
    app.include_router(rbac_web_router)

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


class TestGrantsPageUsesTenantDisplay:
    def test_grants_page_renders_created_at_in_tenant_timezone(
        self,
        web_client: TestClient,
        db: Session,
        tenant_row: Tenant,
        provisioned_admin: dict,
    ) -> None:
        upsert_by_key(
            db,
            SettingDomain.display,
            "timezone",
            "America/New_York",
            tenant_id=tenant_row.id,
        )
        upsert_by_key(
            db,
            SettingDomain.display,
            "datetime_format",
            "%d %b %Y %H:%M",
            tenant_id=tenant_row.id,
        )
        db.commit()
        token = _login(web_client, provisioned_admin["email"])
        resp = web_client.get("/admin/role-grants", cookies={"access_token": token})
        assert resp.status_code == 200
        # Test files may query directly (thin-wrapper rule scopes to app/).
        grant_created = db.scalars(
            select(PartyRole.created_at).order_by(PartyRole.created_at.desc())
        ).first()
        assert grant_created is not None
        expected = (
            grant_created.replace(tzinfo=UTC)
            .astimezone(ZoneInfo("America/New_York"))
            .strftime("%d %b %Y %H:%M")
        )
        assert expected in resp.text
