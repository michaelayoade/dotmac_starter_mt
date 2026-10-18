"""Hosted PostgreSQL canaries on the already-migrated reference admin_engine.

No self-migration or replacement Kernel lineage. Root fixture prerequisites:
admin_engine must be created with hide_parameters=True, echo=False and no DEBUG
result logging. Synthetic fixture setup uses the existing hash owner before
import; the importer and continuity operation must never generate/reset/rehash
passwords. Installed v2 declaration and private audit Connection writer
must be composed in the tested artifact. These are real required gates, not skips.
"""

import logging
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from dotmac_kernel import audit_actions, credential_lifecycle, deps, security
from dotmac_kernel.migrations import identity_history as history
from dotmac_kernel.migrations import versions_dir
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.sql import quoted_name

from alembic.script import ScriptDirectory


class LockedSource:
    def __init__(self, tables):
        self.tables = tables

    def decode(self, connection):
        decoded = {}
        for family, row_type in zip(history._FAMILIES, history._ROW_TYPES, strict=True):
            decoded[family] = tuple(
                row_type(**dict(row))
                for row in connection.execute(sa.select(self.tables[family])).mappings()
            )
        return history.IdentityHistoryRowsV1(**decoded)


def _source_rows(password_hash, token_hash, expires_at):
    now = datetime.now(UTC) - timedelta(days=1)
    tenant, second_tenant, person, role = uuid4(), uuid4(), uuid4(), uuid4()
    timed = {"created_at": now, "updated_at": now + timedelta(seconds=1)}
    rows = history.IdentityHistoryRowsV1(
        tenants=tuple(
            history.HistoricalTenantV1(
                id=key,
                slug="history-" + str(key),
                name="Synthetic tenant",
                is_active=True,
                suspended_at=None,
                deleted_at=None,
                **timed,
            )
            for key in (tenant, second_tenant)
        ),
        tenant_domains=(
            history.HistoricalTenantDomainV1(
                id=uuid4(),
                tenant_id=tenant,
                domain="synthetic-" + str(tenant) + ".example.test",
                verified_at=now,
                **timed,
            ),
        ),
        people=(
            history.HistoricalPersonV1(
                id=person,
                tenant_id=tenant,
                email="synthetic@example.test",
                first_name="Synthetic",
                last_name="Person",
                **timed,
            ),
        ),
        user_credentials=tuple(
            history.HistoricalCredentialV1(
                id=uuid4(),
                tenant_id=tenant,
                person_id=person,
                password_hash=password_hash,
                **timed,
            )
            for _ in range(2)
        ),
        auth_sessions=(
            history.HistoricalSessionV1(
                id=uuid4(),
                tenant_id=tenant,
                person_id=person,
                token_hash=token_hash,
                expires_at=expires_at,
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
        audit_events=tuple(
            history.HistoricalAuditEventV1(
                id=uuid4(),
                tenant_id=tenant,
                actor_person_id=person if index == 0 else None,
                action="historical.event",
                entity_type="person",
                entity_id=str(person),
                details=details,
                created_at=now,
            )
            for index, details in enumerate(
                ({"known": [None, False, 0]}, [1, None], "scalar", None)
            )
        ),
    )
    return rows


def _table(metadata, schema, family, row_type):
    columns = []
    for f in fields(row_type):
        if f.name == "id" or f.name.endswith("_id"):
            column_type = sa.Uuid()
        elif f.name.endswith("_at"):
            column_type = sa.DateTime(timezone=True)
        elif f.name == "is_active":
            column_type = sa.Boolean()
        elif f.name == "details":
            column_type = sa.JSON()
        else:
            column_type = sa.String()
        columns.append(
            sa.Column(
                f.name,
                column_type,
                primary_key=f.name == "id",
                nullable=f.name
                in {
                    "actor_person_id",
                    "revoked_at",
                    "verified_at",
                    "suspended_at",
                    "deleted_at",
                    "entity_id",
                },
            )
        )
    return sa.Table(family, metadata, *columns, schema=schema)


@pytest.fixture
def imported_case(admin_engine, monkeypatch):
    assert (
        admin_engine.hide_parameters is True
    ), "root admin_engine fixture must hide parameters at creation"
    assert admin_engine.echo is False
    assert not admin_engine.logger.isEnabledFor(logging.DEBUG)
    password = "synthetic-history-fixture-only-password"
    password_hash = security.hash_password(password)
    # Produce a genuine owner token before copying its stored hash unchanged.
    rows = _source_rows(password_hash, "0" * 64, datetime.now(UTC) + timedelta(days=1))
    for credential in rows.user_credentials:
        subject = credential_lifecycle.CredentialSubjectRef(
            application="synthetic",
            principal_kind="person",
            principal_ref=str(credential.person_id),
            credential_ref=str(credential.id),
        )
        store = Mock()
        store.load.return_value = credential_lifecycle.CredentialSnapshot(
            subject=subject,
            password_hash=password_hash,
            credential_version=0,
            is_active=True,
        )
        owner = credential_lifecycle.CredentialLifecycle(
            store=store, sessions=Mock(), recovery=Mock(), audit=Mock()
        )
        result = owner.verify(subject, password)
        assert result.verdict == "accepted"
        assert result.replacement_hash is None
    # Every admitted password session is minted only after owner verification.
    issued = tuple(
        security.issue_access_token(rows.people[0].id, rows.people[0].tenant_id)
        for _ in range(3)
    )
    token, expiry = issued[0]
    historical = rows.auth_sessions[0]
    rows = replace(
        rows,
        auth_sessions=(
            replace(
                historical, token_hash=security.hash_token(token), expires_at=expiry
            ),
            replace(
                historical,
                id=uuid4(),
                token_hash=security.hash_token(issued[1][0]),
                expires_at=historical.created_at + timedelta(hours=1),
            ),
            replace(
                historical,
                id=uuid4(),
                token_hash=security.hash_token(issued[2][0]),
                expires_at=issued[2][1],
                revoked_at=historical.created_at + timedelta(hours=12),
            ),
        ),
    )
    schema = "history_probe_" + uuid4().hex
    metadata = sa.MetaData()
    tables = {
        family: _table(metadata, schema, family, row_type)
        for family, row_type in zip(history._FAMILIES, history._ROW_TYPES, strict=True)
    }
    monkeypatch.setattr(
        audit_actions,
        "_active_registry",
        audit_actions.AuditActionRegistry.from_manifests(()),
    )
    with admin_engine.connect() as connection:
        with connection.begin():
            connection.execute(
                sa.text(f'CREATE SCHEMA "{schema}" AUTHORIZATION app_admin')
            )
            metadata.create_all(connection)
            for family in history._FAMILIES:
                for row in getattr(rows, family):
                    connection.execute(
                        sa.insert(tables[family]).values(**history._values(row))
                    )
        with connection.begin():
            relations = history.IdentityHistoryRelationsV1(
                schema=schema,
                **{
                    name: history._relation_fact(connection, schema, name)[0]
                    for name in history._FAMILIES
                },
            )
        cohort = history.IdentityHistoryCohortV1(
            **{
                name: frozenset(row.id for row in getattr(rows, name))
                for name in history._FAMILIES
            }
        )
        scripts = ScriptDirectory(
            str(versions_dir().parent), version_locations=[str(versions_dir())]
        )
        expected = history.IdentityHistoryManifestV1(
            relations=relations,
            cohort=cohort,
            source_revision="a" * 40,
            session_issuance_review_ref="synthetic-owner-password-population",
            password_session_ids=cohort.auth_sessions,
            installed_kernel_head=scripts.get_heads()[0],
        )
        operation = history.IdentityHistoryImportOperationV1(
            is_active=True,
            activated_person_ids=cohort.people,
            reason="Synthetic historical preservation",
            decision_ref="synthetic-decision",
            actor_type="service",
            actor_id="synthetic-maintenance-owner",
            source_application="dotmac_starter_mt",
        )
        root = connection.begin()
        case = SimpleNamespace(
            connection=connection,
            root=root,
            rows=rows,
            source=LockedSource(tables),
            tables=tables,
            expected=expected,
            operation=operation,
            token=token,
            expired_token=issued[1][0],
            revoked_token=issued[2][0],
            password=password,
        )
        try:
            yield case
        finally:
            if connection.in_transaction():
                connection.rollback()
            with connection.begin():
                connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))


