"""Real-PostgreSQL tenant-isolation canaries for dotmac-party."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
PARTY_VERSIONS = (
    REPO_ROOT / "packages/dotmac-party/src/dotmac_party/migrations/versions"
)
TABLES = (
    "party_roles",
    "party_relationships",
    "party_memberships",
    "party_contact_points",
    "party_external_references",
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the RLS canary needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_party() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"party_rls_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {PARTY_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def test_every_table_is_forced_rls_and_hidden_from_platform_role(
    migrated_party: tuple[str, str],
) -> None:
    admin_url, _ = migrated_party
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            for table in TABLES:
                enabled, forced = conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = CAST(:table AS regclass)"
                    ),
                    {"table": f"mod_party.{table}"},
                ).one()
                assert enabled and forced, table
                policies = list(
                    conn.execute(
                        text(
                            "SELECT policyname FROM pg_policies "
                            "WHERE schemaname = 'mod_party' AND tablename = :table"
                        ),
                        {"table": table},
                    ).scalars()
                )
                assert policies == [f"{table}_tenant_isolation"]
                assert not conn.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'platform_api', CAST(:table AS text), 'SELECT')"
                    ),
                    {"table": f"mod_party.{table}"},
                ).scalar_one()
    finally:
        engine.dispose()


def test_app_user_cannot_insert_another_tenants_party_context(
    migrated_party: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_party
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    party_a = uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as conn:
        for tenant, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant, "slug": slug, "name": slug.title()},
            )
        conn.execute(
            text(
                "INSERT INTO public.parties "
                "(id, tenant_id, party_type, display_name) "
                "VALUES (:id, :tenant, 'person', 'Alpha Person')"
            ),
            {"id": party_a, "tenant": tenant_a},
        )
    admin.dispose()

    app = create_engine(app_url)
    try:
        with app.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_b)},
            )
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(
                    text(
                        "INSERT INTO mod_party.party_roles "
                        "(id, tenant_id, party_id, role_type, role_key, status) "
                        "VALUES (:id, :tenant, :party, 'customer', 'default', 'active')"
                    ),
                    {"id": uuid.uuid4(), "tenant": tenant_a, "party": party_a},
                )
    finally:
        app.dispose()


def test_external_reference_identity_is_scoped_per_tenant(
    migrated_party: tuple[str, str],
) -> None:
    admin_url, _ = migrated_party
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for slug in ("alpha", "bravo"):
                tenant, party = uuid.uuid4(), uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant, "slug": slug, "name": slug.title()},
                )
                conn.execute(
                    text(
                        "INSERT INTO public.parties "
                        "(id, tenant_id, party_type, display_name) "
                        "VALUES (:id, :tenant, 'person', :name)"
                    ),
                    {"id": party, "tenant": tenant, "name": slug},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_party.party_external_references "
                        "(id, tenant_id, party_id, source_system, entity_type, "
                        "external_id, source) VALUES "
                        "(:id, :tenant, :party, 'crm', 'person', '42', 'migration')"
                    ),
                    {"id": uuid.uuid4(), "tenant": tenant, "party": party},
                )
    finally:
        engine.dispose()
