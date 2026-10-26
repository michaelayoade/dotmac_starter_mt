"""Source-only authored canaries for the closed historical maintenance boundary."""

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from dotmac_kernel import audit_actions
from dotmac_kernel.migrations import identity_history as history
from dotmac_kernel.source_applications import InvalidSourceApplicationError
from sqlalchemy.dialects import postgresql


@pytest.fixture
def history_case(monkeypatch):
    now = datetime(2026, 10, 1, tzinfo=UTC)
    tenant, person, role = uuid4(), uuid4(), uuid4()
    timed = {"created_at": now, "updated_at": now + timedelta(seconds=1)}
    rows = history.IdentityHistoryRowsV1(
        tenants=(
            history.HistoricalTenantV1(
                id=tenant,
                slug="source",
                name="Source",
                is_active=True,
                suspended_at=None,
                deleted_at=None,
                **timed,
            ),
        ),
        tenant_domains=(
            history.HistoricalTenantDomainV1(
                id=uuid4(),
                tenant_id=tenant,
                domain="source.example.test",
                verified_at=None,
                **timed,
            ),
        ),
        people=(
            history.HistoricalPersonV1(
                id=person,
                tenant_id=tenant,
                email="person@example.test",
                first_name="First",
                last_name="Last",
                **timed,
            ),
        ),
        user_credentials=tuple(
            history.HistoricalCredentialV1(
                id=uuid4(),
                tenant_id=tenant,
                person_id=person,
                password_hash="synthetic-material-" + str(index),
                **timed,
            )
            for index in range(2)
        ),
        auth_sessions=(
            history.HistoricalSessionV1(
                id=uuid4(),
                tenant_id=tenant,
                person_id=person,
                token_hash="a" * 64,
                expires_at=now + timedelta(days=30),
                revoked_at=None,
                **timed,
            ),
        ),
        roles=(
            history.HistoricalRoleV1(
                id=role, tenant_id=tenant, slug="learner", name="Learner", **timed
            ),
        ),
        person_roles=(
            history.HistoricalPersonRoleV1(
                id=uuid4(), tenant_id=tenant, person_id=person, role_id=role, **timed
            ),
        ),
        audit_events=(
            history.HistoricalAuditEventV1(
                id=uuid4(),
                tenant_id=tenant,
                actor_person_id=person,
                action="historical.action",
                entity_type="person",
                entity_id=str(person),
                details={"known": [None, False, 0]},
                created_at=now,
            ),
        ),
    )
    cohort = history.IdentityHistoryCohortV1(
        **{
            f.name: frozenset(row.id for row in getattr(rows, f.name))
            for f in fields(rows)
        }
    )
    relations = history.IdentityHistoryRelationsV1(
        schema="identity_archive",
        **{f.name: index + 1 for index, f in enumerate(fields(rows))},
    )
    expected = history.IdentityHistoryManifestV1(
        relations=relations,
        cohort=cohort,
        source_revision="a" * 40,
        session_issuance_review_ref="reviewed-password-issuance",
        password_session_ids=cohort.auth_sessions,
        installed_kernel_head="0029_audit_history_shape",
    )
    operation = history.IdentityHistoryImportOperationV1(
        is_active=True,
        activated_person_ids=cohort.people,
        reason="Preserve recognized identities",
        decision_ref="decision-1",
        actor_type="service",
        actor_id="maintenance-owner",
        source_application="dotmac_starter_mt",
    )
    registry = audit_actions.AuditActionRegistry.from_manifests(())
    monkeypatch.setattr(audit_actions, "_active_registry", registry)
    connection = Mock()
    connection.dialect.identifier_preparer.quote_identifier.side_effect = (
        lambda name: '"' + name + '"'
    )

    def ids(statement):
        sql = str(statement)
        family = next(f.name for f in fields(rows) if sql.endswith('."' + f.name + '"'))
        return [row.id for row in getattr(rows, family)]

    connection.scalars.side_effect = ids
    return connection, rows, expected, operation


def test_all_eight_families_preflight_and_complete_projections(history_case):
    connection, rows, expected, operation = history_case
    history._operation_preflight(expected, operation)
    history._rows_preflight(connection, rows, expected)
    projected = list(history._projections(rows))
    assert len(projected) == 10  # eight families, person subtype, extra credential
    for table, values in projected:
        assert table.schema == "public"
        assert set(values) == set(table.c.keys())
    credentials = [
        values for table, values in projected if table.name == "user_credentials"
    ]
    assert len(credentials) == 2
    assert {value["id"] for value in credentials} == expected.cohort.user_credentials
    assert all(value["party_id"] == rows.people[0].id for value in credentials)