def _import(case):
    return history.import_identity_history(
        case.connection, case.source, case.expected, case.operation
    )


def _assert_source_unchanged(case):
    for family in history._FAMILIES:
        assert history._relation_fact(
            case.connection, case.expected.relations.schema, family
        )[0] == getattr(case.expected.relations, family)
    decoded = case.source.decode(case.connection)
    # Equality is a boolean assertion to avoid rendering synthetic material.
    assert bool(decoded == case.rows)


def test_eight_families_lossless_nulls_counts_and_caller_rollback(
    imported_case, monkeypatch
):
    case = imported_case
    with monkeypatch.context() as guard:
        for name in ("hash_password", "verify_password", "password_needs_rehash"):
            guard.setattr(
                security,
                name,
                Mock(
                    side_effect=AssertionError("importer invoked credential primitive")
                ),
            )
            guard.setattr(
                credential_lifecycle,
                name,
                Mock(
                    side_effect=AssertionError(
                        "importer invoked credential owner primitive"
                    )
                ),
            )
        for name in ("provision", "complete_reset", "apply_force_reset"):
            if hasattr(credential_lifecycle.CredentialLifecycle, name):
                guard.setattr(
                    credential_lifecycle.CredentialLifecycle,
                    name,
                    Mock(
                        side_effect=AssertionError(
                            "importer invoked credential mutation"
                        )
                    ),
                )
        receipt = _import(case)
    assert case.connection.get_transaction() is case.root and case.root.is_active
    assert receipt.counts.user_credentials == 2
    assert receipt.counts.auth_sessions == 3
    assert case.expected.password_session_ids == case.expected.cohort.auth_sessions
    assert len(case.expected.password_session_ids) == 3
    assert receipt.counts.audit_events == 4
    assert len(receipt.current_audit_ids) == 2
    for table, values in history._projections(case.rows):
        key = next(iter(table.primary_key.columns)).name
        actual = (
            case.connection.execute(sa.select(table).where(table.c[key] == values[key]))
            .mappings()
            .one()
        )
        assert bool(dict(actual) == values)
    assert len(set(receipt.current_audit_ids) & case.expected.cohort.audit_events) == 0
    audit_table = next(t for t in history._TARGETS if t.name == "audit_events")
    for event_id in receipt.current_audit_ids:
        event = (
            case.connection.execute(
                sa.select(audit_table).where(audit_table.c.id == event_id)
            )
            .mappings()
            .one()
        )
        assert event["action"] == "identity.history_imported"
        assert event["actor_type"] == "service"
        assert event["actor_id"] == case.operation.actor_id
        assert event["source_application"] == case.operation.source_application
        assert event["occurred_at"] is not None and event["created_at"] is not None
    _assert_source_unchanged(case)
    # Public credential owner: no hash generation/reset is allowed in this gate.
    monkeypatch.setattr(
        credential_lifecycle,
        "hash_password",
        Mock(side_effect=AssertionError("unexpected generation")),
    )
    credential_table = next(t for t in history._TARGETS if t.name == "user_credentials")
    for row in case.connection.execute(sa.select(credential_table)).mappings():
        subject = credential_lifecycle.CredentialSubjectRef(
            application="synthetic",
            principal_kind="person",
            principal_ref=str(row["party_id"]),
            credential_ref=str(row["id"]),
        )
        store = Mock()
        store.load.return_value = credential_lifecycle.CredentialSnapshot(
            subject=subject,
            password_hash=row["password_hash"],
            credential_version=0,
            is_active=True,
        )
        owner = credential_lifecycle.CredentialLifecycle(
            store=store, sessions=Mock(), recovery=Mock(), audit=Mock()
        )
        result = owner.verify(subject, case.password)
        assert result.verdict == "accepted"
        assert result.replacement_hash is None
        assert owner.verify(subject, "wrong-synthetic-password").verdict == "invalid"
    case.connection.execute(sa.text("SET LOCAL ROLE app_user"))
    case.connection.execute(
        sa.text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(case.rows.people[0].tenant_id)},
    )
    with Session(
        bind=case.connection, join_transaction_mode="rollback_only"
    ) as session:
        request = SimpleNamespace(
            state=SimpleNamespace(
                tenant=SimpleNamespace(id=case.rows.people[0].tenant_id)
            )
        )
        assert (
            deps.authenticate_request(request, session, token=case.token).id
            == case.rows.people[0].id
        )
        for rejected_token in (case.expired_token, case.revoked_token):
            # JWTs remain valid: the actual session owner must reject persisted
            # expiry/revocation, not merely an expired JWT claim.
            decoded = security.decode_access_token(rejected_token)
            assert decoded is not None
            authenticated = deps.authenticate_request(
                request, session, token=rejected_token
            )
            assert authenticated is None
        assert deps.authenticate_request(request, session, token="invalid") is None
        request.state.tenant = SimpleNamespace(id=case.rows.tenants[1].id)
        assert deps.authenticate_request(request, session, token=case.token) is None
    case.connection.execute(
        sa.text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(case.rows.tenants[1].id)},
    )
    for table in history._TARGETS:
        if table.name not in ("tenants", "tenant_domains"):
            observed = case.connection.scalar(
                sa.select(sa.func.count()).select_from(table)
            )
            assert observed == (1 if table.name == "audit_events" else 0)
    case.connection.execute(sa.text("SET LOCAL ROLE app_admin"))
    case.root.rollback()
    with case.connection.begin():
        _assert_source_unchanged(case)
        for table in history._TARGETS:
            assert (
                case.connection.scalar(sa.select(sa.func.count()).select_from(table))
                == 0
            )


