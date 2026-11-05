"""PostgreSQL proofs: stale refusal, out-of-order and concurrent versioned writes."""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_service_access_policy import (
    AccessInputDecision,
    AccessInputRefused,
    AccessSignal,
    RecordAccessInputV2,
    record_access_input_v2,
)
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
VERSIONS = (
    ROOT
    / "packages/dotmac-service-access-policy"
    / "src/dotmac_service_access_policy/migrations/versions"
)
NOW = datetime(2026, 11, 3, tzinfo=UTC)
SERVICE = "service:pg"


def _url(base: str, database: str, user: str | None = None) -> str:
    prefix, _, _ = base.rpartition("/")
    if user:
        scheme, _, host = prefix.partition("://")
        prefix = f"{scheme}://{user}@{host.rpartition('@')[2]}"
    return f"{prefix}/{database}"


def _alembic(admin: str, command_name: str, target: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
    os.environ["MIGRATION_DATABASE_URL"] = admin
    getattr(command, command_name)(cfg, target)


@pytest.fixture
def migrated() -> Iterator[tuple[str, str, str]]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    if not superuser:
        pytest.skip("PostgreSQL URL required")
    name = f"access_ver_{uuid.uuid4().hex[:9]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin = _url(superuser, name, "app_admin")
    try:
        _alembic(admin, "upgrade", "heads")
        yield admin, _url(superuser, name, "app_user"), superuser
    finally:
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


@pytest.fixture
def tenant(migrated: tuple[str, str, str]) -> Iterator[uuid.UUID]:
    tenant_id = uuid.uuid4()
    admin = create_engine(migrated[0])
    with admin.begin() as connection:
        connection.execute(
            text("INSERT INTO public.tenants (id, slug, name) VALUES (:i, :s, :s)"),
            {"i": tenant_id, "s": f"v-{tenant_id.hex[:8]}"},
        )
    admin.dispose()
    yield tenant_id


def _command(
    version: int, active: bool = True, **changes: object
) -> RecordAccessInputV2:
    values: dict[str, object] = {
        "service_reference": SERVICE,
        "signal": AccessSignal.PREPAID_DEPLETED,
        "active": active,
        "source_reference": f"flows:{version}",
        "observed_at": NOW,
        "source_version": version,
        "policy_version": "policy-1",
    }
    values.update(changes)
    return RecordAccessInputV2(**values)  # type: ignore[arg-type]


@contextmanager
def _tx(engine: Engine, tenant_id: uuid.UUID) -> Iterator[Session]:
    """One tenant-scoped transaction: commit on success, roll back on error."""
    with Session(engine) as session:
        session.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(tenant_id)},
        )
        try:
            yield session
        except BaseException:
            session.rollback()
            raise
        session.commit()


def _stored(engine: Engine, tenant_id: uuid.UUID) -> tuple[int, bool, str]:
    with _tx(engine, tenant_id) as session:
        row = session.execute(
            text(
                "SELECT source_version, active, source_reference "
                "FROM mod_serviceaccess.service_access_inputs "
                "WHERE service_reference = :s"
            ),
            {"s": SERVICE},
        ).one()
        return (row[0], row[1], row[2])


def _record(
    engine: Engine, tenant_id: uuid.UUID, command: RecordAccessInputV2
) -> AccessInputDecision:
    with _tx(engine, tenant_id) as session:
        return record_access_input_v2(
            session, scope=TenantScope(tenant_id), command=command
        ).decision


def test_out_of_order_and_replay_against_postgresql(
    migrated: tuple[str, str, str], tenant: uuid.UUID
) -> None:
    app = create_engine(migrated[1])
    try:
        assert _record(app, tenant, _command(5)) is AccessInputDecision.APPLY
        assert _record(app, tenant, _command(8, active=False)) is (
            AccessInputDecision.APPLY
        )
        with pytest.raises(AccessInputRefused) as stale:
            _record(app, tenant, _command(6, active=True))
        assert stale.value.reason_code == "ACCESS_INPUT_STALE"
        assert _record(app, tenant, _command(8, active=False)) is (
            AccessInputDecision.IDEMPOTENT
        )
        with pytest.raises(AccessInputRefused) as conflict:
            _record(app, tenant, _command(8, active=True))
        assert conflict.value.reason_code == "ACCESS_INPUT_VERSION_CONFLICT"
        assert _stored(app, tenant) == (8, False, "flows:8")
    finally:
        app.dispose()


