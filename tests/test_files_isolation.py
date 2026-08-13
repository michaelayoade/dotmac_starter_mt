"""Postgres isolation canaries for both ``dotmac-files`` security planes.

The reference assembly builds but does not install ``dotmac-files``, so this
test composes its lineage in a scratch database and drives the assertions as
the online ``app_user`` role. SQLite cannot prove row-level security.
"""

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
FILES_VERSIONS = (
    REPO_ROOT / "packages/dotmac-files/src/dotmac_files/migrations/versions"
)
_TABLE = "mod_files.stored_files"
_PLATFORM_TABLE = "mod_files.platform_stored_files"


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the RLS canary needs Postgres")
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
    name = f"files_rls_{uuid.uuid4().hex[:12]}"
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
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {FILES_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_two_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant_id, "slug": slug, "name": slug.title()},
                )
                file_id = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO mod_files.stored_files ("
                        "id, tenant_id, provider_code, storage_key, "
                        "original_filename, size_bytes, declared_media_type, "
                        "detected_media_type, checksum_sha256, state"
                        ") VALUES ("
                        ":id, :tenant, 'memory', :key, 'proof.pdf', 9, "
                        "'application/pdf', 'application/pdf', :digest, 'available'"
                        ")"
                    ),
                    {
                        "id": file_id,
                        "tenant": tenant_id,
                        "key": f"tenants/{tenant_id}/files/{file_id}",
                        "digest": f"sha256:{'0' * 64}",
                    },
                )
    finally:
        engine.dispose()
    return tenant_a, tenant_b


def test_rls_is_enabled_forced_and_has_the_tenant_policy(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            enabled, forced = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:table_name AS regclass)"
                ),
                {"table_name": _TABLE},
            ).one()
            assert enabled
            assert forced
            policies = list(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = 'mod_files' "
                        "AND tablename = 'stored_files'"
                    )
                ).scalars()
            )
            assert "stored_files_tenant_isolation" in policies
    finally:
        engine.dispose()


def test_platform_table_has_no_tenant_or_rls_and_is_revoked_from_app_user(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            columns = set(
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'mod_files' "
                        "AND table_name = 'platform_stored_files'"
                    )
                ).scalars()
            )
            assert "tenant_id" not in columns

            enabled, forced = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:table_name AS regclass)"
                ),
                {"table_name": _PLATFORM_TABLE},
            ).one()
            assert not enabled
            assert not forced
            assert not conn.execute(
                text("SELECT has_table_privilege('app_user', :table_name, 'SELECT')"),
                {"table_name": _PLATFORM_TABLE},
            ).scalar_one()
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert conn.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'platform_api', :table_name, :privilege)"
                    ),
                    {"table_name": _PLATFORM_TABLE, "privilege": privilege},
                ).scalar_one()
    finally:
        engine.dispose()


def test_dual_plane_schema_passes_the_kernel_live_catalog_contract(
    migrated_scratch: tuple[str, str],
) -> None:
    from dotmac_files.manifest import module
    from dotmac_kernel.migrations.catalog import audit_live_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry

    admin_url, _ = migrated_scratch
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            assert audit_live_schemas(conn, registry) == ()
    finally:
        engine.dispose()


def test_platform_role_can_manage_platform_files_without_tenant_context(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    platform_url = _url_for(
        admin_url, admin_url.rpartition("/")[2], user="platform_api"
    )
    engine = create_engine(platform_url)
    file_id = uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mod_files.platform_stored_files ("
                    "id, provider_code, storage_key, original_filename, "
                    "size_bytes, declared_media_type, detected_media_type, "
                    "checksum_sha256, state"
                    ") VALUES ("
                    ":id, 'memory', :key, 'bundle.pdf', 9, "
                    "'application/pdf', 'application/pdf', :digest, 'available'"
                    ")"
                ),
                {
                    "id": file_id,
                    "key": f"platform/files/{file_id}",
                    "digest": f"sha256:{'0' * 64}",
                },
            )
            assert (
                conn.execute(
                    text(
                        "SELECT id FROM mod_files.platform_stored_files WHERE id = :id"
                    ),
                    {"id": file_id},
                ).scalar_one()
                == file_id
            )
    finally:
        engine.dispose()


def test_tenant_role_cannot_read_platform_files(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    file_id = uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mod_files.platform_stored_files ("
                "id, provider_code, storage_key, original_filename, size_bytes, "
                "declared_media_type, detected_media_type, checksum_sha256, state"
                ") VALUES ("
                ":id, 'memory', :key, 'bundle.pdf', 9, 'application/pdf', "
                "'application/pdf', :digest, 'available'"
                ")"
            ),
            {
                "id": file_id,
                "key": f"platform/files/{file_id}",
                "digest": f"sha256:{'0' * 64}",
            },
        )
    admin.dispose()

    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text("SELECT id FROM mod_files.platform_stored_files"))
    finally:
        engine.dispose()


def test_online_role_sees_only_the_bound_tenants_file(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            visible = list(
                conn.execute(
                    text("SELECT tenant_id FROM mod_files.stored_files")
                ).scalars()
            )
            assert visible == [tenant_a]
            assert tenant_b not in visible
    finally:
        engine.dispose()


def test_online_role_cannot_insert_for_another_tenant(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            file_id = uuid.uuid4()
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_files.stored_files ("
                        "id, tenant_id, provider_code, storage_key, "
                        "original_filename, size_bytes, declared_media_type, "
                        "detected_media_type, checksum_sha256, state"
                        ") VALUES ("
                        ":id, :tenant, 'memory', :key, 'blocked.pdf', 9, "
                        "'application/pdf', 'application/pdf', :digest, 'available'"
                        ")"
                    ),
                    {
                        "id": file_id,
                        "tenant": tenant_b,
                        "key": f"tenants/{tenant_b}/files/{file_id}",
                        "digest": f"sha256:{'0' * 64}",
                    },
                )
    finally:
        engine.dispose()


def test_online_role_without_tenant_context_sees_no_files(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM mod_files.stored_files")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()
