"""TDD for the settings admin API (Task 5): GET/PUT under `/settings`.

App-builder pattern from `tests/unit/test_errors.py` (bare `FastAPI()` +
`register_error_handlers`), extended to exercise the real guarded router on
in-memory SQLite: `get_db` and `require_user_auth` are overridden via
`app.dependency_overrides` (both are plain module-level callables imported
from `app.core.deps`, so overriding them here overrides every
`Depends(require_role("admin"))` closure that transitively depends on them
too — there is no separate "override require_role" hook, since each
`require_role(...)` call site produces its own closure object). A thin
middleware stands in for `TenantResolverMiddleware`, setting
`request.state.tenant` directly — both `Depends(require_tenant)` and
`require_role`'s internal direct call to `require_tenant(request)` read that
same attribute.

RLS doesn't exist on SQLite (see `tests/unit/conftest.py`), so this only
proves the API logic (spec merge, source labeling, secret masking,
validation-error mapping) — tenancy correctness is the job of
`tests/test_settings_isolation.py`'s Postgres canaries.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import settings_resolver as sr
from app.core.audit import AuditEvent
from app.core.deps import get_db, require_user_auth
from app.core.errors import register_error_handlers
from app.core.models import Person, PersonRole, Role, Tenant
from app.core.settings_models import SettingDomain, SettingValueType

# Import for the side effect: registers custom_fields/max_per_entity,
# branding/ui_branding, audit/retention_days.
from app.features.settings import spec as _settings_spec  # noqa: F401
from app.features.settings.router import router as settings_router


@pytest.fixture()
def admin_person(db: Session, tenant_row: Tenant) -> Person:
    person = Person(
        tenant_id=tenant_row.id,
        email="admin@acme.test",
        first_name="Admin",
        last_name="User",
    )
    db.add(person)
    db.flush()
    role = Role(tenant_id=tenant_row.id, slug="admin", name="Admin")
    db.add(role)
    db.flush()
    db.add(PersonRole(tenant_id=tenant_row.id, person_id=person.id, role_id=role.id))
    db.flush()
    return person


@pytest.fixture()
def app_client(db: Session, tenant_row: Tenant, admin_person: Person) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(settings_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[require_user_auth] = lambda: admin_person

    return TestClient(app)


def _entry(body: list[dict], key: str) -> dict:
    matches = [item for item in body if item["key"] == key]
    assert len(matches) == 1, f"expected exactly one entry for {key!r}, got {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# GET /settings/{domain}: list-with-source progression
# ---------------------------------------------------------------------------


def test_list_settings_shows_spec_default_when_no_row(app_client: TestClient) -> None:
    resp = app_client.get("/settings/custom_fields")
    assert resp.status_code == 200
    entry = _entry(resp.json(), "max_per_entity")
    assert entry == {
        "domain": "custom_fields",
        "key": "max_per_entity",
        "value": 20,
        "value_type": "integer",
        "label": "Maximum custom fields per entity",
        "is_secret": False,
        "source": "default",
    }


def test_put_then_list_shows_tenant_source(app_client: TestClient) -> None:
    put_resp = app_client.put(
        "/settings/custom_fields/max_per_entity", json={"value": 5}
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["value"] == 5
    assert put_resp.json()["source"] == "tenant"

    list_resp = app_client.get("/settings/custom_fields")
    assert list_resp.status_code == 200
    entry = _entry(list_resp.json(), "max_per_entity")
    assert entry["value"] == 5
    assert entry["source"] == "tenant"


def test_list_settings_returns_every_spec_in_domain(app_client: TestClient) -> None:
    resp = app_client.get("/settings/audit")
    assert resp.status_code == 200
    keys = {item["key"] for item in resp.json()}
    assert keys == {"retention_days"}


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------


@pytest.fixture()
def secret_spec():
    """Registers a throwaway `is_secret=True` spec, deregistered on teardown.

    None of this app's three real specs are secrets — this proves the
    masking behavior contractually rather than incidentally.
    """
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


def test_secret_default_is_not_masked(
    app_client: TestClient, secret_spec: sr.SettingSpec
) -> None:
    resp = app_client.get("/settings/auth")
    entry = _entry(resp.json(), "test_secret_token")
    assert entry["source"] == "default"
    assert entry["value"] == "unset"


def test_secret_with_tenant_row_is_masked(
    app_client: TestClient, secret_spec: sr.SettingSpec
) -> None:
    put_resp = app_client.put(
        "/settings/auth/test_secret_token", json={"value": "sekret-value"}
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["value"] == "********"
    assert put_resp.json()["source"] == "tenant"

    list_resp = app_client.get("/settings/auth")
    entry = _entry(list_resp.json(), "test_secret_token")
    assert entry["value"] == "********"
    assert entry["source"] == "tenant"


# ---------------------------------------------------------------------------
# Validation errors (PUT)
# ---------------------------------------------------------------------------


def test_put_below_min_value_is_bad_request(app_client: TestClient) -> None:
    resp = app_client.put("/settings/custom_fields/max_per_entity", json={"value": 0})
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


def test_put_above_max_value_is_bad_request(app_client: TestClient) -> None:
    resp = app_client.put("/settings/custom_fields/max_per_entity", json={"value": 999})
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


def test_put_wrong_type_is_bad_request(app_client: TestClient) -> None:
    resp = app_client.put(
        "/settings/custom_fields/max_per_entity", json={"value": "not-an-int"}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


def test_put_null_value_is_bad_request_for_json_spec(app_client: TestClient) -> None:
    """Closes the `_normalize_for_db` None-handling gap: previously a `None`
    json value would either violate the DB CHECK constraint (raw 500) or, for
    booleans, silently store `false`. Both must now be a clean 400 instead.
    """
    resp = app_client.put("/settings/branding/ui_branding", json={"value": None})
    assert resp.status_code == 400
    assert resp.json()["code"] == "bad_request"


def test_put_valid_json_value_succeeds(app_client: TestClient) -> None:
    resp = app_client.put(
        "/settings/branding/ui_branding", json={"value": {"logo": "a.png"}}
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == {"logo": "a.png"}
    assert resp.json()["source"] == "tenant"


# ---------------------------------------------------------------------------
# Unknown domain/key -> 404
# ---------------------------------------------------------------------------


def test_get_unknown_domain_is_not_found(app_client: TestClient) -> None:
    resp = app_client.get("/settings/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_put_unknown_domain_is_not_found(app_client: TestClient) -> None:
    resp = app_client.put("/settings/does-not-exist/anything", json={"value": 1})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_put_unknown_key_in_known_domain_is_not_found(app_client: TestClient) -> None:
    resp = app_client.put("/settings/custom_fields/does-not-exist", json={"value": 1})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# Guard: no admin role -> 403
# ---------------------------------------------------------------------------


def test_non_admin_person_is_forbidden(
    app_client: TestClient, db: Session, tenant_row: Tenant
) -> None:
    non_admin = Person(
        tenant_id=tenant_row.id,
        email="member@acme.test",
        first_name="Member",
        last_name="User",
    )
    db.add(non_admin)
    db.flush()
    app_client.app.dependency_overrides[require_user_auth] = lambda: non_admin

    resp = app_client.get("/settings/custom_fields")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Audit event on settings update (PUT)
# ---------------------------------------------------------------------------


def test_put_setting_writes_audit_event(
    app_client: TestClient, db: Session, tenant_row: Tenant, admin_person: Person
) -> None:
    resp = app_client.put("/settings/custom_fields/max_per_entity", json={"value": 5})
    assert resp.status_code == 200

    events = db.query(AuditEvent).filter(AuditEvent.tenant_id == tenant_row.id).all()
    assert len(events) == 1
    event = events[0]
    assert event.action == "settings.update"
    assert event.entity_type == "setting"
    assert event.actor_person_id == admin_person.id
    assert event.details["domain"] == "custom_fields"
    assert event.details["key"] == "max_per_entity"
    assert event.details["is_secret"] is False


def test_put_secret_setting_audit_event_has_no_value(
    app_client: TestClient,
    db: Session,
    tenant_row: Tenant,
    admin_person: Person,
    secret_spec: sr.SettingSpec,
) -> None:
    resp = app_client.put(
        "/settings/auth/test_secret_token", json={"value": "sekret-value"}
    )
    assert resp.status_code == 200

    events = db.query(AuditEvent).filter(AuditEvent.tenant_id == tenant_row.id).all()
    assert len(events) == 1
    event = events[0]
    assert event.action == "settings.update"
    assert event.details["domain"] == "auth"
    assert event.details["key"] == "test_secret_token"
    assert event.details["is_secret"] is True
    assert "value" not in event.details