def test_historical_audit_unknowns_are_explicit_null_and_times_exact(history_case):
    _, rows, _, _ = history_case
    projected = list(history._projections(rows))
    event = next(values for table, values in projected if table.name == "audit_events")
    original = rows.audit_events[0]
    assert event["id"] == original.id
    assert event["created_at"] == original.created_at
    assert event["actor_party_id"] == original.actor_person_id
    assert event["details"] == original.details
    for name in (
        "actor_type",
        "actor_id",
        "actor_label",
        "source_application",
        "request_id",
        "status_code",
        "is_success",
        "ip_address",
        "user_agent",
        "occurred_at",
    ):
        assert name in event and event[name] is None
    party = next(values for table, values in projected if table.name == "parties")
    assert party["is_active"] is True
    assert party["custom_fields"] == {}
    assert party["display_name"] == "First Last"
    session = next(
        values for table, values in projected if table.name == "auth_sessions"
    )
    assert session["external_identity_binding_id"] is None


@pytest.mark.parametrize(
    "change", ["email", "tenant", "role", "time", "duplicate", "session"]
)
def test_source_preflight_refuses_malformed_population(history_case, change):
    connection, rows, expected, _ = history_case
    if change == "email":
        rows = replace(
            rows, people=(replace(rows.people[0], email=" Person@Example.test "),)
        )
    elif change == "tenant":
        rows = replace(
            rows,
            user_credentials=(
                replace(rows.user_credentials[0], tenant_id=uuid4()),
                rows.user_credentials[1],
            ),
        )
    elif change == "role":
        rows = replace(
            rows, person_roles=(replace(rows.person_roles[0], role_id=uuid4()),)
        )
    elif change == "time":
        rows = replace(
            rows, people=(replace(rows.people[0], created_at=datetime(2026, 1, 1)),)
        )
    elif change == "duplicate":
        rows = replace(rows, people=(rows.people[0], rows.people[0]))
    else:
        rows = replace(
            rows, auth_sessions=(replace(rows.auth_sessions[0], token_hash="invalid"),)
        )
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._rows_preflight(connection, rows, expected)


@pytest.mark.parametrize(
    "change",
    [
        "activation",
        "cohort",
        "issuance",
        "actor",
        "source",
        "reason",
        "owner",
        "uninstalled",
    ],
)
def test_current_activation_provenance_actor_and_registry_preflight(
    history_case, monkeypatch, change
):
    _, _, expected, operation = history_case
    if change == "activation":
        operation = replace(operation, is_active=False)
    elif change == "cohort":
        operation = replace(operation, activated_person_ids=frozenset())
    elif change == "issuance":
        expected = replace(expected, password_session_ids=frozenset())
    elif change == "actor":
        operation = replace(operation, actor_id="")
    elif change == "source":
        operation = replace(operation, source_application="NOT A CODE")
    elif change == "reason":
        operation = replace(operation, reason="")
    elif change == "owner":
        monkeypatch.setattr(
            audit_actions,
            "_active_registry",
            audit_actions.AuditActionRegistry(
                [("academy", "identity.history_imported")]
            ),
        )
    else:
        monkeypatch.setattr(audit_actions, "_active_registry", None)
    expected_error = {
        "source": InvalidSourceApplicationError,
        "uninstalled": audit_actions.AuditActionsNotInstalledError,
    }.get(change, history.IdentityHistoryImportRefused)
    with pytest.raises(expected_error):
        history._operation_preflight(expected, operation)


def test_sensitive_rows_and_driver_failures_are_not_rendered(history_case, monkeypatch):
    connection, rows, expected, operation = history_case
    for family in (rows.user_credentials, rows.auth_sessions, rows.audit_events):
        for row in family:
            assert "synthetic-material" not in repr(row)
            assert "a" * 64 not in repr(row)
    monkeypatch.setattr(
        history,
        "_check_connection",
        Mock(side_effect=RuntimeError("synthetic-sensitive-driver-diagnostic")),
    )
    with pytest.raises(history.IdentityHistoryImportRefused) as refused:
        history.import_identity_history(connection, Mock(), expected, operation)
    assert "synthetic-sensitive" not in str(refused.value)
    assert refused.value.__suppress_context__ is True
    connection.execute.assert_not_called()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()


