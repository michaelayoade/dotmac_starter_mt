"""Postgres proof for `mod_brand`: BOTH planes, isolated by different mechanisms.

This is the only canary in the vendor programme that has to prove two different
kinds of isolation in one schema, and getting either half wrong looks fine from
the other side:

1. **Tenant plane** — `brand_profiles` has `tenant_id NOT NULL`, RLS ENABLEd
   *and* FORCEd, and a policy. Driven as `app_user`, the ONLINE role, because
   `app_admin` carries BYPASSRLS and would pass against a table with no policy
   at all.
2. **Platform plane** — `platform_brand_profiles` and
   `platform_brand_host_bindings` have no tenant column, no RLS, and `app_user`
   REVOKEd across all seven privileges. There the revoke IS the isolation.

Plus the rule that connects them: **no foreign key crosses the planes** (hard
rule 27). Two planes share a lifecycle, never a row.

The composition installs BOTH planes, which is the combination no product uses
today — deliberately, because it is the one where a cross-plane mistake is
possible at all.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
BRAND_VERSIONS = (
    REPO_ROOT
    / "packages/dotmac-brand-profiles/src/dotmac_brand_profiles"
    / "migrations/versions"
)

SCHEMA = "mod_brand"
TENANT_TABLE = "brand_profiles"
PLATFORM_TABLES = ("platform_brand_profiles", "platform_brand_host_bindings")

#: All seven. A revoke that covers six is not a revoke.
ALL_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the dual-plane canary needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture(scope="module")
def migrated_scratch() -> Iterator[tuple[str, str, str]]:
    """`(admin_url, platform_api_url, app_user_url)` with BOTH planes installed.

    Both, deliberately. Sub selects TENANT and Vendor selects PLATFORM, so the
    union is the combination no product uses — and the only one in which a
    cross-plane foreign key or a leaked grant is possible at all.
    """
    superuser = _superuser_url()
    name = f"brand_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        for role in ("app_user", "platform_api"):
            conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO {role}'))
            # `app_current_tenant_id()` lives in `public`, and every policy
            # calls it.
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {BRAND_VERSIONS}",
        )
        # ADR-0028: a plane-SELECTABLE module has no default. Without an explicit
        # selection the migration raises `ModulePlaneSelectionError` before any
        # DDL runs, which is the correct behaviour and the reason this is not
        # boilerplate — an assembly that forgot it fails loudly at deploy rather
        # than silently installing whichever planes the lineage felt like.
        cfg.attributes["module_plane_selections"] = (
            ModulePlaneSelection(
                module="brand_profiles",
                planes=(ModulePlane.TENANT, ModulePlane.PLATFORM),
            ),
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")

        yield (
            admin_url,
            _url_for(superuser, name, user="platform_api"),
            _url_for(superuser, name, user="app_user"),
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


def _plane_installed(admin_url: str, table: str) -> bool:
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            return (
                conn.execute(
                    text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{table}"}
                ).scalar()
                is not None
            )
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def both_planes(migrated_scratch) -> tuple[str, str, str]:
    """Skip loudly rather than pass vacuously if the composition selected one
    plane. A canary that silently tested nothing would be worse than absent."""
    admin_url, _, _ = migrated_scratch
    if not _plane_installed(admin_url, TENANT_TABLE):
        pytest.skip("the composition did not select the TENANT plane")
    if not _plane_installed(admin_url, PLATFORM_TABLES[0]):
        pytest.skip("the composition did not select the PLATFORM plane")
    return migrated_scratch


def _seed_two_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Two fresh tenants, each with an active brand.

    Slugs are unique per call because the scratch database is MODULE-scoped and
    several tests seed into it. Fixed slugs made every caller after the first
    fail on `tenants.slug`'s unique constraint — and fail in a way that read as
    an isolation failure rather than a fixture one, which is the worst kind of
    false signal a canary can produce.
    """
    a, b = uuid.uuid4(), uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant_id, slug in ((a, f"alpha-{suffix}"), (b, f"bravo-{suffix}")):
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant_id, "slug": slug, "name": slug.split("-")[0].title()},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_brand.brand_profiles ("
                        " id, tenant_id, scope_type, profile_code, display_name,"
                        " status, record_version"
                        ") VALUES (:id, :tenant_id, 'tenant', 'default', :name,"
                        " 'active', 1)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_id,
                        "name": f"{slug.split('-')[0].title()} Brand",
                    },
                )
    finally:
        engine.dispose()
    return a, b


