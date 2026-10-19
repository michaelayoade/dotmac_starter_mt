"""Actual installed Kernel PostgreSQL import/readback canaries; AUTHORED NOT RUN.

Uses the established migrated admin_engine and synthetic source owner. This
partial capsule requires its genuine full baseline before hosted collection.
"""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from dotmac_kernel.migrations import identity_history as history

from tests.test_identity_history import ExactJsonbSource, imported_case
from tests.test_identity_history import _import as owner_import

assert imported_case


def _import(case):
    if not case.rows.tenant_domains:
        tenant = case.rows.tenants[0]
        domain = history.HistoricalTenantDomainV1(
            id=uuid4(),
            tenant_id=tenant.id,
            domain="source.example.test",
            verified_at=None,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )
        case.connection.execute(
            sa.insert(case.tables["tenant_domains"]).values(**history._values(domain))
        )
        case.rows = replace(case.rows, tenant_domains=(domain,))
        case.expected = replace(
            case.expected,
            cohort=replace(
                case.expected.cohort, tenant_domains=frozenset((domain.id,))
            ),
        )
    return owner_import(case)


def _verify(case, receipt, rows=None):
    return history.verify_identity_history(
        case.connection,
        case.rows if rows is None else rows,
        case.expected,
        case.operation,
        receipt,
        **case.composition,
    )


def _table(name):
    return next(table for table in history._TARGETS if table.name == name)


def test_real_import_independent_readback_does_not_write_or_change_root(imported_case):
    case = imported_case
    receipt = _import(case)
    root = case.connection.get_transaction()
    before = case.connection.scalar(sa.text("SELECT txid_current()::text"))
    audit_ids = tuple(
        case.connection.scalars(
            sa.select(_table("audit_events").c.id).order_by(_table("audit_events").c.id)
        )
    )
    assert _verify(case, receipt) is None
    assert _verify(case, receipt) is None
    assert case.connection.get_transaction() is root and root.is_active
    assert case.connection.scalar(sa.text("SELECT txid_current()::text")) == before
    assert (
        tuple(
            case.connection.scalars(
                sa.select(_table("audit_events").c.id).order_by(
                    _table("audit_events").c.id
                )
            )
        )
        == audit_ids
    )
    with pytest.raises(history.IdentityHistoryImportRefused):
        _import(case)  # Original empty-target contract stays closed.


@pytest.mark.parametrize(
    "name,column,value",
    [
        ("tenants", "name", "changed"),
        ("tenant_domains", "verified_at", None),
        ("parties", "display_name", "changed"),
        ("party_persons", "first_name", "changed"),
        ("user_credentials", "password_hash", "synthetic-changed-password-material"),
        ("auth_sessions", "token_hash", "1" * 64),
        ("roles", "name", "changed"),
        ("party_role_grants", "updated_at", None),
        ("audit_events", "action", "changed"),
    ],
)
def test_each_canonical_target_value_drift_is_independently_refused(
    imported_case, name, column, value
):
    case = imported_case
    receipt = _import(case)
    table = _table(name)
    key = next(iter(table.primary_key.columns))
    identifier = case.connection.scalar(sa.select(key).order_by(key).limit(1))
    replacement = sa.func.transaction_timestamp() if value is None else value
    case.connection.execute(
        sa.update(table).where(key == identifier).values({column: replacement})
    )
    with pytest.raises(
        history.IdentityHistoryImportRefused, match="verification refused"
    ):
        _verify(case, receipt)


@pytest.mark.parametrize(
    "column",
    [
        "tenant_id",
        "actor_type",
        "actor_id",
        "actor_label",
        "actor_party_id",
        "source_application",
        "action",
        "entity_type",
        "entity_id",
        "request_id",
        "status_code",
        "is_success",
        "ip_address",
        "user_agent",
        "details",
        "occurred_at",
        "created_at",
    ],
)
def test_every_current_audit_field_is_expected_independently(imported_case, column):
    case = imported_case
    receipt = _import(case)
    table = _table("audit_events")
    identifier = receipt.current_audit_ids[0]
    alternatives = {
        "tenant_id": sorted(case.expected.cohort.tenants, key=str)[1],
        "actor_party_id": uuid4(),
        "status_code": 201,
        "is_success": False,
        "details": {"changed": True},
        "created_at": case.rows.tenants[0].created_at - timedelta(microseconds=1),
        "occurred_at": case.rows.tenants[0].created_at - timedelta(microseconds=1),
    }
    case.connection.execute(
        sa.update(table)
        .where(table.c.id == identifier)
        .values({column: alternatives.get(column, "changed")})
    )
    with pytest.raises(history.IdentityHistoryImportRefused):
        _verify(case, receipt)


@pytest.mark.parametrize(
    "attack",
    ["missing", "extra", "same_count_swap", "receipt_swap", "nullable_forensic"],
)
def test_whole_extent_and_ordered_receipt_tenants(imported_case, attack):
    case = imported_case
    receipt = _import(case)
    table = _table("audit_events")
    historical = case.rows.audit_events[0]
    if attack in {"missing", "same_count_swap"}:
        case.connection.execute(sa.delete(table).where(table.c.id == historical.id))
    if attack in {"extra", "same_count_swap"}:
        values = next(
            values
            for target, values in history._projections(case.rows)
            if target.name == "audit_events"
        )
        case.connection.execute(sa.insert(table).values(**{**values, "id": uuid4()}))
    if attack == "receipt_swap":
        receipt = replace(
            receipt, current_audit_ids=tuple(reversed(receipt.current_audit_ids))
        )
    if attack == "nullable_forensic":
        case.connection.execute(
            sa.update(table)
            .where(table.c.id == historical.id)
            .values(occurred_at=sa.func.now())
        )
    with pytest.raises(history.IdentityHistoryImportRefused):
        _verify(case, receipt)