@pytest.mark.parametrize(
    "stage", [table.name for table in history._TARGETS] + ["current_audit"]
)
def test_every_family_and_current_audit_failure_rolls_back_all(
    imported_case, monkeypatch, stage
):
    case = imported_case
    original = case.connection.execute

    def fail(statement, *args, **kwargs):
        if isinstance(statement, sa.sql.dml.Insert) and statement.table.name == stage:
            raise RuntimeError("synthetic-sensitive-driver-diagnostic")
        return original(statement, *args, **kwargs)

    if stage == "current_audit":
        monkeypatch.setattr(
            history,
            "_write_audit_event_on_connection",
            Mock(side_effect=RuntimeError("synthetic-sensitive-audit-diagnostic")),
        )
    else:
        monkeypatch.setattr(case.connection, "execute", fail)
    with pytest.raises(history.IdentityHistoryImportRefused) as refused:
        _import(case)
    assert "sensitive" not in str(refused.value)
    assert refused.value.__suppress_context__ is True
    case.root.rollback()
    with case.connection.begin():
        _assert_source_unchanged(case)
        for table in history._TARGETS:
            assert (
                case.connection.scalar(sa.select(sa.func.count()).select_from(table))
                == 0
            )


@pytest.mark.parametrize(
    "fault",
    [
        "occupied",
        "provenance",
        "activation",
        "email",
        "tenant",
        "role",
        "actor",
        "oid",
        "role_authority",
        "isolation",
        "policy",
        "kernel_head",
    ],
)
def test_real_refusals_before_any_target_write(imported_case, fault):
    case = imported_case
    if fault == "occupied":
        table = next(t for t in history._TARGETS if t.name == "tenants")
        case.connection.execute(
            sa.insert(table).values(
                id=uuid4(),
                slug="occupied",
                name="Occupied",
                is_active=True,
                suspended_at=None,
                deleted_at=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    elif fault == "provenance":
        case.expected = replace(case.expected, password_session_ids=frozenset())
    elif fault == "activation":
        case.operation = replace(case.operation, is_active=False)
    elif fault == "email":
        case.connection.execute(
            sa.update(case.tables["people"]).values(email=" NonCanonical@Example.test ")
        )
    elif fault == "tenant":
        case.connection.execute(
            sa.update(case.tables["user_credentials"]).values(
                tenant_id=case.rows.tenants[1].id
            )
        )
    elif fault == "role":
        case.connection.execute(
            sa.update(case.tables["person_roles"]).values(role_id=uuid4())
        )
    elif fault == "actor":
        case.operation = replace(case.operation, actor_id="")
    elif fault == "oid":
        case.expected = replace(
            case.expected, relations=replace(case.expected.relations, people=1)
        )
    elif fault == "role_authority":
        case.connection.execute(sa.text("SET LOCAL ROLE app_user"))
    elif fault == "isolation":
        case.connection.execute(
            sa.text("ALTER TABLE public.auth_sessions DISABLE ROW LEVEL SECURITY")
        )
    elif fault == "policy":
        case.connection.execute(
            sa.text(
                "ALTER POLICY auth_sessions_tenant_isolation ON "
                "public.auth_sessions USING (true) WITH CHECK (true)"
            )
        )
    else:
        case.expected = replace(case.expected, installed_kernel_head="not-installed")
    with pytest.raises(history.IdentityHistoryImportRefused):
        _import(case)
    case.root.rollback()
    with case.connection.begin():
        _assert_source_unchanged(case)
        for table in history._TARGETS:
            assert (
                case.connection.scalar(sa.select(sa.func.count()).select_from(table))
                == 0
            )


def test_source_oid_change_inside_decoder_is_refused(imported_case):
    case = imported_case
    decoder = case.source

    class ChangedSource:
        def decode(self, connection):
            rows = decoder.decode(connection)
            schema = case.expected.relations.schema
            connection.execute(
                sa.text(f'ALTER TABLE "{schema}".people RENAME TO people_prior')
            )
            connection.execute(
                sa.text(
                    f'CREATE TABLE "{schema}".people '
                    f'(LIKE "{schema}".people_prior INCLUDING ALL)'
                )
            )
            return rows

    case.source = ChangedSource()
    with pytest.raises(history.IdentityHistoryImportRefused):
        _import(case)
    case.root.rollback()
    case.source = decoder
    with case.connection.begin():
        _assert_source_unchanged(case)


def test_real_driver_diagnostics_and_logs_do_not_export_material(
    imported_case, monkeypatch, caplog
):
    case = imported_case
    original = case.connection.execute
    material = case.rows.user_credentials[0].password_hash

    def driver_failure(statement, *args, **kwargs):
        if (
            isinstance(statement, sa.sql.dml.Insert)
            and statement.table.name == "user_credentials"
        ):
            # PostgreSQL includes the bad input in its diagnostic. The importer
            # must suppress that driver error rather than expose it as its cause.
            return original(
                sa.text("SELECT CAST(:payload AS integer)"), {"payload": material}
            )
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(case.connection, "execute", driver_failure)
    with caplog.at_level(logging.INFO, logger="sqlalchemy.engine"):
        with pytest.raises(history.IdentityHistoryImportRefused) as refused:
            _import(case)
        error = refused.value
        logging.getLogger("identity_history_canary").error(
            "refusal", exc_info=(type(error), error, error.__traceback__)
        )
    if (
        material in str(error)
        or material in caplog.text
        or case.rows.auth_sessions[0].token_hash in caplog.text
    ):
        pytest.fail("synthetic material escaped diagnostics", pytrace=False)
    assert error.__suppress_context__ is True
    case.root.rollback()
    with case.connection.begin():
        _assert_source_unchanged(case)
        for table in history._TARGETS:
            assert (
                case.connection.scalar(sa.select(sa.func.count()).select_from(table))
                == 0
            )


@pytest.mark.parametrize(
    "fault",
    [
        "partial_unique",
        "tenant_catalog_write",
        "truncate_inherited_public",
        "platform_catalog_truncate",
        "platform_catalog_missing_delete",
        "column_reference_public",
    ],
)
def test_canonical_uniqueness_and_effective_acl_refused_before_decode(
    imported_case, monkeypatch, fault
):
    case = imported_case
    if fault == "partial_unique":
        case.connection.execute(
            sa.text(
                "ALTER TABLE public.auth_sessions DROP CONSTRAINT "
                "uq_auth_sessions_tenant_token_hash"
            )
        )
        case.connection.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_auth_sessions_tenant_token_hash ON "
                "public.auth_sessions (tenant_id, token_hash) WHERE false"
            )
        )
    elif fault == "tenant_catalog_write":
        case.connection.execute(sa.text("GRANT INSERT ON public.tenants TO app_user"))
    elif fault == "truncate_inherited_public":
        # PUBLIC is effective for both roles; direct-ACL inspection would miss
        # the RLS-bypassing privilege this adds to the canonical online roles.
        case.connection.execute(
            sa.text("GRANT TRUNCATE ON public.auth_sessions TO PUBLIC")
        )
    elif fault == "platform_catalog_truncate":
        case.connection.execute(
            sa.text("GRANT TRUNCATE ON public.tenant_domains TO platform_api")
        )
    elif fault == "platform_catalog_missing_delete":
        # Platform catalog DML is required by genuine 0001, unlike app_user.
        case.connection.execute(
            sa.text("REVOKE DELETE ON public.tenants FROM platform_api")
        )
    else:
        case.connection.execute(
            sa.text(
                "GRANT REFERENCES (password_hash) ON public.user_credentials TO PUBLIC"
            )
        )
    decode = Mock(wraps=case.source.decode)
    monkeypatch.setattr(case.source, "decode", decode)
    with pytest.raises(history.IdentityHistoryImportRefused):
        _import(case)
    decode.assert_not_called()
    for table in history._TARGETS:
        assert (
            case.connection.scalar(sa.select(sa.func.count()).select_from(table)) == 0
        )
    case.root.rollback()
    with case.connection.begin():
        _assert_source_unchanged(case)
        for table in history._TARGETS:
            assert (
                case.connection.scalar(sa.select(sa.func.count()).select_from(table))
                == 0
            )


