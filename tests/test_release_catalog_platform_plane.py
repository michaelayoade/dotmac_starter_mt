"""The release catalogue's PLATFORM-plane contract, proven against Postgres.

`0.1.0a4` moved both tables from the manifest's tenant `tables` tuple into
`platform_tables`. That was a correction to the DECLARATION, not to the
database: `rl_0001` has created them with no row-level security and REVOKEd them
from `app_user` since `0.1.0a1`, which is exactly the ADR-0023 platform
contract.

## Why the defect survived three releases

The composed live-catalog gate (`dotmac_kernel.migrations.catalog`) requires RLS
ENABLEd AND FORCEd plus a policy for every table NOT declared platform. Under the
old declaration `mod_rel` would have failed it outright — but nothing ever ran
it, because this repository composes the module in no assembly: `app/assembly.py`
omits it, an import-linter contract forbids `app` importing it, and `alembic.ini`
carries no `rl` lineage. The first failure would therefore have been an adopting
vendor control plane's, at ITS migration run, against a migration that was right
and a manifest that was wrong.

So this file does what the starter's own integration gate cannot: it composes a
scratch database, applies the module's lineage exactly as a vendor assembly
would, and runs the REAL gate over the result.

## What is proven here, and what is proven elsewhere

`tests/test_release_catalog_immutability.py` already drives the online roles
against the shared test database and proves a published artifact cannot be
rewritten. It does not build the gate's registry or audit a schema, so it could
not have caught a plane misdeclaration — the two files are complementary, not
duplicates.

The last test is the sensitivity proof. A gate reporting no violations over a
schema is indistinguishable from a gate that examined nothing, so the same LIVE
schema is re-audited with the tables declared TENANT and must be flagged.

Requires real Postgres (`make test-db-up` / `make test-integration`).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from dotmac_kernel.migrations.catalog import (
    TABLE_PRIVILEGES,
    audit_live_schemas,
    audit_snapshot,
    audited_schemas,
    fetch_snapshot,
)
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_release_catalog.manifest import module
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

SCHEMA = "mod_rel"
PLATFORM_TABLES = ("release_artifacts", "artifact_attestations")

_HEX = "b" * 64
_DIGEST = f"sha256:{_HEX}"
_REF = f"registry.example.com/dotmac/plane-canary@{_DIGEST}"


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — this canary needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_scratch() -> Iterator[tuple[str, str, str]]:
    """`(app_admin, app_user, platform_api)` URLs onto a fresh database holding
    only this module's lineage.

    Deliberately WITHOUT the kernel lineage. `rl_0001` is a lineage root with no
    `depends_on`, no foreign key out of `mod_rel` and no RLS predicate to
    evaluate, so a vendor control plane can install it standalone — and a test
    that quietly leaned on `public.tenants` existing would stop proving that.
    """
    superuser = _superuser_url()
    name = f"release_catalog_plane_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from dotmac_release_catalog.migrations.versions import (  # type: ignore[import-not-found]
            rl_0001_release_artifacts as lineage,
        )

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        admin = create_engine(admin_url)
        try:
            with admin.begin() as connection:
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    lineage.upgrade()
        finally:
            admin.dispose()
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


def _registry() -> NamespaceRegistry:
    """The gate's view of a vendor assembly that composes this module alone."""
    return NamespaceRegistry.from_manifests((module,))


#: The four privileges PostgreSQL can grant per COLUMN. A column-level grant
#: does not register as a table-level one, so `has_table_privilege` alone
#: reports "fully revoked" for a role that can still read the column it was
#: granted — the same reason the kernel gate's own query pairs the two
#: functions.
_COLUMN_GRANTABLE = frozenset({"SELECT", "INSERT", "UPDATE", "REFERENCES"})


