"""PostgreSQL proof of the money-flows ``mf_0001`` Settle instance table."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
VERSIONS = (
    ROOT / "packages/dotmac-money-flows/src/dotmac_money_flows/migrations/versions"
)
TABLE = "mod_mflows.settle_instances"

_INSERT = text(
    "INSERT INTO mod_mflows.settle_instances "
    "(id, tenant_id, state, reason_code, policy_version, definition_version, "
    "confirmation_id, intent_id, settlement_id, resolution_ref) "
    "VALUES (:id, :tenant, :state, :reason, 'p1', 'd1', :confirmation, :intent, "
    ":settlement, :resolution)"
)


def _url(base: str, database: str, user: str | None = None) -> str:
    prefix, _, _ = base.rpartition("/")
    if user:
        scheme, _, host = prefix.partition("://")
        prefix = f"{scheme}://{user}@{host.rpartition('@')[2]}"
    return f"{prefix}/{database}"


@pytest.fixture
def migrated() -> Iterator[tuple[str, str]]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    if not superuser:
        pytest.skip("PostgreSQL URL required")
    name = f"mflows_{uuid.uuid4().hex[:12]}"
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
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
        os.environ["MIGRATION_DATABASE_URL"] = admin
        command.upgrade(cfg, "heads")
        yield admin, _url(superuser, name, "app_user")
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


def _tenant(admin_url: str) -> uuid.UUID:
    tenant = uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO public.tenants (id, slug, name) VALUES (:id, :s, :s)"),
            {"id": tenant, "s": f"mf-{tenant}"},
        )
    engine.dispose()
    return tenant


def _insert(connection, tenant, state, **overrides) -> uuid.UUID:
    row_id = uuid.uuid4()
    values = {
        "id": row_id,
        "tenant": tenant,
        "state": state,
        "reason": None,
        "confirmation": uuid.uuid4(),
        "intent": uuid.uuid4(),
        "settlement": None,
        "resolution": None,
    }
    values.update(overrides)
    connection.execute(_INSERT, values)
    return row_id


def _as_tenant(connection, tenant) -> None:
    connection.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant)}
    )


def test_table_has_forced_rls_and_no_delete_grant(migrated) -> None:
    engine = create_engine(migrated[0])
    with engine.begin() as connection:
        enabled, forced = connection.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'mod_mflows.settle_instances'::regclass"
            )
        ).one()
        assert enabled and forced
        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", True),
            ("DELETE", False),
            ("TRUNCATE", False),
        ):
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege('app_user', :t, :p)"),
                    {"t": TABLE, "p": privilege},
                )
                is expected
            ), privilege
        columns = set(
            connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'mod_mflows' "
                    "AND table_name = 'settle_instances'"
                )
            )
        )
        for word in ("amount", "currency", "balance", "total"):
            assert not [c for c in columns if word in c]
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE table_schema = 'mod_mflows' "
                    "AND constraint_type = 'FOREIGN KEY'"
                )
            )
            == 1
        )  # the kernel tenants table only
    engine.dispose()


def test_rows_are_tenant_isolated_and_cross_tenant_writes_refused(migrated) -> None:
    admin_url, app_url = migrated
    first, second = _tenant(admin_url), _tenant(admin_url)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            _as_tenant(connection, first)
            _insert(connection, first, "refused", reason="held")
        with app.begin() as connection:
            _as_tenant(connection, second)
            assert connection.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 0  # noqa: S608
        with pytest.raises(DBAPIError), app.begin() as connection:
            _as_tenant(connection, second)
            _insert(connection, first, "refused", reason="held")
    finally:
        app.dispose()


def test_app_user_cannot_delete(migrated) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            _as_tenant(connection, tenant)
            _insert(connection, tenant, "refused", reason="held")
        with pytest.raises(ProgrammingError), app.begin() as connection:
            _as_tenant(connection, tenant)
            connection.execute(text(f"DELETE FROM {TABLE}"))  # noqa: S608
    finally:
        app.dispose()


@pytest.mark.parametrize(
    "state, overrides",
    [
        ("settled", {"settlement": uuid.UUID(int=1)}),
        ("refused", {"reason": "account_held"}),
        ("unapplied", {"reason": "document_closed", "settlement": uuid.UUID(int=2)}),
        ("resolved", {"resolution": "f3-instance"}),
    ],
)
def test_each_valid_state_shape_is_accepted(migrated, state, overrides) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            _as_tenant(connection, tenant)
            _insert(connection, tenant, state, **overrides)
    finally:
        app.dispose()


@pytest.mark.parametrize(
    "state, overrides",
    [
        ("pending", {}),  # unknown state
        ("settled", {}),  # settled needs a settlement
        ("settled", {"settlement": uuid.UUID(int=1), "reason": "x"}),
        ("refused", {}),  # refused needs a reason
        ("refused", {"reason": "x", "settlement": uuid.UUID(int=1)}),
        ("unapplied", {"reason": "x"}),  # unapplied needs a settlement
        ("unapplied", {"settlement": uuid.UUID(int=1)}),  # and a reason
        ("resolved", {}),  # resolved needs a resolution
        (
            "refused",
            {"reason": "x", "resolution": "ref"},
        ),  # resolution only if resolved
    ],
)
def test_check_constraints_refuse_invalid_shapes(migrated, state, overrides) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        with pytest.raises(IntegrityError), app.begin() as connection:
            _as_tenant(connection, tenant)
            _insert(connection, tenant, state, **overrides)
    finally:
        app.dispose()


def test_one_instance_per_confirmation(migrated) -> None:
    admin_url, app_url = migrated
    tenant, other = _tenant(admin_url), _tenant(admin_url)
    confirmation = uuid.uuid4()
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            _as_tenant(connection, tenant)
            _insert(
                connection, tenant, "refused", reason="x", confirmation=confirmation
            )
        with pytest.raises(IntegrityError), app.begin() as connection:
            _as_tenant(connection, tenant)
            _insert(
                connection, tenant, "refused", reason="y", confirmation=confirmation
            )
        # Another tenant may carry the same confirmation id.
        with app.begin() as connection:
            _as_tenant(connection, other)
            _insert(connection, other, "refused", reason="x", confirmation=confirmation)
    finally:
        app.dispose()


def test_trigger_refuses_identity_changes_and_terminal_exits(migrated) -> None:
    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    try:
        with app.begin() as connection:
            _as_tenant(connection, tenant)
            refused = _insert(connection, tenant, "refused", reason="held")
            settled = _insert(
                connection, tenant, "settled", settlement=uuid.UUID(int=7)
            )
            resolved = _insert(connection, tenant, "resolved", resolution="f3")

        for column, value in (
            ("confirmation_id", uuid.uuid4()),
            ("intent_id", uuid.uuid4()),
            ("tenant_id", uuid.uuid4()),
            ("id", uuid.uuid4()),
            ("created_at", "2001-01-01T00:00:00+00"),
        ):
            with (
                pytest.raises(DBAPIError, match="identity is immutable"),
                app.begin() as connection,
            ):
                _as_tenant(connection, tenant)
                connection.execute(
                    text(
                        f"UPDATE {TABLE} SET {column} = :v WHERE id = :id"  # noqa: S608
                    ),
                    {"v": value, "id": refused},
                )

        # Terminal rows refuse every update, even one that changes nothing.
        for row in (settled, resolved):
            for assignment in ("state = 'refused'", "version = version + 1"):
                with (
                    pytest.raises(DBAPIError, match="terminal state"),
                    app.begin() as connection,
                ):
                    _as_tenant(connection, tenant)
                    connection.execute(
                        text(f"UPDATE {TABLE} SET {assignment} WHERE id = :id"),  # noqa: S608
                        {"id": row},
                    )

        # A non-terminal row may move, and the database clock stamps updated_at.
        with app.begin() as connection:
            _as_tenant(connection, tenant)
            before = connection.scalar(
                text(f"SELECT updated_at FROM {TABLE} WHERE id = :id"),  # noqa: S608
                {"id": refused},
            )
            connection.execute(
                text(
                    f"UPDATE {TABLE} SET state = 'settled', reason_code = NULL, "  # noqa: S608
                    "settlement_id = :s, version = version + 1 WHERE id = :id"
                ),
                {"s": uuid.uuid4(), "id": refused},
            )
            state, version, after = connection.execute(
                text(f"SELECT state, version, updated_at FROM {TABLE} WHERE id = :id"),  # noqa: S608
                {"id": refused},
            ).one()
            assert (state, version) == ("settled", 1)
            assert after >= before
        # ...and is then terminal.
        with (
            pytest.raises(DBAPIError, match="terminal state"),
            app.begin() as connection,
        ):
            _as_tenant(connection, tenant)
            connection.execute(
                text(f"UPDATE {TABLE} SET actor_ref = 'x' WHERE id = :id"),  # noqa: S608
                {"id": refused},
            )
    finally:
        app.dispose()


def test_downgrade_refuses_while_rows_exist_and_reupgrades_when_empty(migrated) -> None:
    from alembic import command
    from alembic.config import Config

    admin_url, app_url = migrated
    tenant = _tenant(admin_url)
    app = create_engine(app_url)
    with app.begin() as connection:
        _as_tenant(connection, tenant)
        _insert(connection, tenant, "refused", reason="held")
    app.dispose()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
    with pytest.raises(RuntimeError, match="instance rows exist"):
        command.downgrade(cfg, "money_flows@base")
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        assert connection.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 1  # noqa: S608
        connection.execute(text("SET LOCAL row_security = off"))
        connection.execute(text(f"DELETE FROM {TABLE}"))  # noqa: S608
    command.downgrade(cfg, "money_flows@base")
    with engine.begin() as connection:
        assert (
            connection.scalar(text("SELECT to_regclass('mod_mflows.settle_instances')"))
            is None
        )
    command.upgrade(cfg, "money_flows@head")
    engine.dispose()
