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

from collections.abc import Generator, Iterator

import pytest
from dotmac_kernel.source_applications import (
    SourceApplicationRegistry,
    clear_host_application,
    install_host_application,
    install_source_applications,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

#: What the test fleet is allowed to attribute things to. Named here rather
#: than per-test because attribution is now a precondition of writing an audit
#: row at all, exactly as an installed audit-action registry is, and the ~30
#: existing `write_audit_event` callers are not about attribution.
#:
#: Scoped to the ROOT conftest, not `tests/unit/`, because the architecture and
#: integration lanes write audit events too.
TEST_HOST_APPLICATION = "dotmac_starter_mt"
TEST_PEER_APPLICATIONS = ("dotmac_sub", "dotmac_erp")


@pytest.fixture(autouse=True)
def _default_attribution() -> Iterator[None]:
    """Install the accepted-peer registry and the host identity for every test.

    Same shape and same reason as `tests/unit/conftest.py`'s
    `_default_declaration_catalogues`: `create_app` installs both, and a test
    that mounts a router on a bare `FastAPI()` never calls it.

    Torn down after each test — NOT merely re-installed before the next one.
    The difference matters for the tests that prove an unattributed write is
    refused: they clear the host identity deliberately, and a fixture that only
    re-installed would let a leaked identity from one of them silently satisfy
    a later assertion that was supposed to fail.
    """
    install_source_applications(
        SourceApplicationRegistry({TEST_HOST_APPLICATION, *TEST_PEER_APPLICATIONS})
    )
    install_host_application(TEST_HOST_APPLICATION)
    yield
    clear_host_application()


@pytest.fixture(scope="session")
def admin_engine():
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these tests require a real Postgres")
    # Hide bound history material; the importer also refuses DEBUG row logging.
    engine = create_engine(url, future=True, hide_parameters=True, echo=False)
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


@pytest.fixture(scope="session")
def identity_reference_url():
    """Require the exact run-owned hosted reference DSN before any connection."""
    import re

    from sqlalchemy.engine import make_url

    assert os.environ.get("CI") == os.environ.get("GITHUB_ACTIONS") == "true"
    run, attempt = (
        os.environ.get("GITHUB_RUN_ID", ""),
        os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    )
    assert re.fullmatch(r"[0-9]+", run) and re.fullmatch(r"[0-9]+", attempt)
    assert os.environ.get("COMPOSE_PROJECT_NAME") == f"starter-identity-{run}-{attempt}"
    raw = os.environ.get("IDENTITY_REFERENCE_DATABASE_URL")
    assert raw, "Hosted identity reference configuration required"
    url = make_url(raw)
    port = os.environ.get("TEST_DB_PORT", "")
    assert re.fullmatch(r"[0-9]{1,5}", port) and 0 < int(port) < 65536
    names = []
    for kind in ("REFERENCE", "GRAPH", "INSTALLED"):
        name = f"starter_identity_{kind.lower()}_{run}_{attempt}"
        assert len(name) <= 63
        assert os.environ.get(f"IDENTITY_{kind}_DB_NAME") == name
        raw_other = os.environ[f"IDENTITY_{kind}_DATABASE_URL"]
        assert raw_other == f"postgresql+psycopg://app_admin@localhost:{port}/{name}"
        other = make_url(raw_other)
        assert (
            other.drivername,
            other.username,
            other.password,
            other.host,
            other.port,
            other.database,
        ) == ("postgresql+psycopg", "app_admin", None, "localhost", int(port), name)
        assert not other.query
        names.append(name)
    assert len({*names, os.environ["TEST_DB_NAME"]}) == 4
    assert url.database == names[0]
    return raw


@pytest.fixture(scope="session")
def identity_reference_engine(identity_reference_url):
    engine = create_engine(identity_reference_url, hide_parameters=True, echo=False)
    try:
        with engine.connect() as connection:
            assert tuple(
                connection.execute(
                    text(
                        "SELECT current_user, session_user, current_database(), "
                        "rolcanlogin, rolsuper, rolbypassrls FROM pg_roles WHERE "
                        "rolname=current_user"
                    )
                ).one()
            ) == (
                "app_admin",
                "app_admin",
                os.environ["IDENTITY_REFERENCE_DB_NAME"],
                True,
                False,
                True,
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE "
                        "datname=current_database()"
                    )
                )
                == "app_admin"
            )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def identity_reference_composition():
    """Derive all trusted locations from the actual ini and gate attribution."""
    from pathlib import Path
    from types import MappingProxyType, SimpleNamespace

    from dotmac_kernel.migrations import versions_dir
    from dotmac_kernel.migrations.gate import run_gate, version_locations_from_ini
    from dotmac_kernel.modules import ModuleRegistry
    from dotmac_kernel.planes import (
        install_module_plane_selections,
        installed_module_plane_selections,
    )
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.assembly import assembly
    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    locations = version_locations_from_ini(ini)
    report = run_gate(
        assembly.modules,
        locations,
        bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
        module_planes=assembly.module_planes,
    )
    assert report.ok, "Actual reference migration composition refused"
    registry = ModuleRegistry(assembly.modules)
    stateful = {m.code for m in registry.startup_order() if m.is_stateful}
    by_owner = {}
    for record in report.revisions:
        by_owner.setdefault(report.attribution[record.revision]["owner"], set()).add(
            record.location.resolve()
        )
    assert set(by_owner) == {"kernel", "assembly", *stateful}
    assert all(len(paths) == 1 for paths in by_owner.values())
    owned = {owner: next(iter(paths)) for owner, paths in by_owner.items()}
    assert set(owned.values()) == {p.resolve() for p in locations}
    assert owned["kernel"] == versions_dir().resolve()
    before_bindings, before_planes = (
        installed_bindings(),
        installed_module_plane_selections(),
    )
    try:
        install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
        install_module_plane_selections(assembly.module_planes)
        bindings, planes = installed_bindings(), installed_module_plane_selections()
        assert bindings == tuple(
            sorted(ASSEMBLY_PREREQUISITE_BINDINGS, key=lambda b: b.prerequisite)
        )
        assert planes == tuple(sorted(assembly.module_planes, key=lambda p: p.module))
        scripts = ScriptDirectory.from_config(Config(str(ini)))
        heads = frozenset(scripts.get_heads())
        kwargs = MappingProxyType(
            {
                "assembly_versions": owned["assembly"],
                "module_registry": registry,
                "module_versions": MappingProxyType(
                    {code: owned[code] for code in stateful}
                ),
                "prerequisite_bindings": bindings,
            }
        )
        yield SimpleNamespace(
            kwargs=kwargs,
            planes=planes,
            bindings=bindings,
            modules=assembly.modules,
            heads=heads,
            report=report,
        )
    finally:
        install_prerequisite_bindings(before_bindings)
        install_module_plane_selections(before_planes)
        assert installed_bindings() == before_bindings
        assert installed_module_plane_selections() == before_planes


