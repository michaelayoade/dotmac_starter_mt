"""Postgres RLS canary for `mod_appdir.application_bindings`.

Unlike the other module canaries in this directory, this one provisions its OWN
scratch database and composes the module's lineage explicitly, because the
reference assembly deliberately does **not** compose
`dotmac-application-directory`: the starter is a target application, and only a
Workspace has a connected-application portfolio (ADR-0021). Adding the module to
`app/assembly.py` or to the shipped `alembic.ini` would put `mod_appdir` into
every starter deployment, which would be a lie about what the starter is.

So the lineage is proven the way `tests/test_migration_split_rehearsals.py`
proves migrations: a scratch database, Alembic's Python API, and a
`version_locations` override that exists only for the duration of the test.
The isolation itself is driven as `app_user` — the ONLINE role — because
`app_admin` carries BYPASSRLS and would pass this test against a table with no
policy at all.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
DIRECTORY_VERSIONS = (
    REPO_ROOT
    / "packages/dotmac-application-directory/src/dotmac_application_directory"
    / "migrations/versions"
)

_TABLE = "mod_appdir.application_bindings"


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
    """Yield `(admin_url, app_user_url)` for a scratch DB at the composed head.

    Mirrors `scripts/dev-db-init.sh` and the migration rehearsals: the cluster
    superuser only creates the database and hands `public` to `app_admin`;
    migrations then run as `app_admin`, the real production migration role.
    """
    superuser = _superuser_url()
    name = f"appdir_rls_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        # A MODULE lineage creates its own schema, and `CREATE SCHEMA` needs
        # CREATE on the DATABASE — not merely ownership of `public`. The
        # migration rehearsals never needed this because the kernel and assembly
        # lineages build only in `public`, whose ownership is transferred above.
        # `mod_appdir` is the first schema this repository's tests create on a
        # scratch database, so this is the first place it bites. In production
        # the migration role holds the same privilege on the application
        # database, which is how `mod_tstudio` and `mod_tkt` get created there.
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        # The rehearsals never connect as the ONLINE role, so they do not need
        # this. This canary does — and the isolation it proves is only
        # meaningful driven as `app_user`, since `app_admin` carries BYPASSRLS.
        # A fresh database grants CONNECT to nobody but its owner.
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        # `app_current_tenant_id()` lives in `public`, and every policy calls it.
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        # The composition under test: the kernel and assembly lineages the
        # starter really ships, PLUS this module's — which the shipped
        # `alembic.ini` deliberately omits.
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {DIRECTORY_VERSIONS}",
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
    a, b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant_id, slug in ((a, "alpha"), (b, "bravo")):
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant_id, "slug": slug, "name": slug.title()},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_appdir.application_bindings ("
                        "  id, tenant_id, application_code, instance_ref,"
                        "  local_tenant_ref, admin_url, api_audience,"
                        "  descriptor_version, descriptor_digest,"
                        "  role_catalogue_digest, state, source,"
                        "  reconciliation_status"
                        ") VALUES ("
                        "  :id, :tenant_id, 'sub', :instance, 'local-ref',"
                        "  'https://sub.example.net/admin',"
                        "  'https://sub.example.net/api', 1, :digest, :digest,"
                        "  'active', 'vendor_allocation', 'fresh'"
                        ")"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_id,
                        "instance": f"sub-{slug}-1",
                        "digest": f"sha256:{'0' * 64}",
                    },
                )
    finally:
        engine.dispose()
    return a, b


def test_the_module_schema_and_table_exist_after_migration(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT to_regclass(:t)"), {"t": _TABLE}).scalar()
                is not None
            )
    finally:
        engine.dispose()


def test_rls_is_enabled_and_forced(migrated_scratch: tuple[str, str]) -> None:
    """FORCE is the half that is easy to omit and impossible to notice.

    Without it the table OWNER — which migrations run as — bypasses its own
    policy, so every migration-time check passes while production leaks.
    """
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            enabled, forced = conn.execute(
                text(
                    # `CAST(... AS regclass)`, not `:t::regclass` — SQLAlchemy's
                    # `text()` bind parser reads the `::` cast as the start of
                    # another parameter.
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:t AS regclass)"
                ),
                {"t": _TABLE},
            ).one()
            assert enabled, "RLS is not ENABLEd on application_bindings"
            assert forced, "RLS is not FORCEd on application_bindings"

            policies = conn.execute(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = 'mod_appdir' "
                    "AND tablename = 'application_bindings'"
                )
            ).scalars()
            assert "application_bindings_tenant_isolation" in list(policies)
    finally:
        engine.dispose()


def test_a_tenant_cannot_see_another_tenants_binding(
    migrated_scratch: tuple[str, str],
) -> None:
    """The canary itself, driven as the ONLINE role.

    Run as `app_admin` this test would pass against a table with no policy at
    all, because that role carries BYPASSRLS — which is exactly how a missing
    policy survives review.
    """
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)

    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :t, false)"),
                {"t": str(tenant_a)},
            )
            visible = (
                conn.execute(
                    text("SELECT tenant_id FROM mod_appdir.application_bindings")
                )
                .scalars()
                .all()
            )
            assert visible == [
                tenant_a
            ], f"tenant A sees {visible}, expected only its own binding"

            conn.execute(
                text("SELECT set_config('app.current_tenant', :t, false)"),
                {"t": str(tenant_b)},
            )
            visible = (
                conn.execute(
                    text("SELECT tenant_id FROM mod_appdir.application_bindings")
                )
                .scalars()
                .all()
            )
            assert visible == [tenant_b]
    finally:
        engine.dispose()


def test_with_no_tenant_scope_nothing_is_visible(
    migrated_scratch: tuple[str, str],
) -> None:
    """RLS fails CLOSED: an unscoped session reads zero rows, not every row.

    This is the property that turns a forgotten `set_tenant` into an obvious
    empty result rather than a silent cross-tenant read.
    """
    admin_url, app_user_url = migrated_scratch
    _seed_two_tenants(admin_url)

    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM mod_appdir.application_bindings")
            ).scalar()
            assert count == 0
    finally:
        engine.dispose()


def test_a_tenant_cannot_insert_a_binding_for_another_tenant(
    migrated_scratch: tuple[str, str],
) -> None:
    """The WITH CHECK half. A USING-only policy hides other tenants' rows while
    happily letting you write into their scope."""
    from sqlalchemy.exc import DBAPIError

    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)

    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :t, false)"),
                {"t": str(tenant_a)},
            )
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_appdir.application_bindings ("
                        "  id, tenant_id, application_code, instance_ref,"
                        "  local_tenant_ref, admin_url, api_audience,"
                        "  descriptor_version, descriptor_digest,"
                        "  role_catalogue_digest, state, source,"
                        "  reconciliation_status"
                        ") VALUES ("
                        "  :id, :tenant_id, 'erp', 'erp-1', 'local',"
                        "  'https://erp.example.net/admin',"
                        "  'https://erp.example.net/api', 1, :digest, :digest,"
                        "  'active', 'customer_attached', 'fresh'"
                        ")"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_b,
                        "digest": f"sha256:{'0' * 64}",
                    },
                )
    finally:
        engine.dispose()
