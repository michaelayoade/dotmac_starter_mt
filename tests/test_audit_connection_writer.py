"""Private audit writer on real migrated PostgreSQL and the caller Connection."""

from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from dotmac_kernel import audit, audit_actions
from dotmac_kernel.features import FeatureManifest
from dotmac_kernel.models import Tenant
from dotmac_kernel.source_applications import (
    InvalidSourceApplicationError,
    clear_host_application,
)
from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


@pytest.fixture
def audit_connection_case(admin_engine, monkeypatch):
    monkeypatch.setattr(
        audit_actions, "_active_registry", audit_actions._active_registry
    )
    audit_actions.install_audit_actions(
        audit_actions.AuditActionRegistry.from_manifests(
            [
                FeatureManifest(
                    name="audit_probe", audit_actions=("probe.connection",)
                ),
            ]
        )
    )
    with admin_engine.connect() as connection:
        transaction = connection.begin()
        tenants = (uuid4(), uuid4())
        try:
            for tenant in tenants:
                connection.execute(
                    insert(Tenant.__table__).values(
                        id=tenant, slug=f"audit-{tenant}", name="Audit probe"
                    )
                )
            yield connection, tenants
        finally:
            if transaction.is_active:
                transaction.rollback()


def _scope(connection, tenant):
    connection.execute(text("SET LOCAL ROLE app_user"))
    connection.execute(
        text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(tenant)},
    )


def _kwargs(tenant, **updates):
    return {
        "tenant_id": tenant,
        "actor_type": "service",
        "actor_id": "worker-7",
        "action": "probe.connection",
        "entity_type": "probe",
        **updates,
    }


def test_actual_connection_current_fields_and_caller_rollback(
    audit_connection_case, monkeypatch
):
    connection, (tenant, _) = audit_connection_case
    root = connection.get_transaction()
    _scope(connection, tenant)
    assert connection.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
    ).one() == (False, False)
    details = {"nested": [None, False, 0, {"value": 2.5}]}
    party_id = uuid4()
    with monkeypatch.context() as guard:
        guard.setattr(
            audit, "Session", Mock(side_effect=AssertionError("Session factory"))
        )
        guard.setattr(
            connection, "commit", Mock(side_effect=AssertionError("internal commit"))
        )
        guard.setattr(
            connection,
            "rollback",
            Mock(side_effect=AssertionError("internal rollback")),
        )
        guard.setattr(
            connection, "begin", Mock(side_effect=AssertionError("internal begin"))
        )
        guard.setattr(
            connection,
            "begin_nested",
            Mock(side_effect=AssertionError("internal savepoint")),
        )
        event_id = audit._write_audit_event_on_connection(
            connection,
            **_kwargs(
                tenant,
                actor_type="api_key",
                actor_id="key-7",
                actor_party_id=party_id,
                actor_label="Display",
                source_application="dotmac_erp",
                details=details,
                request_id="request-7",
                status_code=202,
                is_success=True,
                ip_address="127.0.0.1",
                user_agent="probe",
                entity_id="entity-7",
            ),
        )
    assert connection.get_transaction() is root and root.is_active
    assert isinstance(event_id, UUID)
    row = (
        connection.execute(
            select(audit.AuditEvent.__table__).where(audit.AuditEvent.id == event_id)
        )
        .mappings()
        .one()
    )
    assert row["tenant_id"] == tenant and row["action"] == "probe.connection"
    assert (row["actor_type"], row["actor_id"], row["actor_label"]) == (
        "api_key",
        "key-7",
        "Display",
    )
    assert (
        row["actor_party_id"] is not None and row["source_application"] == "dotmac_erp"
    )
    assert row["details"] == details and row["entity_id"] == "entity-7"
    assert (row["request_id"], row["status_code"], row["is_success"]) == (
        "request-7",
        202,
        True,
    )
    assert (row["ip_address"], row["user_agent"]) == ("127.0.0.1", "probe")
    assert (
        row["occurred_at"]
        == row["created_at"]
        == connection.scalar(text("SELECT transaction_timestamp()"))
    )
    root.rollback()
    with connection.begin():
        assert (
            connection.scalar(
                select(audit.AuditEvent.id).where(audit.AuditEvent.id == event_id)
            )
            is None
        )
        assert connection.scalar(select(Tenant.id).where(Tenant.id == tenant)) is None


def test_connection_and_existing_session_writer_preserve_domain_time_and_defaults(
    audit_connection_case,
):
    connection, (tenant, _) = audit_connection_case
    _scope(connection, tenant)
    occurred = datetime(2026, 10, 17, 8, tzinfo=UTC)
    event_id = audit._write_audit_event_on_connection(
        connection, **_kwargs(tenant, occurred_at=occurred, details=None)
    )
    with Session(bind=connection) as db:
        session_event = audit.write_audit_event(
            db, **_kwargs(tenant, occurred_at=occurred, details=None)
        )
        default_event = audit.write_audit_event(
            db, **_kwargs(tenant, actor_type="system", actor_id=None)
        )
        assert session_event.occurred_at == occurred and session_event.details == {}
        assert default_event.occurred_at == default_event.created_at
        assert (
            default_event.actor_id is None
            and default_event.source_application == "dotmac_starter_mt"
        )
        row = (
            connection.execute(
                select(audit.AuditEvent.__table__).where(
                    audit.AuditEvent.id == event_id
                )
            )
            .mappings()
            .one()
        )
        assert row["occurred_at"] == occurred and row["details"] == {}
        assert (
            row["source_application"]
            == session_event.source_application
            == "dotmac_starter_mt"
        )