class ExactJsonbSource(LockedSource):
    def decode(self, connection):
        decoded = {}
        for family, row_type in zip(history._FAMILIES, history._ROW_TYPES, strict=True):
            table = self.tables[family]
            columns = [
                sa.cast(column, sa.Text()).label(column.name)
                if family == "audit_events" and column.name == "details"
                else column
                for column in table.c
            ]
            records = []
            for row in connection.execute(sa.select(*columns)).mappings():
                values = dict(row)
                if family == "audit_events":
                    values["details"] = history.HistoricalJsonbV1(
                        raw_json=values["details"]
                    )
                records.append(row_type(**values))
            decoded[family] = tuple(records)
        return history.IdentityHistoryRowsV1(**decoded)


@pytest.mark.parametrize(
    "raw_json",
    [
        '{"nested":[0.12345678901234567890123456789,1e1000]}',
        "9" * 5001,
        "1e1000",
        "[0.12345678901234567890123456789,1e1000]",
        "null",
        '"scalar"',
        "true",
        "{}",
        "[]",
    ],
    ids=[
        "nested-precision",
        "large-integer",
        "large-exponent",
        "array",
        "json-null",
        "string",
        "boolean",
        "object",
        "empty-array",
    ],
)
def test_jsonb_history_is_equal_in_postgresql_without_python_numeric_oracle(
    imported_case, raw_json
):
    case = imported_case
    original_source = case.source
    schema = case.expected.relations.schema
    source_table = sa.Table(
        "audit_events",
        sa.MetaData(),
        sa.Column("id", sa.Uuid()),
        sa.Column("details", JSONB()),
        schema=quoted_name(schema, True),
    )
    target_table = sa.Table(
        "audit_events",
        sa.MetaData(),
        sa.Column("id", sa.Uuid()),
        sa.Column("details", JSONB()),
        schema="public",
    )
    # Existing simple fixtures use JSON; this source is genuinely JSONB, as in
    # the reviewed product source. Setup is synthetic and caller-rollback owned.
    case.connection.execute(
        sa.DDL(
            "ALTER TABLE %(fullname)s ALTER COLUMN details "
            "TYPE JSONB USING details::jsonb"
        ).against(source_table)
    )
    event_id = case.rows.audit_events[0].id
    raw_value = sa.cast(sa.bindparam("raw", type_=sa.Text()), JSONB())
    case.connection.execute(
        sa.update(source_table)
        .values(details=raw_value)
        .where(source_table.c.id == sa.bindparam("event_id", type_=sa.Uuid())),
        {"raw": raw_json, "event_id": event_id},
    )
    case.source = ExactJsonbSource(case.tables)
    receipt = _import(case)
    assert receipt.counts.audit_events == len(case.rows.audit_events)
    source = source_table.alias("source")
    target = target_table.alias("target")
    joined_tables = source.join(target, source.c.id == target.c.id)
    equal = case.connection.scalar(
        sa.select(sa.func.bool_and(source.c.details == target.c.details)).select_from(
            joined_tables
        )
    )
    assert equal is True
    joined = case.connection.scalar(
        sa.select(sa.func.count()).select_from(joined_tables)
    )
    assert joined == len(case.rows.audit_events)
    unchanged = case.connection.scalar(
        sa.select(source_table.c.details == raw_value).where(
            source_table.c.id == sa.bindparam("event_id", type_=sa.Uuid())
        ),
        {"raw": raw_json, "event_id": event_id},
    )
    assert unchanged is True
    for name in history._FAMILIES:
        assert history._relation_fact(case.connection, schema, name)[0] == getattr(
            case.expected.relations, name
        )
    case.root.rollback()
    case.source = original_source
    with case.connection.begin():
        _assert_source_unchanged(case)
        for table in history._TARGETS:
            assert (
                case.connection.scalar(sa.select(sa.func.count()).select_from(table))
                == 0
            )