@pytest.mark.parametrize(
    "unsafe", ["hide", "echo", "results", "autocommit", "nested", "root"]
)
def test_unsafe_connection_refused_before_source_decode(history_case, unsafe):
    connection, _, expected, operation = history_case
    connection.closed = False
    connection.invalidated = False
    connection.in_transaction.return_value = True
    connection.in_nested_transaction.return_value = False
    connection.get_transaction.return_value.is_active = True
    connection.dialect.name = "postgresql"
    connection.dialect.driver = "psycopg"
    connection.connection.driver_connection.autocommit = False
    connection.get_execution_options.return_value = {}
    connection.engine.hide_parameters = True
    connection.engine.echo = False
    connection.engine.logger.isEnabledFor.return_value = False
    if unsafe == "hide":
        connection.engine.hide_parameters = False
    elif unsafe == "echo":
        connection.engine.echo = "debug"
    elif unsafe == "results":
        connection.engine.logger.isEnabledFor.return_value = True
    elif unsafe == "autocommit":
        connection.connection.driver_connection.autocommit = True
    elif unsafe == "nested":
        connection.in_nested_transaction.return_value = True
    else:
        connection.get_transaction.return_value = None
    source = Mock()
    with pytest.raises(history.IdentityHistoryImportRefused):
        history.import_identity_history(connection, source, expected, operation)
    source.decode.assert_not_called()
    connection.execute.assert_not_called()


@pytest.mark.parametrize("details", [[1, None, False], "scalar", 12, False, None])
def test_historical_details_preserve_every_json_shape(history_case, details):
    connection, rows, expected, _ = history_case
    rows = replace(rows, audit_events=(replace(rows.audit_events[0], details=details),))
    history._rows_preflight(connection, rows, expected)
    projected = next(
        values
        for table, values in history._projections(rows)
        if table.name == "audit_events"
    )
    assert projected["details"] == details


def test_historical_actor_uuid_need_not_resolve_to_imported_person(history_case):
    connection, rows, expected, _ = history_case
    old_actor = uuid4()
    rows = replace(
        rows, audit_events=(replace(rows.audit_events[0], actor_person_id=old_actor),)
    )
    history._rows_preflight(connection, rows, expected)
    projected = next(
        values
        for table, values in history._projections(rows)
        if table.name == "audit_events"
    )
    assert projected["actor_party_id"] == old_actor


def test_duplicate_canonical_person_email_is_refused(history_case):
    connection, rows, expected, _ = history_case
    rows = replace(rows, people=(rows.people[0], replace(rows.people[0], id=uuid4())))
    expected = replace(
        expected,
        cohort=replace(
            expected.cohort, people=frozenset(row.id for row in rows.people)
        ),
    )
    original = connection.scalars.side_effect

    def ids(statement):
        if str(statement).endswith('."people"'):
            return [row.id for row in rows.people]
        return original(statement)

    connection.scalars.side_effect = ids
    with pytest.raises(
        history.IdentityHistoryImportRefused, match="person email collision"
    ):
        history._rows_preflight(connection, rows, expected)


