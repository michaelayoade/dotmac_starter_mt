"""Test fixtures.

Two-tenant setup: every isolation test gets `tenant_a` and `tenant_b` and a
`client_for(tenant)` helper that issues requests against the right subdomain.

These tests REQUIRE a real Postgres with RLS — SQLite has no RLS. CI/dev should
spin up a disposable Postgres (testcontainers, docker compose, or a per-test schema).

This skeleton uses `os.getenv("TEST_DATABASE_URL")` — set it before running tests.
"""

from __future__ import annotations

import os

# Must run before any `app.`/`dotmac_kernel.` import anywhere in the test
# session. `dotmac_kernel.config` constructs `settings = Settings()` at import
# (reading DATABASE_URL/PLATFORM_DATABASE_URL from the env ONCE), and importing
# `dotmac_kernel` now runs that eagerly (the Task-2 public-API package
# __init__). Since a per-test monkeypatch of the env can't retro-construct that
# frozen `settings`, promote the integration TEST_* URLs to the real env HERE,
# at conftest import — before pytest collects any module whose top-level
# `import dotmac_kernel` would freeze `settings`. Unit runs (no TEST_* set) fall
# through to the hermetic placeholder below.
if os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
if os.getenv("TEST_PLATFORM_DATABASE_URL"):
    os.environ["PLATFORM_DATABASE_URL"] = os.environ["TEST_PLATFORM_DATABASE_URL"]
# Well-formed placeholder with an unroutable port so a bare `pytest tests`
# collects hermetically (engine builds, never connects) and any accidental
# connection fails fast. The autouse `_set_database_url` fixture below still
# re-pins it per test for good measure.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://unit-test:unit-test@127.0.0.1:59999/unit-test"
)

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session")
def admin_engine():
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _set_database_url(monkeypatch):
    """Pin DATABASE_URL for the app under test to the TEST_DATABASE_URL.

    PLATFORM_DATABASE_URL rides along from TEST_PLATFORM_DATABASE_URL (the
    `platform_api` role — set by `make test-integration`) so platform routes
    exercise the REAL platform grants: platform catalog tables are REVOKEd
    from `app_user`, so running them on the app_user connection would fail
    for the wrong reason (and hide a missing-grant bug in the right one).
    """
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        monkeypatch.setenv("DATABASE_URL", url)
    platform_url = os.getenv("TEST_PLATFORM_DATABASE_URL")
    if platform_url:
        monkeypatch.setenv("PLATFORM_DATABASE_URL", platform_url)
    monkeypatch.setenv("PLATFORM_ROOT_DOMAIN", "localhost")


@pytest.fixture
def admin_session(admin_engine) -> Generator[Session, None, None]:
    """Connection as app_admin — RLS bypassed. Used by fixtures to set up data."""
    SessionLocal = sessionmaker(bind=admin_engine, autocommit=False, autoflush=False)
    db = SessionLocal()
    try:
        yield db
        db.rollback()  # keep test DB clean — explicit commits required where needed
    finally:
        db.close()


def _entitle(admin_session, tenant) -> None:
    """Grant every declared capability to a freshly created test tenant.

    `require_capability` is deny-by-default and migration `a004` backfills only
    the tenants that existed when it ran — so a tenant created by a test is
    born un-entitled and would 403 on every gated route. Granting here makes the
    DEFAULT test subject an ordinary, fully-entitled tenant; a test that cares
    about the un-entitled case asserts it explicitly (see
    `tests/unit/test_require_capability.py`).

    Written as `app_admin` (RLS bypassed), like the tenant row itself.
    """
    from dotmac_kernel.capabilities import CapabilityCatalogue
    from dotmac_kernel.entitlements import grant_entitlement

    from app.assembly import assembly

    catalogue = CapabilityCatalogue.from_manifests(assembly.modules)
    for code in sorted(catalogue.codes()):
        grant_entitlement(
            admin_session,
            tenant_id=tenant.id,
            capability_code=code,
            catalogue=catalogue,
            source="test-fixture",
        )
    admin_session.commit()


@pytest.fixture
def tenant_a(admin_session: Session):
    from dotmac_kernel.models import Tenant

    t = Tenant(slug="alpha", name="Alpha Test Tenant")
    admin_session.add(t)
    admin_session.commit()
    admin_session.refresh(t)
    _entitle(admin_session, t)
    yield t
    admin_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": str(t.id)})
    admin_session.commit()


@pytest.fixture
def tenant_b(admin_session: Session):
    from dotmac_kernel.models import Tenant

    t = Tenant(slug="beta", name="Beta Test Tenant")
    admin_session.add(t)
    admin_session.commit()
    admin_session.refresh(t)
    _entitle(admin_session, t)
    yield t
    admin_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": str(t.id)})
    admin_session.commit()