def test_invalid_jsonb_text_refuses_before_copy_and_masks_driver_input(imported_case):
    case = imported_case
    original = case.source

    class InvalidJsonbSource:
        def decode(self, connection):
            rows = original.decode(connection)
            return replace(
                rows,
                audit_events=(
                    replace(
                        rows.audit_events[0],
                        details=history.HistoricalJsonbV1(
                            raw_json="{invalid-synthetic-json"
                        ),
                    ),
                    *rows.audit_events[1:],
                ),
            )

    case.source = InvalidJsonbSource()
    with pytest.raises(history.IdentityHistoryImportRefused) as refused:
        _import(case)
    if "invalid-synthetic-json" in str(refused.value):
        pytest.fail("JSONB driver input escaped refusal", pytrace=False)
    assert refused.value.__suppress_context__ is True
    case.root.rollback()
    case.source = original
    with case.connection.begin():
        _assert_source_unchanged(case)
        for table in history._TARGETS:
            assert (
                case.connection.scalar(sa.select(sa.func.count()).select_from(table))
                == 0
            )


@pytest.mark.parametrize("target", [table.name for table in history._TARGETS])
def test_enabled_target_writer_trigger_refuses_before_decoder_copy_or_current_audit(
    imported_case, monkeypatch, target
):
    case = imported_case
    quote = case.connection.dialect.identifier_preparer.quote_identifier
    schema = case.expected.relations.schema
    # Even a no-op enabled user trigger is outside the qualified canonical
    # inputs. On parties this planted trigger would silently rewrite a copy.
    rewrite = (
        "NEW.display_name := 'unexpected synthetic rewrite'; "
        if target == "parties"
        else ""
    )
    case.connection.execute(
        sa.text(
            f"CREATE FUNCTION {quote(schema)}.unexpected_target_writer() "
            "RETURNS trigger "
            f"LANGUAGE plpgsql AS $$ BEGIN {rewrite}RETURN NEW; END; $$"
        )
    )
    case.connection.execute(
        sa.text(
            "CREATE TRIGGER unexpected_identity_writer BEFORE INSERT "
            f"ON public.{quote(target)} "
            f"FOR EACH ROW EXECUTE FUNCTION {quote(schema)}.unexpected_target_writer()"
        )
    )
    decode = Mock(wraps=case.source.decode)
    current_audit = Mock(wraps=history._write_audit_event_on_connection)
    monkeypatch.setattr(case.source, "decode", decode)
    monkeypatch.setattr(history, "_write_audit_event_on_connection", current_audit)
    with pytest.raises(history.IdentityHistoryImportRefused):
        _import(case)
    decode.assert_not_called()
    current_audit.assert_not_called()
    for table in history._TARGETS:
        assert (
            case.connection.scalar(sa.select(sa.func.count()).select_from(table)) == 0
        )
    _assert_source_unchanged(case)
    case.root.rollback()
    with case.connection.begin():
        _assert_source_unchanged(case)
        for table in history._TARGETS:
            assert (
                case.connection.scalar(sa.select(sa.func.count()).select_from(table))
                == 0
            )


