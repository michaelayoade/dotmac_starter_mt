"""Postgres isolation canaries for both ``dotmac-approvals`` planes.

The reference assembly builds but does not install ``dotmac-approvals``, so this
composes its lineage in a scratch database and drives the assertions as the
online roles. SQLite cannot prove row-level security, and a revocation is not a
property any ORM test can observe.

Two different isolation mechanisms are proven here, because the planes use
different ones:

- tenant tables: FORCEd RLS with a policy on `public.app_current_tenant_id()`;
- platform tables: no RLS at all, and `app_user` REVOKEd — there, the
  revocation IS the isolation (ADR-0023), and the control-plane role must still
  be able to work.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
APPROVALS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-approvals/src/dotmac_approvals/migrations/versions"
)

TENANT_TABLES = ("approval_policies", "approval_requests", "approval_decisions")
PLATFORM_TABLES = (
    "platform_approval_policies",
    "platform_approval_requests",
    "platform_approval_decisions",
)
DIGEST = "sha256:" + "a" * 64


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
def migrated_scratch() -> Iterator[tuple[str, str, str]]:
    superuser = _superuser_url()
    name = f"approvals_rls_{uuid.uuid4().hex[:12]}"
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
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {APPROVALS_VERSIONS}",
        )
        cfg.attributes["module_plane_selections"] = (
            ModulePlaneSelection(
                module="approvals",
                planes=(ModulePlane.TENANT, ModulePlane.PLATFORM),
            ),
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


@pytest.fixture
def platform_only_scratch() -> Iterator[str]:
    """The real Vendor shape: kernel tenant objects exist, but this module's
    explicit assembly declaration selects only its platform plane."""
    superuser = _superuser_url()
    name = f"approvals_platform_{uuid.uuid4().hex[:12]}"
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
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {APPROVALS_VERSIONS}",
        )
        cfg.attributes["module_plane_selections"] = (
            ModulePlaneSelection(module="approvals", planes=(ModulePlane.PLATFORM,)),
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url
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


def test_platform_only_selection_is_not_inferred_from_provider_availability(
    platform_only_scratch: str,
) -> None:
    engine = create_engine(platform_only_scratch)
    try:
        with engine.connect() as conn:
            # Kernel 0001 really ran: the false premise in ADR-0027 is now a
            # canary. The tenant catalogue exists and is truthfully bindable.
            assert conn.execute(text("SELECT to_regclass('public.tenants')")).scalar()
            for table in PLATFORM_TABLES:
                assert conn.execute(
                    text("SELECT to_regclass(:table)"),
                    {"table": f"mod_approvals.{table}"},
                ).scalar()
            for table in TENANT_TABLES:
                assert (
                    conn.execute(
                        text("SELECT to_regclass(:table)"),
                        {"table": f"mod_approvals.{table}"},
                    ).scalar()
                    is None
                )
    finally:
        engine.dispose()


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
                policy_id, request_id = uuid.uuid4(), uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO mod_approvals.approval_policies ("
                        "id, tenant_id, policy_code, version, levels, "
                        "allow_self_approval, document_digest"
                        ") VALUES (:id, :tenant, 'payment.release', 1, "
                        "CAST(:levels AS json), false, :digest)"
                    ),
                    {
                        "id": policy_id,
                        "tenant": tenant_id,
                        "levels": '[{"sequence": 1, "approver_kind": "role", '
                        '"approver_id": "r", "quorum": 1, "sod_rule": null, '
                        '"requires_mfa": false, "allow_delegation": true}]',
                        "digest": DIGEST,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_approvals.approval_requests ("
                        "id, tenant_id, policy_code, policy_version, subject_type, "
                        "subject_id, content_digest, requested_by, state, "
                        "current_level, idempotency_key"
                        ") VALUES (:id, :tenant, 'payment.release', 1, "
                        "'finance.payment', :subject, :digest, :actor, 'pending', "
                        "1, :key)"
                    ),
                    {
                        "id": request_id,
                        "tenant": tenant_id,
                        "subject": str(uuid.uuid4()),
                        "digest": DIGEST,
                        "actor": uuid.uuid4(),
                        "key": f"seed-{slug}",
                    },
                )
    finally:
        engine.dispose()
    return tenant_a, tenant_b


def test_every_tenant_table_has_forced_rls_and_a_tenant_policy(
    migrated_scratch: tuple[str, str, str],
) -> None:
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            for table in TENANT_TABLES:
                enabled, forced = conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = CAST(:t AS regclass)"
                    ),
                    {"t": f"mod_approvals.{table}"},
                ).one()
                assert enabled, table
                # FORCEd matters: without it the table owner bypasses the policy,
                # and migrations run as an owner.
                assert forced, table
                policies = [
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT policyname FROM pg_policies "
                            "WHERE schemaname = 'mod_approvals' AND tablename = :t"
                        ),
                        {"t": table},
                    )
                ]
                assert policies == [f"{table}_tenant_isolation"], table
    finally:
        engine.dispose()


def test_one_tenant_cannot_read_or_write_another_tenants_rows(
    migrated_scratch: tuple[str, str, str],
) -> None:
    admin_url, app_user_url, _ = migrated_scratch
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
                    text("SELECT tenant_id FROM mod_approvals.approval_requests")
                )
                .scalars()
                .all()
            )
            assert visible == [tenant_a]

            # And the write side: WITH CHECK must refuse a row for someone else.
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_approvals.approval_requests ("
                        "id, tenant_id, policy_code, policy_version, subject_type,"
                        " subject_id, content_digest, requested_by, state, "
                        "current_level, idempotency_key) VALUES ("
                        ":id, :tenant, 'payment.release', 1, 'finance.payment', "
                        "'x', :digest, :actor, 'pending', 1, 'cross-tenant')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant_b,
                        "digest": DIGEST,
                        "actor": uuid.uuid4(),
                    },
                )
    finally:
        engine.dispose()


def test_platform_tables_are_unreadable_by_the_tenant_role(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """No RLS here by design — the REVOKE is the isolation."""
    admin_url, app_user_url, _ = migrated_scratch
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            for table in PLATFORM_TABLES:
                with pytest.raises(DBAPIError):
                    # The interpolated value is this file's own PLATFORM_TABLES
                    # literal, not input. noqa: the check cannot see that.
                    conn.execute(
                        text(f"SELECT 1 FROM mod_approvals.{table}")  # noqa: S608
                    )
                conn.rollback()
    finally:
        engine.dispose()

    checker = create_engine(admin_url)
    try:
        with checker.connect() as conn:
            for table in PLATFORM_TABLES:
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    granted = conn.execute(
                        text("SELECT has_table_privilege('app_user', :t, :p)"),
                        {"t": f"mod_approvals.{table}", "p": privilege},
                    ).scalar_one()
                    assert granted is False, f"app_user holds {privilege} on {table}"
    finally:
        checker.dispose()


def test_the_platform_runtime_role_can_still_operate(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """Specificity for the revocation above: proving `app_user` is locked out is
    only meaningful if the role that SHOULD work still does."""
    _, _, platform_url = migrated_scratch
    engine = create_engine(platform_url)
    try:
        with engine.begin() as conn:
            request_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_requests ("
                    "id, policy_code, policy_version, subject_type, subject_id, "
                    "content_digest, requested_by, state, current_level, "
                    "idempotency_key) VALUES (:id, 'fleet.plan', 1, 'fleet.plan', "
                    "'plan-1', :digest, :actor, 'pending', 1, 'plan-1')"
                ),
                {"id": request_id, "digest": DIGEST, "actor": uuid.uuid4()},
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM mod_approvals.platform_approval_requests"
                    )
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_no_foreign_key_crosses_the_planes_in_the_live_catalog(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The ORM-level assertion has a live counterpart, because what ships is the
    migration rather than the model."""
    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT src.relname, tgt.relname
                    FROM pg_constraint c
                    JOIN pg_class src ON src.oid = c.conrelid
                    JOIN pg_class tgt ON tgt.oid = c.confrelid
                    JOIN pg_namespace n ON n.oid = src.relnamespace
                    WHERE c.contype = 'f' AND n.nspname = 'mod_approvals'
                    """
                )
            ).all()
        for source, target in rows:
            if target == "tenants":
                assert source in TENANT_TABLES
                continue
            source_is_platform = source in PLATFORM_TABLES
            target_is_platform = target in PLATFORM_TABLES
            assert (
                source_is_platform == target_is_platform
            ), f"{source} -> {target} crosses the tenant/platform boundary"
    finally:
        engine.dispose()


def test_a_duplicate_vote_is_refused_by_the_live_constraint(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """Distinct-actor quorum, proven where it actually holds under concurrency."""
    _, _, platform_url = migrated_scratch
    engine = create_engine(platform_url)
    actor = uuid.uuid4()
    try:
        with engine.begin() as conn:
            request_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_requests ("
                    "id, policy_code, policy_version, subject_type, subject_id, "
                    "content_digest, requested_by, state, current_level, "
                    "idempotency_key) VALUES (:id, 'fleet.plan', 1, 'fleet.plan', "
                    "'plan-2', :digest, :actor, 'pending', 1, 'plan-2')"
                ),
                {"id": request_id, "digest": DIGEST, "actor": actor},
            )
            for _ in range(1):
                conn.execute(
                    text(
                        "INSERT INTO mod_approvals.platform_approval_decisions ("
                        "id, request_id, level, actor_id, action, mfa_verified, "
                        "decided_at) VALUES (:id, :request, 1, :actor, 'approve', "
                        "false, now())"
                    ),
                    {"id": uuid.uuid4(), "request": request_id, "actor": actor},
                )
        with engine.begin() as conn, pytest.raises(DBAPIError):
            conn.execute(
                text(
                    "INSERT INTO mod_approvals.platform_approval_decisions ("
                    "id, request_id, level, actor_id, action, mfa_verified, "
                    "decided_at) VALUES (:id, :request, 1, :actor, 'approve', "
                    "false, now())"
                ),
                {"id": uuid.uuid4(), "request": request_id, "actor": actor},
            )
    finally:
        engine.dispose()


@contextlib.contextmanager
def _bound_prerequisites() -> Iterator[None]:
    """Install this assembly's bindings, and put back whatever was there.

    Bindings are process state; a test that installs and walks away makes the
    NEXT test's result depend on file order.
    """
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        yield
    finally:
        install_prerequisite_bindings(previous)


# ── `outbox_relay.v1` is declared, and verified against the real catalogue ──
#
# `dotmac_approvals.outbox` enqueues into the kernel relay at REQUEST time and
# `ap_0001` creates neither table, so from a1 through a4 the dependency existed
# only inside two function bodies. `ap_0002` declares it; these prove the
# declaration reaches the verifier, and that the verifier is looking at the
# things that actually matter rather than at a table name.


def _relay_requires() -> tuple[str, ...]:
    """The tuple `ap_0002` itself verifies, loaded from the migration.

    Read from the module rather than restated here on purpose: a test that
    hard-codes `("outbox_relay.v1",)` still passes after someone empties the
    migration's own tuple, which is the mistake it exists to catch.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ap_0002_probe", APPROVALS_VERSIONS / "ap_0002_outbox_relay.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.REQUIRES)