def _race(
    app: Engine,
    tenant_id: uuid.UUID,
    monitor: Engine,
    first: RecordAccessInputV2,
    second: RecordAccessInputV2,
) -> tuple[object, object]:
    """Hold ``first`` uncommitted, start ``second`` and prove it blocks, then commit."""
    holding = threading.Event()
    release = threading.Event()
    results: dict[str, object] = {}

    def writer_one() -> None:
        try:
            with _tx(app, tenant_id) as session:
                results["one"] = record_access_input_v2(
                    session, scope=TenantScope(tenant_id), command=first
                ).decision
                holding.set()
                assert release.wait(20)
        except Exception as exc:
            results["one"] = exc
            holding.set()

    def writer_two() -> None:
        try:
            results["two"] = _record(app, tenant_id, second)
        except Exception as exc:
            results["two"] = exc

    one = threading.Thread(target=writer_one)
    one.start()
    assert holding.wait(20)
    two = threading.Thread(target=writer_two)
    two.start()
    # The second writer must be waiting on the first writer's lock, not finished.
    waited = False
    for _ in range(100):
        with monitor.connect() as connection:
            waited = bool(
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE wait_event_type = 'Lock' AND datname LIKE 'access_ver_%'"
                    )
                ).scalar()
            )
        if waited or "two" in results:
            break
        threading.Event().wait(0.1)
    assert waited, "second writer did not block on the first writer"
    assert "two" not in results
    release.set()
    one.join(30)
    two.join(30)
    return results["one"], results["two"]


def test_concurrent_insert_stale_loser_is_refused(
    migrated: tuple[str, str, str], tenant: uuid.UUID
) -> None:
    app, admin = create_engine(migrated[1]), create_engine(migrated[2])
    try:
        one, two = _race(app, tenant, admin, _command(9), _command(4, active=False))
        assert one is AccessInputDecision.APPLY
        assert isinstance(two, AccessInputRefused)
        assert two.reason_code == "ACCESS_INPUT_STALE"
        assert _stored(app, tenant) == (9, True, "flows:9")
    finally:
        app.dispose()
        admin.dispose()


def test_concurrent_update_newer_loser_wins_after_waiting(
    migrated: tuple[str, str, str], tenant: uuid.UUID
) -> None:
    app, admin = create_engine(migrated[1]), create_engine(migrated[2])
    try:
        assert _record(app, tenant, _command(1)) is AccessInputDecision.APPLY
        one, two = _race(app, tenant, admin, _command(5), _command(6, active=False))
        assert (one, two) == (AccessInputDecision.APPLY, AccessInputDecision.APPLY)
        assert _stored(app, tenant) == (6, False, "flows:6")
    finally:
        app.dispose()
        admin.dispose()


def test_concurrent_update_stale_writer_cannot_clobber(
    migrated: tuple[str, str, str], tenant: uuid.UUID
) -> None:
    app, admin = create_engine(migrated[1]), create_engine(migrated[2])
    try:
        assert _record(app, tenant, _command(1)) is AccessInputDecision.APPLY
        one, two = _race(app, tenant, admin, _command(7), _command(3, active=False))
        assert one is AccessInputDecision.APPLY
        assert isinstance(two, AccessInputRefused)
        assert _stored(app, tenant) == (7, True, "flows:7")
    finally:
        app.dispose()
        admin.dispose()


