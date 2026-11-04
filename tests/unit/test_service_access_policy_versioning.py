"""Ordering rule and V2 service behaviour for versioned access inputs."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_service_access_policy import (
    AccessInputDecision,
    AccessInputRefused,
    AccessSignal,
    DesiredAccess,
    RecordAccessInput,
    RecordAccessInputV2,
    ResolveDesiredAccess,
    StoredAccessStamp,
    decide_access_input,
    decide_unversioned_write,
    record_access_input,
    record_access_input_v2,
    resolve_desired_access,
)
from dotmac_service_access_policy.models import TENANT_TABLES, metadata_table
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
NOW = datetime(2026, 11, 3, tzinfo=UTC)


def _v2(version: int = 5, **changes: object) -> RecordAccessInputV2:
    base = RecordAccessInputV2(
        "service:1",
        AccessSignal.PREPAID_DEPLETED,
        True,
        "flows:1",
        NOW,
        version,
        "policy-1",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _stamp(version: int | None = 5, **changes: object) -> StoredAccessStamp:
    base = StoredAccessStamp(
        version, True, "flows:1", NOW, None if version is None else "policy-1"
    )
    return replace(base, **changes)  # type: ignore[arg-type]


# -- pure rule -------------------------------------------------------------


def test_rule_table() -> None:
    assert decide_access_input(None, _v2(0)) is AccessInputDecision.APPLY
    assert decide_access_input(_stamp(5), _v2(6)) is AccessInputDecision.APPLY
    assert decide_access_input(_stamp(5), _v2(4)) is AccessInputDecision.REFUSE_STALE
    assert decide_access_input(_stamp(5), _v2(5)) is AccessInputDecision.IDEMPOTENT
    assert (
        decide_access_input(_stamp(5), _v2(5, active=False))
        is AccessInputDecision.REFUSE_CONFLICT
    )
    assert (
        decide_access_input(_stamp(5), _v2(5, policy_version="policy-2"))
        is AccessInputDecision.REFUSE_CONFLICT
    )
    assert (
        decide_access_input(_stamp(5), _v2(5, observed_at=NOW + timedelta(seconds=1)))
        is AccessInputDecision.REFUSE_CONFLICT
    )


def test_timestamps_never_order_and_unversioned_is_oldest() -> None:
    later_but_lower = _v2(4, observed_at=NOW + timedelta(days=30))
    assert decide_access_input(_stamp(5), later_but_lower) is (
        AccessInputDecision.REFUSE_STALE
    )
    assert decide_access_input(_stamp(None), _v2(0)) is AccessInputDecision.APPLY


def test_naive_stored_instant_is_read_as_utc() -> None:
    naive = _stamp(5, observed_at=NOW.replace(tzinfo=None))
    assert decide_access_input(naive, _v2(5)) is AccessInputDecision.IDEMPOTENT


@pytest.mark.parametrize("bad", [-1, 2**63, True, 1.5, "3"])
def test_invalid_source_version_is_rejected(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        decide_access_input(None, _v2(bad))  # type: ignore[arg-type]


def test_unversioned_write_only_while_never_versioned() -> None:
    assert decide_unversioned_write(None) is AccessInputDecision.APPLY
    assert decide_unversioned_write(_stamp(None)) is AccessInputDecision.APPLY
    assert decide_unversioned_write(_stamp(0)) is AccessInputDecision.REFUSE_UNVERSIONED


# -- service (SQLite; PostgreSQL proofs: tests/test_service_access_policy_versioning.py)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_serviceaccess": None}},
    )
    Tenant.__table__.create(engine)
    for name in TENANT_TABLES:
        metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT, slug="a", name="A"))
        session.flush()
        yield session
    engine.dispose()


def test_out_of_order_arrival_keeps_the_newer_state(db: Session) -> None:
    scope = TenantScope(TENANT)
    newer = record_access_input_v2(
        db, scope=scope, command=_v2(7, active=False, source_reference="flows:7")
    )
    assert newer.decision is AccessInputDecision.APPLY
    with pytest.raises(AccessInputRefused) as stale:
        record_access_input_v2(db, scope=scope, command=_v2(6, active=True))
    assert stale.value.reason_code == "ACCESS_INPUT_STALE"
    assert (newer.row.active, newer.row.source_version) == (False, 7)
    decision = resolve_desired_access(
        db, scope=scope, command=ResolveDesiredAccess("service:1", NOW)
    )
    assert decision.desired_access == DesiredAccess.ALLOW


def test_replay_is_a_no_op_and_same_version_conflict_is_refused(db: Session) -> None:
    scope = TenantScope(TENANT)
    record_access_input_v2(db, scope=scope, command=_v2(3))
    replay = record_access_input_v2(db, scope=scope, command=_v2(3))
    assert replay.decision is AccessInputDecision.IDEMPOTENT
    with pytest.raises(AccessInputRefused) as conflict:
        record_access_input_v2(db, scope=scope, command=_v2(3, active=False))
    assert conflict.value.reason_code == "ACCESS_INPUT_VERSION_CONFLICT"
    assert replay.row.active is True


def test_newer_applies_and_v1_rows_are_superseded(db: Session) -> None:
    scope = TenantScope(TENANT)
    record_access_input(
        db,
        scope=scope,
        command=RecordAccessInput(
            "service:1", AccessSignal.PREPAID_DEPLETED, True, "legacy", NOW
        ),
    )
    applied = record_access_input_v2(db, scope=scope, command=_v2(0, active=False))
    assert applied.decision is AccessInputDecision.APPLY
    assert (applied.row.source_version, applied.row.policy_version) == (0, "policy-1")


def test_v1_cannot_overwrite_a_versioned_key(db: Session) -> None:
    scope = TenantScope(TENANT)
    record_access_input_v2(db, scope=scope, command=_v2(2))
    with pytest.raises(AccessInputRefused) as refused:
        record_access_input(
            db,
            scope=scope,
            command=RecordAccessInput(
                "service:1", AccessSignal.PREPAID_DEPLETED, False, "legacy", NOW
            ),
        )
    assert refused.value.reason_code == "ACCESS_INPUT_UNVERSIONED_WRITE"


def test_keys_are_independent_per_signal_and_service(db: Session) -> None:
    scope = TenantScope(TENANT)
    record_access_input_v2(db, scope=scope, command=_v2(9))
    other_signal = _v2(1, signal=AccessSignal.ADMIN_HOLD)
    other_service = _v2(1, service_reference="service:2")
    for command in (other_signal, other_service):
        assert (
            record_access_input_v2(db, scope=scope, command=command).decision
            is AccessInputDecision.APPLY
        )


def test_invalid_v2_inputs_and_wrong_contract_are_rejected(db: Session) -> None:
    scope = TenantScope(TENANT)
    for command in (
        _v2(1, policy_version="  "),
        _v2(1, policy_version="x" * 61),
        _v2(1, observed_at=NOW.replace(tzinfo=None)),
        _v2(-1),
    ):
        with pytest.raises((TypeError, ValueError)):
            record_access_input_v2(db, scope=scope, command=command)
    with pytest.raises(TypeError):
        record_access_input_v2(
            db,
            scope=scope,
            command=RecordAccessInput(  # type: ignore[arg-type]
                "service:1", AccessSignal.ADMIN_HOLD, True, "x", NOW
            ),
        )
    with pytest.raises(TypeError):
        record_access_input(db, scope=scope, command=_v2(1))  # type: ignore[arg-type]