def test_successful_public_comparison_is_data_not_import_authority(imported_case):
    case = imported_case
    receipt = _import(case)
    changed = replace(
        case.rows,
        tenants=(replace(case.rows.tenants[0], name="changed"), *case.rows.tenants[1:]),
    )
    case.connection.execute(
        sa.update(_table("tenants"))
        .where(_table("tenants").c.id == changed.tenants[0].id)
        .values(name="changed")
    )
    assert _verify(case, receipt, changed) is None
    with pytest.raises(history.IdentityHistoryImportRefused):
        _verify(case, receipt)  # Original pre-write expectations still detect it.


@pytest.mark.parametrize(
    "sql",
    [
        "REVOKE SELECT ON public.parties FROM app_user",
        "ALTER TABLE public.parties NO FORCE ROW LEVEL SECURITY",
        "SET LOCAL search_path TO pg_catalog,public",
    ],
)
def test_actual_postcopy_catalog_and_connection_posture_drift(imported_case, sql):
    case = imported_case
    receipt = _import(case)
    case.connection.execute(sa.text(sql))
    with pytest.raises(history.IdentityHistoryImportRefused):
        _verify(case, receipt)


def test_driver_diagnostics_suppress_material(imported_case, monkeypatch, caplog):
    case = imported_case
    receipt = _import(case)
    sentinel = "synthetic-driver-password-token-json-sentinel"
    original = case.connection.scalar

    def broken(*args, **kwargs):
        raise ValueError(sentinel)

    monkeypatch.setattr(case.connection, "scalar", broken)
    with pytest.raises(history.IdentityHistoryImportRefused) as error:
        _verify(case, receipt)
    assert sentinel not in str(error.value) and error.value.__suppress_context__
    assert error.value.__cause__ is None and sentinel not in caplog.text
    monkeypatch.setattr(case.connection, "scalar", original)


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "{}",
        "1234567890123456789012345678901234567890",
        '{"decimal":0.123456789012345678901234567890,"nested":[null,"非ASCII",-0.0]}',
    ],
)
def test_raw_jsonb_boundary_roundtrips_without_float_or_sql_null(imported_case, raw):
    case = imported_case
    source = case.tables["audit_events"]
    # Synthetic source setup only; canonical tables remain actual migrated owners.
    case.connection.execute(
        sa.DDL(
            "ALTER TABLE %(fullname)s ALTER COLUMN details TYPE JSONB USING details:"
            ":jsonb"
        ).against(source)
    )
    identifier = case.rows.audit_events[0].id
    case.connection.execute(
        sa.text(
            'UPDATE "' + case.expected.relations.schema + '".audit_events '
            "SET details=CAST(:raw AS jsonb) WHERE id=:identifier"
        ),
        {"raw": raw, "identifier": identifier},
    )
    case.source = ExactJsonbSource(case.tables)
    case.rows = case.source.decode(case.connection)
    receipt = _import(case)
    assert _verify(case, receipt) is None
    target = _table("audit_events")
    assert (
        case.connection.scalar(
            sa.text("SELECT NULL::jsonb IS DISTINCT FROM 'null'::jsonb")
        )
        is True
    )
    changed = {} if raw == "null" else None
    case.connection.execute(
        sa.update(target).where(target.c.id == identifier).values(details=changed)
    )
    with pytest.raises(history.IdentityHistoryImportRefused):
        _verify(case, receipt)


def test_non_uuid_historical_entity_text_tamper_refused_without_writes(imported_case):
    case = imported_case
    receipt = _import(case)
    assert _verify(case, receipt) is None
    row = case.rows.audit_events[1]
    assert row.entity_id == "legacy-person:7" and type(row.entity_id) is str
    target = _table("audit_events")
    case.connection.execute(
        sa.update(target)
        .where(target.c.id == row.id)
        .values(entity_id="changed-history-text")
    )
    before = tuple(case.connection.execute(sa.select(target).order_by(target.c.id)))
    root = case.connection.get_transaction()
    statistics = sa.text(
        "SELECT relid, n_tup_ins, n_tup_upd, n_tup_del "
        "FROM pg_stat_xact_user_tables ORDER BY relid"
    )
    writes = tuple(case.connection.execute(statistics))
    with pytest.raises(history.IdentityHistoryImportRefused):
        _verify(case, receipt)
    assert tuple(case.connection.execute(statistics)) == writes
    assert (
        tuple(case.connection.execute(sa.select(target).order_by(target.c.id)))
        == before
    )
    assert case.connection.get_transaction() is root and root.is_active


def test_reference_composition_is_required_for_public_verify(imported_case):
    case = imported_case
    receipt = _import(case)
    assert _verify(case, receipt) is None
    with pytest.raises(history.IdentityHistoryImportRefused):
        history.verify_identity_history(
            case.connection, case.rows, case.expected, case.operation, receipt
        )
