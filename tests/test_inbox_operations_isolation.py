"""PostgreSQL RLS canary for staffed inbox operations."""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_inbox_operations.contracts import (
    AdmitToQueue,
    AssignConversation,
    Conflict,
)
from dotmac_inbox_operations.service import admit_to_queue, assign_conversation
from dotmac_kernel.cache import TenantScope
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

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
    "inbox_routing_decisions",
    "inbox_presence_events",
    "inbox_transfer_requests",
    "inbox_escalation_requests",
    "inbox_offline_dispositions",
)
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


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
            rule, entry = uuid.uuid4(), uuid.uuid4()
            values = {
                "tenant": tenant,
                "queue": queue,
                "assignment": assignment,
                "rule": rule,
                "entry": entry,
            }
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
                values | {"id": rule},
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
                values | {"id": entry, "conversation": f"queued-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_round_robin_cursors "
                    "(id, tenant_id, queue_id, last_assigned_agent_reference, "
                    "rotation_count) VALUES (:id, :tenant, :queue, :agent, 1)"
                ),
                values | {"id": uuid.uuid4(), "agent": f"agent-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_presence_events "
                    "(id, tenant_id, agent_reference, previous_state, state, "
                    "previous_capacity, assignment_capacity, source, "
                    "actor_reference, reason, occurred_at) VALUES "
                    "(:id, :tenant, :agent, 'AWAY', 'AVAILABLE', 2, 2, "
                    "'MANAGER', :actor, 'covering the desk', now())"
                ),
                values
                | {
                    "id": uuid.uuid4(),
                    "agent": f"agent-{index}",
                    "actor": f"supervisor-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_transfer_requests "
                    "(id, tenant_id, conversation_reference, kind, status, "
                    "source_assignment_id, from_agent_reference, "
                    "to_agent_reference, from_queue_id, to_queue_id, reason, "
                    "requested_by_reference, requested_at, expires_at) VALUES "
                    "(:id, :tenant, :conversation, 'WARM', 'REQUESTED', "
                    ":assignment, :agent, :target, :queue, :queue, 'context', "
                    ":agent, now(), now() + interval '5 minutes')"
                ),
                values
                | {
                    "id": uuid.uuid4(),
                    "conversation": f"conversation-{index}",
                    "agent": f"agent-{index}",
                    "target": f"target-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_escalation_requests "
                    "(id, tenant_id, conversation_reference, dedup_key, "
                    "severity, reason, requested_by_reference, notify_reference, "
                    "assignment_id, requested_at) VALUES "
                    "(:id, :tenant, :conversation, :dedup, 'HIGH', 'angry', "
                    ":agent, :lead, :assignment, now())"
                ),
                values
                | {
                    "id": uuid.uuid4(),
                    "conversation": f"conversation-{index}",
                    "dedup": f"escalation-{index}",
                    "agent": f"agent-{index}",
                    "lead": f"lead-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_offline_dispositions "
                    "(id, tenant_id, agent_reference, assignment_id, "
                    "conversation_reference, disposition, status, reason, "
                    "due_at) VALUES (:id, :tenant, :agent, :assignment, "
                    ":conversation, 'REQUEUE', 'PENDING', 'signed out', now())"
                ),
                values
                | {
                    "id": uuid.uuid4(),
                    "agent": f"agent-{index}",
                    "conversation": f"conversation-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_inbox_ops.inbox_routing_decisions "
                    "(id, tenant_id, decision_reference, conversation_reference, "
                    "channel_code, rule_id, queue_id, queue_entry_id, priority, "
                    "decided_at) VALUES (:id, :tenant, :decision, :conversation, "
                    "'whatsapp', :rule, :queue, :entry, 1, now())"
                ),
                values
                | {
                    "id": uuid.uuid4(),
                    "decision": f"decision-{index}",
                    "conversation": f"queued-{index}",
                },
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
        with pytest.raises(DBAPIError, match="append-only"):
            with app.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenants[0])},
                )
                connection.execute(
                    text(
                        "UPDATE mod_inbox_ops.inbox_routing_decisions "
                        "SET priority = priority + 1"
                    )
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with app.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenants[0])},
                )
                connection.execute(
                    text("DELETE FROM mod_inbox_ops.inbox_routing_decisions")
                )
        # Presence transitions and escalation asks are evidence, not state: a
        # manager override that can be edited afterwards proves nothing.
        for append_only in (
            "inbox_presence_events",
            "inbox_escalation_requests",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                with app.begin() as connection:
                    connection.execute(
                        text("SELECT set_config('app.current_tenant', :tenant, true)"),
                        {"tenant": str(tenants[0])},
                    )
                    connection.execute(
                        text(f"DELETE FROM mod_inbox_ops.{append_only}")  # noqa: S608
                    )
    finally:
        app.dispose()


def _seed_operational_queue(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant, queue = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name) "
                "VALUES (:tenant, :slug, :slug)"
            ),
            {"tenant": tenant, "slug": f"race-{tenant.hex[:10]}"},
        )
        connection.execute(
            text(
                "INSERT INTO mod_inbox_ops.inbox_queues "
                "(id, tenant_id, code, name, active) "
                "VALUES (:queue, :tenant, 'support', 'Support', true)"
            ),
            {"tenant": tenant, "queue": queue},
        )
        connection.execute(
            text(
                "INSERT INTO mod_inbox_ops.inbox_agent_presence "
                "(id, tenant_id, agent_reference, state, assignment_capacity, "
                "observed_at) VALUES (:id, :tenant, 'agent-1', 'AVAILABLE', 1, :now)"
            ),
            {"id": uuid.uuid4(), "tenant": tenant, "now": NOW},
        )
    engine.dispose()
    return tenant, queue


def test_concurrent_admission_allocates_distinct_fifo_positions(
    migrated: tuple[str, str],
) -> None:
    admin_url, app_url = migrated
    tenant, queue = _seed_operational_queue(admin_url)
    barrier = threading.Barrier(2, timeout=10)

    def admit(conversation: str) -> int:
        engine = create_engine(app_url)
        try:
            with Session(engine) as db:
                db.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant)},
                )
                barrier.wait()
                row = admit_to_queue(
                    db,
                    scope=TenantScope(tenant),
                    command=AdmitToQueue(queue, conversation, NOW),
                )
                position = row.queue_position
                db.commit()
                return position
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        positions = list(pool.map(admit, ("conversation-a", "conversation-b")))

    assert sorted(positions) == [1, 2]


def test_concurrent_assignment_cannot_overbook_agent_capacity(
    migrated: tuple[str, str],
) -> None:
    admin_url, app_url = migrated
    tenant, queue = _seed_operational_queue(admin_url)
    barrier = threading.Barrier(2, timeout=10)

    def assign(conversation: str) -> str:
        engine = create_engine(app_url)
        try:
            with Session(engine) as db:
                db.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant)},
                )
                barrier.wait()
                try:
                    assign_conversation(
                        db,
                        scope=TenantScope(tenant),
                        command=AssignConversation(
                            conversation,
                            queue,
                            "agent-1",
                            NOW,
                            ("agent-1",),
                            NOW - timedelta(minutes=5),
                        ),
                    )
                    db.commit()
                    return "assigned"
                except Conflict:
                    db.rollback()
                    return "capacity"
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(assign, ("conversation-a", "conversation-b")))

    assert sorted(outcomes) == ["assigned", "capacity"]