# ── The tenant plane ────────────────────────────────────────────────────────


class TestTheTenantPlaneIsIsolatedByRls:
    def test_rls_is_enabled_and_forced(self, both_planes) -> None:
        """FORCE is the half that is easy to omit and impossible to notice.
        Without it the table OWNER — which migrations run as — bypasses its own
        policy, so every migration-time check passes while production leaks."""
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                enabled, forced = conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = CAST(:t AS regclass)"
                    ),
                    {"t": f"{SCHEMA}.{TENANT_TABLE}"},
                ).one()
            assert enabled, "RLS is not enabled"
            assert forced, "RLS is enabled but not FORCEd"
        finally:
            engine.dispose()

    def test_the_policy_exists(self, both_planes) -> None:
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                policies = (
                    conn.execute(
                        text(
                            "SELECT polname FROM pg_policy "
                            "WHERE polrelid = CAST(:t AS regclass)"
                        ),
                        {"t": f"{SCHEMA}.{TENANT_TABLE}"},
                    )
                    .scalars()
                    .all()
                )
            assert "brand_profiles_tenant_isolation" in policies
        finally:
            engine.dispose()

    def test_one_tenant_cannot_read_anothers_brand(self, both_planes) -> None:
        """The canary itself, driven as `app_user` — the ONLINE role. Driving it
        as `app_admin` would pass against a table with no policy at all, because
        that role carries BYPASSRLS."""
        admin_url, _, app_user_url = both_planes
        alpha, bravo = _seed_two_tenants(admin_url)
        engine = create_engine(app_user_url)
        try:
            with engine.connect() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :t, false)"),
                    {"t": str(alpha)},
                )
                rows = (
                    conn.execute(text("SELECT tenant_id FROM mod_brand.brand_profiles"))
                    .scalars()
                    .all()
                )
            assert rows, "the calling tenant should see its own brand"
            assert set(rows) == {alpha}
            assert bravo not in set(rows)
        finally:
            engine.dispose()

    def test_a_tenant_cannot_write_a_row_for_another(self, both_planes) -> None:
        """`WITH CHECK`, not just `USING`. A policy with only the read half lets
        one tenant insert a brand into another's portal."""
        admin_url, _, app_user_url = both_planes
        alpha, bravo = _seed_two_tenants(admin_url)
        engine = create_engine(app_user_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :t, false)"),
                    {"t": str(alpha)},
                )
                with pytest.raises(DBAPIError):
                    conn.execute(
                        text(
                            "INSERT INTO mod_brand.brand_profiles ("
                            " id, tenant_id, scope_type, profile_code,"
                            " display_name, status, record_version"
                            ") VALUES (:id, :other, 'tenant', 'sneaky',"
                            " 'Injected', 'active', 1)"
                        ),
                        {"id": uuid.uuid4(), "other": bravo},
                    )
        finally:
            engine.dispose()

    def test_the_tenant_column_is_not_nullable(self, both_planes) -> None:
        """A nullable `tenant_id` is a row the policy cannot match and nobody
        can see — ADR-0023 refuses it, and so does hard rule 11."""
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                nullable = conn.execute(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t "
                        "AND column_name = 'tenant_id'"
                    ),
                    {"s": SCHEMA, "t": TENANT_TABLE},
                ).scalar()
            assert nullable == "NO"
        finally:
            engine.dispose()


# ── The platform plane ──────────────────────────────────────────────────────


