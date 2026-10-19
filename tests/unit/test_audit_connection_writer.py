"""Private current-audit Connection boundary; authored for hosted CI only."""

from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from dotmac_kernel import audit, audit_actions
from dotmac_kernel.features import FeatureManifest
from dotmac_kernel.source_applications import (
    InvalidSourceApplicationError,
    clear_host_application,
)


@pytest.fixture(autouse=True)
def declared_action(_default_declaration_catalogues, _default_attribution, monkeypatch):
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


def _kwargs(**updates):
    return {
        "tenant_id": uuid4(),
        "actor_type": "service",
        "actor_id": "worker-7",
        "action": "probe.connection",
        "entity_type": "probe",
        **updates,
    }


def _connection():
    connection = Mock()
    connection.closed = False
    connection.invalidated = False
    connection.get_transaction.return_value.is_active = True
    connection.in_transaction.return_value = True
    connection.dialect.name = "postgresql"
    connection.dialect.driver = "psycopg"
    connection.get_execution_options.return_value = {}
    connection.connection.driver_connection.autocommit = False
    return connection


@pytest.mark.parametrize(
    "updates, error",
    [
        ({"action": "probe.typo"}, audit_actions.UndeclaredAuditActionError),
        ({"actor_type": None}, audit.MissingAuditActorError),
        ({"actor_type": "robot"}, audit.UnknownAuditActorTypeError),
        ({"actor_type": "user", "actor_id": " "}, audit.MissingAuditActorError),
        ({"source_application": ""}, InvalidSourceApplicationError),
    ],
)
@pytest.mark.parametrize("writer", ["connection", "session"])
def test_both_current_writers_refuse_before_any_mutation(updates, error, writer):
    boundary = _connection() if writer == "connection" else Mock()
    function = (
        audit._write_audit_event_on_connection
        if writer == "connection"
        else audit.write_audit_event
    )
    with pytest.raises(error):
        function(boundary, **_kwargs(**updates))
    assert boundary.mock_calls == []


def test_uninstalled_registry_and_missing_attribution_refuse_before_connection_sql(
    monkeypatch,
):
    connection = _connection()
    registry = audit_actions._active_registry
    monkeypatch.setattr(audit_actions, "_active_registry", None)
    with pytest.raises(audit_actions.AuditActionsNotInstalledError):
        audit._write_audit_event_on_connection(connection, **_kwargs())
    monkeypatch.setattr(audit_actions, "_active_registry", registry)
    clear_host_application()
    with pytest.raises(audit.UnattributedAuditEventError):
        audit._write_audit_event_on_connection(connection, **_kwargs())
    assert connection.mock_calls == []


@pytest.mark.parametrize(
    "defect",
    [
        "missing_root",
        "inactive_root",
        "logical_inactive",
        "closed",
        "invalidated",
        "dialect",
        "driver",
        "unverified_autocommit",
        "driver_autocommit",
        "execution_autocommit",
    ],
)
def test_connection_requires_supported_physical_caller_transaction(defect):
    connection = _connection()
    if defect == "missing_root":
        connection.get_transaction.return_value = None
    elif defect == "inactive_root":
        connection.get_transaction.return_value.is_active = False
    elif defect == "logical_inactive":
        connection.in_transaction.return_value = False
    elif defect in {"closed", "invalidated"}:
        setattr(connection, defect, True)
    elif defect == "dialect":
        connection.dialect.name = "sqlite"
    elif defect == "driver":
        connection.dialect.driver = "unknown"
    elif defect == "unverified_autocommit":
        connection.connection.driver_connection.autocommit = None
    elif defect == "driver_autocommit":
        connection.connection.driver_connection.autocommit = True
    else:
        connection.get_execution_options.return_value = {
            "isolation_level": "AUTOCOMMIT"
        }
    with pytest.raises(ValueError):
        audit._write_audit_event_on_connection(connection, **_kwargs())
    connection.execute.assert_not_called()
    connection.begin.assert_not_called()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()


def test_connection_returns_uuid_without_session_or_transaction_control(
    monkeypatch,
):
    connection = _connection()
    event_id = uuid4()
    connection.execute.return_value.scalar_one.return_value = event_id
    monkeypatch.setattr(
        audit, "Session", Mock(side_effect=AssertionError("Session factory"))
    )
    assert audit._write_audit_event_on_connection(connection, **_kwargs()) == event_id
    connection.execute.assert_called_once()
    connection.begin.assert_not_called()
    connection.begin_nested.assert_not_called()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()


def test_public_session_writer_keeps_add_flush_return_and_field_semantics():
    db = Mock()
    party = uuid4()
    occurred = datetime(2026, 10, 17, tzinfo=UTC)
    details = {"nested": [None, False, 0, {"value": 2.5}]}
    event = audit.write_audit_event(
        db,
        **_kwargs(
            actor_type="api_key",
            actor_id=" key-id ",
            actor_party_id=party,
            actor_label="Display",
            source_application="dotmac_erp",
            occurred_at=occurred,
            details=details,
            request_id="request",
            status_code=202,
            is_success=True,
            ip_address="127.0.0.1",
            user_agent="probe",
            entity_id="entity",
        ),
    )
    assert isinstance(event, audit.AuditEvent)
    db.add.assert_called_once_with(event)
    db.flush.assert_called_once_with()
    assert (event.actor_type, event.actor_id, event.actor_party_id) == (
        "api_key",
        " key-id ",
        party,
    )
    assert event.source_application == "dotmac_erp" and event.actor_label == "Display"
    assert event.occurred_at == occurred and event.details is details
    assert (event.request_id, event.status_code, event.is_success) == (
        "request",
        202,
        True,
    )
    assert (event.ip_address, event.user_agent, event.entity_id) == (
        "127.0.0.1",
        "probe",
        "entity",
    )
    default = audit.write_audit_event(
        Mock(), **_kwargs(actor_type="system", actor_id=" ", details=None)
    )
    assert (
        default.actor_id is None
        and default.details == {}
        and default.occurred_at is None
    )
    assert default.source_application == "dotmac_starter_mt"