def test_row_lock_closes_the_read_then_update_window(
    migrated: tuple[str, str, str], tenant: uuid.UUID
) -> None:
    """B skips its insert, A then updates (uncommitted), B must not act on old data.

    Without ``FOR UPDATE`` B would read the old committed version, decide APPLY
    and overwrite A's newer state once A commits.
    """
    app_a, app_b = create_engine(migrated[1]), create_engine(migrated[1])
    monitor = create_engine(migrated[2])
    b_inserted, a_updated = threading.Event(), threading.Event()
    results: dict[str, object] = {}

    @event.listens_for(app_b, "after_cursor_execute")
    def pause_after_insert(*args: object) -> None:
        statement = str(args[2])
        if statement.lstrip().upper().startswith("INSERT") and (
            "service_access_inputs" in statement
        ):
            b_inserted.set()
            assert a_updated.wait(20)

    def writer_b() -> None:
        try:
            results["b"] = _record(app_b, tenant, _command(3, active=False))
        except Exception as exc:
            results["b"] = exc

    try:
        assert _record(app_a, tenant, _command(1)) is AccessInputDecision.APPLY
        thread = threading.Thread(target=writer_b)
        thread.start()
        assert b_inserted.wait(20)
        with _tx(app_a, tenant) as session:
            record_access_input_v2(
                session, scope=TenantScope(tenant), command=_command(7)
            )
            a_updated.set()
            for _ in range(100):
                with monitor.connect() as connection:
                    if connection.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE "
                            "wait_event_type = 'Lock' AND datname LIKE 'access_ver_%'"
                        )
                    ).scalar():
                        break
                threading.Event().wait(0.1)
            assert "b" not in results
        thread.join(30)
        assert isinstance(results["b"], AccessInputRefused)
        assert _stored(app_a, tenant) == (7, True, "flows:7")
    finally:
        a_updated.set()
        app_a.dispose()
        app_b.dispose()
        monitor.dispose()


def test_concurrent_identical_writes_apply_once(
    migrated: tuple[str, str, str], tenant: uuid.UUID
) -> None:
    app, admin = create_engine(migrated[1]), create_engine(migrated[2])
    try:
        same = _command(2, source_reference="flows:same")
        one, two = _race(app, tenant, admin, same, same)
        assert one is AccessInputDecision.APPLY
        assert two is AccessInputDecision.IDEMPOTENT
    finally:
        app.dispose()
        admin.dispose()


def test_version_pair_check_and_rls_and_migration_round_trip(
    migrated: tuple[str, str, str], tenant: uuid.UUID
) -> None:
    admin_url, app_url, _ = migrated
    admin = create_engine(admin_url)
    try:
        with pytest.raises(IntegrityError), admin.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO mod_serviceaccess.service_access_inputs "
                    "(id, tenant_id, service_reference, signal, active, "
                    "source_reference, observed_at, source_version) VALUES "
                    "(:i, :t, 'half', 'ADMIN_HOLD', true, 'x', now(), 1)"
                ),
                {"i": uuid.uuid4(), "t": tenant},
            )
        with pytest.raises(IntegrityError), admin.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO mod_serviceaccess.service_access_inputs "
                    "(id, tenant_id, service_reference, signal, active, "
                    "source_reference, observed_at, source_version, policy_version)"
                    " VALUES (:i, :t, 'neg', 'ADMIN_HOLD', true, 'x', now(), -1, 'p')"
                ),
                {"i": uuid.uuid4(), "t": tenant},
            )
    finally:
        admin.dispose()
    # A second tenant cannot see or order against the first tenant's versions.
    other = uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        connection.execute(
            text("INSERT INTO public.tenants (id, slug, name) VALUES (:i, :s, :s)"),
            {"i": other, "s": f"o-{other.hex[:8]}"},
        )
    admin.dispose()
    app = create_engine(app_url)
    try:
        assert _record(app, tenant, _command(50)) is AccessInputDecision.APPLY
        assert _record(app, other, _command(1)) is AccessInputDecision.APPLY
        assert _stored(app, other)[0] == 1
    finally:
        app.dispose()


def test_sap_0002_downgrade_and_reupgrade(migrated: tuple[str, str, str]) -> None:
    admin_url = migrated[0]
    _alembic(admin_url, "downgrade", "sap_0001_access_policy")
    admin = create_engine(admin_url)
    try:
        with admin.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'mod_serviceaccess' "
                        "AND table_name = 'service_access_inputs'"
                    )
                ).scalars()
            )
        assert not columns & {"source_version", "policy_version"}
        _alembic(admin_url, "upgrade", "sap_0002_input_versions")
        with admin.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'mod_serviceaccess' "
                        "AND table_name = 'service_access_inputs'"
                    )
                ).scalars()
            )
        assert {"source_version", "policy_version"} <= columns
    finally:
        admin.dispose()
