"""DELIVERY claim, sweep and retention races on real Postgres.

SQLite has one writer and ignores ``FOR UPDATE SKIP LOCKED``, so the unit suite
cannot prove the concurrency claims. These run each race with real threads and
a barrier. DDL runs with the MIGRATION url inside a per-run schema (reached
through ``schema_translate_map``) which is dropped afterwards; runtime sessions
use the application url and are granted on that schema only.

Requires Postgres (``make test-db-up``); skipped without ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_integration import (
    CapabilityBinding,
    ConnectorConfigRevision,
    ConnectorInstallation,
    DeliveryAttempt,
    RetentionPolicy,
    claim_delivery,
    purge_expired_delivery_payloads,
    release_expired_leases,
    replay_delivery,
)
from dotmac_integration.execution import LEASE_EXPIRED_ERROR_CODE
from dotmac_integration.policy import ExecutionPolicy
from dotmac_integration.retention import REDACTION_MARKER, DeliveryLegalHold
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

LEASE = ExecutionPolicy(lease_seconds=60)
POLICY = RetentionPolicy(
    payload_retention_days=30,
    replay_evidence_retention_days=180,
    legal_policy_owner="test-legal-owner",
)
TABLES = (
    ConnectorInstallation,
    ConnectorConfigRevision,
    CapabilityBinding,
    DeliveryAttempt,
    DeliveryLegalHold,
)
ROUNDS = 5


@pytest.fixture(autouse=True)
def _no_platform_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    import dotmac_integration.operations as operations
    import dotmac_integration.retention as retention

    monkeypatch.setattr(operations, "record_operation", lambda *a, **k: None)
    monkeypatch.setattr(retention, "record_operation", lambda *a, **k: None)


@pytest.fixture(scope="module")
def schema_factory() -> Iterator[sessionmaker[Session]]:
    runtime_url = os.getenv("TEST_DATABASE_URL")
    if not runtime_url:
        pytest.skip("TEST_DATABASE_URL not set; needs real Postgres")
    admin_url = os.getenv("TEST_MIGRATION_DATABASE_URL") or runtime_url
    schema = f"intg_t_{uuid.uuid4().hex[:12]}"
    translate = {"mod_intg": schema}
    admin: Engine = create_engine(admin_url, future=True)
    runtime: Engine = create_engine(
        runtime_url,
        future=True,
        execution_options={"schema_translate_map": translate},
    )
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        ddl = admin.execution_options(schema_translate_map=translate)
        for model in TABLES:
            model.__table__.create(ddl, checkfirst=True)
        with admin.begin() as conn:
            role = conn.execute(text("select current_user")).scalar_one()
            app_role = runtime.connect()
            try:
                app_user = app_role.execute(text("select current_user")).scalar_one()
            finally:
                app_role.close()
            conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{app_user}"'))
            conn.execute(
                text(f'GRANT ALL ON ALL TABLES IN SCHEMA "{schema}" TO "{app_user}"')
            )
            assert role
        yield sessionmaker(bind=runtime, expire_on_commit=False)
    finally:
        runtime.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _make_delivery(
    factory: sessionmaker[Session],
    *,
    state: str = "pending",
    leased_until: datetime | None = None,
    age_days: int = 0,
    attempt_count: int = 0,
) -> uuid.UUID:
    with factory() as session:
        installation = ConnectorInstallation(
            id=uuid.uuid4(),
            connector_key="conformance_fake",
            connector_version="0.0.0",
            spi_range=">=1.0,<2.0",
            manifest_digest="d" * 64,
            name=f"inst-{uuid.uuid4().hex[:8]}",
            state="enabled",
        )
        session.add(installation)
        session.flush()
        binding = CapabilityBinding(
            id=uuid.uuid4(),
            installation_id=installation.id,
            capability_id="messaging.send.v1",
            state="enabled",
        )
        session.add(binding)
        session.flush()
        delivery = DeliveryAttempt(
            installation_id=installation.id,
            capability_binding_id=binding.id,
            event_type="e",
            idempotency_key=f"k-{uuid.uuid4()}",
            payload_digest="0" * 64,
            payload_json={"to": "x"},
            state="pending",
            next_attempt_at=datetime.now(UTC),
        )
        session.add(delivery)
        session.flush()
        delivery.state = state
        delivery.leased_until = leased_until
        delivery.attempt_count = attempt_count
        if age_days:
            delivery.created_at = datetime.now(UTC) - timedelta(days=age_days)
        session.commit()
        return delivery.id


def _race(*workers: Callable[[], object]) -> list[object]:
    barrier = threading.Barrier(len(workers))
    results: list[object] = [None] * len(workers)
    errors: list[BaseException] = []

    def run(index: int, fn: Callable[[], object]) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = fn()
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(workers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    return results


def _row(factory: sessionmaker[Session], delivery_id: uuid.UUID) -> DeliveryAttempt:
    with factory() as session:
        row = session.get(DeliveryAttempt, delivery_id)
        assert row is not None
        return row


def test_two_concurrent_claimers_exactly_one_wins(
    schema_factory: sessionmaker[Session],
) -> None:
    for _ in range(ROUNDS):
        delivery_id = _make_delivery(schema_factory)

        def claimer(delivery_id: uuid.UUID = delivery_id) -> bool:
            with schema_factory() as session:
                row = session.get(DeliveryAttempt, delivery_id)
                won = claim_delivery(session, row, policy=LEASE)
                session.commit()
                return won

        results = _race(claimer, claimer)
        assert results.count(True) == 1
        row = _row(schema_factory, delivery_id)
        assert row.state == "in_flight"
        assert row.attempt_count == 1


def test_sweep_versus_claim_never_yields_a_second_attempt(
    schema_factory: sessionmaker[Session],
) -> None:
    for _ in range(ROUNDS):
        past = datetime.now(UTC) - timedelta(seconds=600)
        delivery_id = _make_delivery(
            schema_factory, state="in_flight", leased_until=past, attempt_count=1
        )
        later = datetime.now(UTC)

        def claimer(
            delivery_id: uuid.UUID = delivery_id, later: datetime = later
        ) -> bool:
            with schema_factory() as session:
                row = session.get(DeliveryAttempt, delivery_id)
                won = claim_delivery(session, row, policy=LEASE, now=later)
                session.commit()
                return won

        def sweeper(later: datetime = later) -> int:
            with schema_factory() as session:
                moved = release_expired_leases(session, now=later)
                session.commit()
                return moved

        claimed, _swept = _race(claimer, sweeper)
        assert claimed is False
        row = _row(schema_factory, delivery_id)
        assert row.state == "reconciliation_required"
        assert row.error_code == LEASE_EXPIRED_ERROR_CODE
        assert row.attempt_count == 1, "an expired lease was claimed again"


def test_replay_versus_purge_never_leaves_a_pending_tombstone(
    schema_factory: sessionmaker[Session],
) -> None:
    for _ in range(ROUNDS):
        delivery_id = _make_delivery(schema_factory, state="dead_letter", age_days=60)

        def replayer(delivery_id: uuid.UUID = delivery_id) -> str:
            with schema_factory() as session:
                row = session.get(DeliveryAttempt, delivery_id)
                try:
                    replay_delivery(session, row, reason="operator")
                except Exception as exc:  # NotRepairable when the purge won
                    session.rollback()
                    return type(exc).__name__
                session.commit()
                return "replayed"

        def purger() -> int:
            with schema_factory() as session:
                sweep = purge_expired_delivery_payloads(session, policy=POLICY)
                session.commit()
                return sweep.redacted

        _race(replayer, purger)
        row = _row(schema_factory, delivery_id)
        redacted = REDACTION_MARKER in (row.payload_json or {})
        assert not (row.state == "pending" and redacted), "pending tombstone"

        # Even if a tombstone were pending, the claim refuses to lease it.
        with schema_factory() as session:
            fresh = session.get(DeliveryAttempt, delivery_id)
            if redacted:
                fresh.state = "pending"
                session.commit()
                assert claim_delivery(session, fresh, policy=LEASE) is False
                session.rollback()