def _effectively_holds(conn, role: str, table: str, privilege: str) -> bool:
    """Does `role` effectively hold `privilege` on `table`, by any route?

    "Effectively" is the question that matters: a grant reaching the role
    through PUBLIC or through a role it inherits is still a grant.
    """
    sql = "SELECT has_table_privilege(:r, :t, :p)"
    if privilege in _COLUMN_GRANTABLE:
        sql += " OR has_any_column_privilege(:r, :t, :p)"
    return bool(
        conn.execute(text(sql), {"r": role, "t": table, "p": privilege}).scalar_one()
    )


# ── The gate the old declaration would have failed ───────────────────────────


def test_the_live_catalog_gate_passes_over_the_module_schema(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The decisive test, and the one that fails on `0.1.0a3`'s manifest.

    With both tables declared TENANT, `audit_snapshot` demands RLS ENABLEd AND
    FORCEd plus a policy on each — none of which `rl_0001` creates, correctly,
    because these are platform catalog tables.

    It asserts on what it FOUND as well as on what was flagged: an audit over a
    schema whose tables were never created reports no violations and no
    coverage, and the two are indistinguishable in a green run.
    """
    registry = _registry()
    assert SCHEMA in audited_schemas(registry)

    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            live = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = :s"),
                    {"s": SCHEMA},
                )
            }
            assert live == set(PLATFORM_TABLES), f"{SCHEMA} holds {live}"
            violations = audit_live_schemas(conn, registry)
    finally:
        engine.dispose()
    assert not violations, "module schema violations:\n" + "\n".join(violations)


def test_the_expected_table_set_is_the_platform_plane(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """Declaration and reality agree in both directions.

    `expected_tables` is what the gate diffs the live catalog against, and this
    module is atomic — one supported plane set — so it equals the full declared
    set rather than depending on any assembly selection.
    """
    registry = _registry()
    assert registry.declared_platform_tables(SCHEMA) == frozenset(PLATFORM_TABLES)
    assert registry.expected_tables(SCHEMA) == frozenset(PLATFORM_TABLES)
    assert registry.platform_plane_installed(SCHEMA) is True
    assert registry.tenant_plane_installed(SCHEMA) is False


# ── The platform contract, checked directly ──────────────────────────────────


def test_no_table_carries_row_level_security(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """RLS here would be worse than useless. With no `tenant_id` there is
    nothing for a predicate to test, and RLS ENABLEd with no matching policy
    denies every row — so the control plane would silently read zero artifacts
    rather than error."""
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            for table in PLATFORM_TABLES:
                enabled, forced = conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = :t"
                    ),
                    {"s": SCHEMA, "t": table},
                ).one()
                assert not enabled and not forced, table
                policies = conn.execute(
                    text(
                        "SELECT count(*) FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": SCHEMA, "t": table},
                ).scalar_one()
                assert policies == 0, table
                has_tenant = conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t "
                        "AND column_name = 'tenant_id'"
                    ),
                    {"s": SCHEMA, "t": table},
                ).scalar_one()
                assert has_tenant == 0, table
    finally:
        engine.dispose()


def test_the_tenant_role_holds_no_privilege_at_all(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """On this plane the REVOKE **is** the isolation, so it is checked as
    strictly as a policy is on the other side.

    All seven table privileges, and `has_any_column_privilege` beside
    `has_table_privilege` — a column-level grant does not register as a
    table-level one, so a table-level inquiry alone reports "fully revoked" for
    a role that can still read the column it was granted. Schema USAGE is
    checked too: a platform-only schema must not hand the tenant role
    reachability it has no business holding (kernel `0.1.0a57`).
    """
    admin_url, app_user_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT has_schema_privilege('app_user', :s, 'USAGE')"),
                    {"s": SCHEMA},
                ).scalar_one()
                is False
            )
            # Every privilege PostgreSQL can grant, not just the four DML ones:
            # TRUNCATE empties the table, REFERENCES leaks existence and blocks
            # deletes, TRIGGER attaches code to it.
            assert len(TABLE_PRIVILEGES) == 7
            for table in PLATFORM_TABLES:
                for privilege in TABLE_PRIVILEGES:
                    held = _effectively_holds(
                        conn, "app_user", f"{SCHEMA}.{table}", privilege
                    )
                    assert held is False, f"app_user holds {privilege} on {table}"
                # Specificity: the helper above must not simply answer False for
                # everything, or "app_user holds nothing" is a claim about a
                # broken probe rather than about a revocation.
                assert _effectively_holds(
                    conn, "platform_api", f"{SCHEMA}.{table}", "SELECT"
                )
                assert _effectively_holds(
                    conn, "platform_api", f"{SCHEMA}.{table}", "INSERT"
                )
    finally:
        engine.dispose()

    reader = create_engine(app_user_url)
    try:
        with reader.connect() as conn:
            for table in PLATFORM_TABLES:
                with pytest.raises(DBAPIError):
                    # The interpolated name is this file's own literal tuple,
                    # not input; the check cannot see that.
                    conn.execute(text(f"SELECT 1 FROM {SCHEMA}.{table}"))  # noqa: S608
                conn.rollback()
    finally:
        reader.dispose()


def test_the_online_platform_role_can_still_operate(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """Specificity for the revocation above: proving `app_user` is locked out
    means nothing unless the role that SHOULD work still does. A plane nobody
    can reach passes every prohibition and is still broken — which is why the
    gate itself requires at least one DML privilege for the platform role."""
    _, _, platform_url = migrated_scratch
    engine = create_engine(platform_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO mod_rel.release_artifacts "
                    "(id, product_code, version, artifact_kind, digest, "
                    "artifact_ref) VALUES (:id, 'canary', '1.0.0', "
                    "'container_image', :digest, :ref)"
                ),
                {"id": uuid.uuid4(), "digest": _DIGEST, "ref": _REF},
            )
            assert (
                conn.execute(
                    text("SELECT count(*) FROM mod_rel.release_artifacts")
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_the_offline_role_keeps_its_repair_path(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """Immutability must not mean unrepairable. `app_admin` is the offline
    migration role, and confining correction to it is what makes a repair a
    deliberate act under review rather than an accident during a request."""
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            artifact_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_rel.release_artifacts "
                    "(id, product_code, version, artifact_kind, digest, "
                    "artifact_ref) VALUES (:id, 'canary-admin', '1.0.0', "
                    "'container_image', :digest, :ref)"
                ),
                {
                    "id": artifact_id,
                    "digest": f"sha256:{'a' * 64}",
                    "ref": f"registry.example.com/x@sha256:{'a' * 64}",
                },
            )
            conn.execute(
                text(
                    "UPDATE mod_rel.release_artifacts SET source_revision = "
                    "'corrected' WHERE id = :id"
                ),
                {"id": artifact_id},
            )
    finally:
        engine.dispose()


# ── Sensitivity ──────────────────────────────────────────────────────────────


def test_the_gate_flags_the_same_schema_when_the_tables_are_declared_tenant(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """SENSITIVITY PROOF, and a reconstruction of the defect.

    Same live schema, same audit, one difference: `platform_tables` is empty, as
    it was through `0.1.0a3`. The gate must then demand RLS and a policy on both
    tables and report violations — which is precisely what an adopting vendor
    control plane would have hit.

    Without this, "the gate passes" above is a claim about an audit nobody
    proved was looking.
    """
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            snapshot = fetch_snapshot(
                conn,
                SCHEMA,
                declared_tables=frozenset(PLATFORM_TABLES),
                platform_tables=frozenset(),
            )
    finally:
        engine.dispose()

    violations = audit_snapshot(snapshot)
    assert violations, "the audit found nothing wrong with a mis-declared schema"
    flagged = "\n".join(violations)
    for table in PLATFORM_TABLES:
        assert f"{SCHEMA}.{table}: RLS must be ENABLEd AND FORCEd" in flagged
        assert f"{SCHEMA}.{table}: no RLS policy in pg_policies" in flagged