@pytest.fixture
def app_client():
    """TestClient that lets you set Host header per request."""
    from app.main import app

    return TestClient(app)


def client_for(client: TestClient, tenant_slug: str) -> TestClient:
    """Wrap a TestClient so every request carries Host: {slug}.localhost."""
    client.headers.update({"Host": f"{tenant_slug}.localhost"})
    return client


DEFAULT_TEST_PASSWORD = "correct horse battery staple"

PLATFORM_ADMIN_EMAIL = "root@platform.example.com"
PLATFORM_ADMIN_PASSWORD = "platform-canary-password"


def load_platform_admin_cli():
    """Import scripts/create_platform_admin.py — the CLI's `upsert_admin` IS
    the only platform-admin bootstrap path (never HTTP), so platform tests
    create admins through that exact function."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent / "scripts" / "create_platform_admin.py"
    )
    spec = importlib.util.spec_from_file_location("create_platform_admin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def platform_admin(admin_session):
    """A platform admin created through the CLI's own upsert path."""
    from sqlalchemy import text as sa_text

    cli = load_platform_admin_cli()
    admin = cli.upsert_admin(
        admin_session,
        email=PLATFORM_ADMIN_EMAIL,
        password=PLATFORM_ADMIN_PASSWORD,
        is_active=True,
    )
    admin_session.commit()
    yield admin
    admin_session.execute(
        sa_text(
            "DELETE FROM platform_sessions WHERE admin_id IN "
            "(SELECT id FROM platform_admins WHERE email = :email)"
        ),
        {"email": PLATFORM_ADMIN_EMAIL},
    )
    admin_session.execute(
        sa_text("DELETE FROM platform_admins WHERE email = :email"),
        {"email": PLATFORM_ADMIN_EMAIL},
    )
    admin_session.commit()


def platform_login(client: TestClient) -> str:
    """Log the shared `platform_admin` fixture's admin in on the platform
    root host and return its bearer token."""
    resp = client.post(
        "/platform/auth/login",
        json={"email": PLATFORM_ADMIN_EMAIL, "password": PLATFORM_ADMIN_PASSWORD},
        headers={"Host": "localhost"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def provision_owner(
    admin_session: Session,
    tenant,
    email: str,
    password: str = DEFAULT_TEST_PASSWORD,
    *,
    role_slug: str = "admin",
) -> None:
    """Create a login-able owner (party + person + credential + role grant)
    for `tenant` via the admin engine — the test-side mirror of
    `app.features.tenants.service.provision_tenant`.

    Control-plane security Task 2 made platform provisioning the ONLY
    owner-creation path (registration defaults to `closed` and never grants
    admin), so tests that used to register-their-first-user-as-admin
    provision an owner here instead and then just log in.
    """
    from dotmac_kernel.models import (
        Party,
        PartyPerson,
        PartyRoleGrant,
        PartyType,
        Role,
        UserCredential,
    )
    from dotmac_kernel.security import hash_password

    email = email.strip().lower()
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name="Owner Account",
        email=email,
    )
    admin_session.add(party)
    admin_session.flush()
    admin_session.add(
        PartyPerson(party_id=party.id, first_name="Owner", last_name="Account")
    )
    admin_session.add(
        UserCredential(
            tenant_id=tenant.id,
            party_id=party.id,
            password_hash=hash_password(password),
        )
    )
    from sqlalchemy import select

    role = admin_session.scalars(
        select(Role).where(Role.tenant_id == tenant.id).where(Role.slug == role_slug)
    ).first()
    if role is None:
        role = Role(tenant_id=tenant.id, slug=role_slug, name=role_slug.title())
        admin_session.add(role)
        admin_session.flush()
    admin_session.add(
        PartyRoleGrant(tenant_id=tenant.id, party_id=party.id, role_id=role.id)
    )
    admin_session.commit()
    # Cleanup rides on the tenant fixture's teardown (tenants delete cascades).


def provision_and_login(
    admin_session: Session,
    tenant,
    client: TestClient,
    email: str,
    password: str = DEFAULT_TEST_PASSWORD,
) -> str:
    """`provision_owner` + `/auth/login` on the tenant host → bearer token."""
    provision_owner(admin_session, tenant, email, password)
    login = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers={"Host": f"{tenant.slug}.localhost"},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def open_registration(admin_session: Session, tenant) -> None:
    """Set `auth.registration_policy = open` for `tenant` (tenant-scoped
    `domain_settings` row) — for tests exercising the self-registration
    path, which is policy-CLOSED by default since Task 2."""
    from dotmac_kernel.settings_models import (
        DomainSetting,
        SettingDomain,
        SettingValueType,
    )

    admin_session.add(
        DomainSetting(
            tenant_id=tenant.id,
            domain=SettingDomain.auth,
            key="registration_policy",
            value_type=SettingValueType.string,
            value_text="open",
        )
    )
    admin_session.commit()