@pytest.mark.parametrize(
    ("target", "mode"),
    [(table.name, "ENABLE") for table in history._TARGETS]
    + [("user_credentials", "ENABLE REPLICA"), ("user_credentials", "ENABLE ALWAYS")],
)
def test_enabled_target_rule_refuses_suppressed_insert_before_decoder_copy_or_audit(
    imported_case, monkeypatch, target, mode
):
    case = imported_case
    quote = case.connection.dialect.identifier_preparer.quote_identifier
    # This rule would silently suppress the Core insert, including credentials.
    # Every canonical target and each enabled rule mode must be refused.
    case.connection.execute(
        sa.text(
            "CREATE RULE unexpected_identity_rule AS ON INSERT "
            f"TO public.{quote(target)} "
            "DO INSTEAD NOTHING"
        )
    )
    case.connection.execute(
        sa.text(
            f"ALTER TABLE public.{quote(target)} {mode} RULE unexpected_identity_rule"
        )
    )
    decode = Mock(wraps=case.source.decode)
    current_audit = Mock(wraps=history._write_audit_event_on_connection)
    monkeypatch.setattr(case.source, "decode", decode)
    monkeypatch.setattr(history, "_write_audit_event_on_connection", current_audit)
    with pytest.raises(history.IdentityHistoryImportRefused):
        _import(case)
    decode.assert_not_called()
    current_audit.assert_not_called()
    for table in history._TARGETS:
        assert (
            case.connection.scalar(sa.select(sa.func.count()).select_from(table)) == 0
        )
    _assert_source_unchanged(case)
    case.root.rollback()
    with case.connection.begin():
        _assert_source_unchanged(case)
        for table in history._TARGETS:
            assert (
                case.connection.scalar(sa.select(sa.func.count()).select_from(table))
                == 0
            )