@pytest.mark.parametrize(
    "updates, error",
    [
        ({"action": "probe.typo"}, audit_actions.UndeclaredAuditActionError),
        ({"actor_type": "user", "actor_id": None}, audit.MissingAuditActorError),
        ({"actor_type": "robot"}, audit.UnknownAuditActorTypeError),
        ({"source_application": ""}, InvalidSourceApplicationError),
    ],
)
def test_real_connection_validation_refuses_before_sql_and_keeps_caller_usable(
    audit_connection_case, updates, error
):
    connection, (tenant, _) = audit_connection_case
    _scope(connection, tenant)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        with pytest.raises(error):
            audit._write_audit_event_on_connection(
                connection, **_kwargs(tenant, **updates)
            )
        assert statements == []
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert connection.scalar(text("SELECT 42")) == 42
    assert (
        connection.scalar(
            select(audit.AuditEvent.id).where(audit.AuditEvent.tenant_id == tenant)
        )
        is None
    )
    assert (
        audit._write_audit_event_on_connection(connection, **_kwargs(tenant))
        is not None
    )


def test_real_connection_registry_and_host_refusal_before_sql(
    audit_connection_case, monkeypatch
):
    connection, (tenant, _) = audit_connection_case
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        registry = audit_actions._active_registry
        monkeypatch.setattr(audit_actions, "_active_registry", None)
        with pytest.raises(audit_actions.AuditActionsNotInstalledError):
            audit._write_audit_event_on_connection(connection, **_kwargs(tenant))
        monkeypatch.setattr(audit_actions, "_active_registry", registry)
        clear_host_application()
        with pytest.raises(audit.UnattributedAuditEventError):
            audit._write_audit_event_on_connection(connection, **_kwargs(tenant))
        assert statements == []
    finally:
        event.remove(connection, "before_cursor_execute", capture)


def test_connection_writer_retains_rls_and_leaves_sql_failure_for_caller_rollback(
    audit_connection_case,
):
    connection, (tenant, other) = audit_connection_case
    _scope(connection, tenant)
    first = audit._write_audit_event_on_connection(connection, **_kwargs(tenant))
    _scope(connection, other)
    assert (
        connection.scalar(
            select(audit.AuditEvent.id).where(audit.AuditEvent.id == first)
        )
        is None
    )
    # Caller chooses the savepoint; the writer never creates one or rolls it back.
    with pytest.raises(DBAPIError, match="row-level security"):
        with connection.begin_nested():
            audit._write_audit_event_on_connection(connection, **_kwargs(tenant))
    assert connection.scalar(text("SELECT 42")) == 42
    second = audit._write_audit_event_on_connection(connection, **_kwargs(other))
    assert connection.scalars(
        select(audit.AuditEvent.id).where(audit.AuditEvent.id.in_([first, second]))
    ).all() == [second]


def test_engine_configured_autocommit_with_logical_root_is_refused(
    admin_engine, monkeypatch
):
    monkeypatch.setattr(
        audit_actions, "_active_registry", audit_actions._active_registry
    )
    audit_actions.install_audit_actions(
        audit_actions.AuditActionRegistry.from_manifests(
            [
                FeatureManifest(
                    name="audit_probe", audit_actions=("probe.connection",)
                ),
            ]
        )
    )
    engine = create_engine(admin_engine.url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection, connection.begin():
            assert connection.in_transaction()
            # Engine configuration is not a Connection execution-option override.
            assert connection.get_execution_options().get("isolation_level") is None
            assert connection.connection.driver_connection.autocommit is True
            with pytest.raises(ValueError, match="autocommit"):
                audit._write_audit_event_on_connection(connection, **_kwargs(uuid4()))
    finally:
        engine.dispose()


def test_connection_without_caller_root_is_refused_before_autobegin(
    admin_engine, monkeypatch
):
    monkeypatch.setattr(
        audit_actions, "_active_registry", audit_actions._active_registry
    )
    audit_actions.install_audit_actions(
        audit_actions.AuditActionRegistry.from_manifests(
            [
                FeatureManifest(
                    name="audit_probe", audit_actions=("probe.connection",)
                ),
            ]
        )
    )
    with admin_engine.connect() as connection:
        assert not connection.in_transaction()
        with pytest.raises(ValueError, match="caller transaction"):
            audit._write_audit_event_on_connection(connection, **_kwargs(uuid4()))
        assert not connection.in_transaction()


def test_logical_root_before_first_sql_is_supported(
    admin_engine, tenant_a, monkeypatch
):
    monkeypatch.setattr(
        audit_actions, "_active_registry", audit_actions._active_registry
    )
    audit_actions.install_audit_actions(
        audit_actions.AuditActionRegistry.from_manifests(
            [FeatureManifest(name="audit_probe", audit_actions=("probe.connection",))]
        )
    )
    tenant_id = tenant_a.id
    with admin_engine.connect() as connection:
        root = connection.begin()
        assert (
            connection.connection.driver_connection.info.transaction_status.name
            == "IDLE"
        )
        event_id = audit._write_audit_event_on_connection(
            connection, **_kwargs(tenant_id)
        )
        assert connection.get_transaction() is root and root.is_active
        assert (
            connection.scalar(
                select(audit.AuditEvent.id).where(audit.AuditEvent.id == event_id)
            )
            == event_id
        )
        root.rollback()
        with connection.begin():
            assert (
                connection.scalar(
                    select(audit.AuditEvent.id).where(audit.AuditEvent.id == event_id)
                )
                is None
            )