class TestThePlatformPlaneIsIsolatedByRevoke:
    @pytest.mark.parametrize("table", PLATFORM_TABLES)
    @pytest.mark.parametrize("privilege", ALL_PRIVILEGES)
    def test_app_user_holds_no_privilege(
        self, both_planes, table: str, privilege: str
    ) -> None:
        """All seven, not the four anyone remembers."""
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                held = conn.execute(
                    text("SELECT has_table_privilege('app_user', :t, :p)"),
                    {"t": f"{SCHEMA}.{table}", "p": privilege},
                ).scalar()
            assert not held, (table, privilege)
        finally:
            engine.dispose()

    @pytest.mark.parametrize("table", PLATFORM_TABLES)
    def test_app_user_holds_no_column_level_privilege(
        self, both_planes, table: str
    ) -> None:
        """Column grants survive a table-level REVOKE that names only tables."""
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT column_name, privilege_type "
                        "FROM information_schema.column_privileges "
                        "WHERE table_schema = :s AND table_name = :t "
                        "AND grantee = 'app_user'"
                    ),
                    {"s": SCHEMA, "t": table},
                ).all()
            assert not rows, rows
        finally:
            engine.dispose()

    @pytest.mark.parametrize("table", PLATFORM_TABLES)
    def test_no_platform_table_has_row_level_security(
        self, both_planes, table: str
    ) -> None:
        """Not even ENABLEd-with-no-policy, which denies every row to the control
        plane while reading as protected."""
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                enabled, forced = conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = CAST(:t AS regclass)"
                    ),
                    {"t": f"{SCHEMA}.{table}"},
                ).one()
            assert not enabled and not forced, table
        finally:
            engine.dispose()

    def test_a_real_select_as_app_user_is_refused(self, both_planes) -> None:
        _, _, app_user_url = both_planes
        engine = create_engine(app_user_url)
        try:
            with (
                engine.connect() as conn,
                pytest.raises((DBAPIError, ProgrammingError)),
            ):
                conn.execute(text("SELECT 1 FROM mod_brand.platform_brand_profiles"))
        finally:
            engine.dispose()

    def test_platform_api_can_actually_write_and_read_a_profile(
        self, both_planes
    ) -> None:
        """Declared and unusable is a violation too — every assertion above would
        still pass if `platform_api` had been granted nothing at all."""
        _, platform_url, _ = both_planes
        engine = create_engine(platform_url)
        profile_id = uuid.uuid4()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_brand.platform_brand_profiles ("
                        " id, profile_code, display_name, status, record_version"
                        ") VALUES (:id, :code, 'NDIC Academy', 'active', 1)"
                    ),
                    {"id": profile_id, "code": f"ndic-{uuid.uuid4().hex[:8]}"},
                )
                found = conn.execute(
                    text(
                        "SELECT display_name FROM mod_brand.platform_brand_profiles "
                        "WHERE id = :id"
                    ),
                    {"id": profile_id},
                ).scalar()
            assert found == "NDIC Academy"
        finally:
            engine.dispose()

    def test_platform_api_can_bind_a_host(self, both_planes) -> None:
        """The OEM path end to end, as the online role actually runs it."""
        _, platform_url, _ = both_planes
        engine = create_engine(platform_url)
        profile_id = uuid.uuid4()
        host = f"learn-{uuid.uuid4().hex[:8]}.example"
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_brand.platform_brand_profiles ("
                        " id, profile_code, display_name, status, record_version"
                        ") VALUES (:id, :code, 'NDIC Academy', 'active', 1)"
                    ),
                    {"id": profile_id, "code": f"ndic-{uuid.uuid4().hex[:8]}"},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_brand.platform_brand_host_bindings ("
                        " id, host, profile_id, is_canonical"
                        ") VALUES (:id, :host, :pid, true)"
                    ),
                    {"id": uuid.uuid4(), "host": host, "pid": profile_id},
                )
                resolved = conn.execute(
                    text(
                        "SELECT p.display_name "
                        "FROM mod_brand.platform_brand_host_bindings b "
                        "JOIN mod_brand.platform_brand_profiles p "
                        "  ON p.id = b.profile_id "
                        "WHERE b.host = :host"
                    ),
                    {"host": host},
                ).scalar()
            assert resolved == "NDIC Academy"
        finally:
            engine.dispose()


# ── The rule that connects the two planes ───────────────────────────────────


class TestNoForeignKeyCrossesThePlanes:
    """Hard rule 27: two planes share a lifecycle, never a row.

    This is the assertion the both-planes composition exists for — with only one
    plane installed, a cross-plane foreign key could not be created and the test
    would pass without proving anything.
    """

    def test_no_foreign_key_leaves_the_module_schema(self, both_planes) -> None:
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                foreign = conn.execute(
                    text(
                        """
                        SELECT c.conname, tn.nspname
                        FROM pg_constraint c
                        JOIN pg_class t  ON t.oid  = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        JOIN pg_class tt ON tt.oid = c.confrelid
                        JOIN pg_namespace tn ON tn.oid = tt.relnamespace
                        WHERE c.contype = 'f' AND n.nspname = :s
                          AND tn.nspname <> :s
                        """
                    ),
                    {"s": SCHEMA},
                ).all()
            assert not foreign, foreign
        finally:
            engine.dispose()

    def test_no_foreign_key_joins_a_tenant_table_to_a_platform_one(
        self, both_planes
    ) -> None:
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                edges = conn.execute(
                    text(
                        """
                        SELECT t.relname AS source, tt.relname AS target
                        FROM pg_constraint c
                        JOIN pg_class t  ON t.oid  = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        JOIN pg_class tt ON tt.oid = c.confrelid
                        WHERE c.contype = 'f' AND n.nspname = :s
                        """
                    ),
                    {"s": SCHEMA},
                ).all()
            for source, target in edges:
                source_platform = source.startswith("platform_")
                target_platform = target.startswith("platform_")
                assert source_platform == target_platform, (source, target)
        finally:
            engine.dispose()