@contextlib.contextmanager
def _broken_relay(admin_url: str, statement: str) -> Iterator[Connection]:
    """Apply one DDL break, hand back the connection, roll it back.

    The break lives in an open transaction on the SAME connection the verifier
    reads, so the damage is visible to the check and to nothing else — no
    second migrated database per case.
    """
    engine = create_engine(admin_url)
    conn = engine.connect()
    transaction = conn.begin()
    try:
        conn.execute(text(statement))
        yield conn
    finally:
        transaction.rollback()
        conn.close()
        engine.dispose()


def test_the_declared_relay_prerequisite_is_satisfied_after_migration(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The positive half: what `ap_0002` verifies passes on a real database."""
    from dotmac_kernel.migrations.verify import require_prerequisites

    admin_url, _, _ = migrated_scratch
    engine = create_engine(admin_url)
    with _bound_prerequisites(), engine.connect() as conn:
        require_prerequisites(conn, _relay_requires())
    engine.dispose()


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        pytest.param(
            "ALTER TABLE public.outbox_events RENAME TO outbox_events_gone",
            "does not exist",
            id="tenant-relay-table-absent",
        ),
        pytest.param(
            "ALTER TABLE public.platform_outbox_events "
            "RENAME TO platform_outbox_events_gone",
            "does not exist",
            id="platform-relay-table-absent",
        ),
        pytest.param(
            "ALTER TABLE public.outbox_events NO FORCE ROW LEVEL SECURITY",
            "FORCE",
            id="tenant-relay-unforced",
        ),
        pytest.param(
            "REVOKE EXECUTE ON FUNCTION public.claim_outbox_batch(text, integer, "
            "integer) FROM outbox_dispatcher",
            "EXECUTE",
            id="dispatcher-cannot-claim",
        ),
    ],
)
def test_the_relay_prerequisite_refuses_a_provider_missing_one_effect(
    migrated_scratch: tuple[str, str, str], statement: str, expected: str
) -> None:
    """One break per case, each asserting the message for THAT observable.

    Deliberately a subset of the kernel's own 25 refusals
    (`tests/test_outbox_relay_prerequisite.py`), not a copy of them: the kernel
    owns proving its verifier, and this module owns proving that ITS
    declaration reaches that verifier against a database its own lineage
    migrated. Duplicating the full matrix here would give one invariant two
    owners that drift.
    """
    from dotmac_kernel.migrations.verify import (
        PrerequisiteNotSatisfiedError,
        require_prerequisites,
    )

    admin_url, _, _ = migrated_scratch
    with _bound_prerequisites(), _broken_relay(admin_url, statement) as conn:
        with pytest.raises(PrerequisiteNotSatisfiedError, match=expected):
            require_prerequisites(conn, _relay_requires())


def test_the_relay_refusals_are_not_refusing_everything(
    migrated_scratch: tuple[str, str, str],
) -> None:
    """The specificity companion.

    Every case above damages the relay and expects a refusal, so all of them
    would still pass against a verifier that refused unconditionally — or if
    `_broken_relay`'s open transaction poisoned the connection for any query.
    This breaks something the relay contract does not mention (this module's
    own table) and requires SILENCE.
    """
    from dotmac_kernel.migrations.verify import require_prerequisites

    admin_url, _, _ = migrated_scratch
    with (
        _bound_prerequisites(),
        _broken_relay(
            admin_url, "ALTER TABLE mod_approvals.approval_requests RENAME TO gone"
        ) as conn,
    ):
        require_prerequisites(conn, _relay_requires())
