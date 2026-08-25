"""Real-Postgres forced-RLS and immutability canaries for Digital Media."""

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
DIGITAL_MEDIA_VERSIONS = (
    REPO_ROOT
    / "packages/dotmac-digital-media/src/dotmac_digital_media/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — Digital Media needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_scratch() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"digital_media_{uuid.uuid4().hex[:12]}"
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
    previous_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {DIGITAL_MEDIA_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        if previous_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous_url
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


def _seed(admin_url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    library_a, library_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant_id, slug, library_id in (
                (tenant_a, "media-a", library_a),
                (tenant_b, "media-b", library_b),
            ):
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant_id, "slug": slug, "name": slug},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_digitalmedia.media_libraries "
                        "(id, tenant_id, code, name) VALUES "
                        "(:id, :tenant_id, :code, :name)"
                    ),
                    {
                        "id": library_id,
                        "tenant_id": tenant_id,
                        "code": slug,
                        "name": slug,
                    },
                )
    finally:
        engine.dispose()
    return tenant_a, tenant_b, library_a, library_b


def test_every_declared_table_has_enabled_and_forced_rls(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'mod_digitalmedia' AND relkind = 'r'"
                )
            ).all()
            assert len(rows) == 15
            assert all(enabled and forced for _, enabled, forced in rows)
    finally:
        engine.dispose()


def test_tenants_cannot_read_or_write_each_others_library(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b, library_a, library_b = _seed(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_a)},
            )
            visible = (
                conn.execute(
                    text("SELECT id FROM mod_digitalmedia.media_libraries ORDER BY id")
                )
                .scalars()
                .all()
            )
            assert visible == [library_a]
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_digitalmedia.media_libraries "
                        "(id, tenant_id, code, name) VALUES "
                        "(:id, :tenant_id, 'leak', 'Leak')"
                    ),
                    {"id": uuid.uuid4(), "tenant_id": tenant_b},
                )
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_b)},
            )
            visible = (
                conn.execute(text("SELECT id FROM mod_digitalmedia.media_libraries"))
                .scalars()
                .all()
            )
            assert visible == [library_b]
    finally:
        engine.dispose()


def test_source_revision_rows_cannot_be_updated_or_deleted(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    tenant_id, _, library_id, _ = _seed(admin_url)
    asset_id, revision_id = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mod_digitalmedia.media_assets "
                    "(id, tenant_id, library_id, kind, title, lifecycle) VALUES "
                    "(:id, :tenant_id, :library_id, 'image', 'Evidence', 'ingesting')"
                ),
                {"id": asset_id, "tenant_id": tenant_id, "library_id": library_id},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_digitalmedia.media_revisions "
                    "(id, tenant_id, asset_id, revision_number, file_id, checksum, "
                    "media_type, byte_length, source_kind, author_ref, "
                    "source_created_at, "
                    "change_reason) VALUES "
                    "(:id, :tenant_id, :asset_id, 1, :file_id, :checksum, "
                    "'image/jpeg', 10, 'upload', 'user:a', now(), 'initial')"
                ),
                {
                    "id": revision_id,
                    "tenant_id": tenant_id,
                    "asset_id": asset_id,
                    "file_id": uuid.uuid4(),
                    "checksum": "a" * 64,
                },
            )
        with pytest.raises(DBAPIError, match="immutable"):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE mod_digitalmedia.media_revisions "
                        "SET checksum = :checksum WHERE id = :id"
                    ),
                    {"checksum": "b" * 64, "id": revision_id},
                )
        with pytest.raises(DBAPIError, match="immutable"):
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM mod_digitalmedia.media_revisions WHERE id = :id"),
                    {"id": revision_id},
                )
    finally:
        engine.dispose()