# ── Constraints hold against raw SQL ────────────────────────────────────────


class TestTheConstraintsHoldWithoutTheService:
    def test_two_tenants_may_use_the_same_profile_code(self, both_planes) -> None:
        """Hard rule 11's composite unique, from the direction that matters:
        a GLOBAL unique here would make the second tenant's onboarding fail with
        a collision against a row it cannot even see."""
        admin_url, _, _ = both_planes
        alpha, bravo = _seed_two_tenants(admin_url)
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                count = conn.execute(
                    text(
                        "SELECT count(*) FROM mod_brand.brand_profiles "
                        "WHERE profile_code = 'default' AND tenant_id IN "
                        "(:a, :b)"
                    ),
                    {"a": alpha, "b": bravo},
                ).scalar()
            assert count == 2
        finally:
            engine.dispose()

    def test_one_tenant_cannot_reuse_a_profile_code(self, both_planes) -> None:
        admin_url, _, _ = both_planes
        alpha, _ = _seed_two_tenants(admin_url)
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_brand.brand_profiles ("
                        " id, tenant_id, scope_type, profile_code, display_name,"
                        " status, record_version"
                        ") VALUES (:id, :t, 'tenant', 'default', 'Duplicate',"
                        " 'active', 1)"
                    ),
                    {"id": uuid.uuid4(), "t": alpha},
                )
        finally:
            engine.dispose()

    def test_a_host_cannot_be_bound_twice(self, both_planes) -> None:
        """A host has one brand. Two rows would make resolution order-dependent,
        and the order would be whatever the query planner chose that day."""
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        host = f"shared-{uuid.uuid4().hex[:8]}.example"
        try:
            with engine.begin() as conn:
                for _ in range(2):
                    profile_id = uuid.uuid4()
                    conn.execute(
                        text(
                            "INSERT INTO mod_brand.platform_brand_profiles ("
                            " id, profile_code, display_name, status,"
                            " record_version"
                            ") VALUES (:id, :code, 'X', 'active', 1)"
                        ),
                        {"id": profile_id, "code": f"c-{uuid.uuid4().hex[:8]}"},
                    )
                    if profile_id:
                        first = profile_id
                conn.execute(
                    text(
                        "INSERT INTO mod_brand.platform_brand_host_bindings ("
                        " id, host, profile_id, is_canonical"
                        ") VALUES (:id, :host, :pid, false)"
                    ),
                    {"id": uuid.uuid4(), "host": host, "pid": first},
                )
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_brand.platform_brand_host_bindings ("
                        " id, host, profile_id, is_canonical"
                        ") VALUES (:id, :host, :pid, false)"
                    ),
                    {"id": uuid.uuid4(), "host": host, "pid": first},
                )
        finally:
            engine.dispose()

    def test_a_host_binding_cannot_orphan_its_profile(self, both_planes) -> None:
        """`ondelete="RESTRICT"`: a bound profile cannot be deleted out from
        under the host that resolves to it."""
        admin_url, _, _ = both_planes
        engine = create_engine(admin_url)
        profile_id = uuid.uuid4()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO mod_brand.platform_brand_profiles ("
                        " id, profile_code, display_name, status, record_version"
                        ") VALUES (:id, :code, 'X', 'active', 1)"
                    ),
                    {"id": profile_id, "code": f"c-{uuid.uuid4().hex[:8]}"},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_brand.platform_brand_host_bindings ("
                        " id, host, profile_id, is_canonical"
                        ") VALUES (:id, :host, :pid, false)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "host": f"h-{uuid.uuid4().hex[:8]}.example",
                        "pid": profile_id,
                    },
                )
            with engine.begin() as conn, pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "DELETE FROM mod_brand.platform_brand_profiles "
                        "WHERE id = :id"
                    ),
                    {"id": profile_id},
                )
        finally:
            engine.dispose()
