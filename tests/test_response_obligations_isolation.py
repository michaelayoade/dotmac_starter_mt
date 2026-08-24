"""PostgreSQL RLS canary for response obligations.

Unit tests run on SQLite, which has no row-level security and no partial-index
semantics worth trusting. Tenancy correctness and the append-only trigger are
only real here.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
VERSIONS = (
    ROOT
    / "packages/dotmac-response-obligations"
    / "src/dotmac_response_obligations/migrations/versions"
)
TABLES = (
    "sla_policies",
    "sla_targets",
    "sla_clocks",
    "sla_clock_pauses",
    "sla_observations",
)
NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


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
    name = f"resp_obl_{uuid.uuid4().hex[:11]}"
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


def _seed(connection, tenant: uuid.UUID, index: int) -> None:
    policy, target, clock = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    values = {"tenant": tenant, "policy": policy, "target": target, "clock": clock}
    connection.execute(
        text(
            "INSERT INTO public.tenants (id, slug, name) "
            "VALUES (:tenant, :slug, :slug)"
        ),
        {"tenant": tenant, "slug": f"tenant-{index}"},
    )
    connection.execute(
        text(
            "INSERT INTO mod_sla.sla_policies "
            "(id, tenant_id, code, name, subject_type, active) VALUES "
            "(:policy, :tenant, :code, 'Support', 'conversation', true)"
        ),
        values | {"code": f"support-{index}"},
    )
    connection.execute(
        text(
            "INSERT INTO mod_sla.sla_targets "
            "(id, tenant_id, policy_id, kind, priority, target_seconds, "
            "warning_seconds, active) VALUES "
            "(:target, :tenant, :policy, 'FIRST_RESPONSE', NULL, 14400, 1800, true)"
        ),
        values,
    )
    connection.execute(
        text(
            "INSERT INTO mod_sla.sla_clocks "
            "(id, tenant_id, policy_id, target_id, subject_type, "
            "subject_reference, dedup_key, kind, status, started_at, due_at, "
            "total_paused_seconds) VALUES "
            "(:clock, :tenant, :policy, :target, 'conversation', :subject, "
            ":dedup, 'FIRST_RESPONSE', 'RUNNING', :started, :due, 0)"
        ),
        values
        | {
            "subject": f"conv-{index}",
            "dedup": f"clock-{index}",
            "started": NOW,
            "due": NOW + timedelta(hours=4),
        },
    )
    connection.execute(
        text(
            "INSERT INTO mod_sla.sla_clock_pauses "
            "(id, tenant_id, clock_id, reason, paused_at, resumed_at) VALUES "
            "(:id, :tenant, :clock, 'OUTSIDE_BUSINESS_HOURS', :paused, :resumed)"
        ),
        values
        | {
            "id": uuid.uuid4(),
            "paused": NOW + timedelta(hours=1),
            "resumed": NOW + timedelta(hours=2),
        },
    )
    connection.execute(
        text(
            "INSERT INTO mod_sla.sla_observations "
            "(id, tenant_id, clock_id, dedup_key, kind, due_at, observed_at) "
            "VALUES (:id, :tenant, :clock, :dedup, 'BREACH', :due, :observed)"
        ),
        values
        | {
            "id": uuid.uuid4(),
            "dedup": f"sla:{clock}:BREACH",
            "due": NOW + timedelta(hours=4),
            "observed": NOW + timedelta(hours=5),
        },
    )


def test_response_obligation_rows_are_cross_tenant_isolated(
    migrated: tuple[str, str],
) -> None:
    admin_url, app_url = migrated
    tenants = (uuid.uuid4(), uuid.uuid4())
    admin = create_engine(admin_url)
    for index, tenant in enumerate(tenants):
        with admin.begin() as connection:
            _seed(connection, tenant, index)
    admin.dispose()

    app = create_engine(app_url)
    try:
        for tenant in tenants:
            with app.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant)},
                )
                for table in TABLES:
                    query = f"SELECT tenant_id FROM mod_sla.{table}"  # noqa: S608
                    assert set(connection.execute(text(query)).scalars()) == {tenant}
        # A breach that can be edited afterwards is not evidence.
        for statement in (
            "UPDATE mod_sla.sla_observations SET kind = 'WARNING'",
            "DELETE FROM mod_sla.sla_observations",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                with app.begin() as connection:
                    connection.execute(
                        text("SELECT set_config('app.current_tenant', :tenant, true)"),
                        {"tenant": str(tenants[0])},
                    )
                    connection.execute(text(statement))
    finally:
        app.dispose()


def test_one_default_target_per_policy_and_kind_is_enforced_by_the_database(
    migrated: tuple[str, str],
) -> None:
    """PostgreSQL permits many NULLs in a UNIQUE. Without the partial index a
    policy could carry two contradictory defaults and target resolution would
    silently pick one."""
    admin_url, _ = migrated
    tenant = uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        _seed(connection, tenant, 0)
    try:
        with pytest.raises(DBAPIError):
            with admin.begin() as connection:
                policy = connection.execute(
                    text("SELECT id FROM mod_sla.sla_policies LIMIT 1")
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO mod_sla.sla_targets "
                        "(id, tenant_id, policy_id, kind, priority, "
                        "target_seconds, active) VALUES "
                        "(:id, :tenant, :policy, 'FIRST_RESPONSE', NULL, 60, true)"
                    ),
                    {"id": uuid.uuid4(), "tenant": tenant, "policy": policy},
                )
    finally:
        admin.dispose()


def test_a_subject_cannot_hold_two_live_clocks_of_one_kind(
    migrated: tuple[str, str],
) -> None:
    """The second would be measured from a later instant and breach on its own
    schedule, so the two would disagree about when the desk was late."""
    admin_url, _ = migrated
    tenant = uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        _seed(connection, tenant, 0)
    try:
        with pytest.raises(DBAPIError):
            with admin.begin() as connection:
                row = connection.execute(
                    text("SELECT policy_id, target_id FROM mod_sla.sla_clocks LIMIT 1")
                ).one()
                connection.execute(
                    text(
                        "INSERT INTO mod_sla.sla_clocks "
                        "(id, tenant_id, policy_id, target_id, subject_type, "
                        "subject_reference, dedup_key, kind, status, "
                        "started_at, due_at, total_paused_seconds) VALUES "
                        "(:id, :tenant, :policy, :target, 'conversation', "
                        "'conv-0', 'second-clock', 'FIRST_RESPONSE', 'RUNNING', "
                        ":started, :due, 0)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant,
                        "policy": row.policy_id,
                        "target": row.target_id,
                        "started": NOW,
                        "due": NOW + timedelta(hours=4),
                    },
                )
    finally:
        admin.dispose()
