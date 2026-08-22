"""PostgreSQL RLS canary for staffed inbox operations."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
VERSIONS = (
    ROOT
    / "packages/dotmac-inbox-operations"
    / "src/dotmac_inbox_operations/migrations/versions"
)
TABLES = (
    "inbox_queues",
    "inbox_routing_rules",
    "inbox_agent_presence",
    "conversation_assignments",
    "inbox_workflow_events",
    "inbox_queue_entries",
    "inbox_round_robin_cursors",
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
    name = f"inbox_ops_{uuid.uuid4().hex[:11]}"
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


def test_inbox_operation_rows_are_cross_tenant_isolated(
    migrated: tuple[str, str],
) -> None:
    admin_url, app_url = migrated
    tenants = (uuid.uuid4(), uuid.uuid4())
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        for index, tenant in enumerate(tenants):
            queue, assignment = uuid.uuid4(), uuid.uuid4()
            values = {"tenant": tenant, "queue": queue, "assignment": assignment}
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:tenant, :slug, :slug)"
                ),
                {"tenant": tenant, "slug": f"io-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_queues "
                    "(id, tenant_id, code, name, active) VALUES "
                    "(:queue, :tenant, :code, 'Support', true)"
                ),
                values | {"code": f"support-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_routing_rules "
                    "(id, tenant_id, queue_id, channel_code, priority, active) "
                    "VALUES (:id, :tenant, :queue, 'whatsapp', 1, true)"
                ),
                values | {"id": uuid.uuid4()},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_agent_presence "
                    "(id, tenant_id, agent_reference, state, assignment_capacity, "
                    "observed_at) VALUES (:id, :tenant, :agent, 'AVAILABLE', 2, now())"
                ),
                values | {"id": uuid.uuid4(), "agent": f"agent-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.conversation_assignments "
                    "(id, tenant_id, conversation_reference, queue_id, "
                    "agent_reference, status, assigned_at) VALUES "
                    "(:assignment, :tenant, :conversation, :queue, :agent, "
                    "'ASSIGNED', now())"
                ),
                values
                | {
                    "conversation": f"conversation-{index}",
                    "agent": f"agent-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_workflow_events "
                    "(id, tenant_id, assignment_id, event_type, occurred_at, reason) "
                    "VALUES (:id, :tenant, :assignment, 'ASSIGNED', now(), 'route')"
                ),
                values | {"id": uuid.uuid4()},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_queue_entries "
                    "(id, tenant_id, queue_id, conversation_reference, "
                    "queue_position, status, entered_at) VALUES "
                    "(:id, :tenant, :queue, :conversation, 1, 'QUEUED', now())"
                ),
                values | {"id": uuid.uuid4(), "conversation": f"queued-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_round_robin_cursors "
                    "(id, tenant_id, queue_id, last_assigned_agent_reference, "
                    "rotation_count) VALUES (:id, :tenant, :queue, :agent, 1)"
                ),
                values | {"id": uuid.uuid4(), "agent": f"agent-{index}"},
            )
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
                    query = f"SELECT tenant_id FROM mod_inbox_ops.{table}"  # noqa: S608
                    assert set(connection.execute(text(query)).scalars()) == {tenant}
    finally:
        app.dispose()