@pytest.fixture
def identity_app_user_switch(identity_reference_engine, identity_reference_url):
    """Own one temporary downward SET-only grant, after shared migrations."""
    import json
    from pathlib import Path

    from sqlalchemy.engine import make_url

    admin = make_url(os.environ["TEST_MIGRATION_DATABASE_URL"])
    target = make_url(identity_reference_url)
    assert (
        admin.drivername,
        admin.username,
        admin.host,
        admin.port,
        admin.database,
    ) == (
        "postgresql+psycopg",
        "postgres",
        "localhost",
        target.port,
        os.environ["TEST_DB_NAME"],
    )
    membership = (
        "SELECT count(*) FROM pg_auth_members WHERE "
        "roleid='app_user'::regrole AND member='app_admin'::regrole"
    )
    posture = (
        "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls FROM "
        "pg_roles WHERE rolname IN ('app_user', 'app_admin') ORDER BY "
        "rolname"
    )
    engine = create_engine(admin, hide_parameters=True, echo=False)
    owned = False
    status = Path("identity-membership-cleanup.json")

    def record_cleanup(event):
        events = json.loads(status.read_text())["events"] if status.exists() else []
        events.append(event)
        result = (
            "failure"
            if "failure" in events
            else "success"
            if events.count("success") == events.count("pending")
            else "pending"
        )
        status.write_text(
            json.dumps({"status": result, "owned": True, "events": events}) + "\n"
        )

    try:
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT current_user = 'postgres' AND session_user = 'postgres'"
                    )
                )
                is True
            )
            before = tuple(tuple(row) for row in connection.execute(text(posture)))
            assert before == (
                ("app_admin", True, False, True),
                ("app_user", True, False, False),
            )
            assert (
                connection.scalar(text(membership)) == 0
            ), "Unexpected pre-existing role membership"
            connection.execute(
                text(
                    "GRANT app_user TO app_admin WITH SET TRUE, INHERIT FALSE, "
                    "ADMIN FALSE"
                )
            )
            owned = True
        record_cleanup("pending")
        with engine.connect() as connection:
            assert tuple(
                connection.execute(
                    text(
                        "SELECT set_option, inherit_option, admin_option FROM "
                        "pg_auth_members WHERE roleid='app_user'::regrole AND "
                        "member='app_admin'::regrole"
                    )
                ).one()
            ) == (True, False, False)
        with identity_reference_engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE app_user"))
            assert tuple(
                connection.execute(
                    text(
                        "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles "
                        "WHERE rolname=current_user"
                    )
                ).one()
            ) == ("app_user", False, False)
            connection.execute(text("SET LOCAL ROLE app_admin"))
            assert (
                connection.scalar(
                    text(
                        "SELECT current_user = session_user "
                        "AND current_user = 'app_admin'"
                    )
                )
                is True
            )
        yield
    finally:
        try:
            if owned:
                # imported_case has closed its connection and rolled back first.
                # Retire its idle pool before revoking our scoped SET permission.
                try:
                    identity_reference_engine.dispose()
                finally:
                    with engine.begin() as connection:
                        connection.execute(text("REVOKE app_user FROM app_admin"))
                with engine.connect() as connection:
                    assert connection.scalar(text(membership)) == 0
                    assert (
                        tuple(tuple(row) for row in connection.execute(text(posture)))
                        == before
                    )
                record_cleanup("success")
        except BaseException:
            record_cleanup("failure")
            raise
        finally:
            engine.dispose()
