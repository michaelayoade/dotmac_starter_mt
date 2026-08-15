"""Live proof that the two platform-only modules really are platform-only.

`dotmac-release-catalog` and `dotmac-entitlement-allocation` declared their
tables under `tables=` — the TENANT slot — while their migrations built
control-plane tables with no `tenant_id`, no RLS, grants to `platform_api` and
`REVOKE ALL` from `app_user`. The DDL was right and the declaration was wrong.

ADR-0023 § 3 is the reason this file exists at all: **on the platform plane the
REVOKE is the isolation**, so it has to be checked as strictly as an RLS policy
is on the tenant side. An un-revoked platform table is exactly as exposed as an
unpolicied tenant table, and reads just as safe.

The inverse matters too, and is checked here: a table the online platform role
cannot reach is a broken deployment, not a secure one. Declared-and-unreachable
is a contract violation.

These are canaries against a real Postgres. They compose each module's lineage
into a scratch database and drive the assertions as the roles themselves rather
than trusting `information_schema` alone — a grant that exists but does not work
is the failure worth catching.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

REPO_ROOT = Path(__file__).resolve().parents[1]
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"

# (module short code, its versions dir, its schema, its platform tables)
PLATFORM_ONLY_MODULES = (
    pytest.param(
        REPO_ROOT
        / "packages/dotmac-release-catalog/src/dotmac_release_catalog"
        / "migrations/versions",
        "mod_rel",
        ("release_artifacts", "artifact_attestations"),
        id="release-catalog",
    ),
    pytest.param(
        REPO_ROOT
        / "packages/dotmac-entitlement-allocation/src/dotmac_entitlement_allocation"
        / "migrations/versions",
        "mod_ealloc",
        ("allocations", "allocation_entries"),
        id="entitlement-allocation",
    ),
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the plane canary needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def composed(request: pytest.FixtureRequest) -> Iterator[tuple[str, str, str]]:
    """Compose ONE platform-only lineage with **no** plane selection.

    Passing no `module_plane_selections` is itself part of the proof: an atomic
    module must compose without an assembly choosing anything.
    """
    versions_dir = request.param
    superuser = _superuser_url()
    name = f"plane_only_{uuid.uuid4().hex[:12]}"
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
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {versions_dir}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield (
            admin_url,
            _url_for(superuser, name, user="app_user"),
            _url_for(superuser, name, user="platform_api"),
        )
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


@pytest.mark.parametrize(
    ("composed", "schema", "tables"), PLATFORM_ONLY_MODULES, indirect=["composed"]
)
def test_composition_succeeds_without_any_plane_selection(
    composed: tuple[str, str, str], schema: str, tables: tuple[str, ...]
) -> None:
    """The lineage builds its platform tables with nothing selected.

    Reaching this assertion at all means the upgrade ran; an atomic module that
    demanded a selection would have failed in the fixture.
    """
    admin_url, _, _ = composed
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        found = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = :s"),
                {"s": schema},
            )
        }
    engine.dispose()
    assert set(tables) <= found, f"{schema}: expected {tables}, found {sorted(found)}"


@pytest.mark.parametrize(
    ("composed", "schema", "tables"), PLATFORM_ONLY_MODULES, indirect=["composed"]
)
def test_tables_have_no_tenant_column_no_rls_and_no_policy(
    composed: tuple[str, str, str], schema: str, tables: tuple[str, ...]
) -> None:
    """A platform table with a tenant column or RLS is a mis-declared plane."""
    admin_url, _, _ = composed
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        for table in tables:
            tenant_columns = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t "
                    "AND column_name = 'tenant_id'"
                ),
                {"s": schema, "t": table},
            ).fetchall()
            assert not tenant_columns, f"{schema}.{table} carries a tenant_id"

            rls = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = :t"
                ),
                {"s": schema, "t": table},
            ).one()
            assert rls == (False, False), f"{schema}.{table} has RLS enabled: {rls}"

            policies = conn.execute(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = :t"
                ),
                {"s": schema, "t": table},
            ).fetchall()
            assert not policies, f"{schema}.{table} has policies: {policies}"
    engine.dispose()


@pytest.mark.parametrize(
    ("composed", "schema", "tables"), PLATFORM_ONLY_MODULES, indirect=["composed"]
)
def test_the_tenant_role_has_no_schema_access_and_no_table_privilege(
    composed: tuple[str, str, str], schema: str, tables: tuple[str, ...]
) -> None:
    """ADR-0023 § 3: on this plane the REVOKE *is* the isolation.

    Checked three ways, because each alone can lie. The catalogue may report a
    privilege the role cannot exercise, so the last check drives a real query as
    `app_user` and requires it to be refused.
    """
    admin_url, app_user_url, _ = composed

    # Introspect FROM the admin connection ABOUT app_user. Asking as app_user
    # cannot work — `has_table_privilege` must resolve the table name, which
    # needs schema USAGE, and app_user correctly has none. That error is itself
    # evidence of isolation, but it is not an assertion, so the privilege
    # questions are asked by a role that can see the catalogue.
    admin = create_engine(admin_url)
    with admin.connect() as conn:
        assert not conn.execute(
            text("SELECT has_schema_privilege(:r, :s, 'USAGE')"),
            {"r": "app_user", "s": schema},
        ).scalar(), f"app_user holds USAGE on {schema}"

        for table in tables:
            qualified = f"{schema}.{table}"
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "REFERENCES"):
                assert not conn.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"),
                    {"r": "app_user", "t": qualified, "p": privilege},
                ).scalar(), f"app_user holds {privilege} on {qualified}"

            granted_columns = conn.execute(
                text(
                    "SELECT column_name, privilege_type "
                    "FROM information_schema.column_privileges "
                    "WHERE table_schema = :s AND table_name = :t "
                    "AND grantee = 'app_user'"
                ),
                {"s": schema, "t": table},
            ).fetchall()
            assert (
                not granted_columns
            ), f"app_user holds column privileges on {qualified}: {granted_columns}"
    admin.dispose()

    # A grant that exists but does not work is the failure worth catching, so
    # the last word is a real query driven AS app_user, which must be refused.
    tenant = create_engine(app_user_url)
    with tenant.connect() as conn:
        for table in tables:
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text(f"SELECT 1 FROM {schema}.{table} LIMIT 1")  # noqa: S608
                )
            conn.rollback()
    tenant.dispose()


@pytest.mark.parametrize(
    ("composed", "schema", "tables"), PLATFORM_ONLY_MODULES, indirect=["composed"]
)
def test_the_online_platform_role_can_actually_reach_the_tables(
    composed: tuple[str, str, str], schema: str, tables: tuple[str, ...]
) -> None:
    """Declared-and-unreachable is a contract violation, not a secure deployment.

    A table grant without schema USAGE is ineffective, and holding only
    REFERENCES or TRIGGER does not make an ordinary row path usable — so this
    asserts USAGE, real row DML, and then performs an actual read.
    """
    _, _, platform_url = composed
    engine = create_engine(platform_url)
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT has_schema_privilege(:r, :s, 'USAGE')"),
            {"r": "platform_api", "s": schema},
        ).scalar(), f"platform_api lacks USAGE on {schema}"

        for table in tables:
            qualified = f"{schema}.{table}"
            reachable = [
                privilege
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
                if conn.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"),
                    {"r": "platform_api", "t": qualified, "p": privilege},
                ).scalar()
            ]
            assert reachable, (
                f"platform_api holds no row DML on {qualified} — the plane is "
                "declared but unreachable"
            )
            conn.execute(text(f"SELECT 1 FROM {qualified} LIMIT 1"))  # noqa: S608
    engine.dispose()