@pytest.mark.parametrize(
    "table_name, fault",
    [
        ("tenants", "tenant_write"),
        ("tenant_domains", "platform_missing_delete"),
        ("auth_sessions", "truncate"),
        ("user_credentials", "column_reference"),
    ],
)
def test_online_acl_checks_canonical_role_differences_and_forbidden_rights(
    history_case, table_name, fault
):
    connection, _, _, _ = history_case
    table = next(table for table in history._TARGETS if table.name == table_name)
    observed = {
        "app_user": {"SELECT"}
        if table_name in {"tenants", "tenant_domains"}
        else {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "platform_api": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    }
    column_reference = False
    seen = []

    def privilege(statement, params):
        seen.append((params["role"], params["privilege"]))
        if "has_any_column_privilege" in str(statement):
            return (
                column_reference
                and params["role"] == "app_user"
                and params["privilege"] == "REFERENCES"
            )
        return params["privilege"] in observed[params["role"]]

    connection.scalar.side_effect = privilege
    history._verify_online_privileges(connection, table)
    assert {role for role, _ in seen} == {"app_user", "platform_api"}
    assert {privilege for _, privilege in seen} == {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    }
    if fault == "tenant_write":
        observed["app_user"].add("INSERT")
    elif fault == "platform_missing_delete":
        observed["platform_api"].remove("DELETE")
    elif fault == "truncate":
        observed["app_user"].add("TRUNCATE")
    else:
        column_reference = True
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._verify_online_privileges(connection, table)


@pytest.mark.parametrize(
    "raw_json",
    [
        '{"nested":[0.12345678901234567890123456789,1e1000]}',
        "9" * 5001,
        "null",
        '[1e1000,"scalar",false]',
    ],
    ids=["nested-precision", "large-integer", "json-null", "array"],
)
def test_raw_jsonb_uses_postgresql_validation_and_text_bind_without_python_numbers(
    history_case, monkeypatch, raw_json
):
    connection, rows, expected, _ = history_case
    wrapper = history.HistoricalJsonbV1(raw_json=raw_json)
    rows = replace(rows, audit_events=(replace(rows.audit_events[0], details=wrapper),))
    connection.scalar.return_value = "object"
    json_guard = Mock()
    json_guard.dumps.side_effect = AssertionError("Python JSON encoding")
    json_guard.loads.side_effect = AssertionError("Python JSON decoding")
    monkeypatch.setattr(history, "json", json_guard)
    history._rows_preflight(connection, rows, expected)
    statement, params = connection.scalar.call_args.args
    assert "jsonb_typeof(CAST(:raw AS JSONB))" in str(statement)
    if params != {"raw": raw_json}:
        pytest.fail("raw JSONB validation binding changed", pytrace=False)
    assert "raw_json" not in repr(wrapper)
    if raw_json in repr(wrapper):
        pytest.fail("raw JSONB escaped wrapper repr", pytrace=False)
    projected = next(
        values
        for table, values in history._projections(rows)
        if table.name == "audit_events"
    )
    assert projected["details"] is wrapper
    inserted = history._historical_insert_values(projected)
    expression = inserted["details"]
    assert expression.type.__class__.__name__ == "JSONB"
    assert expression.clause.type.__class__.__name__ == "Text"
    compiled = expression.compile(dialect=postgresql.dialect())
    if compiled.params != {"historical_jsonb_raw": raw_json}:
        pytest.fail("raw JSONB insertion binding changed", pytrace=False)
    if raw_json in str(compiled):
        pytest.fail("raw JSONB was inlined into SQL", pytrace=False)


@pytest.mark.parametrize("raw_json", [None, b"{}", 1, {"value": 1}])
def test_raw_jsonb_requires_exact_string_before_database_validation(
    history_case, raw_json
):
    connection, rows, expected, _ = history_case
    rows = replace(
        rows,
        audit_events=(
            replace(
                rows.audit_events[0],
                details=history.HistoricalJsonbV1(raw_json=raw_json),
            ),
        ),
    )
    with pytest.raises(history.IdentityHistoryImportRefused):
        history._rows_preflight(connection, rows, expected)
    connection.scalar.assert_not_called()


def test_wrapper_subclass_is_not_an_exact_jsonb_sql_escape(history_case):
    connection, rows, expected, _ = history_case

    class Derived(history.HistoricalJsonbV1):
        pass

    wrapper = Derived(raw_json="{}")
    rows = replace(rows, audit_events=(replace(rows.audit_events[0], details=wrapper),))
    with pytest.raises(TypeError):
        history._rows_preflight(connection, rows, expected)
    connection.scalar.assert_not_called()
    original = {"details": wrapper}
    assert history._historical_insert_values(original) is original


def test_plain_json_insert_values_remain_unchanged():
    for details in ({"value": [None, False, 0]}, [1, None], "scalar", 12, False, None):
        values = {"details": details}
        assert history._historical_insert_values(values) is values


def test_fixed_target_trigger_guard_refuses_enabled_or_unverifiable_state(history_case):
    connection, _, _, _ = history_case
    connection.scalar.return_value = False
    for table in history._TARGETS:
        history._refuse_enabled_target_triggers(connection, table)
    assert len(connection.scalar.call_args_list) == 9
    assert {call.args[1]["table"] for call in connection.scalar.call_args_list} == {
        "public." + table.name for table in history._TARGETS
    }
    for result in (True, None):
        connection.scalar.return_value = result
        with pytest.raises(history.IdentityHistoryImportRefused):
            history._refuse_enabled_target_triggers(connection, history._TARGETS[0])


def test_fixed_target_rule_guard_refuses_enabled_or_unverifiable_state(history_case):
    connection, _, _, _ = history_case
    connection.scalar.return_value = False
    for table in history._TARGETS:
        history._refuse_enabled_target_rules(connection, table)
    assert len(connection.scalar.call_args_list) == 9
    assert {call.args[1]["table"] for call in connection.scalar.call_args_list} == {
        "public." + table.name for table in history._TARGETS
    }
    for call in connection.scalar.call_args_list:
        statement = str(call.args[0])
        assert "pg_rewrite" in statement
        assert "ev_class=to_regclass(:table)" in statement
        assert "ev_enabled <> 'D'" in statement
    for result in (True, None):
        connection.scalar.return_value = result
        with pytest.raises(history.IdentityHistoryImportRefused):
            history._refuse_enabled_target_rules(connection, history._TARGETS[0])
