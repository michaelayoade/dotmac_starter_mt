"""Real-PostgreSQL isolation canaries for the optional publishing module.

The reference assembly builds but does not compose ``dotmac-publishing``. This
suite creates a disposable database, composes only kernel and publishing, and
drives the real ``app_user`` role so service filtering cannot hide an RLS defect.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_publishing.manifest import module
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
PUBLISHING_VERSIONS = (
    REPO_ROOT / "packages/dotmac-publishing/src/dotmac_publishing/migrations/versions"
)
TABLES = (
    "publication_releases",
    "publication_deliveries",
    "publication_attempts",
    "publication_observations",
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — publishing RLS needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_publishing() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"publishing_rls_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations", f"{KERNEL_VERSIONS} {PUBLISHING_VERSIONS}"
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


def _seed_plane(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            release_id = uuid.uuid4()
            delivery_id = uuid.uuid4()
            attempt_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_publishing.publication_releases "
                    "(id, tenant_id, request_key, request_fingerprint, "
                    "source_ref, actor_ref, requested_for, snapshot_version, "
                    "snapshot_payload, snapshot_digest, state, timer_generation) "
                    "VALUES (:id, :tenant, :key, :fingerprint, :source, :actor, "
                    ":requested, 1, CAST(:payload AS jsonb), :digest, 'scheduled', 1)"
                ),
                {
                    "id": release_id,
                    "tenant": tenant_id,
                    "key": f"{slug}:request",
                    "fingerprint": "1" * 64,
                    "source": f"content:{slug}",
                    "actor": f"party:{slug}",
                    "requested": "2026-08-19T11:00:00+00:00",
                    "payload": '{"schema_version": 1}',
                    "digest": "2" * 64,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_publishing.publication_deliveries "
                    "(id, tenant_id, publication_release_id, target_ref, state) "
                    "VALUES (:id, :tenant, :release, :target, 'intent_published')"
                ),
                {
                    "id": delivery_id,
                    "tenant": tenant_id,
                    "release": release_id,
                    "target": f"binding:{slug}",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_publishing.publication_attempts "
                    "(id, tenant_id, publication_delivery_id, attempt_number, "
                    "state, outbox_event_ref, requested_at) VALUES "
                    "(:id, :tenant, :delivery, 1, 'intent_published', :outbox, :at)"
                ),
                {
                    "id": attempt_id,
                    "tenant": tenant_id,
                    "delivery": delivery_id,
                    "outbox": str(uuid.uuid4()),
                    "at": "2026-08-19T11:00:00+00:00",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_publishing.publication_observations "
                    "(id, tenant_id, publication_attempt_id, receipt_ref, "
                    "fingerprint, outcome, remote_ref, observed_at, recorded_at) "
                    "VALUES (:id, :tenant, :attempt, :receipt, :fingerprint, "
                    "'published', :remote, :observed, :recorded)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_id,
                    "attempt": attempt_id,
                    "receipt": f"receipt:{slug}",
                    "fingerprint": "3" * 64,
                    "remote": f"remote:{slug}",
                    "observed": "2026-08-19T11:01:00+00:00",
                    "recorded": "2026-08-19T11:02:00+00:00",
                },
            )
    engine.dispose()
    return tenant_a, tenant_b


def test_live_catalog_proves_the_complete_forced_rls_plane(
    migrated_publishing: tuple[str, str],
) -> None:
    admin_url, _ = migrated_publishing
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert audit_live_schemas(conn, registry) == ()
        rows = list(
            conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='mod_publishing' AND c.relkind='r'"
                )
            )
        )
    engine.dispose()
    assert {row[0] for row in rows} == set(TABLES)
    assert all(row[1] and row[2] for row in rows)


def test_real_app_user_sees_only_its_tenant_on_all_four_tables(
    migrated_publishing: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_publishing
    tenant_a, tenant_b = _seed_plane(admin_url)
    engine = create_engine(app_url)
    try:
        for tenant_id in (tenant_a, tenant_b):
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant_id)},
                )
                for table in TABLES:
                    tenants = set(
                        conn.execute(
                            text(
                                f"SELECT tenant_id FROM mod_publishing.{table}"  # noqa: S608
                            )
                        ).scalars()
                    )
                    assert tenants == {tenant_id}, table
    finally:
        engine.dispose()


def test_real_app_user_cannot_write_for_another_tenant(
    migrated_publishing: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_publishing
    tenant_a, tenant_b = _seed_plane(admin_url)
    engine = create_engine(app_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_a)},
            )
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(
                    text(
                        "INSERT INTO mod_publishing.publication_releases "
                        "(id, tenant_id, request_key, request_fingerprint, "
                        "source_ref, actor_ref, requested_for, snapshot_version, "
                        "snapshot_payload, snapshot_digest, state) VALUES "
                        "(:id, :tenant, 'forged', :fingerprint, 'source', 'actor', "
                        ":requested, 1, CAST('{}' AS jsonb), :digest, 'scheduled')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant_b,
                        "fingerprint": "1" * 64,
                        "requested": "2026-08-19T11:00:00+00:00",
                        "digest": "2" * 64,
                    },
                )
    finally:
        engine.dispose()


def test_unscoped_online_role_fails_closed(
    migrated_publishing: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_publishing
    _seed_plane(admin_url)
    engine = create_engine(app_url)
    try:
        with engine.begin() as conn:
            for table in TABLES:
                assert (
                    conn.execute(
                        text(
                            f"SELECT count(*) FROM mod_publishing.{table}"  # noqa: S608
                        )
                    ).scalar_one()
                    == 0
                )
    finally:
        engine.dispose()
